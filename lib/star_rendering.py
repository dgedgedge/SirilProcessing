"""Rendu stellaire adaptatif et recomposition screen contrôlée.

L'asinh puis les quantiles des pics renforcent les étoiles faibles.
La même transformation RGB est appliquée partout, sans masque ni débruitage.
Le gain commun aux trois canaux préserve les rapports de couleur sans présumer les primaires du FITS.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import logging
from pathlib import Path

import numpy as np
from scipy.ndimage import label, maximum_filter

from lib.processor import processor
from lib.ghs_diagnostics import HistogramDiagnostics
from lib.stretch_transfer import (
    asinh_rgb, stellar_operation,
    protected_stellar_target, MONOTONE_BRIGHT_PROTECTION,
)
from lib.restoration_quality import normalized_planes
from lib.star_separation import validate_rgb, write_rgb
from lib.type_defs import JSONReport
from lib.stellar_appearance import StellarAppearanceQuality

VISIBILITY_LEVEL = 0.03  # Contribution normalisée, indicateur d'affichage seulement.
FRACTION_VISIBLE_TARGET = 0.08  # Sommet visé au quantile retenu, affichage direct.
FRACTION_BRIGHT_TARGET = 0.15  # Au-delà, conserver la base et les hautes lumières.
PEAK_QUANTILE_COUNT = 17
PEAK_QUANTILE_RANGE = (0.05, 0.95)  # Exclure les extrêmes du calage des pics.
PEAK_OUTPUT_RANGE = (0.04, 0.65)  # Niveaux visés pour un plafond de référence 0,85.
REFERENCE_STAR_CEILING = 0.85
MAX_BALANCE_ATTEMPTS = 8  # Réductions par moitiés avant de conserver le gain fixe.
FAINT_BOOST_STEP = 1.0  # Décrément du renforcement de base, jusqu'au minimum 1.
LOGGER = logging.getLogger(__name__)
NOISE_SIGMA_FACTOR = 0.0  # Aucun débruitage dans le rendu stellaire global.
NOISE_PRECISION_FLOOR = 1e-7  # Intensité normalisée ; éviter d'interpréter les queues numériques.
MIN_PROFILE_RATIO = 0.90  # Plancher dur de FWHM et début de pénalité pour ailes/jupes.
MIN_TAIL_PROFILE_RATIO = 0.60  # Limite dure des ailes/jupes ; sous 0,90, pénalité progressive.
TAIL_WIDTH_TOLERANCE = 1.10  # Dépassement d'ailes borné à 10 % de la limite commune.
MAX_MISSING_PROFILE_FRACTION = .05  # Petits effectifs inconnus pénalisés ; au-delà, rejet.
PROFILE_RADIUS = 7
EXPANDED_PROFILE_RADIUS = 15


def stellar_noise_estimate(light: np.ndarray) -> tuple[float, float]:
    """Estime le niveau résiduel et sa dispersion par différences voisines.

    Parameters
    ----------
    light : np.ndarray
        Maximum RGB 2D normalisé, fini, dans [0,1].

    Returns
    -------
    tuple[float, float]
        Médiane et sigma robuste des différences, en intensités normalisées.
        Le minimum des deux axes limite la confusion avec un gradient axial.
        Une dimension inférieure à trois ou une dispersion sous 1e-7 désactive
        retourne un sigma nul. Le bruit corrélé, écrêté à zéro ou un champ
        très dense peut biaiser cette estimation ; elle ne filtre pas les pixels
        et ne constitue pas un SNR photométrique.
    """
    if min(light.shape) < 3:
        return float(np.median(light)), 0.0
    estimates = []
    for axis in (0, 1):
        differences = np.diff(light, axis=axis)
        mad = float(np.median(abs(differences - np.median(differences))))
        estimates.append(1.4826 * mad / np.sqrt(2))
    sigma = float(min(estimates))
    return float(np.median(light)), sigma if sigma > NOISE_PRECISION_FLOOR else 0.0


def stellar_peak_positions(data: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Repère des candidats à 7 pixels, sans prétendre détecter de vraies étoiles.

    Le seuil max(1e-5, médiane + 5 MAD normalisées) écarte les résidus proches
    de zéro. Chaque plateau connexe ne fournit qu'un pic. Les mêmes positions
    sont ensuite utilisées avant/après ; aucune nouvelle source n'est comptée.
    """
    light = data.max(axis=0)
    if np.ptp(light) == 0:
        return np.array([], dtype=int), np.array([], dtype=int)
    floor = float(np.median(light))
    noise = 1.4826 * float(np.median(abs(light - floor)))
    maxima = (light == maximum_filter(light, size=7))
    maxima &= light > max(1e-5, floor + 5 * noise)
    groups, count = label(maxima)
    yy, xx = np.nonzero(maxima)
    if not count:
        return yy, xx
    _, indices = np.unique(groups[yy, xx], return_index=True)
    return yy[indices], xx[indices]


def peak_visibility(
    data: np.ndarray, positions: tuple[np.ndarray, np.ndarray],
) -> JSONReport:
    """Compte les pics au-dessus de 0,03 en max RGB, sans modèle de perception.

    Ce seuil d'affichage sur [0,1] ne mesure ni SNR ni détection astrophysique.
    Appliqué à la contribution screen, il mesure le contraste ajouté au fond.
    """
    values = data[:, positions[0], positions[1]].max(axis=0)
    return {
        "candidate_count": int(values.size), "threshold": VISIBILITY_LEVEL,
        "above_threshold": int(np.count_nonzero(values >= VISIBILITY_LEVEL)),
        "median_peak": float(np.median(values)) if values.size else None,
    }


def clipping_metrics(data: np.ndarray) -> JSONReport:
    """Mesure les fractions aux bornes par canal et les excursions, sans écrêtage."""
    return {
        "black_fraction_rgb": [float(np.mean(c <= 0)) for c in data],
        "white_fraction_rgb": [float(np.mean(c >= 1)) for c in data],
        "below_zero_fraction_rgb": [float(np.mean(c < 0)) for c in data],
        "above_one_fraction_rgb": [float(np.mean(c > 1)) for c in data],
        "minimum": float(data.min()),
        "maximum": float(data.max()),
    }


def color_drift(
    before: np.ndarray, after: np.ndarray, mask: np.ndarray | None = None
) -> dict[str, float]:
    """Mesure les changements de chromaticité RGB normalisée (pas de couleur calibrée)."""
    first_sum, last_sum = before.sum(axis=0), after.sum(axis=0)
    active = (first_sum > 1e-6) & (last_sum > 1e-6)
    if mask is not None:
        active &= mask
    if not active.any():
        return {"chromaticity_mean_abs": 0.0, "chroma_mean_change": 0.0}
    first = before[:, active] / first_sum[active]
    last = after[:, active] / last_sum[active]
    return {
        "chromaticity_mean_abs": float(np.mean(abs(first - last))),
        "chroma_mean_change": float(
            np.mean(np.ptp(last, axis=0) - np.ptp(first, axis=0))
        ),
    }


def profile_widths(
    before: np.ndarray, after: np.ndarray, relative_height: float = 0.5,
) -> JSONReport:
    """Mesure une FWHM axiale locale sur 64 pics isolés au plus, aux mêmes positions.

    Deux traversées à mi-hauteur, après soustraction de la médiane du bord,
    sont interpolées sur une fenêtre de 15 pixels. Les profils tronqués ou
    ambigus sont remesurés à 31 pixels si la référence initiale était mesurable,
    avec la même fenêtre élargie avant/après et assez de marge dans l'image.
    Un échec restant ne fournit pas de largeur. Le seuil dépend du bruit, avec un
    plancher de 1e-5 normalisé ; 64 pics sont répartis dans les amplitudes.
    relative_height, dans ]0,1[, fixe
    la fraction de hauteur au-dessus du fond (0,5 : FWHM ; 0,1 : ailes).
    Le nombre de références mesurables et les médianes par groupes de
    luminosité permettent de contrôler les étoiles brillantes séparément.
    Les entrées RGB sont normalisées, les largeurs en pixels. Ce contrôle ne
    remplace pas une PSF. Une fraction invalide lève ValueError.
    """
    if not np.isfinite(relative_height) or not 0 < relative_height < 1:
        raise ValueError('Hauteur relative hors de ]0,1[')
    first, last = before.max(axis=0), after.max(axis=0)
    radius = PROFILE_RADIUS
    floor, sigma = stellar_noise_estimate(first)
    peak_threshold = max(1e-5, floor + 12*sigma)
    peaks = (first == maximum_filter(first, size=2 * radius + 1)) & (first > peak_threshold)
    yy, xx = np.nonzero(peaks)
    ordered = np.argsort(first[yy, xx])
    if len(ordered) > 64:
        weak = ordered[:-16]
        selected = np.r_[weak[np.linspace(0, len(weak)-1, 48, dtype=int)], ordered[-16:]]
    else:
        selected = ordered
    widths: list[tuple[float, float]] = []
    reference_count = 0
    expanded_count = 0

    def axial_width(plane: np.ndarray, y: int, x: int, window_radius: int) -> float | None:
        """Mesure les deux axes dans une fenêtre commune avant/après, sans modifier les pixels."""
        axes = (plane[y, x-window_radius:x+window_radius+1],
                plane[y-window_radius:y+window_radius+1, x])
        radius = window_radius
        measures = []
        for profile in axes:
            floor = float(np.median(np.r_[profile[:2], profile[-2:]]))
            half = floor + relative_height * (float(profile[radius]) - floor)
            left = np.flatnonzero(profile[:radius] <= half)
            right = np.flatnonzero(profile[radius + 1 :] <= half)
            if not left.size or not right.size or profile[radius] <= floor:
                continue
            i, j = int(left[-1]), int(right[0] + radius + 1)
            lpos = i + (half - profile[i]) / max(
                float(profile[i + 1] - profile[i]), 1e-12
            )
            rpos = (
                j
                - 1
                + (profile[j - 1] - half)
                / max(float(profile[j - 1] - profile[j]), 1e-12)
            )
            measures.append(float(rpos - lpos))
        return float(np.mean(measures)) if measures else None

    for index in selected:
        y, x = int(yy[index]), int(xx[index])
        edge_distance = min(y, x, first.shape[0]-1-y, first.shape[1]-1-x)
        if edge_distance < radius:
            continue
        pair = [axial_width(plane, y, x, radius) for plane in (first, last)]
        if pair[0] is not None and pair[1] is None and edge_distance >= EXPANDED_PROFILE_RADIUS:
            expanded = [axial_width(plane, y, x, EXPANDED_PROFILE_RADIUS) for plane in (first, last)]
            if all(value is not None for value in expanded):
                pair = expanded
                expanded_count += 1
        if pair[0] is not None:
            reference_count += 1
        if all(value is not None for value in pair):
            widths.append((pair[0], pair[1]))
    groups = [dict(count=len(group), median_ratio=float(np.median(group)))
              for group in np.array_split(
                  np.array([b/a for a, b in widths]), min(4, len(widths)),
              )] if widths else []
    return {
        "method": "brightness-stratified axial profiles, 15px with paired 31px fallback; not fitted PSF",
        "maximum_group_median_ratio": max(group['median_ratio'] for group in groups) if groups else None,
        "brightness_groups": groups,
        "reference_count": reference_count,
        "expanded_window_count": expanded_count,
        "count": len(widths),
        "peak_threshold": peak_threshold,
        "relative_height": relative_height,
        "p10_ratio": float(np.quantile([b / a for a, b in widths], 0.1))
        if widths else None,
        "before_median_px": float(np.median([p[0] for p in widths]))
        if widths
        else None,
        "after_median_px": float(np.median([p[1] for p in widths])) if widths else None,
        "median_ratio": float(np.median([b / a for a, b in widths]))
        if widths
        else None,
    }


class ConservativeStarRenderer(processor):
    """Étire globalement l’histogramme des étoiles, sans traitement spatial ni bruit.

    Voir docs/scripts/postProcess/star-rendering.md pour les quantiles et les
    limites du critère de visibilité, distinct d'une détection astrophysique.
    """

    default_faint_boost = 5.0
    default_histogram_strength = 1.0
    default_max_width_ratio = 2.0
    default_stretch_max_width_ratio = None
    default_noise_sigma = NOISE_SIGMA_FACTOR
    default_boost_fraction = 0.25
    default_visible_fraction = StellarAppearanceQuality.VISIBLE_TARGET
    VISIBILITY_FRACTION_STEPS = 4
    VISIBILITY_BLEND_FACTORS = (.125, .25, .5, 1.)
    VISIBILITY_POWER_EXPONENTS = (.7, .6, .5, .475, .45, .425, .4)
    default_contrast_mode = 'pointwise'

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie le moteur de rendu des étoiles."""
        return "stars"

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Expose la courbe globale, les gardes et les paramètres anciens sans effet."""
        parser.add_argument(
            "--star-contrast-mode", choices=("profiles", "pointwise"),
            default=cls.default_contrast_mode,
            help="Courbe globale uniquement ; profiles est un alias de compatibilité sans effet local",
        )
        parser.add_argument(
            "--star-render", choices=("conservative",), default="conservative"
        )
        parser.add_argument("--star-strength", type=float, default=2.0)
        parser.add_argument(
            "--star-faint-boost", type=float, default=cls.default_faint_boost,
            help="Gain demandé des étoiles faibles, réduit automatiquement si la largeur dépasse la limite",
        )
        parser.add_argument(
            "--star-histogram-strength", type=float, default=cls.default_histogram_strength,
            help="Répartition logarithmique des pics stellaires, de 0 (gain fixe) à 1",
        )
        parser.add_argument("--star-ceiling", type=float, default=0.85)
        parser.add_argument(
            '--star-boost-fraction', type=float, default=cls.default_boost_fraction,
            help='Quantile de calibration de la courbe globale, de 0 à 1 (défaut 0,25) ; aucun masque',
        )
        parser.add_argument(
            '--star-visible-fraction', type=float, default=cls.default_visible_fraction,
            help='Objectif de candidats visibles guidant la courbe globale (défaut 0.30 ; 0 désactive la recherche)',
        )
        parser.add_argument(
            "--star-noise-sigma", type=float, default=NOISE_SIGMA_FACTOR,
            help="Ancien réglage accepté pour compatibilité, sans effet : aucun débruitage des étoiles",
        )
        parser.add_argument(
            "--star-max-width-ratio", type=float, default=cls.default_max_width_ratio,
        )
        parser.add_argument(
            "--star-stretch-max-width-ratio", type=float,
            default=cls.default_stretch_max_width_ratio,
            help="Limite FWHM des étoiles seules ; sinon hérite de --star-max-width-ratio",
        )
        parser.add_argument(
            "--star-max-color-drift",
            type=float,
            default=0.08,
            help="Dérive moyenne maximale de chromaticité RGB après fusion",
        )
        parser.add_argument(
            "--star-max-chroma-excess",
            type=float,
            default=0.10,
            help="Augmentation moyenne maximale de chroma RGB après fusion",
        )

    @staticmethod
    def render(
        data: np.ndarray, strength: float = 2.0, ceiling: float = 0.85,
        max_width_ratio: float = default_max_width_ratio,
        faint_boost: float = default_faint_boost,
        histogram_strength: float = default_histogram_strength,
        diagnostics: HistogramDiagnostics | None = None,
        noise_sigma: float = NOISE_SIGMA_FACTOR,
        boost_fraction: float = default_boost_fraction,
        color_mode: str = 'human',
        contrast_mode: str = default_contrast_mode,
        visible_fraction: float = default_visible_fraction,
    ) -> tuple[np.ndarray, JSONReport]:
        """Étire uniquement l'histogramme des étoiles avec une courbe RGB globale.

        Parameters
        ----------
        data : np.ndarray
            RGB fini dans [0,1], de forme (3, hauteur, largeur), non modifié.
        strength, ceiling, max_width_ratio, faint_boost : float
            Force asinh dans ]0,20], plafond dans ]0,1[, limite de largeur >=1
            et renforcement global des faibles niveaux dans [1,10].
        histogram_strength, boost_fraction : float
            Force et quantile de calibration de la courbe globale, dans [0,1].
            Aucun pixel n'est sélectionné selon ses coordonnées.
        diagnostics : HistogramDiagnostics or None
            Enregistre les essais et leur recette, sans filtrer les pixels.
        noise_sigma : float
            Ancien réglage dans [0,20], validé mais sans effet sur l'image.
            Le rendu ne réalise aucune suppression de bruit.
        color_mode : str
            Luminance human ou even pour la transformation RGB liée.
        contrast_mode : str
            pointwise ; profiles reste un alias de compatibilité, sans effet local.
        visible_fraction : float
            Objectif dans [0,1] favorisant les courbes admissibles qui révèlent
            cette fraction de candidats, sans imposer un rejet si inaccessible.
            Zéro désactive cette recherche supplémentaire. Les contrôles de
            largeur et des ailes priment toujours sur cet objectif.

        Returns
        -------
        tuple[np.ndarray, JSONReport]
            RGB float32 et audit de la courbe. Deux triplets RGB identiques ont
            toujours la même sortie, quel que soit leur emplacement ou voisinage.

        Raises
        ------
        ValueError
            Entrée ou paramètre invalide, ou aucune courbe globale admissible
            sous la garde de largeur. Les mesures de profils ajustent uniquement
            les paramètres globaux, jamais des gains ou des masques locaux.
        """
        if not np.isfinite(noise_sigma) or not 0 <= noise_sigma <= 20:
            raise ValueError('Seuil de bruit stellaire hors de [0,20]')
        if contrast_mode not in ('profiles', 'pointwise'):
            raise ValueError('Mode de contraste stellaire inconnu')
        if not np.isfinite(visible_fraction) or not 0 <= visible_fraction <= 1:
            raise ValueError('Objectif de visibilité stellaire hors de [0,1]')
        rendered, report = ConservativeStarRenderer._render_global(
            data, strength, ceiling, max_width_ratio, faint_boost,
            histogram_strength, diagnostics, 0., boost_fraction, color_mode,
            'pointwise',
        )
        positions = stellar_peak_positions(data)
        appearance = StellarAppearanceQuality.evaluate(
            data, rendered, positions, target_fraction=visible_fraction,
        )
        rendered, report, appearance = ConservativeStarRenderer._select_visibility(
            data, rendered, report, appearance, positions, visible_fraction,
            strength, ceiling, max_width_ratio, faint_boost, histogram_strength,
            boost_fraction, color_mode, diagnostics,
        )
        report.update(
            method='global histogram stretch: luminance-linked asinh + RGBBlend + intensity curve',
            contrast_mode='pointwise', spatially_varying=False, histogram_only=True,
        )
        report['appearance_quality'] = appearance
        report['noise_suppression'].update(
            method='disabled: histogram transformation only',
            requested_sigma_factor=noise_sigma,
        )
        return rendered, report

    @staticmethod
    def profile_guard(profiles: JSONReport, maximum: float) -> JSONReport:
        """Sépare déformations bloquantes et écarts progressifs des profils.

        La FWHM conserve son plancher 0,90. Ailes et jupe ont un plancher dur
        0,60 ; sous 0,90 leur écart pénalise le classement, après la visibilité.
        La limite maximale FWHM reste dure ; les ailes ont une tolérance dure
        de 10 % au-dessus de la limite, avec pénalité dès celle-ci. Au moins
        95 % des références doivent rester mesurables ; les pertes moindres
        sont pénalisées. Les groupes de luminosité restent contrôlés. Ces
        rapports sont des heuristiques de sélection de courbes globales.
        """
        reasons = []
        penalty = 0.
        for name, widths in profiles.items():
            minimum = MIN_PROFILE_RATIO if name == 'fwhm' else MIN_TAIL_PROFILE_RATIO
            upper = maximum if name == 'fwhm' else maximum*TAIL_WIDTH_TOLERANCE
            ratio = widths.get('maximum_group_median_ratio', widths.get('median_ratio'))
            p10 = widths.get('p10_ratio')
            groups = [group['median_ratio'] for group in widths.get('brightness_groups', [])]
            reference_count = widths.get('reference_count', 0)
            missing = max(0., 1-widths.get('count', 0)/reference_count) if reference_count else 1.
            if (ratio is None or p10 is None or not np.isfinite([ratio, p10, *groups]).all()
                    or ratio > upper or p10 < minimum
                    or missing > MAX_MISSING_PROFILE_FRACTION
                    or any(value < minimum or value > upper for value in groups)):
                reasons.append(name)
            if p10 is not None and np.isfinite(p10):
                penalty += max(0., MIN_PROFILE_RATIO-p10)/MIN_PROFILE_RATIO
            penalty += sum(max(0., MIN_PROFILE_RATIO-value)/MIN_PROFILE_RATIO
                           for value in groups if np.isfinite(value))
            penalty += missing/MAX_MISSING_PROFILE_FRACTION
            if ratio is not None and np.isfinite(ratio):
                penalty += max(0., ratio/maximum-1.)
        return dict(safe=not reasons, penalty=penalty, rejected_profiles=reasons)

    @staticmethod
    def _select_visibility(
        data: np.ndarray, rendered: np.ndarray, report: JSONReport, appearance: JSONReport,
        positions: tuple[np.ndarray, np.ndarray], target: float,
        strength: float, ceiling: float, max_width_ratio: float, faint_boost: float,
        histogram_strength: float, boost_fraction: float, color_mode: str,
        diagnostics: HistogramDiagnostics | None,
    ) -> tuple[np.ndarray, JSONReport, JSONReport]:
        """Compare les courbes quantiles puis des puissances douces pour la visibilité.

        Les positions initiales restent fixes. Le score sature à l'objectif,
        puis préfère les profils/ailes les moins élargis et le plus petit
        changement du quantile demandé. Les candidats supplémentaires doivent
        respecter les gardes dures de largeur à 50 %, 10 % et 5 % du pic, y compris
        les groupes d'amplitude. Les écarts modérés d'ailes sous 0,90 sont
        pénalisés après l'objectif de visibilité. Une cible inaccessible conserve le meilleur essai
        sûr, sans traitement local et sans inventer de mesure sur les bords.
        Si la cible reste inaccessible, sept puissances globales entre 0,4 et
        0,7 sont testées, avec un raccord monotone aux étoiles brillantes et
        les mêmes gardes. Leur table est conservée pour un rejeu exact.
        """
        def rank(audit: JSONReport, measured: JSONReport, fraction: float) -> tuple[float, float, float]:
            """Sature le gain utile à la cible et départage par la largeur des profils."""
            lower = measured['visibility']['fraction_lower_bound']
            penalty = sum(
                float(np.mean([abs(group['median_ratio']-1.)
                               for group in audit.get(key, {}).get('brightness_groups', [])]))
                for key in ('fwhm', 'wings', 'skirt')
                if audit.get(key, {}).get('brightness_groups')
            )
            return (min(target, lower) if lower is not None else -1.,
                    -penalty-ConservativeStarRenderer.profile_guard(
                        {key: audit[key] for key in ('fwhm', 'wings', 'skirt')}, max_width_ratio,
                    )['penalty'], -abs(fraction-boost_fraction))

        report['skirt'] = profile_widths(data, rendered, .05)
        best_key = rank(report, appearance, boost_fraction)
        attempts = [dict(fraction=boost_fraction, status='initial',
                         visible_fraction=appearance['visibility']['fraction_lower_bound'],
                         profile_guard=ConservativeStarRenderer.profile_guard(
                             {key: report[key] for key in ('fwhm', 'wings', 'skirt')}, max_width_ratio,
                         ))]
        enabled = target > 0 and histogram_strength > 0 and boost_fraction > 0
        reason = 'disabled' if not enabled else 'already_satisfied'
        if enabled and appearance['visibility']['status'] != 'PASS':
            visibility = appearance['visibility']
            if visibility['candidate_count'] == 0 or visibility['unknown_count'] == visibility['candidate_count']:
                reason = 'unmeasurable_visibility'
            else:
                reason = 'best_safe_visibility'
                first = max(boost_fraction, target)
                fractions = np.unique(np.linspace(first, 1., ConservativeStarRenderer.VISIBILITY_FRACTION_STEPS))
                settings = [(float(f), histogram_strength) for f in fractions
                            if f < 1 and abs(f-boost_fraction) >= 1e-9]
                settings += [(1., histogram_strength*b)
                             for b in ConservativeStarRenderer.VISIBILITY_BLEND_FACTORS]
                for fraction, blend in settings:
                    if fraction == boost_fraction and blend == histogram_strength:
                        continue
                    try:
                        candidate, audit = ConservativeStarRenderer._render_global(
                            data, strength, ceiling, max_width_ratio, faint_boost,
                            blend, None, 0., fraction, color_mode, 'pointwise',
                        )
                    except ValueError as error:
                        if not str(error).startswith('Gonflement des profils stellaires'):
                            raise
                        attempts.append(dict(fraction=fraction, histogram_strength=blend,
                                             status='rejected', reason='width_limit'))
                        continue
                    audit['skirt'] = profile_widths(data, candidate, .05)
                    guard = ConservativeStarRenderer.profile_guard(
                        {key: audit[key] for key in ('fwhm', 'wings', 'skirt')}, max_width_ratio,
                    )
                    safe = guard['safe']
                    measured = StellarAppearanceQuality.evaluate(
                        data, candidate, positions, target_fraction=target,
                    )
                    attempts.append(dict(fraction=fraction, histogram_strength=blend,
                                         status='admissible' if safe else 'rejected',
                                         visible_fraction=measured['visibility']['fraction_lower_bound'],
                                         profile_groups={k: audit[k].get('brightness_groups', [])
                                                         for k in ('fwhm', 'wings', 'skirt')},
                                         reason=None if safe else 'profile_guard'))
                    attempts[-1]['profile_guard'] = guard
                    if diagnostics is not None:
                        diagnostics.record_stellar_trial(
                            candidate, f'Objectif {100*target:g} % — quantile {fraction:g}, mélange {blend:g}',
                            'admissible' if safe else 'rejected',
                            transform_operations=[stellar_operation(audit, audit['histogram_balance'])],
                        )
                    key = rank(audit, measured, fraction)
                    if safe and key > best_key:
                        rendered, report, appearance, best_key = candidate, audit, measured, key
                if appearance['visibility']['status'] != 'PASS':
                    baseline, base_audit = ConservativeStarRenderer._render_global(
                        data, strength, ceiling, max_width_ratio, faint_boost,
                        0., None, 0., 0., color_mode, 'pointwise',
                    )
                    light, base_light = data.max(axis=0), baseline.max(axis=0)
                    knots = np.geomspace(NOISE_PRECISION_FLOOR, 1., 33)
                    base_levels = ceiling*np.arcsinh(base_audit['strength']*knots)/np.arcsinh(
                        base_audit['strength'],
                    )
                    gain = base_audit['faint_boost']
                    base_levels = gain*base_levels/(1+(gain-1)*base_levels/ceiling)
                    bright = FRACTION_BRIGHT_TARGET*ceiling/REFERENCE_STAR_CEILING
                    for exponent in ConservativeStarRenderer.VISIBILITY_POWER_EXPONENTS:
                        levels = ceiling*(base_levels/ceiling)**exponent
                        mapped = protected_stellar_target(light, base_light, knots, levels, bright)
                        mapped = base_light + histogram_strength*(mapped-base_light)
                        ratio = np.divide(mapped, base_light, out=np.zeros_like(mapped), where=base_light > 0)
                        candidate = (baseline*ratio[None]).astype(np.float32)
                        audit = deepcopy(base_audit)
                        audit['boost_selection'] = dict(
                            mode='histogram_quantile', fraction=boost_fraction,
                            curve_family='smooth_power', exponent=exponent,
                            bright_protection_policy=MONOTONE_BRIGHT_PROTECTION,
                            bright_protection=bright,
                        )
                        audit['histogram_balance'].update(
                            requested_strength=histogram_strength, accepted_strength=histogram_strength,
                            input_knots=knots.tolist(),
                            target_knots=levels.tolist(), stop_reason='smooth power candidate',
                        )
                        for name, height in (('fwhm', .5), ('wings', .1), ('skirt', .05)):
                            audit[name] = profile_widths(data, candidate, height)
                        guard = ConservativeStarRenderer.profile_guard(
                            {name: audit[name] for name in ('fwhm', 'wings', 'skirt')}, max_width_ratio,
                        )
                        measured = StellarAppearanceQuality.evaluate(
                            data, candidate, positions, target_fraction=target,
                        )
                        audit.update(after=clipping_metrics(candidate), color=color_drift(data, candidate),
                                     visibility=peak_visibility(candidate, positions))
                        attempts.append(dict(
                            curve_family='smooth_power', exponent=exponent,
                            status='admissible' if guard['safe'] else 'rejected', profile_guard=guard,
                            visible_fraction=measured['visibility']['fraction_lower_bound'],
                            reason=None if guard['safe'] else 'profile_guard',
                        ))
                        if diagnostics is not None:
                            diagnostics.record_stellar_trial(
                                candidate, f'Courbe globale puissance {exponent:g}',
                                'admissible' if guard['safe'] else 'rejected',
                                transform_operations=[stellar_operation(audit, audit['histogram_balance'])],
                            )
                        key = rank(audit, measured, boost_fraction)
                        if guard['safe'] and key > best_key:
                            rendered, report, appearance, best_key = candidate, audit, measured, key
                if diagnostics is not None:
                    diagnostics.record_stellar_trial(
                        rendered, 'Étirement global retenu — visibilité', 'selected',
                        transform_operations=[stellar_operation(report, report['histogram_balance'])],
                    )
        report['visibility_selection'] = dict(
            target_fraction=target, enabled=enabled, attempts=attempts,
            stop_reason='target_reached' if enabled and appearance['visibility']['status'] == 'PASS' else reason,
            selected_fraction=report.get('boost_selection', {}).get('fraction', boost_fraction),
            requested_calibration_fraction=boost_fraction,
            profile_guard=dict(minimum_ratio=MIN_PROFILE_RATIO, maximum_ratio=max_width_ratio,
                               minimum_tail_ratio=MIN_TAIL_PROFILE_RATIO,
                               maximum_tail_ratio=max_width_ratio*TAIL_WIDTH_TOLERANCE,
                               minimum_measured_fraction=1-MAX_MISSING_PROFILE_FRACTION,
                               soft_tail_minimum=MIN_PROFILE_RATIO,
                               selection_priority='visibility_then_profile_penalty',
                               relative_heights=[.5, .1, .05], brightness_stratified=True),
            target_reached=appearance['visibility']['status'] == 'PASS',
            spatially_varying=False,
        )
        initial = attempts[0]['visible_fraction']
        final = appearance['visibility']['fraction_lower_bound']
        LOGGER.info(
            'Étoiles — objectif visibilité %.1f %% ; courbe initiale=%s ; '
            'courbe retenue=%s ; décision=%s ; %d courbe(s) évaluée(s)',
            100*target, 'inconnue' if initial is None else f'{100*initial:.1f} %',
            'inconnue' if final is None else f'{100*final:.1f} %',
            report['visibility_selection']['stop_reason'], len(attempts),
        )
        return rendered, report, appearance

    @staticmethod
    def _render_global(
        data: np.ndarray, strength: float = 2.0, ceiling: float = 0.85,
        max_width_ratio: float = default_max_width_ratio,
        faint_boost: float = default_faint_boost,
        histogram_strength: float = default_histogram_strength,
        diagnostics: HistogramDiagnostics | None = None,
        noise_sigma: float = NOISE_SIGMA_FACTOR,
        boost_fraction: float = default_boost_fraction,
        color_mode: str = 'human',
        contrast_mode: str = default_contrast_mode,
    ) -> tuple[np.ndarray, JSONReport]:
        """Calibre une courbe RGB globale, indépendante des positions.

        Les paramètres, domaines et retours suivent render. Les profils ne
        servent qu'à sélectionner des paramètres communs à toute l'image.
        Cette fonction est appelée avec noise_sigma=0 par le rendu public.
        """
        if not np.isfinite(boost_fraction) or not 0 <= boost_fraction <= 1:
            raise ValueError('Fraction stellaire hors de [0,1]')
        if not np.isfinite(histogram_strength) or not 0 <= histogram_strength <= 1:
            raise ValueError('Paramètres stellaires hors limites')
        if not np.isfinite(max_width_ratio) or max_width_ratio < 1:
            raise ValueError('Paramètres stellaires : limite de largeur invalide')
        if contrast_mode not in ('profiles', 'pointwise'):
            raise ValueError('Mode de contraste stellaire inconnu')
        if boost_fraction == 1:
            rendered, report = ConservativeStarRenderer._render_unrestricted(
                data, strength, ceiling, max_width_ratio, faint_boost,
                histogram_strength, diagnostics, noise_sigma, color_mode,
            )
            count = report['histogram_balance']['calibration_peak_count']
            report['boost_selection'] = dict(
                mode='all', fraction=1., selected_count=count, budget=count,
                eligible_count=count, shape=list(data.shape[1:]),
            )
            return rendered, report
        baseline, report = ConservativeStarRenderer._render_unrestricted(
            data, strength, ceiling, max_width_ratio, faint_boost, 0., None,
            noise_sigma, color_mode,
        )
        light = data.max(axis=0)
        positions = stellar_peak_positions(data)
        amplitudes = light[positions]
        # L'historique de recherche de la base conserve ses propres instantanés.
        balance = deepcopy(report['histogram_balance'])
        report['histogram_balance'] = balance
        eligible = amplitudes > balance['calibration_peak_threshold']
        peaks = amplitudes[eligible]
        budget = int(np.floor(boost_fraction * peaks.size))
        selection: JSONReport = dict(
            mode='histogram_quantile', fraction=float(boost_fraction),
            bright_protection_policy=MONOTONE_BRIGHT_PROTECTION,
            eligible_count=int(peaks.size), budget=budget,
            selected_count=budget, shape=list(light.shape),
        )
        balance.update(requested_strength=histogram_strength, accepted_strength=0.)
        report['boost_selection'] = selection
        report['fraction_adaptation'] = []
        if diagnostics is not None:
            diagnostics.record_stellar_trial(
                data, 'Entrée linéaire — étoiles', 'input', transform_operations=[],
            )
            diagnostics.record_stellar_trial(
                baseline, f"Courbe fixe — renforcement {report['faint_boost']:g}",
                'candidate', transform_operations=[stellar_operation(report, balance)],
            )
        rendered = baseline
        balance['stop_reason'] = 'insufficient distinct peaks'
        if histogram_strength and budget and peaks.size >= 8:
            cutoff = float(np.quantile(peaks, 1 - boost_fraction))
            transition = max(float(balance['calibration_peak_threshold']), cutoff / 2)
            bright_input = max(cutoff * (1 + 1e-6), float(np.quantile(peaks, .95)))
            bright_input = min(bright_input, 1.)
            fixed = ceiling * np.arcsinh(strength * transition) / np.arcsinh(strength)
            fixed = faint_boost * fixed / (1 + (faint_boost - 1) * fixed / ceiling)
            visible = FRACTION_VISIBLE_TARGET * ceiling / REFERENCE_STAR_CEILING
            bright = FRACTION_BRIGHT_TARGET * ceiling / REFERENCE_STAR_CEILING
            knots = np.asarray([transition, cutoff, bright_input, 1.], dtype=np.float64)
            levels = np.asarray([fixed, visible, bright, ceiling], dtype=np.float64)
            if np.all(np.diff(knots) > 0) and np.all(np.diff(levels) > 0):
                balance['input_knots'], balance['target_knots'] = (
                    knots.tolist(), levels.tolist()
                )
                selection.update(
                    cutoff=cutoff, transition_start=transition,
                    visible_target=visible, bright_protection=bright,
                )
                base_light = baseline.max(axis=0)
                mapped = protected_stellar_target(light, base_light, knots, levels, bright)
                balance['stop_reason'] = 'width limit'
                for index in range(MAX_BALANCE_ATTEMPTS + 1):
                    blend = 0. if index == MAX_BALANCE_ATTEMPTS else histogram_strength / 2 ** index
                    target = base_light + blend * (mapped - base_light)
                    gain = np.divide(
                        target, base_light, out=np.zeros_like(target), where=base_light > 0,
                    )
                    candidate = baseline * gain[None]
                    widths = profile_widths(data, candidate)
                    wings = profile_widths(data, candidate, .1)
                    skirt = profile_widths(data, candidate, .05)
                    ratio = widths['median_ratio']
                    guard = ConservativeStarRenderer.profile_guard(
                        dict(fwhm=widths, wings=wings, skirt=skirt), max_width_ratio,
                    )
                    accepted = guard['safe']
                    trial = dict(strength=blend, median_width_ratio=ratio, accepted=accepted,
                                 profile_guard=guard)
                    balance['iterations'].append(trial)
                    report['fraction_adaptation'].append(dict(trial, wings_p10=wings['p10_ratio']))
                    if diagnostics is not None:
                        trial_balance = dict(balance, accepted_strength=blend)
                        diagnostics.record_stellar_trial(
                            candidate, f'Courbe quantile {blend:g} — fraction {boost_fraction:g}',
                            'admissible' if accepted else 'rejected',
                            transform_operations=[stellar_operation(report, trial_balance)],
                        )
                    if accepted:
                        rendered = candidate.astype(np.float32)
                        report['fwhm'], report['wings'] = widths, wings
                        balance['accepted_strength'] = blend
                        balance['stop_reason'] = (
                            'requested strength' if index == 0 else 'width limited'
                        )
                        break
        validate_rgb(rendered)
        report['method'] = (
            'luminance-linked asinh + RGBBlend + quantile histogram curve '
            '(global)'
        )
        report['after'] = clipping_metrics(rendered)
        report['color'] = color_drift(data, rendered)
        report['visibility'] = peak_visibility(rendered, positions)
        selection['actually_boosted_count'] = int(np.count_nonzero(
            rendered[:, positions[0], positions[1]].max(axis=0)
            > baseline[:, positions[0], positions[1]].max(axis=0) + 1e-7
        ))
        report['faint_gain_role'] = 'quantile_histogram_curve'
        if diagnostics is not None:
            diagnostics.record_stellar_trial(
                rendered, 'Étirement global retenu', 'selected',
                transform_operations=[stellar_operation(report, balance)],
            )
        return rendered, report

    @staticmethod
    def _render_unrestricted(
        data: np.ndarray,
        strength: float = 2.0,
        ceiling: float = 0.85,
        max_width_ratio: float = default_max_width_ratio,
        faint_boost: float = default_faint_boost,
        histogram_strength: float = default_histogram_strength,
        diagnostics: HistogramDiagnostics | None = None,
        noise_sigma: float = NOISE_SIGMA_FACTOR,
        color_mode: str = 'human',
    ) -> tuple[np.ndarray, JSONReport]:
        """Retient le premier rendu stellaire respectant la limite de largeur.

        Parameters
        ----------
        data : np.ndarray
            Étoiles RGB normalisées dans [0, 1], de forme (3, hauteur, largeur).
            Les résidus négatifs doivent avoir été compensés par l'appelant.
        strength : float, optional
            Force asinh dans ]0, 20], conservée pendant la recherche.
        ceiling : float, optional
            Plafond normalisé dans ]0, 1[, conservé pendant la recherche.
        max_width_ratio : float, optional
            Limite du rapport médian des largeurs après/avant, au moins 1.
        faint_boost : float, optional
            Renforcement demandé dans [1, 10]. Après échec de l'adaptation des
            pics, il diminue par pas de 1, avec un dernier essai à 1 inclus.
        histogram_strength : float, optional
            Mélange adaptatif demandé dans [0, 1], réduit par moitiés à chaque
            essai de renforcement de base avant de tester sa courbe fixe.
            Actif par défaut (1) ; zéro retrouve la courbe fixe.
        diagnostics : HistogramDiagnostics or None, optional
            Collecteur des essais sur les étoiles seules, sans effet sur leur
            sélection. Il appartient à l'appelant, qui publie puis ferme son cache.
        noise_sigma : float, optional
            Ancien réglage validé dans [0,20], sans effet sur les pixels.
            Aucun débruitage n’est appliqué dans ce moteur.

        Returns
        -------
        tuple[np.ndarray, JSONReport]
            Étoiles float32 et mesures, dont les renforcements demandé/retenu
            et l'historique ``width_adaptation``. Une largeur non mesurable
            reste admissible mais n'est pas une validation de la garde.

        Raises
        ------
        ValueError
            Entrées invalides ou aucun renforcement essayé admissible, même à 1.
            La recherche est bornée à dix essais et ne modifie pas la source.
        """
        requested_boost = faint_boost
        if not np.isfinite(noise_sigma) or not 0 <= noise_sigma <= 20:
            raise ValueError('Seuil de bruit stellaire hors de [0,20]')
        if diagnostics is not None:
            validate_rgb(data)
            diagnostics.record_stellar_trial(data, 'Entrée linéaire — étoiles', 'input',
                                             transform_operations=[])
        attempts: list[JSONReport] = []
        while True:
            rendered, report = ConservativeStarRenderer._render_candidate(
                data, strength, ceiling, max_width_ratio, faint_boost,
                histogram_strength,
                diagnostics,
                noise_sigma,
                color_mode,
            )
            ratio = report["fwhm"]["median_ratio"]
            checked_ratio = report["fwhm"].get("maximum_group_median_ratio", ratio)
            accepted = checked_ratio is None or checked_ratio <= max_width_ratio
            if diagnostics is not None:
                diagnostics.record_stellar_trial(
                    rendered, f'Renforcement {faint_boost:g}',
                    'selected' if accepted else 'rejected',
                    dict(strength=strength, ceiling=ceiling, faint_boost=faint_boost,
                         histogram_strength=report['histogram_balance']['accepted_strength']),
                    transform_operations=[stellar_operation(report, report['histogram_balance'])],
                )
            attempts.append({
                "faint_boost": faint_boost,
                "median_width_ratio": ratio,
                "checked_width_ratio": checked_ratio,
                "accepted": accepted,
                "histogram_balance": report["histogram_balance"],
            })
            if accepted:
                report["requested_faint_boost"] = requested_boost
                report["width_adaptation"] = {
                    "step": FAINT_BOOST_STEP,
                    "iterations": attempts,
                    "stop_reason": (
                        "unmeasurable width" if ratio is None
                        else "requested boost" if len(attempts) == 1
                        else "width limited"
                    ),
                }
                if len(attempts) > 1:
                    width_text = "non mesurable" if ratio is None else f"{ratio:.6f}"
                    LOGGER.info(
                        "Étoiles : renforcement réduit de %g à %g ; "
                        "rapport médian de largeur %s ; limite %g",
                        requested_boost, faint_boost, width_text, max_width_ratio,
                    )
                return rendered, report
            LOGGER.info(
                "Étoiles : renforcement %g refusé ; rapport maximal des médianes "
                "FWHM par groupe %.6f > limite %g (médiane globale %s)",
                faint_boost, checked_ratio, max_width_ratio,
                'non mesurable' if ratio is None else f'{ratio:.6f}',
            )
            if faint_boost <= 1:
                history = "; ".join(
                    f"gain {item['faint_boost']:g} : ratio {item['checked_width_ratio']:.6f}"
                    for item in attempts
                )
                raise ValueError(
                    "Gonflement des profils stellaires supérieur à la limite "
                    f"{max_width_ratio:g} après {len(attempts)} essai(s) : {history}"
                )
            # Ne pas conserver les grandes images des essais refusés en mémoire.
            del rendered
            faint_boost = max(1.0, faint_boost - FAINT_BOOST_STEP)

    @staticmethod
    def _render_candidate(
        data: np.ndarray,
        strength: float,
        ceiling: float,
        max_width_ratio: float,
        faint_boost: float,
        histogram_strength: float,
        diagnostics: HistogramDiagnostics | None = None,
        noise_sigma: float = NOISE_SIGMA_FACTOR,
        color_mode: str = 'human',
    ) -> tuple[np.ndarray, JSONReport]:
        """Retourne un rendu monotone borné et ses mesures sur les étoiles seules.

        Une courbe rationnelle multiplie les faibles intensités par faint_boost.
        Une table de quantiles des pics augmente ensuite les faibles sources ;
        son mélange est réduit par moitiés si la FWHM médiane dépasse la garde.
        histogram_strength=0 désactive cette adaptation ; avec faint_boost=1
        cela reproduit l'ancien asinh. Aucun débruitage ni gain spatial n'est
        appliqué ; noise_sigma reste un paramètre de compatibilité sans effet.
        Les zéros sont conservés.
        Les rapports RGB le sont sous le plafond lié ; RGBBlend peut les
        modifier près du gamut protégé.
        Les résidus négatifs doivent être compensés explicitement par l'appelant.
        Les paramètres et unités sont ceux de ``render``. Les valeurs non finies
        ou hors limites lèvent ValueError. Un dépassement de largeur est retourné
        dans le rapport pour que ``render`` essaie un renforcement inférieur.
        Aucun traitement spatial n'est appliqué.
        diagnostics enregistre les courbes fixes et essais adaptatifs sans
        modifier leurs paramètres ni leurs critères.
        """
        validate_rgb(data)
        if not np.isfinite([
            strength, ceiling, max_width_ratio, faint_boost, histogram_strength, noise_sigma
        ]).all():
            raise ValueError("Paramètres stellaires non finis")
        if (
            not 0 < strength <= 20 or not 0 < ceiling < 1
            or max_width_ratio < 1 or not 1 <= faint_boost <= 10
            or not 0 <= histogram_strength <= 1
            or not 0 <= noise_sigma <= 20
        ):
            raise ValueError("Paramètres stellaires hors limites")
        if color_mode not in ('human', 'even'):
            raise ValueError('Mode couleur stellaire inconnu')
        light = data.max(axis=0)
        # Le maximum RGB calibre les pics ; aucune estimation ne filtre les pixels.
        base = asinh_rgb(data, strength, ceiling, faint_boost, color_mode)
        target = base.max(axis=0)
        positions = stellar_peak_positions(data)
        peaks = light[positions]
        floor, sigma = stellar_noise_estimate(light)
        # Un résidu écrêté à zéro peut rendre la MAD des niveaux presque nulle.
        # Écarter ses pics de bruit du calage adaptatif, avant les quantiles.
        balance_threshold = max(1e-5, floor + 12 * sigma)
        peaks = peaks[peaks > balance_threshold]
        fixed_origin_gain = float(ceiling * strength * faint_boost / np.arcsinh(strength))
        origin_gain = fixed_origin_gain
        balance: JSONReport = {
            "requested_strength": histogram_strength, "accepted_strength": 0.0,
            "iterations": [], "stop_reason": "disabled",
            "before": peak_visibility(data, positions),
            "calibration_peak_count": int(peaks.size),
            "calibration_peak_threshold": balance_threshold,
        }
        balance["fixed_gain"] = peak_visibility(base, positions)
        if diagnostics is not None:
            diagnostics.record_stellar_trial(
                base, f'Courbe fixe — renforcement {faint_boost:g}',
                'candidate', dict(
                    strength=strength, ceiling=ceiling,
                    faint_boost=faint_boost, color_mode=color_mode,
                ),
                transform_operations=[stellar_operation(dict(
                    strength=strength, ceiling=ceiling, faint_boost=faint_boost,
                    color_mode=color_mode,
                ))],
            )
        if histogram_strength > 0:
            balance["stop_reason"] = "insufficient distinct peaks"
            # Des quantiles de pics, pas les millions de pixels du fond nul.
            quantiles = np.linspace(*PEAK_QUANTILE_RANGE, PEAK_QUANTILE_COUNT)
            anchors = np.quantile(peaks, quantiles) if peaks.size >= 8 else np.array([])
            if anchors.size and np.unique(anchors).size >= 4:
                anchors, unique = np.unique(anchors, return_index=True)
                levels = np.geomspace(*PEAK_OUTPUT_RANGE, PEAK_QUANTILE_COUNT)[unique]
                levels *= ceiling / REFERENCE_STAR_CEILING
                # Les bornes conservent 0 et le plafond ; interpolation log-log.
                inside = anchors < 1
                knots = np.r_[anchors[inside], 1.0]
                levels = np.r_[levels[inside], ceiling]
                balanced = np.exp(np.interp(
                    np.log(np.maximum(light, np.finfo(np.float32).tiny)),
                    np.log(knots), np.log(levels),
                ))
                below = light < knots[0]
                balanced[below] = light[below] * levels[0] / knots[0]
                balanced = np.maximum(target, balanced).astype(np.float32)
                balance["input_knots"] = knots.tolist()
                balance["target_knots"] = levels.tolist()
                balance["stop_reason"] = "width limit"
                for iteration in range(MAX_BALANCE_ATTEMPTS):
                    fraction = histogram_strength / 2 ** iteration
                    candidate_target = target + fraction * (balanced - target)
                    gain = np.divide(
                        candidate_target, target, out=np.zeros_like(target),
                        where=target > 0,
                    )
                    candidate = base * gain[None]
                    widths = profile_widths(data, candidate)
                    ratio = widths["median_ratio"]
                    accepted = ratio is None or ratio <= max_width_ratio
                    if diagnostics is not None:
                        diagnostics.record_stellar_trial(
                            candidate,
                            f'Adaptation {fraction:g} — renforcement {faint_boost:g}',
                            'admissible' if accepted else 'rejected',
                            dict(faint_boost=faint_boost, histogram_strength=fraction),
                            transform_operations=[stellar_operation(dict(
                                strength=strength, ceiling=ceiling, faint_boost=faint_boost,
                                histogram_strength=fraction, color_mode=color_mode,
                            ), balance)],
                        )
                    balance["iterations"].append({
                        "strength": fraction, "median_width_ratio": ratio,
                        "accepted": accepted,
                    })
                    if accepted:
                        target = candidate_target
                        base = candidate
                        balance["accepted_strength"] = fraction
                        origin_gain += fraction * (
                            max(fixed_origin_gain, float(levels[0] / knots[0]))
                            - fixed_origin_gain
                        )
                        balance["stop_reason"] = (
                            "requested strength" if iteration == 0 else "width limited"
                        )
                        break
        # Mesurer les profils ne modifie aucun pixel : il n'y a aucune porte
        # de bruit, même dans les chemins internes du rendu global.
        rendered = base
        threshold = 0.0
        requested_threshold = 0.0
        noise_attempts: list[JSONReport] = []
        half_widths = profile_widths(data, rendered)
        wing_widths = profile_widths(data, rendered, relative_height=0.1)
        validate_rgb(rendered)
        widths = half_widths
        return rendered.astype(np.float32), {
            "method": (
                "global luminance-linked asinh + RGBBlend + faint boost + log peak quantiles"
            ),
            "strength": strength,
            "color_mode": color_mode,
            "faint_boost": faint_boost,
            "faint_intensity_gain": 0.0 if threshold else origin_gain,
            "noise_suppression": {
                "method": (
                    "disabled: histogram transformation only"
                ),
                "floor": floor, "sigma": sigma,
                "sigma_factor": noise_sigma,
                "requested_threshold": requested_threshold, "threshold": threshold,
                "minimum_profile_ratio": MIN_PROFILE_RATIO,
                "iterations": noise_attempts,
            },
            "wings": wing_widths,
            "fixed_curve_faint_intensity_gain": fixed_origin_gain,
            "ceiling": ceiling,
            "max_width_ratio": max_width_ratio,
            "before": clipping_metrics(data),
            "after": clipping_metrics(rendered),
            "color": color_drift(data, rendered),
            "fwhm": widths,
            "histogram_balance": balance,
            "visibility": peak_visibility(rendered, positions),
        }

    @classmethod
    def stretch_width_limit(cls, args: argparse.Namespace) -> float:
        """Résout la garde stellaire explicite, sinon la garde commune existante.

        Parameters
        ----------
        args : argparse.Namespace
            Paramètres résolus par Config. Une valeur None signifie héritage.

        Returns
        -------
        float
            Rapport FWHM maximal des étoiles seules. La validation numérique
            est effectuée par render. La garde de recomposition n'est pas modifiée.
        """
        value = getattr(args, 'star_stretch_max_width_ratio', None)
        if value is None:
            return getattr(args, 'star_max_width_ratio', cls.default_max_width_ratio)
        return value

    def post_process(
        self,
        input_path: Path,
        output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Écrit les étoiles FITS/JPEG, le diagnostic PNG et le rapport JSON.

        La source est préservée. Les pixels de l'aperçu sont affichés directement,
        dans le sens de l'affichage natif Siril ; aucun gamma n'est ajouté.
        """
        from astropy.io import fits

        from lib.render_exports import write_render_jpeg
        from lib.star_diagnostics import write_star_diagnostic

        args = self._get_args(args)
        source, report = Path(input_path).resolve(), Path(output_path).resolve()
        destination = report.with_name(f"{report.stem}_stretched.fits")
        if source in (report, destination):
            raise ValueError("Le rendu stellaire ne peut pas remplacer la source")
        before = normalized_planes(source)
        data, metrics = self.render(
            before,
            getattr(args, "star_strength", 2.0),
            getattr(args, "star_ceiling", 0.85),
            self.stretch_width_limit(args),
            getattr(args, "star_faint_boost", self.default_faint_boost),
            getattr(args, "star_histogram_strength", self.default_histogram_strength),
            noise_sigma=getattr(args, "star_noise_sigma", NOISE_SIGMA_FACTOR),
            boost_fraction=getattr(args, "star_boost_fraction", self.default_boost_fraction),
            color_mode=getattr(args, "ghs_color_mode", "human"),
            contrast_mode=getattr(args, "star_contrast_mode", self.default_contrast_mode),
            visible_fraction=getattr(args, "star_visible_fraction", self.default_visible_fraction),
        )
        report.parent.mkdir(parents=True, exist_ok=True)
        write_rgb(source, destination, data, nonlinear=True, role="STARS")
        jpeg = destination.with_suffix(".jpg")
        diagnostic = report.with_name(f"{report.stem}_stars_diagnostic.png")
        write_render_jpeg(destination, jpeg)
        write_star_diagnostic(
            before, data, metrics, diagnostic,
            row_order=fits.getheader(source).get("ROWORDER", "BOTTOM-UP"),
        )
        result = {
            "output_image": str(destination), "output_jpeg": str(jpeg),
            "diagnostic_path": str(diagnostic), **metrics,
        }
        report.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result


def recompose(
    starless: np.ndarray, stars: np.ndarray, headroom: float = 0.95
) -> tuple[np.ndarray, JSONReport]:
    """Fusionne par screen : 1-(1-headroom*fond)*(1-étoiles), sans clip.

    Le fond est atténué par une constante commune aux canaux. Cette loi bornée
    correspond à l'opération PixelMath screen, pas à une inversion du StarNet.
    Les diagnostics comparent aussi l'addition hypothétique et les profils sur
    la contribution effectivement réinsérée. Toute incohérence est fatale.
    """
    validate_rgb(starless)
    validate_rgb(stars)
    if stars.shape != starless.shape:
        raise ValueError("Les branches doivent avoir la même géométrie RGB")
    if not np.isfinite(headroom) or not 0 < headroom < 1:
        raise ValueError("La marge de recomposition doit être dans ]0, 1[")
    background = headroom * starless
    result = background + stars * (1 - background)
    validate_rgb(result)
    contribution = result - background
    active = stars.max(axis=0) > 0.001
    return result.astype(np.float32), {
        "method": "screen",
        "background_headroom": headroom,
        "addition_without_control": clipping_metrics(background + stars),
        "after": clipping_metrics(result),
        "star_color_after_fusion": color_drift(stars, contribution, active),
        "star_profiles_after_fusion": profile_widths(stars, contribution),
        "star_contribution_sum": float(contribution.sum(dtype=np.float64)),
        "maximum_star_contribution": float(contribution.max()),
    }
