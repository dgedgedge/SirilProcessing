"""Échantillonnage spatial et statistiques de bruit par lots NumPy/PyTorch.

Les calculs sont des utilitaires sans état du SkyNoiseProcessor. La sélection
ne dépend que des coordonnées des tuiles de la référence, jamais du candidat.
Voir docs/scripts/postProcess/noise-tiles.md pour les formules et limites.
"""

from __future__ import annotations

from collections.abc import Sequence
from types import ModuleType
from typing import TYPE_CHECKING

import numpy as np

from lib.restoration_quality import MAD_SCALE
from lib.torch_backend import TorchArrayAPI, TorchComputeBackend

if TYPE_CHECKING:
    import torch

Tile = tuple[slice, slice]


def spatial_noise_tiles(
    tiles: Sequence[Tile], shape: tuple[int, int], maximum: int,
) -> tuple[Tile, ...]:
    """Retient au plus maximum tuiles couvrant les quadrants et le champ.

    maximum=0 conserve toutes les tuiles. Sinon, une première tuile par quadrant
    présent est choisie, puis le point le plus éloigné des points déjà retenus
    complète l'échantillon. Les distances utilisent les coordonnées relatives
    au champ. Les ex aequo suivent l'ordre initial, conservé dans le résultat.
    Aucun choix ne dépend des intensités ou du résultat après traitement.
    """
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 0:
        raise ValueError('Le plafond des tuiles doit être positif ou nul')
    if maximum == 0 or len(tiles) <= maximum:
        return tuple(tiles)
    height, width = shape
    centers = np.array([
        ((ys.start + ys.stop) / (2 * height), (xs.start + xs.stop) / (2 * width))
        for ys, xs in tiles
    ])
    quadrants = np.array([
        2 * int(ys.start >= height / 2) + int(xs.start >= width / 2)
        for ys, xs in tiles
    ])
    selected = np.zeros(len(tiles), dtype=bool)
    minimum_distance = np.full(len(tiles), np.inf)

    def include(index: int) -> None:
        """Actualise la distance minimale aux seules tuiles déjà retenues."""
        selected[index] = True
        distance = np.sum((centers - centers[index]) ** 2, axis=1)
        np.minimum(minimum_distance, distance, out=minimum_distance)
        minimum_distance[selected] = -1

    for quadrant in np.unique(quadrants):
        if np.count_nonzero(selected) == maximum:
            break
        indices = np.flatnonzero(quadrants == quadrant)
        target = np.array([.25 + .5 * (quadrant // 2), .25 + .5 * (quadrant % 2)])
        include(int(indices[np.argmin(np.sum((centers[indices] - target) ** 2, axis=1))]))
    while np.count_nonzero(selected) < maximum:
        include(int(np.argmax(minimum_distance)))
    return tuple(tile for tile, retained in zip(tiles, selected) if retained)


def batch_noise_statistics(
    before: np.ndarray | torch.Tensor, after: np.ndarray | torch.Tensor,
    masks: np.ndarray | torch.Tensor, *, xp: ModuleType | TorchArrayAPI = np,
) -> tuple[np.ndarray | torch.Tensor, np.ndarray | torch.Tensor]:
    """Mesure dispersion MAD et corrélation horizontale, par tuile et image.

    Parameters
    ----------
    before, after : tableaux NumPy ou tenseurs PyTorch
        Lots flottants de forme (tuiles, hauteur, largeur), mêmes unités avant/après.
    masks : tableau booléen NumPy ou tenseur PyTorch
        Même forme ; les pixels admissibles et les limites des tuiles sont True.
        Chaque tuile doit contenir au moins un pixel admissible.
    xp : module ou TorchArrayAPI, optional
        NumPy ou adaptateur PyTorch préinitialisé. Aucun transfert implicite vers CPU n'est effectué.

    Returns
    -------
    tuple de tableaux
        Dispersion et corrélation, chacun de forme (tuiles, 2), avant puis après.
        Une corrélation non mesurable est NaN ; la dispersion nulle reste nulle.

    Notes
    -----
    La tendance plane est ajustée en float64 avec coordonnées centrées. Le
    système de covariance 2x2 est résolu en parallèle ; sa pseudo-inverse de
    rang un couvre les tuiles de bord à une seule ligne/colonne. Le MAD vaut
    1,4826 fois la médiane des écarts absolus au résidu médian, sur le masque.
    Seules les paires horizontales dont les deux pixels sont admissibles entrent
    dans la corrélation. Les tableaux d'entrée ne sont jamais modifiés.
    """
    residual, mask = _noise_residuals(before, after, masks, xp=xp)
    return _residual_statistics(residual, mask, xp=xp)


def _noise_residuals(
    before: np.ndarray | torch.Tensor, after: np.ndarray | torch.Tensor,
    masks: np.ndarray | torch.Tensor, *, xp: ModuleType | TorchArrayAPI = np,
) -> tuple[np.ndarray | torch.Tensor, np.ndarray | torch.Tensor]:
    """Retire les plans en float64 avant toute conversion MPS des résidus."""
    before = xp.asarray(before, dtype=xp.float64)
    after = xp.asarray(after, dtype=xp.float64)
    mask = xp.asarray(masks, dtype=bool)
    if (before.ndim != 3 or before.shape != after.shape or before.shape != mask.shape
            or any(length == 0 for length in before.shape)):
        raise ValueError('Lots de tuiles avant/après et masques de même forme 3D requis')
    if bool(xp.any(mask & (~xp.isfinite(before) | ~xp.isfinite(after))).item()):
        raise ValueError('Les pixels admissibles des tuiles doivent être finis')
    batch, height, width = before.shape
    weights = xp.asarray(mask.reshape(batch, -1), dtype=xp.float64)
    count = weights.sum(axis=1)
    if bool(xp.any(count == 0).item()):
        raise ValueError('Une tuile sans pixel admissible ne peut être mesurée')
    yy, xx = xp.indices((height, width), dtype=xp.float64)
    x, y = xx.ravel(), yy.ravel()
    dx = x[None] - (weights * x[None]).sum(axis=1)[:, None] / count[:, None]
    dy = y[None] - (weights * y[None]).sum(axis=1)[:, None] / count[:, None]
    sxx, syy = (weights * dx * dx).sum(axis=1), (weights * dy * dy).sum(axis=1)
    sxy = (weights * dx * dy).sum(axis=1)
    values = xp.stack((before, after), axis=1).reshape(batch, 2, -1)
    values = xp.where(mask.reshape(batch, 1, -1), values, 0)
    means = values.sum(axis=2) / count[:, None]
    vx = (values * dx[:, None]).sum(axis=2)
    vy = (values * dy[:, None]).sum(axis=2)
    determinant = sxx * syy - sxy * sxy
    full_rank = determinant > 64 * np.finfo(np.float64).eps * sxx * syy
    safe_det = xp.where(full_rank, determinant, 1)[:, None]
    trace_squared = xp.maximum((sxx + syy) ** 2, np.finfo(np.float64).tiny)[:, None]
    slope_x = xp.where(
        full_rank[:, None], (syy[:, None] * vx - sxy[:, None] * vy) / safe_det,
        (sxx[:, None] * vx + sxy[:, None] * vy) / trace_squared,
    )
    slope_y = xp.where(
        full_rank[:, None], (sxx[:, None] * vy - sxy[:, None] * vx) / safe_det,
        (sxy[:, None] * vx + syy[:, None] * vy) / trace_squared,
    )
    residual = values - means[:, :, None] - slope_x[:, :, None] * dx[:, None]
    residual -= slope_y[:, :, None] * dy[:, None]
    return residual.reshape(batch, 2, height, width), mask


def _residual_statistics(
    residual: np.ndarray | torch.Tensor, mask: np.ndarray | torch.Tensor, *, xp: ModuleType | TorchArrayAPI = np,
) -> tuple[np.ndarray | torch.Tensor, np.ndarray | torch.Tensor]:
    """Mesure MAD et corrélation sur les mêmes résidus et paires admissibles."""
    batch, _, height, width = residual.shape
    residual = residual.reshape(batch, 2, -1)
    valid = mask.reshape(batch, 1, -1)
    masked = xp.where(valid, residual, xp.nan)
    median = xp.nanmedian(masked, axis=2)
    sigma = MAD_SCALE * xp.nanmedian(abs(masked - median[:, :, None]), axis=2)

    residual = residual.reshape(batch, 2, height, width)
    adjacent = (mask[:, :, :-1] & mask[:, :, 1:]).reshape(batch, 1, -1)
    n_adjacent = adjacent.sum(axis=2)
    safe_count = xp.maximum(n_adjacent, 1)
    left, right = residual[:, :, :, :-1].reshape(batch, 2, -1), residual[:, :, :, 1:].reshape(batch, 2, -1)
    left_mean = xp.where(adjacent, left, 0).sum(axis=2) / safe_count
    right_mean = xp.where(adjacent, right, 0).sum(axis=2) / safe_count
    left = xp.where(adjacent, left - left_mean[:, :, None], 0)
    right = xp.where(adjacent, right - right_mean[:, :, None], 0)
    denominator = xp.sqrt((left * left).sum(axis=2) * (right * right).sum(axis=2))
    correlation = (left * right).sum(axis=2) / xp.where(denominator > 0, denominator, 1)
    correlation = xp.where(
        (n_adjacent > 3) & (denominator > 0), xp.clip(correlation, -1, 1), xp.nan,
    )
    return sigma, correlation


class NoiseTileBackend(TorchComputeBackend):
    """Accélère les lots CUDA ; MPS reçoit des résidus déjà ajustés en float64."""

    def _preflight(self) -> None:
        """Vérifie aussi les statistiques masquées et le type requis par le moteur."""
        super()._preflight()
        before = (.1 + .001*np.sin(np.arange(64))).reshape(2, 4, 8)
        masks = np.ones(before.shape, dtype=bool)
        masks[:, 0, 0] = False
        sigma, correlation = self.statistics(before, .1+.5*(before-.1), masks)
        if (not np.isfinite(sigma).all() or not np.isfinite(correlation).all()
                or not np.allclose(sigma[:, 1]/sigma[:, 0], .5, rtol=3e-5)):
            raise RuntimeError('Contrôle initial des statistiques de bruit incorrect')

    def statistics(
        self, before: np.ndarray, after: np.ndarray, masks: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Retourne deux petites matrices CPU, sans changer les seuils scientifiques.

        CUDA conserve float64. MPS retire les plans sur CPU float64 puis
        normalise les résidus par tuile/image avant conversion float32 :
        même un bruit faible sur un fond élevé conserve ses chiffres utiles.
        Seule la mesure de MAD/corrélation est alors effectuée sur MPS.
        """
        if self.name == 'cpu':
            return batch_noise_statistics(before, after, masks)
        with self.torch.inference_mode():
            if self.name == 'mps':
                residual, mask = _noise_residuals(before, after, masks)
                magnitude = np.max(np.where(mask[:, None], abs(residual), 0), axis=(2, 3), keepdims=True)
                magnitude = np.where(magnitude > 0, magnitude, 1)
                scaled = np.where(mask[:, None], residual / magnitude, 0)
                sigma, correlation = _residual_statistics(
                    self.xp.asarray(scaled, dtype=self.xp.float32), self.xp.asarray(mask), xp=self.xp,
                )
                return self.numpy(sigma).astype(np.float64) * magnitude[:, :, 0, 0], self.numpy(correlation)
            sigma, correlation = batch_noise_statistics(before, after, masks, xp=self.xp)
            return self.numpy(sigma), self.numpy(correlation)
