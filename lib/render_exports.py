"""Exports JPEG des rendus AutoGHS déjà non linéaires.

Les intensités sont affichées directement, sans gamma ni auto-étirement ajouté.
Les FITS scientifiques restent intacts ; les JPEG RGB sont quantifiés sur 8 bits.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from astropy.io import fits
from PIL import Image

from lib.star_separation import validate_rgb

JPEG_QUALITY = 95


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
