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
from lib.parameterized_component import ParameterizedComponent
from lib.ghs_diagnostics import HistogramDiagnostics
from lib.ghs_blackpoint import HistogramBlackpointNormalizer
from lib.ghs_render_quality import HistogramRenderQuality
from lib.ghs_histogram_shape import (
    piecewise_histogram_profile, protect_histogram_shape,
    DEFAULT_SMOOTHING, DEFAULT_SHAPE_RETENTION, DEFAULT_SHAPE_DISTANCE, DEFAULT_PEAK_SHIFT,
    DEFAULT_SLOPE_STIFFNESS, DEFAULT_LOCAL_RETENTION,
    histogram_selection_key,
    PIECEWISE_SELECTION_POLICY,
)
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
# Trois chemins de familles/positions distinctes, reconstruits depuis l'entrée.
DEFAULT_SHAPE_BRANCHES = 3
MAX_SHAPE_BRANCHES = 8
# Deux classes de 128 : résolution de diversification et d'affinage du pic.
PEAK_POSITION_STEP = 2 / HISTOGRAM_BINS
PEAK_REFINEMENT_ROUNDS = 3
PEAK_REFINEMENTS_PER_ROUND = 8
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
# Les populations rares conservent au moins 45 % de leur contraste brillant.
DEFAULT_SPARSE_RETENTION = .45
SPARSE_TAIL_THRESHOLD = .8
# 262144 pixels : au plus 6 Mio environ par tableau RGB float64 temporaire.
DEFAULT_TRANSFORM_BLOCK_PIXELS = 262144
MAX_TRANSFORM_BLOCK_PIXELS = 4194304
BLACKPOINT_BINS = 1024
VALLEY_RATIO = .2
# 4096 strates et 16 représentants : au plus 65552 pixels, zéros à part.
POPULATION_STRATA = 4096
PIXELS_PER_STRATUM = 16


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
    population_sampling = False
    preserve_shape = False

    @classmethod
    def parameter_children(cls) -> tuple[type[ParameterizedComponent], ...]:
        """Déclare le classement de rendu et ses options sans état global partagé."""
        return (HistogramRenderQuality,)

    @staticmethod
    def population_sample(
        image: np.ndarray, color_mode: str,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Représente chaque strate de luminosité sans perdre les pixels rares.

        Parameters
        ----------
        image : numpy.ndarray
            Image CPU RGB normalisée, de forme (3, hauteur, largeur).
        color_mode : str
            Définition human ou even de la luminosité.

        Returns
        -------
        tuple of numpy.ndarray
            Pixels (3, 1, N), poids (1, N), et indices spatiaux aplatis (N,).
            Les poids somment à la population exacte ; chaque strate uniforme
            de 1/4096 fournit au plus 16 pixels répartis dans l'ordre spatial.
            Les valeurs Y <= 1e-6 occupent une strate distincte. Ce sondage
            estime les histogrammes ; les gardes restent spatiales et les
            finalistes sont toujours recomptés sur tous les pixels.
        """
        from lib.auto_ghs import luminance

        validate_rgb(image)
        light = luminance(image, color_mode).ravel()
        strata = np.minimum((light*POPULATION_STRATA).astype(np.int32), POPULATION_STRATA-1)
        strata[light <= 1e-6] = POPULATION_STRATA
        order = np.argsort(strata, kind='stable')
        sizes = np.bincount(strata, minlength=POPULATION_STRATA+1)
        indices: list[np.ndarray] = []
        weights: list[np.ndarray] = []
        start = 0
        for size in sizes:
            if size:
                count = min(int(size), PIXELS_PER_STRATUM)
                positions = np.linspace(start, start+size-1, count, dtype=np.int64)
                indices.append(order[positions])
                weights.append(np.full(count, size/count, dtype=np.float64))
            start += int(size)
        selected = np.concatenate(indices)
        return image.reshape(3, -1)[:, None, selected], np.concatenate(weights)[None], selected

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie les paramètres de la recherche itérative de longueur rectiligne."""
        return 'ghs_quartile'

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare passages, linéarité, seuil de droite, queue et perte noire."""
        HistogramRenderQuality.add_arguments(parser)
        parser.add_argument('--ghs-histogram-model', choices=('piecewise', 'line'), default='piecewise',
                            help='Profil par morceaux (piecewise, défaut) ou droite unique historique (line)')
        parser.add_argument('--ghs-shape-smoothing', type=float, default=DEFAULT_SMOOTHING,
                            help='Régularisation commune des splines de mesure (défaut 0,0001)')
        parser.add_argument('--ghs-shape-retention', type=float, default=DEFAULT_SHAPE_RETENTION,
                            help='Cible de conservation de décroissance logarithmique (défaut 0,65)')
        parser.add_argument('--ghs-shape-local-retention', type=float, default=DEFAULT_LOCAL_RETENTION,
                            help='Cible de conservation de chaque pente locale significative (défaut 0,1)')
        parser.add_argument('--ghs-shape-distance', type=float, default=DEFAULT_SHAPE_DISTANCE,
                            help='Seuil de pénalité de distance des formes normalisées (défaut 0,15)')
        parser.add_argument('--ghs-shape-peak-shift', type=float, default=DEFAULT_PEAK_SHIFT,
                            help='Limite du déplacement du pic après BP (défaut 0,25 ; départage des qualités égales)')
        parser.add_argument('--ghs-shape-branches', type=int, default=DEFAULT_SHAPE_BRANCHES,
                            help='Chemins de familles et positions du pic distinctes en mode piecewise (défaut 3 ; 1 à 8)')
        parser.add_argument('--ghs-shape-stiffness', type=float, default=DEFAULT_SLOPE_STIFFNESS,
                            help='Déformation relative maximale des pentes intermédiaires (défaut 1,25)')
        parser.add_argument('--ghs-post-blackpoint', choices=('rise', 'off'), default='rise',
                            help='En mode piecewise, recaler le BP avant conformité (rise, défaut) ou désactiver (off)')
        parser.add_argument('--ghs-quartile-iterations', type=int, default=12,
                            help='Passes GHS de régularisation du profil logarithmique, de 0 (désactivé) à 64')
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
            help='Conservation minimale du contraste brillant si la queue est rare (défaut 0,45)',
        )
        parser.add_argument('--ghs-linearity-tolerance', type=float, default=DEFAULT_LINEARITY_TOLERANCE,
                            help='RMS local maximal par morceau, ou de la droite en mode line, en dex (défaut 0,05)')
        parser.add_argument('--ghs-regression-min-count', type=float,
                            default=DEFAULT_REGRESSION_MIN_COUNT,
                            help='Niveau minimal du profil à 0,8 sur le graphique (défaut 10 pixels/classe ; 0 désactive)')
        parser.add_argument('--ghs-tail-height', type=float, default=DEFAULT_TAIL_HEIGHT,
                            help='Hauteur log cible à 0,875, relative au pic du canal [0,1] ; 0 désactive')
        parser.add_argument('--ghs-tail-channel', choices=TAIL_CHANNELS, default='red',
                            help='Canal de référence du niveau log (défaut red)')
        parser.add_argument('--ghs-blackpoint-loss', type=float, default=.001,
                            help='Fraction cumulée de pixels annulables par pic sombre isolé (0 à 0,01)')

    @staticmethod
    def validate_options(args: argparse.Namespace) -> None:
        """Refuse limites non finies, booléennes ou hors domaine avant calcul."""
        HistogramRenderQuality.validate_options(args)
        if getattr(args, 'ghs_histogram_model', 'piecewise') not in ('piecewise', 'line'):
            raise ValueError('Modèle histogramme GHS invalide')
        if getattr(args, 'ghs_post_blackpoint', 'rise') not in ('rise', 'off'):
            raise ValueError('ghs-post-blackpoint doit valoir rise ou off')
        if not 0 <= getattr(args, 'ghs_shape_peak_shift', DEFAULT_PEAK_SHIFT) <= .25:
            raise ValueError('ghs-shape-peak-shift doit être dans [0,0.25]')
        for name, low, high in (('ghs_shape_smoothing', 1e-8, .1),
                                ('ghs_shape_retention', 0., 1.), ('ghs_shape_distance', 0., 1.),
                                ('ghs_shape_stiffness', 0., 4.), ('ghs_shape_local_retention', 0., 1.)):
            value = getattr(args, name, {'ghs_shape_smoothing': DEFAULT_SMOOTHING,
                                        'ghs_shape_retention': DEFAULT_SHAPE_RETENTION,
                                        'ghs_shape_distance': DEFAULT_SHAPE_DISTANCE,
                                        'ghs_shape_stiffness': DEFAULT_SLOPE_STIFFNESS,
                                        'ghs_shape_local_retention': DEFAULT_LOCAL_RETENTION}[name])
            if ((name in ('ghs_shape_stiffness', 'ghs_shape_local_retention')
                 and isinstance(value, (bool, np.bool_)))
                    or not math.isfinite(value) or not low <= value <= high
                    or (name != 'ghs_shape_smoothing' and value == 0)):
                raise ValueError(name.replace('_', '-')+' hors domaine')
        if (type(args.ghs_quartile_iterations) is not int
                or not 0 <= args.ghs_quartile_iterations <= 64):
            raise ValueError('ghs-quartile-iterations doit être dans [0,64]')
        if (type(args.ghs_quartile_validations) is not int
                or not 1 <= args.ghs_quartile_validations <= 64):
            raise ValueError('ghs-quartile-validations doit être dans [1,64]')
        branches = getattr(args, 'ghs_shape_branches', DEFAULT_SHAPE_BRANCHES)
        if type(branches) is not int or not 1 <= branches <= MAX_SHAPE_BRANCHES:
            raise ValueError('ghs-shape-branches doit être dans [1,8]')
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
    def shape_candidate_family(record: JSONReport) -> tuple[str, float, int | None]:
        """Identifie la famille GHS et la classe de pic de largeur 2/128.

        record porte parameters et profile ou full_profile. Le recomptage
        complet prime ; une position absente conserve une classe distincte.
        """
        parameters = record['parameters']
        group = ('gentle' if parameters['D'] <= GENTLE_LIMIT else
                 'protected' if parameters['HP'] < PROTECTED_HP_LIMIT else 'broad')
        profile = record.get('full_profile', record.get('profile', {}))
        peak = profile.get('peak')
        slot = int(peak / PEAK_POSITION_STEP) if peak is not None else None
        return group, parameters['B'], slot

    @staticmethod
    def shape_candidate_order(records: list[JSONReport]) -> list[JSONReport]:
        """Réserve des positions de pic et familles avant leurs variantes proches.

        Parameters
        ----------
        records : list[JSONReport]
            Essais avec parameters et profile ou full_profile, après BP.

        Returns
        -------
        list[JSONReport]
            Tous les essais sans mutation. Le meilleur classement ouvre la
            liste. Ensuite les nouvelles familles (D faible, HP protégé,
            courbe large, avec B) et classes de pic de 2/128 sont privilégiées,
            puis la distance au pic déjà exploré et le classement. Le sens
            du déplacement ne reçoit aucun bonus. Les doublons de famille
            et classe attendent que chaque groupe ait eu un représentant.
        """
        def measured(record: JSONReport) -> JSONReport:
            """Préfère un recomptage complet à sa prédiction échantillonnée."""
            return record.get('full_profile', record.get('profile', {}))

        def family(record: JSONReport) -> tuple[str, float]:
            """Sépare force douce, protection précoce et courbe large par B."""
            group, b, _ = QuartileGHSProcessor.shape_candidate_family(record)
            return group, b

        ranked = sorted(records, key=lambda item: histogram_selection_key(measured(item)), reverse=True)
        groups: dict[tuple[tuple[str, float], int | None], deque[JSONReport]] = {}
        for record in ranked:
            peak = measured(record).get('peak')
            slot = int(peak / PEAK_POSITION_STEP) if peak is not None else None
            groups.setdefault((family(record), slot), deque()).append(record)
        ordered: list[JSONReport] = []
        peaks: list[float] = []
        families: set[tuple[str, float]] = set()
        slots: set[int | None] = set()
        visited: set[tuple[tuple[str, float], int | None]] = set()
        while groups:
            def priority(
                key: tuple[tuple[str, float], int | None],
            ) -> tuple[int, int, float, tuple[bool, int, float, float]]:
                """Favorise une couverture nouvelle, sans confondre absence et zéro."""
                record = groups[key][0]
                peak = measured(record).get('peak')
                distance = min((abs(peak - old) for old in peaks), default=0.) if peak is not None else -1.
                novelty = int(key[0] not in families) + int(key[1] not in slots)
                return (int(key not in visited), novelty if ordered else 0, distance if ordered else 0.,
                        histogram_selection_key(measured(record)))

            key = max(groups, key=priority)
            record = groups[key].popleft()
            ordered.append(record)
            peak = measured(record).get('peak')
            if peak is not None:
                peaks.append(peak)
            families.add(key[0])
            slots.add(key[1])
            visited.add(key)
            if not groups[key]:
                del groups[key]
        return ordered

    @staticmethod
    def peak_gap_parameters(records: list[JSONReport]) -> list[JSONReport]:
        """Bissecte les plus grands intervalles de pic selon D et SP séparément.

        Les records portent parameters, BP et un profile contenant peak.
        À autres paramètres fixes, le milieu de deux D ou SP voisins est
        essayé lorsque leurs pics diffèrent de plus de 2/128. L'identité de
        chaque BP ancre toutes ses familles en D. Les deux axes alternent,
        avec au plus huit propositions retournées,
        sans réutiliser des essais d'une autre image ou d'une autre branche.
        Une transition de mode du pic peut rester discontinue malgré D continu.
        """
        identities = {item['BP']: item for item in records if item['parameters']['D'] == 0}
        queues = []
        for axis in ('D', 'SP'):
            families: dict[tuple[float, ...], list[JSONReport]] = {}
            fixed_parameters = [name for name in ('D', 'B', 'SP', 'LP', 'HP') if name != axis]
            for record in records:
                parameters = record['parameters']
                if record['profile'].get('peak') is None or parameters['D'] == 0:
                    continue
                key = (record['BP'], *(parameters[name] for name in fixed_parameters))
                families.setdefault(key, []).append(record)
            gaps = []
            for key, members in families.items():
                if axis == 'D' and key[0] in identities:
                    members = [identities[key[0]], *members]
                members = sorted(members, key=lambda item: item['parameters'][axis])
                for left, right in zip(members, members[1:]):
                    first, last = left['profile'].get('peak'), right['profile'].get('peak')
                    if (first is None or last is None
                            or abs(last-first) <= PEAK_POSITION_STEP+np.finfo(float).eps):
                        continue
                    value = (left['parameters'][axis]+right['parameters'][axis])/2
                    parameters = dict(right['parameters'], **{axis: value})
                    gaps.append((abs(last-first), dict(BP=key[0], parameters=parameters)))
            gaps.sort(key=lambda item: item[0], reverse=True)
            queues.append(deque(record for _, record in gaps))
        proposals = []
        while any(queues) and len(proposals) < PEAK_REFINEMENTS_PER_ROUND:
            for queue in queues:
                if queue and len(proposals) < PEAK_REFINEMENTS_PER_ROUND:
                    proposals.append(queue.popleft())
        return proposals

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
        reference_shape: JSONReport | None = None,
        trial_images: HistogramDiagnostics | None = None,
        blackpoint_reference: np.ndarray | None = None,
    ) -> tuple[np.ndarray, JSONReport]:
        """Classe des passes CPU échantillonnées puis valide sur le backend choisi.

        D explore douze forces et des forces faibles ; B=15 et B=5. SP explore
        zéro, le pic et les ruptures de pente. HP inclut les raccords précoces
        Q90/Q95/Q98, 0,25 et 0,5, ainsi que 0,75 et 1. Les étoiles restent fixes
        dans la recomposition. Le niveau log est mesuré sur cette recomposition
        lorsqu'elle est disponible, sinon sur le fond seul.
        La droite doit atteindre le seuil configuré à 0,8 sur l’échelle des
        effectifs du graphique ; sous le seuil, sa progression guide les passes.
        Elle commence à la sortie du pic, sans saut vers un segment éloigné.
        population_sampling remplace uniquement les prédictions d'histogramme
        par un sondage stratifié pondéré ; les gardes spatiales et le recomptage
        complet restent identiques. Les poids ne modifient jamais les pixels
        du candidat final ni ceux des étoiles.
        Les finalistes sont diversifiés et contrôlés jusqu'au budget configuré
        (12 par défaut), même après les premiers rejets en pleine résolution.
        En mode piecewise, ce budget est partagé entre les chemins d'une même
        profondeur. Jusqu'à trois chemins distincts par famille et classe de
        pic sont reconstruits depuis l'entrée ; aucune image de branche n'est
        gardée sur GPU. Les grands intervalles de pic d'une famille sont
        affinés par bissection de D et SP, au plus 24 essais estimés par parent.
        Le meilleur candidat complet rencontré reste disponible en sortie,
        même si sa branche cesse de progresser. Aucun essai historique n'est lu.
        Un échantillon ciblé des régions brillantes contrôle contraste et
        couleur avant validation complète, sans estimer le bruit ni les pertes.
        Une queue rare Y>=0,8 abaisse la conservation brillante jusqu'au plancher
        configuré, depuis une population initiale figée ; le cœur reste protégé
        par la conservation normale.
        Le bruit et les pertes de contraste/couleur sont comparés à l'entrée,
        empêchant l'accumulation des tolérances. Les transformations sont faites
        par blocs ; seuls les paramètres du meilleur essai sont retenus, puis
        sa sortie est recalculée une fois par passe. La source reste intacte.
        reference_shape permet au mode piecewise de garder la référence
        antérieure à l'égalisation ; None utilise l'entrée de cette recherche.
        trial_images enregistre les images des essais majeurs. Les refus
        estimés sont recalculés sur tous les pixels seulement pour cet export,
        sans changer leur classement ni certifier leur histogramme estimé.
        En mode piecewise, le recalage affine post-GHS précède histogrammes
        et gardes. blackpoint_reference fixe les canaux positifs à protéger
        dans toutes les phases ; None utilise l'entrée de cette recherche.
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
        align_blackpoint = self.preserve_shape and args.ghs_post_blackpoint == 'rise'
        blackpoint_reference = start if blackpoint_reference is None else blackpoint_reference
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
        if self.preserve_shape:
            report.update(blackpoint_order='GHS, linear BP, compliance',
                          noise_reference='diffuse_contrast',
                          selection_policy=PIECEWISE_SELECTION_POLICY,
                          blackpoint_reference_role=('initial_linear_positive_channels'
                                                     if blackpoint_reference is not start else 'phase_entry'))
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
        quality_reference_light = (luminance(sample_start, analyzer.color_mode)
                                   if self.preserve_shape else None)
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

        def histogram(
            image: np.ndarray, engine: ModuleType, weights: np.ndarray | None = None,
        ) -> np.ndarray:
            """Compte Y actif, avec poids de population optionnels sur CPU.

            Les poids ont la géométrie spatiale de l'image et sont déjà en
            pixels du champ entier. Sur CUDA seuls les 128 effectifs sortent
            du GPU ; les mesures complètes n'utilisent jamais de poids.
            """
            light = luminance(image, analyzer.color_mode, xp=engine)
            active = light > 1e-6
            counts = engine.histogram(light[active], bins=HISTOGRAM_BINS, range=(0, 1),
                                      weights=weights[active] if weights is not None else None)[0]
            return counts if engine is np else backend.numpy(counts)

        def tail_profile(
            image: np.ndarray, engine: ModuleType, channel: str,
            weights: np.ndarray | None = None, sampled_stars: np.ndarray | None = None,
        ) -> JSONReport:
            """Mesure le canal rendu avec les mêmes étoiles et poids spatiaux.

            sampled_stars correspond aux coordonnées du sondage stratifié.
            Ses poids expriment la population complète et ne sont pas
            redimensionnés une deuxième fois. estimated reste vrai jusqu'au
            recomptage non pondéré du champ entier.
            """
            index = TAIL_CHANNELS.index(channel)
            plane = image[index]
            if stars is not None:
                # La hauteur vise le rendu visible ; la longueur reste mesurée sur le fond.
                field = (sampled_stars if weights is not None else
                         sample_stars if engine is np and image.shape == sample_start.shape else stars)
                plane = headroom*plane + engine.asarray(field[index])*(1-headroom*plane)
            counts = engine.histogram(plane, bins=TAIL_HISTOGRAM_BINS, range=(0, 1), weights=weights)[0]
            counts = counts if engine is np else backend.numpy(counts)
            scale = 1.0 if weights is not None else data.shape[1]*data.shape[2]/(image.shape[1]*image.shape[2])
            result = log_tail_height(counts, scale)
            result['estimated'] = bool(weights is not None or result['estimated'])
            return result

        shape_reference = reference_shape if self.preserve_shape else None
        minimum_gain = MIN_IMPROVEMENT/100 if self.preserve_shape else MIN_IMPROVEMENT

        def profile(
            image: np.ndarray, engine: ModuleType, weights: np.ndarray | None = None,
            sampled_stars: np.ndarray | None = None,
        ) -> JSONReport:
            """Combine longueur et hauteur avec les mêmes unités d'effectifs."""
            reference = REGRESSION_REFERENCE_INTENSITY if args.ghs_regression_min_count > 0 else None
            counts = histogram(image, engine, weights)
            line = (piecewise_histogram_profile(counts, args.ghs_linearity_tolerance,
                                                args.ghs_shape_smoothing)
                    if self.preserve_shape else
                    quartile_profile(counts, args.ghs_linearity_tolerance, reference))
            if self.preserve_shape and shape_reference is not None:
                line = protect_histogram_shape(line, shape_reference, args.ghs_shape_retention,
                                               args.ghs_shape_distance, args.ghs_shape_peak_shift,
                                               args.ghs_shape_stiffness, args.ghs_shape_local_retention)
            tail = tail_profile(image, engine, args.ghs_tail_channel, weights, sampled_stars)
            combined = add_tail_height_objective(
                line, tail, args.ghs_tail_height, args.ghs_tail_channel,
            )
            scale = 1.0 if weights is not None else data.shape[1]*data.shape[2]/(image.shape[1]*image.shape[2])
            result = add_regression_floor_objective(
                combined, analyzer.bins, scale, args.ghs_regression_min_count,
            )
            result['regression_floor']['estimated'] = bool(weights is not None or scale != 1)
            return result

        def improvement(before: JSONReport, after: JSONReport) -> float:
            """Compare la qualité en piecewise, ou la progression historique en line."""
            if after['peak'] is None or after['peak'] >= .25:
                return -math.inf
            if (self.preserve_achieved_tail and not before['tail']['estimated'] and before['tail']['achieved']
                    and not after['tail']['achieved']):
                return -math.inf
            if self.preserve_shape:
                first, last = histogram_selection_key(before), histogram_selection_key(after)
                if not last[0]:
                    return -math.inf
                if last <= first:
                    return -math.inf
                if last[:2] != first[:2]:
                    return 1.0
                # Le déplacement ne départage que deux scores de qualité égaux.
                return max(minimum_gain*2, last[2]-first[2], last[3]-first[3])
            return after['score']-before['score']

        def grade(
            measured_profile: JSONReport, measured: JSONReport, *, estimated: bool,
            composite: JSONReport | None = None,
        ) -> JSONReport:
            """Applique le même compromis, sur grille spatiale ou champ entier."""
            return HistogramRenderQuality.attach(
                measured_profile, sample_before if estimated else full_before, measured,
                shape_reference, bright_retention=effective_retention,
                cnr_floor=args.ghs_quality_cnr_floor, tail_bonus=args.ghs_quality_tail_bonus,
                composite_before=sample_fused_before if estimated else fused_before,
                composite_after=composite, estimated=estimated,
            )

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
                    if self.preserve_shape and key != 'core_contrast' and final is not None:
                        # La compression brillante est une pénalité continue.
                        # Une mesure manquante requise ne reçoit pas ce bénéfice.
                        continue
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
                if not regions_only:
                    noise = fixed.noise_assessment(first, last, relative=self.preserve_shape)
                    last['noise_assessment'] = noise
                    reasons.extend(noise['rejections'])
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

        if self.preserve_shape:
            shape_reference = shape_reference or piecewise_histogram_profile(
                histogram(start, xp), args.ghs_linearity_tolerance, args.ghs_shape_smoothing,
            )
            report.update(shape_reference=shape_reference, histogram_model='piecewise',
                          objective='piecewise_log_histogram_with_shape_preservation')

        def explore_branch(
            current: np.ndarray, iteration: int, validation_limit: int, parent_id: str,
            current_profile: JSONReport | None = None,
        ) -> tuple[JSONReport, list[JSONReport]]:
            """Évalue une génération depuis un seul parent, sous les gardes d'entrée.

            Les images ne quittent pas cette génération ; seuls les audits,
            paramètres et histogrammes CPU des enfants admissibles subsistent.
            validation_limit partage le budget de profondeur entre parents.
            """
            sample = backend.numpy(current[:, ::stride, ::stride])
            scoring, weights, scoring_stars = sample, None, None
            scoring_reference = backend.numpy(blackpoint_reference[:, ::stride, ::stride])
            if self.population_sampling:
                scoring, weights, indices = self.population_sample(backend.numpy(current), analyzer.color_mode)
                scoring_stars = (backend.numpy(stars).reshape(3, -1)[:, indices][:, None]
                                 if stars is not None else None)
                scoring_reference = backend.numpy(
                    blackpoint_reference.reshape(3, -1)[:, indices],
                )[:, None]
                report['histogram_sampling'] = dict(method='intensity_strata', strata=POPULATION_STRATA,
                                                     pixels=scoring.shape[2], population=float(weights.sum()))
            before = profile(scoring, np, weights, scoring_stars)
            if self.preserve_shape:
                quality_before = HistogramRenderQuality.sample_measure(
                    quality_reference_light, sample, thresholds, analyzer.color_mode,
                )
                before = grade(before, quality_before, estimated=True)
            bp = isolated_blackpoint(sample, args.ghs_blackpoint_loss)
            minimum = float(xp.min(xp.where(current > 0, current, xp.inf)).item())
            minimum_bp = minimum * (1 - 1e-5) if math.isfinite(minimum) else 0.0
            candidates = []
            refinement_records: list[JSONReport] = []
            major_estimated_rejected = None
            points = sorted(set([0.0, minimum_bp, bp]))
            if self.preserve_shape:
                # Des BP inférieurs à un epsilon d'intensité ajoutaient des
                # doublons presque identiques au budget plein champ.
                epsilon = np.finfo(data.dtype).eps
                points = [point for point in points if point == 0 or point >= epsilon]

            def evaluate(point: float, params: dict[str, float]) -> None:
                """Mesure une courbe après BP et mémorise son pic pour l'affinage."""
                nonlocal major_estimated_rejected
                shifted = linear_blackpoint(scoring, point) if point > 0 else scoring
                transformed = transform(shifted, params, np)
                post: JSONReport = {'BP': 0., 'status': 'disabled'}
                if align_blackpoint:
                    transformed, post = HistogramBlackpointNormalizer.apply(
                        transformed, args.ghs_blackpoint_loss, weights=weights,
                        reference=scoring_reference,
                    )
                after = profile(transformed, np, weights, scoring_stars)
                if self.preserve_shape:
                    spatial = transformed if weights is None else transform(sample, params, np, point)
                    if weights is not None and post['BP'] > 0:
                        spatial = linear_blackpoint(spatial, post['BP'])
                    measured_quality = HistogramRenderQuality.sample_measure(
                        quality_reference_light, spatial, thresholds, analyzer.color_mode,
                    )
                    after = grade(after, measured_quality, estimated=True)
                if self.preserve_shape:
                    refinement_records.append(dict(parameters=params, BP=point,
                                                   profile={'peak': after.get('peak')}))
                gain = improvement(before, after)
                if gain > minimum_gain:
                    shape_reasons = (HistogramRenderQuality.hard_shape_rejections(after)
                                     if self.preserve_shape else
                                     after.get('shape_preservation', {}).get('rejections', []))
                    if shape_reasons and (major_estimated_rejected is None
                                          or gain > major_estimated_rejected['gain']):
                        light = luminance(transformed, analyzer.color_mode)
                        counts = np.histogram(light, bins=analyzer.bins, range=(0, 1), weights=weights)[0]
                        if weights is None:
                            counts = counts*(data.shape[1]*data.shape[2]/light.size)
                        major_estimated_rejected = dict(parameters=params, BP=point, gain=gain,
                                                         post_BP=post['BP'], post_blackpoint=post,
                                                         profile=after, estimated=True,
                                                         rejections=shape_reasons.copy(),
                                                         histogram={'counts': counts.tolist()})
                    if self.preserve_shape and after.get('shape'):
                        after = dict(after)
                        after['shape'] = {key: value for key, value in after['shape'].items()
                                          if key not in ('x', 'log_density')}
                        after.pop('legacy_line', None)
                    candidates.append({'parameters': params, 'BP': point,
                                       'post_BP': post['BP'], 'post_blackpoint': post,
                                       'gain': gain, 'profile': after})

            for point in points:
                shifted = linear_blackpoint(scoring, point) if point > 0 else scoring
                for params in self.candidate_parameters(
                    histogram(shifted, np, weights), args.ghs_quartile_max_strength, protected_hp,
                ):
                    evaluate(point, params)
            refined_count = 0
            if self.preserve_shape:
                for _ in range(PEAK_REFINEMENT_ROUNDS):
                    proposals = self.peak_gap_parameters(refinement_records)
                    if not proposals:
                        break
                    for proposal in proposals:
                        evaluate(proposal['BP'], proposal['parameters'])
                    refined_count += len(proposals)
                candidates = self.shape_candidate_order(candidates)
            else:
                candidates = self.diverse_candidates(candidates)
            entry: JSONReport = {'iteration': iteration + 1, 'before': before,
                                 'candidate_count': len(candidates),
                                 'validations': [], 'sample_rejections': [], 'accepted': None}
            if self.preserve_shape:
                entry.update(parent_branch_id=parent_id, peak_refinements=refined_count,
                             validation_limit=validation_limit)
            if major_estimated_rejected is not None:
                entry['major_estimated_rejected'] = major_estimated_rejected
            full_profile = current_profile if current_profile is not None else profile(current, xp)
            current_probe = probe(current) if probe_start is not None else None
            best = None
            verified: list[JSONReport] = []
            for record in candidates:
                if len(entry['validations']) >= validation_limit:
                    break
                point = record['BP']
                shifted_sample = linear_blackpoint(sample, point) if point > 0 else sample
                candidate_sample = transform(shifted_sample, record['parameters'], np)
                if record['post_BP'] > 0:
                    candidate_sample = linear_blackpoint(candidate_sample, record['post_BP'])
                rejected, _, _ = guards(candidate_sample, sample_start, sample_before, np,
                                      sample_stars, sample_fused, sample_fused_before)
                if align_blackpoint:
                    # Le sondage spatial ne certifie pas une perte de 0,1 %.
                    # Le recalage et la garde exacte sont refaits sur tout le champ.
                    rejected = [reason for reason in rejected if reason != 'cumulative blackpoint loss']
                rejected.extend(HistogramRenderQuality.hard_shape_rejections(record['profile'])
                                if self.preserve_shape else
                                record['profile'].get('shape_preservation', {}).get('rejections', []))
                if rejected:
                    entry['sample_rejections'].append(dict(record, rejections=rejected))
                    continue
                if current_probe is not None:
                    shifted_probe = (linear_blackpoint(current_probe, point)
                                     if point > 0 else current_probe)
                    candidate_probe = transform(shifted_probe, record['parameters'], np)
                    if record['post_BP'] > 0:
                        candidate_probe = linear_blackpoint(candidate_probe, record['post_BP'])
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
                full_post: JSONReport = {'BP': 0., 'status': 'disabled'}
                if align_blackpoint:
                    candidate, full_post = HistogramBlackpointNormalizer.apply(
                        candidate, args.ghs_blackpoint_loss, xp,
                        reference=blackpoint_reference,
                    )
                after = profile(candidate, xp)
                rejected, loss, protection = guards(candidate, start, full_before, xp, stars,
                                         fused_start, fused_before)
                if self.preserve_shape:
                    after = grade(after, protection['starless'], estimated=False,
                                  composite=protection.get('composite'))
                gain = improvement(full_profile, after)
                if align_blackpoint and full_post['loss_fraction'] > args.ghs_blackpoint_loss+1e-12:
                    rejected.append('cumulative blackpoint loss')
                rejected.extend(HistogramRenderQuality.hard_shape_rejections(after)
                                if self.preserve_shape else
                                after.get('shape_preservation', {}).get('rejections', []))
                if gain <= minimum_gain:
                    rejected.append('no full-resolution render quality improvement'
                                    if self.preserve_shape else
                                    'no full-resolution line/height improvement')
                audit = dict(record, full_profile=after, full_gain=gain if math.isfinite(gain) else None,
                             post_BP=full_post['BP'], post_blackpoint=full_post,
                             sample_post_blackpoint=record['post_blackpoint'],
                             rejections=rejected,
                             blackpoint_loss=(full_post['loss_fraction'] if align_blackpoint else loss),
                             protection=protection)
                if not rejected:
                    light = luminance(candidate, analyzer.color_mode, xp=xp)
                    audit['histogram'] = {'counts': backend.numpy(
                        xp.histogram(light, bins=analyzer.bins, range=(0, 1))[0]).tolist()}
                    del light
                    verified.append(audit)
                    if self.preserve_shape and trial_images is not None:
                        trial_images.capture(audit, candidate)
                if rejected and math.isfinite(gain) and (entry.get('major_rejected') is None
                                 or gain > entry['major_rejected']['full_gain']):
                    light = luminance(candidate, analyzer.color_mode, xp=xp)
                    counts = backend.numpy(xp.histogram(light, bins=analyzer.bins, range=(0, 1))[0])
                    entry['major_rejected'] = dict(parameters=record['parameters'], BP=point,
                                                    post_BP=full_post['BP'], post_blackpoint=full_post,
                                                    full_gain=gain, rejections=rejected.copy(),
                                                    profile=after, histogram={'counts': counts.tolist()})
                    del light
                entry['validations'].append(audit)
                LOGGER.info(
                    'AutoGHS droite log : passe %d, parent %s, validation %d/%d, '
                    'D=%.3f B=%.1f SP=%.5f HP=%.5f : %s',
                    iteration + 1, parent_id, len(entry['validations']),
                    validation_limit, record['parameters']['D'],
                    record['parameters']['B'], record['parameters']['SP'],
                    record['parameters']['HP'],
                    ', '.join(rejected) if rejected else 'admissible',
                )
                better = (best is None or (
                    histogram_selection_key(after) > histogram_selection_key(best[1]['full_profile'])
                    if self.preserve_shape else gain > best[0]))
                if not rejected and better:
                    best = (gain, audit)
                # Les rapports ne portent que des paramètres et mesures CPU.
                del candidate
            examined = len(entry['sample_rejections']) + len(entry['validations'])
            entry['unexamined_candidates'] = len(candidates) - examined
            entry['validation_budget_exhausted'] = examined < len(candidates)
            if trial_images is not None:
                for rejected in (entry.get('major_rejected'), entry.get('major_estimated_rejected')):
                    if rejected is not None:
                        rejected_image = transform(current, rejected['parameters'], xp, rejected['BP'])
                        if rejected.get('post_BP', 0.) > 0:
                            rejected_image = linear_blackpoint(rejected_image, rejected['post_BP'], xp=xp)
                        trial_images.capture(rejected, rejected_image)
                        del rejected_image
            if best is None:
                return entry, verified
            entry['accepted'] = best[1]
            return entry, verified

        def apply_step(image: np.ndarray, step: JSONReport) -> np.ndarray:
            """Rejoue exactement GHS puis BP enregistrés, sans refaire le classement."""
            result = transform(image, step['parameters'], xp, step['BP'])
            if step.get('post_BP', 0.) > 0:
                result = linear_blackpoint(result, step['post_BP'], xp=xp)
            return result

        def replay(steps: list[JSONReport]) -> np.ndarray:
            """Reconstruit un chemin de l'exécution courante, sans cache d'images GPU."""
            image = start
            for step in steps:
                image = apply_step(image, step)
            return image

        if self.preserve_shape:
            width = min(getattr(args, 'ghs_shape_branches', DEFAULT_SHAPE_BRANCHES),
                        args.ghs_quartile_validations)
            root = dict(branch_id='root', steps=[], full_profile=grade(
                profile(start, xp), full_before, estimated=False, composite=fused_before,
            ))
            frontier, winner = [root], root
            report.update(search_method='diverse_beam', max_branches=width,
                          peak_position_step=PEAK_POSITION_STEP,
                          peak_refinement_limit=PEAK_REFINEMENT_ROUNDS*PEAK_REFINEMENTS_PER_ROUND,
                          branch_storage='parameters_only', branch_generations=[])
            next_id = 0
            for depth in range(args.ghs_quartile_iterations):
                children = []
                remaining = args.ghs_quartile_validations
                generation = []
                for index, parent in enumerate(frontier):
                    current = replay(parent['steps'])
                    quota = remaining // (len(frontier)-index)
                    entry, verified = explore_branch(current, depth, quota, parent['branch_id'],
                                                     parent['full_profile'])
                    del current
                    remaining -= len(entry['validations'])
                    report['iterations'].append(entry)
                    entry['verified_candidates'] = verified
                    for audit in verified:
                        next_id += 1
                        audit.update(branch_id=f'b{next_id}', parent_branch_id=parent['branch_id'])
                        step = {key: audit[key] for key in ('parameters', 'BP', 'post_BP', 'branch_id')}
                        child = dict(audit, steps=[*parent['steps'], step])
                        children.append(child)
                        if histogram_selection_key(child['full_profile']) > histogram_selection_key(winner['full_profile']):
                            winner = child
                    generation.append(entry)
                retained = []
                families = set()
                for child in self.shape_candidate_order(children):
                    family = self.shape_candidate_family(child)
                    if family not in families:
                        retained.append(child)
                        families.add(family)
                    if len(retained) == width:
                        break
                identifiers = {child['branch_id'] for child in retained}
                for entry in generation:
                    for audit in entry['verified_candidates']:
                        audit['continued'] = audit['branch_id'] in identifiers
                report['branch_generations'].append(dict(
                    depth=depth+1, parents=[parent['branch_id'] for parent in frontier],
                    retained=[child['branch_id'] for child in retained],
                    validations=args.ghs_quartile_validations-remaining,
                ))
                LOGGER.info('AutoGHS branches : profondeur %d, %d parents, %d enfants admissibles, '
                            '%d chemins conservés ; pics %s', depth+1, len(frontier), len(children),
                            len(retained), ', '.join(f"{child['full_profile']['peak']:.4f}" for child in retained))
                if not retained:
                    report['stop_reason'] = (
                        'full-resolution validation budget exhausted'
                        if any(entry['validation_budget_exhausted'] for entry in generation)
                        else 'no admissible render quality improvement'
                    )
                    break
                frontier = retained
            current = replay(winner['steps'])
            report.update(selected_branch_id=winner['branch_id'], selected_path=winner['steps'],
                          depths_explored=len(report['branch_generations']))
            selected_ids = {step['branch_id'] for step in winner['steps']}
            for entry in report['iterations']:
                for audit in entry['verified_candidates']:
                    audit['on_selected_path'] = audit['branch_id'] in selected_ids
            if winner['steps']:
                report.update(status='applied', blackpoint_loss=winner['blackpoint_loss'])
        else:
            for iteration in range(args.ghs_quartile_iterations):
                entry, _ = explore_branch(current, iteration, args.ghs_quartile_validations, 'root')
                report['iterations'].append(entry)
                accepted = entry['accepted']
                if accepted is None:
                    report['stop_reason'] = (
                        'full-resolution validation budget exhausted'
                        if entry['validation_budget_exhausted']
                        else 'no admissible line/height improvement'
                    )
                    break
                current = apply_step(current, accepted)
                if trial_images is not None:
                    trial_images.capture(accepted, current)
                report.update(status='applied', blackpoint_loss=accepted['blackpoint_loss'])
                LOGGER.info('AutoGHS droite log : passe %d D=%.3f B=%.1f SP=%.5f HP=%.2f '
                            'BP=%.5f perte=%.6g longueur=%.5f hauteur=%s', iteration+1,
                            accepted['parameters']['D'], accepted['parameters']['B'],
                            accepted['parameters']['SP'], accepted['parameters']['HP'], accepted['BP'],
                            accepted['blackpoint_loss'], accepted['full_profile']['line_span'],
                            accepted['full_profile']['tail']['height'])
        report['before'] = root['full_profile'] if self.preserve_shape else profile(start, xp)
        report['after'] = winner['full_profile'] if self.preserve_shape else profile(current, xp)
        if self.preserve_shape:
            report['quality_sufficient'] = report['after']['render_quality'].get('sufficient_cnr', False)
        report['tail_channels_before'] = {channel: tail_profile(start, xp, channel) for channel in TAIL_CHANNELS}
        report['tail_channels_after'] = {channel: tail_profile(current, xp, channel) for channel in TAIL_CHANNELS}
        tail = report['after']['tail']
        report['tail_height_achieved'] = tail['achieved']
        floor = report['after']['regression_floor']
        report['regression_floor_achieved'] = floor['achieved']
        report['targets_achieved'] = bool(
            report['after']['measurable']
            and (not self.preserve_shape or
                 report['after'].get('shape_preservation', {}).get('achieved') is True)
            and (args.ghs_regression_min_count == 0 or floor['achieved'])
            and (args.ghs_tail_height == 0 or tail['achieved'])
        )
        if args.ghs_regression_min_count > 0 and not floor['achieved']:
            if not floor['reference_covered']:
                LOGGER.info('AutoGHS : aucune droite depuis la sortie du pic ne couvre 0,8 ; arrêt : %s',
                            report['stop_reason'])
            else:
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
