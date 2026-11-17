"""Sélection et pondération spatiale reproductibles des pics stellaires.

Les coordonnées sont natives FITS (y, x). La sélection ne lit ni fond ni image
originale. Voir docs/scripts/postProcess/star-rendering.md pour les limites.
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import distance_transform_edt

from lib.type_defs import JSONReport


def select_stellar_peaks(
    shape: tuple[int, int], positions: tuple[np.ndarray, np.ndarray],
    amplitudes: np.ndarray, eligible: np.ndarray, fraction: float,
) -> JSONReport:
    """Réserve floor(fraction*n) pics éligibles, en excluant les ex aequo limites.

    Parameters
    ----------
    shape : tuple[int, int]
        Hauteur et largeur natives de la couche d'étoiles.
    positions : tuple[np.ndarray, np.ndarray]
        Coordonnées y/x des pics candidats détectés sur cette couche seule.
    amplitudes : np.ndarray
        Maximum RGB normalisé aux positions candidates.
    eligible : np.ndarray
        Masque des pics suffisamment distincts du bruit pour le calage.
    fraction : float
        Budget dans [0,1]. Zéro conserve la base ; un retrouve l'adaptation
        historique globale, y compris entre les pics.

    Returns
    -------
    dict
        Recette JSON indépendante des tableaux d'entrée : budget, effectifs et
        coordonnées sélectionnées/autres, nécessaires à sa reproduction.

    Raises
    ------
    ValueError
        Fraction non finie ou hors domaine. Les tableaux proviennent du moteur
        validé ; les coordonnées sont contrôlées lors du calcul des poids.

    Examples
    --------
    >>> positions = (np.array([1, 3]), np.array([1, 3]))
    >>> plan = select_stellar_peaks((5, 5), positions, np.array([.01, .1]),
    ...                            np.array([True, True]), .5)
    >>> plan['selected']
    [[3, 3]]
    >>> stellar_selection_weights(plan, (5, 5))[positions].tolist()
    [0.0, 1.0]
    """
    if not np.isfinite(fraction) or not 0 <= fraction <= 1:
        raise ValueError('Fraction stellaire hors de [0,1]')
    peaks = amplitudes[eligible]
    budget = int(np.floor(fraction * peaks.size))
    selected = np.zeros_like(eligible, dtype=bool)
    cutoff = None
    if fraction == 1:
        selected = eligible.copy()
        mode = 'all'
    elif budget == 0:
        mode = 'none'
    else:
        cutoff = float(np.sort(peaks)[-budget - 1])
        selected = eligible & (amplitudes > cutoff)
        mode = 'spatial' if selected.any() else 'none'
    coordinates = np.column_stack(positions)
    return dict(
        mode=mode, fraction=float(fraction), shape=list(shape),
        eligible_count=int(peaks.size), budget=budget,
        selected_count=int(selected.sum()), cutoff=cutoff,
        selected=coordinates[selected].tolist() if mode == 'spatial' else [],
        others=coordinates[~selected].tolist() if mode == 'spatial' else [],
    )


def stellar_selection_weights(selection: JSONReport, shape: tuple[int, ...]) -> np.ndarray:
    """Rejoue les poids float32 des distances sans redétecter ni classer de pics.

    Le poids vaut d_autres^4/(d_sélection^4+d_autres^4), un aux pics sélectionnés
    et zéro aux autres. Les modes all/none sont constants. Un mode spatial
    nécessite la géométrie native et des coordonnées entières distinctes,
    dans le domaine ; toute incohérence lève ValueError. Les distances sont en
    pixels. Les étoiles proches ne sont pas séparées physiquement.
    """
    mode = selection.get('mode', 'all')
    if mode in ('all', 'none'):
        return np.full(shape, 1 if mode == 'all' else 0, dtype=np.float32)
    if mode != 'spatial' or len(shape) != 2 or list(shape) != selection.get('shape'):
        raise ValueError('Géométrie de sélection stellaire incompatible')
    distances = []
    seen: set[tuple[int, int]] = set()
    for key in ('selected', 'others'):
        points = np.asarray(selection.get(key, []))
        if (points.ndim != 2 or points.shape[1] != 2 or not points.size
                or not np.issubdtype(points.dtype, np.integer)):
            raise ValueError('Coordonnées de sélection stellaire invalides')
        if ((points < 0).any() or (points[:, 0] >= shape[0]).any()
                or (points[:, 1] >= shape[1]).any()):
            raise ValueError('Coordonnées stellaires hors image')
        unique = {tuple(map(int, p)) for p in points}
        if len(unique) != len(points) or seen.intersection(unique):
            raise ValueError('Pics stellaires dupliqués ou partagés')
        seen.update(unique)
        seeds = np.ones(shape, dtype=bool)
        seeds[points[:, 0], points[:, 1]] = False
        distances.append(distance_transform_edt(seeds).astype(np.float32))
    selected_power = distances[0] ** 4
    other_power = distances[1] ** 4
    return other_power / (selected_power + other_power)
