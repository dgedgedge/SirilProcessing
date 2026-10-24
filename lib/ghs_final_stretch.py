"""Recherche GHS finale bornée, avec protection des hautes lumières.

Les masques sont fixés avant les essais. Le classement porte sur des pixels RGB
réels échantillonnés ; le candidat est ensuite vérifié sur tous les pixels.
Les seuils sont des heuristiques de présentation, pas un modèle de perception.
Voir docs/scripts/postProcess/ghs-highlight-protection.md.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import numpy as np

from lib.ghs_log_histogram import (
    DEFAULT_LOG_RMS_WEIGHT, HISTOGRAM_BINS, HISTOGRAM_SLOPE_WEIGHT,
    RMS_REGULARIZER_DEX, TAIL_ENDPOINT,
    fit_log_tail_counts, log_tail_score,
)
from lib.processor import processor
from lib.star_separation import validate_rgb, write_rgb
from lib.type_defs import JSONReport

if TYPE_CHECKING:
    from lib.auto_ghs import ComputeBackend, HistogramAnalyzer

# Limites déterministes : coût CPU borné et au plus trois validations complètes.
MAX_SAMPLE_PIXELS = 65536
MAX_FULL_VALIDATIONS = 3
MIN_REGION_PIXELS = 8
MAX_FINAL_NOISE_GAIN = 3.0
MAX_FINAL_BACKGROUND = 0.25
MAX_COLOR_DRIFT = 0.01
MAX_CHROMA_LOSS = 0.02
NEAR_WHITE = 0.98
MAX_NEW_NEAR_WHITE = 0.001
MIN_SCORE = 0.01
# En dessous du milieu de l'échelle, P98 peut encore désigner du signal diffus.
# Le cœur P99,9 reste protégé même sur un champ n'atteignant pas ce niveau.
HIGHLIGHT_START = 0.5
DIFFUSE_VISIBILITY_TARGET = 0.5
DIFFUSE_VISIBILITY_WEIGHT = 0.35


class ProtectedGHSFinalizer(processor):
    """Classe le gain des tons moyens et la queue log sous gardes du fond/fusion."""

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie le moteur de présentation finale."""
        return 'ghs_final'

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare les réglages locaux et persistants de la dernière passe."""
        parser.add_argument(
            '--ghs-final-strength', type=float, default=1.25,
            help='D maximal recherché (auto) ou force fixe ; 0 désactive, maximum 10',
        )
        parser.add_argument(
            '--ghs-final-mode', choices=('auto', 'fixed'), default='auto',
            help='Recherche D/B/SP/HP protégée, ou courbe fixe pour comparaison',
        )
        parser.add_argument(
            '--ghs-highlight-retention', type=float, default=0.9,
            help='Fraction minimale du contraste relatif des hautes lumières (0 à 1)',
        )
        parser.add_argument(
            '--ghs-log-rms-weight', type=float, default=DEFAULT_LOG_RMS_WEIGHT,
            help='Poids du gain relatif de régularité log ; 0 désactive, maximum 10',
        )

    @staticmethod
    def thresholds(data: np.ndarray, color_mode: str) -> dict[str, float]:
        """Fixe les régions positives et distingue tons moyens et hautes lumières.

        Les zéros de bordure sont exclus de cette estimation. Les quantiles
        décrivent l'image, sans identifier une galaxie ni une nébuleuse. Le
        diffus va de P75 à P95 ; le brillant commence à max(P98,min(0,5,P99,9)).
        Ce seuil conserve la protection des petits cœurs sans bloquer des tons
        moyens simplement parce qu'ils appartiennent aux 2 % les plus élevés.
        """
        from lib.auto_ghs import luminance

        light = luminance(data, color_mode)
        active = light[light > 1e-6]
        if active.size < MIN_REGION_PIXELS:
            return {}
        values = np.percentile(active, [25, 50, 75, 90, 95, 98, 99, 99.9])
        thresholds = dict(zip(
            ('sky', 'median', 'diffuse_low', 'q90', 'diffuse_high', 'q98', 'q99', 'core'),
            map(float, values),
        ))
        thresholds['bright'] = max(thresholds['q98'], min(HIGHLIGHT_START, thresholds['core']))
        return thresholds

    @staticmethod
    def measure(
        original: np.ndarray, candidate: np.ndarray, thresholds: dict[str, float],
        color_mode: str, xp: ModuleType = np,
        mask_reference: np.ndarray | None = None,
        tail_endpoint: float | None = None,
    ) -> JSONReport:
        """Mesure les mêmes régions Y avant/après, sur le moteur demandé.

        Le contraste brillant est (P90-P10)/(P90+P10), complété par le contraste
        relatif médian des voisins horizontaux dans ce masque. Le bruit est un
        proxy MAD des différences voisines du ciel ; une mesure absente vaut None.
        Les couleurs sont des proportions RGB, sans primaires FITS supposées.
        mask_reference fixe les masques par le fond lors de la comparaison de
        deux fusions ; original reste la référence des proportions de couleur.
        La visibilité diffuse est sa séparation du ciel divisée par celle du
        brillant. La queue d'histogramme utilise le champ actif, aux mêmes classes
        de [0,1] sur CPU et CUDA ; seuls les 128 effectifs sont transférés sur CPU.
        tail_endpoint fixe la borne haute de la régression pour garder la même
        borne entre la référence et les candidats, échantillon et champ entier.
        """
        from lib.auto_ghs import luminance

        reference = luminance(
            original if mask_reference is None else mask_reference, color_mode, xp=xp,
        )
        light = luminance(candidate, color_mode, xp=xp)
        sky = (reference > 1e-6) & (reference <= thresholds['sky'])
        diffuse = ((reference >= thresholds['diffuse_low'])
                   & (reference <= thresholds['diffuse_high']))
        bright = reference >= thresholds['bright']
        core = reference >= thresholds['core']
        result: JSONReport = {}
        for name, mask in (('sky', sky), ('diffuse', diffuse), ('bright', bright), ('core', core)):
            count = int(xp.count_nonzero(mask).item())
            result[name + '_count'] = count
            if count < MIN_REGION_PIXELS:
                result[name + '_median'] = None
                if name in ('bright', 'core'):
                    result['highlight_contrast' if name == 'bright' else 'core_contrast'] = None
                continue
            values = light[mask]
            result[name + '_median'] = float(xp.median(values).item())
            if name in ('bright', 'core'):
                low, high = xp.percentile(values, [10, 90])
                key = 'highlight_contrast' if name == 'bright' else 'core_contrast'
                result[key] = float(((high - low) / (high + low + 1e-12)).item())
        sky_pairs = sky[:, 1:] & sky[:, :-1]
        bright_pairs = bright[:, 1:] & bright[:, :-1]
        for name, pairs in (('noise', sky_pairs), ('local_contrast', bright_pairs)):
            count = int(xp.count_nonzero(pairs).item())
            value = None
            if count >= MIN_REGION_PIXELS:
                difference = (light[:, 1:] - light[:, :-1])[pairs]
                if name == 'noise':
                    value = float((1.4826 * xp.median(abs(difference - xp.median(difference)))
                                   / math.sqrt(2)).item())
                else:
                    total = (light[:, 1:] + light[:, :-1])[pairs]
                    value = float(xp.median(abs(difference) / xp.maximum(total, 1e-12)).item())
            result[name] = value
        for prefix, mask, region in (('', bright, 'bright'), ('core_', core, 'core')):
            if result.get(region + '_median') is None:
                result[prefix + 'color_drift'], result[prefix + 'chroma_change'] = None, None
                continue
            first, last = original[:, mask], candidate[:, mask]
            first = first / xp.maximum(first.sum(axis=0), 1e-12)
            last = last / xp.maximum(last.sum(axis=0), 1e-12)
            result[prefix + 'color_drift'] = float(xp.mean(abs(last - first)).item())
            result[prefix + 'chroma_change'] = float(xp.mean(
                xp.ptp(last, axis=0) - xp.ptp(first, axis=0),
            ).item())
        result['near_white_fraction'] = float(xp.mean(candidate >= NEAR_WHITE).item())
        if all(result.get(key) is not None for key in
               ('sky_median', 'diffuse_median', 'bright_median')):
            separation = result['diffuse_median'] - result['sky_median']
            span = result['bright_median'] - result['sky_median']
            result['diffuse_visibility'] = separation / span if span > 1e-7 else None
        else:
            result['diffuse_visibility'] = None
        counts = xp.histogram(light[reference > 1e-6], bins=HISTOGRAM_BINS, range=(0, 1))[0]
        result['log_histogram'] = fit_log_tail_counts(
            counts if xp is np else xp.asnumpy(counts),
            window_end=tail_endpoint,
        )
        return result

    @staticmethod
    def assess(
        before: JSONReport, after: JSONReport, retention: float,
        rms_weight: float = DEFAULT_LOG_RMS_WEIGHT,
    ) -> tuple[float, list[str]]:
        """Évalue le gain diffus et rejette bruit, perte de contraste et dérive RGB.

        Le score combine le gain de contraste diffus, sa progression vers le
        milieu de l'écart ciel/brillant, le coût du bruit et un bonus borné de
        régression logarithmique. Ce dernier ne peut pas rendre admissible une
        courbe sans gain diffus ou violant une garde. Voir la page spécialisée
        pour les poids et les limites de ces heuristiques de présentation.
        Une métrique brillante principale non mesurable empêche de certifier
        un candidat ; le contrôle du petit cœur s'ajoute lorsqu'il est mesurable.
        Les limites de luminosité ne tentent pas de réparer un défaut déjà présent.
        """
        rejected = []
        for key in ('sky_median', 'diffuse_median', 'bright_median', 'highlight_contrast'):
            if before.get(key) is None or after.get(key) is None:
                return 0.0, ['insufficient fixed-region measurements']
        for key in ('highlight_contrast', 'core_contrast', 'local_contrast'):
            first, last = before.get(key), after.get(key)
            if first is not None and first > 1e-7 and (last is None or last < retention * first):
                rejected.append(key + ' loss')
        if before['highlight_contrast'] <= 1e-7:
            rejected.append('unmeasurable highlight contrast')
        if after['sky_median'] > max(MAX_FINAL_BACKGROUND, before['sky_median']):
            rejected.append('background brightness')
        if (after.get('core_median') is not None and before.get('core_median') is not None
                and after['core_median'] > max(0.98, before['core_median'])):
            rejected.append('core brightness')
        if after['bright_median'] > max(0.95, before['bright_median']):
            rejected.append('highlight brightness')
        if after['near_white_fraction'] > before['near_white_fraction'] + MAX_NEW_NEAR_WHITE:
            rejected.append('new near-white pixels')
        if after['color_drift'] is None or after['color_drift'] > MAX_COLOR_DRIFT:
            rejected.append('highlight color drift')
        if after['chroma_change'] is None or after['chroma_change'] < -MAX_CHROMA_LOSS:
            rejected.append('highlight chroma loss')
        if (after.get('core_color_drift') is not None
                and after['core_color_drift'] > MAX_COLOR_DRIFT):
            rejected.append('core color drift')
        if (after.get('core_chroma_change') is not None
                and after['core_chroma_change'] < -MAX_CHROMA_LOSS):
            rejected.append('core chroma loss')
        noise_gain = 1.0
        if before.get('noise') is not None and before['noise'] > 1e-8:
            if after.get('noise') is None:
                rejected.append('unmeasurable background noise')
            else:
                noise_gain = after['noise'] / before['noise']
                if noise_gain > MAX_FINAL_NOISE_GAIN:
                    rejected.append('background noise amplification')
        contrast = before['diffuse_median'] - before['sky_median']
        gain = (after['diffuse_median'] - after['sky_median']) / max(contrast, 1e-12)
        diffuse_score = math.log(max(gain, 1e-12)) - 0.2 * math.log(max(1, noise_gain))
        visibility_score = 0.0
        if (before.get('diffuse_visibility') is not None
                and after.get('diffuse_visibility') is not None):
            first = min(DIFFUSE_VISIBILITY_TARGET, max(before['diffuse_visibility'], 1e-12))
            last = min(DIFFUSE_VISIBILITY_TARGET, max(after['diffuse_visibility'], 1e-12))
            visibility_score = DIFFUSE_VISIBILITY_WEIGHT * math.log(last / first)
            if last < first - 1e-7:
                rejected.append('diffuse visibility regression')
        histogram_score = log_tail_score(
            before['log_histogram'], after['log_histogram'], rms_weight,
        )
        score = diffuse_score + visibility_score + histogram_score
        if contrast <= 1e-7 or diffuse_score <= MIN_SCORE:
            rejected.append('no useful diffuse gain')
        elif score <= MIN_SCORE:
            rejected.append('no useful presentation gain')
        return score, rejected

    def search(
        self, data: np.ndarray, analyzer: HistogramAnalyzer, backend: ComputeBackend,
        strength: float, mode: str = 'auto', retention: float = 0.9,
        stars: np.ndarray | None = None, headroom: float = 0.95,
        rms_weight: float = DEFAULT_LOG_RMS_WEIGHT,
    ) -> tuple[np.ndarray, JSONReport]:
        """Compare D/B/SP/HP, affine puis valide les meilleurs sur tout le champ.

        Parameters
        ----------
        data : numpy.ndarray
            Trois plans RGB rendus dans [0,1], sur le backend choisi.
        analyzer : HistogramAnalyzer
            Mesures d'histogrammes et modèle de luminosité commun.
        backend : ComputeBackend
            Moteur déjà sélectionné, conservé jusqu'à la fin.
        strength : float
            D maximal dans [0,10] ; zéro désactive.
        mode : str
            auto pour chercher D/B/SP/HP, fixed pour l'ancienne courbe à D imposé.
        retention : float
            Fraction minimale du contraste brillant à conserver, dans ]0,1].
        stars : numpy.ndarray, optional
            RGB stellaire rendu, de même géométrie, inchangé par les essais.
        headroom : float
            Facteur du fond dans la fusion screen, dans ]0,1[.
        rms_weight : float
            Poids de la réduction relative du RMS logarithmique, dans [0,10].
            Deux par défaut ; zéro désactive ce terme sans modifier les gardes.

        Returns
        -------
        tuple[numpy.ndarray, JSONReport]
            Fond retenu sur le backend et audit des essais/validations. Le fond
            original est retourné si aucun candidat n'améliore le compromis.

        Raises
        ------
        ValueError
            Paramètre, domaine RGB ou géométrie invalide.
        RuntimeError
            Une erreur du backend est propagée sans reprise sur un autre moteur.

        Notes
        -----
        Les essais partent tous de data. Une grille RGB régulière de 65 536 pixels
        au plus sert au classement CPU. Trois candidats au plus sont validés sur
        tous les pixels du backend ; le meilleur score complet admissible est
        retenu, sinon l'identité est conservée. Si stars est
        fourni, les mêmes contrôles sont faits après fusion screen, étoiles fixes.
        Le mode fixed conserve explicitement la courbe historique sans ces gardes.
        """
        from lib.auto_ghs import ghs_rgb

        if (not math.isfinite(strength) or not 0 <= strength <= 10
                or mode not in ('auto', 'fixed') or not math.isfinite(retention)
                or not 0 < retention <= 1):
            raise ValueError('Paramètres du GHS final invalides')
        if not math.isfinite(rms_weight) or not 0 <= rms_weight <= 10:
            raise ValueError('ghs-log-rms-weight doit être dans [0,10]')
        validate_rgb(data)
        if stars is not None:
            validate_rgb(stars)
            if stars.shape != data.shape:
                raise ValueError('Étoiles et fond de géométries différentes')
        if not math.isfinite(headroom) or not 0 < headroom < 1:
            raise ValueError('ghs-headroom doit être dans ]0,1[')
        before = analyzer.analyze(data, backend).metrics
        identity = {'D': 0.0, 'B': 0.0, 'SP': 0.0, 'LP': 0.0, 'HP': 1.0}
        report: JSONReport = {
            'status': 'no admissible improvement', 'mode': mode, 'parameters': identity,
            'strength_limit': strength, 'highlight_retention': retention,
            'before': before, 'after': before, 'color_mode': analyzer.color_mode,
            'clip_mode': 'rgbblend',
            'objective': {
                'name': 'diffuse visibility and robust log-tail regression',
                'diffuse_visibility_target': DIFFUSE_VISIBILITY_TARGET,
                'diffuse_visibility_weight': DIFFUSE_VISIBILITY_WEIGHT,
                'highlight_start': HIGHLIGHT_START,
                'histogram_slope_weight': HISTOGRAM_SLOPE_WEIGHT,
                'histogram_rms_weight': rms_weight,
                'histogram_rms_regularizer_dex': RMS_REGULARIZER_DEX,
                'histogram_bins': HISTOGRAM_BINS,
                'histogram_window_end': TAIL_ENDPOINT,
            },
        }
        if strength == 0 or before['range_width'] <= 1e-7:
            report['status'] = 'disabled' if strength == 0 else 'constant distribution'
            return data, report
        if mode == 'fixed':
            parameters = dict(identity, D=strength)
            output = ghs_rgb(data, **parameters, color_mode=analyzer.color_mode, xp=backend.xp)
            validate_rgb(output)
            report.update(status='applied', parameters=parameters,
                          after=analyzer.analyze(output, backend).metrics)
            return output, report
        stride = max(1, math.ceil(math.sqrt(data.shape[1] * data.shape[2] / MAX_SAMPLE_PIXELS)))
        while (math.ceil(data.shape[1] / stride) * math.ceil(data.shape[2] / stride)
               > MAX_SAMPLE_PIXELS):
            stride += 1
        sample = backend.numpy(data[:, ::stride, ::stride])
        sample_stars = stars[:, ::stride, ::stride] if stars is not None else None
        thresholds = self.thresholds(sample, analyzer.color_mode)
        report.update(sample_stride=stride, thresholds=thresholds, candidates=[],
                      full_validation=[])
        if not thresholds:
            report['reason'] = 'insufficient positive support'
            return data, report
        baseline = self.measure(sample, sample, thresholds, analyzer.color_mode)
        tail_endpoint = baseline['log_histogram']['window_end']
        report['sample_before'] = baseline
        fusion_before = None
        fusion_tail_endpoint = None
        if sample_stars is not None:
            composed = headroom * sample + sample_stars * (1 - headroom * sample)
            fusion_reference = composed
            fusion_before = self.measure(
                fusion_reference, composed, thresholds, analyzer.color_mode,
                mask_reference=sample,
            )
            fusion_tail_endpoint = fusion_before['log_histogram']['window_end']
        report['histogram_windows'] = {
            'starless': tail_endpoint, 'composite': fusion_tail_endpoint,
        }
        tested: dict[tuple[float, float, float, float], JSONReport] = {}
        hp_values = sorted(set([thresholds[key] for key in
                               ('q90', 'diffuse_high', 'q98', 'q99')] + [1.0]))
        peak = baseline['log_histogram']['peak'] or 0.0
        sp_values = sorted(set([
            0.0, min(peak, thresholds['q90']), thresholds['median'],
            thresholds['diffuse_low'], thresholds['q90'],
            (thresholds['diffuse_low'] + thresholds['q90']) / 2,
        ]))

        def evaluate(d: float, b: float, sp: float, hp: float) -> None:
            """Évalue une courbe sur les pixels RGB échantillonnés, sans cumul."""
            key = (round(d, 8), round(b, 8), round(sp, 8), round(hp, 8))
            if key in tested or sp > hp:
                return
            parameters = {'D': d, 'B': b, 'SP': sp, 'LP': 0.0, 'HP': hp}
            result = ghs_rgb(sample, **parameters, color_mode=analyzer.color_mode)
            metrics = self.measure(sample, result, thresholds, analyzer.color_mode,
                                   tail_endpoint=tail_endpoint)
            score, rejected = self.assess(
                baseline, metrics, 1 - 0.8 * (1 - retention), rms_weight,
            )
            record: JSONReport = {'parameters': parameters, 'score': score,
                                  'rejections': rejected, 'starless': metrics}
            if sample_stars is not None:
                composite = headroom * result + sample_stars * (1 - headroom * result)
                metrics = self.measure(
                    fusion_reference, composite, thresholds, analyzer.color_mode,
                    mask_reference=sample,
                    tail_endpoint=fusion_tail_endpoint,
                )
                fusion_score, fusion_rejected = self.assess(
                    fusion_before, metrics, 1 - 0.8 * (1 - retention), rms_weight,
                )
                record.update(composite=metrics, score=min(score, fusion_score),
                              rejections=rejected + [
                                  'composite: ' + reason for reason in fusion_rejected])
            tested[key] = record

        for d, b, sp, hp in itertools.product(
            [strength * part for part in (0.15, 0.3, 0.5, 0.75, 1)],
            (0.0, 2.0, 5.0, 10.0, 15.0), sp_values, hp_values,
        ):
            evaluate(d, b, sp, hp)
        eligible = [item for item in tested.values() if not item['rejections']]
        if eligible:
            best = max(eligible, key=lambda item: item['score'])['parameters']
            index = hp_values.index(best['HP'])
            low, high = hp_values[max(0, index - 1)], hp_values[min(len(hp_values) - 1, index + 1)]
            sp_index = sp_values.index(best['SP'])
            sp_low = sp_values[max(0, sp_index - 1)]
            sp_high = sp_values[min(len(sp_values) - 1, sp_index + 1)]
            for d, b, sp, hp in itertools.product(
                [max(strength * 0.025, best['D'] - strength / 8), best['D'],
                 min(strength, best['D'] + strength / 8)],
                [max(0, best['B'] - 2.5), best['B'], min(15, best['B'] + 2.5)],
                [(sp_low + best['SP']) / 2, best['SP'], (sp_high + best['SP']) / 2],
                [(low + best['HP']) / 2, best['HP'], (high + best['HP']) / 2],
            ):
                evaluate(d, b, sp, hp)
        report['candidates'] = list(tested.values())
        ranked = sorted((item for item in tested.values() if not item['rejections']),
                        key=lambda item: item['score'], reverse=True)
        if not ranked:
            return data, report
        # Réserver une place à la meilleure régularité pour ne pas limiter la
        # comparaison complète à trois courbes presque identiques en gain diffus.
        def rms_quality(record: JSONReport) -> float:
            """Mesure le pire gain relatif RMS fond/fusion sur l'échantillon."""
            pairs = [(baseline, record['starless'])]
            if fusion_before is not None:
                pairs.append((fusion_before, record['composite']))
            gains = []
            for first, last in pairs:
                initial = first['log_histogram']['rms_dex']
                final = last['log_histogram']['rms_dex']
                if initial is None or final is None:
                    return -math.inf
                gains.append(math.log((initial + RMS_REGULARIZER_DEX)
                                      / (final + RMS_REGULARIZER_DEX)))
            return min(gains)

        finalists = ranked[:MAX_FULL_VALIDATIONS]
        if rms_weight > 0:
            smoothest = max(ranked, key=rms_quality)
            if smoothest not in finalists:
                finalists[-1] = smoothest
        xp = backend.xp
        full_before = self.measure(data, data, thresholds, analyzer.color_mode, xp,
                                   tail_endpoint=tail_endpoint)
        full_stars = xp.asarray(stars) if stars is not None else None
        composite_before = None
        if full_stars is not None:
            composite = headroom * data + full_stars * (1 - headroom * data)
            composite_reference = composite
            composite_before = self.measure(
                composite_reference, composite, thresholds, analyzer.color_mode, xp,
                mask_reference=data,
                tail_endpoint=fusion_tail_endpoint,
            )
        best_output = None
        best_score = MIN_SCORE
        for record in finalists:
            parameters = record['parameters']
            output = ghs_rgb(data, **parameters, color_mode=analyzer.color_mode, xp=xp)
            validate_rgb(output)
            measured = self.measure(data, output, thresholds, analyzer.color_mode, xp,
                                    tail_endpoint=tail_endpoint)
            score, rejected = self.assess(full_before, measured, retention, rms_weight)
            audit: JSONReport = {'parameters': parameters, 'before': full_before,
                                 'after': measured, 'score': score, 'rejections': rejected}
            if full_stars is not None:
                composed = headroom * output + full_stars * (1 - headroom * output)
                fusion = self.measure(
                    composite_reference, composed, thresholds, analyzer.color_mode, xp,
                    mask_reference=data,
                    tail_endpoint=fusion_tail_endpoint,
                )
                fusion_score, reasons = self.assess(
                    composite_before, fusion, retention, rms_weight,
                )
                score = min(score, fusion_score)
                audit['score'] = score
                rejected.extend('composite: ' + reason for reason in reasons)
                audit.update(composite_before=composite_before, composite_after=fusion)
            report['full_validation'].append(audit)
            if not rejected and score > best_score:
                best_output, best_score = output, score
                report.update(status='applied', parameters=parameters,
                              selected_validation=len(report['full_validation']) - 1)
            del output
        if best_output is not None:
            report['after'] = analyzer.analyze(best_output, backend).metrics
            return best_output, report
        return data, report

    def post_process(
        self, input_path: Path, output_path: Path, args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Expose le moteur seul sur un fond RGB rendu, sans fusion d'étoiles.

        Le FITS source reste intact ; output_path est l'audit JSON, accompagné
        d'un FITS de même stem. Les erreurs de lecture, calcul et écriture remontent.
        """
        from lib.auto_ghs import ComputeBackend, GHSOptimizer, HistogramAnalyzer
        from lib.restoration_quality import normalized_planes

        runtime = GHSOptimizer.options(self._get_args(args))
        source, report_path = Path(input_path).resolve(), Path(output_path).resolve()
        output = report_path.with_suffix('.fits')
        if source in (report_path, output):
            raise ValueError('La sortie GHS finale ne peut pas remplacer la source')
        data = normalized_planes(source)
        backend = ComputeBackend(runtime.compute_backend, data.nbytes * 16)
        result, report = self.search(
            backend.xp.asarray(data), HistogramAnalyzer(runtime.ghs_bins,
                runtime.ghs_q_low, runtime.ghs_q_high, runtime.ghs_color_mode),
            backend, runtime.ghs_final_strength, runtime.ghs_final_mode,
            runtime.ghs_highlight_retention,
            rms_weight=runtime.ghs_log_rms_weight,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        write_rgb(source, output, backend.numpy(result), nonlinear=True, role='STARLESS')
        report['output_image'] = str(output)
        report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        return report
