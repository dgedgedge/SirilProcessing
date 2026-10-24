"""Inférence ONNX GraXpert Denoising, indépendante de Siril et du CLI GraXpert.

Adaptation des conventions numériques de GraXpert-AI.py, Adrian Knagg-Baugh,
GPL-3.0-or-later : médiane/MAD sous-échantillonnées, NHWC, fenêtres 256/128,
recopie centrale et mélange d’intensité. Poids GraXpert : CC-BY-NC-SA-4.0.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import time
from typing import TYPE_CHECKING

import numpy as np
from astropy.io import fits

from lib.compute_config import ComputeConfig
from lib.cpu_config import CpuConfig
from lib.graxpert_models import GraXpertModels
from lib.parameterized_component import ParameterizedComponent
from lib.type_defs import JSONReport

if TYPE_CHECKING:
    import onnxruntime as ort

WINDOW_SIZE = 256
STRIDE = 128
BORDER = (WINDOW_SIZE - STRIDE) // 2
MODEL_MAD = 0.04
MODEL_THRESHOLD = 10.0
GPU_PROVIDERS = (
    "CUDAExecutionProvider",
    "ROCMExecutionProvider",
    "DmlExecutionProvider",
    "CoreMLExecutionProvider",
)


class GraXpertEngine(ParameterizedComponent):
    """Produit un candidat natif avec périphérique effectif et poids audités."""

    parameter_prefixes = ("graxpert_",)

    def __init__(self, args: argparse.Namespace | None = None) -> None:
        """Capture politique commune et surcharges sans importer ONNX.

        En auto, le choix GPU/CPU attend le démarrage de la session. Une
        surcharge graxpert_gpu non nulle garde son comportement historique.
        """
        self.args = self._get_args(args)
        self.models = GraXpertModels(self.args)
        self.cpu_fallback = False
        self.session: ort.InferenceSession | None = None
        self.backend = self.requested_backend(self.args)
        self.gpu = None if self.backend == 'auto' else self.backend != 'cpu'
        self.allow_cpu = getattr(self.args, "graxpert_cpu_fallback", False)
        self.batch_size = getattr(self.args, "graxpert_batch_size", 1)
        self.strength = getattr(self.args, "graxpert_strength", 0.5)
        if (
            not 1 <= self.batch_size <= 32
            or not np.isfinite(self.strength)
            or not 0 <= self.strength <= 1
        ):
            raise ValueError("Strength entre 0 et 1, batch_size entre 1 et 32 requis")

    @classmethod
    def parameter_children(cls) -> tuple[type[ParameterizedComponent], ...]:
        """Déclare le gestionnaire de poids configuré avant l’inférence."""
        return (GraXpertModels,)

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Expose modèles, politique commune et surcharges historiques persistantes."""
        ComputeConfig.add_arguments(parser)
        GraXpertModels.add_arguments(parser)
        if any(action.dest == "graxpert_strength" for action in parser._actions):
            return
        parser.add_argument("--graxpert-strength", type=float, default=0.5)
        parser.add_argument("--graxpert-batch-size", type=int, default=1)
        parser.add_argument(
            "--graxpert-gpu", action=argparse.BooleanOptionalAction, default=None,
            help="Surcharge GPU/CPU GraXpert ; sinon --compute-backend"
        )
        parser.add_argument(
            "--graxpert-cpu-fallback",
            action=argparse.BooleanOptionalAction,
            default=False,
        )

    @staticmethod
    def requested_backend(args: argparse.Namespace) -> str:
        """Conserve les surcharges booléennes historiques, sinon la politique commune."""
        explicit = getattr(args, 'graxpert_gpu', None)
        if explicit is not None:
            return 'gpu' if explicit else 'cpu'
        return ComputeConfig.requested(args)

    def _create_session(self, model: Path, gpu: bool) -> ort.InferenceSession:
        """Précharge CUDA depuis les paquets NVIDIA du venv puis crée la session.

        Le GPU exigé ne permet pas de repli CPU implicite. Les versions ONNX
        antérieures sans API preload_dlls conservent leur chargement habituel.
        """
        import onnxruntime as ort

        ort.disable_telemetry_events()
        if gpu and hasattr(ort, 'preload_dlls'):
            # Les installations CUDA du venv fournissent les DLL/.so NVIDIA ;
            # les charger avant la création de session, sans modifier le système.
            ort.preload_dlls(directory='')
        options = ort.SessionOptions()
        options.intra_op_num_threads = CpuConfig.get_limit()
        options.inter_op_num_threads = 1
        available = ort.get_available_providers()
        if gpu:
            providers = [
                provider for provider in GPU_PROVIDERS if provider in available
                and (self.backend != 'cuda' or provider == 'CUDAExecutionProvider')
            ]
            if not providers:
                raise RuntimeError("Aucun fournisseur ONNX GPU disponible")
            options.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
        else:
            providers = ["CPUExecutionProvider"]
        session = ort.InferenceSession(
            str(model), sess_options=options, providers=providers
        )
        session.disable_fallback()
        if gpu and not any(
            provider in GPU_PROVIDERS for provider in session.get_providers()
        ):
            raise RuntimeError("ONNX a sélectionné CPU malgré la demande GPU")
        inputs = session.get_inputs()
        if (
            len(inputs) != 1
            or inputs[0].name != "gen_input_image"
            or inputs[0].type != "tensor(float)"
            or len(inputs[0].shape) != 4
            or inputs[0].shape[1:] != [WINDOW_SIZE, WINDOW_SIZE, 3]
        ):
            raise ValueError("Contrat ONNX GraXpert NHWC 256×256×3 incompatible")
        return session

    def _initialize_session(self, model: Path) -> ort.InferenceSession:
        """Résout auto au démarrage ; ne reprend jamais une inférence déjà commencée.

        Un fournisseur annoncé par ONNX peut manquer de pilote au chargement.
        Seul auto autorise alors une session CPU avant le premier calcul.
        Les erreurs de contrat/modèle ValueError restent fatales.
        """
        import onnxruntime as ort

        if self.backend != 'auto' or not self.gpu:
            return self._create_session(model, bool(self.gpu))
        try:
            return self._create_session(model, True)
        except (RuntimeError, ort.capi.onnxruntime_pybind11_state.Fail,
                ort.capi.onnxruntime_pybind11_state.RuntimeException) as error:
            logging.info('GraXpert : accélérateur indisponible au démarrage, CPU choisi : %s', error)
            self.gpu = False
            return self._create_session(model, False)

    @staticmethod
    def _append_tail(image: np.ndarray, count: int, axis: int) -> np.ndarray:
        """Recopie la fin, comme Siril ; répète si une petite image est trop courte."""
        size = image.shape[axis]
        start = max(0, size - count)
        indices = start + np.arange(count) % (size - start)
        return np.concatenate((image, np.take(image, indices, axis=axis)), axis=axis)

    def _denoise(self, image: np.ndarray) -> np.ndarray:
        """Traite un tableau NHWC RGB et restitue sa géométrie exacte.

        Le MAD nul est refusé plutôt qu’une normalisation instable. Les mêmes
        médianes/MAD sont utilisées pour toutes les tuiles d’un canal. Les pixels
        normalisés au-dessus du seuil du modèle restent inchangés, comme Siril.
        """
        height, width, _ = image.shape
        sampled = image[::4, ::4]
        median = np.median(sampled, axis=(0, 1))
        mad = np.median(abs(sampled - median), axis=(0, 1))
        if np.any(mad <= np.finfo(np.float32).eps):
            raise ValueError("MAD nul ou insuffisant pour normaliser GraXpert")
        rows, columns = height // STRIDE + 1, width // STRIDE + 1
        padded = self._append_tail(image, rows * STRIDE - height, axis=0)
        padded = self._append_tail(padded, columns * STRIDE - width, axis=1)
        padded = self._append_tail(padded, BORDER, axis=0)
        padded = np.concatenate((padded[:BORDER], padded), axis=0)
        padded = self._append_tail(padded, BORDER, axis=1)
        padded = np.concatenate((padded[:, :BORDER], padded), axis=1)
        output = padded.copy()
        positions = [
            (row * STRIDE, column * STRIDE)
            for column in range(columns)
            for row in range(rows)
        ]
        logging.info(
            "GraXpert ONNX : %d tuiles, lot=%d, fournisseurs=%s",
            len(positions),
            self.batch_size,
            self.session.get_providers(),
        )
        last_progress = -1
        for start in range(0, len(positions), self.batch_size):
            progress = int(100 * start / len(positions)) // 10
            if progress > last_progress:
                logging.info("GraXpert ONNX : %d %%", progress * 10)
                last_progress = progress
            batch_positions = positions[start : start + self.batch_size]
            tiles = np.stack(
                [
                    padded[y : y + WINDOW_SIZE, x : x + WINDOW_SIZE]
                    for y, x in batch_positions
                ]
            )
            normalized = np.asarray(
                (tiles - median) / mad * MODEL_MAD, dtype=np.float32
            )
            predictions = self.session.run(None, {"gen_input_image": normalized})[0]
            if (
                predictions.shape != normalized.shape
                or not np.isfinite(predictions).all()
            ):
                raise ValueError("Sortie ONNX GraXpert invalide ou non finie")
            predictions = np.where(
                normalized < MODEL_THRESHOLD, predictions, normalized
            )
            restored = predictions / MODEL_MAD * mad + median
            for (y, x), tile in zip(batch_positions, restored):
                output[
                    y + BORDER : y + BORDER + STRIDE, x + BORDER : x + BORDER + STRIDE
                ] = tile[BORDER : BORDER + STRIDE, BORDER : BORDER + STRIDE]
        return output[BORDER : BORDER + height, BORDER : BORDER + width]

    def process(self, source: Path | str, destination: Path | str) -> JSONReport:
        """Charge les poids à la demande, infère et préserve FITS, WCS et unités.

        Le repli CPU, s’il est autorisé, recommence l’image entière. Les erreurs
        de format/contrat ne déclenchent pas un repli de périphérique. Un fichier
        candidat est écrit uniquement après une inférence valide.
        """
        import onnxruntime as ort

        source, destination = Path(source).resolve(), Path(destination).resolve()
        if source == destination:
            raise ValueError("Le candidat GraXpert doit être distinct de l’original")
        with fits.open(source, memmap=False) as hdul:
            original = hdul[0].data
            if (
                original is None
                or original.ndim not in (2, 3)
                or (original.ndim == 3 and original.shape[0] != 3)
            ):
                raise ValueError("FITS mono ou RGB requis")
            if original.ndim == 2 and hdul[0].header.get("BAYERPAT", "").strip():
                raise ValueError("Dématricer le FITS CFA avant GraXpert")
            if original.dtype.kind not in "fu" or (
                original.dtype.kind == "u" and original.dtype.itemsize != 2
            ):
                raise ValueError("FITS flottant ou uint16 requis")
            if not np.isfinite(original).all() or min(original.shape[-2:]) < 2:
                raise ValueError("Image non finie ou trop petite")
            scale = 65535.0 if original.dtype.kind == "u" else 1.0
            pixels = np.asarray(original, dtype=np.float32) / scale
            image = (
                pixels[:, :, None] if pixels.ndim == 2 else np.moveaxis(pixels, 0, -1)
            )
            mono = image.shape[-1] == 1
            if mono:
                image = np.repeat(image, 3, axis=-1)
            if self.gpu is None:
                self.gpu = any(provider in GPU_PROVIDERS for provider in ort.get_available_providers())
                logging.info("GraXpert : mode auto, périphérique choisi avant calcul : %s",
                             'GPU' if self.gpu else 'CPU')
            model = self.models.ensure_model()
            start = time.monotonic()
            try:
                self.session = self._initialize_session(model)
                denoised = self._denoise(image)
            except (
                RuntimeError,
                ort.capi.onnxruntime_pybind11_state.Fail,
                ort.capi.onnxruntime_pybind11_state.RuntimeException,
            ) as error:
                if not self.gpu or not self.allow_cpu:
                    raise RuntimeError(
                        f"Inférence ONNX impossible : {error}"
                    ) from error
                logging.warning(
                    "GraXpert ONNX : échec GPU, repli CPU autorisé : %s", error
                )
                self.cpu_fallback = True
                self.session = self._create_session(model, False)
                denoised = self._denoise(image)
            if mono:
                denoised = denoised[:, :, 0]
            else:
                denoised = np.moveaxis(denoised, -1, 0)
            if original.dtype.kind == "u":
                denoised = np.clip(denoised * scale, 0, scale).astype(original.dtype)
            else:
                denoised = denoised.astype(original.dtype)
            blended = self.strength * denoised + (1 - self.strength) * original
            hdul[0].data = blended.astype(original.dtype)
            hdul[0].header.add_history(
                f"GraXpert ONNX denoise {self.models.version}; strength={self.strength}"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            hdul.writeto(destination, overwrite=True)
        return dict(
            engine="onnxruntime",
            onnxruntime_version=ort.__version__,
            model_version=self.models.version,
            model_path=str(model),
            model_sha256=self.models.entry["sha256"],
            checksum_provenance=self.models.entry["provenance"],
            providers=self.session.get_providers(),
            cpu_fallback=self.cpu_fallback,
            requested_backend=self.backend,
            duration_seconds=time.monotonic() - start,
            strength=self.strength,
            batch_size=self.batch_size,
            window_size=WINDOW_SIZE,
            stride=STRIDE,
            output_image=str(destination),
        )
