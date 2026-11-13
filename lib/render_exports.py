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


def siril_display_planes(data: np.ndarray) -> np.ndarray:
    """Expose les plans FITS dans le sens haut/bas de l'affichage natif Siril.

    Parameters
    ----------
    data : numpy.ndarray
        Plans RGB natifs du FITS, de forme (3, hauteur, largeur), dans [0,1].

    Returns
    -------
    numpy.ndarray
        Vue des plans avec les lignes inversées, sans copie ni modification
        des intensités. Siril affiche la première ligne FITS en bas même
        lorsque ROWORDER indique TOP-DOWN ; cette carte ne retourne donc
        pas une seconde fois la présentation RGB. Voir render-exports.md.
    """
    validate_rgb(data)
    return data[:, ::-1, :]


def render_array_image(
    data: np.ndarray, row_order: str = 'BOTTOM-UP',
) -> Image.Image:
    """Convertit un tableau RGB [0,1] en 8 bits, sans étirement ni gamma.

    Parameters
    ----------
    data : numpy.ndarray
        Trois plans finis, dans l'ordre RGB, de forme (3, hauteur, largeur).
    row_order : str
        Carte FITS BOTTOM-UP ou TOP-DOWN, validée pour compatibilité. Les deux
        valeurs utilisent le même affichage natif de Siril, bas vers haut.

    Returns
    -------
    PIL.Image.Image
        Copie RGB quantifiée au plus proche, à fermer par l'appelant.
    """
    if row_order not in ('TOP-DOWN', 'BOTTOM-UP'):
        raise ValueError(f'ROWORDER inconnu : {row_order}')
    pixels = np.rint(np.moveaxis(siril_display_planes(data), 0, -1)*255).astype(np.uint8)
    return Image.fromarray(pixels)


def write_array_jpeg(
    data: np.ndarray, destination: Path, row_order: str = 'BOTTOM-UP',
) -> None:
    """Écrit un instantané RGB entier en JPEG 95, sans FITS intermédiaire.

    Les unités, l'arrondi et l'orientation sont ceux de render_array_image.
    Aucune image n'est retenue après l'écriture ; les erreurs remontent.
    """
    with render_array_image(data, row_order) as rendered:
        rendered.save(destination, format='JPEG', quality=JPEG_QUALITY,
                      subsampling=0, optimize=True)


def read_render_image(source: Path) -> Image.Image:
    """Convertit un FITS RGB non linéaire en image RGB 8 bits orientée pour l'écran.

    SPNLIN doit être vrai et les pixels finis dans [0,1]. ROWORDER est validé,
    avec BOTTOM-UP par défaut. L'orientation suit l'affichage natif Siril,
    première ligne FITS en bas, y compris avec TOP-DOWN. Arrondi au plus proche ;
    aucune normalisation par les extrema ni conversion colorimétrique implicite
    n'est appliquée. Les erreurs de format ou de lecture sont propagées.
    """
    with fits.open(source, memmap=True) as hdul:
        if not hdul[0].header.get("SPNLIN", False):
            raise ValueError("L'export attend un rendu FITS non linéaire SPNLIN=True")
        data = hdul[0].data
        if data is None:
            raise ValueError("Le FITS ne contient pas de rendu RGB")
        row_order = hdul[0].header.get("ROWORDER", "BOTTOM-UP")
        return render_array_image(data, row_order)


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
