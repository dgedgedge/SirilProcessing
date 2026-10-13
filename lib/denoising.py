"""Débruitage Siril conservateur, avec comparaison sur les mêmes étoiles."""

from __future__ import annotations

import argparse
import json
import logging
from copy import deepcopy
from pathlib import Path
from shutil import copyfile

import numpy as np
from astropy.io import fits

from lib.deconvolution import _quote, compare_star_catalogs
from lib.deconvolution_quality import artifact_metrics, assess_denoising
from lib.postprocess_paths import reset_ordered_work_dir
from lib.siril_utils import Siril
from lib.processor import processor
from lib.restoration import RestorationComparisonProcessor
from lib.type_defs import JSONReport


class _DenoisingExecution:
    """État indépendant d’une exécution Siril, sans configuration globale mutable."""

    def __init__(
        self, input_path: Path | str, output_path: Path | str,
        args: argparse.Namespace | None,
    ) -> None:
        """Valide les options et initialise les chemins et l’audit de cet appel.

        Parameters
        ----------
        input_path : Path or str
            Image FITS source, conservée intacte.
        output_path : Path or str
            Rapport JSON de cet appel.
        args : argparse.Namespace or None
            Valeurs déjà résolues par le processeur appelant.
        """
        self.args = deepcopy(args)
        self.modulation = getattr(self.args, "denoise_modulation", 0.5)
        self.max_blur = getattr(self.args, "denoise_max_blur", 0.03)
        if (
            not np.isfinite([self.modulation, self.max_blur]).all()
            or not 0 <= self.modulation <= 1
            or not 0 <= self.max_blur <= 1
        ):
            raise ValueError("Modulation et tolérance de flou doivent être entre 0 et 1")
        self.source, self.report = Path(input_path).resolve(), Path(output_path).resolve()
        if self.source == self.report:
            raise ValueError("Le rapport ne peut pas remplacer l’image")
        with fits.open(self.source, memmap=False) as hdul:
            self.shape = hdul[0].shape
            if len(self.shape) not in (2, 3) or (len(self.shape) == 3 and self.shape[0] != 3):
                raise ValueError("Image mono ou RGB requise")
            if len(self.shape) == 2 and hdul[0].header.get("BAYERPAT", "").strip():
                raise ValueError("Dématricer le FITS CFA avant réduction du bruit")
        self.layer = 1 if len(self.shape) == 3 else 0
        self.report.parent.mkdir(parents=True, exist_ok=True)
        self.work = reset_ordered_work_dir(self.report)
        self.candidate = self.work / "candidate.fits"
        self.before, self.after = self.work / "before.tsv", self.work / "after.tsv"
        self.result: JSONReport = dict(
            image_path=str(self.source),
            status="evaluating",
            modulation=self.modulation,
            cosmetic=True,
            secondary_algorithm="Anscombe VST",
            precision="float32",
            candidate_image_path=str(self.candidate),
            max_blur=self.max_blur,
            script_path=str(self.work / "denoise.sps"),
            log_path=str(self.work / "denoise.log"),
        )

    def _save_report(self) -> None:
        """Persiste l’état courant de l’audit, y compris les motifs de rejet."""
        self.report.write_text(
            json.dumps(self.result, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _build_script(self) -> str:
        """Construit les commandes Siril de traitement et de catalogues."""
        return "\n".join(
            [
                "requires 1.4",
                f"cd {_quote(self.work)}",
                "setext fit",
                "set32bits",
                f"load {_quote(self.source)}",
                "setfindstar reset",
                "setfindstar -gaussian",
                f"findstar -layer={self.layer} -out=before.tsv",
                f"denoise -vst -mod={self.modulation:g}",
                "save candidate.fits",
                "close",
                "load candidate.fits",
                "setfindstar reset",
                "setfindstar -gaussian",
                f"findstar -layer={self.layer} -out=after.tsv",
                "close",
                "",
            ]
        )

    def _run_siril(self) -> None:
        """Exécute le script du candidat ; un échec technique est propagé."""
        if not Siril(
            siril_path=getattr(self.args, "siril_path", None),
            siril_mode=getattr(self.args, "siril_mode", None),
        ).run_siril_script(
            self._build_script(), str(self.work), script_name="denoise.sps"
        ):
            raise RuntimeError(f"Échec Siril : {self.work / 'denoise.log'}")

    def _evaluate_candidate(self) -> list[str]:
        """Mesure les mêmes étoiles et retourne les motifs de rejet."""
        comparison = compare_star_catalogs(
            self.before, self.after, 1.0, 10, 0.7, self.layer,
            reference_images=(self.source, self.candidate)
            if getattr(self.args, 'deconvolution_reference_measurements', True) else None,
        )
        self.result["comparison"] = comparison
        reasons = []
        if comparison["status"] != "validated":
            reasons.append("insufficient_matches")
        else:
            metrics = artifact_metrics(self.source, self.candidate, comparison["pairs"])
            self.result["artifacts"] = metrics
            mean_ratio = (
                comparison["after_fwhm_px"]["mean"]
                / comparison["before_fwhm_px"]["mean"]
            )
            median_ratio = comparison["paired_ratio"]["median"]
            reasons = assess_denoising(comparison, metrics, max_blur=self.max_blur)
            logging.info(
                "Réduction du bruit : %d étoiles appariées ; gain de bruit minimal %+.2f %% ; "
                "variation FWHM moyenne %+.2f %% ; variation médiane appariée %+.2f %%",
                comparison["matched_count"],
                100 * (1 - metrics["max_noise_ratio"]),
                100 * (mean_ratio - 1),
                100 * (median_ratio - 1),
            )
        self.result["rejection_reasons"] = reasons
        return reasons

    def _publish_candidate(self, reasons: list[str]) -> None:
        """Publie le candidat accepté ou conserve l’image précédente."""
        if reasons:
            self.result.update(status="rejected", output_image=str(self.source))
            logging.info(
                "Réduction du bruit annulée : %s ; image précédente conservée",
                ", ".join(reasons),
            )
        else:
            destination = self.report.with_suffix(".fits")
            if destination == self.source:
                raise ValueError("La sortie ne peut pas remplacer l’image source")
            copyfile(self.candidate, destination)
            self.result.update(status="accepted", output_image=str(destination))
            logging.info("Réduction du bruit acceptée : %s", destination)

    def execute(self) -> JSONReport:
        """Exécute les phases de calcul, évaluation et publication avec audit d’erreur.

        Returns
        -------
        dict
            Statut, mesures et image retenue pour cet appel indépendant.
        """
        self._save_report()
        try:
            self._run_siril()
            reasons = self._evaluate_candidate()
            self._publish_candidate(reasons)
            self._save_report()
            return self.result
        except Exception as error:
            self.result.update(status="failed", error=str(error))
            self._save_report()
            raise


class SirilNoiseReductionProcessor(processor):
    """Exécute uniquement le moteur Siril et ses contrôles de qualité."""

    @classmethod
    def get_prefix(cls) -> str:
        """Retourne l’identifiant commun de l’étape."""
        return 'denoise'

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Expose les paramètres locaux de l’algorithme Siril."""
        parser.add_argument("--denoise-modulation", type=float, default=0.5)
        parser.add_argument(
            "--denoise-max-blur",
            type=float,
            default=0.03,
            help="Augmentation relative maximale de FWHM (défaut 3 %%)",
        )

    def post_process(
        self, input_path: Path | str, output_path: Path | str,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Exécute le traitement Siril avec les paramètres de cette instance."""
        return type(self)._execute(input_path, output_path, self._get_args(args))


    @staticmethod
    def _execute(
        input_path: Path | str, output_path: Path | str,
        args: argparse.Namespace | None,
    ) -> JSONReport:
        """Crée l’état de cet appel sans réutiliser les essais d’une exécution précédente."""
        return _DenoisingExecution(input_path, output_path, args).execute()


class NoiseReductionProcessor(RestorationComparisonProcessor):
    """Orchestre les candidats Siril et Cosmic Clarity et conserve le meilleur accepté."""

    operation = 'denoise'
    siril_processor = SirilNoiseReductionProcessor

    @classmethod
    def get_prefix(cls) -> str:
        """Retourne l’identifiant stable des options et rapports."""
        return 'denoise'

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare les paramètres communs de l’étape."""
        cls.add_shared_arguments(parser)
        cls.siril_processor.add_arguments(parser)
