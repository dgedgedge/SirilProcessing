"""Déconvolution Siril et comparaison FWHM sur des étoiles appariées univoques."""

from __future__ import annotations

import argparse
import csv
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from shutil import copyfile

import numpy as np
from astropy.io import fits
from PIL import Image, ImageDraw
from scipy.spatial import cKDTree

from lib.deconvolution_quality import artifact_metrics, assess_quality, select_psf_stars
from lib.postprocess_paths import reset_ordered_work_dir, treatment_file_prefix
from lib.siril_utils import create_siril_from_args
from lib.type_defs import ConfigValue, JSONReport, StarMeasurement


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Déclare les options de PSF, de recherche du pas et les seuils d’acceptation."""
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
) -> JSONReport:
    """Apparie les étoiles par voisinage unique réciproque dans le rayon en pixels.

    Ne publie les statistiques FWHM et rondeur que si min_stars et la fraction
    minimale sont atteints. Refuse des catalogues de canaux différents.
    Voir docs/scripts/postProcess/README.md pour les critères de déconvolution."""
    before, rejected_before = read_star_catalog(before_path)
    after, rejected_after = read_star_catalog(after_path)
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


def post_process(
    input_path: Path | str,
    output_path: Path | str,
    args: argparse.Namespace | None = None,
) -> JSONReport:
    """Déconvolue avec Siril et conserve uniquement une amélioration contrôlée.

    Écrit le rapport JSON, les catalogues et les essais dans le dossier de
    output_path ; préserve le FITS source. Les options viennent de args.

    Raises:
        ValueError: Si les paramètres ou le format FITS sont incompatibles.
        RuntimeError: Si Siril échoue ou si aucun essai sûr n’est accepté.

    Les erreurs locales des essais et des PSF sont auditées ; les autres essais
    disponibles continuent. Voir docs/scripts/postProcess/README.md pour les seuils
    et le repli adaptatif."""

    def option(name: str, default: ConfigValue) -> ConfigValue:
        """Lit une option de déconvolution, avec repli sur son défaut local."""
        return getattr(args, f"deconvolution_{name}", default)

    iterations = option("iterations", 10)
    size = option("psf_size", 15)
    psf_method = option("psf_method", "blind")
    adaptive = option("adaptive", True)
    alpha = option("alpha", 3000)
    if psf_method not in ("blind", "stars") or not np.isfinite(alpha) or alpha <= 0:
        raise ValueError("Méthode PSF ou alpha invalide")
    radius = option("match_radius", 1.0)
    minimum = option("min_stars", 10)
    fraction = option("min_match_fraction", 0.7)
    step = option("step", 0.0003)
    max_step = option("max_step", 0.001)
    fine_quantile = option("fine_quantile", 0.35)
    roundness = option("psf_roundness", 0.8)
    quality_settings = dict(
        min_gain=option("min_gain", 0.02),
        max_noise=option("max_noise", 1.15),
        max_rings=option("max_rings", 0.1),
    )
    if not isinstance(iterations, int) or not 1 <= iterations <= 1000:
        raise ValueError("deconvolution-iterations doit être entre 1 et 1000")
    if not isinstance(size, int) or not 3 <= size <= 255 or size % 2 != 1:
        raise ValueError("deconvolution-psf-size doit être impair, entre 3 et 255")
    values = [
        radius,
        fraction,
        step,
        max_step,
        fine_quantile,
        roundness,
        *quality_settings.values(),
    ]
    if (
        not all(np.isfinite(values))
        or radius <= 0
        or minimum < 3
        or not 0 < fraction <= 1
        or not 0 < step <= 0.01
        or not 0.0004 <= max_step <= 0.01
        or not 0 < fine_quantile <= 1
        or not 0 < roundness <= 0.95
        or not 0 < quality_settings["min_gain"] < 1
        or quality_settings["max_noise"] < 1
        or not 0 <= quality_settings["max_rings"] <= 1
    ):
        raise ValueError(
            "Paramètres de sélection, de comparaison ou de recherche du pas invalides"
        )
    input_path, output_path = Path(input_path).resolve(), Path(output_path).resolve()
    with fits.open(input_path, memmap=False) as hdul:
        shape = hdul[0].shape
        if len(shape) not in (2, 3) or (len(shape) == 3 and shape[0] != 3):
            raise ValueError("Image FITS mono ou RGB requise")
        if len(shape) == 2 and hdul[0].header.get("BAYERPAT", "").strip():
            raise ValueError("Déconvolution : dématricer le FITS CFA au préalable")
    layer = option("layer", None)
    layer = (1 if len(shape) == 3 else 0) if layer is None else layer
    if layer not in ([0, 1, 2] if len(shape) == 3 else [0]):
        raise ValueError("Canal de mesure incompatible avec cette image")
    tag = treatment_file_prefix(output_path)
    default_image = (
        f"{output_path.stem}.fits" if tag else f"{input_path.stem}_deconvolved.fits"
    )
    destination = Path(
        option("output", None) or output_path.parent / default_image
    ).resolve()
    if destination in (input_path, output_path) or destination.suffix.lower() not in (
        ".fit",
        ".fits",
        ".fts",
    ):
        raise ValueError("Choisir une sortie FITS différente de l’entrée et du rapport")
    _quote(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    work = reset_ordered_work_dir(output_path)
    runner = create_siril_from_args(args)
    snapshot = work / f"{tag}original.fits"
    copyfile(input_path, snapshot)
    result = dict(
        image_path=str(input_path),
        original_image_path=str(snapshot),
        report_saved_to=str(output_path),
        method="Richardson-Lucy gradient descent",
        iterations=iterations,
        psf_size=size,
        psf_method=psf_method,
        regularization="TV",
        alpha=alpha,
        adaptive=adaptive,
        status="evaluating",
        attempts=[],
        psf_selections=[],
        scripts=[],
        layer=layer,
    )

    def save_report() -> None:
        """Écrit l’état courant de l’audit JSON pour conserver les diagnostics d’essai."""
        output_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def run(name: str, lines: Sequence[str]) -> None:
        """Écrit et exécute un script Siril ; lève RuntimeError si l’exécution échoue."""
        name = f"{tag}{name}"
        script = "\n".join(
            [
                "requires 1.4",
                f"cd {_quote(work)}",
                "setext fit",
                "set32bits",
                *lines,
                "close",
                "",
            ]
        )
        result["scripts"].append(
            dict(
                script_path=str(work / f"{name}.sps"),
                log_path=str(work / f"{name}.log"),
            )
        )
        save_report()
        if not runner.run_siril_script(script, str(work), script_name=f"{name}.sps"):
            raise RuntimeError(f"Échec Siril ; consulter {work / f'{name}.log'}")

    def detect(
        source: Path | str,
        label: str,
        profile: str = "Gaussian",
        amplitude: bool = False,
    ) -> Path:
        """Produit un catalogue stellaire Siril sur le canal et le profil demandés."""
        catalog = work / f"{tag}{label}_stars.tsv"
        settings = f"setfindstar -{profile.lower()}"
        if amplitude:
            settings += f" -minA=0.1 -maxA=0.7 -roundness={roundness}"
        run(
            label,
            [
                f"load {_quote(source)}",
                "setfindstar reset",
                settings,
                f"findstar -layer={layer} -out={catalog.name}",
            ],
        )
        return catalog

    def build_psf(
        source: Path | str,
        catalog: Path | str,
        label: str,
        profile: str,
        amplitude_min: float,
    ) -> Path:
        """Sélectionne des étoiles isolées et écrit une PSF FITS avec son aperçu PNG."""
        stars, rejected = read_star_catalog(catalog, profile=profile)
        sheet = work / f"{tag}{label}_selected_stars.fits"
        try:
            selection = select_psf_stars(
                stars,
                source,
                sheet,
                quantile=fine_quantile,
                roundness=roundness,
                min_amplitude=amplitude_min,
                max_amplitude=0.7,
                layer=layer,
            )
        except ValueError:
            stamp_audit = sheet.with_suffix(".json")
            if stamp_audit.exists():
                result["psf_selections"].append(
                    dict(
                        profile=profile,
                        catalog_path=str(catalog),
                        stamp_audit_path=str(stamp_audit),
                        status="insufficient_clean_stars",
                    )
                )
                save_report()
            raise
        selection.update(profile=profile, catalog_path=str(catalog), rejected=rejected)
        result["psf_selections"].append(selection)
        selection_path = work / f"{tag}{label}_selection.json"
        selection_path.write_text(json.dumps(selection, indent=2), encoding="utf-8")
        psf = work / f"{tag}{label}_psf.fits"
        run(
            label + "_psf",
            [
                f"load {_quote(sheet)}",
                "setfindstar reset",
                f"setfindstar -{profile.lower()} -roundness={roundness} -minA={amplitude_min} -maxA=0.7",
                f"findstar -layer=0 -out={tag}{label}_sheet_stars.tsv",
                "makepsf clear",
                f"makepsf stars -ks={size}",
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
        save_report()
        if measured_width > 1.5 * selected_width:
            raise ValueError(
                "PSF générée trop large par rapport aux étoiles sélectionnées"
            )
        return psf

    def record_failure(label: str, error: ValueError | RuntimeError) -> JSONReport:
        """Audite un échec local sans empêcher les autres essais de restauration."""
        attempt = dict(
            number=len(result["attempts"]) + 1,
            label=label,
            status="failed",
            error=str(error),
            quality=dict(accepted=False, artifact_failure=False, reasons=[str(error)]),
        )
        result["attempts"].append(attempt)
        save_report()
        logging.warning(
            "Déconvolution essai %d (%s) échoué : %s",
            attempt["number"], label, error,
        )
        return attempt

    def trial(
        source: Path | str,
        before_catalog: Path | str,
        psf: Path | str,
        step_value: float,
        label: str,
    ) -> JSONReport:
        """Numérote l’essai et conserve ses erreurs comme diagnostics locaux."""
        logging.info(
            "Déconvolution essai %d (%s), pas %.7f",
            len(result["attempts"]) + 1, label, step_value,
        )
        try:
            return evaluate_trial(source, before_catalog, psf, step_value, label)
        except (ValueError, RuntimeError) as error:
            return record_failure(label, error)

    def evaluate_trial(
        source: Path | str,
        before_catalog: Path | str,
        psf: Path | str,
        step_value: float,
        label: str,
    ) -> JSONReport:
        """Exécute un essai au pas demandé et renvoie l’image avec son audit de qualité."""
        candidate = work / f"{tag}{label}.fits"
        after_catalog = work / f"{tag}{label}_stars.tsv"
        run(
            label,
            [
                f"load {_quote(source)}",
                f'rl "-loadpsf={psf}" -iters={iterations} -gdstep={step_value:.7f} -tv -alpha={alpha:g}',
                f"save {candidate.name}",
                "close",
                f"load {candidate.name}",
                "setfindstar reset",
                "setfindstar -gaussian",
                f"findstar -layer={layer} -out={after_catalog.name}",
            ],
        )
        comparison = compare_star_catalogs(
            before_catalog, after_catalog, radius, minimum, fraction, layer
        )
        attempt = dict(
            number=len(result["attempts"]) + 1,
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
                    source, candidate, comparison["pairs"]
                )
                attempt["quality"] = assess_quality(
                    comparison, attempt["artifacts"], **quality_settings
                )
            except ValueError as error:
                attempt["quality"] = dict(
                    accepted=False, artifact_failure=True, reasons=[str(error)]
                )
        else:
            attempt["quality"] = dict(
                accepted=False, artifact_failure=True, reasons=["insufficient_matches"]
            )
        result["attempts"].append(attempt)
        save_report()
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
            if artifacts:
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
        logging.info(
            "Déconvolution %s, pas %.7f : %s", label, step_value, attempt["quality"]
        )
        return attempt

    try:
        before = detect(snapshot, "before")
        accepted = None
        try:
            if psf_method == "blind":
                psf = work / f"{tag}blind_psf.fits"
                run(
                    "blind_psf",
                    [
                        f"load {_quote(snapshot)}",
                        "makepsf clear",
                        f"makepsf blind -l0 -ks={size}",
                        f"makepsf save {psf.name}",
                    ],
                )
                save_psf_preview(psf, psf.with_suffix(".png"))
                result["psf_selections"].append(
                    dict(
                        method="blind_l0",
                        psf_path=str(psf),
                        psf_preview_path=str(psf.with_suffix(".png")),
                    )
                )
                save_report()
            else:
                psf = build_psf(snapshot, before, "simple", "Gaussian", 0.01)
            first = trial(snapshot, before, psf, step, "simple_trial")
            if first["quality"]["accepted"]:
                accepted = first
        except (ValueError, RuntimeError) as error:
            result["simple_psf_error"] = str(error)
            record_failure("simple_psf", error)
        if accepted is None and adaptive:
            # Le tiers de chaque dimension, issu de l'original, jamais d'un essai déconvolué.
            crop = work / f"{tag}central_original.fits"
            with fits.open(snapshot, memmap=False) as hdul:
                height, width = shape[-2:]
                crop_h, crop_w = height // 3, width // 3
                y0, x0 = (height - crop_h) // 2, (width - crop_w) // 2
                data = hdul[0].data[..., y0 : y0 + crop_h, x0 : x0 + crop_w]
                crop_header = fits.Header()
                if "ROWORDER" in hdul[0].header:
                    crop_header["ROWORDER"] = hdul[0].header["ROWORDER"]
                fits.writeto(crop, data, crop_header)
            result["crop"] = dict(
                path=str(crop), x=x0, y=y0, width=crop_w, height=crop_h
            )
            try:
                crop_before = detect(crop, "central_before")
                psf_catalog = detect(crop, "central_moffat", "Moffat", amplitude=True)
                psf = build_psf(crop, psf_catalog, "central_moffat", "Moffat", 0.1)
                best = None
                for step_number in range(4, int(round(max_step * 10000)) + 1):
                    attempt = trial(
                        crop,
                        crop_before,
                        psf,
                        step_number / 10000,
                        f"central_step_{step_number:02d}",
                    )
                    if attempt["quality"]["artifact_failure"]:
                        result["search_stopped_at_step"] = step_number / 10000
                        break
                    if attempt["quality"]["accepted"]:
                        # Dernier pas efficace avant le premier essai excessif.
                        best = attempt
                if best is not None:
                    full = trial(snapshot, before, psf, best["step"], "full_trial")
                    if full["quality"]["accepted"]:
                        accepted = full
            except (ValueError, RuntimeError) as error:
                result["adaptive_psf_error"] = str(error)
                record_failure("central_moffat", error)
        if accepted is None:
            result["status"] = "no_safe_improvement"
            save_report()
            raise RuntimeError(
                f"Aucune déconvolution efficace sans dégradation mesurée ; original conservé, audit : {output_path}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        copyfile(accepted["candidate_image_path"], destination)
        pairs_path = work / f"{tag}matched_stars.csv"
        with pairs_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(
                stream, fieldnames=list(accepted["comparison"]["pairs"][0])
            )
            writer.writeheader()
            writer.writerows(accepted["comparison"]["pairs"])
        result.update(
            status="accepted",
            output_image=str(destination),
            selected_step=accepted["step"],
            psf_path=accepted["psf_path"],
            psf_preview_path=str(Path(accepted["psf_path"]).with_suffix(".png")),
            comparison=accepted["comparison"],
            quality=accepted["quality"],
            artifacts=accepted["artifacts"],
            matched_stars_path=str(pairs_path),
        )
        save_report()
        return result
    except Exception as error:
        if result["status"] == "evaluating":
            result["status"] = "failed"
        result["error"] = str(error)
        save_report()
        raise
