"""Exports JPEG et comparatifs des rendus AutoGHS déjà non linéaires.

Les intensités sont affichées directement, sans gamma ni auto-étirement ajouté.
Les FITS scientifiques restent intacts ; les JPEG RGB sont quantifiés sur 8 bits.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
from astropy.io import fits
from PIL import Image

from lib.star_separation import validate_rgb
from lib.type_defs import JSONReport

LOGGER = logging.getLogger(__name__)
JPEG_QUALITY = 95
COMPARISON_MAX_SIDE = 1024
PreviewGroups = dict[str, dict[str, Image.Image]]


def read_render_image(source: Path) -> Image.Image:
    """Convertit un FITS RGB non linéaire en image RGB 8 bits orientée pour l'écran.

    SPNLIN doit être vrai et les pixels finis dans [0,1]. ROWORDER est respecté,
    avec BOTTOM-UP par défaut, comme Siril. Les arrondis se font au plus proche ;
    aucune normalisation par les extrema ni conversion colorimétrique implicite
    n'est appliquée. Les erreurs de format ou de lecture sont propagées.
    """
    with fits.open(source, memmap=True) as hdul:
        if not hdul[0].header.get("SPNLIN", False):
            raise ValueError("L'export attend un rendu FITS non linéaire SPNLIN=True")
        data = hdul[0].data
        if data is None:
            raise ValueError("Le FITS ne contient pas de rendu RGB")
        validate_rgb(data)
        row_order = hdul[0].header.get("ROWORDER", "BOTTOM-UP")
        if row_order not in ("TOP-DOWN", "BOTTOM-UP"):
            raise ValueError(f"ROWORDER inconnu : {row_order}")
        if row_order == "BOTTOM-UP":
            data = data[:, ::-1, :]
        pixels = np.rint(np.moveaxis(data, 0, -1) * 255).astype(np.uint8)
    return Image.fromarray(pixels)


def write_render_jpeg(source: Path, destination: Path) -> None:
    """Exporte le rendu entier en JPEG qualité 95, sans sous-échantillonnage couleur.

    La taille et l'orientation écran du FITS sont conservées. La source ne peut
    pas être remplacée ; les erreurs d'écriture restent fatales pour l'appelant.
    """
    if source.resolve() == destination.resolve():
        raise ValueError("Le JPEG ne peut pas remplacer le FITS source")
    with read_render_image(source) as rendered:
        rendered.save(destination, format="JPEG", quality=JPEG_QUALITY,
                      subsampling=0, optimize=True)


def load_previous_previews(report_path: Path) -> PreviewGroups:
    """Capture les anciens rendus avant la réinitialisation du dossier AutoGHS.

    Les aperçus restent en mémoire, au plus 1024 pixels de côté. Un ancien
    rapport absent, incomplet ou illisible n'empêche pas le nouveau traitement :
    les précédents indisponibles ne seront pas présentés comme des images réelles.
    """
    previews: PreviewGroups = {"starless": {}, "with_stars": {}}
    if not report_path.is_file():
        return previews
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(report, dict) or report.get("status") not in (
            "SUCCESS", "NO_IMPROVEMENT"
        ):
            return previews
        strategies = report.get("strategies", {})
        if not isinstance(strategies, dict):
            return previews
    except (OSError, ValueError) as error:
        LOGGER.warning("Comparatif précédent indisponible : %s", error)
        return previews
    for strategy in ("range", "peak"):
        branch = strategies.get(strategy)
        if not isinstance(branch, dict):
            continue
        for kind, key in (("starless", "starless_image"),
                          ("with_stars", "output_image")):
            path = branch.get(key)
            if not isinstance(path, str):
                continue
            source = Path(path)
            if not source.is_absolute():
                source = report_path.parent / source
            try:
                preview = read_render_image(source)
                preview.thumbnail((COMPARISON_MAX_SIDE, COMPARISON_MAX_SIDE),
                                  Image.Resampling.LANCZOS)
                previews[kind][strategy] = preview
            except (OSError, ValueError) as error:
                LOGGER.warning("Rendu précédent %s indisponible : %s", source, error)
    return previews


def write_render_comparisons(
    reports: dict[str, JSONReport], directory: Path, stem: str,
    previous: PreviewGroups,
) -> dict[str, str]:
    """Écrit deux comparatifs PNG séparés, sans étoiles et avec étoiles.

    Chaque colonne est une stratégie. Une ligne précédente est ajoutée lorsque
    des aperçus de cette catégorie ont été capturés ; les absences sont indiquées.
    Les vues utilisent la même conversion RGB directe, sans auto-étirement.
    Retourne les chemins absolus des deux PNG, pour le rapport de traitement.
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    if not reports:
        raise ValueError("Aucun rendu à comparer")
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for kind, key, label in (
        ("starless", "starless_image", "Sans étoiles"),
        ("with_stars", "output_image", "Avec étoiles recomposées"),
    ):
        old = previous.get(kind, {})
        has_previous = any(strategy in old for strategy in reports)
        rows = 2 if has_previous else 1
        figure = Figure(figsize=(6 * len(reports), 5.5 * rows),
                        constrained_layout=True)
        FigureCanvasAgg(figure)
        axes = figure.subplots(rows, len(reports), squeeze=False)
        figure.suptitle(f"{label} — comparaison des rendus")
        for column, (strategy, branch) in enumerate(reports.items()):
            if has_previous:
                axis = axes[0, column]
                if strategy in old:
                    axis.imshow(old[strategy], origin="upper")
                else:
                    axis.text(0.5, 0.5, "Aucun rendu précédent disponible",
                              ha="center", va="center", transform=axis.transAxes)
                axis.set_title(f"{strategy.upper()} — exécution précédente")
            with read_render_image(Path(branch[key])) as current:
                current.thumbnail((COMPARISON_MAX_SIDE, COMPARISON_MAX_SIDE),
                                  Image.Resampling.LANCZOS)
                axes[-1, column].imshow(current, origin="upper")
            axes[-1, column].set_title(f"{strategy.upper()} — exécution actuelle")
        for axis in axes.flat:
            axis.set_axis_off()
        figure.supxlabel("Même affichage RGB, sans auto-étirement ; "
                         f"aperçus limités à {COMPARISON_MAX_SIDE} px de côté.",
                         fontsize=9)
        destination = directory / f"{stem}_ghs_comparison_{kind}.png"
        figure.savefig(destination, dpi=130)
        figure.clear()
        paths[kind] = str(destination.resolve())
    return paths
