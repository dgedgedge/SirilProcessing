"""Mesure fixe des structures faibles et du ciel pour le classement GHS.

Les masques ne servent qu'aux mesures. Aucune correction spatiale des pixels
n'est effectuée. Voir docs/scripts/postProcess/ghs-weak-structures.md.
"""
from __future__ import annotations

import math
import hashlib
from types import ModuleType
import numpy as np
from scipy.ndimage import gaussian_filter, label, binary_dilation

from lib.type_defs import JSONReport


class WeakStructureQuality:
    """Évalue le contraste des parties faibles d'objets étendus détectés.

    Le modèle est déterministe sur une grille spatiale de référence bornée.
    Une détection non fiable reste indisponible et conserve la politique
    antérieure. Les masques sont toujours construits sur l'entrée de phase,
    pas sur le candidat étiré ; la même grille sert aux deux images.
    """

    MAX_SIDE = 192
    MIN_PIXELS = 32
    MIN_COMPONENT_PIXELS = 16
    BACKGROUND_SCALE = 12.
    SIGNAL_SCALES = (1., 2., 4.)
    SIGNAL_SIGMA = 3.
    SKY_SIGMA = 2.
    REGION_GRID = 4
    MIN_SKY_REGIONS = 4
    FAINT_QUANTILES = (.25, .5)
    SKY_REFERENCE_QUANTILE = .75
    MIN_BACKGROUND_IMPROVEMENT = .01
    CNR_RETENTION = .98
    CONTRAST_RETENTION = .98
    SCORE_WEIGHT = .5
    VISIBILITY_CONTRAST_TARGET = .1
    VISIBLE_FAINT_LEVEL = .08
    SHAPE_PENALTY_CAP = .25
    UNNECESSARY_SKY_WEIGHT = 2.
    COMPARABLE_QUALITY_TOLERANCE = .02

    @staticmethod
    def _noise(light: np.ndarray, sky: np.ndarray) -> float | None:
        """Estime la dispersion robuste des différences de voisins tous deux ciel."""
        pairs = sky[:, 1:] & sky[:, :-1]
        if np.count_nonzero(pairs) < WeakStructureQuality.MIN_PIXELS:
            return None
        differences = np.diff(light, axis=1)[pairs]
        value = 1.4826*np.median(abs(differences-np.median(differences)))/np.sqrt(2)
        return float(value) if value > 1e-10 else None

    @classmethod
    def measure(cls, reference: np.ndarray, candidate: np.ndarray, xp: ModuleType = np) -> JSONReport:
        """Mesure luminosités, contraste relatif, CNR et ciel régional sur une grille.

        Les images sont des luminosités normalisées 2D, dans la même géométrie.
        Les objets proviennent d'un excès multi-échelle positif par rapport à
        une tendance lente. Les composantes de moins de 16 cellules sont exclues.
        Le quartile 25–50 % à l'intérieur des objets définit leurs parties faibles.
        La tendance et le bruit sont des outils de détection, jamais des corrections.
        """
        if reference.ndim != 2 or reference.shape != candidate.shape:
            raise ValueError('Géométrie des structures faibles incompatible')
        stride = max(1, math.ceil(max(reference.shape)/cls.MAX_SIDE))
        def cpu(array: np.ndarray) -> np.ndarray:
            """Transfère uniquement la petite grille spatiale vers les mesures CPU."""
            grid = array[::stride, ::stride]
            return np.asarray(grid if xp is np else xp.asnumpy(grid), dtype=np.float64)
        first, last = cpu(reference), cpu(candidate)
        if (not np.isfinite(first).all() or not np.isfinite(last).all()
                or min(first.min(), last.min()) < 0 or max(first.max(), last.max()) > 1+1e-6):
            raise ValueError('Luminosités GHS invalides')
        unavailable = dict(available=False, reason='INSUFFICIENT_FIXED_REGIONS',
                           grid_shape=list(first.shape), stride=stride, estimated=True)
        if min(first.shape) < 2*cls.BACKGROUND_SCALE:
            return unavailable
        active = first > 1e-6
        if np.count_nonzero(active) < 4*cls.MIN_PIXELS:
            return unavailable
        difference = np.diff(first, axis=1)
        sigma = float(1.4826*np.median(abs(difference-np.median(difference)))/np.sqrt(2))
        if sigma <= 1e-10:
            return dict(unavailable, reason='UNMEASURABLE_REFERENCE_NOISE')
        trend = gaussian_filter(first, cls.BACKGROUND_SCALE, mode='reflect')
        objects = np.zeros(first.shape, dtype=bool)
        smooth = gaussian_filter(first, 1., mode='reflect')
        for scale in cls.SIGNAL_SCALES:
            signal = gaussian_filter(first, scale, mode='reflect')-trend
            objects |= signal > max(cls.SIGNAL_SIGMA*sigma/(np.sqrt(4*np.pi)*scale), 1e-7)
        components, _ = label(objects)
        sizes = np.bincount(components.ravel())
        accepted = sizes >= cls.MIN_COMPONENT_PIXELS
        accepted[0] = False
        objects = accepted[components] & active
        sky = (active & ~binary_dilation(objects, iterations=2)
               & (abs(smooth-trend) <= max(cls.SKY_SIGMA*sigma/np.sqrt(4*np.pi), 1e-7))
               & (smooth <= np.quantile(smooth[active], cls.SKY_REFERENCE_QUANTILE)))
        if min(np.count_nonzero(objects), np.count_nonzero(sky)) < 2*cls.MIN_PIXELS:
            return unavailable
        low, high = np.quantile(smooth[objects], cls.FAINT_QUANTILES)
        faint = objects & (smooth >= low) & (smooth <= high)
        if np.count_nonzero(faint) < cls.MIN_PIXELS:
            return unavailable
        result = dict(available=True, method='fixed multiscale positive structures; object P25–P50',
                      grid_shape=list(first.shape), stride=stride, estimated=True,
                      classification='heuristic_not_proof_of_empty_sky',
                      reference_sha256=hashlib.sha256(first.tobytes()).hexdigest(),
                      object_count=int(objects.sum()), faint_count=int(faint.sum()),
                      sky_count=int(sky.sum()), faint_reference_range=[float(low), float(high)])
        for name, plane in [('reference', first), ('candidate', last)]:
            background = float(np.median(plane[sky]))
            object_level = float(np.median(plane[faint]))
            separation = object_level-background
            noise = cls._noise(plane, sky)
            tile_levels = []
            for ys in np.array_split(np.arange(first.shape[0]), cls.REGION_GRID):
                for xs in np.array_split(np.arange(first.shape[1]), cls.REGION_GRID):
                    local_sky = sky[np.ix_(ys, xs)]
                    if local_sky.sum() >= cls.MIN_PIXELS:
                        tile_levels.append(float(np.median(plane[np.ix_(ys, xs)][local_sky])))
            result[name] = dict(sky_median=background, faint_median=object_level,
                                contrast=(separation/(object_level+background)
                                          if object_level+background > 1e-10 else None),
                                cnr=separation/noise if noise is not None else None,
                                noise=noise, sky_upper_median=float(np.quantile(tile_levels, .9))
                                if len(tile_levels) >= cls.MIN_SKY_REGIONS else None,
                                sky_region_count=len(tile_levels))
        return result

    @classmethod
    def compare(cls, before: JSONReport, after: JSONReport) -> JSONReport:
        """Préfère un ciel sobre si contraste et CNR des structures sont conservés.

        Les mesures portent sur la même référence ; une métadonnée différente
        ou une mesure absente n'offre aucun bonus. Les gains du contraste et du
        CNR saturent à 1 : éclaircir davantage n'ajoute pas de récompense.
        La perte d'une des deux mesures est pénalisée, jamais compensée par le ciel.
        """
        first_model, last_model = before.get('weak_structure', {}), after.get('weak_structure', {})
        if (not first_model.get('available') or not last_model.get('available')
                or any(first_model.get(k) != last_model.get(k) for k in
                       ('reference_sha256', 'grid_shape', 'object_count', 'faint_count',
                        'sky_count', 'faint_reference_range'))):
            return dict(available=False, score_adjustment=0., sufficient=False)
        first, last = first_model['candidate'], last_model['candidate']
        keys = ('contrast', 'cnr', 'sky_upper_median', 'faint_median')
        if any(first.get(k) is None or last.get(k) is None for k in keys):
            return dict(available=False, score_adjustment=0., sufficient=False)
        if any(not math.isfinite(item[key]) for item in (first, last) for key in keys):
            raise ValueError('Mesures de structures faibles non finies')
        if first['contrast'] <= 1e-7 or first['cnr'] <= 1e-7:
            return dict(available=False, score_adjustment=0., sufficient=False)
        contrast = last['contrast']/first['contrast']
        cnr = last['cnr']/first['cnr']
        sky = last['sky_upper_median']/max(first['sky_upper_median'], 1e-7)
        faint_floor = min(first['faint_median'], cls.VISIBLE_FAINT_LEVEL)
        faint_retention = last['faint_median']/max(faint_floor, 1e-7)
        sufficient = (contrast >= cls.CONTRAST_RETENTION and cnr >= cls.CNR_RETENTION
                      and faint_retention >= cls.CONTRAST_RETENTION)
        deficit = (max(0., 1-contrast)**2 + max(0., 1-cnr)**2
                   + max(0., 1-faint_retention)**2)
        # Pénaliser le ciel au-delà du budget nécessaire à la visibilité faible.
        visibility_gain_budget = max(1., cls.VISIBLE_FAINT_LEVEL/max(first['faint_median'], 1e-7))
        useful_gain = visibility_gain_budget*min(2., max(1., cnr))
        sky_cost = cls.SCORE_WEIGHT*max(0., math.log2(max(sky/useful_gain, 1e-7)))
        visibility_progress = (
            min(1., max(0., last['contrast'])/cls.VISIBILITY_CONTRAST_TARGET)
            * min(1., last['faint_median']/cls.VISIBLE_FAINT_LEVEL)
            - min(1., first['contrast']/cls.VISIBILITY_CONTRAST_TARGET)
            * min(1., first['faint_median']/cls.VISIBLE_FAINT_LEVEL))
        reward = cls.SCORE_WEIGHT*min(1., max(0., -math.log2(max(sky, 1e-7)))) if sufficient else 0.
        reference_visible = (first['contrast'] >= cls.VISIBILITY_CONTRAST_TARGET
                             and first['faint_median'] >= cls.VISIBLE_FAINT_LEVEL)
        unnecessary_lift = (reference_visible and sky > 1+cls.MIN_BACKGROUND_IMPROVEMENT
                            and cnr <= 1+cls.COMPARABLE_QUALITY_TOLERANCE
                            and visibility_progress <= cls.COMPARABLE_QUALITY_TOLERANCE)
        waste_penalty = (cls.UNNECESSARY_SKY_WEIGHT*math.log2(sky)
                         if unnecessary_lift else 0.)
        return dict(available=True, sufficient=sufficient, contrast_retention=contrast,
                    reference_contrast=first['contrast'], contrast=last['contrast'],
                    reference_sky_level=first['sky_upper_median'], sky_level=last['sky_upper_median'],
                    reference_faint_level=first['faint_median'], faint_level=last['faint_median'],
                    cnr_retention=cnr, sky_brightness_ratio=sky,
                    score_adjustment=(reward-sky_cost-2*deficit+cls.SCORE_WEIGHT*visibility_progress
                                      - waste_penalty),
                    unnecessary_sky_lift=unnecessary_lift,
                    unnecessary_sky_penalty=waste_penalty,
                    visibility_progress=visibility_progress,
                    lower_background_preferred=sufficient and sky < 1-cls.MIN_BACKGROUND_IMPROVEMENT,
                    contrast_floor=cls.CONTRAST_RETENTION, cnr_floor=cls.CNR_RETENTION,
                    faint_level_floor=faint_floor, faint_level_retention=faint_retention,
                    visibility_gain_budget=visibility_gain_budget,
                    sky_penalty=sky_cost, preservation_penalty=2*deficit)

    @classmethod
    def shape_penalty(cls, audit: JSONReport, comparison: JSONReport) -> float:
        """Borne les seuls écarts de courbure si les pentes restent descendantes.

        Ne retire aucun rejet structurel. Sans preuve de préservation des
        structures faibles, la pénalité antérieure est conservée intégralement.
        """
        penalty = audit.get('penalty', 0.)
        slopes = audit.get('candidate_geometry', {}).get('slopes', [])
        if (comparison.get('available') and comparison.get('sufficient')
                and slopes and all(value < 0 for value in slopes)):
            return min(penalty, cls.SHAPE_PENALTY_CAP)
        return penalty
