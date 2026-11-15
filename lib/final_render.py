"""Dernière phase facultative : StarNet → étoiles → GHS RANGE → fusion.

Les artefacts sont écrits dans des sous-dossiers visibles et ordonnés.
Un rendu calculé dont les objectifs actifs restent incomplets porte le statut
TARGETS_NOT_REACHED. Une erreur conserve les diagnostics ; la reprise CPU
limitée au manque de mémoire CUDA en mode auto appartient à GHSOptimizer.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import time
from pathlib import Path

import numpy as np
from astropy.io import fits

from lib.auto_ghs import ComputeBackend, GHSOptimizer, HistogramAnalyzer
from lib.ghs_diagnostics import (
    HistogramDiagnostics, atomic_report, histogram_figure,
)
# Compatibilité des lecteurs et tests qui importaient cette fonction ici.
from lib.ghs_diagnostics import ghs_annotation as ghs_annotation
from lib.postprocess_paths import reset_autoghs_work_dir
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
from lib.stretch_branches import StarStretchProcessor, StarlessStretchProcessor
from lib.star_separation import (
    StarNetSeparator,
    validate_linear_header,
    validate_rgb,
    write_rgb,
)
from lib.type_defs import JSONReport

LOGGER = logging.getLogger(__name__)


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
        return (StarNetSeparator, StarStretchProcessor, StarlessStretchProcessor, HistogramDiagnostics)

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
        le fond StarNet brut peut dépasser 1 si son résidu signé reconstitue
        une entrée bornée. Aucun écrêtage indépendant des couches n'est fait ;
        une incohérence de géométrie ou de domaine reste une erreur ValueError.
        Voir docs/scripts/postProcess/star-rendering.md.
        """
        if not np.isfinite(tolerance) or not 0 <= tolerance <= 0.05:
            raise ValueError("ghs-negative-tolerance doit être dans [0,.05]")
        if starless.shape != stars.shape or not np.isfinite(stars).all():
            raise ValueError("Résidu StarNet incompatible")
        validate_rgb(starless, allow_upper_overshoot=True)
        validate_rgb(starless + stars)
        negative = np.minimum(stars, 0)
        background, positive = starless + negative, stars - negative
        validate_rgb(background)
        validate_rgb(positive)
        minimum = float(stars.min())
        exceeded_count = int(np.count_nonzero(stars < -tolerance))
        above_one_count = int(np.count_nonzero(starless > 1))
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
                "raw_starless_above_one_count": above_one_count,
                "raw_starless_maximum": float(starless.max()),
                "status": "CORRECTED" if exceeded_count or above_one_count else "SUCCESS",
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

        La séparation, les deux branches et la recomposition sont écrites directement
        dans le parent du rapport, sans enveloppe autoghs_work. Seuls leurs
        produits et l'ancienne enveloppe sont nettoyés ; une entrée située
        dans ces dossiers est refusée avant toute suppression.

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
            output_image et output_jpeg référencent le rendu RANGE. Le statut
            TARGETS_NOT_REACHED signale les objectifs actifs incomplets ; les
            exports restent disponibles et unmet_targets décrit ces objectifs.
            Les cartes
            output_images/output_jpegs gardent une seule entrée range pour les
            lecteurs de rapports existants. Les couches et leurs diagnostics sont
            publiés séparément dans 03_ghs_stars et 03_ghs_starless avant la
            recomposition ; chacune possède sa propre galerie histogram_trials.

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
                options.compute_backend,
                required_bytes=GHSOptimizer.estimate_working_bytes(
                    data.shape, options.ghs_quartile_block_pixels,
                ),
            )
            report_path.parent.mkdir(parents=True, exist_ok=True)
            work_root = reset_autoghs_work_dir(report_path, source)
            separation_dir = work_root / "01_separation"
            stars_dir = work_root / "03_ghs_stars"
            ghs_dir = work_root / "03_ghs_starless"
            recomposition_dir = work_root / "04_recomposition"
            for directory in (separation_dir, stars_dir, ghs_dir,
                              recomposition_dir):
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
            stars, star_report = StarStretchProcessor().process(
                positive, source, stars_dir / 'stars.json', backend, args,
            )
            stars_path = Path(star_report['output_image'])
            stars_jpeg = Path(star_report['output_jpeg'])
            stars_diagnostic = Path(star_report['diagnostic_path'])
            star_histogram_path = Path(star_report['histogram_path'])
            star_positions = stellar_peak_positions(positive)
            stretched, branch = StarlessStretchProcessor().process(
                background, source, ghs_dir / 'starless.json', backend, args,
            )
            starless_path = Path(branch['output_image'])
            histogram_path = Path(branch['histogram_path'])
            series = {key: branch[key] for key in (
                'histogram_series', 'histogram_gallery', 'histogram_series_manifest',
                'selected_trial_index', 'selected_trial_stage', 'trial_jpegs_enabled',
            )}
            analyzer = HistogramAnalyzer(
                options.ghs_bins, options.ghs_q_low, options.ghs_q_high,
                options.ghs_color_mode,
            )
            cpu = ComputeBackend('cpu')
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
            recomposed_histogram_path = (
                recomposition_dir / f"{source.stem}_ghs_histograms_with_stars.png"
            )
            histogram_figure(branch, recomposed_histogram_path, with_stars=True)
            digest = hashlib.sha256()
            with source.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            fingerprint = digest.hexdigest()
            result = {
                "status": branch["status"],
                "targets_achieved": branch.get('quartile_stretch', {}).get('targets_achieved'),
                "unmet_targets": branch.get('quartile_stretch', {}).get('unmet_targets', []),
                "algorithm": "AutoGHS v1; experimental histogram heuristics",
                "implementation_sha256": {
                    module: hashlib.sha256(
                        Path(__file__).with_name(module).read_bytes()
                    ).hexdigest()
                    for module in (
                        "auto_ghs.py",
                        "ghs_final_stretch.py",
                        "ghs_quartiles.py",
                        "ghs_log_render.py",
                        "ghs_log_histogram.py",
                        "ghs_linear_histogram.py",
                        "ghs_histogram_shape.py",
                        "ghs_render_quality.py",
                        "ghs_diagnostics.py",
                        "ghs_blackpoint.py",
                        "final_render.py",
                        "stretch_branches.py",
                        "stretch_transfer.py",
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
                    "estimated_working_bytes": backend.required_bytes,
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
                    "stars": str(star_histogram_path),
                    "starless": str(histogram_path),
                    "with_stars": str(recomposed_histogram_path),
                },
                **series,
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
                    "ghs_save_trial_jpegs": bool(getattr(args, 'ghs_save_trial_jpegs', False)),
                },
                "duration_seconds": time.monotonic() - started,
            }
            atomic_report(separation_dir / "separation.json", result["separation"])
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
