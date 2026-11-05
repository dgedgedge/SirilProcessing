"""Étirements GHS successifs guidés par les quartiles de l'axe des intensités.

La plus longue droite continue de log10(effectifs) est cherchée après le pic,
jusqu’à 0,875, sans objectif de pente nulle. Un niveau log cible dans le
canal de référence complète la longueur sans gain au-delà de la cible.
Les gardes photométriques restent cumulatives depuis l'entrée de cette phase.
Voir docs/scripts/postProcess/ghs-quartiles.md.
"""

from __future__ import annotations

import argparse
import logging
import math
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import numpy as np

from lib.ghs_final_stretch import (
    ProtectedGHSFinalizer, MAX_SAMPLE_PIXELS, MAX_FINAL_BACKGROUND,
    MAX_FINAL_NOISE_GAIN, MAX_NEW_NEAR_WHITE, MAX_COLOR_DRIFT, MAX_CHROMA_LOSS,
)
from lib.ghs_log_histogram import HISTOGRAM_BINS
from lib.ghs_linear_histogram import (
    DEFAULT_LINEARITY_TOLERANCE, linear_histogram_profile, symmetry_points,
    DEFAULT_TAIL_HEIGHT, TAIL_CHANNELS, TAIL_HISTOGRAM_BINS, TAIL_HEIGHT_WEIGHT,
    add_tail_height_objective, log_tail_height,
)
from lib.processor import processor
from lib.star_separation import validate_rgb
from lib.type_defs import JSONReport

if TYPE_CHECKING:
    from lib.auto_ghs import ComputeBackend, HistogramAnalyzer

LOGGER = logging.getLogger(__name__)
# Un quart de classe de bonus RMS ne peut compenser une classe de longueur.
MIN_IMPROVEMENT = 1e-5
MAX_FULL_CANDIDATES = 3
BLACKPOINT_BINS = 1024
VALLEY_RATIO = .2


def quartile_profile(
    counts: np.ndarray, tolerance: float = DEFAULT_LINEARITY_TOLERANCE,
) -> JSONReport:
    """Expose le profil de longueur rectiligne sous le nom historique de l'API."""
    return linear_histogram_profile(counts, tolerance)


def isolated_blackpoint(data: np.ndarray, budget: float) -> float:
    """Propose le creux séparant un petit pic RGB sombre du support principal.

    Le budget porte sur les pixels dont au moins un canal positif serait annulé,
    en excluant les pixels déjà entièrement noirs. Les effectifs RGB servent
    seulement à détecter le creux ; une validation pleine résolution reste requise.
    Aucun pic ou creux n'est inventé dans une distribution monotone.
    """
    from scipy.signal import find_peaks

    if not np.isfinite(budget) or not 0 <= budget <= .01:
        raise ValueError('Budget de point noir hors de [0,0.01]')
    validate_rgb(data)
    if budget == 0:
        return 0.0
    values = data[data > 0]
    if values.size < 32 or np.ptp(values) <= 1e-7:
        return 0.0
    high = float(np.quantile(values, .999))
    counts, edges = np.histogram(values, bins=BLACKPOINT_BINS, range=(0, high))
    smooth = np.convolve(counts, [1/4, 1/2, 1/4], mode='same')
    # Le remplissage nul permet de détecter aussi un petit pic proche de zéro.
    peaks = find_peaks(np.pad(smooth, 1), prominence=max(2, smooth.max() * .001))[0] - 1
    active = np.any(data > 0, axis=0)
    for left, right in zip(peaks[:-1], peaks[1:]):
        if right - left < 3:
            continue
        valley = left + 1 + int(np.argmin(smooth[left + 1:right]))
        if smooth[valley] > VALLEY_RATIO * min(smooth[left], smooth[right]):
            continue
        point = float(edges[valley + 1])
        lost = np.any((data > 0) & (data <= point), axis=0)
        if np.count_nonzero(lost) / max(1, np.count_nonzero(active)) <= budget:
            return point
    return 0.0


class QuartileGHSProcessor(processor):
    """Réévalue SP, point noir et niveau relatif, sous gardes cumulatives."""

    parameter_dependencies = {'ghs_highlight_retention', 'ghs_color_mode'}

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie les paramètres de la recherche itérative de longueur rectiligne."""
        return 'ghs_quartile'

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare passages, linéarité, niveau log et perte noire autorisée."""
        parser.add_argument('--ghs-quartile-iterations', type=int, default=12,
                            help='Passes GHS pour allonger la droite logarithmique, de 0 (désactivé) à 64')
        parser.add_argument('--ghs-quartile-max-strength', type=float, default=10.0,
                            help='D maximal par passe de quartiles, dans ]0,10]')
        parser.add_argument('--ghs-linearity-tolerance', type=float, default=DEFAULT_LINEARITY_TOLERANCE,
                            help='RMS maximal de la droite log en dex, de 0,005 à 0,5 (défaut 0,05)')
        parser.add_argument('--ghs-tail-height', type=float, default=DEFAULT_TAIL_HEIGHT,
                            help='Hauteur log cible à 0,875, relative au pic du canal [0,1] ; 0 désactive')
        parser.add_argument('--ghs-tail-channel', choices=TAIL_CHANNELS, default='red',
                            help='Canal de référence du niveau log (défaut red)')
        parser.add_argument('--ghs-blackpoint-loss', type=float, default=.001,
                            help='Fraction cumulée de pixels annulables par pic sombre isolé (0 à 0,01)')

    @staticmethod
    def validate_options(args: argparse.Namespace) -> None:
        """Refuse limites non finies, booléennes ou hors domaine avant calcul."""
        if (type(args.ghs_quartile_iterations) is not int
                or not 0 <= args.ghs_quartile_iterations <= 64):
            raise ValueError('ghs-quartile-iterations doit être dans [0,64]')
        if (not np.isfinite(args.ghs_quartile_max_strength)
                or not 0 < args.ghs_quartile_max_strength <= 10):
            raise ValueError('ghs-quartile-max-strength doit être dans ]0,10]')
        if (not np.isfinite(args.ghs_linearity_tolerance)
                or not .005 <= args.ghs_linearity_tolerance <= .5):
            raise ValueError('ghs-linearity-tolerance doit être dans [0.005,0.5] dex')
        if not np.isfinite(args.ghs_tail_height) or not 0 <= args.ghs_tail_height <= 1:
            raise ValueError('ghs-tail-height doit être dans [0,1]')
        if args.ghs_tail_channel not in TAIL_CHANNELS:
            raise ValueError('ghs-tail-channel doit être red, green ou blue')
        if not np.isfinite(args.ghs_blackpoint_loss) or not 0 <= args.ghs_blackpoint_loss <= .01:
            raise ValueError('ghs-blackpoint-loss doit être dans [0,0.01]')

    def search(
        self, data: np.ndarray, analyzer: HistogramAnalyzer, backend: ComputeBackend,
        args: argparse.Namespace, stars: np.ndarray | None = None, headroom: float = .95,
    ) -> tuple[np.ndarray, JSONReport]:
        """Classe des passes CPU échantillonnées puis valide sur le backend choisi.

        D explore douze forces jusqu'à la limite ; B=15 et B=5, SP explore zéro,
        le pic et les ruptures de pente, HP=0,75 ou 1. Les étoiles restent fixes
        dans la recomposition. Le niveau log est mesuré sur cette recomposition
        lorsqu'elle est disponible, sinon sur le fond seul.
        Au plus trois finalistes sont contrôlés sur tous les pixels par passage.
        Le bruit et les pertes de contraste/couleur sont comparés à l'entrée,
        empêchant l'accumulation des tolérances. Aucun essai ne modifie la source.
        """
        from lib.auto_ghs import ghs_rgb, linear_blackpoint, luminance

        self.validate_options(args)
        validate_rgb(data)
        if stars is not None:
            validate_rgb(stars)
            if stars.shape != data.shape:
                raise ValueError('Étoiles et fond de géométries différentes')
        if not np.isfinite(headroom) or not 0 < headroom < 1:
            raise ValueError('ghs-headroom doit être dans ]0,1[')
        xp = backend.xp
        start = data
        current = data
        stride = max(1, math.ceil(math.sqrt(data.shape[1] * data.shape[2] / MAX_SAMPLE_PIXELS)))
        while math.ceil(data.shape[1] / stride) * math.ceil(data.shape[2] / stride) > MAX_SAMPLE_PIXELS:
            stride += 1
        fixed = ProtectedGHSFinalizer()
        sample_start = backend.numpy(start[:, ::stride, ::stride])
        thresholds = fixed.thresholds(sample_start, analyzer.color_mode)
        report: JSONReport = {'status': 'no admissible improvement', 'iterations': [],
                              'stop_reason': 'iteration limit', 'window': ['after_peak', .875],
                              'objective': ('longest_log_linear_span_with_tail_height'
                                            if args.ghs_tail_height > 0 else 'longest_contiguous_log_linear_span'),
                              'tail_height_target': args.ghs_tail_height,
                              'tail_height_channel': args.ghs_tail_channel,
                              'tail_height_weight': TAIL_HEIGHT_WEIGHT,
                              'tail_histogram_bins': TAIL_HISTOGRAM_BINS,
                              'tail_image_role': 'with_stars' if stars is not None else 'starless',
                              'linearity_tolerance_dex': args.ghs_linearity_tolerance,
                              'sample_stride': stride, 'blackpoint_loss': 0.0,
                              'blackpoint_budget': args.ghs_blackpoint_loss,
                              'max_strength': args.ghs_quartile_max_strength,
                              'max_iterations': args.ghs_quartile_iterations,
                              'highlight_retention': args.ghs_highlight_retention}
        if args.ghs_quartile_iterations == 0 or not thresholds:
            report.update(status='disabled' if args.ghs_quartile_iterations == 0
                          else 'insufficient positive support', stop_reason='not searched')
            return current, report
        # Mesures de protection fixes pour l'ensemble des passages.
        # Ici les quartiles désignent l'axe, pas les quantiles de population.
        thresholds['bright'], thresholds['core'] = .75, .95
        sample_before = fixed.measure(sample_start, sample_start, thresholds, analyzer.color_mode)
        sample_stars = backend.numpy(stars[:, ::stride, ::stride]) if stars is not None else None
        sample_fused = (headroom * sample_start + sample_stars * (1 - headroom * sample_start)
                        if sample_stars is not None else None)
        sample_fused_before = (fixed.measure(sample_fused, sample_fused, thresholds,
                                            analyzer.color_mode, mask_reference=sample_start)
                               if sample_fused is not None else None)
        full_before = fixed.measure(start, start, thresholds, analyzer.color_mode, xp)
        report.update(protection_before=full_before, thresholds=thresholds)
        fused_start = (headroom * start + xp.asarray(stars) * (1 - headroom * start)
                       if stars is not None else None)
        fused_before = (fixed.measure(fused_start, fused_start, thresholds, analyzer.color_mode,
                                     xp, mask_reference=start) if stars is not None else None)

        def transform(
            image: np.ndarray, params: dict[str, float], engine: ModuleType,
        ) -> np.ndarray:
            """Évalue les fortes courbes en float64 sans écrêtage du résultat."""
            result = ghs_rgb(image.astype(engine.float64), **params,
                             color_mode=analyzer.color_mode, xp=engine)
            return result.astype(image.dtype)

        def histogram(image: np.ndarray, engine: ModuleType) -> np.ndarray:
            """Ne transfère sur CPU que les 128 effectifs de luminosité active."""
            light = luminance(image, analyzer.color_mode, xp=engine)
            counts = engine.histogram(light[light > 1e-6], bins=HISTOGRAM_BINS, range=(0, 1))[0]
            return counts if engine is np else backend.numpy(counts)

        def tail_profile(image: np.ndarray, engine: ModuleType, channel: str) -> JSONReport:
            """Mesure le canal rendu (étoiles fixes incluses si fournies), sans transfert RGB."""
            index = TAIL_CHANNELS.index(channel)
            plane = image[index]
            if stars is not None:
                # La hauteur vise le rendu visible ; la longueur reste mesurée sur le fond.
                field = sample_stars if engine is np and image.shape == sample_start.shape else stars
                plane = headroom*plane + engine.asarray(field[index])*(1-headroom*plane)
            counts = engine.histogram(plane, bins=TAIL_HISTOGRAM_BINS, range=(0, 1))[0]
            counts = counts if engine is np else backend.numpy(counts)
            scale = data.shape[1]*data.shape[2]/(image.shape[1]*image.shape[2])
            return log_tail_height(counts, scale)

        def profile(image: np.ndarray, engine: ModuleType) -> JSONReport:
            """Combine longueur et hauteur avec les mêmes unités d'effectifs."""
            line = quartile_profile(histogram(image, engine), args.ghs_linearity_tolerance)
            tail = tail_profile(image, engine, args.ghs_tail_channel)
            return add_tail_height_objective(line, tail, args.ghs_tail_height, args.ghs_tail_channel)

        def improvement(before: JSONReport, after: JSONReport) -> float:
            """Compare longueur et progression de hauteur, sous les mêmes gardes."""
            if after['peak'] is None or after['peak'] >= .25:
                return -math.inf
            return after['score']-before['score']

        def guards(
            candidate: np.ndarray, original: np.ndarray, baseline: JSONReport,
            engine: ModuleType, star_field: np.ndarray | None,
            fusion_original: np.ndarray | None, fusion_baseline: JSONReport | None,
        ) -> tuple[list[str], float, JSONReport]:
            """Contrôle les budgets cumulés sur l'échantillon ou le champ entier."""
            validate_rgb(candidate)
            lost = engine.any((original > 0) & (candidate <= 0), axis=0)
            count = int(engine.count_nonzero(engine.any(original > 0, axis=0)).item())
            fraction = float(engine.count_nonzero(lost).item()) / max(1, count)
            rejected = []
            if fraction > args.ghs_blackpoint_loss + 1e-12:
                rejected.append('cumulative blackpoint loss')
            def check(first: JSONReport, last: JSONReport, prefix: str = '') -> None:
                """Protège les régions réellement présentes, sans inventer de mesures."""
                reasons = []
                for key in ('highlight_contrast', 'core_contrast', 'local_contrast'):
                    initial, final = first.get(key), last.get(key)
                    if initial is not None and initial > 1e-7 and (
                        final is None or final < args.ghs_highlight_retention * initial
                    ):
                        reasons.append(key + ' loss')
                for key in ('color_drift', 'core_color_drift'):
                    if last.get(key) is not None and last[key] > MAX_COLOR_DRIFT:
                        reasons.append(key)
                for key in ('chroma_change', 'core_chroma_change'):
                    if last.get(key) is not None and last[key] < -MAX_CHROMA_LOSS:
                        reasons.append(key)
                if (first.get('sky_median') is not None and last.get('sky_median') is not None
                        and last['sky_median'] > max(MAX_FINAL_BACKGROUND, first['sky_median'])):
                    reasons.append('background brightness')
                if first.get('noise') is not None and first['noise'] > 1e-8 and (
                    last.get('noise') is None or last['noise'] > MAX_FINAL_NOISE_GAIN * first['noise']
                ):
                    reasons.append('background noise amplification')
                if last['near_white_fraction'] > first['near_white_fraction'] + MAX_NEW_NEAR_WHITE:
                    reasons.append('new near-white pixels')
                rejected.extend(prefix + reason for reason in reasons)

            measured = fixed.measure(original, candidate, thresholds, analyzer.color_mode, engine)
            check(baseline, measured)
            protection = {'starless': measured}
            if star_field is not None:
                fused = headroom * candidate + engine.asarray(star_field) * (1 - headroom * candidate)
                measured = fixed.measure(fusion_original, fused, thresholds, analyzer.color_mode,
                                         engine, mask_reference=original)
                check(fusion_baseline, measured, 'composite: ')
                protection['composite'] = measured
            return rejected, fraction, protection

        for iteration in range(args.ghs_quartile_iterations):
            sample = backend.numpy(current[:, ::stride, ::stride])
            before = profile(sample, np)
            bp = isolated_blackpoint(sample, args.ghs_blackpoint_loss)
            minimum = float(xp.min(xp.where(current > 0, current, xp.inf)).item())
            minimum_bp = minimum * (1 - 1e-5) if math.isfinite(minimum) else 0.0
            candidates = []
            # L'identité GHS autorise un point noir utile sans étirement additionnel.
            strengths = np.unique(np.r_[0, np.linspace(
                args.ghs_quartile_max_strength / 40, args.ghs_quartile_max_strength, 12,
            )])
            for point in sorted(set([0.0, minimum_bp, bp])):
                shifted = linear_blackpoint(sample, point) if point > 0 else sample
                points = symmetry_points(histogram(shifted, np))
                for strength in strengths:
                    for b in (15.0, 5.0):
                        for sp in points:
                            for hp in (.75, 1.0):
                                params = dict(D=float(strength), B=b, SP=sp, LP=0.0, HP=hp)
                                transformed = transform(shifted, params, np)
                                after = profile(transformed, np)
                                gain = improvement(before, after)
                                if gain > MIN_IMPROVEMENT:
                                    candidates.append({'parameters': params, 'BP': point,
                                                       'gain': gain, 'profile': after})
            candidates.sort(key=lambda item: item['gain'], reverse=True)
            entry: JSONReport = {'iteration': iteration + 1, 'before': before,
                                 'validations': [], 'sample_rejections': [], 'accepted': None}
            full_profile = profile(current, xp)
            best = None
            for record in candidates:
                if len(entry['validations']) >= MAX_FULL_CANDIDATES:
                    break
                point = record['BP']
                shifted_sample = linear_blackpoint(sample, point) if point > 0 else sample
                candidate_sample = transform(shifted_sample, record['parameters'], np)
                rejected, _, _ = guards(candidate_sample, sample_start, sample_before, np,
                                      sample_stars, sample_fused, sample_fused_before)
                if rejected:
                    entry['sample_rejections'].append(dict(record, rejections=rejected))
                    continue
                shifted = linear_blackpoint(current, point, xp=xp) if point > 0 else current
                candidate = transform(shifted, record['parameters'], xp)
                after = profile(candidate, xp)
                gain = improvement(full_profile, after)
                rejected, loss, protection = guards(candidate, start, full_before, xp, stars,
                                         fused_start, fused_before)
                if gain <= MIN_IMPROVEMENT:
                    rejected.append('no full-resolution line/height improvement')
                audit = dict(record, full_profile=after, full_gain=gain if math.isfinite(gain) else None,
                             rejections=rejected, blackpoint_loss=loss, protection=protection)
                entry['validations'].append(audit)
                if not rejected and (best is None or gain > best[0]):
                    best = (gain, candidate, audit)
            report['iterations'].append(entry)
            if best is None:
                report['stop_reason'] = 'no admissible line/height improvement'
                break
            _, current, accepted = best
            entry['accepted'] = accepted
            report.update(status='applied', blackpoint_loss=accepted['blackpoint_loss'])
            LOGGER.info('AutoGHS droite log : passe %d D=%.3f B=%.1f SP=%.5f HP=%.2f BP=%.5f perte=%.6g longueur=%.5f hauteur=%s',
                        iteration + 1, accepted['parameters']['D'], accepted['parameters']['B'],
                        accepted['parameters']['SP'], accepted['parameters']['HP'],
                        accepted['BP'], accepted['blackpoint_loss'], accepted['full_profile']['line_span'],
                        accepted['full_profile']['tail']['height'])
        report['before'] = profile(start, xp)
        report['after'] = profile(current, xp)
        report['tail_channels_before'] = {channel: tail_profile(start, xp, channel) for channel in TAIL_CHANNELS}
        report['tail_channels_after'] = {channel: tail_profile(current, xp, channel) for channel in TAIL_CHANNELS}
        tail = report['after']['tail']
        report['tail_height_achieved'] = tail['achieved']
        if args.ghs_tail_height > 0 and not tail['achieved']:
            LOGGER.info('AutoGHS : hauteur log cible %.3f non atteinte pour %s (mesure %s) ; '
                        'arrêt : %s', args.ghs_tail_height,
                        args.ghs_tail_channel, tail['height'], report['stop_reason'])
        return current, report

    def post_process(
        self, input_path: Path | str, output_path: Path | str, args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Exécute ce moteur sur un FITS et publie son audit sans écraser l'entrée."""
        import json
        from lib.auto_ghs import ComputeBackend, GHSOptimizer, HistogramAnalyzer
        from lib.restoration_quality import normalized_planes
        from lib.star_separation import write_rgb

        if Path(input_path).resolve() == Path(output_path).with_suffix('.fits').resolve():
            raise ValueError('La sortie doit être différente de la source')
        options = GHSOptimizer.options(self._get_args(args))
        backend = ComputeBackend(getattr(options, 'compute_backend', 'auto'))
        result, report = self.search(backend.xp.asarray(normalized_planes(input_path)),
                                     HistogramAnalyzer(color_mode=options.ghs_color_mode),
                                     backend, options)
        destination = Path(output_path)
        report['output_image'] = str(destination.with_suffix('.fits'))
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_rgb(Path(input_path), destination.with_suffix('.fits'), backend.numpy(result),
                  nonlinear=True, role='STARLESS')
        destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
        return report
