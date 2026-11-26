"""Mesure fixe des structures faibles et du ciel pour le classement GHS.

Les masques ne servent qu'aux mesures. Aucune correction spatiale des pixels
n'est effectuée. Voir docs/scripts/postProcess/ghs-weak-structures.md.
"""
from __future__ import annotations

import math
import argparse
from copy import deepcopy
import hashlib
from types import ModuleType
import numpy as np
from scipy.ndimage import gaussian_filter, label, binary_dilation, binary_propagation

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
    SEED_SIGMA = 6.
    SEED_QUANTILE = .95
    INTERMEDIATE_SIGMA = 3.
    WEAK_SIGMA = 1.1
    MIN_SEED_PIXELS = 4
    SEGMENTATION_VERSION = 'connected_hysteresis_v5'
    FALLBACK_FAINT_FRACTION = .01
    SKY_EXCLUSION_ITERATIONS = 2
    SKY_SIGMA = 2.
    REGION_GRID = 4
    MIN_SKY_REGIONS = 4
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

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare le seuil faible en unités de bruit, persistant entre les appels."""
        parser.add_argument('--ghs-weak-sigma', type=float, default=cls.WEAK_SIGMA,
                            help='Seuil des extensions faibles connectées, en sigma '
                                 '(défaut 1,1 ; [1,3])')

    @staticmethod
    def validate_sigma(value: float) -> float:
        """Retourne un seuil fini entre 1 et 3 inclus ; refuse booléens et hors domaine."""
        if isinstance(value, (bool, np.bool_)) or not math.isfinite(value) or not 1 <= value <= 3:
            raise ValueError('ghs-weak-sigma doit être compris entre 1 et 3')
        return float(value)

    @classmethod
    def connected_regions(
        cls, significance: np.ndarray, active: np.ndarray,
        *, seed_support: np.ndarray | None = None, weak_sigma: float = WEAK_SIGMA,
    ) -> dict[str, np.ndarray]:
        """Relie les faibles excès aux graines fortes par hystérésis à huit voisins.

        significance est le maximum multi-échelle du signal normalisé par
        son bruit estimé, en unités sigma. Les graines ≥6 sigma d'au moins
        quatre cellules croissent dans le support ≥3 puis ≥weak_sigma, sans
        franchir de trou sous le seuil faible. Les composantes finales doivent
        avoir au moins 16 cellules. Les parties faibles sont le support connecté
        entre weak_sigma (défaut 1,1 ; [1,3]) et 3 sigma ; les îlots sans graine
        sont exclus du critère,
        sans être déclarés bruit ni supprimés des pixels scientifiques.
        seed_support restreint facultativement les graines aux pixels dont la
        luminosité dépasse le percentile fort ; cette restriction ne s'applique
        pas au support d'extension faible.
        """
        weak_sigma = cls.validate_sigma(weak_sigma)
        if (significance.ndim != 2 or not significance.size or significance.shape != active.shape
                or not np.isfinite(significance).all() or active.dtype != bool):
            raise ValueError('Significativité finie et masque actif booléen 2D requis')
        if seed_support is not None and (seed_support.shape != active.shape or seed_support.dtype != bool):
            raise ValueError('Masque des graines incompatible')
        connectivity = np.ones((3, 3), dtype=bool)
        strong = active & (significance >= cls.SEED_SIGMA)
        if seed_support is not None:
            strong &= seed_support
        labels, _ = label(strong, structure=connectivity)
        sizes = np.bincount(labels.ravel())
        accepted = sizes >= cls.MIN_SEED_PIXELS
        accepted[0] = False
        seeds = accepted[labels]
        middle_support = active & (significance >= cls.INTERMEDIATE_SIGMA)
        weak_support = active & (significance >= weak_sigma)
        middle = binary_propagation(seeds, structure=connectivity, mask=middle_support)
        objects = binary_propagation(middle, structure=connectivity, mask=weak_support)
        labels, _ = label(objects, structure=connectivity)
        sizes = np.bincount(labels.ravel())
        accepted = sizes >= cls.MIN_COMPONENT_PIXELS
        accepted[0] = False
        objects = accepted[labels]
        return dict(seeds=seeds & objects, middle=middle_support & objects, objects=objects,
                    faint=objects & ~middle_support)

    @classmethod
    def admit_margin_regions(
        cls, significance: np.ndarray, active: np.ndarray,
        masks: dict[str, np.ndarray], *, weak_sigma: float = WEAK_SIGMA,
        sky_candidates: np.ndarray | None = None,
    ) -> dict[str, np.ndarray]:
        """Propage les admissions amorcées dans la marge figée, puis rattache le faible.

        La marge est la dilatation à quatre voisins, deux itérations, des objets
        initiaux. Les cellules jaunes S>weak_sigma de cette marge amorcent une
        propagation à huit voisins dans leur composante jaune entière, puis
        dans le support faible S>=weak_sigma connecté. Les trous sous seuil,
        cellules inactives et le ciel figé arrêtent la propagation. Aucune
        nouvelle dilatation n'est faite après admission. sky_candidates fournit
        les conditions photométriques du ciel avant exclusion spatiale ; None
        signifie qu'aucune cellule n'est éligible au ciel pour cet appel.
        Le masque exclusion reste initial. Les cellules admises <3 sigma rejoignent faint,
        les autres middle ; elles ne créent pas de nouvelles graines.
        Les tableaux d'entrée ne sont pas modifiés. Géométries incompatibles
        ou valeurs de significativité invalides lèvent ValueError.
        """
        weak_sigma = cls.validate_sigma(weak_sigma)
        if 'exclusion' in masks:
            raise ValueError('La marge des structures faibles est déjà préparée')
        if (significance.ndim != 2 or not significance.size
                or significance.shape != active.shape or active.dtype != bool
                or not np.isfinite(significance).all()
                or any(masks[name].shape != active.shape or masks[name].dtype != bool
                       for name in ('objects', 'seeds', 'middle', 'faint'))):
            raise ValueError('Géométrie de la marge des structures faibles incompatible')
        initial = masks['objects'].copy()
        exclusion = binary_dilation(initial, iterations=cls.SKY_EXCLUSION_ITERATIONS)
        if sky_candidates is not None and (
                sky_candidates.shape != active.shape or sky_candidates.dtype != bool):
            raise ValueError('Masque du ciel des structures faibles incompatible')
        sky = (active & sky_candidates & ~exclusion if sky_candidates is not None
               else np.zeros_like(active))
        yellow = active & ~initial & ~sky & (significance > weak_sigma)
        starts = exclusion & yellow
        connectivity = np.ones((3, 3), dtype=bool)
        selected = binary_propagation(starts, structure=connectivity, mask=yellow)
        support = initial | (active & ~sky & (significance >= weak_sigma))
        objects = binary_propagation(initial | selected, structure=connectivity, mask=support)
        admitted = objects & ~initial
        return dict(
            **{key: value for key, value in masks.items() if key not in ('objects', 'middle', 'faint')},
            initial_objects=initial, exclusion=exclusion, admitted=admitted, sky=sky,
            admission_starts=starts, objects=objects,
            middle=masks['middle'] | (admitted & (significance >= cls.INTERMEDIATE_SIGMA)),
            faint=masks['faint'] | (admitted & (significance < cls.INTERMEDIATE_SIGMA)),
        )

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
    def faint_reference_regions(
        cls, reference: np.ndarray, objects: np.ndarray, detected: np.ndarray,
    ) -> tuple[np.ndarray, JSONReport]:
        """Choisit le faible détecté ou le centile inférieur du signal retenu.

        Avec moins de MIN_PIXELS cellules détectées, prend exactement
        ceil(1 % de l'effectif objects), au moins une cellule si objects existe,
        par luminosité de référence croissante. Les égalités sont départagées
        par l'ordre spatial stable. Aucun minimum de 32 n'est réappliqué au
        repli. Le masque retourné est indépendant et figé sur cette référence.
        Les tableaux doivent partager une géométrie 2D, les masques être booléens
        et detected inclus dans objects ; les valeurs doivent être finies.
        """
        if (reference.ndim != 2 or reference.shape != objects.shape
                or objects.shape != detected.shape or objects.dtype != bool
                or detected.dtype != bool or not np.isfinite(reference).all()
                or (detected & ~objects).any()):
            raise ValueError('Régions de référence faible incompatibles')
        count = int(detected.sum())
        if count >= cls.MIN_PIXELS:
            return detected.copy(), dict(mode='detected_weak', detected_count=count)
        indices = np.flatnonzero(objects)
        mask = np.zeros_like(objects)
        size = math.ceil(indices.size*cls.FALLBACK_FAINT_FRACTION)
        if size:
            order = np.argsort(reference.ravel()[indices], kind='stable')
            mask.ravel()[indices[order[:size]]] = True
        return mask, dict(mode='lowest_percentile', fraction=cls.FALLBACK_FAINT_FRACTION,
                          detected_count=count, selected_count=size,
                          ranking='reference_luminance; stable_spatial_ties')

    @classmethod
    def classify_grid(
        cls, reference: np.ndarray, *, weak_sigma: float = WEAK_SIGMA,
    ) -> tuple[JSONReport, dict[str, np.ndarray]]:
        """Expose les masques exacts utilisés par les mesures sur une grille CPU.

        reference est la grille de luminosité 2D déjà échantillonnée (au plus
        192 cellules par côté dans le chemin normal), finie dans [0,1]. Les
        masques seeds, middle, objects, sky et faint sont fixes sur cette
        référence et ne modifient aucun pixel. Si les effectifs des régions
        faibles sont insuffisants, le centile inférieur du signal retenu sert
        de repli. Sans assez d'objets ou de ciel, l'audit reste indisponible.
        La bande diagnostique 1<S<weak_sigma
        reste publiée si elle existe, même sans objet connecté ; elle ne sert
        pas aux mesures de qualité.
        weak_sigma est le seuil faible, fini dans [1,3], en unités de bruit.
        """
        weak_sigma = cls.validate_sigma(weak_sigma)
        first = np.asarray(reference, dtype=np.float64)
        if (first.ndim != 2 or not first.size or not np.isfinite(first).all()
                or first.min() < 0 or first.max() > 1+1e-6):
            raise ValueError('Grille de luminosité GHS invalide')
        unavailable = dict(available=False, reason='INSUFFICIENT_FIXED_REGIONS',
                           grid_shape=list(first.shape), stride=1, estimated=True)
        if min(first.shape) < 2*cls.BACKGROUND_SCALE:
            return unavailable, {}
        active = first > 1e-6
        if np.count_nonzero(active) < 4*cls.MIN_PIXELS:
            return unavailable, {}
        difference = np.diff(first, axis=1)
        sigma = float(1.4826*np.median(abs(difference-np.median(difference)))/np.sqrt(2))
        if sigma <= 1e-10:
            return dict(unavailable, reason='UNMEASURABLE_REFERENCE_NOISE'), {}
        trend = gaussian_filter(first, cls.BACKGROUND_SCALE, mode='reflect')
        significance = np.full(first.shape, -np.inf)
        smooth = gaussian_filter(first, 1., mode='reflect')
        for scale in cls.SIGNAL_SCALES:
            signal = gaussian_filter(first, scale, mode='reflect')-trend
            filtered_noise = max(sigma/(np.sqrt(4*np.pi)*scale), 1e-7)
            significance = np.maximum(significance, signal/filtered_noise)
        seed_level = float(np.quantile(first[active], cls.SEED_QUANTILE))
        masks = cls.connected_regions(significance, active, seed_support=first > seed_level,
                                      weak_sigma=weak_sigma)
        sky_candidates = (active
                          & (abs(smooth-trend) <= max(cls.SKY_SIGMA*sigma/np.sqrt(4*np.pi), 1e-7))
                          & (smooth <= np.quantile(smooth[active], cls.SKY_REFERENCE_QUANTILE)))
        masks = cls.admit_margin_regions(
            significance, active, masks, weak_sigma=weak_sigma, sky_candidates=sky_candidates,
        )
        subthreshold = active & (significance > 1.) & (significance < weak_sigma)
        objects, faint = masks['objects'], masks['faint']
        masks['detected_faint'] = faint
        faint, faint_selection = cls.faint_reference_regions(first, objects, faint)
        masks['faint'] = faint
        sky = masks['sky']
        enough_regions = min(np.count_nonzero(objects), np.count_nonzero(sky)) >= 2*cls.MIN_PIXELS
        enough_faint = bool(faint.any())
        low = float(smooth[faint].min()) if faint.any() else None
        high = float(smooth[faint].max()) if faint.any() else None
        result = dict(available=bool(enough_regions and enough_faint),
                      classification_available=bool(objects.any()),
                      method='fixed multiscale significance; hysteresis and frozen margin admission',
                      segmentation_version=cls.SEGMENTATION_VERSION,
                      seed_quantile=cls.SEED_QUANTILE, seed_luminance_threshold=seed_level,
                      sigma_thresholds=dict(seed=cls.SEED_SIGMA, intermediate=cls.INTERMEDIATE_SIGMA,
                                            weak=weak_sigma), connectivity=8,
                      seed_count=int(masks['seeds'].sum()), intermediate_count=int(masks['middle'].sum()),
                      grid_shape=list(first.shape), stride=1, estimated=True,
                      classification='heuristic_not_proof_of_empty_sky',
                      reference_sha256=hashlib.sha256(first.tobytes()).hexdigest(),
                      object_count=int(objects.sum()), faint_count=int(faint.sum()),
                      faint_selection=faint_selection,
                      initial_object_count=int(masks['initial_objects'].sum()),
                      admitted_count=int(masks['admitted'].sum()),
                      admission_start_count=int(masks['admission_starts'].sum()),
                      exclusion_count=int(masks['exclusion'].sum()),
                      subthreshold_count=int(subthreshold.sum()),
                      sky_count=int(sky.sum()), faint_reference_range=[low, high])
        if not result['available']:
            result['reason'] = ('INSUFFICIENT_FIXED_REGIONS' if not enough_regions
                                else 'INSUFFICIENT_FAINT_PIXELS')
        return result, dict(
            **masks, subthreshold=subthreshold,
            below_one=active & (significance < 1.),
            above_threshold=active & (significance > weak_sigma),
            on_threshold=active & ((significance == 1.) | (significance == weak_sigma)),
        )

    @classmethod
    def measure(
        cls, reference: np.ndarray, candidate: np.ndarray, xp: ModuleType = np,
        *, weak_sigma: float = WEAK_SIGMA,
        evaluator: WeakStructureEvaluator | None = None,
    ) -> JSONReport:
        """Mesure luminosités, contraste relatif, CNR et ciel régional sur une grille.

        Les images sont des luminosités normalisées 2D, dans la même géométrie.
        Les objets proviennent d'un excès multi-échelle positif par rapport à
        une tendance lente. Les composantes de moins de 16 cellules sont exclues.
        Les parties faibles sont les excès de weak_sigma (défaut 1,1) à 3 sigma
        connectés à une graine forte ; aucun quantile commun ne mélange les niveaux des objets.
        La tendance et le bruit sont des outils de détection, jamais des corrections.
        evaluator conserve les masques et les mesures de référence entre appels.
        Sans instance fournie, cet appel autonome en crée une temporaire.
        À défaut de 32 cellules faibles détectées, leur masque est remplacé
        par le centile inférieur des luminosités de référence du signal retenu.
        """
        model = evaluator or WeakStructureEvaluator(reference, xp, weak_sigma=weak_sigma)
        model.validate_reference(reference, xp, weak_sigma=weak_sigma)
        return model.evaluate(candidate, xp)

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
                       ('segmentation_version', 'sigma_thresholds', 'faint_selection', 'reference_sha256',
                        'grid_shape', 'object_count', 'faint_count',
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


class WeakStructureEvaluator:
    """Conserve la segmentation d'une référence et mesure ses candidats sans la refaire.

    Une instance appartient à une référence, une grille et un seuil. Elle copie
    uniquement la petite grille CPU, fige ses masques et calcule les statistiques
    de référence une seule fois. Aucun état n'est partagé entre phases/images.
    Les audits retournés sont des copies indépendantes. Voir la documentation
    des structures faibles GHS pour les hypothèses et limites de détection.
    """

    def __init__(
        self, reference: np.ndarray, xp: ModuleType = np, *,
        weak_sigma: float = WeakStructureQuality.WEAK_SIGMA,
    ) -> None:
        """Prépare une luminosité 2D normalisée ; refuse géométrie ou valeurs invalides."""
        if reference.ndim != 2 or not reference.size:
            raise ValueError('Géométrie des structures faibles incompatible')
        self._shape = reference.shape
        self._weak_sigma = WeakStructureQuality.validate_sigma(weak_sigma)
        self._stride = max(1, math.ceil(max(reference.shape)/WeakStructureQuality.MAX_SIDE))
        self._reference = self._grid(reference, xp).copy()
        self._reference.setflags(write=False)
        self._audit, self._masks = WeakStructureQuality.classify_grid(
            self._reference, weak_sigma=self._weak_sigma,
        )
        self._audit['stride'] = self._stride
        for mask in self._masks.values():
            mask.setflags(write=False)
        if self._audit['available']:
            self._audit['reference'] = self._statistics(self._reference)

    def _grid(self, image: np.ndarray, xp: ModuleType) -> np.ndarray:
        """Échantillonne les mêmes coordonnées CPU/backend et contrôle le domaine [0,1]."""
        if image.shape != self._shape:
            raise ValueError('Géométrie des structures faibles incompatible')
        grid = image[::self._stride, ::self._stride]
        grid = np.asarray(grid if xp is np else xp.asnumpy(grid), dtype=np.float64)
        if (not np.isfinite(grid).all() or grid.min() < 0 or grid.max() > 1+1e-6):
            raise ValueError('Luminosités GHS invalides')
        return grid

    def validate_reference(
        self, reference: np.ndarray, xp: ModuleType = np, *, weak_sigma: float,
    ) -> None:
        """Refuse la réutilisation avec un autre seuil ou une autre grille de référence."""
        if (WeakStructureQuality.validate_sigma(weak_sigma) != self._weak_sigma
                or not np.array_equal(self._grid(reference, xp), self._reference)):
            raise ValueError('Référence ou seuil des structures faibles incompatible')

    def _statistics(self, plane: np.ndarray) -> JSONReport:
        """Mesure médianes, contraste et bruit sur les masques fixes, sans redétection."""
        quality = WeakStructureQuality
        sky, faint = self._masks['sky'], self._masks['faint']
        background = float(np.median(plane[sky]))
        object_level = float(np.median(plane[faint]))
        separation = object_level-background
        noise = quality._noise(plane, sky)
        tile_levels = []
        for ys in np.array_split(np.arange(plane.shape[0]), quality.REGION_GRID):
            for xs in np.array_split(np.arange(plane.shape[1]), quality.REGION_GRID):
                local_sky = sky[np.ix_(ys, xs)]
                if local_sky.sum() >= quality.MIN_PIXELS:
                    tile_levels.append(float(np.median(plane[np.ix_(ys, xs)][local_sky])))
        return dict(sky_median=background, faint_median=object_level,
                    contrast=(separation/(object_level+background)
                              if object_level+background > 1e-10 else None),
                    cnr=separation/noise if noise is not None else None,
                    noise=noise, sky_upper_median=float(np.quantile(tile_levels, .9))
                    if len(tile_levels) >= quality.MIN_SKY_REGIONS else None,
                    sky_region_count=len(tile_levels))

    def evaluate(self, candidate: np.ndarray, xp: ModuleType = np) -> JSONReport:
        """Mesure un candidat normalisé et retourne un audit indépendant des appels précédents."""
        last = self._grid(candidate, xp)
        result = deepcopy(self._audit)
        if result['available']:
            result['candidate'] = self._statistics(last)
        return result
