"""Détection Siril, mesures locales et comparaison stellaire sous forme de processeurs.

Les catalogues et les images sources sont conservés ; seuls les catalogues
explicitement demandés et les rapports optionnels sont écrits. Voir
``docs/scripts/postProcess/quality-processors.md``.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
import logging
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from lib.quality_processors import QualityContext, QualityProcessor
from lib.siril import Siril
from lib.type_defs import JSONReport, StarMeasurement


class ReferenceStarMeasurementProcessor(QualityProcessor):
    """Ajuste les mêmes profils gaussiens avant/après aux positions initiales."""

    stage_key = 'reference_stars'
    stage_label = 'Qualité — ajustements stellaires aux positions initiales'

    CONFIG_DEFAULTS = {'deconvolution_reference_measurements': True}

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare les options de ce contrôle après celles de ses enfants."""
        super().add_arguments(parser)
        if not cls.has_argument(parser, 'deconvolution_reference_measurements'):
            parser.add_argument(
                "--deconvolution-reference-measurements", action=argparse.BooleanOptionalAction,
                default=cls.CONFIG_DEFAULTS['deconvolution_reference_measurements'],
                help="Mesurer les mêmes étoiles aux positions initiales (SNR ≥ 10)",
            )

    def __init__(
        self, context: QualityContext, stars: list[StarMeasurement], layer: int, radius: float,
    ) -> None:
        """Attache le catalogue initial, le canal et le rayon d'appariement en pixels."""
        super().__init__(context)
        self.stars, self.layer, self.radius = stars, layer, radius

    def _process(self, args: argparse.Namespace) -> JSONReport:
        """Mesure les références isolées et les candidats sans nouvelle sélection après."""
        from lib.reference_stars import image_plane, measure_star, MIN_REFERENCE_SNR

        source, candidate = self.context.source, self.context.candidate_path
        stars, layer, radius = self.stars, self.layer, self.radius
        before_plane, after_plane = image_plane(source, layer), image_plane(candidate, layer)
        if before_plane.shape != after_plane.shape:
            raise ValueError('Dimensions différentes pour la mesure aux positions initiales')
        tree = cKDTree([[star['x'], star['y']] for star in stars]) if stars else None
        reliable, measured_after, audits = [], [], []
        for star in stars:
            neighbors = tree.query_ball_point([star['x'], star['y']], 3*star['fwhm'])
            if len(neighbors) > 1:
                audits.append(dict(reference_id=star['id'], stage='before', reason='neighbor_contamination'))
                continue
            measured, audit = measure_star(before_plane, star, radius)
            audit['stage'] = 'before'
            audits.append(audit)
            if measured is None:
                continue
            if audit['snr'] < MIN_REFERENCE_SNR:
                audit['reason'] = 'low_reference_snr'
                continue
            reliable.append(measured)
            after, after_audit = measure_star(after_plane, measured, radius)
            after_audit['stage'] = 'after'
            audits.append(after_audit)
            if after is not None and after_audit['snr'] >= MIN_REFERENCE_SNR:
                measured_after.append(after)
            elif after is not None:
                after_audit['reason'] = 'low_candidate_snr'
        summary = Counter(f"{entry['stage']}:{entry['reason']}" for entry in audits)
        return dict(before=reliable, after=measured_after, audit=dict(
            failure_summary=dict(summary),
            method='gaussian_at_reference_positions', initial_count=len(stars),
            reliable_reference_count=len(reliable), measured_after_count=len(measured_after),
            eligible_reference_ids=[star['id'] for star in reliable],
            min_reference_snr=MIN_REFERENCE_SNR, measurements=audits,
        ))


class StarComparisonProcessor(QualityProcessor):
    """Compare les mesures stellaires et contrôle la cohorte représentative."""

    stage_key = 'star_comparison'
    stage_label = 'Qualité — appariement et critères stellaires'

    CONFIG_DEFAULTS = {
        'deconvolution_match_radius': 1.0, 'deconvolution_min_stars': 10,
        'deconvolution_min_match_fraction': .7,
        'deconvolution_force_manual_matching': False,
    }

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare les options de ce contrôle après celles de ses enfants."""
        super().add_arguments(parser)
        if not cls.has_argument(parser, 'deconvolution_match_radius'):
            parser.add_argument(
                "--deconvolution-match-radius",
                type=float,
                default=cls.CONFIG_DEFAULTS['deconvolution_match_radius'],
                help="Distance maximale de correspondance, en pixels",
            )
        if not cls.has_argument(parser, 'deconvolution_min_stars'):
            parser.add_argument("--deconvolution-min-stars", type=int, default=cls.CONFIG_DEFAULTS['deconvolution_min_stars'])
        if not cls.has_argument(parser, 'deconvolution_min_match_fraction'):
            parser.add_argument(
                "--deconvolution-min-match-fraction",
                type=float,
                default=cls.CONFIG_DEFAULTS['deconvolution_min_match_fraction'],
                help="Fraction minimale des étoiles valides avant retrouvées après",
            )
        if not cls.has_argument(parser, 'deconvolution_force_manual_matching'):
            parser.add_argument(
                "--deconvolution-force-manual-matching",
                action="store_true", default=cls.CONFIG_DEFAULTS['deconvolution_force_manual_matching'],
                help=(
                    "Forcer les seuils saisis (--deconvolution-match-radius, "
                    "--deconvolution-min-stars, --deconvolution-min-match-fraction) "
                    "au lieu du mode auto adaptatif"
                ),
            )

    def __init__(
        self, context: QualityContext, before_catalog: Path, after_catalog: Path,
        radius: float, min_stars: int, min_fraction: float, layer: int,
        *, reference_images: bool = True, quality_mode: str = 'reliable',
    ) -> None:
        """Configure les catalogues, seuils d'appariement et mode de mesure commun."""
        super().__init__(context)
        self.before_catalog, self.after_catalog = before_catalog, after_catalog
        self.radius, self.min_stars, self.min_fraction = radius, min_stars, min_fraction
        self.layer, self.reference_images, self.quality_mode = layer, reference_images, quality_mode

    @classmethod
    def parameter_children(cls) -> tuple[type[QualityProcessor], ...]:
        """Déclare la mesure locale guidée par la référence."""
        return (ReferenceStarMeasurementProcessor,)

    def _process(self, args: argparse.Namespace) -> JSONReport:
        """Apparie et compare les étoiles avec les règles scientifiques historiques."""
        from lib.deconvolution import read_star_catalog

        before_path, after_path = self.before_catalog, self.after_catalog
        radius, min_stars, min_fraction = self.radius, self.min_stars, self.min_fraction
        layer, quality_mode = self.layer, self.quality_mode
        reference_images = (
            (self.context.source, self.context.candidate_path) if self.reference_images else None
        )
        before, rejected_before = read_star_catalog(before_path)
        after, rejected_after = read_star_catalog(after_path)
        reference_audit = None
        independent_after_count = len(after)
        if reference_images is not None:
            measurement = self.run_child(ReferenceStarMeasurementProcessor(
                self.context, before, layer, radius,
            ), args)
            before, after = measurement['before'], measurement['after']
            reference_audit = measurement['audit']
            reference_audit['processing'] = measurement['processing']
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


class SirilStarDetectionProcessor(QualityProcessor):
    """Produit un ou plusieurs catalogues dans un seul appel Siril mesuré."""

    stage_key = 'star_detection'
    stage_label = 'Qualité — détection stellaire Siril'

    CONFIG_DEFAULTS = {'deconvolution_layer': None}

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare les options de ce contrôle après celles de ses enfants."""
        super().add_arguments(parser)
        if not cls.has_argument(parser, 'deconvolution_layer'):
            parser.add_argument(
                "--deconvolution-layer",
                type=int,
                choices=[0, 1, 2],
                default=cls.CONFIG_DEFAULTS['deconvolution_layer'],
                help="Canal des mesures avant/après : défaut vert RGB (1), mono (0)",
            )

    def __init__(
        self, context: QualityContext, images: Sequence[tuple[Path, Path]],
        work_dir: Path, layer: int, *, runner: Siril | None = None,
        script_name: str = 'quality.sps', findstar_settings: str = '-gaussian',
    ) -> None:
        """Déclare les couples image/catalogue, le canal et le service Siril injectable."""
        super().__init__(context)
        self.images, self.work_dir, self.layer = tuple(images), Path(work_dir), layer
        self.runner, self.script_name = runner, script_name
        self.findstar_settings = findstar_settings

    def _process(self, args: argparse.Namespace) -> JSONReport:
        """Exécute setfindstar/findstar sans modifier les FITS ni leurs métadonnées."""
        from lib.deconvolution import _quote

        lines = [f'cd {_quote(self.work_dir)}', 'set32bits']
        for image, catalog in self.images:
            lines += [
                f'load {_quote(image)}', 'setfindstar reset',
                f'setfindstar {self.findstar_settings}',
                f'findstar -layer={self.layer} -out={catalog.name}', 'close',
            ]
        runner = self.runner or Siril(
            siril_path=getattr(args, 'siril_path', None), siril_mode=getattr(args, 'siril_mode', None),
        )
        if not runner.run_siril_script(
            '\n'.join(lines) + '\n', str(self.work_dir), self.script_name,
        ):
            raise RuntimeError(f'Échec Siril : {self.work_dir / self.script_name}')
        return dict(catalogs=[str(catalog) for _, catalog in self.images],
                    script_path=str(self.work_dir / self.script_name))


class StellarAssessmentProcessor(QualityProcessor):
    """Exécute la détection puis la comparaison stellaire guidée par la référence."""

    stage_key = 'stellar_assessment'
    stage_label = 'Qualité — ensemble des contrôles stellaires'

    def __init__(self, context: QualityContext, work_dir: Path) -> None:
        """Fixe le dossier des catalogues, avec cache de référence dans son parent."""
        super().__init__(context)
        self.work_dir = Path(work_dir)

    @classmethod
    def parameter_children(cls) -> tuple[type[QualityProcessor], ...]:
        """Déclare la détection et la comparaison comme deux étapes distinctes."""
        return (SirilStarDetectionProcessor, StarComparisonProcessor)

    def _process(self, args: argparse.Namespace) -> JSONReport:
        """Réutilise le catalogue commun puis mesure le candidat sans autre calcul d'image.

        L’orchestrateur peut désigner son catalogue partagé via
        _restoration_reference_catalog ; sinon le parent du dossier est utilisé.
        """
        from astropy.io import fits
        from shutil import copyfile
        from lib.siril import Siril

        source, candidate = self.context.source, self.context.candidate_path
        if candidate is None:
            raise ValueError('Une image candidate est requise pour le contrôle stellaire')
        work = self.work_dir
        work.mkdir(parents=True, exist_ok=True)
        before = Path(getattr(
            args, "_restoration_reference_catalog", work.parent / "common_reference.tsv",
        ))
        before.parent.mkdir(parents=True, exist_ok=True)
        after = work / "final.tsv"
        staged_reference = work / 'reference.tsv'
        needs_reference = not before.is_file()
        images = [(candidate, after)]
        if needs_reference:
            images.insert(0, (source, staged_reference))
        layer = getattr(args, 'deconvolution_layer', None)
        if layer is None:
            layer = 1 if fits.getdata(source).ndim == 3 else 0
        service = Siril(
            siril_path=getattr(args, 'siril_path', None),
            siril_mode=getattr(args, 'siril_mode', None),
        )
        self.run_child(SirilStarDetectionProcessor(
            self.context, images, work, layer, runner=service, script_name='final_quality.sps',
        ), args)
        if needs_reference:
            copyfile(staged_reference, before)
        return self.run_child(StarComparisonProcessor(
            self.context, before, after,
            getattr(args, 'deconvolution_match_radius', 1),
            getattr(args, 'deconvolution_min_stars', 10),
            getattr(args, 'deconvolution_min_match_fraction', .7),
            layer, reference_images=True, quality_mode='reliable',
        ), args)
