"""Modes couleur de Cosmic Clarity AI3, adaptés du code MIT de Franklin Marek.

Conversions YCbCr BT.601 et filtrage guidé de la révision amont
423acc442724872ff6d81011962a8cbf5744a7f7. Les tableaux sont en float32,
avec canaux en première dimension et valeurs normalisées dans [0, 1].
Voir docs/scripts/postProcess/cosmic-clarity.md pour les limites colorimétriques.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import uniform_filter


FloatImage = NDArray[np.float32]
RGB_TO_YCBCR = np.array([
    [0.299, 0.587, 0.114],
    [-0.168736, -0.331264, 0.5],
    [0.5, -0.418688, -0.081312],
], dtype=np.float32)
YCBCR_TO_RGB = np.array([
    [1.0, 0.0, 1.402],
    [1.0, -0.344136, -0.714136],
    [1.0, 1.772, 0.0],
], dtype=np.float32)
CHROMA_STRENGTH_SCALE = 2.0  # Facteur du curseur de chrominance amont.
CHROMA_RADIUS_MIN_PX = 2
CHROMA_RADIUS_SPAN_PX = 10
CHROMA_EPSILON_BASE = 0.001
CHROMA_EPSILON_SPAN = 0.05


def extract_luminance(rgb: FloatImage) -> tuple[FloatImage, FloatImage, FloatImage]:
    """Sépare un RGB 3×H×W en Y, Cb et Cr ; Cb/Cr sont décalés de +0,5.

    Utilise les coefficients BT.601 de l’auteur sans conversion gamma.
    Lève ValueError pour une forme incorrecte ou des pixels hors de [0, 1].
    """
    if (rgb.ndim != 3 or rgb.shape[0] != 3 or rgb.size == 0
            or not np.isfinite(rgb).all() or rgb.min() < 0 or rgb.max() > 1):
        raise ValueError('RGB normalisé 3×H×W requis pour la conversion YCbCr')
    converted = np.einsum('ij,jhw->ihw', RGB_TO_YCBCR, rgb)
    return converted[0], converted[1] + 0.5, converted[2] + 0.5


def merge_luminance(y: FloatImage, cb: FloatImage, cr: FloatImage) -> FloatImage:
    """Recompose un RGB 3×H×W avec les coefficients et écrêtages de l’auteur.

    Les trois plans doivent être finis, non vides et de même forme H×W.
    Écrête Y/Cb/Cr puis RGB dans [0, 1] ; cet écrêtage peut changer la couleur.
    """
    if (y.ndim != 2 or y.size == 0 or y.shape != cb.shape or y.shape != cr.shape
            or not all(np.isfinite(p).all() for p in (y, cb, cr))):
        raise ValueError('Plans Y/Cb/Cr finis et de même forme requis')
    planes = np.stack((np.clip(y, 0, 1), np.clip(cb, 0, 1) - 0.5,
                       np.clip(cr, 0, 1) - 0.5)).astype(np.float32)
    return np.clip(np.einsum('ij,jhw->ihw', YCBCR_TO_RGB, planes), 0, 1)


def guided_chroma(
    guide: FloatImage, chroma: FloatImage, strength: float,
) -> FloatImage:
    """Lisse Cb ou Cr avec le filtre guidé par Y du mode « full » amont.

    Plans H×W finis et normalisés ; intensité dans [0,1]. Le rayon (pixels)
    va de 2 à 12 et epsilon vaut (0,001 + 0,05×intensité effective)².
    scipy.uniform_filter avec mode reflect reproduit les moyennes locales
    de cv2.boxFilter/BORDER_REFLECT sans dépendance OpenCV supplémentaire.
    """
    if (guide.ndim != 2 or guide.size == 0 or guide.shape != chroma.shape
            or not np.isfinite(strength) or not 0 <= strength <= 1
            or not all(np.isfinite(p).all() and p.min() >= 0 and p.max() <= 1
                       for p in (guide, chroma))):
        raise ValueError('Guide/chrominance normalisés et intensité dans [0,1] requis')
    effective = min(strength * CHROMA_STRENGTH_SCALE, 1.0)
    if effective == 0:
        return chroma.copy()
    radius = CHROMA_RADIUS_MIN_PX + int(round(CHROMA_RADIUS_SPAN_PX * effective))
    epsilon = (CHROMA_EPSILON_BASE + CHROMA_EPSILON_SPAN * effective) ** 2

    def mean(plane: FloatImage) -> FloatImage:
        """Calcule la moyenne carrée de rayon donné avec réflexion aux bords."""
        return uniform_filter(plane, size=2 * radius + 1, mode='reflect')

    mean_y, mean_c = mean(guide), mean(chroma)
    covariance = mean(guide * chroma) - mean_y * mean_c
    variance = mean(guide * guide) - mean_y * mean_y
    slope = covariance / (variance + epsilon)
    intercept = mean_c - slope * mean_y
    filtered = mean(slope) * guide + mean(intercept)
    return ((1 - effective) * chroma + effective * filtered).astype(np.float32)
