"""Correction optique conditionnelle avant restauration, guidée par les étoiles.

Les statistiques spatiales servent à la décision, jamais à un gain local.
Voir docs/scripts/postProcess/aberration-stage.md pour les seuils heuristiques.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
from astropy.io import fits

from lib.aberration import AberrationEngine
from lib.deconvolution import read_star_catalog
from lib.processor import processor
from lib.parameterized_component import ParameterizedComponent
from lib.postprocess_paths import reset_work_dir
from lib.quality_processors import QualityContext, DiffuseSignalProcessor
from lib.quality_star_processors import (
    SirilStarDetectionProcessor, StarComparisonProcessor, ReferenceStarMeasurementProcessor,
)
from lib.type_defs import JSONReport, StarMeasurement


class AberrationCorrectionStage(processor):
    """Déclenche, mesure et retient une correction optique indépendante.

    Sans assez d'étoiles pour prouver l'utilité du traitement, l'entrée est
    conservée. Les seuils de FWHM absolue sont facultatifs car dépendants de
    l'échantillonnage. Les statistiques appariées conservent les positions
    initiales, notamment pour le groupe des bords.
    """

    parameter_prefixes = ('aberration_', 'deconvolution_', 'quality_', 'siril_')
    parameter_dependencies = frozenset({'compute_backend'})
    MIN_STARS = 10
    MIN_EDGE_STARS = 5
    MIN_RETENTION = .7
    MAX_ROUNDNESS_LOSS = .01
    MAX_FWHM_INCREASE = .03

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie l'étape distincte et son option d'activation."""
        return 'aberration'

    @classmethod
    def parameter_children(cls) -> tuple[type[ParameterizedComponent], ...]:
        """Déclare moteur et contrôles stellaires sans charger le modèle."""
        return (AberrationEngine, SirilStarDetectionProcessor, StarComparisonProcessor,
                DiffuseSignalProcessor)

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare les seuils dimensionnels et relatifs propres à la correction."""
        AberrationEngine.add_arguments(parser)
        if any(a.dest == 'aberration_roundness_target' for a in parser._actions):
            return
        for name, default, help_text in (
            ('roundness-target', .85, 'Rondeur médiane b/a visée (défaut 0.85)'),
            ('edge-fwhm-ratio', 1.25, 'FWHM bords/centre maximale avant essai (défaut 1.25)'),
            ('max-fwhm-px', None, 'FWHM médiane maximale facultative, en pixels'),
            ('min-roundness-gain', .03, 'Gain médian de rondeur requis, en points (défaut 0.03)'),
            ('min-fwhm-gain', .05, 'Réduction médiane FWHM requise (défaut 0.05)'),
            ('edge-fraction', .2, 'Largeur de chaque bande de bord (fraction, défaut 0.2)'),
        ):
            parser.add_argument('--aberration-' + name, type=float, default=default, help=help_text)
        parser.add_argument('--aberration-force', action=argparse.BooleanOptionalAction,
                            default=False, help='Forcer l’essai ; conserver les contrôles après calcul')

    @staticmethod
    def _groups(x: float, y: float, shape: tuple[int, int], edge: float) -> tuple[str, ...]:
        """Classe une position initiale en bord ou centre, sans modifier l'image."""
        height, width = shape
        border = x < edge*width or x > (1-edge)*width or y < edge*height or y > (1-edge)*height
        return ('all', 'edge' if border else 'center')

    @classmethod
    def baseline(
        cls, stars: list[StarMeasurement], shape: tuple[int, int], edge: float,
    ) -> JSONReport:
        """Résume médianes de rondeur et FWHM des détections valides non saturées."""
        result = {}
        for group in ('all', 'edge', 'center'):
            selected = [s for s in stars if group in cls._groups(s['x'], s['y'], shape, edge)]
            result[group] = dict(
                count=len(selected),
                roundness=float(np.median([min(s['fwhm_x'], s['fwhm_y']) /
                                            max(s['fwhm_x'], s['fwhm_y']) for s in selected]))
                if selected else None,
                fwhm_px=float(np.median([s['fwhm'] for s in selected])) if selected else None,
            )
        return result

    @staticmethod
    def validate(args: argparse.Namespace) -> None:
        """Refuse les seuils non finis, fractions invalides et FWHM non positives."""
        for key, low, high in (
            ('roundness_target', 0., 1.), ('edge_fwhm_ratio', 1., np.inf),
            ('min_roundness_gain', 0., 1.), ('min_fwhm_gain', 0., 1.),
            ('edge_fraction', 0., .5),
        ):
            value = getattr(args, 'aberration_' + key)
            if not np.isfinite(value) or not low < value < high:
                raise ValueError('Seuil aberration invalide : ' + key)
        maximum = args.aberration_max_fwhm_px
        if maximum is not None and (not np.isfinite(maximum) or maximum <= 0):
            raise ValueError('aberration-max-fwhm-px doit être positif et fini')

    @staticmethod
    def trigger(before: JSONReport, args: argparse.Namespace) -> list[str]:
        """Nomme les défauts d'entrée ; un groupe insuffisant reste inconnu."""
        reasons = []
        for group, minimum in (
            ('all', AberrationCorrectionStage.MIN_STARS),
            ('edge', AberrationCorrectionStage.MIN_EDGE_STARS),
        ):
            values = before[group]
            if values['count'] < minimum:
                continue
            if values['roundness'] < args.aberration_roundness_target:
                reasons.append(group + ':roundness')
            if (args.aberration_max_fwhm_px is not None
                    and values['fwhm_px'] > args.aberration_max_fwhm_px):
                reasons.append(group + ':fwhm_absolute')
        minimum = AberrationCorrectionStage.MIN_EDGE_STARS
        if before['edge']['count'] >= minimum and before['center']['count'] >= minimum:
            ratio = before['edge']['fwhm_px'] / before['center']['fwhm_px']
            if ratio > args.aberration_edge_fwhm_ratio:
                reasons.append('edge:fwhm_relative_to_center')
        return reasons

    @classmethod
    def assess(
        cls, comparison: JSONReport, before: JSONReport, shape: tuple[int, int],
        reasons: list[str], args: argparse.Namespace,
    ) -> JSONReport:
        """Compare les mêmes étoiles, exige un gain utile et aucune régression forte.

        Les bords doivent conserver 70 % des références et au moins cinq
        paires si au moins cinq étoiles y étaient détectées. Un gain global
        seul ne valide pas une correction demandée spécifiquement aux bords.
        """
        groups, failures = {}, []
        if comparison.get('status') != 'validated':
            failures.append('insufficient_matches')
        for group, minimum in (('all', cls.MIN_STARS), ('edge', cls.MIN_EDGE_STARS),
                               ('center', cls.MIN_EDGE_STARS)):
            pairs = [p for p in comparison.get('pairs', []) if group in cls._groups(
                p['x_before'], p['y_before'], shape, args.aberration_edge_fraction)]
            count = len(pairs)
            reliable = count >= minimum and count >= cls.MIN_RETENTION * before[group]['count']
            item = dict(count=count, reliable=reliable)
            if reliable:
                delta = float(np.median([
                    p['roundness_after'] - p['roundness_before'] for p in pairs
                ]))
                gain = float(1 - np.median([p['ratio_after_before'] for p in pairs]))
                item.update(roundness_gain=delta, fwhm_gain=gain,
                            roundness_before=float(np.median([p['roundness_before'] for p in pairs])),
                            roundness_after=float(np.median([p['roundness_after'] for p in pairs])),
                            fwhm_before_px=float(np.median([p['fwhm_before_px'] for p in pairs])),
                            fwhm_after_px=float(np.median([p['fwhm_after_px'] for p in pairs])))
                item['useful'] = (delta >= args.aberration_min_roundness_gain
                                  or gain >= args.aberration_min_fwhm_gain)
                if delta < -cls.MAX_ROUNDNESS_LOSS or gain < -cls.MAX_FWHM_INCREASE:
                    failures.append(group + ':regression')
            elif group == 'all' or (group == 'edge' and before[group]['count'] >= minimum):
                failures.append(group + ':insufficient_pairs')
            groups[group] = item
        targets = {'edge' if reason.startswith('edge:') else 'all' for reason in reasons}
        if not targets:
            targets = {'all'}
        for group in targets:
            if not groups[group].get('useful', False):
                failures.append(group + ':insufficient_gain')
        return dict(accepted=not failures, reasons=failures, groups=groups)

    @staticmethod
    def measure_flux(
        source: Path, candidate: Path, report: Path, args: argparse.Namespace,
    ) -> JSONReport:
        """Résume les variations diffuses locales du candidat, sans changer sa sélection.

        La médiane porte sur toutes les mesures région/canal disponibles,
        y compris UNKNOWN avec valeur ; elle n'est pas un flux global intégré.
        Le rapport détaillé conserve les ouvertures, fonds et incertitudes.
        Une absence de mesure conserve une médiane None, jamais zéro.
        """
        from lib.restoration_quality import QualityReference, normalized_planes

        reference = QualityReference(source, args)
        context = QualityContext(source, candidate, reference.data,
                                 normalized_planes(candidate), reference)
        audit = DiffuseSignalProcessor(context).post_process(candidate, report, args)
        entries = audit.get('per_region', [])
        values = [entry['value'] for entry in entries
                  if isinstance(entry.get('value'), (int, float))
                  and np.isfinite(entry['value'])]
        return dict(
            status=audit['status'], scope='candidate_local_regions_and_channels',
            formula='(flux_after - flux_before) / flux_before',
            median_relative_change=float(np.median(values)) if values else None,
            loss_limit=-reference.flux_loss,
            measured_count=len(values), failed_count=sum(e['status'] == 'FAIL' for e in entries),
            audit_path=str(report),
        )

    def post_process(
        self, input_path: Path, output_path: Path, args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Écrit l'audit et transmet le candidat accepté ou exactement le chemin source.

        Les pannes techniques restent fatales et enregistrées ; une mesure
        insuffisante ou un gain faible conserve l'entrée sans la réécrire.
        Aucun modèle n'est chargé si les déclencheurs ne sont pas satisfaits.
        """
        args = self._get_args(args)
        defaults = argparse.ArgumentParser(add_help=False)
        self.add_arguments(defaults)
        args = argparse.Namespace(**{**vars(defaults.parse_args([])), **vars(args)})
        self.validate(args)
        source, report = Path(input_path).resolve(), Path(output_path).resolve()
        work = report.parent / (report.stem + '_work')
        if source == report or source.is_relative_to(work.resolve()):
            raise ValueError('La source doit être extérieure aux sorties Aberration Remover')
        work = reset_work_dir(report)
        candidate = work / 'candidate.fits'
        result = dict(image_path=str(source), output_image=str(source), status='evaluating',
                      engine='aberration_remover', measurement_convention='b/a; geometric FWHM px',
                      parameters={k: v for k, v in vars(args).items() if k.startswith('aberration_')})

        def save() -> None:
            """Persiste la décision et les mesures même lors d'une panne du moteur."""
            report.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')

        try:
            if not args.aberration_model:
                result.update(status='skipped', reason='model_disabled')
                return result
            shape = fits.getheader(source)
            geometry = (int(shape['NAXIS2']), int(shape['NAXIS1']))
            layer = 1 if shape['NAXIS'] == 3 else 0
            before_path, after_path = work / 'before.tsv', work / 'after.tsv'
            context = QualityContext.for_catalogs(source, source)
            SirilStarDetectionProcessor(context, [(source, before_path)], work, layer,
                                        name='before').post_process(source, None, args)
            stars, _ = read_star_catalog(before_path)
            stars = [s for s in stars if s['layer'] == layer]
            initial = ReferenceStarMeasurementProcessor(
                context, stars, layer, getattr(args, 'deconvolution_match_radius', 1.),
            ).post_process(source, None, args)
            stars = initial['before']
            result['reference_measurements'] = initial['audit']
            before = self.baseline(stars, geometry, args.aberration_edge_fraction)
            logging.info('Aberration Remover — mesures initiales : %s', before)
            reasons = self.trigger(before, args)
            result.update(before=before, trigger_reasons=reasons)
            if before['all']['count'] < self.MIN_STARS:
                result.update(status='skipped', reason='insufficient_reference_stars')
                return result
            if not reasons and not args.aberration_force:
                result.update(status='skipped', reason=(
                    'insufficient_edge_coverage' if before['edge']['count'] < self.MIN_EDGE_STARS
                    else 'no_measured_defect'))
                return result
            logging.info('Aberration Remover — essai : %s', reasons or ['forced'])
            engine = AberrationEngine()
            engine.set_from_args(args)
            result['inference'] = engine.process(source, candidate)
            from lib.restoration_quality import QualityReference

            QualityReference.validate_metadata(source, candidate)
            with fits.open(candidate, memmap=False) as hdul:
                pixels = hdul[0].data
                if (pixels is None or pixels.shape[-2:] != geometry
                        or not np.isfinite(pixels).all()):
                    raise ValueError('Candidat Aberration Remover invalide')
            context = QualityContext.for_catalogs(source, candidate)
            SirilStarDetectionProcessor(context, [(candidate, after_path)], work, layer,
                                        name='after').post_process(candidate, None, args)
            comparison = StarComparisonProcessor(
                context, before_path, after_path, getattr(args, 'deconvolution_match_radius', 1.),
                self.MIN_STARS, self.MIN_RETENTION, layer, reference_images=True,
            ).post_process(candidate, None, args)
            result['comparison'] = comparison
            verdict = self.assess(comparison, before, geometry, reasons, args)
            result['quality'] = verdict
            result['diffuse_flux'] = self.measure_flux(
                source, candidate, work / 'diffuse_flux.json', args,
            )
            result.update(status='accepted' if verdict['accepted'] else 'retained_input',
                          output_image=str(candidate if verdict['accepted'] else source))
            logging.info('Aberration Remover — %s ; contrôles=%s', result['status'], verdict)
            return result
        except Exception as error:
            result.update(status='failed', error=str(error))
            raise
        finally:
            logging.info('Aberration Remover — décision=%s ; motif=%s ; sortie=%s',
                         result['status'], result.get('reason', result.get('quality', {})),
                         result['output_image'])
            save()
