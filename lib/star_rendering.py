"""Rendu stellaire adaptatif et recomposition screen contrôlée.

L'asinh puis les quantiles des pics renforcent les étoiles faibles.
Le gain commun aux trois canaux préserve les rapports de couleur sans présumer les primaires du FITS.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
from scipy.ndimage import label, maximum_filter

from lib.processor import processor
from lib.restoration_quality import normalized_planes
from lib.star_separation import validate_rgb, write_rgb
from lib.type_defs import JSONReport

VISIBILITY_LEVEL = 0.03  # Contribution normalisée, indicateur d'affichage seulement.
PEAK_QUANTILE_COUNT = 17
PEAK_QUANTILE_RANGE = (0.05, 0.95)  # Exclure les extrêmes du calage des pics.
PEAK_OUTPUT_RANGE = (0.04, 0.65)  # Niveaux visés pour un plafond de référence 0,85.
REFERENCE_STAR_CEILING = 0.85
MAX_BALANCE_ATTEMPTS = 8  # Réductions par moitiés avant de conserver le gain fixe.
FAINT_BOOST_STEP = 1.0  # Décrément du renforcement de base, jusqu'au minimum 1.
LOGGER = logging.getLogger(__name__)


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


def profile_widths(before: np.ndarray, after: np.ndarray) -> JSONReport:
    """Mesure une FWHM axiale locale sur 64 pics isolés au plus, aux mêmes positions.

    Deux traversées à mi-hauteur, après soustraction de la médiane du bord,
    sont interpolées sur une fenêtre de 15 pixels. Les profils tronqués ou
    ambigus ne fournissent pas de largeur. Ce contrôle ne remplace pas une PSF.
    """
    first, last = before.max(axis=0), after.max(axis=0)
    radius = 7
    peaks = (first == maximum_filter(first, size=2 * radius + 1)) & (first > 0.005)
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
                half = (float(profile[radius]) + floor) / 2
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
        "method": "local axial half-height, 15px; not fitted PSF",
        "count": len(widths),
        "before_median_px": float(np.median([p[0] for p in widths]))
        if widths
        else None,
        "after_median_px": float(np.median([p[1] for p in widths])) if widths else None,
        "median_ratio": float(np.median([b / a for a, b in widths]))
        if widths
        else None,
    }


class ConservativeStarRenderer(processor):
    """Étire les étoiles avec répartition des pics et contrôle de largeur.

    Voir docs/scripts/postProcess/star-rendering.md pour les quantiles et les
    limites du critère de visibilité, distinct d'une détection astrophysique.
    """

    default_faint_boost = 5.0
    default_histogram_strength = 0.8
    default_max_width_ratio = 2.0

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie le moteur de rendu des étoiles."""
        return "stars"

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Expose le renforcement des faibles étoiles et les gardes persistantes."""
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
            "--star-max-width-ratio", type=float, default=cls.default_max_width_ratio,
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
        data: np.ndarray,
        strength: float = 2.0,
        ceiling: float = 0.85,
        max_width_ratio: float = default_max_width_ratio,
        faint_boost: float = default_faint_boost,
        histogram_strength: float = default_histogram_strength,
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
        attempts: list[JSONReport] = []
        while True:
            rendered, report = ConservativeStarRenderer._render_candidate(
                data, strength, ceiling, max_width_ratio, faint_boost,
                histogram_strength,
            )
            ratio = report["fwhm"]["median_ratio"]
            accepted = ratio is None or ratio <= max_width_ratio
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
    ) -> tuple[np.ndarray, JSONReport]:
        """Retourne un rendu monotone borné et ses mesures sur les étoiles seules.

        Une courbe rationnelle multiplie les faibles intensités par faint_boost.
        Une table de quantiles des pics augmente ensuite les faibles sources ;
        son mélange est réduit par moitiés si la FWHM médiane dépasse la garde.
        histogram_strength=0 désactive cette adaptation ; avec faint_boost=1
        cela reproduit l'ancien asinh. Les rapports RGB et zéros sont conservés.
        Les résidus négatifs doivent être compensés explicitement par l'appelant.
        Les paramètres et unités sont ceux de ``render``. Les valeurs non finies
        ou hors limites lèvent ValueError. Un dépassement de largeur est retourné
        dans le rapport pour que ``render`` essaie un renforcement inférieur.
        Aucun traitement spatial n'est appliqué.
        """
        validate_rgb(data)
        if not np.isfinite([
            strength, ceiling, max_width_ratio, faint_boost, histogram_strength
        ]).all():
            raise ValueError("Paramètres stellaires non finis")
        if (
            not 0 < strength <= 20 or not 0 < ceiling < 1
            or max_width_ratio < 1 or not 1 <= faint_boost <= 10
            or not 0 <= histogram_strength <= 1
        ):
            raise ValueError("Paramètres stellaires hors limites")
        light = data.max(axis=0)
        target = ceiling * np.arcsinh(strength * light) / np.arcsinh(strength)
        target = faint_boost * target / (1 + (faint_boost - 1) * target / ceiling)
        positions = stellar_peak_positions(data)
        peaks = light[positions]
        fixed_origin_gain = float(ceiling * strength * faint_boost / np.arcsinh(strength))
        origin_gain = fixed_origin_gain
        balance: JSONReport = {
            "requested_strength": histogram_strength, "accepted_strength": 0.0,
            "iterations": [], "stop_reason": "disabled",
            "before": peak_visibility(data, positions),
        }
        base_gain = np.divide(target, light, out=np.zeros_like(light), where=light > 0)
        balance["fixed_gain"] = peak_visibility(data * base_gain[None], positions)
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
                    candidate = target + fraction * (balanced - target)
                    gain = np.divide(
                        candidate, light, out=np.zeros_like(light), where=light > 0
                    )
                    widths = profile_widths(data, data * gain[None])
                    ratio = widths["median_ratio"]
                    accepted = ratio is None or ratio <= max_width_ratio
                    balance["iterations"].append({
                        "strength": fraction, "median_width_ratio": ratio,
                        "accepted": accepted,
                    })
                    if accepted:
                        target = candidate
                        balance["accepted_strength"] = fraction
                        origin_gain += fraction * (
                            max(fixed_origin_gain, float(levels[0] / knots[0]))
                            - fixed_origin_gain
                        )
                        balance["stop_reason"] = (
                            "requested strength" if iteration == 0 else "width limited"
                        )
                        break
        gain = np.divide(target, light, out=np.zeros_like(light), where=light > 0)
        rendered = data * gain[None]
        validate_rgb(rendered)
        widths = profile_widths(data, rendered)
        return rendered.astype(np.float32), {
            "method": "asinh + faint boost + log peak quantiles, shared RGB gain",
            "strength": strength,
            "faint_boost": faint_boost,
            "faint_intensity_gain": origin_gain,
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

    def post_process(
        self,
        input_path: Path,
        output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Écrit les étoiles FITS/JPEG, le diagnostic PNG et le rapport JSON.

        La source est préservée. Les pixels de l'aperçu sont affichés directement,
        en respectant ROWORDER ; aucune correction gamma n'est ajoutée.
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
            getattr(args, "star_max_width_ratio", self.default_max_width_ratio),
            getattr(args, "star_faint_boost", self.default_faint_boost),
            getattr(args, "star_histogram_strength", self.default_histogram_strength),
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
