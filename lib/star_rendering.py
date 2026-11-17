"""Rendu stellaire adaptatif et recomposition screen contrôlée.

L'asinh puis les quantiles des pics renforcent les étoiles faibles.
Une porte douce liée au bruit résiduel est limitée par les profils à 50 et 10 %.
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
    asinh_rgb, stellar_noise_gate, stellar_operation,
)
from lib.restoration_quality import normalized_planes
from lib.star_separation import validate_rgb, write_rgb
from lib.type_defs import JSONReport

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
NOISE_SIGMA_FACTOR = 8.0  # Écarts robustes du résidu positif, pas du bruit signé initial.
NOISE_PRECISION_FLOOR = 1e-7  # Intensité normalisée ; éviter d'interpréter les queues numériques.
MIN_PROFILE_RATIO = 0.90  # Rétrécissement maximal admis à 50 % et à 10 % du pic.
MAX_NOISE_ATTEMPTS = 8


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
        la porte. Le bruit corrélé, écrêté à zéro ou un champ très dense peuvent
        biaiser cette estimation ; ce n'est pas un SNR photométrique.
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
    ambigus ne fournissent pas de largeur. Les pics doivent dépasser 0,005
    en intensité normalisée.
    relative_height, dans ]0,1[, fixe
    la fraction de hauteur au-dessus du fond (0,5 : FWHM ; 0,1 : ailes).
    Les entrées RGB sont normalisées, les largeurs en pixels. Ce contrôle ne
    remplace pas une PSF. Une fraction invalide lève ValueError.
    """
    if not np.isfinite(relative_height) or not 0 < relative_height < 1:
        raise ValueError('Hauteur relative hors de ]0,1[')
    first, last = before.max(axis=0), after.max(axis=0)
    radius = 7
    peak_threshold = 0.005
    peaks = (first == maximum_filter(first, size=2 * radius + 1)) & (first > peak_threshold)
    yy, xx = np.nonzero(peaks)
    selected = np.argsort(first[yy, xx])[::-1][:64]
    widths: list[tuple[float, float]] = []
    for index in selected:
        y, x = int(yy[index]), int(xx[index])
        if min(y, x, first.shape[0] - 1 - y, first.shape[1] - 1 - x) < radius:
            continue
        pair = []
        for plane in (first, last):
            axes = (
                plane[y, x - radius : x + radius + 1],
                plane[y - radius : y + radius + 1, x],
            )
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
            pair.append(float(np.mean(measures)) if measures else None)
        if all(value is not None for value in pair):
            widths.append((pair[0], pair[1]))
    return {
        "method": "local axial relative-height, 15px; not fitted PSF",
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
    """Étire les étoiles avec atténuation du bruit et contrôle des profils.

    Voir docs/scripts/postProcess/star-rendering.md pour les quantiles et les
    limites du critère de visibilité, distinct d'une détection astrophysique.
    """

    default_faint_boost = 5.0
    default_histogram_strength = 1.0
    default_max_width_ratio = 2.0
    default_stretch_max_width_ratio = None
    default_noise_sigma = NOISE_SIGMA_FACTOR
    default_boost_fraction = 0.25

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie le moteur de rendu des étoiles."""
        return "stars"

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Expose renforcement, seuil de bruit et gardes de largeur persistants."""
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
            help='Fraction maximale de pics renforcés en plus : 0 à 1 (défaut 0,25)',
        )
        parser.add_argument(
            "--star-noise-sigma", type=float, default=NOISE_SIGMA_FACTOR,
            help="Seuil doux en écarts robustes du résidu positif ; 0 désactive (défaut 8)",
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
    ) -> tuple[np.ndarray, JSONReport]:
        """Calibre une courbe ponctuelle sur une fraction des pics éligibles.

        Parameters
        ----------
        data : np.ndarray
            RGB positif normalisé (3, hauteur, largeur), source préservée.
        strength, ceiling, max_width_ratio, faint_boost : float
            Paramètres de la base décrits par _render_unrestricted. Sa garde
            de largeur est résolue avant l'adaptation sélective.
        histogram_strength : float
            Mélange adaptatif demandé dans [0,1], réduit par moitiés jusqu'à
            huit essais, puis essai nul. Les profils à 50 et 10 % sont contrôlés.
        diagnostics : HistogramDiagnostics or None
            Collecteur des essais et recettes indépendantes, fermé par l'appelant.
        noise_sigma : float
            Seuil doux de bruit dans [0,20], celui retenu pour la base est fixé
            pendant les essais sélectifs.
        boost_fraction : float
            Fraction de pics éligibles dans [0,1], 0,25 par défaut. Zéro
            conserve la base et un retrouve le moteur historique global. Entre
            les deux, son quantile fixe le point d'inflexion d'une courbe unique.
        color_mode : str
            ``human`` utilise la luminosité pondérée du GHS ; ``even`` utilise
            la moyenne RGB. La balance SPCC/PCC est déjà intégrée aux pixels.

        Returns
        -------
        tuple[np.ndarray, JSONReport]
            RGB float32 et mesures de la courbe ponctuelle, de la FWHM et des
            ailes. Aucun masque ni raccord spatial n'est appliqué.

        Raises
        ------
        ValueError
            Entrée ou paramètres invalides, ou base non admissible. Une largeur
            non mesurable ne certifie aucune garde. Sans pics suffisants ou sans
            candidat admissible, conserver la base explicitement signalée.

        Notes
        -----
        Voir docs/scripts/postProcess/star-rendering.md. Deux pixels ayant la
        même intensité reçoivent la même cible, indépendamment de leur position.
        """
        if not np.isfinite(boost_fraction) or not 0 <= boost_fraction <= 1:
            raise ValueError('Fraction stellaire hors de [0,1]')
        if not np.isfinite(histogram_strength) or not 0 <= histogram_strength <= 1:
            raise ValueError('Paramètres stellaires hors limites')
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
            fixed *= float(stellar_noise_gate(
                np.asarray(transition), report['noise_suppression']['threshold'],
            ))
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
                mapped = np.exp(np.interp(
                    np.log(np.maximum(light, np.finfo(np.float32).tiny)),
                    np.log(knots), np.log(levels),
                ))
                mapped[light <= transition] = base_light[light <= transition]
                mapped[base_light >= bright] = base_light[base_light >= bright]
                mapped = np.maximum(base_light, mapped).astype(np.float32)
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
                    ratio = widths['median_ratio']
                    accepted = (
                        ratio is not None and ratio <= max_width_ratio
                        and widths['p10_ratio'] >= MIN_PROFILE_RATIO
                        and wings['p10_ratio'] is not None
                        and wings['p10_ratio'] >= MIN_PROFILE_RATIO
                    )
                    trial = dict(strength=blend, median_width_ratio=ratio, accepted=accepted)
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
            '+ soft noise gate'
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
                rendered, 'Renforcement sélectif retenu', 'selected',
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
            Seuil doux = médiane du résidu + noise_sigma fois sa dispersion
            robuste, dans [0,20]. Zéro désactive la suppression. La porte est
            réduite si les largeurs à 50 % ou 10 % du pic rétrécissent trop.

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
            accepted = ratio is None or ratio <= max_width_ratio
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
                "Étoiles : renforcement %g refusé ; rapport médian de largeur "
                "%.6f > limite %g", faint_boost, ratio, max_width_ratio,
            )
            if faint_boost <= 1:
                history = "; ".join(
                    f"gain {item['faint_boost']:g} : ratio {item['median_width_ratio']:.6f}"
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
        et noise_sigma=0 cela reproduit l'ancien asinh. Une porte quartique
        atténue les résidus proches du bruit ; son seuil est réduit si les
        profils à 50 % ou 10 % se rétrécissent trop. Les zéros sont conservés.
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
        # La porte de bruit reste calée sur le maximum RGB : elle doit supprimer
        # un résidu présent dans un seul canal aussi efficacement qu'un résidu
        # neutre. Le mode couleur ne pilote que la courbe asinh/RGBBlend.
        noise_light = light
        base = asinh_rgb(data, strength, ceiling, faint_boost, color_mode)
        target = base.max(axis=0)
        positions = stellar_peak_positions(data)
        peaks = light[positions]
        floor, sigma = stellar_noise_estimate(noise_light)
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
        requested_threshold = (
            min(1.0, floor + noise_sigma * sigma) if sigma and noise_sigma else 0.0
        )
        threshold = requested_threshold
        noise_attempts: list[JSONReport] = []
        for attempt in range(MAX_NOISE_ATTEMPTS + 1):
            # Le dernier essai désactive la porte si les ailes ne sont pas préservées.
            if attempt == MAX_NOISE_ATTEMPTS:
                threshold = 0.0
            candidate = base * stellar_noise_gate(noise_light, threshold)[None]
            half_widths = profile_widths(data, candidate)
            wing_widths = profile_widths(data, candidate, relative_height=0.1)
            measured = [item['p10_ratio'] for item in (half_widths, wing_widths)]
            accepted = all(
                value is not None and value >= MIN_PROFILE_RATIO for value in measured
            )
            # Sans profil exploitable, ne pas certifier une suppression du bruit.
            accepted = accepted or threshold == 0
            noise_attempts.append(dict(threshold=threshold, accepted=accepted,
                                       fwhm_p10=measured[0], wings_p10=measured[1]))
            if accepted:
                rendered = candidate
                break
            threshold /= 2
        validate_rgb(rendered)
        widths = half_widths
        return rendered.astype(np.float32), {
            "method": (
                "luminance-linked asinh + RGBBlend + faint boost + log peak "
                "quantiles + soft noise gate"
            ),
            "strength": strength,
            "color_mode": color_mode,
            "faint_boost": faint_boost,
            "faint_intensity_gain": 0.0 if threshold else origin_gain,
            "noise_suppression": {
                "method": (
                    "max-RGB neighbor-difference MAD + quartic soft gate"
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
