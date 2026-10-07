"""Adaptateurs Cosmic Clarity avec les contrôles appariés du pipeline Siril.

Le calcul neuronal est optionnel et chargé à la demande ; Siril reste requis
pour les catalogues stellaires. Voir docs/scripts/postProcess/cosmic-clarity.md.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from shutil import copyfile
from typing import Literal

import numpy as np
from astropy.io import fits

from lib.cosmic_clarity.models import DEFAULT_MODEL_DIR
from lib.deconvolution import _quote, compare_star_catalogs, read_star_catalog
from lib.deconvolution_quality import artifact_metrics, assess_denoising, assess_quality
from lib.postprocess_paths import reset_ordered_work_dir
from lib.processor import processor
from lib.siril_utils import create_siril_from_args
from lib.type_defs import JSONReport


class _ToggleClarityAction(argparse.Action):
    """Mappe --enable-clarity/--disable-clarity vers un booléen unique."""

    def __init__(self, option_strings: list[str], dest: str, **kwargs: object) -> None:
        super().__init__(option_strings=option_strings, dest=dest, nargs=0, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, option_string == '--enable-clarity')


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Déclare le moteur et les réglages persistants, sans dépendance à PyTorch."""
    group = parser.add_argument_group('Moteur de restauration')
    group.add_argument(
        '--enable-clarity',
        '--disable-clarity',
        dest='enable_clarity',
        action=_ToggleClarityAction,
        default=True,
        help=(
            'Active (--enable-clarity, défaut) ou désactive (--disable-clarity) '
            'le pipeline comparatif Cosmic Clarity + Siril'
        ),
    )
    group.add_argument('--cosmic-model-dir', default=str(DEFAULT_MODEL_DIR),
                       help='Dossier des poids installés par --install-cosmic-clarity dans le .sh')
    group.add_argument('--cosmic-device', choices=('cuda', 'cpu'), default='cuda',
                       help='CUDA requis par défaut ; aucun repli implicite')
    group.add_argument('--cosmic-stellar-amount', type=float, default=0.5,
                       help='Mélange stellaire entre 0 et 1')
    group.add_argument('--cosmic-nonstellar-amount', type=float, default=0.5,
                       help='Mélange des structures diffuses entre 0 et 1')
    group.add_argument('--cosmic-nonstellar-radius', type=float, default=3.0,
                       help='Rayon du modèle entre 1 et 8 pixels, interpolation entre poids')
    group.add_argument(
        '--cosmic-sharpen-mode', choices=('luminance', 'separate'),
        default='luminance', help='Netteté sur luminance (défaut) ou R/V/B séparés',
    )
    group.add_argument(
        '--cosmic-denoise-mode', choices=('luminance', 'full', 'separate'),
        default='luminance',
        help=('Débruitage : luminance (défaut), luminance + chrominance, '
              'ou R/V/B séparés'),
    )
    group.add_argument(
        '--cosmic-color-denoise-amount', type=float, default=None,
        help=('Intensité chrominance en mode full, entre 0 et 1 ; '
              'défaut : intensité débruitage'),
    )
    group.add_argument('--cosmic-denoise-amount', type=float, default=0.5,
                       help='Mélange du débruitage entre 0 et 1')


class _CosmicClarityProcessor(processor):
    """Produit un candidat neuronal puis applique les règles communes de qualité."""

    operation: Literal['sharpen', 'denoise'] = 'sharpen'
    parameter_persistence = {'deconvolution_output': False}

    def get_prefix(self) -> str:
        """Conserve les noms des rapports et options d'activation historiques."""
        return 'deconvolution' if self.operation == 'sharpen' else 'denoise'

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Expose les seuils historiques pour une utilisation indépendante de la classe."""
        if self.operation == 'sharpen':
            from lib.deconvolution import add_arguments as add_quality_arguments
        else:
            from lib.denoising import add_arguments as add_quality_arguments
        add_quality_arguments(parser)

    @staticmethod
    def _auto_matching_thresholds(
        before_catalog: Path, layer: int,
    ) -> tuple[float, int, float, dict[str, float | int | str]]:
        """Calcule des seuils d'appariement adaptés au champ mesuré.

        Le rayon suit la FWHM médiane, et les seuils de volume sont ajustés
        à la densité du catalogue avant traitement. En absence d'étoiles
        valides, l'appelant doit revenir aux seuils manuels.
        """
        stars, _ = read_star_catalog(before_catalog)
        layer_stars = [star for star in stars if star['layer'] == layer]
        count = len(layer_stars)
        if count == 0:
            raise ValueError('Aucune étoile valide pour calculer les seuils auto')
        median_fwhm = float(np.median([star['fwhm'] for star in layer_stars]))
        match_radius = float(np.clip(0.35 * median_fwhm, 0.8, 2.0))
        minimum = int(np.clip(round(count * 0.30), 3, 30))
        if count < 20:
            fraction = 0.50
        elif count < 60:
            fraction = 0.60
        else:
            fraction = 0.70
        metadata: dict[str, float | int | str] = {
            'mode': 'auto',
            'valid_stars_before': count,
            'median_fwhm_before_px': median_fwhm,
            'match_radius_px': match_radius,
            'min_stars': minimum,
            'min_match_fraction': fraction,
        }
        return match_radius, minimum, fraction, metadata

    @staticmethod
    def _matching_thresholds(
        args: argparse.Namespace, before_catalog: Path, layer: int,
    ) -> tuple[float, int, float, dict[str, float | int | str]]:
        """Choisit le mode auto par défaut, ou manuel si explicitement forcé."""
        manual_radius = float(getattr(args, 'deconvolution_match_radius', 1.0))
        manual_minimum = int(getattr(args, 'deconvolution_min_stars', 10))
        manual_fraction = float(getattr(args, 'deconvolution_min_match_fraction', 0.7))
        forced_manual = bool(getattr(args, 'deconvolution_force_manual_matching', False))
        if forced_manual:
            return manual_radius, manual_minimum, manual_fraction, {
                'mode': 'forced-manual',
                'match_radius_px': manual_radius,
                'min_stars': manual_minimum,
                'min_match_fraction': manual_fraction,
            }
        try:
            return _CosmicClarityProcessor._auto_matching_thresholds(before_catalog, layer)
        except (OSError, ValueError):
            return manual_radius, manual_minimum, manual_fraction, {
                'mode': 'manual-fallback-no-stars',
                'match_radius_px': manual_radius,
                'min_stars': manual_minimum,
                'min_match_fraction': manual_fraction,
            }

    def post_process(
        self, input_path: Path | str, output_path: Path | str,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Évalue Cosmic Clarity et Siril, puis sélectionne le meilleur candidat sûr.

        Conserve dans l’audit les essais Siril, y compris les erreurs locales
        de PSF et de repli. Sans amélioration Siril sûre, Cosmic Clarity reste
        sélectionnable ; sans candidat accepté, l’image source est conservée.
        Journalise les verdicts, mesures, scores et le choix pour chaque étape.
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
        maximum_blur = getattr(args, 'denoise_max_blur', 0.03)
        quality = dict(
            min_gain=getattr(args, 'deconvolution_min_gain', 0.02),
            max_noise=getattr(args, 'deconvolution_max_noise', 1.15),
            max_rings=getattr(args, 'deconvolution_max_rings', 0.1),
        )
        thresholds = [maximum_blur, *quality.values()]
        if (not np.isfinite(thresholds).all()
                or not 0 <= maximum_blur <= 1
                or not 0 < quality['min_gain'] < 1 or quality['max_noise'] < 1
                or not 0 <= quality['max_rings'] <= 1):
            raise ValueError('Seuils de contrôle de qualité invalides')
        with fits.open(source, memmap=False) as hdul:
            shape = hdul[0].shape
        if len(shape) not in (2, 3) or (len(shape) == 3 and shape[0] != 3):
            raise ValueError('Image FITS mono ou RGB requise')
        layer = getattr(args, 'deconvolution_layer', None) if self.operation == 'sharpen' else None
        layer = (1 if len(shape) == 3 else 0) if layer is None else layer
        if layer not in ([0, 1, 2] if len(shape) == 3 else [0]):
            raise ValueError('Canal de mesure incompatible avec cette image')
        report.parent.mkdir(parents=True, exist_ok=True)
        work = reset_ordered_work_dir(report)
        candidate = work / 'candidate.fits'
        before, after = work / 'before.tsv', work / 'after.tsv'
        result = dict(
            image_path=str(source),
            engine='dual',
            status='evaluating',
            candidate_image_path=str(candidate),
            layer=layer,
            script_path=str(work / 'quality.sps'),
            log_path=str(work / 'quality.log'),
            evaluations={},
        )

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
            """Persiste l'état de l'audit même si l'étape échoue."""
            report.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')

        save()
        try:
            try:
                from lib.cosmic_clarity.inference import CosmicClarityEngine
            except ImportError as error:
                raise RuntimeError(
                    'Dépendances Cosmic Clarity absentes : lancer '
                    'bin/postProcess.sh --install-cosmic-clarity'
                ) from error
            engine = CosmicClarityEngine(
                Path(getattr(args, 'cosmic_model_dir', DEFAULT_MODEL_DIR)).expanduser().resolve(),
                getattr(args, 'cosmic_device', 'cuda'),
            )
            logging.info('%s — début de l’évaluation Cosmic Clarity', treatment)
            result['inference'] = engine.process(
                source, candidate, self.operation,
                stellar_amount=getattr(args, 'cosmic_stellar_amount', 0.5),
                nonstellar_amount=getattr(args, 'cosmic_nonstellar_amount', 0.5),
                radius=getattr(args, 'cosmic_nonstellar_radius', 3.0),
                denoise_amount=getattr(args, 'cosmic_denoise_amount', 0.5),
                sharpen_mode=getattr(args, 'cosmic_sharpen_mode', 'luminance'),
                denoise_mode=getattr(args, 'cosmic_denoise_mode', 'luminance'),
                color_denoise_amount=getattr(args, 'cosmic_color_denoise_amount', None),
            )
            lines = ['requires 1.4', f'cd {_quote(work)}', 'setext fit', 'set32bits']
            for image, catalog in ((source, before), (candidate, after)):
                lines += [f'load {_quote(image)}', 'setfindstar reset', 'setfindstar -gaussian',
                          f'findstar -layer={layer} -out={catalog.name}', 'close']
            if not create_siril_from_args(args).run_siril_script(
                '\n'.join(lines) + '\n', str(work), script_name='quality.sps',
            ):
                raise RuntimeError(f'Échec Siril : {work / "quality.log"}')
            if self.operation == 'sharpen':
                match_radius, minimum, fraction, matching = self._matching_thresholds(
                    args, before, layer,
                )
            else:
                match_radius, minimum, fraction = 1.0, 10, 0.7
                matching = {
                    'mode': 'fixed-default',
                    'match_radius_px': match_radius,
                    'min_stars': minimum,
                    'min_match_fraction': fraction,
                }
            if (match_radius <= 0 or minimum < 3 or not 0 < fraction <= 1
                    or not np.isfinite([match_radius, minimum, fraction]).all()):
                raise ValueError('Seuils d’appariement invalides')
            result['matching'] = matching
            comparison = compare_star_catalogs(
                before, after, match_radius, minimum, fraction, layer,
            )
            artifacts = {}
            if comparison['status'] == 'validated':
                artifacts = artifact_metrics(source, candidate, comparison['pairs'])
            if self.operation == 'sharpen':
                verdict = assess_quality(comparison, artifacts, **quality)
                reasons = verdict['reasons']
                cosmic_report: JSONReport = dict(
                    engine='cosmic-clarity',
                    status='accepted' if not reasons else 'rejected',
                    output_image=str(candidate if not reasons else source),
                    matching=matching,
                    comparison=comparison,
                    artifacts=artifacts,
                    quality=verdict,
                    rejection_reasons=reasons,
                )
            else:
                reasons = assess_denoising(comparison, artifacts, max_blur=maximum_blur)
                cosmic_report = dict(
                    engine='cosmic-clarity',
                    status='accepted' if not reasons else 'rejected',
                    output_image=str(candidate if not reasons else source),
                    matching=matching,
                    comparison=comparison,
                    artifacts=artifacts,
                    rejection_reasons=reasons,
                )
            result['evaluations']['cosmic_clarity'] = cosmic_report
            log_evaluation('cosmic_clarity', cosmic_report)
            save()
            logging.info('%s — début de l’évaluation Siril', treatment)

            siril_report_path = report.with_name(report.stem + '_siril.json')
            if self.operation == 'sharpen':
                from lib.deconvolution import post_process as siril_post_process

                try:
                    siril_report = siril_post_process(source, siril_report_path, args)
                except RuntimeError as error:
                    if 'Aucune déconvolution efficace' not in str(error):
                        raise
                    siril_report = (
                        json.loads(siril_report_path.read_text(encoding='utf-8'))
                        if siril_report_path.is_file() else {}
                    )
                    siril_report.update({
                        'engine': 'siril',
                        'status': 'no_safe_improvement',
                        'error': str(error),
                        'output_image': str(source),
                    })
            else:
                from lib.denoising import post_process as siril_post_process

                siril_report = siril_post_process(source, siril_report_path, args)
                siril_report = dict(siril_report)
                siril_report['engine'] = 'siril'
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


class CosmicClaritySharpenProcessor(_CosmicClarityProcessor):
    """Remplace la déconvolution Siril par les réseaux stellaire et non stellaire."""

    operation = 'sharpen'


class CosmicClarityDenoiseProcessor(_CosmicClarityProcessor):
    """Remplace NL-Bayes par le réseau de débruitage, après la netteté."""

    operation = 'denoise'
