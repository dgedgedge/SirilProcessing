"""Orchestration commune des candidats de restauration, indépendante des moteurs."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from shutil import copyfile
from typing import ClassVar, Literal

from lib.processor import processor
from lib.deconvolution_quality import log_quality_diagnostics
from lib.type_defs import JSONReport


class RestorationComparisonProcessor(processor):
    """Évalue les moteurs enfants, sélectionne un candidat accepté et écrit l’audit.

    Les sous-classes déclarent le moteur Siril et le moteur neuronal. Les moteurs
    traitent la même entrée et ne connaissent pas le choix final. La sauvegarde
    des paramètres reste extérieure à cette orchestration.
    """

    parameter_prefixes: ClassVar[tuple[str, ...]] = ('cosmic_', 'deconvolution_', 'denoise_')
    parameter_dependencies: ClassVar[frozenset[str]] = frozenset({'enable_clarity'})
    operation: ClassVar[Literal['sharpen', 'denoise']] = 'denoise'
    siril_processor: ClassVar[type[processor]]

    @classmethod
    def cosmic_processor(cls) -> type[processor]:
        """Retourne la classe neuronale à importer sans charger PyTorch."""
        from lib.cosmic_clarity.processors import (
            CosmicClaritySharpenProcessor, CosmicClarityDenoiseProcessor,
        )
        return CosmicClaritySharpenProcessor if cls.operation == 'sharpen' else CosmicClarityDenoiseProcessor

    @classmethod
    def parameter_children(cls) -> tuple[type[processor], ...]:
        """Déclare tous les moteurs avant leur instanciation éventuelle."""
        return (cls.siril_processor, cls.cosmic_processor())

    @classmethod
    def add_shared_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare une seule fois les options neuronales communes aux deux étapes."""
        if not any(action.dest == 'enable_clarity' for action in parser._actions):
            from lib.cosmic_clarity.processors import add_arguments

            add_arguments(parser)

    def post_process(
        self, input_path: Path | str, output_path: Path | str,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Compare les moteurs, ou délègue uniquement à Siril lorsque Clarity est désactivé.

        Un refus de qualité conserve l’entrée. Les erreurs techniques sont
        propagées avec l’audit disponible ; le refus de déconvolution Siril
        n’empêche pas de retenir le moteur neuronal.
        """
        args = self._get_args(args)
        source, report = Path(input_path).resolve(), Path(output_path).resolve()
        destination = Path(
            (getattr(args, 'deconvolution_output', None) if self.operation == 'sharpen' else None)
            or report.with_suffix('.fits')
        ).resolve()
        if source == report or destination in (source, report):
            raise ValueError('Les sorties doivent être différentes de la source et du rapport')
        if destination.suffix.lower() not in ('.fit', '.fits', '.fts'):
            raise ValueError('Une sortie FITS est requise')
        siril = self.siril_processor()
        if not getattr(args, 'enable_clarity', True):
            return siril.post_process(source, report, args)
        report.parent.mkdir(parents=True, exist_ok=True)
        result = dict(image_path=str(source), engine='dual', status='evaluating', evaluations={})
        def _sharpen_score(report_data: JSONReport) -> float:
            """Score de netteté : plus élevé = meilleur candidat accepté."""
            comparison = report_data['comparison']
            artifacts = report_data['artifacts']
            mean_gain = (
                comparison['before_fwhm_px']['mean'] - comparison['after_fwhm_px']['mean']
            ) / comparison['before_fwhm_px']['mean']
            ratio_gain = 1 - comparison['paired_ratio']['median']
            return (
                2.0 * float(mean_gain)
                + 2.0 * float(ratio_gain)
                - 0.2 * float(max(0.0, artifacts['max_noise_ratio'] - 1))
                - 0.2 * float(artifacts['max_ring_fraction'])
            )

        def _denoise_score(report_data: JSONReport) -> float:
            """Score de débruitage : réduction du bruit avec pénalité de flou/anneaux."""
            comparison = report_data['comparison']
            artifacts = report_data['artifacts']
            blur = max(
                comparison['after_fwhm_px']['mean'] / comparison['before_fwhm_px']['mean'],
                comparison['paired_ratio']['median'],
            )
            return (
                float(1 - artifacts['max_noise_ratio'])
                - 0.5 * float(max(0.0, blur - 1.0))
                - 0.2 * float(artifacts['max_ring_fraction'])
            )

        treatment = 'Déconvolution' if self.operation == 'sharpen' else 'Débruitage'
        engine_labels = {'cosmic_clarity': 'Cosmic Clarity', 'siril': 'Siril'}

        def log_evaluation(engine_name: str, evaluation: JSONReport) -> None:
            """Journalise le verdict, les motifs et les mesures disponibles du candidat.

            Les variations sont relatives à l’entrée, en pourcentage ; les
            FWHM sont en pixels. Une mesure absente reste non évaluée.
            """
            reasons = evaluation.get('rejection_reasons') or evaluation.get(
                'quality', {},
            ).get('reasons') or []
            if evaluation.get('error'):
                reasons = [*reasons, evaluation['error']]
            logging.info(
                '%s — évaluation %s : statut=%s ; motifs=%s',
                treatment, engine_labels[engine_name], evaluation.get('status'),
                ', '.join(reasons) if reasons else 'aucun',
            )
            comparison = evaluation.get('comparison', {})
            log_quality_diagnostics(
                f'{treatment} — {engine_labels[engine_name]}', comparison,
                evaluation.get('quality', {}), evaluation.get('artifacts'),
            )
            if comparison.get('status') == 'validated':
                before_mean = comparison['before_fwhm_px']['mean']
                after_mean = comparison['after_fwhm_px']['mean']
                logging.info(
                    '%s — %s : FWHM moyenne %.4f → %.4f px ; '
                    'gain de finesse moyen %+.2f %% ; gain médian apparié %+.2f %%',
                    treatment, engine_labels[engine_name], before_mean, after_mean,
                    100 * (1 - after_mean / before_mean),
                    100 * (1 - comparison['paired_ratio']['median']),
                )
            elif evaluation.get('attempts'):
                logging.info('%s — %s : aucun essai retenu ; mesures et rejets '
                             'détaillés ci-dessus pour chacun des %d essais',
                             treatment, engine_labels[engine_name], len(evaluation['attempts']))
            else:
                logging.info(
                    '%s — %s : finesse non évaluée (appariement=%s)',
                    treatment, engine_labels[engine_name],
                    comparison.get('status', 'indisponible'),
                )
            artifacts = evaluation.get('artifacts')
            if artifacts:
                logging.info(
                    '%s — %s : variation maximale du bruit %+.2f %% ; '
                    'fraction maximale d’étoiles avec nouveaux anneaux %.2f %%',
                    treatment, engine_labels[engine_name],
                    100 * (artifacts['max_noise_ratio'] - 1),
                    100 * artifacts['max_ring_fraction'],
                )

        def save() -> None:
            """Conserve les évaluations et le verdict global en JSON UTF-8."""
            report.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')

        save()
        try:
            cosmic_class = self.cosmic_processor()
            cosmic = cosmic_class()
            try:
                cosmic_report = cosmic.post_process(source, report, args)
            except Exception:
                # Le moteur écrit son audit avant de propager une erreur technique.
                try:
                    failed_audit = json.loads(report.read_text(encoding='utf-8'))
                    if isinstance(failed_audit, dict) and failed_audit.get('engine') == 'cosmic-clarity':
                        result['evaluations']['cosmic_clarity'] = failed_audit
                except (OSError, json.JSONDecodeError) as audit_error:
                    logging.warning('Audit neuronal illisible : %s', audit_error)
                raise
            result.update({key: value for key, value in cosmic_report.items()
                           if key not in ('engine', 'status', 'evaluations')})
            result['evaluations']['cosmic_clarity'] = cosmic_report
            log_evaluation('cosmic_clarity', cosmic_report)
            save()
            logging.info('%s — début de l’évaluation Siril', treatment)
            siril_report_path = report.with_name(report.stem + '_siril.json')
            try:
                siril_report = siril.post_process(source, siril_report_path, args)
            except RuntimeError as error:
                if self.operation != 'sharpen' or 'Aucune déconvolution efficace' not in str(error):
                    raise
                siril_report = (json.loads(siril_report_path.read_text(encoding='utf-8'))
                                if siril_report_path.is_file() else {})
                siril_report.update(engine='siril', status='no_safe_improvement',
                                    error=str(error), output_image=str(source))
            siril_report = dict(siril_report, engine='siril')
            result['evaluations']['siril'] = siril_report
            log_evaluation('siril', siril_report)
            save()
            candidates = []
            for engine_name, report_data in result['evaluations'].items():
                if report_data.get('status') != 'accepted':
                    continue
                if self.operation == 'sharpen':
                    if not report_data.get('comparison') or not report_data.get('artifacts'):
                        continue
                    score = _sharpen_score(report_data)
                else:
                    if not report_data.get('comparison') or not report_data.get('artifacts'):
                        continue
                    score = _denoise_score(report_data)
                logging.info(
                    '%s — score %s : %.6f (plus élevé = meilleur)',
                    treatment, engine_labels[engine_name], score,
                )
                candidates.append((score, engine_name, report_data))

            if candidates:
                candidates.sort(key=lambda item: item[0], reverse=True)
                score, selected_engine, selected = candidates[0]
                selected_path = Path(selected['output_image']).resolve()
                if not selected_path.is_file():
                    raise FileNotFoundError(
                        f"Sortie sélectionnée introuvable ({selected_engine}): {selected_path}"
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                copyfile(selected_path, destination)
                result.update(
                    status='accepted',
                    output_image=str(destination),
                    selected_engine=selected_engine,
                    selection_score=score,
                    comparison=selected.get('comparison'),
                    artifacts=selected.get('artifacts'),
                    rejection_reasons=[],
                )
                if 'quality' in selected:
                    result['quality'] = selected['quality']
            else:
                if self.operation == 'sharpen':
                    result.update(
                        status='no_safe_improvement',
                        output_image=str(source),
                        selected_engine=None,
                    )
                else:
                    result.update(
                        status='rejected',
                        output_image=str(source),
                        selected_engine=None,
                    )
                result['rejection_reasons'] = sorted(
                    {
                        reason
                        for report_data in result['evaluations'].values()
                        for reason in report_data.get('rejection_reasons', [])
                    }
                )
            save()
            if result['selected_engine'] is not None:
                logging.info(
                    '%s — choix final : %s ; score=%.6f ; '
                    'meilleur score parmi %d candidat(s) accepté(s) ; sortie=%s',
                    treatment, engine_labels[result['selected_engine']],
                    result['selection_score'], len(candidates), result['output_image'],
                )
            else:
                logging.info(
                    '%s — choix final : image d’entrée conservée ; '
                    'aucun candidat accepté ; statut=%s ; image=%s',
                    treatment, result['status'], result['output_image'],
                )
            return result
        except Exception as error:
            if result['status'] != 'no_safe_improvement':
                result['status'] = 'failed'
            result['error'] = str(error)
            save()
            raise
