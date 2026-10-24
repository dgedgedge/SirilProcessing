"""Dernière phase facultative : StarNet → étoiles → GHS RANGE → fusion.

Les artefacts sont écrits dans des sous-dossiers visibles et ordonnés.
Le rapport SUCCESS est écrit uniquement après réussite de tous les rendus.
Une erreur conserve les diagnostics, sans reprise CPU ni rendu de substitution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import textwrap
import time
from pathlib import Path

import numpy as np
from astropy.io import fits

from lib.auto_ghs import ComputeBackend, GHSOptimizer, HistogramAnalyzer
from lib.postprocess_paths import reset_ordered_work_dir
from lib.render_exports import write_render_jpeg
from lib.processor import processor
from lib.restoration_quality import normalized_planes
from lib.star_rendering import (
    ConservativeStarRenderer,
    clipping_metrics,
    color_drift,
    peak_visibility,
    recompose,
    stellar_peak_positions,
)
from lib.star_diagnostics import write_star_diagnostic
from lib.star_separation import (
    StarNetSeparator,
    validate_linear_header,
    validate_rgb,
    write_rgb,
)
from lib.type_defs import JSONReport

LOGGER = logging.getLogger(__name__)


def atomic_report(path: Path, report: JSONReport) -> None:
    """Remplace un rapport JSON seulement après une sérialisation complète et finie."""
    temporary = path.with_name(path.name + ".pending")
    try:
        payload = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)
        temporary.write_text(payload + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def ghs_annotation(report: JSONReport) -> str:
    """Résume l'objectif et les recherches GHS consignées dans le rapport.

    Les recherches comptent les passages ayant évalué des candidats ; un arrêt
    préalable sur distribution constante ou pic ambigu n'ajoute aucun passage.
    Les applications acceptées et les corrections BP sont comptées séparément.
    Les métriques décrivent le fond optimisé avant fusion, même sur le PNG avec
    étoiles. Le GHS final explicite est distingué des recherches sous contraintes.
    Une information absente est affichée comme non renseignée.
    """
    lines = ["OBJECTIF", textwrap.fill(
        "Élargir l’étendue robuste des intensités entre les quantiles "
        "configurés, sous contraintes de bruit et d’écrêtage.", width=38
    ), "", "BOUCLE GHS — FOND SANS ÉTOILES"]
    records = report.get("iterations")
    if records is None:
        lines.append("Itérations : non renseignées")
    else:
        accepted = [item for item in records if item.get("accepted") is not None]
        limit = report.get("parameters", {}).get("ghs_max_iterations", "?")
        blackpoint_records = report.get("blackpoint_iterations", records)
        blackpoint_count = sum(item.get("BP", 0) > 0 for item in blackpoint_records)
        lines.extend([
            f"Recherches effectuées : {len(records)} / {limit}",
            f"GHS appliqués : {len(accepted)}",
            f"Recherches sans application : {len(records) - len(accepted)}",
            "Candidats examinés : "
            f"{sum(len(item.get('candidates', [])) for item in records)}",
            f"Corrections du point noir : {blackpoint_count}",
        ])
    balance = report.get("histogram_balance")
    if balance is not None:
        lines.extend([
            "", "ÉGALISATION PARTIELLE",
            f"Force : {balance['strength']:.2f} — plafond : {balance['clip_limit']:.1f}",
        ])
    dynamic = report.get("dynamic_range")
    if dynamic is not None:
        lines.extend([
            "", "BOUCLE D’EXPANSION RGB",
            f"Passages : {len(dynamic['iterations'])} / {dynamic['max_iterations']}",
            f"Étendue : {dynamic['initial_width']:.3f} → {dynamic['final_width']:.3f}",
            f"Cible : {dynamic['target']:.2f} — "
            + ("atteinte" if dynamic['target_reached'] else "non atteinte"),
        ])
    presentation = report.get("presentation_stretch")
    if presentation is not None:
        lines.extend([
            "", "GHS FINAL — SANS ÉTOILES",
            f"D : {presentation['parameters']['D']:.2f} — "
            + {"applied": "appliqué", "disabled": "désactivé",
               "constant distribution": "distribution constante"}.get(
                presentation["status"], presentation["status"],
            ),
        ])
        if presentation.get("mode") == "auto":
            parameters = presentation["parameters"]
            lines.extend([
                f"B : {parameters['B']:.2f} — SP : {parameters['SP']:.4f}",
                f"HP : {parameters['HP']:.4f}",
            ])
    reasons = {
        "max_iterations": "limite d’itérations atteinte",
        "constant distribution": "distribution constante",
        "no admissible improvement": "aucune amélioration admissible",
    }
    reason = report.get("stop_reason", "non renseigné")
    lines.extend(["", "Arrêt GHS :", textwrap.fill(reasons.get(reason, reason), 38)])
    metric = "range_width"
    initial = report.get("initial", {}).get(metric)
    final = report.get("final", {}).get(metric)
    if initial is not None and final is not None:
        lines.extend(["", "Largeur du critère (intensité 0–1)",
                      f"{initial:.5f} → {final:.5f}"])
        if initial > 0:
            lines.append(f"Gain relatif : {100 * (final / initial - 1):+.1f} %")
    return "\n".join(lines)


def histogram_figure(
    report: JSONReport, destination: Path, *, with_stars: bool = False
) -> None:
    """Trace les effectifs avec axes Y linéaire et logarithmique, X linéaire.

    Les classes vides sont masquées dans la vue logarithmique, sans ajouter
    de pseudo-effectif. Le log1p utilisé par l'analyse GHS reste indépendant
    de cette présentation. Une distribution entièrement vide garde un axe
    log valide de 1 à 10 pixels.
    Les deux panneaux numérotés identifient l'objectif RANGE et l'échelle. Une
    légende commune distingue les étapes sans répéter les mêmes libellés.
    Avec ``with_stars=True``, seuls les histogrammes finaux recomposés sont
    tracés ; sinon, la figure contient uniquement les états sans étoiles.
    Un encart rappelle l’objectif et les statistiques de boucle
    du fond avant fusion. Le PNG est écrit dans ``destination``.
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(16, 6.5), constrained_layout=True)
    figure.suptitle(
        "Images finales avec étoiles recomposées"
        if with_stars else "Évolution de l’image sans étoiles"
    )
    FigureCanvasAgg(figure)
    grid = figure.add_gridspec(1, 3, width_ratios=(1, 1, 0.8))
    axes = [figure.add_subplot(grid[0, column]) for column in range(2)]
    legend_handles = {}
    states = report["histograms"]
    selection = sorted({0, len(states) // 2, len(states) - 1})
    curves = []
    for index in ([] if with_stars else selection):
        if index == 0:
            label = "Sans étoiles — entrée linéaire"
            color = "C0"
        elif index == len(states) - 1:
            label = "Sans étoiles — rendu final"
            color = "C2"
        else:
            label = "Sans étoiles — étape intermédiaire"
            color = "C1"
        curves.append((states[index]["counts"], label, color))
    if with_stars:
        curves.append((
            report["recomposed_histogram"]["counts"],
            "Image finale — étoiles recomposées", "C3",
        ))
    maximum = 0.0
    for raw_counts, label, color in curves:
        counts = np.asarray(raw_counts)
        maximum = max(maximum, float(np.max(counts)))
        x = (np.arange(len(counts)) + 0.5) / len(counts)
        line, = axes[0].plot(x, counts, label=label, color=color)
        legend_handles[label] = line
        axes[1].plot(
            x, np.ma.masked_less_equal(counts, 0), label=label, color=color
        )
    axes[1].set_ylim(1, max(10, maximum * 1.2))
    axes[1].set_yscale("log")
    for column, axis in enumerate(axes):
        axis.set_title(
            f"{column + 1}. RANGE — étendue globale\n"
            "Effectifs en échelle "
            + ("linéaire" if column == 0 else "logarithmique")
        )
        axis.set_xlabel("Intensité (linéaire sur cet axe)")
        axis.set_xlim(0, 1)
        axis.set_ylabel("Nombre de pixels")
    annotation = figure.add_subplot(grid[0, 2])
    annotation.set_axis_off()
    annotation.text(
        0.03, 0.98, ghs_annotation(report),
        transform=annotation.transAxes, va="top", fontsize=9,
        bbox={"boxstyle": "round,pad=0.7", "facecolor": "#f3f5f7",
              "edgecolor": "#c5cbd1"},
    )
    figure.legend(
        handles=list(legend_handles.values()),
        loc="outside lower center", ncol=2, fontsize=9,
    )
    figure.savefig(destination, dpi=130)
    figure.clear()


class AutoGHSFinalRenderer(processor):
    """Orchestre le rendu final au point de sortie linéaire du pipeline existant.

    Activé par défaut ; --disable-autoghs conserve une sortie linéaire. Les enfants
    déclarent leurs propres options et Config assure leur rémanence explicite.
    Un seul fond RANGE est optimisé puis recomposé avec les étoiles rendues.
    Les JPEG accompagnent les FITS et les histogrammes diagnostiquent le rendu.
    Aucun comparatif de stratégies ou d’exécutions précédentes n’est généré.
    """

    enabled_by_default = True
    config_path_parameters = StarNetSeparator.config_path_parameters

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie la phase finale et ses rapports dans la séquence standard."""
        return "autoghs"

    @classmethod
    def parameter_children(cls) -> tuple[type[processor], ...]:
        """Déclare les moteurs de séparation, d'étoiles et de GHS indépendants."""
        return (StarNetSeparator, ConservativeStarRenderer, GHSOptimizer)

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Réunit les paramètres des enfants et les limites de fusion/résidus."""
        for child in cls.parameter_children():
            child.add_arguments(parser)
        parser.add_argument(
            "--ghs-negative-tolerance",
            type=float,
            default=0.005,
            help=(
                "Seuil d’alerte des résidus négatifs, en unités 0..1 ; "
                "compensation conservant la somme linéaire"
            ),
        )
        parser.add_argument(
            "--ghs-headroom",
            type=float,
            default=0.95,
            help="Facteur commun du fond avant fusion screen",
        )

    @staticmethod
    def compensate_residual(
        starless: np.ndarray, stars: np.ndarray, tolerance: float
    ) -> tuple[np.ndarray, np.ndarray, JSONReport]:
        """Transfère les résidus négatifs dans le fond en conservant la somme.

        Fond corrigé = fond + min(étoiles,0), étoiles corrigées = max(étoiles,0).
        Lorsque StarNet a surestimé le fond, celui-ci revient ainsi à l’intensité
        originale et la contribution stellaire devient nulle à cet endroit.
        tolerance, finie dans [0,.05], est un seuil d’alerte diagnostique : un
        dépassement est consigné sans invalider une décomposition corrigeable.
        Les branches corrigées doivent être RGB, finies et bornées sur [0,1] ;
        une incohérence de géométrie ou de domaine reste une erreur ValueError.
        Voir docs/scripts/postProcess/star-rendering.md.
        """
        if not np.isfinite(tolerance) or not 0 <= tolerance <= 0.05:
            raise ValueError("ghs-negative-tolerance doit être dans [0,.05]")
        if starless.shape != stars.shape or not np.isfinite(stars).all():
            raise ValueError("Résidu StarNet incompatible")
        validate_rgb(starless)
        negative = np.minimum(stars, 0)
        background, positive = starless + negative, stars - negative
        validate_rgb(background)
        validate_rgb(positive)
        minimum = float(stars.min())
        exceeded_count = int(np.count_nonzero(stars < -tolerance))
        if exceeded_count:
            LOGGER.warning(
                "StarNet : %d valeur(s) RGB (%.6f %%) sous -%g, minimum=%g ; "
                "fond et étoiles compensés en conservant la somme linéaire",
                exceeded_count, 100 * exceeded_count / stars.size,
                tolerance, minimum,
            )
        return (
            background,
            positive,
            {
                "method": "flux-conserving nonnegative residual compensation",
                "status": "CORRECTED" if exceeded_count else "SUCCESS",
                "tolerance": tolerance,
                "tolerance_exceeded": bool(exceeded_count),
                "above_tolerance_count": exceeded_count,
                "above_tolerance_fraction": exceeded_count / stars.size,
                "negative_fraction": float(np.mean(stars < 0)),
                "minimum": minimum,
                "transferred_absolute_flux": float(-negative.sum(dtype=np.float64)),
                "sum_preserved_max_abs": float(
                    np.max(
                        abs(
                            (
                                background.astype(np.float64)
                                + positive.astype(np.float64)
                            )
                            - (starless.astype(np.float64) + stars.astype(np.float64))
                        )
                    )
                ),
            },
        )

    def post_process(
        self,
        input_path: Path,
        output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Publie un rendu RANGE étoilé, ses couches et ses diagnostics.

        Parameters
        ----------
        input_path : Path
            Image RGB linéaire étoilée issue de la dernière étape amont.
        output_path : Path
            Rapport de cette étape dans le dossier d'artefacts existant.
        args : argparse.Namespace, optional
            Paramètres résolus avec priorité CLI > JSON > défauts des moteurs.

        Returns
        -------
        dict
            output_image et output_jpeg référencent le rendu RANGE. Les cartes
            output_images/output_jpegs gardent une seule entrée range pour les
            lecteurs de rapports existants.

        Raises
        ------
        ValueError, RuntimeError, OSError
            Toute entrée invalide, panne moteur ou écriture échouée. Un
            rapport ERROR conserve les chemins des produits et journaux si possible.
        """
        source, report_path = Path(input_path).resolve(), Path(output_path).resolve()
        if source == report_path:
            raise ValueError("Le rapport AutoGHS ne peut pas remplacer la source")
        work_root: Path | None = None
        started = time.monotonic()
        try:
            args = self._get_args(args)
            options = GHSOptimizer.options(args)
            data = normalized_planes(source)
            validate_rgb(data)
            header = fits.getheader(source)
            validate_linear_header(header)
            headroom = getattr(args, "ghs_headroom", 0.95)
            max_color_drift = getattr(args, "star_max_color_drift", 0.08)
            max_chroma_excess = getattr(args, "star_max_chroma_excess", 0.10)
            if not np.isfinite(headroom) or not 0 < headroom < 1:
                raise ValueError("ghs-headroom doit être dans ]0,1[")
            if (
                not np.isfinite([max_color_drift, max_chroma_excess]).all()
                or not 0 <= max_color_drift <= 1
                or not 0 <= max_chroma_excess <= 1
            ):
                raise ValueError("Limites de couleur stellaire hors de [0,1]")
            # Réserver le budget avant StarNet ; ensuite aucune bascule de moteur.
            backend = ComputeBackend(
                options.compute_backend, required_bytes=data.nbytes * 16
            )
            report_path.parent.mkdir(parents=True, exist_ok=True)
            parent_work = report_path.parent / f"{report_path.stem}_work"
            if source.is_relative_to(parent_work):
                raise ValueError(
                    "L’entrée ne peut pas se trouver dans le dossier réinitialisé"
                )
            separation_dir = reset_ordered_work_dir(report_path)
            work_root = separation_dir.parent
            stars_dir = work_root / "002_stars"
            ghs_dir = work_root / "003_ghs"
            recomposition_dir = work_root / "004_recomposition"
            diagnostics_dir = work_root / "005_diagnostics"
            for directory in (stars_dir, ghs_dir, recomposition_dir, diagnostics_dir):
                directory.mkdir()
            separation = StarNetSeparator().post_process(
                source, separation_dir / "separation.json", args
            )
            starless = normalized_planes(separation["starless_linear"])
            residual = normalized_planes(separation["stars_linear"])
            background, positive, compensation = self.compensate_residual(
                starless, residual, getattr(args, "ghs_negative_tolerance", 0.005)
            )
            if not np.any(positive > 0):
                raise ValueError(
                    "StarNet n’a extrait aucune contribution stellaire positive"
                )
            # Les étoiles suivent leur propre rendu avant la fusion avec RANGE.
            stars, star_report = ConservativeStarRenderer.render(
                positive,
                getattr(args, "star_strength", 2.0),
                getattr(args, "star_ceiling", 0.85),
                getattr(
                    args, "star_max_width_ratio",
                    ConservativeStarRenderer.default_max_width_ratio,
                ),
                getattr(
                    args, "star_faint_boost", ConservativeStarRenderer.default_faint_boost
                ),
                getattr(
                    args, "star_histogram_strength",
                    ConservativeStarRenderer.default_histogram_strength,
                ),
            )
            stars_path = stars_dir / f"{source.stem}_stars_render.fits"
            LOGGER.info(
                "Étoiles : %d pics candidats au-dessus du contraste 0,03 après rendu "
                "(%d avec le gain fixe) ; adaptation %.3f",
                star_report["visibility"]["above_threshold"],
                star_report["histogram_balance"]["fixed_gain"]["above_threshold"],
                star_report["histogram_balance"]["accepted_strength"],
            )
            write_rgb(source, stars_path, stars, nonlinear=True, role="STARS")
            stars_jpeg = stars_path.with_suffix(".jpg")
            write_render_jpeg(stars_path, stars_jpeg)
            stars_diagnostic = stars_dir / f"{source.stem}_stars_diagnostic.png"
            write_star_diagnostic(
                positive, stars, star_report, stars_diagnostic,
                row_order=header.get("ROWORDER", "BOTTOM-UP"),
            )
            star_positions = stellar_peak_positions(positive)
            optimizer = GHSOptimizer()
            analyzer = HistogramAnalyzer(
                options.ghs_bins,
                options.ghs_q_low,
                options.ghs_q_high,
                options.ghs_color_mode,
            )
            # Histograms CPU des sorties finales ; le calcul GHS conserve ses tableaux GPU.
            cpu = ComputeBackend("cpu")
            stretched, branch = optimizer.optimize(
                background, "range", args, backend,
                rendered_stars=stars, headroom=headroom,
            )
            starless_path = ghs_dir / f"{source.stem}_starless_ghs_range.fits"
            write_rgb(
                source, starless_path, stretched, nonlinear=True, role="STARLESS"
            )
            final, fusion = recompose(stretched, stars, headroom)
            fusion["peak_contrast"] = peak_visibility(
                final - headroom * stretched, star_positions,
            )
            profile_ratio = fusion["star_profiles_after_fusion"]["median_ratio"]
            if profile_ratio is not None and profile_ratio > getattr(
                args, "star_max_width_ratio",
                ConservativeStarRenderer.default_max_width_ratio,
            ):
                raise ValueError("Recomposition : gonflement stellaire excessif")
            color = fusion["star_color_after_fusion"]
            if (
                color["chromaticity_mean_abs"] > max_color_drift
                or color["chroma_mean_change"] > max_chroma_excess
            ):
                raise ValueError(
                    "Recomposition : dérive de couleur stellaire excessive"
                )
            final_path = recomposition_dir / f"{source.stem}_render_range.fits"
            write_rgb(source, final_path, final, nonlinear=True, role="WITHSTARS")
            # Relire les artefacts réellement écrits, pas seulement les tableaux mémoire.
            disk = normalized_planes(final_path)
            validate_rgb(disk)
            if disk.shape != data.shape or not fits.getheader(final_path).get(
                "SPNLIN"
            ):
                raise RuntimeError("FITS final invalide après écriture")
            jpeg_path = final_path.with_suffix(".jpg")
            write_render_jpeg(final_path, jpeg_path)
            branch.update(
                {
                    "output_image": str(final_path),
                    "output_jpeg": str(jpeg_path),
                    "starless_image": str(starless_path),
                    "recomposition": fusion,
                    "recomposed_histogram": analyzer.analyze(final, cpu).report(),
                    "color_change_from_linear": color_drift(data, final),
                }
            )
            rendered = {"range": branch}
            star_report["recomposed_visibility"] = {"range": fusion["peak_contrast"]}
            write_star_diagnostic(
                positive, stars, star_report, stars_diagnostic,
                row_order=header.get("ROWORDER", "BOTTOM-UP"),
            )
            histogram_path = diagnostics_dir / f"{source.stem}_ghs_histograms.png"
            histogram_figure(branch, histogram_path)
            recomposed_histogram_path = (
                diagnostics_dir / f"{source.stem}_ghs_histograms_with_stars.png"
            )
            histogram_figure(branch, recomposed_histogram_path, with_stars=True)
            digest = hashlib.sha256()
            with source.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            fingerprint = digest.hexdigest()
            result = {
                "status": (
                    "SUCCESS"
                    if branch["status"] == "SUCCESS"
                    else "NO_IMPROVEMENT"
                ),
                "algorithm": "AutoGHS v1; experimental histogram heuristics",
                "implementation_sha256": {
                    module: hashlib.sha256(
                        Path(__file__).with_name(module).read_bytes()
                    ).hexdigest()
                    for module in (
                        "auto_ghs.py",
                        "ghs_final_stretch.py",
                        "ghs_log_histogram.py",
                        "final_render.py",
                        "render_exports.py",
                        "star_rendering.py",
                        "star_separation.py",
                    )
                },
                "image_path": str(source),
                "source_sha256": fingerprint,
                "input_linear": True,
                "output_linear": False,
                "shape": list(data.shape),
                "input_statistics": clipping_metrics(data),
                "numpy_version": np.__version__,
                "backend": {
                    "requested": options.compute_backend,
                    "selected": backend.name,
                    "reason": backend.reason,
                    **backend.device,
                },
                "separation": separation,
                "residual_compensation": compensation,
                "stars": {
                    **star_report, "output_image": str(stars_path),
                    "output_jpeg": str(stars_jpeg),
                    "diagnostic_path": str(stars_diagnostic),
                },
                "strategies": rendered,
                "output_images": {"range": str(final_path)},
                "output_jpegs": {"range": str(jpeg_path)},
                "output_jpeg": str(jpeg_path),
                "output_image": str(final_path),
                "histogram_path": str(histogram_path),
                "histogram_paths": {
                    "starless": str(histogram_path),
                    "with_stars": str(recomposed_histogram_path),
                },
                "parameters": {
                    **vars(options),
                    "starnet_path": separation["executable"],
                    "star_render": getattr(args, "star_render", "conservative"),
                    "star_strength": getattr(args, "star_strength", 2.0),
                    "star_faint_boost": getattr(
                        args, "star_faint_boost",
                        ConservativeStarRenderer.default_faint_boost,
                    ),
                    "star_ceiling": getattr(args, "star_ceiling", 0.85),
                    "star_histogram_strength": getattr(
                        args, "star_histogram_strength",
                        ConservativeStarRenderer.default_histogram_strength,
                    ),
                    "star_max_width_ratio": getattr(
                        args, "star_max_width_ratio",
                        ConservativeStarRenderer.default_max_width_ratio,
                    ),
                    "star_max_color_drift": max_color_drift,
                    "star_max_chroma_excess": max_chroma_excess,
                    "ghs_headroom": headroom,
                    "ghs_negative_tolerance": getattr(
                        args, "ghs_negative_tolerance", 0.005
                    ),
                },
                "duration_seconds": time.monotonic() - started,
            }
            atomic_report(separation_dir / "separation.json", result["separation"])
            atomic_report(diagnostics_dir / f"{source.stem}_ghs_report.json", result)
            atomic_report(report_path, result)
            LOGGER.info("AutoGHS : rendus finaux écrits dans %s", work_root)
            return result
        except Exception as error:
            failure = {
                "status": "ERROR",
                "image_path": str(source),
                "error": str(error),
                "work_directory": str(work_root) if work_root is not None else None,
                "no_automatic_retry": True,
            }
            try:
                report_path.parent.mkdir(parents=True, exist_ok=True)
                atomic_report(report_path, failure)
            except OSError:
                LOGGER.exception("Impossible d’écrire le rapport d’échec AutoGHS")
            raise
