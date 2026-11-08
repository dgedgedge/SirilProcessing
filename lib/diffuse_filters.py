"""Filtres diffus PyTorch CUDA/MPS et référence SciPy CPU.

Les bords reproduisent reflect de SciPy, distinct du padding reflect natif
PyTorch. La médiane paire moyenne ses deux valeurs centrales. Voir
``docs/scripts/postProcess/diffuse-regions.md``.
"""
from __future__ import annotations

from collections.abc import Sequence
from contextlib import nullcontext

import numpy as np
from scipy.ndimage import gaussian_filter

from lib.torch_backend import TorchComputeBackend
from lib.type_defs import JSONReport

class DiffuseFilterBackend(TorchComputeBackend):
    """Sélectionne CPU/CUDA/MPS avant toute fenêtre, sans reprise après panne."""

    BYTES_PER_PIXEL = 128

    def __init__(self, requested: str, maximum_pixels: int, *, requires_float64: bool = False) -> None:
        """Estime 128 octets par pixel avant le précontrôle commun CUDA/MPS."""
        if not isinstance(maximum_pixels, int) or maximum_pixels < 0:
            raise ValueError('Nombre maximal de pixels invalide')
        self.windows = self.scales = 0
        self.maximum_pixels = maximum_pixels
        super().__init__(requested, maximum_pixels * self.BYTES_PER_PIXEL,
                         engine='Signal diffus', requires_float64=requires_float64)

    def multiscale(
        self, centered: np.ndarray, difference: np.ndarray, aperture: np.ndarray,
        sigma_map: np.ndarray, scales: Sequence[float], source_sigma: float, sigma: float,
    ) -> tuple[list[JSONReport], bool]:
        """Calcule les mêmes mesures, en conservant les types et les seuils CPU.

        Les flux et le bootstrap restent sur CPU. Les fenêtres sont transférées
        successivement ; seuls les effectifs et médianes scalaires reviennent.
        Les plans de types différents sont filtrés séparément sans promotion.
        """
        if (centered.ndim != 2 or centered.shape != difference.shape
                or centered.shape != aperture.shape or centered.shape != sigma_map.shape):
            raise ValueError('Fenêtres diffuses et masques 2D de mêmes dimensions requis')
        if centered.size > self.maximum_pixels:
            raise ValueError('Fenêtre diffuse supérieure au budget mémoire vérifié')
        gpu = self.name != 'cpu'
        if gpu and self.name == 'mps' and any(a.dtype == np.float64 for a in (centered, difference, sigma_map)):
            raise RuntimeError('MPS ne prend pas en charge float64 ; sélectionner CPU avant calcul')
        self.windows += 1
        mixed = centered.dtype != difference.dtype
        if gpu:
            torch = self.torch
            if mixed:
                first = torch.as_tensor(np.ascontiguousarray(centered), device=self.device)[None, None]
                second = torch.as_tensor(np.ascontiguousarray(difference), device=self.device)[None, None]
            else:
                batch = torch.as_tensor(np.stack((centered, difference)), device=self.device)[:, None]
            mask = torch.as_tensor(np.array(aperture, copy=True), device=self.device)
            threshold = torch.as_tensor(source_sigma*sigma_map/8, device=self.device)
        results, corroborated = [], False
        with self.torch.inference_mode() if gpu else nullcontext():
            for scale in scales:
                if gpu:
                    if mixed:
                        structure = self._torch_gaussian(first, scale)[0, 0]
                        removed = self._torch_gaussian(second, scale)[0, 0]
                    else:
                        filtered = self._torch_gaussian(batch, scale)
                        structure, removed = filtered[0, 0], filtered[1, 0]
                    valid = mask & (structure > threshold)
                    count = int(valid.sum().item())
                    amplitude = self._torch_median(removed[valid]) if count >= 32 else None
                else:
                    structure = gaussian_filter(centered, scale)
                    removed = gaussian_filter(difference, scale)
                    valid = aperture & (structure > source_sigma*sigma_map/8)
                    count = int(valid.sum())
                    amplitude = float(np.median(removed[valid])) if count >= 32 else None
                self.scales += 1
                if amplitude is not None:
                    detectable = bool(abs(amplitude) > 3*sigma/max(1, min(2*np.sqrt(np.pi)*scale, 8)))
                    corroborated |= detectable
                    results.append(dict(scale_px=scale, residual_median=amplitude, significant=detectable))
        return results, corroborated

    def report(self) -> JSONReport:
        """Expose le moteur effectif, sa raison et le volume filtré."""
        return dict(name=self.name, implementation='pytorch' if self.name != 'cpu' else 'scipy',
                    requested=self.requested, reason=self.reason, required_bytes=self.required_bytes,
                    bytes_per_pixel=self.BYTES_PER_PIXEL, filtered_windows=self.windows,
                    filtered_scales=self.scales, device=self.device_info)
