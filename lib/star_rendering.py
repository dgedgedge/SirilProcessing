"""Rendu stellaire conservateur et recomposition screen contrôlée.

L'asinh puis une compression rationnelle renforcent les étoiles faibles.
Le gain commun aux trois canaux préserve les rapports de couleur sans présumer les primaires du FITS.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import maximum_filter

from lib.processor import processor
from lib.restoration_quality import normalized_planes
from lib.star_separation import validate_rgb, write_rgb
from lib.type_defs import JSONReport


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
    """Étire les étoiles par asinh et renforcement doux, sans optimiser le fond."""

    default_faint_boost = 5.0

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
            help="Gain supplémentaire des étoiles faibles, avec compression douce des fortes",
        )
        parser.add_argument("--star-ceiling", type=float, default=0.85)
        parser.add_argument("--star-max-width-ratio", type=float, default=1.5)
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
        max_width_ratio: float = 1.5,
        faint_boost: float = default_faint_boost,
    ) -> tuple[np.ndarray, JSONReport]:
        """Retourne un rendu monotone borné et ses mesures sur les étoiles seules.

        Une courbe rationnelle multiplie les faibles intensités par faint_boost
        et conserve le plafond des fortes ; faint_boost=1 reproduit l'ancien rendu.
        Les résidus négatifs doivent être compensés explicitement par l'appelant.
        Les paramètres non finis/hors limites et un gonflement excessif mesurable
        lèvent ValueError. Aucun traitement spatial n'est appliqué.
        """
        validate_rgb(data)
        if not np.isfinite([strength, ceiling, max_width_ratio, faint_boost]).all():
            raise ValueError("Paramètres stellaires non finis")
        if (
            not 0 < strength <= 20 or not 0 < ceiling < 1
            or max_width_ratio < 1 or not 1 <= faint_boost <= 10
        ):
            raise ValueError("Paramètres stellaires hors limites")
        light = data.max(axis=0)
        target = ceiling * np.arcsinh(strength * light) / np.arcsinh(strength)
        target = faint_boost * target / (1 + (faint_boost - 1) * target / ceiling)
        gain = np.divide(target, light, out=np.zeros_like(light), where=light > 0)
        rendered = data * gain[None]
        validate_rgb(rendered)
        widths = profile_widths(data, rendered)
        if (
            widths["median_ratio"] is not None
            and widths["median_ratio"] > max_width_ratio
        ):
            raise ValueError("Gonflement des profils stellaires supérieur à la limite")
        return rendered.astype(np.float32), {
            "method": "asinh + soft faint boost of max RGB, shared RGB gain",
            "strength": strength,
            "faint_boost": faint_boost,
            "faint_intensity_gain": float(
                ceiling * strength * faint_boost / np.arcsinh(strength)
            ),
            "ceiling": ceiling,
            "max_width_ratio": max_width_ratio,
            "before": clipping_metrics(data),
            "after": clipping_metrics(rendered),
            "color": color_drift(data, rendered),
            "fwhm": widths,
        }

    def post_process(
        self,
        input_path: Path,
        output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Écrit la couche étirée et son rapport ; refuse de remplacer la source."""
        args = self._get_args(args)
        source, report = Path(input_path).resolve(), Path(output_path).resolve()
        destination = report.with_name(f"{report.stem}_stretched.fits")
        if source in (report, destination):
            raise ValueError("Le rendu stellaire ne peut pas remplacer la source")
        data, metrics = self.render(
            normalized_planes(source),
            getattr(args, "star_strength", 2.0),
            getattr(args, "star_ceiling", 0.85),
            getattr(args, "star_max_width_ratio", 1.5),
            getattr(args, "star_faint_boost", self.default_faint_boost),
        )
        report.parent.mkdir(parents=True, exist_ok=True)
        write_rgb(source, destination, data, nonlinear=True, role="STARS")
        result = {"output_image": str(destination), **metrics}
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
