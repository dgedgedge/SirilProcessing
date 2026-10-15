"""Déconvolution Siril et comparaison FWHM sur des étoiles appariées univoques."""

from __future__ import annotations

import argparse
import csv
import json
import logging
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from shutil import copyfile

import numpy as np
from astropy.io import fits
from PIL import Image, ImageDraw
from scipy.spatial import cKDTree

from lib.deconvolution_quality import (
    InsufficientPSFStars, artifact_metrics, assess_quality, select_psf_stars,
    log_quality_diagnostics,
)
from lib.postprocess_paths import reset_ordered_work_dir, treatment_file_prefix
from lib.siril_utils import Siril
from lib.processor import processor
from lib.restoration import RestorationComparisonProcessor
from lib.type_defs import ConfigValue, JSONReport, StarMeasurement


# Fractions de chaque dimension : du tiers central au champ entier.
ADAPTIVE_CROP_FRACTIONS = (1 / 3, 1 / 2, 2 / 3, 1.0)


def read_star_catalog(
    path: Path | str, profile: str = "Gaussian"
) -> tuple[list[StarMeasurement], list[JSONReport]]:
    """Lit le TSV findstar Siril 1.4, avec unités explicites en pixels."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    header_index = next(
        (i for i, line in enumerate(lines) if line.startswith("# star#\t")), None
    )
    if header_index is None:
        raise ValueError(f"En-tête findstar Siril absent : {path}")
    reader = csv.DictReader(
        [lines[header_index].lstrip("# "), *lines[header_index + 1 :]], delimiter="\t"
    )
    required = {
        "star#",
        "layer",
        "X",
        "Y",
        "FWHMx [px]",
        "FWHMy [px]",
        "Sat",
        "Profile",
    }
    if not required.issubset(reader.fieldnames or []):
        raise ValueError(f"Colonnes FWHM en pixels ou identité manquantes : {path}")
    stars, rejected = [], []
    ids = set()
    for row in reader:
        identifier = int(row["star#"])
        if identifier in ids:
            raise ValueError(
                f"Identifiant stellaire dupliqué : {identifier} dans {path}"
            )
        ids.add(identifier)
        x, y, fx, fy = (float(row[k]) for k in ("X", "Y", "FWHMx [px]", "FWHMy [px]"))
        reason = None
        if not all(np.isfinite([x, y, fx, fy])) or min(fx, fy) <= 0:
            reason = "invalid_measurement"
        elif int(row["Sat"]) != 0:
            reason = "saturated"
        elif row["Profile"].strip() != profile:
            reason = "unexpected_profile"
        elif profile == "Moffat" and (
            not np.isfinite(float(row.get("beta", "nan"))) or float(row["beta"]) <= 1
        ):
            reason = "invalid_moffat_beta"
        if reason:
            rejected.append({"id": identifier, "reason": reason})
            continue
        stars.append(
            dict(
                id=identifier,
                layer=int(row["layer"]),
                x=x,
                y=y,
                fwhm_x=fx,
                fwhm_y=fy,
                fwhm=float(np.sqrt(fx * fy)),
                amplitude=float(row.get("A", "nan")),
                beta=float(row.get("beta", "nan")),
            )
        )
    return stars, rejected


def compare_star_catalogs(
    before_path: Path | str,
    after_path: Path | str,
    radius: float,
    min_stars: int,
    min_fraction: float,
    layer: int,
    *,
    reference_images: tuple[Path | str, Path | str] | None = None,
    quality_mode: str = 'reliable',
) -> JSONReport:
    """Apparie les étoiles par voisinage unique réciproque dans le rayon en pixels.

    Ne publie les statistiques FWHM et rondeur que si min_stars et la fraction
    minimale sont atteints. Refuse des catalogues de canaux différents.
    reference_images déclenche une mesure locale commune avant/après, avec
    sélection des références fiables sur l’entrée ; la détection indépendante
    après reste un diagnostic. Les flux, SNR et motifs sont conservés dans
    reference_measurements. Voir postProcess/reference-stars.md.
    Voir docs/scripts/postProcess/README.md pour les critères de déconvolution."""
    before, rejected_before = read_star_catalog(before_path)
    after, rejected_after = read_star_catalog(after_path)
    reference_audit = None
    independent_after_count = len(after)
    if reference_images is not None:
        from lib.reference_stars import measure_reference_catalog

        before, after, reference_audit = measure_reference_catalog(
            *reference_images, before, layer, radius,
        )
    if any(star["layer"] != layer for star in before + after):
        raise ValueError("Les catalogues avant/après ne portent pas sur le même canal")
    pairs = []
    used_before, used_after = set(), set()
    if before and after:
        bxy = np.array([[s["x"], s["y"]] for s in before])
        axy = np.array([[s["x"], s["y"]] for s in after])
        forward = cKDTree(axy).query_ball_point(bxy, radius)
        reverse = cKDTree(bxy).query_ball_point(axy, radius)
        for i, candidates in enumerate(forward):
            if len(candidates) != 1:
                continue
            j = candidates[0]
            if reverse[j] != [i]:
                continue
            b, a = before[i], after[j]
            used_before.add(b["id"])
            used_after.add(a["id"])
            pairs.append(
                dict(
                    reference_id=b["id"],
                    after_id=a["id"],
                    x_before=b["x"],
                    y_before=b["y"],
                    x_after=a["x"],
                    y_after=a["y"],
                    distance_px=float(np.linalg.norm(bxy[i] - axy[j])),
                    fwhm_x_before_px=b["fwhm_x"],
                    fwhm_y_before_px=b["fwhm_y"],
                    fwhm_x_after_px=a["fwhm_x"],
                    fwhm_y_after_px=a["fwhm_y"],
                    fwhm_before_px=b["fwhm"],
                    fwhm_after_px=a["fwhm"],
                    ratio_after_before=a["fwhm"] / b["fwhm"],
                    roundness_before=min(b["fwhm_x"], b["fwhm_y"])
                    / max(b["fwhm_x"], b["fwhm_y"]),
                    roundness_after=min(a["fwhm_x"], a["fwhm_y"])
                    / max(a["fwhm_x"], a["fwhm_y"]),
                )
            )
    fraction = len(pairs) / len(before) if before else 0.0
    valid = len(pairs) >= min_stars and fraction >= min_fraction
    result = dict(
        status="validated" if valid else "insufficient_matches",
        metric="sqrt(FWHMx_px * FWHMy_px)",
        profile="Gaussian",
        layer=layer,
        matching="unique_both_directions_within_radius",
        match_radius_px=radius,
        valid_before_count=len(before),
        valid_after_count=len(after),
        matched_count=len(pairs),
        matched_fraction_before=fraction,
        min_stars=min_stars,
        min_match_fraction=min_fraction,
        rejected_before=rejected_before,
        rejected_after=rejected_after,
        unmatched_before_ids=[s["id"] for s in before if s["id"] not in used_before],
        unmatched_after_ids=[s["id"] for s in after if s["id"] not in used_after],
        pairs=pairs,
    )
    if reference_audit is not None:
        result["reference_measurements"] = reference_audit
        result["independent_after_count"] = independent_after_count
        logging.info(
            "Mesure aux positions initiales : %d détections initiales ; %d références fiables ; "
            "%d mesures candidates ; %d étoiles appariées ; SNR minimal %.1f",
            reference_audit['initial_count'], len(before), len(after), len(pairs),
            reference_audit['min_reference_snr'],
        )
        logging.info("Mesure aux positions initiales — diagnostics : %s",
                     reference_audit['failure_summary'])
    if quality_mode != 'legacy' and valid:
        from lib.cosmic_clarity.processors import assess_matching_representation

        representation = assess_matching_representation(before_path, result, layer)
        result['representation'] = representation
        representation['status'] = (
            'UNKNOWN' if representation['reasons'] == ['missing_valid_brightness']
            else 'PASS' if representation['accepted'] else 'FAIL'
        )
        if representation['status'] == 'FAIL':
            valid = False
            result['status'] = 'unrepresentative_matches'
    result['criterion'] = dict(
        status='PASS' if valid else 'UNKNOWN' if len(before) < min_stars else 'FAIL',
        reason=result['status'], value=fraction, threshold=min_fraction,
        uncertainty=None, sample_count=len(before),
    )
    # Aucune statistique de comparaison publiée tant que la cohorte n'est pas validée.
    if valid:

        def stats(values: Sequence[float]) -> dict[str, float]:
            """Résume les mesures appariées par moyenne, médiane, dispersion et quartiles."""
            values = np.asarray(values)
            return dict(
                count=len(values),
                mean=float(values.mean()),
                median=float(np.median(values)),
                std=float(values.std()),
                mad=float(np.median(abs(values - np.median(values)))),
                p25=float(np.percentile(values, 25)),
                p75=float(np.percentile(values, 75)),
            )

        result["before_fwhm_px"] = stats([p["fwhm_before_px"] for p in pairs])
        result["after_fwhm_px"] = stats([p["fwhm_after_px"] for p in pairs])
        result["before_roundness"] = stats([p["roundness_before"] for p in pairs])
        result["after_roundness"] = stats([p["roundness_after"] for p in pairs])
        result["paired_ratio"] = stats([p["ratio_after_before"] for p in pairs])
        result["median_paired_reduction_percent"] = 100 * (
            1 - result["paired_ratio"]["median"]
        )
    return result


def save_psf_preview(psf_path: Path | str, png_path: Path | str) -> None:
    """Écrit un aperçu PNG linéaire et racine carrée d’une PSF FITS positive.

    Raises:
        ValueError: Si le noyau n’est pas carré, fini et non négatif."""
    data = np.asarray(fits.getdata(psf_path), dtype=float)
    if (
        data.ndim != 2
        or data.shape[0] != data.shape[1]
        or not np.isfinite(data).all()
        or data.max() <= 0
        or data.min() < 0
    ):
        raise ValueError("PSF FITS invalide : noyau carré, fini et positif attendu")
    normalized = data / data.max()
    preview = Image.new("RGB", (512, 282), "black")
    draw = ImageDraw.Draw(preview)
    for index, (values, label) in enumerate(
        ((normalized, "PSF linear"), (np.sqrt(normalized), "PSF sqrt (wings)"))
    ):
        tile = Image.fromarray((values * 255).astype("uint8")).resize(
            (256, 256), Image.Resampling.NEAREST
        )
        preview.paste(tile, (index * 256, 26))
        draw.text((index * 256 + 8, 8), label, fill="white")
    preview.save(png_path)


def _quote(path: Path | str) -> str:
    """Protège un chemin Siril et refuse guillemets, retours ligne et octets NUL."""
    value = str(path)
    if any(c in value for c in ('"', "\n", "\r", "\x00")):
        raise ValueError("Chemin incompatible avec un script Siril")
    return f'"{value}"'


class _DeconvolutionExecution:
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
        self.input_path = input_path
        self.output_path = output_path

        self.iterations = self._option("iterations", 10)
        self.size = self._option("psf_size", 15)
        self.psf_method = self._option("psf_method", "blind")
        self.adaptive = self._option("adaptive", True)
        self.alpha = self._option("alpha", 3000)
        if self.psf_method not in ("blind", "stars") or not np.isfinite(self.alpha) or self.alpha <= 0:
            raise ValueError("Méthode PSF ou alpha invalide")
        self.radius = self._option("match_radius", 1.0)
        self.minimum = self._option("min_stars", 10)
        self.fraction = self._option("min_match_fraction", 0.7)
        self.step = self._option("step", 0.0003)
        self.max_step = self._option("max_step", 0.001)
        self.fine_quantile = self._option("fine_quantile", 0.35)
        self.roundness = self._option("psf_roundness", 0.8)
        self.quality_settings = dict(
            min_gain=self._option("min_gain", 0.02),
            max_noise=self._option("max_noise", 1.15),
            max_rings=self._option("max_rings", 0.1),
        )
        if not isinstance(self.iterations, int) or not 1 <= self.iterations <= 1000:
            raise ValueError("deconvolution-iterations doit être entre 1 et 1000")
        if not isinstance(self.size, int) or not 3 <= self.size <= 255 or self.size % 2 != 1:
            raise ValueError("deconvolution-psf-size doit être impair, entre 3 et 255")
        values = [
            self.radius,
            self.fraction,
            self.step,
            self.max_step,
            self.fine_quantile,
            self.roundness,
            *self.quality_settings.values(),
        ]
        if (
            not all(np.isfinite(values))
            or self.radius <= 0
            or self.minimum < 3
            or not 0 < self.fraction <= 1
            or not 0 < self.step <= 0.01
            or not 0.0004 <= self.max_step <= 0.01
            or not 0 < self.fine_quantile <= 1
            or not 0 < self.roundness <= 0.95
            or not 0 < self.quality_settings["min_gain"] < 1
            or self.quality_settings["max_noise"] < 1
            or not 0 <= self.quality_settings["max_rings"] <= 1
        ):
            raise ValueError(
                "Paramètres de sélection, de comparaison ou de recherche du pas invalides"
            )
        self.input_path = Path(self.input_path).resolve()
        self.output_path = Path(self.output_path).resolve()
        with fits.open(self.input_path, memmap=False) as hdul:
            self.shape = hdul[0].shape
            if len(self.shape) not in (2, 3) or (len(self.shape) == 3 and self.shape[0] != 3):
                raise ValueError("Image FITS mono ou RGB requise")
            if len(self.shape) == 2 and hdul[0].header.get("BAYERPAT", "").strip():
                raise ValueError("Déconvolution : dématricer le FITS CFA au préalable")
        self.layer = self._option("layer", None)
        self.layer = (1 if len(self.shape) == 3 else 0) if self.layer is None else self.layer
        if self.layer not in ([0, 1, 2] if len(self.shape) == 3 else [0]):
            raise ValueError("Canal de mesure incompatible avec cette image")
        self.tag = treatment_file_prefix(self.output_path)
        default_image = (
            f"{self.output_path.stem}.fits" if self.tag else f"{self.input_path.stem}_deconvolved.fits"
        )
        self.destination = Path(
            self._option("output", None) or self.output_path.parent / default_image
        ).resolve()
        if self.destination in (self.input_path, self.output_path) or (
            self.destination.suffix.lower() not in (".fit", ".fits", ".fts")
        ):
            raise ValueError("Choisir une sortie FITS différente de l’entrée et du rapport")
        _quote(self.input_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.work = reset_ordered_work_dir(self.output_path)
        self.runner = Siril(
            siril_path=getattr(self.args, "siril_path", None),
            siril_mode=getattr(self.args, "siril_mode", None),
        )
        self.snapshot = self.work / f"{self.tag}original.fits"
        copyfile(self.input_path, self.snapshot)
        self.result: JSONReport = dict(
            image_path=str(self.input_path),
            original_image_path=str(self.snapshot),
            report_saved_to=str(self.output_path),
            method="Richardson-Lucy gradient descent",
            iterations=self.iterations,
            psf_size=self.size,
            psf_method=self.psf_method,
            regularization="TV",
            alpha=self.alpha,
            adaptive=self.adaptive,
            status="evaluating",
            attempts=[],
            psf_selections=[],
            scripts=[],
            layer=self.layer,
        )

    def _option(self, name: str, default: ConfigValue) -> ConfigValue:
        """Lit une option de déconvolution, avec repli sur son défaut local."""
        return getattr(self.args, f"deconvolution_{name}", default)

    def _save_report(self) -> None:
        """Écrit l’état courant de l’audit JSON pour conserver les diagnostics d’essai."""
        self.output_path.write_text(
            json.dumps(self.result, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _run_siril(self, name: str, lines: Sequence[str]) -> None:
        """Écrit et exécute un script Siril ; lève RuntimeError si l’exécution échoue."""
        name = f"{self.tag}{name}"
        script = "\n".join(
            [
                "requires 1.4",
                f"cd {_quote(self.work)}",
                "setext fit",
                "set32bits",
                *lines,
                "close",
                "",
            ]
        )
        self.result["scripts"].append(
            dict(
                script_path=str(self.work / f"{name}.sps"),
                log_path=str(self.work / f"{name}.log"),
            )
        )
        self._save_report()
        if not self.runner.run_siril_script(script, str(self.work), script_name=f"{name}.sps"):
            raise RuntimeError(f"Échec Siril ; consulter {self.work / f'{name}.log'}")

    def _detect(
        self,
        source: Path | str,
        label: str,
        profile: str = "Gaussian",
        amplitude: bool = False,
    ) -> Path:
        """Produit un catalogue stellaire Siril sur le canal et le profil demandés."""
        catalog = self.work / f"{self.tag}{label}_stars.tsv"
        settings = f"setfindstar -{profile.lower()}"
        if amplitude:
            settings += f" -minA=0.1 -maxA=0.7 -roundness={self.roundness}"
        self._run_siril(
            label,
            [
                f"load {_quote(source)}",
                "setfindstar reset",
                settings,
                f"findstar -layer={self.layer} -out={catalog.name}",
            ],
        )
        return catalog

    def _build_psf(
        self,
        source: Path | str,
        catalog: Path | str,
        label: str,
        profile: str,
        amplitude_min: float,
    ) -> Path:
        """Sélectionne des étoiles isolées et écrit une PSF FITS avec son aperçu PNG."""
        stars, rejected = read_star_catalog(catalog, profile=profile)
        sheet = self.work / f"{self.tag}{label}_selected_stars.fits"
        try:
            selection = select_psf_stars(
                stars,
                source,
                sheet,
                quantile=self.fine_quantile,
                roundness=self.roundness,
                min_amplitude=amplitude_min,
                max_amplitude=0.7,
                layer=self.layer,
            )
        except ValueError:
            stamp_audit = sheet.with_suffix(".json")
            if stamp_audit.exists():
                self.result["psf_selections"].append(
                    dict(
                        profile=profile,
                        catalog_path=str(catalog),
                        stamp_audit_path=str(stamp_audit),
                        status="insufficient_clean_stars",
                    )
                )
                self._save_report()
            raise
        selection.update(profile=profile, catalog_path=str(catalog), rejected=rejected)
        self.result["psf_selections"].append(selection)
        selection_path = self.work / f"{self.tag}{label}_selection.json"
        selection_path.write_text(json.dumps(selection, indent=2), encoding="utf-8")
        psf = self.work / f"{self.tag}{label}_psf.fits"
        self._run_siril(
            label + "_psf",
            [
                f"load {_quote(sheet)}",
                "setfindstar reset",
                f"setfindstar -{profile.lower()} -roundness={self.roundness} "
                f"-minA={amplitude_min} -maxA=0.7",
                f"findstar -layer=0 -out={self.tag}{label}_sheet_stars.tsv",
                "makepsf clear",
                f"makepsf stars -ks={self.size}",
                f"makepsf save {psf.name}",
            ],
        )
        preview = psf.with_suffix(".png")
        save_psf_preview(psf, preview)
        kernel = fits.getdata(psf)
        # Contrôle géométrique du coeur, indépendant de la dimension du fichier PSF.
        measured_width = float(
            2 * np.sqrt(np.count_nonzero(kernel >= kernel.max() / 2) / np.pi)
        )
        selected_width = float(np.median([star["fwhm"] for star in selection["stars"]]))
        selection.update(
            psf_path=str(psf),
            psf_preview_path=str(preview),
            kernel_fwhm_equivalent_px=measured_width,
            selected_median_fwhm_px=selected_width,
        )
        selection_path.write_text(json.dumps(selection, indent=2), encoding="utf-8")
        self._save_report()
        if measured_width > 1.5 * selected_width:
            raise ValueError(
                "PSF générée trop large par rapport aux étoiles sélectionnées"
            )
        return psf

    def _record_failure(self, label: str, error: ValueError | RuntimeError) -> JSONReport:
        """Audite un échec local sans empêcher les autres essais de restauration."""
        attempt = dict(
            number=len(self.result["attempts"]) + 1,
            label=label,
            status="failed",
            error=str(error),
            quality=dict(accepted=False, artifact_failure=False, reasons=[str(error)]),
        )
        self.result["attempts"].append(attempt)
        self._save_report()
        logging.warning(
            "Déconvolution essai %d (%s) échoué : %s",
            attempt["number"], label, error,
        )
        return attempt

    def _trial(
        self,
        source: Path | str,
        before_catalog: Path | str,
        psf: Path | str,
        step_value: float,
        label: str,
    ) -> JSONReport:
        """Numérote l’essai et conserve ses erreurs comme diagnostics locaux."""
        logging.info(
            "Déconvolution essai %d (%s), pas %.7f",
            len(self.result["attempts"]) + 1, label, step_value,
        )
        try:
            return self._evaluate_trial(source, before_catalog, psf, step_value, label)
        except (ValueError, RuntimeError) as error:
            return self._record_failure(label, error)

    def _evaluate_trial(
        self,
        source: Path | str,
        before_catalog: Path | str,
        psf: Path | str,
        step_value: float,
        label: str,
    ) -> JSONReport:
        """Exécute un essai au pas demandé et renvoie l’image avec son audit de qualité."""
        candidate = self.work / f"{self.tag}{label}.fits"
        after_catalog = self.work / f"{self.tag}{label}_stars.tsv"
        self._run_siril(
            label,
            [
                f"load {_quote(source)}",
                f'rl "-loadpsf={psf}" -iters={self.iterations} '
                f'-gdstep={step_value:.7f} -tv -alpha={self.alpha:g}',
                f"save {candidate.name}",
                "close",
                f"load {candidate.name}",
                "setfindstar reset",
                "setfindstar -gaussian",
                f"findstar -layer={self.layer} -out={after_catalog.name}",
            ],
        )
        comparison = compare_star_catalogs(
            before_catalog, after_catalog, self.radius, self.minimum, self.fraction, self.layer,
            reference_images=(source, candidate)
            if self._option("reference_measurements", True) else None,
            quality_mode=getattr(self.args, "quality_mode", "reliable"),
        )
        attempt = dict(
            number=len(self.result["attempts"]) + 1,
            label=label,
            source_path=str(source),
            candidate_image_path=str(candidate),
            psf_path=str(psf),
            step=step_value,
            before_catalog_path=str(before_catalog),
            after_catalog_path=str(after_catalog),
            comparison=comparison,
        )
        if comparison["status"] == "validated":
            try:
                attempt["artifacts"] = artifact_metrics(
                    source, candidate, comparison["pairs"], args=self.args
                )
                attempt["quality"] = assess_quality(
                    comparison, attempt["artifacts"], **self.quality_settings
                )
            except ValueError as error:
                attempt["quality"] = dict(
                    accepted=False, artifact_failure=True, reasons=[str(error)]
                )
        else:
            attempt["quality"] = dict(
                accepted=False, artifact_failure=True, reasons=["insufficient_matches"]
            )
        self.result["attempts"].append(attempt)
        self._save_report()
        if comparison["status"] == "validated":
            fb, fa = (
                comparison["before_fwhm_px"]["mean"],
                comparison["after_fwhm_px"]["mean"],
            )
            rb, ra = (
                comparison["before_roundness"]["mean"],
                comparison["after_roundness"]["mean"],
            )
            logging.info(
                "Déconvolution %s : %d étoiles appariées ; gain de finesse médian %+.2f %% ; "
                "FWHM moyenne %.4f → %.4f px (gain %+.2f %%) ; "
                "rondeur moyenne %.4f → %.4f (variation %+.4f)",
                label,
                comparison["matched_count"],
                100 * (1 - comparison["paired_ratio"]["median"]),
                fb,
                fa,
                100 * (1 - fa / fb),
                rb,
                ra,
                ra - rb,
            )
            artifacts = attempt.get("artifacts")
            if artifacts and artifacts.get("max_noise_ratio") is not None:
                logging.info(
                    "Déconvolution %s : variation maximale du bruit %+.2f %% ; "
                    "fraction maximale d’étoiles avec nouveaux anneaux %.2f %% (tous canaux)",
                    label,
                    100 * (artifacts["max_noise_ratio"] - 1),
                    100 * artifacts["max_ring_fraction"],
                )
        else:
            logging.info(
                "Déconvolution %s : gain non évalué, seulement %d étoiles appariées (%.1f %%)",
                label,
                comparison["matched_count"],
                100 * comparison["matched_fraction_before"],
            )
        log_quality_diagnostics(
            f"Déconvolution {label}", comparison, attempt["quality"], attempt.get("artifacts"),
        )
        logging.info("Déconvolution %s, pas %.7f : %s ; motifs=%s", label, step_value,
                     "acceptée" if attempt["quality"]["accepted"] else "rejetée",
                     ", ".join(attempt["quality"]["reasons"]) or "aucun")
        logging.debug("Audit de qualité : %s", attempt["quality"])
        return attempt

    def _attempt_simple(self, before: Path) -> JSONReport | None:
        """Construit la PSF initiale et retourne le premier candidat sûr, sinon None."""
        try:
            if self.psf_method == "blind":
                psf = self.work / f"{self.tag}blind_psf.fits"
                self._run_siril(
                    "blind_psf",
                    [
                        f"load {_quote(self.snapshot)}",
                        "makepsf clear",
                        f"makepsf blind -l0 -ks={self.size}",
                        f"makepsf save {psf.name}",
                    ],
                )
                save_psf_preview(psf, psf.with_suffix(".png"))
                self.result["psf_selections"].append(
                    dict(
                        method="blind_l0",
                        psf_path=str(psf),
                        psf_preview_path=str(psf.with_suffix(".png")),
                    )
                )
                self._save_report()
            else:
                psf = self._build_psf(self.snapshot, before, "simple", "Gaussian", 0.01)
            first = self._trial(self.snapshot, before, psf, self.step, "simple_trial")
            if first["quality"]["accepted"]:
                return first
        except (ValueError, RuntimeError) as error:
            self.result["simple_psf_error"] = str(error)
            self._record_failure("simple_psf", error)
        return None

    def _attempt_adaptive(self, before: Path) -> JSONReport | None:
        """Élargit la PSF puis cherche le pas sûr sur l’original, sans modifier les seuils."""
        accepted = None
        try:
            self.result["adaptive_crop_attempts"] = []
            for crop_index, crop_fraction in enumerate(ADAPTIVE_CROP_FRACTIONS):
                label = "central" if crop_index == 0 else f"central_expand_{crop_index}"
                crop = self.work / f"{self.tag}{label}_original.fits"
                with fits.open(self.snapshot, memmap=False) as hdul:
                    height, width = self.shape[-2:]
                    crop_h = max(1, int(height * crop_fraction))
                    crop_w = max(1, int(width * crop_fraction))
                    y0, x0 = (height - crop_h) // 2, (width - crop_w) // 2
                    data = hdul[0].data[..., y0:y0 + crop_h, x0:x0 + crop_w]
                    crop_header = fits.Header()
                    if "ROWORDER" in hdul[0].header:
                        crop_header["ROWORDER"] = hdul[0].header["ROWORDER"]
                    fits.writeto(crop, data, crop_header)
                crop_info = dict(
                    path=str(crop), x=x0, y=y0, width=crop_w, height=crop_h,
                    dimension_fraction=crop_fraction,
                )
                self.result["crop"] = crop_info
                logging.info(
                    "PSF adaptative : zone %d/%d, %dx%d pixels (%.1f %% des dimensions)",
                    crop_index + 1, len(ADAPTIVE_CROP_FRACTIONS),
                    crop_w, crop_h, 100 * crop_fraction,
                )
                psf_catalog = self._detect(crop, label + "_moffat", "Moffat", amplitude=True)
                try:
                    psf = self._build_psf(crop, psf_catalog, label + "_moffat", "Moffat", 0.1)
                except InsufficientPSFStars as error:
                    self.result["adaptive_crop_attempts"].append(
                        dict(**crop_info, status="insufficient_stars", error=str(error))
                    )
                    logging.info("PSF adaptative : %s ; élargissement si possible", error)
                    if crop_index == len(ADAPTIVE_CROP_FRACTIONS) - 1:
                        raise
                    continue
                self.result["adaptive_crop_attempts"].append(dict(**crop_info, status="accepted"))
                logging.info(
                    "PSF adaptative construite sur %dx%d pixels ; "
                    "début de la recherche du pas", crop_w, crop_h,
                )
                crop_before = self._detect(crop, label + "_before")
                break
            best = None
            for step_number in range(4, int(round(self.max_step * 10000)) + 1):
                attempt = self._trial(
                    crop,
                    crop_before,
                    psf,
                    step_number / 10000,
                    f"central_step_{step_number:02d}",
                )
                if attempt["quality"]["artifact_failure"]:
                    logging.info("Recherche adaptative arrêtée au pas %.7f : artefacts détectés",
                                 step_number / 10000)
                    self.result["search_stopped_at_step"] = step_number / 10000
                    break
                if attempt["quality"]["accepted"]:
                    # Dernier pas efficace avant le premier essai excessif.
                    best = attempt
            if best is not None:
                full = self._trial(self.snapshot, before, psf, best["step"], "full_trial")
                if full["quality"]["accepted"]:
                    accepted = full
        except (ValueError, RuntimeError) as error:
            self.result["adaptive_psf_error"] = str(error)
            self._record_failure("central_moffat", error)
        return accepted

    def _publish_accepted(self, accepted: JSONReport) -> JSONReport:
        """Copie le candidat validé, exporte les étoiles appariées et écrit l’audit."""
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        copyfile(accepted["candidate_image_path"], self.destination)
        pairs_path = self.work / f"{self.tag}matched_stars.csv"
        with pairs_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(
                stream, fieldnames=list(accepted["comparison"]["pairs"][0])
            )
            writer.writeheader()
            writer.writerows(accepted["comparison"]["pairs"])
        self.result.update(
            status="accepted",
            output_image=str(self.destination),
            selected_step=accepted["step"],
            psf_path=accepted["psf_path"],
            psf_preview_path=str(Path(accepted["psf_path"]).with_suffix(".png")),
            comparison=accepted["comparison"],
            quality=accepted["quality"],
            artifacts=accepted["artifacts"],
            matched_stars_path=str(pairs_path),
        )
        self._save_report()
        return self.result

    def execute(self) -> JSONReport:
        """Orchestre les essais simples/adaptatifs et conserve chaque échec dans l’audit.

        Returns
        -------
        dict
            Mesures, essais et image retenue, sans modifier la source.

        Raises
        ------
        RuntimeError
            Si aucune amélioration sûre n’est disponible ou si Siril échoue.
        """
        try:
            before = self._detect(self.snapshot, "before")
            accepted = self._attempt_simple(before)
            if accepted is None and self.adaptive:
                accepted = self._attempt_adaptive(before)
            if accepted is None:
                self.result["status"] = "no_safe_improvement"
                self._save_report()
                raise RuntimeError(
                    "Aucune déconvolution efficace sans dégradation mesurée ; "
                    f"original conservé, audit : {self.output_path}"
                )
            return self._publish_accepted(accepted)
        except Exception as error:
            if self.result["status"] == "evaluating":
                self.result["status"] = "failed"
            self.result["error"] = str(error)
            self._save_report()
            raise


class SirilDeconvolutionProcessor(processor):
    """Exécute uniquement le moteur Siril et ses contrôles de qualité."""

    parameter_persistence = {"deconvolution_output": False}

    parameter_prefixes = ('quality_', 'deconvolution_', 'denoise_', 'cosmic_')

    @classmethod
    def get_prefix(cls) -> str:
        """Retourne l’identifiant commun de l’étape."""
        return 'deconvolution'

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Expose les paramètres locaux de l’algorithme Siril."""
        parser.add_argument(
            "--deconvolution-reference-measurements", action=argparse.BooleanOptionalAction,
            default=True, help="Mesurer les mêmes étoiles aux positions initiales (SNR ≥ 10)",
        )
        parser.add_argument("--deconvolution-iterations", type=int, default=10)
        parser.add_argument(
            "--deconvolution-psf-size",
            type=int,
            default=15,
            help="Taille impaire de la PSF en pixels",
        )
        parser.add_argument(
            "--deconvolution-psf-method",
            choices=["blind", "stars"],
            default="blind",
            help="PSF native aveugle l0 (défaut) ou sélection des étoiles",
        )
        parser.add_argument(
            "--deconvolution-adaptive",
            action=argparse.BooleanOptionalAction,
            default=True,
            help=(
                "Activer (défaut) ou désactiver (--no-deconvolution-adaptive) "
                "le repli Moffat et les essais de pas croissants"
            ),
        )
        parser.add_argument(
            "--deconvolution-alpha",
            type=float,
            default=3000,
            help="Régularisation TV : valeur plus faible = régularisation plus forte",
        )
        parser.add_argument(
            "--deconvolution-layer",
            type=int,
            choices=[0, 1, 2],
            default=None,
            help="Canal des mesures avant/après : défaut vert RGB (1), mono (0)",
        )
        parser.add_argument(
            "--deconvolution-match-radius",
            type=float,
            default=1.0,
            help="Distance maximale de correspondance, en pixels",
        )
        parser.add_argument("--deconvolution-min-stars", type=int, default=10)
        parser.add_argument(
            "--deconvolution-min-match-fraction",
            type=float,
            default=0.7,
            help="Fraction minimale des étoiles valides avant retrouvées après",
        )
        parser.add_argument(
            "--deconvolution-force-manual-matching",
            action="store_true",
            help=(
                "Forcer les seuils saisis (--deconvolution-match-radius, "
                "--deconvolution-min-stars, --deconvolution-min-match-fraction) "
                "au lieu du mode auto adaptatif"
            ),
        )
        parser.add_argument(
            "--deconvolution-step",
            type=float,
            default=0.0003,
            help="Pas de descente du premier essai",
        )
        parser.add_argument(
            "--deconvolution-max-step",
            type=float,
            default=0.001,
            help="Dernier pas testé sur le tiers central",
        )
        parser.add_argument(
            "--deconvolution-fine-quantile",
            type=float,
            default=0.35,
            help="Fraction des étoiles les plus fines admissibles à la PSF",
        )
        parser.add_argument("--deconvolution-psf-roundness", type=float, default=0.8)
        parser.add_argument(
            "--deconvolution-min-gain",
            type=float,
            default=0.02,
            help="Gain relatif minimal de finesse",
        )
        parser.add_argument(
            "--deconvolution-max-noise",
            type=float,
            default=1.15,
            help="Amplification maximale du bruit de fond",
        )
        parser.add_argument(
            "--deconvolution-max-rings",
            type=float,
            default=0.1,
            help="Fraction maximale d’étoiles avec un halo nouveau",
        )
        parser.add_argument(
            "--deconvolution-output",
            type=Path,
            help="FITS déconvolué ; défaut dans le dossier du rapport",
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
        return _DeconvolutionExecution(input_path, output_path, args).execute()


class DeconvolutionProcessor(RestorationComparisonProcessor):
    """Orchestre les candidats Siril et Cosmic Clarity et conserve le meilleur accepté."""

    parameter_persistence = {'deconvolution_output': False}
    operation = 'sharpen'
    siril_processor = SirilDeconvolutionProcessor

    @classmethod
    def get_prefix(cls) -> str:
        """Retourne l’identifiant stable des options et rapports."""
        return 'deconvolution'

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare les paramètres communs de l’étape."""
        cls.add_shared_arguments(parser)
        cls.siril_processor.add_arguments(parser)
