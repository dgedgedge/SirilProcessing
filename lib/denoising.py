"""Débruitage Siril conservateur, avec comparaison sur les mêmes étoiles."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from shutil import copyfile
from tempfile import mkdtemp

import numpy as np
from astropy.io import fits

from lib.deconvolution import _quote, compare_star_catalogs
from lib.deconvolution_quality import artifact_metrics
from lib.siril_utils import create_siril_from_args
from lib.type_defs import JSONReport


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Déclare la modulation du débruitage et la tolérance de flou relatif."""
    parser.add_argument("--denoise-modulation", type=float, default=0.5)
    parser.add_argument(
        "--denoise-max-blur",
        type=float,
        default=0.03,
        help="Augmentation relative maximale de FWHM (défaut 3 %%)",
    )


def post_process(
    input_path: Path | str,
    output_path: Path | str,
    args: argparse.Namespace | None = None,
) -> JSONReport:
    """Débruite un FITS mono/RGB puis contrôle les mêmes étoiles et pixels de ciel.

    Écrit les diagnostics JSON et conserve les essais. Si les critères échouent,
    output_image désigne l’original ; un échec d’exécution Siril est propagé.
    Les paramètres et seuils sont décrits dans docs/scripts/postProcess/README.md."""
    modulation = getattr(args, "denoise_modulation", 0.5)
    max_blur = getattr(args, "denoise_max_blur", 0.03)
    if (
        not np.isfinite([modulation, max_blur]).all()
        or not 0 <= modulation <= 1
        or not 0 <= max_blur <= 1
    ):
        raise ValueError("Modulation et tolérance de flou doivent être entre 0 et 1")
    source, report = Path(input_path).resolve(), Path(output_path).resolve()
    if source == report:
        raise ValueError("Le rapport ne peut pas remplacer l’image")
    with fits.open(source, memmap=False) as hdul:
        shape = hdul[0].shape
        if len(shape) not in (2, 3) or (len(shape) == 3 and shape[0] != 3):
            raise ValueError("Image mono ou RGB requise")
        if len(shape) == 2 and hdul[0].header.get("BAYERPAT", "").strip():
            raise ValueError("Dématricer le FITS CFA avant réduction du bruit")
    layer = 1 if len(shape) == 3 else 0
    report.parent.mkdir(parents=True, exist_ok=True)
    work = Path(mkdtemp(prefix=report.stem + "_", dir=report.parent))
    candidate = work / "candidate.fit"
    before, after = work / "before.tsv", work / "after.tsv"
    result = dict(
        image_path=str(source),
        status="evaluating",
        modulation=modulation,
        cosmetic=True,
        secondary_algorithm="Anscombe VST",
        precision="float32",
        candidate_image_path=str(candidate),
        max_blur=max_blur,
        script_path=str(work / "denoise.sps"),
        log_path=str(work / "denoise.log"),
    )

    def save() -> None:
        """Persiste l’état courant de l’audit, y compris les motifs de rejet."""
        report.write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    save()
    try:
        script = "\n".join(
            [
                "requires 1.4",
                f"cd {_quote(work)}",
                "setext fit",
                "set32bits",
                f"load {_quote(source)}",
                "setfindstar reset",
                "setfindstar -gaussian",
                f"findstar -layer={layer} -out=before.tsv",
                f"denoise -vst -mod={modulation:g}",
                "save candidate.fit",
                "close",
                "load candidate.fit",
                "setfindstar reset",
                "setfindstar -gaussian",
                f"findstar -layer={layer} -out=after.tsv",
                "close",
                "",
            ]
        )
        if not create_siril_from_args(args).run_siril_script(
            script, str(work), script_name="denoise.sps"
        ):
            raise RuntimeError(f"Échec Siril : {work / 'denoise.log'}")
        comparison = compare_star_catalogs(before, after, 1.0, 10, 0.7, layer)
        result["comparison"] = comparison
        reasons = []
        if comparison["status"] != "validated":
            reasons.append("insufficient_matches")
        else:
            metrics = artifact_metrics(source, candidate, comparison["pairs"])
            result["artifacts"] = metrics
            mean_ratio = (
                comparison["after_fwhm_px"]["mean"]
                / comparison["before_fwhm_px"]["mean"]
            )
            median_ratio = comparison["paired_ratio"]["median"]
            if max(mean_ratio, median_ratio) > 1 + max_blur:
                reasons.append("stellar_blurring")
            if (
                comparison["after_roundness"]["mean"]
                < comparison["before_roundness"]["mean"] - 0.01
            ):
                reasons.append("roundness_degraded")
            if metrics["max_noise_ratio"] >= 1:
                reasons.append("no_noise_reduction")
            if metrics["max_ring_fraction"] > 0.1:
                reasons.append("stellar_rings")
            logging.info(
                "Réduction du bruit : %d étoiles appariées ; gain de bruit minimal %+.2f %% ; "
                "variation FWHM moyenne %+.2f %% ; variation médiane appariée %+.2f %%",
                comparison["matched_count"],
                100 * (1 - metrics["max_noise_ratio"]),
                100 * (mean_ratio - 1),
                100 * (median_ratio - 1),
            )
        result["rejection_reasons"] = reasons
        if reasons:
            result.update(status="rejected", output_image=str(source))
            logging.info(
                "Réduction du bruit annulée : %s ; image précédente conservée",
                ", ".join(reasons),
            )
        else:
            destination = report.with_suffix(".fit")
            if destination == source:
                raise ValueError("La sortie ne peut pas remplacer l’image source")
            copyfile(candidate, destination)
            result.update(status="accepted", output_image=str(destination))
            logging.info("Réduction du bruit acceptée : %s", destination)
        save()
        return result
    except Exception as error:
        result.update(status="failed", error=str(error))
        save()
        raise
