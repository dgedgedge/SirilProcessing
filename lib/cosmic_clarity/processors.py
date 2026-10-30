"""Adaptateurs Cosmic Clarity avec les contrôles appariés du pipeline Siril.

Le calcul neuronal est optionnel et chargé à la demande ; Siril reste requis
pour les catalogues stellaires. Voir docs/scripts/postProcess/cosmic-clarity.md.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Literal

import numpy as np
from astropy.io import fits

from lib.compute_config import ComputeConfig
from lib.cosmic_clarity.models import DEFAULT_MODEL_DIR
from lib.resource_cache import ResourceCache
from lib.deconvolution import _quote, compare_star_catalogs, read_star_catalog
from lib.deconvolution_quality import (
    artifact_metrics, assess_denoising, denoising_criteria, assess_quality,
)
from lib.postprocess_paths import reset_ordered_work_dir
from lib.processor import processor
from lib.siril_utils import Siril
from lib.type_defs import JSONReport


# Conserve la frontière historique entre petits et grands catalogues.
LARGE_MATCH_SAMPLE = 60
MATCH_FRACTION_WARNING = 0.70
MIN_REPRESENTATIVE_GROUP = 10
# Une strate doit conserver au moins la moitié du taux global d’appariement.
MIN_RELATIVE_GROUP_RETENTION = 0.5


def assess_matching_representation(
    before_catalog: Path, comparison: JSONReport, layer: int,
) -> JSONReport:
    """Contrôle couverture spatiale et flux des étoiles appariées d’un grand catalogue.

    Les quadrants sont définis par les médianes X/Y du catalogue initial et les
    classes de flux par ses quartiles. Le flux relatif gaussien est proportionnel
    à A*FWHMx*FWHMy : il sert seulement à classer les étoiles avant traitement.
    Une strate de dix étoiles ou plus doit retenir au moins la moitié du taux
    global. Ce facteur est une heuristique conservatrice, pas un test statistique
    calibré ; les strates trop petites sont signalées sans verdict individuel.
    L’absence d’amplitudes exploitables empêche de valider la représentativité.
    """
    stars, _ = read_star_catalog(before_catalog)
    stars = [star for star in stars if star['layer'] == layer]
    reference = comparison.get('reference_measurements')
    if reference is not None:
        eligible = set(reference['eligible_reference_ids'])
        stars = [star for star in stars if star['id'] in eligible]
    matched = {pair['reference_id'] for pair in comparison['pairs']}
    if not stars:
        return dict(accepted=False, reasons=['missing_reference_stars'], groups=[])
    retained = np.array([star['id'] in matched for star in stars])
    fraction = float(retained.mean())
    x = np.array([star['x'] for star in stars])
    y = np.array([star['y'] for star in stars])
    spatial = (x >= np.median(x)).astype(int) + 2 * (y >= np.median(y)).astype(int)
    groups = []
    reasons = []

    def assess_groups(labels: np.ndarray, kind: str) -> None:
        """Enregistre les taux de rétention des strates initiales non vides."""
        for label in np.unique(labels):
            mask = labels == label
            total = int(mask.sum())
            count = int(retained[mask].sum())
            rate = count / total
            evaluated = total >= MIN_REPRESENTATIVE_GROUP
            accepted = not evaluated or rate >= max(fraction, comparison.get("min_match_fraction", 0)) * MIN_RELATIVE_GROUP_RETENTION
            groups.append(dict(kind=kind, index=int(label), reference_count=total,
                               matched_count=count, retention=rate,
                               evaluated=evaluated, accepted=accepted))
            if not accepted:
                reasons.append(f'underrepresented_{kind}_group_{int(label)}')

    assess_groups(spatial, 'spatial')
    flux = np.array([star.get('amplitude', np.nan) * star['fwhm_x'] * star['fwhm_y']
                     for star in stars])
    if not np.isfinite(flux).all() or np.any(flux <= 0):
        reasons.append('missing_valid_brightness')
    else:
        labels = np.searchsorted(np.quantile(flux, [0.25, 0.5, 0.75]), flux, side='right')
        assess_groups(labels, 'brightness')
    return dict(accepted=not reasons, reasons=reasons, groups=groups,
                matched_fraction=fraction, min_group_size=MIN_REPRESENTATIVE_GROUP,
                min_relative_retention=MIN_RELATIVE_GROUP_RETENTION,
                brightness_proxy='A * FWHMx * FWHMy (before)')


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
    """Déclare moteur, politique de calcul et réglages sans charger PyTorch."""
    ComputeConfig.add_arguments(parser)
    group = (parser.add_argument_group('Moteur de restauration')
             if isinstance(parser, argparse.ArgumentParser) else parser)
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
    group.add_argument('--cosmic-model-dir', type=ResourceCache.cosmic_argument,
                       default=str(DEFAULT_MODEL_DIR),
                       help='Dossier des poids téléchargés à la demande ou préinstallés par le .sh')
    group.add_argument('--cosmic-device', choices=('auto', 'cuda', 'mps', 'cpu'), default=None,
                       help='Surcharge Cosmic Clarity ; sinon --compute-backend')
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

    parameter_prefixes = ('quality_', 'deconvolution_', 'denoise_', 'cosmic_')

    @classmethod
    def get_prefix(cls) -> str:
        """Conserve les noms des rapports et options d'activation historiques."""
        return 'deconvolution' if cls.operation == 'sharpen' else 'denoise'

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Expose les seuils historiques pour une utilisation indépendante de la classe."""
        if cls.operation == 'sharpen':
            from lib.deconvolution import SirilDeconvolutionProcessor as quality_processor
        else:
            from lib.denoising import SirilNoiseReductionProcessor as quality_processor
        quality_processor.add_arguments(parser)

    @staticmethod
    def _auto_matching_thresholds(
        before_catalog: Path, layer: int,
    ) -> tuple[float, int, float, dict[str, float | int | str]]:
        """Calcule des seuils d'appariement adaptés au champ mesuré.

        Le rayon suit la FWHM médiane et le minimum de correspondances dépend
        du nombre initial. La référence de fraction reste 70 % : bloquante pour
        un petit catalogue, alerte avec contrôle de représentativité à partir
        de LARGE_MATCH_SAMPLE. Sans étoiles valides, retour aux seuils manuels.
        """
        stars, _ = read_star_catalog(before_catalog)
        layer_stars = [star for star in stars if star['layer'] == layer]
        count = len(layer_stars)
        if count == 0:
            raise ValueError('Aucune étoile valide pour calculer les seuils auto')
        median_fwhm = float(np.median([star['fwhm'] for star in layer_stars]))
        match_radius = float(np.clip(0.35 * median_fwhm, 0.8, 2.0))
        minimum = int(np.clip(round(count * 0.30), 3, 30))
        fraction = MATCH_FRACTION_WARNING
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
        if getattr(args, 'quality_mode', 'reliable') != 'legacy':
            radius = getattr(args, 'deconvolution_match_radius', 1.0)
            minimum = getattr(args, 'deconvolution_min_stars', 10)
            fraction = getattr(args, 'deconvolution_min_match_fraction', .7)
            return radius, minimum, fraction, dict(
                mode='common', match_radius_px=radius, min_stars=minimum,
                min_match_fraction=fraction,
            )
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
        """Produit et contrôle uniquement le candidat Cosmic Clarity.

        Retourne l’entrée si la qualité est refusée, sans évaluer le moteur
        Siril ni sélectionner un gagnant. L’orchestrateur réalise cette sélection.
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
        layer = getattr(args, 'deconvolution_layer', None)
        layer = (1 if len(shape) == 3 else 0) if layer is None else layer
        if layer not in ([0, 1, 2] if len(shape) == 3 else [0]):
            raise ValueError('Canal de mesure incompatible avec cette image')
        report.parent.mkdir(parents=True, exist_ok=True)
        work = reset_ordered_work_dir(report)
        candidate = work / 'candidate.fits'
        before, after = work / 'before.tsv', work / 'after.tsv'
        result = dict(
            image_path=str(source),
            engine='cosmic-clarity',
            status='evaluating',
            candidate_image_path=str(candidate),
            layer=layer,
            script_path=str(work / 'quality.sps'),
            log_path=str(work / 'quality.log'),
        )

        treatment = 'Déconvolution' if self.operation == 'sharpen' else 'Débruitage'

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
                    'bin/install_dependances.sh'
                ) from error
            engine = CosmicClarityEngine(
                Path(getattr(args, 'cosmic_model_dir', DEFAULT_MODEL_DIR)).expanduser().resolve(),
                ComputeConfig.requested(args, 'cosmic_device'),
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
            if not Siril(
                siril_path=getattr(args, "siril_path", None),
                siril_mode=getattr(args, "siril_mode", None),
            ).run_siril_script(
                '\n'.join(lines) + '\n', str(work), script_name='quality.sps',
            ):
                raise RuntimeError(f'Échec Siril : {work / "quality.log"}')
            if self.operation == 'sharpen':
                match_radius, minimum, fraction, matching = self._matching_thresholds(
                    args, before, layer,
                )
            else:
                match_radius = getattr(args, 'deconvolution_match_radius', 1.0)
                minimum = getattr(args, 'deconvolution_min_stars', 10)
                fraction = getattr(args, 'deconvolution_min_match_fraction', .7)
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
            large_auto = (
                self.operation == 'sharpen' and matching['mode'] == 'auto'
                and matching['valid_stars_before'] >= LARGE_MATCH_SAMPLE
            )
            comparison = compare_star_catalogs(
                before, after, match_radius, minimum,
                0.0 if large_auto else fraction, layer,
                reference_images=(source, candidate)
                if getattr(args, 'deconvolution_reference_measurements', True) else None,
                quality_mode=getattr(args, 'quality_mode', 'reliable'),
            )
            if large_auto and comparison['valid_before_count'] < LARGE_MATCH_SAMPLE:
                large_auto = False
                comparison['min_match_fraction'] = fraction
                if comparison['matched_fraction_before'] < fraction:
                    comparison['status'] = 'insufficient_matches'

            if large_auto:
                matching['fraction_policy'] = 'warning_with_representation_checks'
                matching['warning_fraction'] = fraction
                if comparison['matched_fraction_before'] < fraction:
                    comparison['warnings'] = ['low_matching_fraction']
                    logging.warning(
                        'Déconvolution Cosmic Clarity : %d/%d étoiles appariées '
                        '(%.2f %%), référence %.0f %% ; contrôle de représentativité',
                        comparison['matched_count'], comparison['valid_before_count'],
                        100 * comparison['matched_fraction_before'], 100 * fraction,
                    )
                if comparison['status'] == 'validated':
                    representation = assess_matching_representation(before, comparison, layer)
                    comparison['representation'] = representation
                    if not representation['accepted']:
                        comparison['status'] = 'unrepresentative_matches'
                    logging.info('Représentativité Cosmic Clarity : %s ; motifs=%s',
                                 representation['accepted'], representation['reasons'])
            else:
                matching['fraction_policy'] = 'blocking'

            artifacts = {}
            if comparison['status'] == 'validated':
                artifacts = artifact_metrics(source, candidate, comparison['pairs'], args=args)
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
                result['criteria'] = denoising_criteria(comparison, artifacts, maximum_blur)
                cosmic_report = dict(
                    engine='cosmic-clarity',
                    status='accepted' if not reasons else 'rejected',
                    output_image=str(candidate if not reasons else source),
                    matching=matching,
                    comparison=comparison,
                    artifacts=artifacts,
                    rejection_reasons=reasons,
                )
            result.update(cosmic_report)
            save()
            return result
        except Exception as error:
            if result['status'] != 'no_safe_improvement':
                result['status'] = 'failed'
            result['error'] = str(error)
            save()
            raise


class CosmicClaritySharpenProcessor(_CosmicClarityProcessor):
    """Candidat neuronal de netteté, sans orchestration du moteur Siril."""

    operation = 'sharpen'


class CosmicClarityDenoiseProcessor(_CosmicClarityProcessor):
    """Candidat neuronal de débruitage, sans orchestration du moteur Siril."""

    operation = 'denoise'
