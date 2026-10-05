"""Inférence Cosmic Clarity sur FITS mono/RGB, sans interface ni moteur externe.

Adaptation des réseaux MIT de Franklin Marek : canaux indépendants, tuiles
256 px recouvrantes, mélange avec l'entrée et étirement temporaire. Les choix
et différences avec l'application amont figurent dans cosmic-clarity.md.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import numpy as np
from astropy.io import fits
from numpy.typing import NDArray
import torch
from torch import nn

from lib.cosmic_clarity.models import (
    DENOISE_MODEL, NONSTELLAR_MODELS, STELLAR_MODEL, manifest, verify_model,
)
from lib.cosmic_clarity.network import RestorationCNN
from lib.type_defs import JSONReport


FloatImage = NDArray[np.float32]
TILE_SIZE = 256
OVERLAP = 64
BORDER = 16
TARGET_MEDIAN = 0.25


def select_device(name: str) -> torch.device:
    """Choisit CPU ou CUDA explicitement, sans repli silencieux vers le CPU."""
    if name not in ('cpu', 'cuda'):
        raise ValueError('cosmic-device doit être cpu ou cuda')
    if name == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError(
            'CUDA indisponible. Vérifier le pilote NVIDIA et installer avec '
            '--install-cosmic-clarity, ou choisir --cosmic-device cpu.'
        )
    return torch.device(name)


def midtone(image: FloatImage, source: float, target: float) -> FloatImage:
    """Déplace une médiane dans ]0,1[ par la transformation rationnelle amont.

    La transformation conserve 0 et 1. Une médiane dégénérée laisse les pixels
    inchangés pour éviter une division par zéro. Entrée et sortie dans [0,1].
    """
    if not 0 < source < 1 or not 0 < target < 1:
        return image.copy()
    denominator = source * (target + image - 1) - target * image
    return np.clip(target * (source - 1) * image / denominator, 0, 1).astype(np.float32)


def restore_plane(plane: FloatImage, model: nn.Module, device: torch.device) -> FloatImage:
    """Infère par tuiles avec marge médiane et moyenne des recouvrements.

    Les 16 pixels externes de chaque prédiction sont exclus ; la marge ajoutée
    garantit la couverture des bords de l'image. Un seul lot est envoyé au GPU.
    """
    padded = np.pad(plane, BORDER, constant_values=float(np.median(plane)))
    total = np.zeros_like(padded)
    count = np.zeros_like(padded)
    stride = TILE_SIZE - OVERLAP
    with torch.inference_mode():
        for y in range(0, padded.shape[0] - 2 * BORDER, stride):
            for x in range(0, padded.shape[1] - 2 * BORDER, stride):
                tile = padded[y:y + TILE_SIZE, x:x + TILE_SIZE]
                height, width = tile.shape
                full = np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.float32)
                full[:height, :width] = tile
                tensor = torch.from_numpy(full)[None, None].repeat(1, 3, 1, 1).to(device)
                predicted = model(tensor)[0, 0].float().cpu().numpy()
                if not np.isfinite(predicted).all():
                    raise ValueError('Le réseau a produit des pixels non finis')
                ys = slice(y + BORDER, y + height - BORDER)
                xs = slice(x + BORDER, x + width - BORDER)
                total[ys, xs] += predicted[BORDER:height - BORDER, BORDER:width - BORDER]
                count[ys, xs] += 1
    core = np.s_[BORDER:-BORDER, BORDER:-BORDER]
    if not np.all(count[core] > 0):
        raise RuntimeError('Couverture incomplète des tuiles Cosmic Clarity')
    return total[core] / count[core]


class CosmicClarityEngine:
    """Charge uniquement les poids nécessaires et produit un candidat FITS.

    Les poids sont vérifiés par SHA-256 puis chargés avec weights_only=True.
    Les données sont traitées en float32 ; les HDU et en-têtes sont conservés.
    """

    def __init__(self, directory: Path, device: str = 'cuda') -> None:
        """Configure les poids locaux et le périphérique sans télécharger de fichier."""
        self.directory = directory
        self.device = select_device(device)
        self.used_models: dict[str, str] = {}

    def _load(self, name: str) -> nn.Module:
        """Charge strictement un checkpoint compatible sur le périphérique choisi."""
        path = verify_model(self.directory, name)
        checkpoint = torch.load(path, map_location='cpu', weights_only=True)
        state = checkpoint.get('model_state_dict', checkpoint)
        model = RestorationCNN()
        model.load_state_dict(state, strict=True)
        self.used_models[name] = manifest()[name]['sha256']
        return model.to(self.device).eval()

    def _apply(self, planes: FloatImage, name: str) -> FloatImage:
        """Applique le même réseau à chaque canal puis libère ses poids."""
        model = self._load(name)
        try:
            return np.stack([restore_plane(p, model, self.device) for p in planes])
        finally:
            del model

    def process(
        self, source: Path, destination: Path,
        operation: Literal['sharpen', 'denoise'], *,
        stellar_amount: float = 0.5, nonstellar_amount: float = 0.5,
        radius: float = 3.0, denoise_amount: float = 0.5,
    ) -> JSONReport:
        """Écrit un candidat sans écraser l'entrée et retourne les paramètres d'audit.

        Les FITS CFA, dimensions non mono/RGB et pixels non finis sont refusés.
        Les canaux sont normalisés dans [0,1], restaurés indépendamment, puis
        remis dans l’échelle initiale des flottants ou l’échelle Siril normalisée
        pour les entiers. Une image constante est refusée.
        """
        if operation not in ('sharpen', 'denoise'):
            raise ValueError('Opération Cosmic Clarity inconnue')
        self.used_models.clear()
        values = (stellar_amount, nonstellar_amount, radius, denoise_amount)
        if (not np.isfinite(values).all() or not 1 <= radius <= 8
                or any(not 0 <= value <= 1 for value in
                       (stellar_amount, nonstellar_amount, denoise_amount))):
            raise ValueError('Intensités Cosmic Clarity entre 0 et 1 ; rayon entre 1 et 8')
        if source.resolve() == destination.resolve():
            raise ValueError('Le candidat ne peut pas remplacer la source')
        with fits.open(source, memmap=False) as original:
            hdul = fits.HDUList([hdu.copy() for hdu in original])
        with hdul:
            data = hdul[0].data
            if (data is None or data.ndim not in (2, 3)
                    or (data.ndim == 3 and data.shape[0] != 3) or data.size == 0):
                raise ValueError('Image FITS mono ou RGB requise')
            if hdul[0].header.get('BAYERPAT', '').strip():
                raise ValueError('Dématricer le FITS CFA avant Cosmic Clarity')
            if not np.isfinite(data).all() or data.max() == data.min():
                raise ValueError('Image constante ou contenant des pixels non finis')
            integer_scale = float(np.iinfo(data.dtype).max) if data.dtype.kind in 'iu' else 1.0
            planes = np.asarray(data, dtype=np.float32) / integer_scale
            if not np.isfinite(planes).all():
                raise ValueError('Pixels hors de la plage float32')
            if planes.ndim == 2:
                planes = planes[None]
            offset = min(0.0, float(planes.min()))
            scale = max(1.0, float(planes.max()) - offset)
            normalized = (planes - offset) / scale
            prepared = normalized.copy()
            stretches = []
            threshold = 0.08 if operation == 'sharpen' else 0.05
            for i, plane in enumerate(normalized):
                minimum = float(plane.min())
                median = float(np.median(plane - minimum))
                enabled = 0 < median < threshold
                if enabled:
                    prepared[i] = midtone(plane - minimum, median, TARGET_MEDIAN)
                stretches.append((enabled, minimum, median))
            restored = prepared.copy()
            if operation == 'denoise' and denoise_amount > 0:
                restored += denoise_amount * (self._apply(prepared, DENOISE_MODEL) - prepared)
            elif operation == 'sharpen':
                if stellar_amount > 0:
                    restored += stellar_amount * (self._apply(restored, STELLAR_MODEL) - restored)
                if nonstellar_amount > 0:
                    radii = list(NONSTELLAR_MODELS)
                    low = max(r for r in radii if r <= radius)
                    high = min(r for r in radii if r >= radius)
                    prediction = self._apply(restored, NONSTELLAR_MODELS[low])
                    if high != low:
                        weight = (radius - low) / (high - low)
                        prediction = ((1 - weight) * prediction
                                      + weight * self._apply(restored, NONSTELLAR_MODELS[high]))
                    restored += nonstellar_amount * (prediction - restored)
            for i, (enabled, minimum, median) in enumerate(stretches):
                if enabled:
                    restored[i] = midtone(
                        restored[i], float(np.median(restored[i])), median,
                    ) + minimum
            restored = (restored * scale + offset).astype(np.float32)
            if not np.isfinite(restored).all():
                raise ValueError('Pixels non finis après restauration des unités')
            hdul[0].data = restored[0] if data.ndim == 2 else restored
            hdul[0].header.pop('BLANK', None)
            hdul[0].header.add_history(f'Cosmic Clarity {operation}; {self.device}; float32')
            hdul.writeto(destination, overwrite=False)
        logging.info('Cosmic Clarity %s sur %s : %s', operation, self.device, destination)
        return dict(
            engine='cosmic-clarity', operation=operation, device=str(self.device),
            torch_version=str(torch.__version__), models=dict(self.used_models),
            precision='float32', channels='independent', tile_size=TILE_SIZE,
            overlap=OVERLAP, normalization_offset=offset, normalization_scale=scale,
            integer_scale=integer_scale, temporary_stretch=[s[0] for s in stretches],
            stellar_amount=stellar_amount, nonstellar_amount=nonstellar_amount,
            nonstellar_radius=radius, denoise_amount=denoise_amount,
        )
