"""Calculs PyTorch partagés CUDA/MPS : précontrôle, filtres et statistiques.

La référence CPU reste NumPy/SciPy. Les conventions de bord et de médiane
reproduisent SciPy/NumPy ; aucune reprise CPU n'a lieu après le précontrôle.
Voir docs/scripts/postProcess/torch-compute.md.
"""
from __future__ import annotations

import importlib
import logging
from contextlib import nullcontext
from typing import TYPE_CHECKING
from types import ModuleType
from collections.abc import Sequence

import numpy as np

from lib.compute_config import ComputeConfig
from lib.type_defs import JSONReport

if TYPE_CHECKING:
    import torch


class TorchComputeBackend:
    """Choisit CUDA, MPS ou CPU avant traitement et conserve les opérations GPU."""

    def __init__(self, requested: str, required_bytes: int = 0, *, engine: str = "PyTorch",
                 requires_float64: bool = False) -> None:
        """Contrôle disponibilité, précision, mémoire et opérations avant calcul.

        auto privilégie CUDA puis MPS, sinon SciPy CPU. Une demande explicite
        cuda/mps indisponible échoue. MPS float64 choisit CPU en auto, sans
        conversion silencieuse ; mps forcé exige des données float32.
        """
        if requested not in ('auto', 'cpu', 'cuda', 'mps'):
            raise ValueError('Backend PyTorch attendu : auto, cpu, cuda ou mps')
        if not isinstance(required_bytes, int) or required_bytes < 0:
            raise ValueError('Estimation mémoire invalide')
        self.requested, self.required_bytes = requested, required_bytes
        self.name, self.reason = 'cpu', 'CPU explicitly requested'
        self.torch = None
        self.device = None
        self.device_info: JSONReport = {}
        self._kernels: dict[tuple[float, torch.dtype], torch.Tensor] = {}
        if requested != 'cpu':
            try:
                self.torch = importlib.import_module('torch')
                self.name = self._select_device(requested)
                if self.name == 'cpu':
                    raise RuntimeError('CUDA et MPS indisponibles dans PyTorch')
                if self.name == 'mps' and requires_float64:
                    raise RuntimeError('MPS ne prend pas en charge float64 ; précision conservée sur CPU')
                self.device = self.torch.device(self.name)
                self._check_memory()
                with self.torch.inference_mode():
                    self._preflight()
                self.reason = f'PyTorch {self.name} preflight succeeded'
            except (ImportError, OSError, RuntimeError) as error:
                if requested != 'auto':
                    raise RuntimeError(f'Filtres PyTorch {requested} indisponibles : {error}') from error
                self.name, self.device = 'cpu', None
                self.reason = f'CPU choisi avant calcul : {error}'
                self.device_info = {}
                self._kernels.clear()
                ComputeConfig.warn_cpu_fallback(engine, str(error))
        logging.getLogger(__name__).info('%s : backend %s (%s)', engine, self.name, self.reason)

    def _preflight(self) -> None:
        """Vérifie filtres, médianes paires et percentiles avant les grandes images."""
        probe = self.torch.ones((2, 1, 5, 7), dtype=self.torch.float32, device=self.device)
        result = self._torch_gaussian(probe, 2.)
        median = self._torch_median(result[result > 0])
        values = self.xp.asarray(np.array([1., 4., 2., 3.], dtype='f4'))
        quantiles = self.xp.percentile(values, [25., 50., 75.])
        valid = (bool(self.torch.isfinite(result).all().item()) and abs(median-1) <= 1e-5
                 and abs(self.xp.median(values).item()-2.5) < 1e-6
                 and bool((abs(quantiles-self.xp.asarray(np.array([1.75, 2.5, 3.25], dtype='f4'))) < 1e-6).all().item()))
        if not valid:
            raise RuntimeError('Contrôle initial des opérations PyTorch incorrect')

    def _select_device(self, requested: str) -> str:
        """Résout CUDA puis MPS sans charger les poids des moteurs de restauration."""
        cuda = self.torch.cuda.is_available()
        mps = self.torch.backends.mps.is_available()
        if requested == 'auto':
            return 'cuda' if cuda else 'mps' if mps else 'cpu'
        if not (cuda if requested == 'cuda' else mps):
            raise RuntimeError(f'{requested.upper()} indisponible dans PyTorch')
        return requested

    def _check_memory(self) -> None:
        """Compare l'estimation d'une fenêtre à 80 % de la mémoire disponible."""
        self.device_info = {'torch_version': str(self.torch.__version__)}
        if self.name == 'cuda':
            free, total = self.torch.cuda.mem_get_info(self.device)
            self.device_info.update(free_bytes=int(free), total_bytes=int(total))
        else:
            maximum = self.torch.mps.recommended_max_memory()
            allocated = self.torch.mps.driver_allocated_memory()
            free = max(0, maximum - allocated)
            self.device_info.update(recommended_bytes=int(maximum), driver_allocated_bytes=int(allocated))
        if self.required_bytes > .8 * free:
            raise RuntimeError(f'Mémoire {self.name} insuffisante : {free} octets disponibles')

    def _torch_gaussian(self, tensor: torch.Tensor, scale: float) -> torch.Tensor:
        """Convolution séparable, reflect demi-échantillon et troncature 4 sigma.

        Le repliement modulo 2*n inclut les pixels de bord et fonctionne même
        pour une marge plus grande que la fenêtre ou un axe de longueur un.
        Chaque passe conserve le type de sortie comme SciPy. TF32 est désactivé
        dans un contexte cuDNN temporaire, restauré même en cas d'exception.
        """
        torch = self.torch
        key = (float(scale), tensor.dtype)
        if key not in self._kernels:
            radius = int(4 * float(scale) + .5)
            x = np.arange(-radius, radius + 1)
            kernel = np.exp(-.5 / float(scale)**2 * x**2)
            kernel /= kernel.sum()
            self._kernels[key] = torch.as_tensor(kernel, dtype=tensor.dtype, device=tensor.device)
        kernel = self._kernels[key]
        radius = kernel.numel() // 2
        context = (torch.backends.cudnn.flags(enabled=True, benchmark=False,
                                             deterministic=True, allow_tf32=False)
                   if tensor.device.type == 'cuda' else nullcontext())
        with context:
            for axis in (2, 3):
                length = tensor.shape[axis]
                indices = torch.arange(-radius, length+radius, device=tensor.device)
                indices = indices.remainder(2*length)
                indices = torch.where(indices < length, indices, 2*length-1-indices)
                padded = tensor.index_select(axis, indices)
                weight = kernel.reshape(1, 1, -1, 1) if axis == 2 else kernel.reshape(1, 1, 1, -1)
                tensor = torch.nn.functional.conv2d(padded, weight)
        return tensor

    def _torch_median(self, values: torch.Tensor) -> float:
        """Reproduit NumPy pour un effectif pair, impair ou contenant NaN."""
        ordered = values.sort().values
        count = ordered.numel()
        middle = (ordered[count//2] if count % 2 else
                  (ordered[count//2-1] + ordered[count//2]) / 2)
        result = self.torch.where(self.torch.isnan(values).any(),
                                  self.torch.full_like(middle, float('nan')), middle)
        return float(result.item())


    def numpy(self, array: np.ndarray | torch.Tensor) -> np.ndarray:
        """Transfère explicitement un résultat GPU vers NumPy, sans conversion de précision."""
        return array.detach().cpu().numpy() if self.name != 'cpu' else np.asarray(array)

    @property
    def xp(self) -> TorchArrayAPI | ModuleType:
        """Expose les seules opérations communes requises par le bruit et la couleur."""
        if self.name == 'cpu':
            return np
        if not hasattr(self, '_array_api'):
            self._array_api = TorchArrayAPI(self)
        return self._array_api

    def gaussian(self, image: torch.Tensor, scale: float) -> torch.Tensor:
        """Filtre indépendamment les plans d'une image (plans, hauteur, largeur)."""
        return self._torch_gaussian(image[:, None], scale)[:, 0]

    def report(self) -> JSONReport:
        """Décrit la sélection de moteur et l'estimation mémoire avant calcul."""
        return dict(name=self.name, requested=self.requested, reason=self.reason,
                    implementation='pytorch' if self.name != 'cpu' else 'numpy/scipy',
                    required_bytes=self.required_bytes, device=self.device_info)


class TorchArrayAPI:
    """Sous-ensemble explicite NumPy utilisé par les deux formules partagées.

    Les réductions retournent des tenseurs, les médianes paires sont moyennées
    et les percentiles sont interpolés sans la limite de taille torch.quantile.
    Aucun appel inconnu n'est redirigé implicitement vers NumPy.
    """

    def __init__(self, backend: TorchComputeBackend) -> None:
        """Lie les opérations au périphérique déjà contrôlé, sans état global."""
        self.backend, self.torch = backend, backend.torch
        self.float32, self.float64 = self.torch.float32, self.torch.float64
        self.nan, self.inf = float('nan'), float('inf')
        self.abs, self.sqrt = self.torch.abs, self.torch.sqrt
        self.isfinite, self.any = self.torch.isfinite, self.torch.any
        self.count_nonzero, self.clip = self.torch.count_nonzero, self.torch.clamp
        self.stack, self.where = self.torch.stack, self.torch.where

    def asarray(self, values: np.ndarray | torch.Tensor | Sequence[float] | float,
                dtype: torch.dtype | type | None = None) -> torch.Tensor:
        """Copie les entrées NumPy vers le GPU ; les conversions restent explicites."""
        if dtype is bool:
            dtype = self.torch.bool
        if isinstance(values, self.torch.Tensor):
            return values.to(device=self.backend.device, dtype=dtype)
        return self.torch.as_tensor(np.array(values, copy=True), dtype=dtype, device=self.backend.device)

    def indices(self, shape: tuple[int, int], dtype: torch.dtype) -> torch.Tensor:
        """Construit les deux grilles de coordonnées sur le périphérique actif."""
        axes = [self.torch.arange(length, dtype=dtype, device=self.backend.device) for length in shape]
        return self.torch.stack(self.torch.meshgrid(*axes, indexing='ij'))

    def maximum(self, first: torch.Tensor, second: torch.Tensor | float) -> torch.Tensor:
        """Compare un tableau et un scalaire sans transfert vers CPU."""
        return self.torch.maximum(first, self.torch.as_tensor(second, dtype=first.dtype, device=first.device))

    def minimum(self, first: torch.Tensor, second: torch.Tensor | float) -> torch.Tensor:
        """Compare un tableau et un scalaire avec le type du premier tableau."""
        return self.torch.minimum(first, self.torch.as_tensor(second, dtype=first.dtype, device=first.device))

    def max(self, values: torch.Tensor, axis: int | None = None) -> torch.Tensor:
        """Renvoie uniquement les maxima, sans les indices retournés par Tensor.max."""
        return self.torch.amax(values, dim=axis)

    def min(self, values: torch.Tensor, axis: int | None = None) -> torch.Tensor:
        """Renvoie les minima selon les conventions de réduction NumPy."""
        return self.torch.amin(values, dim=axis)

    def mean(self, values: torch.Tensor, axis: int | None = None) -> torch.Tensor:
        """Moyenne sur un axe ou tous les pixels sans modifier le type flottant."""
        return self.torch.mean(values, dim=axis)

    def ptp(self, values: torch.Tensor, axis: int | None = None) -> torch.Tensor:
        """Mesure l'étendue sur le même axe que NumPy."""
        return self.max(values, axis) - self.min(values, axis)

    def nanmedian(self, values: torch.Tensor, axis: int | None = None) -> torch.Tensor:
        """Moyenne les deux centres valides ; une ligne sans valeur reste NaN."""
        if axis is None:
            values, axis = values.reshape(-1), 0
        if values.shape[axis] == 0:
            shape = list(values.shape)
            shape.pop(axis)
            return self.torch.full(shape, self.nan, dtype=values.dtype, device=values.device)
        valid = ~self.torch.isnan(values)
        counts = valid.sum(dim=axis, keepdim=True)
        ordered = self.torch.where(valid, values, self.inf).sort(dim=axis).values
        lower = ((counts-1).clamp(min=0)//2).long()
        upper = (counts//2).long()
        result = (ordered.gather(axis, lower) + ordered.gather(axis, upper)) / 2
        return self.torch.where(counts > 0, result, self.nan).squeeze(axis)

    def median(self, values: torch.Tensor, axis: int | None = None) -> torch.Tensor:
        """Suit NumPy : un NaN contamine la médiane, y compris pour un effectif pair."""
        result = self.nanmedian(values, axis)
        invalid = self.torch.isnan(values).any() if axis is None else self.torch.isnan(values).any(dim=axis)
        return self.torch.where(invalid, self.nan, result)

    def percentile(self, values: torch.Tensor, levels: list[float]) -> torch.Tensor:
        """Interpole les statistiques d'ordre linéairement sur tout tableau fini."""
        ordered = values.reshape(-1).sort().values
        ranks = np.asarray(levels, dtype=np.float64) / 100 * (ordered.numel()-1)
        lower, upper = np.floor(ranks).astype('int64'), np.ceil(ranks).astype('int64')
        lo = ordered[self.asarray(lower)]
        hi = ordered[self.asarray(upper)]
        weight = self.asarray(ranks-lower, dtype=values.dtype)
        return lo + (hi-lo) * weight
