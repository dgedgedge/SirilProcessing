"""Diagnostic de la couche stellaire seule, sans auto-étirement d'affichage."""

from pathlib import Path

import numpy as np

from lib.star_rendering import VISIBILITY_LEVEL, stellar_peak_positions
from lib.star_separation import validate_rgb
from lib.type_defs import JSONReport


def write_star_diagnostic(
    before: np.ndarray, after: np.ndarray, report: JSONReport,
    destination: Path, *, row_order: str = "BOTTOM-UP",
) -> None:
    """Écrit deux vues centrales sans étirement et deux histogrammes avec axes log.

    Les vues RGB utilisent directement [0,1], sans gamma ni normalisation, sur
    le même carré central de 512 pixels au plus. Les histogrammes portent sur
    toute l'image : maximum RGB positif puis pics candidats aux mêmes positions.
    Les zéros sont exclus des axes log et leur fraction est affichée. Le seuil
    de 0,03 est un indicateur de contraste, pas un décompte d'étoiles physiques.
    Une orientation inconnue ou une géométrie incompatible lève ValueError.
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    validate_rgb(before)
    validate_rgb(after)
    if before.shape != after.shape or row_order not in ("BOTTOM-UP", "TOP-DOWN"):
        raise ValueError("Géométrie ou orientation du diagnostic stellaire invalide")
    figure = Figure(figsize=(14, 11))
    FigureCanvasAgg(figure)
    axes = figure.subplots(2, 2)
    positions = stellar_peak_positions(before)
    height, width = before.shape[1:]
    y, x = max(0, (height - 512) // 2), max(0, (width - 512) // 2)
    lower = min(
        float(np.min(data, where=data > 0, initial=1))
        for data in (before.max(axis=0), after.max(axis=0))
    )
    bins = np.geomspace(min(lower, 1e-7), 1, 100)
    for column, (data, label, color) in enumerate((
        (before, "Avant — étoiles linéaires positives", "tab:blue"),
        (after, "Après — étoiles étirées", "tab:orange"),
    )):
        displayed = data[:, ::-1] if row_order == "BOTTOM-UP" else data
        crop = np.moveaxis(displayed[:, y:y + 512, x:x + 512], 0, -1)
        axes[0, column].imshow(crop, vmin=0, vmax=1)
        axes[0, column].set_title(label + "\nMême extrait central, affichage direct [0,1]")
        axes[0, column].axis("off")
        light = data.max(axis=0)
        for axis, values in (
            (axes[1, 0], light[light > 0]),
            (axes[1, 1], light[positions]),
        ):
            axis.hist(values, bins=bins, histtype="step", label=label, color=color)
    for axis, title, unit in (
        (axes[1, 0], "Couche d’étoiles entière — pixels positifs", "Pixels par classe"),
        (axes[1, 1], "Pics candidats — mêmes positions avant/après", "Pics par classe"),
    ):
        axis.set_ylim(0.8, max(2, axis.get_ylim()[1] * 1.1))
        axis.set(xscale="log", yscale="log", title=title,
                 xlabel="Intensité max RGB (log)", ylabel=unit + " (log)")
        axis.axvline(VISIBILITY_LEVEL, color="gray", linestyle=":", label="Seuil indicatif 0,03")
        axis.legend(fontsize=8)
        axis.grid(alpha=0.2)
    balance = report["histogram_balance"]
    initial, fixed, final = balance["before"], balance["fixed_gain"], report["visibility"]
    width_ratio = report["fwhm"]["median_ratio"]
    width_text = "non mesurable" if width_ratio is None else f"{width_ratio:.3f}"
    reasons = {
        "disabled": "adaptation désactivée",
        "insufficient distinct peaks": "pics distincts insuffisants",
        "width limit": "limite de largeur ; gain fixe conservé",
        "width limited": "force réduite par la limite de largeur",
        "requested strength": "force demandée admissible",
    }
    annotation = (
        "OBJECTIF : répartir les amplitudes des pics sur une plage logarithmique plus large, "
        "pour relever les faibles contributions sans créer de source.\n"
        f"Pics candidats : {initial['candidate_count']} ; au-dessus de 0,03 : "
        f"linéaire {initial['above_threshold']}, gain fixe {fixed['above_threshold']}, "
        f"rendu adapté {final['above_threshold']}. "
        "Ce sont des indicateurs, pas des étoiles certifiées.\n"
        f"Force demandée / retenue : {balance['requested_strength']:.3f} / "
        f"{balance['accepted_strength']:.3f} ; essais : {len(balance['iterations'])} ; "
        f"arrêt : {reasons[balance['stop_reason']]}. Rapport FWHM médian : {width_text}.\n"
        f"Pixels max RGB nuls exclus du log : {100 * np.mean(before.max(axis=0) == 0):.2f} % ; "
        "la visibilité finale dépend aussi du fond après recomposition."
    )
    recomposed = report.get("recomposed_visibility", {})
    if recomposed:
        annotation += "\nContraste ajouté au fond ≥ 0,03 après fusion : " + "; ".join(
            f"{name.upper()} {metrics['above_threshold']} pics"
            for name, metrics in recomposed.items()
        ) + "."
    figure.suptitle("Diagnostic spécifique — image des étoiles seules", fontsize=16)
    figure.text(0.03, 0.025, annotation, fontsize=9, va="bottom",
                bbox={"facecolor": "#f0f0f0", "edgecolor": "gray", "pad": 8})
    figure.subplots_adjust(top=0.9, bottom=0.23, hspace=0.35, wspace=0.22)
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, dpi=120)
    figure.clear()
