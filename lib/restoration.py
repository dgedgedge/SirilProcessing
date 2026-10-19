"""Comparaison séquentielle de moteurs indépendants sur une référence commune."""

from __future__ import annotations

import argparse
from copy import copy
import json
import logging
from pathlib import Path
from shutil import copyfile
import time
from typing import ClassVar, Literal

from lib.logging_utils import log_section
from lib.processor import processor
from lib.parameterized_component import ParameterizedComponent
from lib.restoration_quality import QualityReference, RestorationQuality
from lib.type_defs import JSONReport
from lib.restoration_summary import RestorationSummary


class RestorationComparisonProcessor(processor):
    """Accepte les candidats avant classement ; conserve l’identité si nécessaire.

    Les erreurs d’un moteur sont auditées, puis les autres moteurs sont essayés.
    Le mode legacy conserve le contrat historique des métriques et des erreurs.
    """

    parameter_prefixes: ClassVar[tuple[str, ...]] = (
        "cosmic_",
        "deconvolution_",
        "denoise_",
        "quality_",
        "graxpert_",
        "restoration_",
    )
    parameter_dependencies = frozenset({"enable_clarity", "enable_graxpert"})
    operation: ClassVar[Literal["sharpen", "denoise"]] = "denoise"
    siril_processor: ClassVar[type[processor]]

    @classmethod
    def cosmic_processor(cls) -> type[processor]:
        """Importe la classe du moteur neuronal sans charger ses poids."""
        from lib.cosmic_clarity.processors import (
            CosmicClaritySharpenProcessor,
            CosmicClarityDenoiseProcessor,
        )

        return (
            CosmicClaritySharpenProcessor
            if cls.operation == "sharpen"
            else CosmicClarityDenoiseProcessor
        )

    @classmethod
    def parameter_children(cls) -> tuple[type[ParameterizedComponent], ...]:
        """Recense moteurs et service qualité sans instanciation anticipée."""
        from lib.graxpert import GraXpertDenoiseProcessor

        children = (cls.siril_processor, cls.cosmic_processor(), RestorationQuality)
        return children + (
            (GraXpertDenoiseProcessor,) if cls.operation == "denoise" else ()
        )

    @classmethod
    def add_shared_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare une seule fois services, moteurs optionnels et modes de chaînes."""
        from lib.compute_config import ComputeConfig

        ComputeConfig.add_arguments(parser)
        if not any(action.dest == "enable_clarity" for action in parser._actions):
            from lib.cosmic_clarity.processors import add_arguments

            add_arguments(parser)
        RestorationQuality.add_arguments(parser)
        from lib.graxpert import GraXpertDenoiseProcessor

        GraXpertDenoiseProcessor.add_arguments(parser)
        if not any(
            action.dest == "restoration_compare_chains" for action in parser._actions
        ):
            parser.add_argument(
                "--restoration-compare-chains",
                action=argparse.BooleanOptionalAction,
                default=True,
                help="Comparer les chaînes complètes (par défaut) ; --no-restoration-compare-chains compare les étapes séparément",
            )
            parser.add_argument(
                "--restoration-reverse-order",
                action=argparse.BooleanOptionalAction,
                default=False,
                help="Inclure aussi netteté puis débruitage",
            )
            parser.add_argument(
                "--restoration-siril",
                action=argparse.BooleanOptionalAction,
                default=True,
            )

    @classmethod
    def engines(cls, args: argparse.Namespace) -> list[tuple[str, type[processor]]]:
        """Recense les moteurs, dont GraXpert automatique si ses poids sont locaux."""
        engines = []
        if getattr(args, "enable_clarity", True):
            engines.append(("cosmic_clarity", cls.cosmic_processor()))
        if getattr(args, "restoration_siril", True):
            engines.append(("siril", cls.siril_processor))
        if cls.operation == "denoise":
            from lib.graxpert import GraXpertDenoiseProcessor

            if GraXpertDenoiseProcessor.is_enabled(args):
                engines.append(("graxpert", GraXpertDenoiseProcessor))
        return engines

    @staticmethod
    def score(report: JSONReport, operation: str) -> float:
        """Score sur candidats acceptés ; aucune récompense pour un bruit inconnu.

        Avec diagnostics v2, le gain de bruit récompensé est sa borne inférieure
        bootstrap, jamais le seul résidu haute fréquence historique.
        """
        comparison, artifacts = report["comparison"], report["artifacts"]
        mean_ratio = (
            comparison["after_fwhm_px"]["mean"] / comparison["before_fwhm_px"]["mean"]
        )
        median_ratio = comparison["paired_ratio"]["median"]
        noise = artifacts["max_noise_ratio"]
        rings = artifacts["max_ring_fraction"]
        if operation == "sharpen":
            noise_penalty = 0.2 * max(0, noise - 1) if noise is not None else 0
            return float(
                2 * (1 - mean_ratio)
                + 2 * (1 - median_ratio)
                - noise_penalty
                - 0.2 * rings
            )
        gain = artifacts.get("noise", {}).get("proven_gain")
        gain = (1 - noise) if "noise" not in artifacts and noise is not None else gain
        return (
            float(max(0, gain) if gain is not None else 0)
            - 0.5 * max(0, max(mean_ratio, median_ratio) - 1)
            - 0.2 * rings
        )

    def post_process(
        self,
        input_path: Path | str,
        output_path: Path | str,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Évalue tous les enfants séquentiellement et publie le meilleur accepté.

        Des titres INFO entre lignes de tirets annoncent la préparation commune,
        chaque moteur réellement évalué et le classement, y compris en legacy.
        """
        args = copy(self._get_args(args))
        legacy = getattr(args, "quality_mode", "reliable") == "legacy"
        source, report = Path(input_path).resolve(), Path(output_path).resolve()
        destination = Path(
            (
                getattr(args, "deconvolution_output", None)
                if self.operation == "sharpen"
                else None
            )
            or report.with_suffix(".fits")
        ).resolve()
        if source == report or destination in (source, report):
            raise ValueError(
                "Les sorties doivent être différentes de la source et du rapport"
            )
        if destination.suffix.lower() not in (".fit", ".fits", ".fts"):
            raise ValueError("Une sortie FITS est requise")
        engines = self.engines(args)
        treatment = "Déconvolution" if self.operation == "sharpen" else "Débruitage"
        if (
            legacy
            and not getattr(args, "enable_clarity", True)
            and not any(name == "graxpert" for name, _ in engines)
        ):
            log_section(f"{treatment} — Évaluation du moteur Siril", separator="-")
            result = self.siril_processor().post_process(source, report, args)
            log_section(f"{treatment} — Publication du résultat", separator="-")
            RestorationSummary.write(
                result,
                report,
                mode="isolated",
                operation=self.operation,
                legacy=True,
                args=args,
            )
            report.write_text(json.dumps(result, indent=2), encoding="utf-8")
            return result
        report.parent.mkdir(parents=True, exist_ok=True)
        result = dict(
            image_path=str(source),
            engine="comparison",
            status="evaluating",
            evaluations={},
        )
        if not legacy:
            log_section(
                f"{treatment} — Préparation de la référence et des masques", separator="-",
            )
            reference = getattr(args, "_quality_reference", None)
            if reference is None or reference.path != source:
                reference = QualityReference(source, args)
            args._quality_reference = reference
            result["quality_preview_path"] = str(
                reference.save_masks(report.with_name(report.stem + "_masks.fits"))
            )
            result["quality_reference"] = reference.audit()
        candidates = []
        for name, engine in engines:
            label = {
                "siril": "Siril",
                "cosmic_clarity": "Cosmic Clarity",
                "graxpert": "GraXpert",
            }[name]
            log_section(f"{treatment} — Évaluation du moteur {label}", separator="-")
            child_path = report.with_name(report.stem + "_" + name + ".json")
            start = time.monotonic()
            try:
                evaluation = engine().post_process(source, child_path, args)
                if evaluation.get("status") == "accepted":
                    score = self.score(evaluation, self.operation)
                    candidates.append((score, name, evaluation))
                    evaluation["selection_score"] = score
                    logging.info("%s — score %s : %.6f", treatment, label, score)
            except (OSError, ValueError, RuntimeError, ImportError) as error:
                evaluation = {}
                if child_path.is_file():
                    try:
                        evaluation = json.loads(child_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        pass
                no_gain = "Aucune déconvolution efficace" in str(error)
                evaluation.update(
                    status="no_safe_improvement" if no_gain else "failed",
                    error=str(error),
                    output_image=str(source),
                )
                if legacy and "Aucune déconvolution efficace" not in str(error):
                    result.update(status="failed", error=str(error))
                    result["evaluations"][name] = evaluation
                    RestorationSummary.write(
                        result,
                        report,
                        mode="isolated",
                        operation=self.operation,
                        legacy=legacy,
                        args=args,
                    )
                    report.write_text(json.dumps(result, indent=2), encoding="utf-8")
                    raise
            evaluation["duration_seconds"] = time.monotonic() - start
            result["evaluations"][name] = evaluation
            logging.info(
                "%s — évaluation %s : statut=%s ; raisons=%s",
                treatment,
                label,
                evaluation["status"],
                evaluation.get("rejection_reasons")
                or evaluation.get("error")
                or "aucune",
            )
            if evaluation.get("comparison") and evaluation.get("artifacts"):
                comparison, artifacts = (
                    evaluation["comparison"],
                    evaluation["artifacts"],
                )
                if "paired_ratio" in comparison:
                    logging.info(
                        "%s — %s : gain médian apparié %+.4f %% ; variation maximale du bruit=%s",
                        treatment,
                        label,
                        100 * (1 - comparison["paired_ratio"]["median"]),
                        (
                            artifacts.get("max_noise_ratio")
                            if artifacts.get("max_noise_ratio") is not None
                            else "UNKNOWN"
                        ),
                    )
            report.write_text(json.dumps(result, indent=2), encoding="utf-8")
        log_section(
            f"{treatment} — Classement et publication du résultat", separator="-",
        )
        # Identité : score zéro ; un candidat sans bénéfice démontré ne la bat pas.
        candidates = [
            candidate for candidate in candidates if legacy or candidate[0] > 0
        ]
        if candidates:
            score, name, selected = max(candidates, key=lambda item: item[0])
            path = Path(selected["output_image"]).resolve()
            if not path.is_file():
                raise FileNotFoundError(f"Sortie sélectionnée introuvable : {path}")
            copyfile(path, destination)
            result.update(
                {
                    key: value
                    for key, value in selected.items()
                    if key not in ("engine", "status", "output_image", "evaluations")
                }
            )
            result.update(
                status="accepted",
                output_image=str(destination),
                selected_engine=name,
                selection_score=score,
                comparison=selected.get("comparison"),
                artifacts=selected.get("artifacts"),
                rejection_reasons=[],
            )
            if "quality" in selected:
                result["quality"] = selected["quality"]
        else:
            result.update(
                status=(
                    "no_safe_improvement" if self.operation == "sharpen" else "rejected"
                ),
                output_image=str(source),
                selected_engine=None,
                rejection_reasons=sorted(
                    {
                        reason
                        for entry in result["evaluations"].values()
                        for reason in entry.get("rejection_reasons", [])
                    }
                ),
            )
        labels = {
            "siril": "Siril",
            "cosmic_clarity": "Cosmic Clarity",
            "graxpert": "GraXpert",
        }
        logging.info(
            "%s — choix final : %s ; score=%s ; %s",
            "Déconvolution" if self.operation == "sharpen" else "Débruitage",
            labels.get(result["selected_engine"], "image d’entrée conservée"),
            result.get("selection_score"),
            (
                f"meilleur score parmi {len(candidates)} candidat(s) accepté(s)"
                if candidates
                else "aucun candidat accepté"
            ),
        )
        RestorationSummary.write(
            result,
            report,
            mode="isolated",
            operation=self.operation,
            legacy=legacy,
            args=args,
        )
        report.write_text(
            json.dumps(result, indent=2, allow_nan=False), encoding="utf-8"
        )
        return result
