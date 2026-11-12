"""Compare les recherches de droite logarithmique depuis la sortie du pic.

La recherche de longueur conserve son propre candidat, la recherche de niveau
contrôle la référence à 0,8. Chaque moteur valide son rendu final. Le sélecteur
recompte les objectifs sur tout le champ, conserve une entrée inchangée de
référence et ne choisit aucun candidat qui assombrit le diffus initial.
Une reprise stratifiée représente la queue rare si les objectifs restent
incomplets après les deux recherches régulières.
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
from lib.ghs_histogram_shape import (
    piecewise_histogram_profile, protect_histogram_shape, histogram_selection_key,
    PIECEWISE_SELECTION_POLICY,
)
from lib.processor import processor
from lib.ghs_diagnostics import HistogramDiagnostics
from lib.ghs_render_quality import HistogramRenderQuality
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
        trial_images: HistogramDiagnostics | None = None,
    ) -> tuple[np.ndarray, JSONReport]:
        """Cherche sa propre référence, puis contrôle son diffus final fond/fusion."""
        options = argparse.Namespace(**vars(args))
        options.ghs_regression_min_count = 0.0
        options.ghs_sparse_tail_fraction = 0.0
        options.ghs_quartile_validations = min(3, args.ghs_quartile_validations)
        output, report = super().search(data, analyzer, backend, options, stars, headroom,
                                       **({'trial_images': trial_images}
                                          if trial_images is not None else {}))
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


class PopulationGHSProcessor(QuartileGHSProcessor):
    """Reprend la recherche de niveau avec un histogramme stratifié pondéré.

    Les populations rares restent représentées dans les prédictions. Les
    contrôles spatiaux et les validations complètes restent ceux du moteur
    de niveau, depuis la même entrée de phase et sous les mêmes protections.
    """

    population_sampling = True

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie le moteur de reprise des distributions peu peuplées."""
        return 'ghs_population'

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Partage les options du moteur QuartileGHSProcessor sans duplication."""


class ShapeGHSProcessor(PopulationGHSProcessor):
    """Explore plusieurs chemins sous une référence de forme pleine résolution figée.

    Les familles GHS et les classes de position du pic partagent les contrôles
    complets. Les chemins de l'exécution courante sont rejoués depuis l'entrée ;
    aucun ancien résultat ni collection de tableaux GPU n'est réutilisé.
    """

    preserve_shape = True
    preserve_achieved_tail = False
    parameter_dependencies = LogSpanGHSProcessor.parameter_dependencies | {
        'ghs_histogram_model', 'ghs_shape_smoothing', 'ghs_shape_retention',
        'ghs_shape_distance', 'ghs_shape_peak_shift', 'ghs_shape_stiffness', 'ghs_tail_channel',
        'ghs_shape_local_retention', 'ghs_post_blackpoint', 'ghs_shape_branches',
        'ghs_quality_cnr_floor', 'ghs_quality_tail_bonus',
    }

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie le moteur de forme, avec les options partagées des quartiles."""
        return 'ghs_shape'


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
        """Déclare les deux moteurs réguliers et leur reprise stratifiée."""
        return (QuartileGHSProcessor, LogSpanGHSProcessor, PopulationGHSProcessor, ShapeGHSProcessor)

    @staticmethod
    def assess(
        image: np.ndarray, args: argparse.Namespace, stars: np.ndarray | None,
        headroom: float,
    ) -> JSONReport:
        """Recompte longueur et référence soutenue à 0,8 sur tous les pixels CPU.

        Les deux mesures commencent à la sortie du pic principal. Une portion
        éloignée ne peut contourner une bosse pour vérifier la droite à 0,8.
        Aucun échantillonnage ni extrapolation ne certifie ce segment.
        """
        from lib.auto_ghs import luminance

        light = luminance(image, args.ghs_color_mode)
        counts = np.histogram(light[light > 1e-6], bins=HISTOGRAM_BINS, range=(0, 1))[0]
        piecewise = getattr(args, 'ghs_histogram_model', 'piecewise') == 'piecewise'
        longest = (piecewise_histogram_profile(counts, args.ghs_linearity_tolerance,
                                               args.ghs_shape_smoothing) if piecewise else
                   linear_histogram_profile(counts, args.ghs_linearity_tolerance))
        reference = REGRESSION_REFERENCE_INTENSITY if args.ghs_regression_min_count > 0 else None
        line = longest if piecewise else linear_histogram_profile(counts, args.ghs_linearity_tolerance, reference)
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
        unmet = [name for name, met in (
            ('linearity_from_peak', profile['measurable']), ('peak_below_quarter', peak_met),
            ('regression_floor', floor_met), ('tail_height', tail_met),
        ) if not met]
        return dict(profile=profile, longest_line=longest,
                    targets_achieved=bool(profile['measurable'] and peak_met and floor_met and tail_met),
                    unmet_targets=unmet,
                    met_count=(int(bool(floor_met))+int(bool(tail_met))
                               + int(bool(peak_met))+int(bool(profile['measurable']))))

    @staticmethod
    def ranking(assessment: JSONReport) -> tuple[bool, int, float, float]:
        """Privilégie le compromis de rendu en piecewise, sans cible de position.

        Le mode historique conserve la priorité de longueur puis de score.
        """
        if assessment['profile'].get('model') == 'piecewise':
            return histogram_selection_key(assessment['profile'])
        return (assessment['targets_achieved'], assessment['met_count'],
                assessment['longest_line']['line_span'], assessment['profile']['score'])

    def search(
        self, data: np.ndarray, analyzer: HistogramAnalyzer, backend: ComputeBackend,
        args: argparse.Namespace, stars: np.ndarray | None = None, headroom: float = .95,
        reference_shape: JSONReport | None = None,
        trial_images: HistogramDiagnostics | None = None,
        blackpoint_reference: np.ndarray | None = None,
    ) -> tuple[np.ndarray, JSONReport]:
        """Exécute les moteurs indépendants et sélectionne leur meilleur rendu.

        Les candidats sont transférés en RAM après leur production ; seules les
        entrées nécessaires au calcul restent sur GPU. Le classement commun
        porte sur la qualité mesurée en pleine résolution en piecewise, et les
        objectifs historiques en line, sans seuil lié à un nom d'objet.
        L'entrée inchangée reste une candidate de référence.
        reference_shape fixe la forme avant égalisation en mode piecewise ;
        None prend la forme à l'entrée de cette phase.
        trial_images est transmis aux moteurs pour capturer leurs essais
        majeurs ; son absence conserve le coût et les sorties habituels.
        blackpoint_reference transmet le masque positif de l'entrée linéaire,
        pour vérifier le budget après chaque transformation complète.
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
        piecewise = getattr(args, 'ghs_histogram_model', 'piecewise') == 'piecewise'
        shape_reference = (reference_shape or before['longest_line']) if piecewise else None
        if piecewise:
            before['profile'] = protect_histogram_shape(before['profile'], shape_reference,
                                                          args.ghs_shape_retention, args.ghs_shape_distance,
                                                          args.ghs_shape_peak_shift, args.ghs_shape_stiffness,
                                                          args.ghs_shape_local_retention)
            if before['profile']['shape_preservation']['achieved'] is not True:
                before['targets_achieved'] = False
                before['unmet_targets'].append('histogram_shape')
        stride = max(1, math.ceil(math.sqrt(original.shape[1]*original.shape[2]/MAX_SAMPLE_PIXELS)))
        thresholds = ProtectedGHSFinalizer.thresholds(original[:, ::stride, ::stride], analyzer.color_mode)
        if thresholds:
            thresholds['bright'], thresholds['core'] = .75, .95
        baseline = (ProtectedGHSFinalizer.measure(original, original, thresholds, analyzer.color_mode)
                    if thresholds else None)
        fusion_original, fusion_baseline = None, None
        effective_retention = args.ghs_highlight_retention
        if piecewise:
            from lib.auto_ghs import luminance

            light = luminance(original, analyzer.color_mode)
            sparse = QuartileGHSProcessor.sparse_tail_retention(
                light, args.ghs_highlight_retention, args.ghs_sparse_tail_fraction,
                args.ghs_sparse_highlight_retention,
            )
            del light
            effective_retention = sparse['effective_retention']
            if cpu_stars is not None and thresholds:
                fusion_original = headroom*original+cpu_stars*(1-headroom*original)
                fusion_baseline = ProtectedGHSFinalizer.measure(
                    fusion_original, fusion_original, thresholds, analyzer.color_mode,
                    mask_reference=original,
                )
            before['profile'] = HistogramRenderQuality.attach(
                before['profile'], baseline or {}, baseline or {}, shape_reference,
                bright_retention=effective_retention, cnr_floor=args.ghs_quality_cnr_floor,
                tail_bonus=args.ghs_quality_tail_bonus,
                composite_before=fusion_baseline, composite_after=fusion_baseline,
            )
        best_image, best_name, best_assessment = original, 'unchanged', before
        best_report: JSONReport = dict(status='no admissible improvement', iterations=[],
                                      before=before['profile'], after=before['profile'])
        searches = {}
        engines = [('length', LogSpanGHSProcessor()), ('reference_level', QuartileGHSProcessor()),
                   ('population', PopulationGHSProcessor())]
        if piecewise:
            engines = [('shape', ShapeGHSProcessor())]
        if not thresholds:
            engines = []
        if not args.ghs_quartile_iterations:
            best_report['status'] = 'disabled'
            engines = []
        for name, engine in engines:
            if name == 'population' and best_assessment['targets_achieved']:
                continue
            LOGGER.info('AutoGHS histogramme : recherche %s', name)
            image_options = {'trial_images': trial_images} if trial_images is not None else {}
            if blackpoint_reference is not None:
                image_options['blackpoint_reference'] = blackpoint_reference
            candidate, report = (engine.search(data, analyzer, backend, args, stars, headroom,
                                               reference_shape=shape_reference, **image_options)
                                 if piecewise and reference_shape is not None else
                                 engine.search(data, analyzer, backend, args, stars, headroom, **image_options))
            image = backend.numpy(candidate)
            del candidate
            assessment = self.assess(image, args, cpu_stars, headroom)
            shape_rejections = []
            if piecewise:
                protected = protect_histogram_shape(assessment['profile'], shape_reference,
                                                      args.ghs_shape_retention, args.ghs_shape_distance,
                                                      args.ghs_shape_peak_shift, args.ghs_shape_stiffness,
                                                      args.ghs_shape_local_retention)
                assessment['profile'] = protected
                shape_rejections = HistogramRenderQuality.hard_shape_rejections(protected)
                if protected['shape_preservation']['achieved'] is not True:
                    assessment['targets_achieved'] = False
                    assessment['unmet_targets'].append('histogram_shape')
            measured = ProtectedGHSFinalizer.measure(original, image, thresholds, analyzer.color_mode)
            if piecewise:
                fusion_measured = None
                if fusion_original is not None:
                    fusion_image = headroom*image+cpu_stars*(1-headroom*image)
                    fusion_measured = ProtectedGHSFinalizer.measure(
                        fusion_original, fusion_image, thresholds, analyzer.color_mode,
                        mask_reference=original,
                    )
                    del fusion_image
                assessment['profile'] = HistogramRenderQuality.attach(
                    assessment['profile'], baseline, measured, shape_reference,
                    bright_retention=effective_retention, cnr_floor=args.ghs_quality_cnr_floor,
                    tail_bonus=args.ghs_quality_tail_bonus,
                    composite_before=fusion_baseline, composite_after=fusion_measured,
                )
                if not assessment['profile']['render_quality']['available']:
                    shape_rejections.append('unmeasurable render quality')
            rejections = QuartileGHSProcessor.diffuse_rejections(baseline, measured)
            if piecewise:
                noise = ProtectedGHSFinalizer.noise_assessment(baseline, measured, relative=True)
                assessment['noise_assessment'] = noise
                rejections.extend(noise['rejections'])
            rejections.extend(shape_rejections)
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
                      unmet_targets=best_assessment.get('unmet_targets', []),
                      selected_search=best_name, searches=searches)
        if piecewise:
            result.update(histogram_model='piecewise', shape_reference=shape_reference,
                          selection_policy=PIECEWISE_SELECTION_POLICY,
                          quality_sufficient=best_assessment['profile'].get('render_quality', {}).get('sufficient_cnr', False),
                          shape_preservation_achieved=best_assessment['profile'].get(
                              'shape_preservation', {}).get('achieved'),
                          objective='select_piecewise_log_histogram_preserving_shape')
        LOGGER.info('AutoGHS histogramme : rendu retenu %s ; objectifs atteints %s',
                    best_name, result['targets_achieved'])
        if not result['targets_achieved'] and engines:
            LOGGER.warning('AutoGHS : objectifs non atteints : %s ; meilleur rendu admissible conservé',
                           ', '.join(result['unmet_targets']))
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
