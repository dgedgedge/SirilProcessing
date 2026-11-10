"""Compare deux recherches de droite logarithmique sur les mêmes pixels.

La recherche de longueur conserve son propre candidat, la recherche de niveau
contrôle la référence à 0,8. Chaque moteur valide son rendu final. Le sélecteur
recompte les objectifs sur tout le champ, conserve une entrée inchangée de
référence et ne choisit aucun candidat qui assombrit le diffus initial.
Voir docs/scripts/postProcess/ghs-quartiles.md.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from lib.ghs_linear_histogram import (
    REGRESSION_REFERENCE_INTENSITY, REGRESSION_THRESHOLD_EPSILON,
    TAIL_CHANNELS, TAIL_HISTOGRAM_BINS,
    add_regression_floor_objective, add_tail_height_objective,
    linear_histogram_profile, log_tail_height, symmetry_points,
)
from lib.ghs_log_histogram import HISTOGRAM_BINS
from lib.ghs_quartiles import QuartileGHSProcessor
from lib.processor import processor
from lib.star_separation import validate_rgb
from lib.type_defs import JSONReport

if TYPE_CHECKING:
    from lib.auto_ghs import ComputeBackend, HistogramAnalyzer

LOGGER = logging.getLogger(__name__)


class LogSpanGHSProcessor(QuartileGHSProcessor):
    """Produit la référence de longueur avec D/B/SP/HP du moteur antérieur.

    Les essais intermédiaires peuvent comprimer les tons moyens ; le rendu
    final doit retrouver au moins la luminosité diffuse et sa séparation au
    ciel initiales. L'entrée est retournée si ce contrôle final échoue.
    Les autres protections restent cumulatives et les calculs restent par blocs.
    """

    preserve_achieved_tail = False
    parameter_dependencies = QuartileGHSProcessor.parameter_dependencies | {
        'ghs_quartile_iterations', 'ghs_quartile_max_strength', 'ghs_quartile_validations',
        'ghs_quartile_block_pixels', 'ghs_linearity_tolerance', 'ghs_tail_height',
        'ghs_tail_channel', 'ghs_blackpoint_loss', 'ghs_regression_min_count',
        'ghs_sparse_tail_fraction', 'ghs_sparse_highlight_retention',
    }

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie le moteur de longueur indépendant du moteur de niveau."""
        return 'ghs_log_span'

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Réutilise les options partagées déclarées par QuartileGHSProcessor."""

    @staticmethod
    def candidate_parameters(
        counts: np.ndarray, max_strength: float, protected_hp: tuple[float, ...],
    ) -> Iterator[dict[str, float]]:
        """Explore la grille de référence : douze D, B=15/5 et HP=0,75/1.

        protected_hp est conservé dans l'interface commune mais ne modifie pas
        cette grille indépendante. SP reste adapté au pic et aux inflexions.
        """
        yield dict(D=0., B=5., SP=0., LP=0., HP=1.)
        for strength in np.linspace(max_strength / 40, max_strength, 12):
            for b in (15., 5.):
                for sp in symmetry_points(counts):
                    for hp in (.75, 1.):
                        if sp <= hp:
                            yield dict(D=float(strength), B=b, SP=sp, LP=0., HP=hp)

    @staticmethod
    def diverse_candidates(records: list[JSONReport]) -> list[JSONReport]:
        """Classe uniquement par gain de longueur et hauteur, comme la référence."""
        return sorted(records, key=lambda record: record['gain'], reverse=True)

    @staticmethod
    def diffuse_rejections(before: JSONReport, after: JSONReport) -> list[str]:
        """Diffère le contrôle du diffus au rendu final de ce moteur exploratoire."""
        return []

    def search(
        self, data: np.ndarray, analyzer: HistogramAnalyzer, backend: ComputeBackend,
        args: argparse.Namespace, stars: np.ndarray | None = None, headroom: float = .95,
    ) -> tuple[np.ndarray, JSONReport]:
        """Cherche sa propre référence, puis contrôle son diffus final fond/fusion."""
        options = argparse.Namespace(**vars(args))
        options.ghs_regression_min_count = 0.0
        options.ghs_sparse_tail_fraction = 0.0
        options.ghs_quartile_validations = min(3, args.ghs_quartile_validations)
        output, report = super().search(data, analyzer, backend, options, stars, headroom)
        accepted = [item['accepted'] for item in report['iterations'] if item['accepted']]
        reasons = []
        if accepted:
            protection = accepted[-1]['protection']
            reasons.extend(QuartileGHSProcessor.diffuse_rejections(
                report['protection_before'], protection['starless'],
            ))
            if report.get('composite_protection_before') is not None:
                reasons.extend('composite: ' + reason for reason in
                               QuartileGHSProcessor.diffuse_rejections(
                                   report['composite_protection_before'], protection['composite'],
                               ))
        report['final_output_rejections'] = reasons
        if reasons:
            report['status'] = 'rejected final diffuse loss'
            report['discarded_after'] = report['after']
            report['after'] = report['before']
            return data, report
        return output, report


class LogHistogramRenderer(processor):
    """Sélectionne un rendu contrôlé parmi longueur, niveau et entrée inchangée."""

    parameter_dependencies = QuartileGHSProcessor.parameter_dependencies

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie l'orchestration des moteurs de droite logarithmique."""
        return 'ghs_log_render'

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Les paramètres appartiennent aux moteurs enfants, sans duplication."""
        for child in cls.parameter_children():
            child.add_arguments(parser)

    @classmethod
    def parameter_children(cls) -> tuple[type[processor], ...]:
        """Déclare les deux moteurs qui produisent et contrôlent leurs candidats."""
        return (QuartileGHSProcessor, LogSpanGHSProcessor)

    @staticmethod
    def assess(
        image: np.ndarray, args: argparse.Namespace, stars: np.ndarray | None,
        headroom: float,
    ) -> JSONReport:
        """Recompte longueur et référence soutenue à 0,8 sur tous les pixels CPU.

        La plus longue portion globale reste un diagnostic distinct du segment
        qui permet de vérifier réellement la droite à 0,8. Aucun échantillonnage
        ni extrapolation ne peut certifier ce second segment.
        """
        from lib.auto_ghs import luminance

        light = luminance(image, args.ghs_color_mode)
        counts = np.histogram(light[light > 1e-6], bins=HISTOGRAM_BINS, range=(0, 1))[0]
        longest = linear_histogram_profile(counts, args.ghs_linearity_tolerance)
        reference = REGRESSION_REFERENCE_INTENSITY if args.ghs_regression_min_count > 0 else None
        line = linear_histogram_profile(counts, args.ghs_linearity_tolerance, reference)
        plane = image[TAIL_CHANNELS.index(args.ghs_tail_channel)]
        if stars is not None:
            stellar_plane = stars[TAIL_CHANNELS.index(args.ghs_tail_channel)]
            plane = headroom*plane + stellar_plane*(1-headroom*plane)
        tail_counts = np.histogram(plane, bins=TAIL_HISTOGRAM_BINS, range=(0, 1))[0]
        profile = add_tail_height_objective(
            line, log_tail_height(tail_counts), args.ghs_tail_height, args.ghs_tail_channel,
        )
        profile = add_regression_floor_objective(
            profile, args.ghs_bins, minimum_count=args.ghs_regression_min_count,
        )
        global_floor = add_regression_floor_objective(
            longest, args.ghs_bins, minimum_count=args.ghs_regression_min_count,
        )['regression_floor']
        global_prediction = global_floor['predicted_log10_count']
        global_met = (args.ghs_regression_min_count == 0 or (
            global_prediction is not None
            and global_prediction >= np.log10(args.ghs_regression_min_count) - REGRESSION_THRESHOLD_EPSILON
        ))
        if args.ghs_regression_min_count > 0:
            profile['regression_floor']['achieved'] = bool(
                profile['regression_floor']['achieved'] and global_met,
            )
        profile['regression_floor']['longest_line_predicted_log10_count'] = global_prediction
        floor_met = args.ghs_regression_min_count == 0 or profile['regression_floor']['achieved']
        tail_met = args.ghs_tail_height == 0 or profile['tail']['achieved']
        peak_met = profile['peak'] is not None and profile['peak'] < .25
        return dict(profile=profile, longest_line=longest,
                    targets_achieved=bool(profile['measurable'] and peak_met and floor_met and tail_met),
                    met_count=(int(bool(floor_met))+int(bool(tail_met))
                               + int(bool(peak_met))+int(bool(profile['measurable']))))

    @staticmethod
    def ranking(assessment: JSONReport) -> tuple[bool, int, float, float]:
        """Privilégie les objectifs vérifiés, puis la plus longue portion globale."""
        return (assessment['targets_achieved'], assessment['met_count'],
                assessment['longest_line']['line_span'], assessment['profile']['score'])

    def search(
        self, data: np.ndarray, analyzer: HistogramAnalyzer, backend: ComputeBackend,
        args: argparse.Namespace, stars: np.ndarray | None = None, headroom: float = .95,
    ) -> tuple[np.ndarray, JSONReport]:
        """Exécute les moteurs indépendants et sélectionne leur meilleur rendu.

        Les candidats sont transférés en RAM après leur production ; seules les
        entrées nécessaires au calcul restent sur GPU. Le classement commun
        porte sur des objectifs mesurés en pleine résolution, sans seuil lié à
        un nom d'objet. L'entrée inchangée reste une candidate de référence.
        """
        from lib.ghs_final_stretch import MAX_SAMPLE_PIXELS, ProtectedGHSFinalizer

        original = backend.numpy(data)
        cpu_stars = (stars if isinstance(stars, np.ndarray) else backend.numpy(stars)) if stars is not None else None
        validate_rgb(original)
        QuartileGHSProcessor.validate_options(args)
        if not np.isfinite(headroom) or not 0 < headroom < 1:
            raise ValueError('La réserve de fusion doit être dans ]0,1[')
        if cpu_stars is not None:
            validate_rgb(cpu_stars)
            if cpu_stars.shape != original.shape:
                raise ValueError('Étoiles et fond de géométries différentes')
        before = self.assess(original, args, cpu_stars, headroom)
        stride = max(1, math.ceil(math.sqrt(original.shape[1]*original.shape[2]/MAX_SAMPLE_PIXELS)))
        thresholds = ProtectedGHSFinalizer.thresholds(original[:, ::stride, ::stride], analyzer.color_mode)
        if thresholds:
            thresholds['bright'], thresholds['core'] = .75, .95
        baseline = (ProtectedGHSFinalizer.measure(original, original, thresholds, analyzer.color_mode)
                    if thresholds else None)
        best_image, best_name, best_assessment = original, 'unchanged', before
        best_report: JSONReport = dict(status='no admissible improvement', iterations=[],
                                      before=before['profile'], after=before['profile'])
        searches = {}
        engines = [('length', LogSpanGHSProcessor()), ('reference_level', QuartileGHSProcessor())]
        if not thresholds:
            engines = []
        if not args.ghs_quartile_iterations:
            best_report['status'] = 'disabled'
            engines = []
        for name, engine in engines:
            LOGGER.info('AutoGHS histogramme : recherche %s', name)
            candidate, report = engine.search(data, analyzer, backend, args, stars, headroom)
            image = backend.numpy(candidate)
            del candidate
            assessment = self.assess(image, args, cpu_stars, headroom)
            measured = ProtectedGHSFinalizer.measure(original, image, thresholds, analyzer.color_mode)
            rejections = QuartileGHSProcessor.diffuse_rejections(baseline, measured)
            if report['status'] == 'rejected final diffuse loss':
                rejections.extend(report['final_output_rejections'])
            searches[name] = dict(report=report, assessment=assessment, output_rejections=rejections)
            LOGGER.info(
                'AutoGHS histogramme : candidat %s, objectifs %s, longueur %.5f, rejets %s',
                name, assessment['targets_achieved'], assessment['longest_line']['line_span'],
                ', '.join(rejections) if rejections else 'aucun',
            )
            if not rejections and self.ranking(assessment) > self.ranking(best_assessment):
                best_image, best_name = image, name
                best_assessment, best_report = assessment, report
        result = dict(best_report)
        result.update(before=before['profile'], after=best_assessment['profile'],
                      objective='select_log_histogram_render_with_verified_targets',
                      regression_min_count=args.ghs_regression_min_count,
                      longest_line_before=before['longest_line'],
                      longest_line_after=best_assessment['longest_line'],
                      targets_achieved=best_assessment['targets_achieved'],
                      regression_floor_achieved=best_assessment['profile']['regression_floor']['achieved'],
                      tail_height_achieved=best_assessment['profile']['tail']['achieved'],
                      selected_search=best_name, searches=searches)
        LOGGER.info('AutoGHS histogramme : rendu retenu %s ; objectifs atteints %s',
                    best_name, result['targets_achieved'])
        return backend.xp.asarray(best_image), result

    def post_process(
        self, input_path: Path | str, output_path: Path | str,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Lit un RGB, sélectionne son rendu et écrit un FITS et un rapport distincts."""
        from lib.auto_ghs import ComputeBackend, GHSOptimizer, HistogramAnalyzer
        from lib.restoration_quality import normalized_planes
        from lib.star_separation import write_rgb

        source, destination = Path(input_path), Path(output_path)
        if source.resolve() == destination.with_suffix('.fits').resolve():
            raise ValueError('La sortie doit être distincte de la source')
        options = GHSOptimizer.options(self._get_args(args))
        backend = ComputeBackend(options.compute_backend)
        output, report = self.search(
            normalized_planes(source),
            HistogramAnalyzer(options.ghs_bins, color_mode=options.ghs_color_mode), backend, options,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_rgb(source, destination.with_suffix('.fits'), backend.numpy(output), nonlinear=True, role='STARLESS')
        report['output_image'] = str(destination.with_suffix('.fits'))
        destination.write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
        return report
