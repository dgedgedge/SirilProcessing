"""Moteur GraXpert ONNX natif, avec acquisition automatique et contrôles communs.

Les fichiers de travail sont directs, sans niveau 001. Dans le plan numéroté,
le dossier porte le nom du rapport sans suffixe _work ; les sources sont
protégées avant sa réinitialisation. Le dossier préparé par un orchestrateur
de chaînes est conservé, avec ses masques et son rapport.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from shutil import copyfile
import subprocess
import time

from astropy.io import fits

from lib.resource_manager import ResourceManager
from lib.logging_utils import log_duration
from lib.processor import processor
from lib.parameterized_component import ParameterizedComponent
from lib.siril import Siril
from lib.postprocess_paths import reset_restoration_work_dir
from lib.restoration_quality import QualityReference
from lib.deconvolution_quality import (
    artifact_metrics,
    assess_denoising,
    denoising_criteria,
)
from lib.graxpert_inference import GraXpertEngine, GPU_PROVIDERS
from lib.type_defs import JSONReport


class GraXpertDenoiseProcessor(processor):
    """Produit un candidat par ONNX puis applique les contrôles communs."""

    parameter_prefixes = ("quality_", "graxpert_", "deconvolution_", "denoise_")

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie ce moteur dans l’audit de débruitage."""
        return "graxpert"

    @classmethod
    def parameter_children(cls) -> tuple[type[ParameterizedComponent], ...]:
        """Déclare l’inférence et ses modèles pour la liaison hiérarchique."""
        return (GraXpertEngine,)

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare les options natives ; le binaire sert seulement au téléchargement."""
        if any(action.dest == "enable_graxpert" for action in parser._actions):
            return
        parser.add_argument(
            "--enable-graxpert",
            action=argparse.BooleanOptionalAction,
            default=None,
            help=(
                "Ajouter GraXpert ONNX à la comparaison "
                "(automatique si des poids locaux valides sont présents)"
            ),
        )
        GraXpertEngine.add_arguments(parser)

    @classmethod
    def is_enabled(cls, args: argparse.Namespace) -> bool:
        """Résout le choix explicite, sinon détecte des poids locaux vérifiés.

        None ou l’absence d’option signifie automatique. True autorise
        l’acquisition habituelle ; False désactive même en présence de poids.
        La détection ne télécharge pas et ne modifie pas les arguments, afin
        que la sauvegarde conserve ce choix plutôt qu’un état des caches.
        """
        explicit = getattr(args, "enable_graxpert", None)
        if explicit is not None:
            return bool(explicit)
        try:
            local_model = ResourceManager(args).find_graxpert()
        except (OSError, ValueError) as error:
            logging.warning("Activation automatique GraXpert impossible : %s", error)
            return False
        if local_model is not None:
            logging.info(
                "GraXpert activé automatiquement : poids locaux vérifiés dans %s",
                local_model,
            )
            return True
        logging.info("GraXpert inactif : aucun poids local valide")
        return False

    @staticmethod
    def availability(args: argparse.Namespace) -> JSONReport:
        """Vérifie ONNX et le périphérique sans acquérir ni charger les poids."""
        try:
            import onnxruntime as ort
        except ImportError:
            return dict(
                available=False,
                reason="ONNX_RUNTIME_ABSENT",
                detail="Installer avec bin/install_dependances.sh",
            )
        providers = ort.get_available_providers()
        requested = GraXpertEngine.requested_backend(args)
        required = ('CUDAExecutionProvider',) if requested == 'cuda' else GPU_PROVIDERS
        if (
            requested in ('cuda', 'gpu')
            and not any(provider in required for provider in providers)
            and not getattr(args, "graxpert_cpu_fallback", False)
        ):
            return dict(available=False, reason="GPU_UNAVAILABLE", providers=providers)
        return dict(
            available=True, onnxruntime_version=ort.__version__, providers=providers
        )

    def post_process(
        self,
        input_path: Path | str,
        output_path: Path | str,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Infère en Python, audite les durées et écarte les erreurs du moteur.

        timings distingue chargement/inférence et contrôle stellaire, qualité et
        publication en secondes murales ; les durées restent écrites en échec.
        """
        from lib.deconvolution import compare_star_catalogs

        args = self._get_args(args)
        source, report = Path(input_path).resolve(), Path(output_path).resolve()
        if source == report or source == report.with_suffix(".fits"):
            raise ValueError("Sorties distinctes de l’image requises")
        availability = self.availability(args)
        result = dict(
            engine="graxpert",
            status="unavailable",
            image_path=str(source),
            output_image=str(source),
            availability=availability,
            rejection_reasons=[],
        )
        report.parent.mkdir(parents=True, exist_ok=True)
        if not availability["available"]:
            report.write_text(json.dumps(result, indent=2), encoding="utf-8")
            return result
        work = reset_restoration_work_dir(
            report, source,
            prepared_directory=getattr(args, "_restoration_work_dir", None),
        )
        candidate = work / "candidate.fits"
        result.update(
            status="evaluating",
            candidate_image_path=str(candidate),
            log_path=str(work / "quality.log"),
        )
        start = time.monotonic()
        result["timings"] = {}
        try:
            with log_duration(
                "GraXpert — chargement et inférence",
                result["timings"], "inference_seconds",
            ):
                result["inference"] = GraXpertEngine(args).process(source, candidate)
            with log_duration(
                "GraXpert — contrôle stellaire, qualité et publication",
                result["timings"], "quality_seconds",
            ):
                result["cpu_fallback"] = result["inference"]["cpu_fallback"]
                QualityReference.validate_metadata(source, candidate)
                before, after = work / "before.tsv", work / "after.tsv"
                layer = getattr(args, "deconvolution_layer", None)
                layer = (
                    (1 if fits.getdata(source).ndim == 3 else 0) if layer is None else layer
                )
                from lib.quality_processors import QualityContext
                from lib.quality_star_processors import SirilStarDetectionProcessor

                context = QualityContext.for_catalogs(source, candidate)
                service = Siril(
                    siril_path=getattr(args, 'siril_path', None),
                    siril_mode=getattr(args, 'siril_mode', None),
                )
                detection = SirilStarDetectionProcessor(
                    context, [(source, before), (candidate, after)], work, layer,
                    runner=service,
                )
                result['star_detection'] = detection.post_process(candidate, None, args)
                comparison = compare_star_catalogs(
                    before,
                    after,
                    getattr(args, "deconvolution_match_radius", 1),
                    getattr(args, "deconvolution_min_stars", 10),
                    getattr(args, "deconvolution_min_match_fraction", 0.7),
                    layer,
                    reference_images=(source, candidate),
                    quality_mode=getattr(args, "quality_mode", "reliable"),
                )
                metrics = (
                    artifact_metrics(source, candidate, comparison["pairs"], args=args)
                    if comparison["status"] == "validated"
                    else {}
                )
                reasons = assess_denoising(
                    comparison, metrics, max_blur=getattr(args, "denoise_max_blur", 0.03)
                )
                result.update(
                    criteria=denoising_criteria(
                        comparison, metrics, getattr(args, "denoise_max_blur", 0.03)
                    ),
                    comparison=comparison,
                    artifacts=metrics,
                    rejection_reasons=reasons,
                    candidate_image_path=str(candidate),
                    status="rejected" if reasons else "accepted",
                )
                if not reasons:
                    destination = report.with_suffix(".fits")
                    copyfile(candidate, destination)
                    result["output_image"] = str(destination)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            result.update(status="failed", error=str(error))
        finally:
            result["duration_seconds"] = time.monotonic() - start
            report.write_text(
                json.dumps(result, indent=2, allow_nan=False), encoding="utf-8"
            )
        return result
