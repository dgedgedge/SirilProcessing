"""Étirements GHS successifs guidés par les quartiles de l'axe des intensités.

La plus longue droite continue de log10(effectifs) est cherchée après le pic,
jusqu’à 0,875, sans objectif de pente nulle. Un niveau log cible dans le
canal de référence complète la longueur sans gain au-delà de la cible.
Les gardes photométriques restent cumulatives depuis l'entrée de cette phase.
Voir docs/scripts/postProcess/ghs-quartiles.md.
"""

from __future__ import annotations

import argparse
from collections import deque
from collections.abc import Iterator
import logging
import math
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import numpy as np

from lib.ghs_final_stretch import (
    ProtectedGHSFinalizer, MAX_SAMPLE_PIXELS, MAX_FINAL_BACKGROUND,
    MAX_FINAL_NOISE_GAIN, MAX_NEW_NEAR_WHITE, MAX_COLOR_DRIFT, MAX_CHROMA_LOSS,
)
from lib.ghs_log_histogram import HISTOGRAM_BINS
from lib.ghs_linear_histogram import (
    DEFAULT_LINEARITY_TOLERANCE, linear_histogram_profile, symmetry_points,
    DEFAULT_TAIL_HEIGHT, TAIL_CHANNELS, TAIL_HISTOGRAM_BINS, TAIL_HEIGHT_WEIGHT,
    add_tail_height_objective, log_tail_height,
    add_regression_floor_objective, DEFAULT_REGRESSION_MIN_COUNT,
    REGRESSION_REFERENCE_INTENSITY,
)
from lib.processor import processor
from lib.star_separation import validate_rgb
from lib.type_defs import JSONReport

if TYPE_CHECKING:
    from lib.auto_ghs import ComputeBackend, HistogramAnalyzer

LOGGER = logging.getLogger(__name__)
# Un quart de classe de bonus RMS ne peut compenser une classe de longueur.
MIN_IMPROVEMENT = 1e-5
# Marge absolue en intensité pour les arrondis float32 des mesures diffuses.
DIFFUSE_RETENTION_EPSILON = 1e-7
DEFAULT_FULL_CANDIDATES = 12
# Forces absolues D ajoutées au maillage pour permettre des progrès modestes.
GENTLE_STRENGTHS = (.0625, .125, .25, .5, .75, 1.0)
GENTLE_LIMIT = .5
PROTECTED_HP_LIMIT = .75
# Jusqu'à 2048 paires horizontales, uniquement pour les gardes brillantes.
MAX_HIGHLIGHT_PROBES = 2048
# Régions fixes de cette phase, en intensité normalisée [0,1].
BRIGHT_THRESHOLD = .75
CORE_THRESHOLD = .95
# Au-delà de 0,1 % du signal actif, garder la protection brillante demandée.
DEFAULT_SPARSE_TAIL_FRACTION = .001
# Plancher heuristique : autoriser jusqu’à 50 % de perte relative brillante.
DEFAULT_SPARSE_RETENTION = .5
SPARSE_TAIL_THRESHOLD = .8
# 262144 pixels : au plus 6 Mio environ par tableau RGB float64 temporaire.
DEFAULT_TRANSFORM_BLOCK_PIXELS = 262144
MAX_TRANSFORM_BLOCK_PIXELS = 4194304
BLACKPOINT_BINS = 1024
VALLEY_RATIO = .2


def quartile_profile(
    counts: np.ndarray, tolerance: float = DEFAULT_LINEARITY_TOLERANCE,
    reference_intensity: float | None = None,
) -> JSONReport:
    """Mesure une droite, prioritairement couvrant la référence si demandée."""
    return linear_histogram_profile(counts, tolerance, reference_intensity)


def isolated_blackpoint(data: np.ndarray, budget: float) -> float:
    """Propose le creux séparant un petit pic RGB sombre du support principal.

    Le budget porte sur les pixels dont au moins un canal positif serait annulé,
    en excluant les pixels déjà entièrement noirs. Les effectifs RGB servent
    seulement à détecter le creux ; une validation pleine résolution reste requise.
    Aucun pic ou creux n'est inventé dans une distribution monotone.
    """
    from scipy.signal import find_peaks

    if not np.isfinite(budget) or not 0 <= budget <= .01:
        raise ValueError('Budget de point noir hors de [0,0.01]')
    validate_rgb(data)
    if budget == 0:
        return 0.0
    values = data[data > 0]
    if values.size < 32 or np.ptp(values) <= 1e-7:
        return 0.0
    high = float(np.quantile(values, .999))
    counts, edges = np.histogram(values, bins=BLACKPOINT_BINS, range=(0, high))
    smooth = np.convolve(counts, [1/4, 1/2, 1/4], mode='same')
    # Le remplissage nul permet de détecter aussi un petit pic proche de zéro.
    peaks = find_peaks(np.pad(smooth, 1), prominence=max(2, smooth.max() * .001))[0] - 1
    active = np.any(data > 0, axis=0)
    for left, right in zip(peaks[:-1], peaks[1:]):
        if right - left < 3:
            continue
        valley = left + 1 + int(np.argmin(smooth[left + 1:right]))
        if smooth[valley] > VALLEY_RATIO * min(smooth[left], smooth[right]):
            continue
        point = float(edges[valley + 1])
        lost = np.any((data > 0) & (data <= point), axis=0)
        if np.count_nonzero(lost) / max(1, np.count_nonzero(active)) <= budget:
            return point
    return 0.0


class QuartileGHSProcessor(processor):
    """Réévalue SP, point noir, seuil de droite et queue sous gardes cumulatives."""

    parameter_dependencies = {'ghs_highlight_retention', 'ghs_color_mode', 'ghs_bins'}
    preserve_achieved_tail = True

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie les paramètres de la recherche itérative de longueur rectiligne."""
        return 'ghs_quartile'

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare passages, linéarité, seuil de droite, queue et perte noire."""
        parser.add_argument('--ghs-quartile-iterations', type=int, default=12,
                            help='Passes GHS pour allonger la droite logarithmique, de 0 (désactivé) à 64')
        parser.add_argument('--ghs-quartile-max-strength', type=float, default=10.0,
                            help='D maximal par passe de quartiles, dans ]0,10]')
        parser.add_argument(
            '--ghs-quartile-validations', type=int,
            default=DEFAULT_FULL_CANDIDATES,
            help='Candidats diversifiés vérifiés sur tout le champ par passe (défaut 12 ; 1 à 64)',
        )
        parser.add_argument(
            '--ghs-quartile-block-pixels', type=int,
            default=DEFAULT_TRANSFORM_BLOCK_PIXELS,
            help='Pixels par bloc de transformation float64 (défaut 262144 ; 1 à 4194304)',
        )
        parser.add_argument(
            '--ghs-sparse-tail-fraction', type=float,
            default=DEFAULT_SPARSE_TAIL_FRACTION,
            help='Fraction active à Y>=0,8 rétablissant la protection brillante normale (défaut 0,001 ; 0 désactive)',
        )
        parser.add_argument(
            '--ghs-sparse-highlight-retention', type=float,
            default=DEFAULT_SPARSE_RETENTION,
            help='Conservation minimale du contraste brillant si la queue est rare (défaut 0,5)',
        )
        parser.add_argument('--ghs-linearity-tolerance', type=float, default=DEFAULT_LINEARITY_TOLERANCE,
                            help='RMS maximal de la droite log en dex, de 0,005 à 0,5 (défaut 0,05)')
        parser.add_argument('--ghs-regression-min-count', type=float,
                            default=DEFAULT_REGRESSION_MIN_COUNT,
                            help='Effectif prédit minimal de la droite à 0,8 sur le graphique (défaut 10 ; 0 désactive)')
        parser.add_argument('--ghs-tail-height', type=float, default=DEFAULT_TAIL_HEIGHT,
                            help='Hauteur log cible à 0,875, relative au pic du canal [0,1] ; 0 désactive')
        parser.add_argument('--ghs-tail-channel', choices=TAIL_CHANNELS, default='red',
                            help='Canal de référence du niveau log (défaut red)')
        parser.add_argument('--ghs-blackpoint-loss', type=float, default=.001,
                            help='Fraction cumulée de pixels annulables par pic sombre isolé (0 à 0,01)')

    @staticmethod
    def validate_options(args: argparse.Namespace) -> None:
        """Refuse limites non finies, booléennes ou hors domaine avant calcul."""
        if (type(args.ghs_quartile_iterations) is not int
                or not 0 <= args.ghs_quartile_iterations <= 64):
            raise ValueError('ghs-quartile-iterations doit être dans [0,64]')
        if (type(args.ghs_quartile_validations) is not int
                or not 1 <= args.ghs_quartile_validations <= 64):
            raise ValueError('ghs-quartile-validations doit être dans [1,64]')
        if (type(args.ghs_quartile_block_pixels) is not int
                or not 1 <= args.ghs_quartile_block_pixels <= MAX_TRANSFORM_BLOCK_PIXELS):
            raise ValueError('ghs-quartile-block-pixels doit être dans [1,4194304]')
        if (not math.isfinite(args.ghs_sparse_tail_fraction)
                or not 0 <= args.ghs_sparse_tail_fraction <= 1):
            raise ValueError('ghs-sparse-tail-fraction doit être dans [0,1]')
        if (not math.isfinite(args.ghs_sparse_highlight_retention)
                or not 0 < args.ghs_sparse_highlight_retention <= 1):
            raise ValueError('ghs-sparse-highlight-retention doit être dans ]0,1]')
        if (not np.isfinite(args.ghs_quartile_max_strength)
                or not 0 < args.ghs_quartile_max_strength <= 10):
            raise ValueError('ghs-quartile-max-strength doit être dans ]0,10]')
        if (not np.isfinite(args.ghs_linearity_tolerance)
                or not .005 <= args.ghs_linearity_tolerance <= .5):
            raise ValueError('ghs-linearity-tolerance doit être dans [0.005,0.5] dex')
        if (not np.isfinite(args.ghs_regression_min_count)
                or args.ghs_regression_min_count < 0):
            raise ValueError('ghs-regression-min-count doit être fini et positif ou nul')
        if not np.isfinite(args.ghs_tail_height) or not 0 <= args.ghs_tail_height <= 1:
            raise ValueError('ghs-tail-height doit être dans [0,1]')
        if args.ghs_tail_channel not in TAIL_CHANNELS:
            raise ValueError('ghs-tail-channel doit être red, green ou blue')
        if not np.isfinite(args.ghs_blackpoint_loss) or not 0 <= args.ghs_blackpoint_loss <= .01:
            raise ValueError('ghs-blackpoint-loss doit être dans [0,0.01]')

    @staticmethod
    def candidate_parameters(
        counts: np.ndarray, max_strength: float, protected_hp: tuple[float, ...],
    ) -> Iterator[dict[str, float]]:
        """Explore D/B/SP/HP, avec forces faibles et raccords précoces.

        Parameters
        ----------
        counts : numpy.ndarray
            Les 128 effectifs de luminosité après le recalage du point noir.
        max_strength : float
            Limite D validée dans ]0,10].
        protected_hp : tuple of float
            Intensités Q90/Q95/Q98 du début de phase, dans [0,1].

        Yields
        ------
        dict of str to float
            Courbes distinctes respectant LP=0 <= SP <= HP <= 1.
            L'identité D=0 n'est émise qu'une fois par point noir.

        Notes
        -----
        Les faibles D permettent des progrès intermédiaires. Les raccords
        HP précoces peuvent réduire les pertes brillantes, sans les garantir :
        les protections du champ entier restent obligatoires.
        """
        strengths = np.unique(np.r_[
            np.linspace(max_strength / 40, max_strength, 12),
            [value for value in GENTLE_STRENGTHS if value <= max_strength],
        ])
        points = symmetry_points(counts)
        hp_values = sorted(set((.25, .5, .75, 1.0, *protected_hp)))
        yield dict(D=0.0, B=5.0, SP=0.0, LP=0.0, HP=1.0)
        for strength in strengths:
            for b in (15.0, 5.0):
                for sp in points:
                    for hp in hp_values:
                        if hp >= sp:
                            yield dict(D=float(strength), B=b, SP=sp, LP=0.0, HP=hp)

    @staticmethod
    def diverse_candidates(records: list[JSONReport]) -> list[JSONReport]:
        """Alterne les familles pour éviter trois variantes de la même courbe.

        Parameters
        ----------
        records : list of JSONReport
            Candidats avec leurs paramètres GHS et leur gain de score fini.

        Returns
        -------
        list of JSONReport
            Tous les candidats réordonnés, sans mutation des entrées.

        Notes
        -----
        Les enregistrements portent parameters et gain. Trois groupes sont
        alternés : D <= 0,5, raccord HP < 0,75, puis courbes larges. Le groupe
        du meilleur gain commence. Dans chaque groupe, les meilleurs de chaque
        famille (B, HP, octile de SP) précèdent leurs variantes de D et BP.
        Tous les candidats restent disponibles, sans mutation des entrées.
        """
        ranked = sorted(records, key=lambda item: item['gain'], reverse=True)
        groups: dict[str, list[JSONReport]] = {}
        for record in ranked:
            params = record['parameters']
            group = ('gentle' if params['D'] <= GENTLE_LIMIT else
                     'protected' if params['HP'] < PROTECTED_HP_LIMIT else 'broad')
            groups.setdefault(group, []).append(record)
        queues = []
        for records_in_group in groups.values():
            seen = set()
            first, repeated = [], []
            for record in records_in_group:
                params = record['parameters']
                family = (params['B'], params['HP'], int(params['SP'] * 8))
                (repeated if family in seen else first).append(record)
                seen.add(family)
            queues.append(deque(first + repeated))
        ordered = []
        while any(queues):
            for queue in queues:
                if queue:
                    ordered.append(queue.popleft())
        return ordered

    @staticmethod
    def highlight_coordinates(
        light: np.ndarray, xp: ModuleType = np,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Sélectionne les régions Y >= 0,75 avec leur voisin horizontal.

        Parameters
        ----------
        light : numpy.ndarray
            Luminosité 2D finie dans [0,1], de géométrie non vide.
        xp : module, optional
            NumPy ou CuPy, identique au backend du tableau.

        Returns
        -------
        tuple of numpy.ndarray
            Indices de lignes et débuts de segments sur le backend choisi.

        Notes
        -----
        Les coordonnées sont calculées sur le backend du champ et restent
        fixes pendant la phase. Au-delà de 2048 pixels brillants, une grille
        régulière dans leur liste ordonnée borne le coût. Les colonnes
        retournées sont les débuts des paires, sans franchir une ligne.
        Une image large d'un seul pixel conserve des segments de largeur un.
        Cet échantillon ciblé protège contraste et couleur ; il ne doit pas
        estimer des fractions de pixels, une population ou le bruit du ciel.
        """
        rows, columns = xp.nonzero(light >= BRIGHT_THRESHOLD)
        if rows.size > MAX_HIGHLIGHT_PROBES:
            selected = xp.linspace(
                0, rows.size - 1, MAX_HIGHLIGHT_PROBES, dtype=xp.int64,
            )
            rows, columns = rows[selected], columns[selected]
        pair_width = min(2, light.shape[1])
        return rows, xp.minimum(columns, light.shape[1] - pair_width)

    @staticmethod
    def sparse_tail_retention(
        light: np.ndarray, requested: float, sparse_fraction: float,
        minimum: float, xp: ModuleType = np,
    ) -> JSONReport:
        """Adapte le contraste brillant à la population réelle de la queue.

        Parameters
        ----------
        light : numpy.ndarray
            Luminosité finie 2D dans [0,1], sur le backend choisi.
        requested : float
            Conservation normale, dans ]0,1], utilisée aussi pour le cœur.
        sparse_fraction : float
            Fraction Y >= 0,8 parmi Y > 1e-6 rétablissant requested.
            Dans [0,1] ; zéro conserve exactement la protection demandée.
        minimum : float
            Conservation minimale dans ]0,1], plafonnée à requested.
        xp : module, optional
            NumPy ou CuPy, identique au backend de light.

        Returns
        -------
        JSONReport
            Effectifs, fraction et conservation interpolée, calculés sur tout
            le champ initial. Une population vide garde requested et une
            fraction inconnue. Les zéros de bordure sont exclus.

        Raises
        ------
        ValueError
            Si une limite est non finie ou hors de son domaine.

        Notes
        -----
        La conservation vaut min(minimum,requested) + (requested -
        min(minimum,requested)) * min(fraction/sparse_fraction,1).
        Le seuil est figé depuis l'entrée : la marge ne se renouvelle pas
        à chaque passe. Le cœur, le bruit et les couleurs ne sont pas adaptés.
        Voir docs/scripts/postProcess/ghs-quartiles.md.
        """
        if not math.isfinite(requested) or not 0 < requested <= 1:
            raise ValueError('Conservation normale hors de ]0,1]')
        if not math.isfinite(sparse_fraction) or not 0 <= sparse_fraction <= 1:
            raise ValueError('Fraction de queue hors de [0,1]')
        if not math.isfinite(minimum) or not 0 < minimum <= 1:
            raise ValueError('Conservation minimale hors de ]0,1]')
        active_count = int(xp.count_nonzero(light > 1e-6).item())
        tail_count = int(xp.count_nonzero(light >= SPARSE_TAIL_THRESHOLD).item())
        fraction = tail_count / active_count if active_count else None
        effective = requested
        if sparse_fraction > 0 and fraction is not None:
            floor = min(minimum, requested)
            effective = floor + (requested - floor) * min(fraction / sparse_fraction, 1.0)
        return dict(
            threshold=SPARSE_TAIL_THRESHOLD, active_count=active_count,
            tail_count=tail_count, fraction=fraction,
            sparse_fraction=sparse_fraction, minimum_retention=minimum,
            requested_retention=requested, effective_retention=effective,
        )

    @staticmethod
    def transform_candidate(
        image: np.ndarray, parameters: dict[str, float], xp: ModuleType = np,
        *, blackpoint: float = 0.0,
        block_pixels: int = DEFAULT_TRANSFORM_BLOCK_PIXELS,
        color_mode: str = 'human',
    ) -> np.ndarray:
        """Calcule le GHS en blocs float64 et retourne une seule image complète.

        Parameters
        ----------
        image : numpy.ndarray
            RGB normalisé de forme (3, hauteur, largeur), sur xp. L'appelant
            garantit son domaine. Les vues non contiguës sont acceptées.
        parameters : dict of str to float
            Paramètres D/B/SP/LP/HP de ghs_rgb, validés par cette fonction.
        xp : module, optional
            NumPy ou CuPy, identique au backend de image.
        blackpoint : float, optional
            Point noir appliqué dans le type source avant conversion float64,
            comme dans le calcul plein champ historique.
        block_pixels : int, optional
            Maximum de pixels spatiaux par bloc, entre 1 et 4194304.
        color_mode : str, optional
            Luminosité liée human ou even.

        Returns
        -------
        numpy.ndarray
            Nouvelle image de même géométrie et type, sur xp. Seule la sortie
            est allouée en plein champ ; les temporaires GHS restent bornés
            par block_pixels, même si une ligne dépasse cette limite.

        Raises
        ------
        ValueError
            Géométrie ou taille de bloc invalide, ou paramètres GHS invalides.

        Notes
        -----
        Le GHS/RGBBlend est ponctuel : les blocs n'ont ni halo ni statistiques
        locales. Le point noir et les arrondis conservent l'ordre historique.
        Les mesures de qualité restent réalisées sur toute l'image produite.
        Aucun fichier intermédiaire ni changement de backend n'est introduit.
        Voir docs/scripts/postProcess/ghs-quartiles.md.
        """
        from lib.auto_ghs import ghs_rgb, linear_blackpoint

        if image.ndim != 3 or image.shape[0] != 3 or image.size == 0:
            raise ValueError('La transformation nécessite une image RGB non vide')
        if (type(block_pixels) is not int
                or not 1 <= block_pixels <= MAX_TRANSFORM_BLOCK_PIXELS):
            raise ValueError('Nombre de pixels par bloc hors de [1,4194304]')
        height, width = image.shape[1:]
        columns = min(width, block_pixels)
        rows = max(1, block_pixels // columns)
        output = xp.empty(image.shape, dtype=image.dtype)
        for top in range(0, height, rows):
            for left in range(0, width, columns):
                region = (slice(None), slice(top, top + rows), slice(left, left + columns))
                block = image[region]
                shifted = linear_blackpoint(block, blackpoint, xp=xp) if blackpoint else block
                rendered = ghs_rgb(
                    shifted.astype(xp.float64), **parameters,
                    color_mode=color_mode, xp=xp,
                )
                output[region] = rendered.astype(image.dtype)
                # Ne pas conserver le bloc précédent pendant le calcul du suivant.
                del block, shifted, rendered
        return output

    @staticmethod
    def diffuse_rejections(before: JSONReport, after: JSONReport) -> list[str]:
        """Protège la luminosité diffuse et sa séparation du ciel à masques fixes.

        Parameters
        ----------
        before, after : JSONReport
            Médianes de luminosité normalisée des mêmes régions : ciel jusqu'à
            P25 initial et diffus entre P75 et P95 initiaux. Une médiane absente
            n'est pas remplacée par zéro.

        Returns
        -------
        list of str
            Motifs de rejet lorsque la médiane diffuse ou sa différence au ciel
            diminue au-delà de 1e-7, marge absolue d'arrondi float32. Si la mesure
            initiale est absente, la garde correspondante n'est pas vérifiable.

        Notes
        -----
        Cette garde porte sur l'entrée de phase, pas sur un masque redéfini
        après chaque essai. Elle évite d'améliorer une droite logarithmique en
        assombrissant les structures déjà révélées par la présentation GHS.
        """
        first, last = before.get('diffuse_median'), after.get('diffuse_median')
        if first is None or first <= DIFFUSE_RETENTION_EPSILON:
            return []
        reasons = []
        if last is None or last + DIFFUSE_RETENTION_EPSILON < first:
            reasons.append('diffuse brightness loss')
        first_sky, last_sky = before.get('sky_median'), after.get('sky_median')
        if first_sky is not None and first > first_sky:
            if (last_sky is None or last is None
                    or last - last_sky + DIFFUSE_RETENTION_EPSILON < first - first_sky):
                reasons.append('diffuse separation loss')
        return reasons

    def search(
        self, data: np.ndarray, analyzer: HistogramAnalyzer, backend: ComputeBackend,
        args: argparse.Namespace, stars: np.ndarray | None = None, headroom: float = .95,
    ) -> tuple[np.ndarray, JSONReport]:
        """Classe des passes CPU échantillonnées puis valide sur le backend choisi.

        D explore douze forces et des forces faibles ; B=15 et B=5. SP explore
        zéro, le pic et les ruptures de pente. HP inclut les raccords précoces
        Q90/Q95/Q98, 0,25 et 0,5, ainsi que 0,75 et 1. Les étoiles restent fixes
        dans la recomposition. Le niveau log est mesuré sur cette recomposition
        lorsqu'elle est disponible, sinon sur le fond seul.
        La droite doit atteindre le seuil configuré à 0,8 sur l’échelle des
        effectifs du graphique ; sous le seuil, sa progression guide les passes.
        Les finalistes sont diversifiés et contrôlés jusqu'au budget configuré
        (12 par défaut), même après les premiers rejets en pleine résolution.
        Un échantillon ciblé des régions brillantes contrôle contraste et
        couleur avant validation complète, sans estimer le bruit ni les pertes.
        Une queue rare Y>=0,8 abaisse la conservation brillante jusqu'au plancher
        configuré, depuis une population initiale figée ; le cœur reste protégé
        par la conservation normale.
        Le bruit et les pertes de contraste/couleur sont comparés à l'entrée,
        empêchant l'accumulation des tolérances. Les transformations sont faites
        par blocs ; seuls les paramètres du meilleur essai sont retenus, puis
        sa sortie est recalculée une fois par passe. La source reste intacte.
        """
        from lib.auto_ghs import linear_blackpoint, luminance

        self.validate_options(args)
        validate_rgb(data)
        if stars is not None:
            validate_rgb(stars)
            if stars.shape != data.shape:
                raise ValueError('Étoiles et fond de géométries différentes')
        if not np.isfinite(headroom) or not 0 < headroom < 1:
            raise ValueError('ghs-headroom doit être dans ]0,1[')
        xp = backend.xp
        backend.release_unused_memory()
        start = data
        current = data
        stride = max(1, math.ceil(math.sqrt(data.shape[1] * data.shape[2] / MAX_SAMPLE_PIXELS)))
        while math.ceil(data.shape[1] / stride) * math.ceil(data.shape[2] / stride) > MAX_SAMPLE_PIXELS:
            stride += 1
        fixed = ProtectedGHSFinalizer()
        sample_start = backend.numpy(start[:, ::stride, ::stride])
        thresholds = fixed.thresholds(sample_start, analyzer.color_mode)
        report: JSONReport = {'status': 'no admissible improvement', 'iterations': [],
                              'stop_reason': 'iteration limit', 'window': ['after_peak', .875],
                              'objective': ('longest_log_linear_span_with_tail_height'
                                            if args.ghs_tail_height > 0 else 'longest_contiguous_log_linear_span'),
                              'regression_min_count': args.ghs_regression_min_count,
                              'regression_reference_bins': analyzer.bins,
                              'tail_height_target': args.ghs_tail_height,
                              'tail_height_channel': args.ghs_tail_channel,
                              'tail_height_weight': TAIL_HEIGHT_WEIGHT,
                              'tail_histogram_bins': TAIL_HISTOGRAM_BINS,
                              'tail_image_role': 'with_stars' if stars is not None else 'starless',
                              'linearity_tolerance_dex': args.ghs_linearity_tolerance,
                              'sample_stride': stride, 'blackpoint_loss': 0.0,
                              'blackpoint_budget': args.ghs_blackpoint_loss,
                              'max_strength': args.ghs_quartile_max_strength,
                              'max_iterations': args.ghs_quartile_iterations,
                              'max_full_validations': args.ghs_quartile_validations,
                              'transform_block_pixels': args.ghs_quartile_block_pixels,
                              'candidate_storage': 'parameters_only',
                              'highlight_retention': args.ghs_highlight_retention}
        if args.ghs_regression_min_count > 0:
            report['objective'] += '_with_regression_floor'
        if args.ghs_quartile_iterations == 0 or not thresholds:
            report.update(status='disabled' if args.ghs_quartile_iterations == 0
                          else 'insufficient positive support', stop_reason='not searched')
            return current, report
        # Mesures de protection fixes pour l'ensemble des passages.
        # Ici les quartiles désignent l'axe, pas les quantiles de population.
        thresholds['bright'], thresholds['core'] = BRIGHT_THRESHOLD, CORE_THRESHOLD
        protected_hp = tuple(thresholds[key] for key in ('q90', 'diffuse_high', 'q98'))
        report['protected_hp'] = list(protected_hp)
        sample_before = fixed.measure(sample_start, sample_start, thresholds, analyzer.color_mode)
        sample_stars = backend.numpy(stars[:, ::stride, ::stride]) if stars is not None else None
        sample_fused = (headroom * sample_start + sample_stars * (1 - headroom * sample_start)
                        if sample_stars is not None else None)
        sample_fused_before = (fixed.measure(sample_fused, sample_fused, thresholds,
                                            analyzer.color_mode, mask_reference=sample_start)
                               if sample_fused is not None else None)
        full_before = fixed.measure(start, start, thresholds, analyzer.color_mode, xp)
        report.update(protection_before=full_before, thresholds=thresholds)
        fused_start = (headroom * start + xp.asarray(stars) * (1 - headroom * start)
                       if stars is not None else None)
        fused_before = (fixed.measure(fused_start, fused_start, thresholds, analyzer.color_mode,
                                     xp, mask_reference=start) if stars is not None else None)
        report['composite_protection_before'] = fused_before
        initial_light = luminance(start, analyzer.color_mode, xp=xp)
        sparse_tail = self.sparse_tail_retention(
            initial_light, args.ghs_highlight_retention,
            args.ghs_sparse_tail_fraction, args.ghs_sparse_highlight_retention, xp,
        )
        effective_retention = sparse_tail['effective_retention']
        report['sparse_tail'] = sparse_tail
        report['effective_highlight_retention'] = effective_retention
        probe_rows, probe_columns = self.highlight_coordinates(initial_light, xp)
        del initial_light
        LOGGER.info(
            'AutoGHS droite log : queue Y>=0,8 %d/%d ; '
            'conservation brillante %.4f (normale %.4f), cœur inchangé',
            sparse_tail['tail_count'], sparse_tail['active_count'],
            effective_retention, args.ghs_highlight_retention,
        )
        pair_offsets = xp.arange(min(2, data.shape[2]))

        def probe(image: np.ndarray) -> np.ndarray:
            """Extrait sur CPU les mêmes paires brillantes du tableau backend."""
            return backend.numpy(image[
                :, probe_rows[:, None], probe_columns[:, None] + pair_offsets,
            ])

        probe_start = probe(start) if probe_rows.size else None
        probe_stars = (probe(xp.asarray(stars))
                       if stars is not None and probe_rows.size else None)
        probe_before = (fixed.measure(probe_start, probe_start, thresholds, analyzer.color_mode)
                        if probe_start is not None else None)
        probe_fused = (headroom * probe_start + probe_stars * (1 - headroom * probe_start)
                       if probe_stars is not None else None)
        probe_fused_before = (
            fixed.measure(probe_fused, probe_fused, thresholds, analyzer.color_mode,
                          mask_reference=probe_start)
            if probe_fused is not None else None
        )
        # Les paires peuvent répéter un même pixel brillant. Elles ne doivent
        # jamais créer une région mesurable absente du champ entier.
        for compact, complete in ((probe_before, full_before),
                                  (probe_fused_before, fused_before)):
            if compact is not None and complete is not None:
                for key in ('highlight_contrast', 'core_contrast', 'local_contrast',
                            'color_drift', 'chroma_change', 'core_color_drift',
                            'core_chroma_change'):
                    if complete.get(key) is None:
                        compact[key] = None
        report['highlight_probe_count'] = int(probe_rows.size)
        report['highlight_probe_before'] = probe_before

        def transform(
            image: np.ndarray, params: dict[str, float], engine: ModuleType,
            blackpoint: float = 0.0,
        ) -> np.ndarray:
            """Évalue le même GHS float64 avec des temporaires bornés par bloc."""
            return self.transform_candidate(
                image, params, engine, blackpoint=blackpoint,
                block_pixels=args.ghs_quartile_block_pixels,
                color_mode=analyzer.color_mode,
            )

        def histogram(image: np.ndarray, engine: ModuleType) -> np.ndarray:
            """Ne transfère sur CPU que les 128 effectifs de luminosité active."""
            light = luminance(image, analyzer.color_mode, xp=engine)
            counts = engine.histogram(light[light > 1e-6], bins=HISTOGRAM_BINS, range=(0, 1))[0]
            return counts if engine is np else backend.numpy(counts)

        def tail_profile(image: np.ndarray, engine: ModuleType, channel: str) -> JSONReport:
            """Mesure le canal rendu (étoiles fixes incluses si fournies), sans transfert RGB."""
            index = TAIL_CHANNELS.index(channel)
            plane = image[index]
            if stars is not None:
                # La hauteur vise le rendu visible ; la longueur reste mesurée sur le fond.
                field = sample_stars if engine is np and image.shape == sample_start.shape else stars
                plane = headroom*plane + engine.asarray(field[index])*(1-headroom*plane)
            counts = engine.histogram(plane, bins=TAIL_HISTOGRAM_BINS, range=(0, 1))[0]
            counts = counts if engine is np else backend.numpy(counts)
            scale = data.shape[1]*data.shape[2]/(image.shape[1]*image.shape[2])
            return log_tail_height(counts, scale)

        def profile(image: np.ndarray, engine: ModuleType) -> JSONReport:
            """Combine longueur et hauteur avec les mêmes unités d'effectifs."""
            reference = REGRESSION_REFERENCE_INTENSITY if args.ghs_regression_min_count > 0 else None
            line = quartile_profile(histogram(image, engine), args.ghs_linearity_tolerance, reference)
            tail = tail_profile(image, engine, args.ghs_tail_channel)
            combined = add_tail_height_objective(
                line, tail, args.ghs_tail_height, args.ghs_tail_channel,
            )
            scale = data.shape[1]*data.shape[2]/(image.shape[1]*image.shape[2])
            return add_regression_floor_objective(
                combined, analyzer.bins, scale, args.ghs_regression_min_count,
            )

        def improvement(before: JSONReport, after: JSONReport) -> float:
            """Compare longueur et progression de hauteur, sous les mêmes gardes."""
            if after['peak'] is None or after['peak'] >= .25:
                return -math.inf
            if (self.preserve_achieved_tail and not before['tail']['estimated'] and before['tail']['achieved']
                    and not after['tail']['achieved']):
                return -math.inf
            return after['score']-before['score']

        def guards(
            candidate: np.ndarray, original: np.ndarray, baseline: JSONReport,
            engine: ModuleType, star_field: np.ndarray | None,
            fusion_original: np.ndarray | None, fusion_baseline: JSONReport | None,
            *, regions_only: bool = False,
        ) -> tuple[list[str], float, JSONReport]:
            """Contrôle les gardes cumulées ; les sondes ne mesurent que les régions.

            regions_only exclut les fractions de perte et de quasi-blanc, le
            niveau du ciel et son bruit : un sondage ciblé ne représente pas
            leur population. Contraste et couleur restent comparés aux mêmes
            régions initiales, fond et fusion compris.
            """
            validate_rgb(candidate)
            lost = engine.any((original > 0) & (candidate <= 0), axis=0)
            count = int(engine.count_nonzero(engine.any(original > 0, axis=0)).item())
            fraction = float(engine.count_nonzero(lost).item()) / max(1, count)
            rejected = []
            if not regions_only and fraction > args.ghs_blackpoint_loss + 1e-12:
                rejected.append('cumulative blackpoint loss')
            def check(first: JSONReport, last: JSONReport, prefix: str = '') -> None:
                """Protège les régions réellement présentes, sans inventer de mesures."""
                reasons = []
                for key in ('highlight_contrast', 'core_contrast', 'local_contrast'):
                    initial, final = first.get(key), last.get(key)
                    retention = (args.ghs_highlight_retention
                                 if key == 'core_contrast' else effective_retention)
                    if initial is not None and initial > 1e-7 and (
                        final is None or final < retention * initial
                    ):
                        reasons.append(key + ' loss')
                for key in ('color_drift', 'core_color_drift'):
                    if first.get(key) is not None and last.get(key) is not None and last[key] > MAX_COLOR_DRIFT:
                        reasons.append(key)
                for key in ('chroma_change', 'core_chroma_change'):
                    if first.get(key) is not None and last.get(key) is not None and last[key] < -MAX_CHROMA_LOSS:
                        reasons.append(key)
                if not regions_only:
                    reasons.extend(self.diffuse_rejections(first, last))
                if (not regions_only and first.get('sky_median') is not None
                        and last.get('sky_median') is not None
                        and last['sky_median'] > max(MAX_FINAL_BACKGROUND, first['sky_median'])):
                    reasons.append('background brightness')
                if (not regions_only and first.get('noise') is not None
                        and first['noise'] > 1e-8 and (
                            last.get('noise') is None
                            or last['noise'] > MAX_FINAL_NOISE_GAIN * first['noise']
                        )):
                    reasons.append('background noise amplification')
                if (not regions_only and last['near_white_fraction']
                        > first['near_white_fraction'] + MAX_NEW_NEAR_WHITE):
                    reasons.append('new near-white pixels')
                rejected.extend(prefix + reason for reason in reasons)

            measured = fixed.measure(original, candidate, thresholds, analyzer.color_mode, engine)
            check(baseline, measured)
            protection = {'starless': measured}
            if star_field is not None:
                fused = headroom * candidate + engine.asarray(star_field) * (1 - headroom * candidate)
                measured = fixed.measure(fusion_original, fused, thresholds, analyzer.color_mode,
                                         engine, mask_reference=original)
                check(fusion_baseline, measured, 'composite: ')
                protection['composite'] = measured
            return rejected, fraction, protection

        for iteration in range(args.ghs_quartile_iterations):
            sample = backend.numpy(current[:, ::stride, ::stride])
            before = profile(sample, np)
            bp = isolated_blackpoint(sample, args.ghs_blackpoint_loss)
            minimum = float(xp.min(xp.where(current > 0, current, xp.inf)).item())
            minimum_bp = minimum * (1 - 1e-5) if math.isfinite(minimum) else 0.0
            candidates = []
            for point in sorted(set([0.0, minimum_bp, bp])):
                shifted = linear_blackpoint(sample, point) if point > 0 else sample
                for params in self.candidate_parameters(
                    histogram(shifted, np), args.ghs_quartile_max_strength, protected_hp,
                ):
                    transformed = transform(shifted, params, np)
                    after = profile(transformed, np)
                    gain = improvement(before, after)
                    if gain > MIN_IMPROVEMENT:
                        candidates.append({'parameters': params, 'BP': point,
                                           'gain': gain, 'profile': after})
            candidates = self.diverse_candidates(candidates)
            entry: JSONReport = {'iteration': iteration + 1, 'before': before,
                                 'candidate_count': len(candidates),
                                 'validations': [], 'sample_rejections': [], 'accepted': None}
            full_profile = profile(current, xp)
            current_probe = probe(current) if probe_start is not None else None
            best = None
            for record in candidates:
                if len(entry['validations']) >= args.ghs_quartile_validations:
                    break
                point = record['BP']
                shifted_sample = linear_blackpoint(sample, point) if point > 0 else sample
                candidate_sample = transform(shifted_sample, record['parameters'], np)
                rejected, _, _ = guards(candidate_sample, sample_start, sample_before, np,
                                      sample_stars, sample_fused, sample_fused_before)
                if rejected:
                    entry['sample_rejections'].append(dict(record, rejections=rejected))
                    continue
                if current_probe is not None:
                    shifted_probe = (linear_blackpoint(current_probe, point)
                                     if point > 0 else current_probe)
                    candidate_probe = transform(shifted_probe, record['parameters'], np)
                    rejected, _, _ = guards(
                        candidate_probe, probe_start, probe_before, np,
                        probe_stars, probe_fused, probe_fused_before,
                        regions_only=True,
                    )
                    if rejected:
                        entry['sample_rejections'].append(dict(
                            record, rejections=['highlight probe: ' + reason for reason in rejected],
                        ))
                        continue
                candidate = transform(current, record['parameters'], xp, point)
                after = profile(candidate, xp)
                gain = improvement(full_profile, after)
                rejected, loss, protection = guards(candidate, start, full_before, xp, stars,
                                         fused_start, fused_before)
                if gain <= MIN_IMPROVEMENT:
                    rejected.append('no full-resolution line/height improvement')
                audit = dict(record, full_profile=after, full_gain=gain if math.isfinite(gain) else None,
                             rejections=rejected, blackpoint_loss=loss, protection=protection)
                entry['validations'].append(audit)
                LOGGER.info(
                    'AutoGHS droite log : passe %d, validation %d/%d, '
                    'D=%.3f B=%.1f SP=%.5f HP=%.5f : %s',
                    iteration + 1, len(entry['validations']),
                    args.ghs_quartile_validations, record['parameters']['D'],
                    record['parameters']['B'], record['parameters']['SP'],
                    record['parameters']['HP'],
                    ', '.join(rejected) if rejected else 'admissible',
                )
                if not rejected and (best is None or gain > best[0]):
                    best = (gain, audit)
                # Les rapports ne portent que des paramètres et mesures CPU.
                del candidate
            examined = len(entry['sample_rejections']) + len(entry['validations'])
            entry['unexamined_candidates'] = len(candidates) - examined
            entry['validation_budget_exhausted'] = examined < len(candidates)
            report['iterations'].append(entry)
            if best is None:
                report['stop_reason'] = (
                    'full-resolution validation budget exhausted'
                    if entry['validation_budget_exhausted']
                    else 'no admissible line/height improvement'
                )
                break
            _, accepted = best
            current = transform(current, accepted['parameters'], xp, accepted['BP'])
            entry['accepted'] = accepted
            report.update(status='applied', blackpoint_loss=accepted['blackpoint_loss'])
            LOGGER.info('AutoGHS droite log : passe %d D=%.3f B=%.1f SP=%.5f HP=%.2f BP=%.5f perte=%.6g longueur=%.5f hauteur=%s',
                        iteration + 1, accepted['parameters']['D'], accepted['parameters']['B'],
                        accepted['parameters']['SP'], accepted['parameters']['HP'],
                        accepted['BP'], accepted['blackpoint_loss'], accepted['full_profile']['line_span'],
                        accepted['full_profile']['tail']['height'])
        report['before'] = profile(start, xp)
        report['after'] = profile(current, xp)
        report['tail_channels_before'] = {channel: tail_profile(start, xp, channel) for channel in TAIL_CHANNELS}
        report['tail_channels_after'] = {channel: tail_profile(current, xp, channel) for channel in TAIL_CHANNELS}
        tail = report['after']['tail']
        report['tail_height_achieved'] = tail['achieved']
        floor = report['after']['regression_floor']
        report['regression_floor_achieved'] = floor['achieved']
        report['targets_achieved'] = bool(
            report['after']['measurable']
            and (args.ghs_regression_min_count == 0 or floor['achieved'])
            and (args.ghs_tail_height == 0 or tail['achieved'])
        )
        if args.ghs_regression_min_count > 0 and not floor['achieved']:
            LOGGER.info('AutoGHS : droite à 0,8 sous le seuil de %.3f pixels/classe ; arrêt : %s',
                        args.ghs_regression_min_count, report['stop_reason'])
        if args.ghs_tail_height > 0 and not tail['achieved']:
            LOGGER.info('AutoGHS : hauteur log cible %.3f non atteinte pour %s (mesure %s) ; '
                        'arrêt : %s', args.ghs_tail_height,
                        args.ghs_tail_channel, tail['height'], report['stop_reason'])
        return current, report

    def post_process(
        self, input_path: Path | str, output_path: Path | str, args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Exécute ce moteur sur un FITS et publie son audit sans écraser l'entrée."""
        import json
        from lib.auto_ghs import ComputeBackend, GHSOptimizer, HistogramAnalyzer
        from lib.restoration_quality import normalized_planes
        from lib.star_separation import write_rgb

        if Path(input_path).resolve() == Path(output_path).with_suffix('.fits').resolve():
            raise ValueError('La sortie doit être différente de la source')
        options = GHSOptimizer.options(self._get_args(args))
        backend = ComputeBackend(getattr(options, 'compute_backend', 'auto'))
        result, report = self.search(backend.xp.asarray(normalized_planes(input_path)),
                                     HistogramAnalyzer(options.ghs_bins, color_mode=options.ghs_color_mode),
                                     backend, options)
        destination = Path(output_path)
        report['output_image'] = str(destination.with_suffix('.fits'))
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_rgb(Path(input_path), destination.with_suffix('.fits'), backend.numpy(result),
                  nonlinear=True, role='STARLESS')
        destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
        return report
