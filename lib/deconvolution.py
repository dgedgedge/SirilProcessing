"""Déconvolution Siril et comparaison FWHM sur des étoiles appariées univoques.

Les fichiers de travail sont directs, sans niveau 001. Dans le plan numéroté,
le dossier porte le nom du rapport sans suffixe _work ; les sources sont
protégées avant sa réinitialisation. Le dossier préparé par un orchestrateur
de chaînes est conservé, avec ses masques et son rapport.
"""

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

from lib.deconvolution_quality import (
    InsufficientPSFStars, artifact_metrics, assess_quality, select_psf_stars,
    log_quality_diagnostics,
)
from lib.postprocess_paths import reset_restoration_work_dir, treatment_file_prefix
from lib.siril import Siril
from lib.logging_utils import log_duration
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
    """Délègue la comparaison et les mesures locales aux processeurs récursifs.

    Les critères et le format scientifique sont conservés ; processing décrit
    la durée de comparaison et celle de la mesure aux positions initiales.
    """
    from lib.quality_processors import QualityContext
    from lib.quality_star_processors import StarComparisonProcessor

    source, candidate = reference_images or (Path(before_path), Path(after_path))
    context = QualityContext.for_catalogs(source, candidate)
    return StarComparisonProcessor(
        context, Path(before_path), Path(after_path), radius, min_stars, min_fraction,
        layer, reference_images=reference_images is not None, quality_mode=quality_mode,
    ).post_process(Path(candidate), None)


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
        self.work = reset_restoration_work_dir(
            self.output_path, self.input_path,
            prepared_directory=getattr(self.args, "_restoration_work_dir", None),
        )
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
        """Exécute un script Siril et cumule sa durée murale dans l'audit.

        RuntimeError signale un échec ; le temps passé reste compté et journalisé.
        """
        name = f"{self.tag}{name}"
        script = "\n".join(
            [
                f"cd {_quote(self.work)}",
                "setext fits",
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
        with log_duration(
            f"Déconvolution Siril — {name}",
            self.result.setdefault("timings", {}), "siril_seconds",
        ):
            if not self.runner.run_siril_script(script, str(self.work), script_name=f"{name}.sps"):
                raise RuntimeError(f"Échec Siril ; consulter {self.work / f'{name}.log'}")

    def _detect(
        self,
        source: Path | str,
        label: str,
        profile: str = "Gaussian",
        amplitude: bool = False,
        *, catalog_path: Path | None = None,
    ) -> Path:
        """Délègue la détection au processeur Siril, avec audit et durée propres.

        Le profil Gaussian/Moffat et les paramètres PSF restent ceux demandés
        par ce moteur ; le catalogue n'est pas recalculé par un autre contrôle.
        """
        catalog = catalog_path or self.work / f"{self.tag}{label}_stars.tsv"
        settings = f"setfindstar -{profile.lower()}"
        if amplitude:
            settings += f" -minA=0.1 -maxA=0.7 -roundness={self.roundness}"
        from lib.quality_processors import QualityContext
        from lib.quality_star_processors import SirilStarDetectionProcessor

        context = QualityContext.for_catalogs(source)
        detection = SirilStarDetectionProcessor(
            context, [(Path(source), catalog)], self.work, self.layer,
            runner=self.runner, script_name=f"{self.tag}{label}.sps",
            findstar_settings=settings.removeprefix('setfindstar '),
        )
        self.result['scripts'].append(dict(
            script_path=str(self.work / detection.script_name),
            log_path=str(self.work / Path(detection.script_name).with_suffix('.log')),
        ))
        self._save_report()
        try:
            detection.post_process(Path(source), None, self.args)
        finally:
            if detection.last_processing is not None:
                self.result.setdefault('star_detection_processing', []).append(detection.last_processing)
                timings = self.result.setdefault('timings', {})
                timings['siril_seconds'] = timings.get('siril_seconds', 0) + detection.last_processing['duration_seconds']
                self._save_report()
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
        """Exécute un essai et mesure séparément Siril et les contrôles de qualité.

        Les cumuls en secondes sont conservés dans le rapport de recherche.
        """
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
            ],
        )
        self._detect(candidate, label + '_measure', catalog_path=after_catalog)
        with log_duration(
            f"Déconvolution Siril — qualité de {label}",
            self.result.setdefault("timings", {}), "quality_seconds",
        ):
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
    def parameter_children(cls) -> tuple[type[processor], ...]:
        """Collecte récursivement les paramètres des contrôles de ce moteur."""
        from lib.quality_processors import (
            ArtifactQualityProcessor, ReferencePreparationProcessor,
        )
        from lib.quality_star_processors import StellarAssessmentProcessor

        return (
            ReferencePreparationProcessor, StellarAssessmentProcessor, ArtifactQualityProcessor,
        )

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Expose les paramètres locaux de l’algorithme Siril."""
        for child in cls.parameter_children():
            child.add_arguments(parser)
        if any(action.dest == 'deconvolution_iterations' for action in parser._actions):
            return
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
