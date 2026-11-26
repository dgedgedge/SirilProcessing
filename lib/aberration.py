"""Correction ONNX des aberrations sur données linéaires mono ou RGB.

Algorithme compatible avec AberrationRemover de Riccardo Alberghi : voir
docs/scripts/postProcess/aberration.md pour les hypothèses et différences.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Protocol

import numpy as np
from astropy.io import fits
from numpy.typing import NDArray
from scipy.ndimage import percentile_filter, zoom

from lib.compute_config import ComputeConfig
from lib.cosmic_clarity.processors import _CosmicClarityProcessor
from lib.parameterized_component import ParameterizedComponent
from lib.type_defs import JSONReport

PATCH_SIZE = 512
OVERLAP = 64
MAD_FLOOR = 1e-8
DEFAULT_MODEL_PATH = (
    Path(__file__).resolve().parents[1]
    / "data" / "AberrationRemover" / "model_v2_5_0.onnx"
)


class InferenceSession(Protocol):
    """Contrat minimal de session ONNX utilisé par le calcul des tuiles."""

    def run(
        self, names: None, inputs: dict[str, NDArray[np.float32]],
    ) -> list[NDArray[np.float32]]:
        """Retourne les tenseurs de sortie pour un lot mono NCHW."""
        ...


class AberrationEngine(ParameterizedComponent):
    """Calcule un candidat indépendant, sans décider de son acceptation."""

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare modèle local, mélange et protections optionnelles."""
        if any(action.dest == 'aberration_model' for action in parser._actions):
            return
        parser.add_argument(
            '--aberration-model', default=str(DEFAULT_MODEL_PATH),
            help=('Modèle ONNX AberrationRemover local '
                  '(par défaut : %(default)s) ; chemin vide pour désactiver'),
        )
        parser.add_argument('--aberration-strength', type=float, default=1.0)
        parser.add_argument('--aberration-dark-ringing', action=argparse.BooleanOptionalAction,
                            default=False)
        parser.add_argument('--aberration-protect-background',
                            action=argparse.BooleanOptionalAction,
                            default=True)

    @staticmethod
    def starts(length: int) -> list[int]:
        """Couvre un axe en pixels, y compris les images inférieures à 512 px."""
        last = max(0, length - PATCH_SIZE)
        return sorted(set([*range(0, last, PATCH_SIZE - OVERLAP), last]))

    @staticmethod
    def restore(
        data: NDArray[np.float32], session: InferenceSession, input_name: str,
        strength: float = 1.0, dark_ringing: bool = False,
        protect_background: bool = True,
    ) -> NDArray[np.float32]:
        """Infère les plans CHW normalisés puis applique les protections.

        Les tuiles de 512 px se recouvrent de 64 px et sont pondérées par Hann.
        Chaque prédiction retrouve médiane et MAD de son entrée. Les petites
        images sont complétées par réflexion. Les valeurs non finies, formes
        incompatibles et intensités hors [0, 1] lèvent ValueError.
        """
        if (data.ndim != 3 or data.shape[0] not in (1, 3)
                or min(data.shape) < 1 or not np.isfinite(data).all()):
            raise ValueError('Image finie CHW mono ou RGB requise')
        if not np.isfinite(strength) or not 0 <= strength <= 1:
            raise ValueError('aberration-strength doit être dans [0, 1]')
        if strength == 0:
            return data.copy()
        height, width = data.shape[1:]
        padded = np.pad(data, ((0, 0), (0, max(0, PATCH_SIZE - height)),
                               (0, max(0, PATCH_SIZE - width))), mode='reflect')
        window = np.outer(np.hanning(PATCH_SIZE), np.hanning(PATCH_SIZE)).astype(np.float32)
        window = np.maximum(window / window.max(), 1e-3)
        output = np.zeros_like(padded, dtype=np.float32)
        weights = np.zeros(padded.shape[1:], dtype=np.float32)
        for y in AberrationEngine.starts(padded.shape[1]):
            for x in AberrationEngine.starts(padded.shape[2]):
                region = (slice(y, y + PATCH_SIZE), slice(x, x + PATCH_SIZE))
                weights[region] += window
                for channel in range(len(data)):
                    patch = padded[channel][region]
                    prediction = np.asarray(session.run(None, {
                        input_name: patch[None, None].astype(np.float32),
                    })[0])
                    if prediction.shape != (1, 1, PATCH_SIZE, PATCH_SIZE):
                        raise ValueError('Sortie ONNX attendue : [1, 1, 512, 512]')
                    if not np.isfinite(prediction).all():
                        raise ValueError('Prédiction ONNX non finie')
                    prediction = prediction[0, 0]
                    median = np.median(patch)
                    mad = max(float(np.median(np.abs(patch - median))), MAD_FLOOR)
                    predicted_median = np.median(prediction)
                    predicted_mad = max(
                        float(np.median(np.abs(prediction - predicted_median))),
                        MAD_FLOOR,
                    )
                    corrected = (prediction - predicted_median) / predicted_mad * mad + median
                    output[channel][region] += corrected * window
        restored = (output / weights)[:, :height, :width]
        if dark_ringing:
            small = data if min(height, width) < 100 else data[:, ::2, ::2]
            size = 15 if small is data else 7
            threshold = percentile_filter(small, 2, size=(1, size, size))
            if small is not data:
                threshold = zoom(
                    threshold, (1, height / small.shape[1], width / small.shape[2]),
                    order=1,
                )
            restored = np.where(restored < threshold, data, restored)
        if protect_background:
            for channel, plane in enumerate(data):
                sample = plane[::4, ::4]
                median = np.median(sample)
                threshold = median + 1.4826 * np.median(np.abs(sample - median))
                restored[channel] = np.where(plane < threshold, plane, restored[channel])
        blended = np.clip(data * (1 - strength) + restored * strength, 0, 1)
        if not np.isfinite(blended).all():
            raise ValueError('Résultat de restauration non fini')
        return blended.astype(np.float32)

    def process(self, source: Path, candidate: Path) -> JSONReport:
        """Écrit un FITS distinct en conservant en-têtes et unités uint16.

        ONNX Runtime est importé au calcul uniquement. La politique commune
        choisit CUDA si disponible, sinon CPU ; CoreML est exclu comme en amont.
        Les bibliothèques NVIDIA du venv sont préchargées avant la session CUDA
        si ONNX Runtime fournit preload_dlls, comme pour GraXpert.
        Une panne d'inférence est propagée sans reprise silencieuse.
        """
        args = self._get_args()
        if source.resolve() == candidate.resolve():
            raise ValueError('La source FITS doit être préservée')
        model = Path(args.aberration_model).expanduser().resolve()
        if not model.is_file():
            raise FileNotFoundError(f'Modèle ONNX absent : {model}')
        import onnxruntime as ort

        providers = ['CPUExecutionProvider']
        if (ComputeConfig.requested(args) == 'auto'
                and 'CUDAExecutionProvider' in ort.get_available_providers()):
            if hasattr(ort, 'preload_dlls'):
                # Rechercher CUDA/cuDNN dans les paquets NVIDIA du venv,
                # sans dépendre du chargement préalable d'un autre moteur.
                ort.preload_dlls(directory='')
            providers.insert(0, 'CUDAExecutionProvider')
        session = ort.InferenceSession(str(model), providers=providers)
        with fits.open(source, memmap=False) as hdul:
            original = hdul[0].data
            if original is None or original.ndim not in (2, 3):
                raise ValueError('Image FITS mono ou RGB requise')
            scale = 65535.0 if original.dtype == np.uint16 else 1.0
            if original.dtype.kind != 'f' and scale == 1:
                raise ValueError('FITS flottant ou uint16 requis')
            data = original.astype(np.float32) / scale
            mono = data.ndim == 2
            restored = self.restore(
                data[None] if mono else data, session, session.get_inputs()[0].name,
                getattr(args, 'aberration_strength', 1.0),
                getattr(args, 'aberration_dark_ringing', False),
                getattr(args, 'aberration_protect_background', True),
            )
            hdul[0].data = (restored[0] if mono else restored) * scale
            candidate.parent.mkdir(parents=True, exist_ok=True)
            hdul.writeto(candidate, overwrite=True)
        return dict(
            model_path=str(model), providers=session.get_providers(),
            strength=getattr(args, 'aberration_strength', 1.0),
            dark_ringing=getattr(args, 'aberration_dark_ringing', False),
            protect_background=getattr(args, 'aberration_protect_background', True),
        )


class AberrationRemoverProcessor(_CosmicClarityProcessor):
    """Soumet un candidat AberrationRemover aux contrôles de netteté communs."""

    engine_name = 'aberration_remover'
    engine_label = 'Aberration Remover'
    parameter_prefixes = ('quality_', 'deconvolution_', 'aberration_')

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie les paramètres et rapports du moteur d'aberrations."""
        return 'aberration'

    @classmethod
    def parameter_children(cls) -> tuple[type[ParameterizedComponent], ...]:
        """Lie les options du moteur à la configuration résolue."""
        return (AberrationEngine,)

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Expose les réglages ONNX sans charger le modèle."""
        AberrationEngine.add_arguments(parser)

    def _infer(self, source: Path, candidate: Path, args: argparse.Namespace) -> JSONReport:
        """Produit le candidat ; la classe parente contrôle étoiles et artefacts."""
        engine = AberrationEngine()
        engine.set_from_args(args)
        return engine.process(source, candidate)
