"""Classe les rendus par contraste utile, bosses lissées et compression brillante.

Les mesures restent relatives à une entrée fixe. Aucun numéro d'essai ni nom
d'objet n'intervient. Voir docs/scripts/postProcess/ghs-histogram-shape.md.
"""

from __future__ import annotations

import argparse
import math

import numpy as np

from lib.parameterized_component import ParameterizedComponent
from lib.type_defs import JSONReport
from lib.ghs_weak_structure import WeakStructureQuality, WeakStructureEvaluator

DEFAULT_CNR_FLOOR = .9
DEFAULT_TAIL_BONUS = .1
DEFAULT_REFERENCE_WEIGHT = 1.
# Un doublement du contraste diffus suffit au bonus d'ouverture du rendu.
VISIBILITY_WEIGHT = .2
SHAPE_WEIGHT = .5
BRIGHT_WEIGHT = .5
# Une bosse nouvelle de 0,1 dex coûte 0,5 point ; la queue rare est exclue.
RISE_SCALE_DEX = .1
RISE_WEIGHT = .5
CNR_DEFICIT_WEIGHT = 2.
# Un GHS n'est pas un débruitage : écraser les variations du ciel au-delà
# d'un facteur quatre dégrade aussi les détails faibles de cette région.
MIN_SKY_NOISE_GAIN = .25
SKY_COMPRESSION_WEIGHT = .5
# Prominence relative au sommet du pic, après lissage local des effectifs.
INITIAL_PEAK_RISE_SCALE = .1
INITIAL_PEAK_WEIGHT = .5
HARD_SHAPE_REASONS = frozenset((
    'unmeasurable histogram shape', 'histogram peak displacement',
    'histogram shoulder lost', 'histogram shoulder advance',
    'histogram slope sign inversion',
))
# Tolérance d'aggravation du creux intermodal : 10 % du sommet local.
MAX_LOW_LIGHT_MODE_EXCESS = .1
LOW_LIGHT_MODE_REJECTION = 'new low-light histogram mode'
QUALITY_SELECTION_POLICY = 'weak_structure_background_cnr_shape_dark_modes'


class HistogramRenderQuality(ParameterizedComponent):
    """Composant de mesure sans transformation d'image ni position de pic imposée."""

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie les options du classement indépendant des moteurs GHS."""
        return 'ghs_render_quality'

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare le seuil de suffisance et le bonus borné des objectifs lumineux."""
        WeakStructureQuality.add_arguments(parser)
        parser.add_argument('--ghs-quality-cnr-floor', type=float, default=DEFAULT_CNR_FLOOR,
                            help='Début de pénalité contraste/bruit en piecewise '
                                 '(défaut 0,9 ; ]0,1])')
        parser.add_argument('--ghs-quality-reference-weight', type=float, default=DEFAULT_REFERENCE_WEIGHT,
                            help='Poids du niveau logarithmique à 0,8 (défaut 1 ; [0,10])')
        parser.add_argument('--ghs-quality-tail-bonus', type=float, default=DEFAULT_TAIL_BONUS,
                            help='Bonus maximal de la queue à 0,875 '
                                 '(défaut 0,1 ; [0,1])')

    @staticmethod
    def validate_options(args: argparse.Namespace) -> None:
        """Refuse les réglages non finis, booléens ou hors de leur domaine."""
        WeakStructureQuality.validate_sigma(getattr(args, 'ghs_weak_sigma', WeakStructureQuality.WEAK_SIGMA))
        reference_weight = getattr(args, 'ghs_quality_reference_weight', DEFAULT_REFERENCE_WEIGHT)
        if (isinstance(reference_weight, (bool, np.bool_)) or not math.isfinite(reference_weight)
                or not 0 <= reference_weight <= 10):
            raise ValueError('ghs-quality-reference-weight hors domaine')
        for name, default, lower_open in (
            ('ghs_quality_cnr_floor', DEFAULT_CNR_FLOOR, True),
            ('ghs_quality_tail_bonus', DEFAULT_TAIL_BONUS, False),
        ):
            value = getattr(args, name, default)
            if (isinstance(value, (bool, np.bool_)) or not math.isfinite(value)
                    or value > 1 or value < 0 or (lower_open and value == 0)):
                raise ValueError(name.replace('_', '-')+' hors domaine')

    @staticmethod
    def hard_shape_rejections(profile: JSONReport) -> list[str]:
        """Retourne les refus obligatoires de forme et de modes sombres.

        Les anciens audits sans garde de modes restent lisibles. Les pénalités
        continues de ratios et de contraste ne sont pas des refus structurels.
        """
        reasons = [reason for reason in profile.get('shape_preservation', {}).get('rejections', [])
                   if reason in HARD_SHAPE_REASONS or reason == LOW_LIGHT_MODE_REJECTION]
        reasons.extend(profile.get('low_light_mode_guard', {}).get('rejections', []))
        return list(dict.fromkeys(reasons))

    @staticmethod
    def low_light_mode_guard(reference: JSONReport, candidate: JSONReport) -> JSONReport:
        """Refuse une aggravation intermodale supérieure à 10 % du sommet.

        Parameters
        ----------
        reference, candidate : JSONReport
            Profils portant low_light_modes, produit sans spline par
            piecewise_histogram_profile. La référence reste fixe pendant la
            recherche ; les bosses natives sont admises à la tolérance additive.

        Returns
        -------
        JSONReport
            Audit indépendant avec fractions, seuil et motifs de rejet.
            Les anciens rapports sans mesure restent explicitement non certifiés.

        Raises
        ------
        ValueError
            Mesure déclarée exploitable mais non finie ou hors [0,1].
        """
        def measured(profile: JSONReport) -> float | None:
            """Lit une mesure exploitable sans assimiler son absence à zéro."""
            modes = profile.get('low_light_modes') or {}
            value = modes.get('prominence_fraction')
            if value is None or not modes.get('measurable'):
                return None
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError('Proéminence des modes sombres GHS invalide')
            return float(value)

        first, last = measured(reference), measured(candidate)
        excess = max(0., last-first) if first is not None and last is not None else None
        rejected = excess is not None and excess > MAX_LOW_LIGHT_MODE_EXCESS+1e-12
        return dict(
            measurable=excess is not None, reference_prominence_fraction=first,
            prominence_fraction=last, excess_prominence_fraction=excess,
            maximum_excess=MAX_LOW_LIGHT_MODE_EXCESS,
            achieved=not rejected if excess is not None else None,
            rejections=[LOW_LIGHT_MODE_REJECTION] if rejected else [],
        )

    @staticmethod
    def middle_rise(profile: JSONReport) -> float | None:
        """Mesure la remontée maximale depuis la sortie du pic à 80 % du support.

        Retourne des dex, indépendamment de N et de la hauteur globale. Une
        forme absente reste None ; les fluctuations de la queue sont ignorées.
        """
        shape = profile.get('shape')
        if not shape or not shape.get('log_density'):
            return None
        values = np.asarray(shape['log_density'], dtype=float)
        if values.ndim != 1 or not np.isfinite(values).all():
            raise ValueError('Enveloppe de qualité invalide')
        coordinates = np.linspace(0, 1, values.size)
        values = values[coordinates <= .8]
        if not values.size:
            return None
        return float(np.max(values-np.minimum.accumulate(values)))

    @staticmethod
    def sample_measure(
        reference_light: np.ndarray, image: np.ndarray, thresholds: JSONReport, color_mode: str,
        *, weak_sigma: float = WeakStructureQuality.WEAK_SIGMA,
        weak_evaluator: WeakStructureEvaluator | None = None,
    ) -> JSONReport:
        """Mesure vite les régions fixes d'une grille spatiale, jamais d'un sondage trié.

        Parameters
        ----------
        reference_light : numpy.ndarray
            Luminosité 2D de l'entrée, aux mêmes coordonnées que image.
        image : numpy.ndarray
            RGB spatial CPU, avant toute recomposition, normalisé sur [0,1].
        thresholds : JSONReport
            Seuils figés sky/diffuse_low/diffuse_high/bright de cette phase.
        color_mode : str
            Mode human ou even partagé avec le moteur GHS.
        weak_sigma : float
            Seuil faible connecté en unités de bruit, entre 1 et 3 inclus.
        weak_evaluator : WeakStructureEvaluator or None
            Instance conservant les masques de cette référence pendant la phase.

        Returns
        -------
        JSONReport
            Médianes du ciel/diffus, bruit MAD des différences voisines et
            contrastes brillants, dans les mêmes unités que measure du finaliseur.
            Moins de 32 pixels/paires donne None, pas un contraste ou bruit nul.
        """
        from lib.auto_ghs import luminance
        from lib.ghs_final_stretch import MIN_REGION_PIXELS

        light = luminance(image, color_mode)
        if reference_light.shape != light.shape or light.ndim != 2:
            raise ValueError('Grille spatiale de qualité incompatible')
        sky = (reference_light > 1e-6) & (reference_light <= thresholds['sky'])
        diffuse = ((reference_light >= thresholds['diffuse_low'])
                   & (reference_light <= thresholds['diffuse_high']))
        bright = reference_light >= thresholds['bright']
        result: JSONReport = {}
        for name, mask in (('sky', sky), ('diffuse', diffuse)):
            result[name+'_median'] = (float(np.median(light[mask]))
                                     if np.count_nonzero(mask) >= MIN_REGION_PIXELS else None)
        pairs = sky[:, 1:] & sky[:, :-1]
        result['noise'] = None
        if np.count_nonzero(pairs) >= MIN_REGION_PIXELS:
            differences = (light[:, 1:]-light[:, :-1])[pairs]
            result['noise'] = float(
                1.4826*np.median(abs(differences-np.median(differences)))/math.sqrt(2),
            )
        result['highlight_contrast'] = None
        if np.count_nonzero(bright) >= MIN_REGION_PIXELS:
            low, high = np.percentile(light[bright], [10, 90])
            result['highlight_contrast'] = float((high-low)/(high+low+1e-12))
        result['local_contrast'] = None
        pairs = bright[:, 1:] & bright[:, :-1]
        if np.count_nonzero(pairs) >= MIN_REGION_PIXELS:
            delta = (light[:, 1:]-light[:, :-1])[pairs]
            total = (light[:, 1:]+light[:, :-1])[pairs]
            result['local_contrast'] = float(np.median(abs(delta)/np.maximum(total, 1e-12)))
        from lib.ghs_weak_structure import WeakStructureQuality
        result['weak_structure'] = WeakStructureQuality.measure(
            reference_light, light, weak_sigma=weak_sigma, evaluator=weak_evaluator,
        )
        return result

    @classmethod
    def attach(
        cls, profile: JSONReport, before: JSONReport, after: JSONReport,
        reference_shape: JSONReport, *, bright_retention: float,
        cnr_floor: float = DEFAULT_CNR_FLOOR, tail_bonus: float = DEFAULT_TAIL_BONUS,
        composite_before: JSONReport | None = None, composite_after: JSONReport | None = None,
        estimated: bool = False, reference_weight: float = DEFAULT_REFERENCE_WEIGHT,
    ) -> JSONReport:
        """Ajoute un score plafonné et ses composantes, sans modifier les mesures.

        Parameters
        ----------
        profile, reference_shape : JSONReport
            Profil candidat après BP et référence de forme immuable.
        before, after : JSONReport
            Mesures du fond, sur les mêmes masques : sky_median, diffuse_median,
            noise, highlight_contrast et local_contrast. Une référence non
            mesurable ne permet pas de certifier la qualité.
        bright_retention : float
            Conservation brillante demandée, éventuellement adaptée à la population.
        cnr_floor, tail_bonus : float
            Début de pénalité du contraste/bruit et bonus maximal de la queue à 0,875.
        reference_weight : float
            Poids maximal du niveau à 0,8, de 0 à 10, 1 par défaut.
        composite_before, composite_after : JSONReport or None
            Mesures de fusion ; leur compression brillante est aussi pénalisée.
        estimated : bool
            True pour une estimation spatiale, False pour le champ entier.

        Returns
        -------
        JSONReport
            Copie enrichie de render_quality, y compris les contributions du
            score. Le bonus contraste/bruit sature à 100 % ; le bonus de
            visibilité sature à un doublement de la séparation diffus/ciel.
            Les seuils à 0,8 et 0,875 ne sont jamais des priorités absolues.
            Un creux nouveau avant le pic et l'écrasement des variations du
            ciel sont pénalisés ; une mesure de creux absente reste None.
            La remontée près du pic est mesurée sans la spline du corps et
            pénalisée relativement à celle de la même référence fixe. Une
            seconde bosse sombre significative relève en plus d’un rejet
            obligatoire, consigné dans low_light_mode_guard indépendamment du score.
        """
        cls.validate_options(argparse.Namespace(ghs_quality_cnr_floor=cnr_floor,
                                              ghs_quality_tail_bonus=tail_bonus,
                                              ghs_quality_reference_weight=reference_weight))
        if not math.isfinite(bright_retention) or not 0 < bright_retention <= 1:
            raise ValueError('Conservation brillante de qualité invalide')
        result = dict(profile)
        result['low_light_mode_guard'] = cls.low_light_mode_guard(reference_shape, profile)
        quality: JSONReport = dict(available=False, estimated=estimated, cnr_retention=None,
                                   visibility_gain=None, score=None,
                                   policy=QUALITY_SELECTION_POLICY)
        result['render_quality'] = quality
        required = ('sky_median', 'diffuse_median', 'noise')
        if any(item.get(key) is None for item in (before, after) for key in required):
            return result
        if any(not math.isfinite(item[key]) for item in (before, after) for key in required):
            raise ValueError('Mesure de contraste/bruit non finie')
        initial = before['diffuse_median']-before['sky_median']
        final = after['diffuse_median']-after['sky_median']
        if initial <= 1e-7 or before['noise'] <= 1e-8 or after['noise'] <= 1e-8:
            return result
        gain = final/initial
        retention = gain*before['noise']/after['noise']
        if not math.isfinite(retention):
            raise ValueError('Mesure de contraste/bruit non finie')
        cnr_reward = min(1., max(0., retention))
        cnr_penalty = CNR_DEFICIT_WEIGHT*max(0., (cnr_floor-retention)/cnr_floor)**2
        noise_gain = after['noise']/before['noise']
        sky_penalty = SKY_COMPRESSION_WEIGHT*max(0., 1-noise_gain/MIN_SKY_NOISE_GAIN)**2
        visibility_reward = VISIBILITY_WEIGHT*min(1., math.log2(max(1., gain)))
        audit = profile.get('shape_preservation', {})
        deviations = []
        for value_key, limit_key, lower in (
            ('drop_retention', 'minimum_drop_retention', True),
            ('local_slope_retention', 'minimum_local_retention', True),
            ('distance', 'maximum_shape_distance', False),
            ('shoulder_change', 'maximum_shape_distance', False),
            ('slope_deformation', 'max_slope_stiffness', False),
            ('slope_stiffness', 'max_slope_stiffness', False),
        ):
            value, limit = audit.get(value_key), audit.get(limit_key)
            if value is not None and limit is not None and limit > 0:
                scale = max(1., limit) if value_key == 'local_slope_retention' else limit
                deviations.append(max(0., (limit-value if lower else value-limit)/scale)**2)
        if audit.get('policy') == 'shoulder_and_slope_ratios':
            deviations = []
        rise, reference_rise = cls.middle_rise(profile), cls.middle_rise(reference_shape)
        excess_rise = (max(0., rise-reference_rise)
                       if rise is not None and reference_rise is not None else None)
        rise_penalty = (RISE_WEIGHT*(excess_rise/RISE_SCALE_DEX)**2
                        if excess_rise is not None else 0.)
        valley = profile.get('prepeak_valley_dex')
        reference_valley = reference_shape.get('prepeak_valley_dex')
        excess_valley = (max(0., valley-reference_valley)
                         if valley is not None and reference_valley is not None else None)
        valley_penalty = (RISE_WEIGHT*(excess_valley/RISE_SCALE_DEX)**2
                          if excess_valley is not None else 0.)
        peak_rise = (profile.get('initial_peak') or {}).get('secondary_rise_fraction')
        reference_peak_rise = (reference_shape.get('initial_peak') or {}).get(
            'secondary_rise_fraction',
        )
        excess_peak_rise = (max(0., peak_rise-reference_peak_rise)
                            if peak_rise is not None and reference_peak_rise is not None else None)
        peak_penalty = (INITIAL_PEAK_WEIGHT*(excess_peak_rise/INITIAL_PEAK_RISE_SCALE)**2
                        if excess_peak_rise is not None else 0.)
        compressions = []
        measured_retention: JSONReport = {}
        for name, first, last in (('starless', before, after),
                                  ('composite', composite_before, composite_after)):
            if first is None or last is None:
                continue
            for key in ('highlight_contrast', 'local_contrast'):
                old, new = first.get(key), last.get(key)
                if any(value is not None and not math.isfinite(value) for value in (old, new)):
                    raise ValueError('Mesure de contraste brillant non finie')
                value = new/old if old is not None and old > 1e-7 and new is not None else None
                measured_retention[name+'_'+key] = value
                if value is not None:
                    compressions.append(max(0., (bright_retention-value)/bright_retention)**2)
        bright_penalty = BRIGHT_WEIGHT*max(compressions, default=0.)
        floor = profile.get('regression_floor', {})
        target = floor.get('minimum_count', 0.)
        prediction = floor.get('predicted_log10_count')
        if prediction is not None and not math.isfinite(prediction):
            raise ValueError('Prédiction de niveau non finie')
        target_log = math.log10(target) if target > 0 else None
        level_progress = 0.
        if target_log is not None and prediction is not None and floor.get('reference_covered'):
            level_progress = (min(1., max(0., prediction/target_log)) if target_log > 0
                              else float(prediction >= target_log))
        tail = profile.get('tail', {})
        tail_target, height = tail.get('target', 0.), tail.get('height')
        tail_progress = (min(1., max(0., height)/tail_target)
                         if tail_target and height is not None else 0.)
        reference_reward = reference_weight*level_progress
        tail_reward = tail_bonus*tail_progress
        light_reward = reference_reward+tail_reward
        from lib.ghs_weak_structure import WeakStructureQuality
        weak = WeakStructureQuality.compare(before, after)
        shape_penalty = SHAPE_WEIGHT*(WeakStructureQuality.shape_penalty(audit, weak)
                                      + sum(deviations))
        score = (cnr_reward+visibility_reward+light_reward
                 - cnr_penalty-shape_penalty-rise_penalty-bright_penalty
                 - valley_penalty-sky_penalty-peak_penalty)
        score += weak['score_adjustment']
        quality['weak_structure'] = weak
        if not math.isfinite(score):
            raise ValueError('Score de qualité non fini')
        quality.update(available=True, cnr_retention=retention, cnr_floor=cnr_floor,
                       sufficient_cnr=retention >= cnr_floor, visibility_gain=gain,
                       cnr_reward=cnr_reward, cnr_penalty=cnr_penalty,
                       sky_noise_gain=noise_gain, sky_noise_gain_floor=MIN_SKY_NOISE_GAIN,
                       sky_compression_penalty=sky_penalty,
                       visibility_reward=visibility_reward, light_reward=light_reward,
                       reference_reward=reference_reward, reference_weight=reference_weight,
                       reference_progress=level_progress, reference_target_log10=target_log,
                       tail_reward=tail_reward,
                       shape_penalty=shape_penalty, middle_rise_dex=rise,
                       reference_middle_rise_dex=reference_rise, excess_middle_rise_dex=excess_rise,
                       rise_penalty=rise_penalty, prepeak_valley_dex=valley,
                       reference_prepeak_valley_dex=reference_valley,
                       excess_prepeak_valley_dex=excess_valley,
                       prepeak_valley_penalty=valley_penalty,
                       initial_peak_rise_fraction=peak_rise,
                       reference_initial_peak_rise_fraction=reference_peak_rise,
                       excess_initial_peak_rise_fraction=excess_peak_rise,
                       initial_peak_penalty=peak_penalty,
                       bright_retention=measured_retention,
                       bright_target=bright_retention, bright_penalty=bright_penalty, score=score)
        return result
