"""Indicateurs non bloquants d'apparence des étoiles après étirement global.

Mesure seulement les pixels, sans les modifier ni piloter de correction locale.
Voir docs/scripts/postProcess/stellar-appearance.md pour unités et limites.
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import map_coordinates

from lib.star_separation import validate_rgb
from lib.type_defs import JSONReport


class StellarAppearanceQuality:
    """Mesure visibilité indicative, diamètre apparent, transition et jupe.

    Les candidats et coordonnées proviennent de l'entrée, jamais d'une nouvelle
    détection du rendu. Le seuil de visibilité est un contraste de luminosité pondérée au-dessus
    du fond local, en unités normalisées. Les mesures locales sont diagnostiques.
    """

    VISIBLE_TARGET = .40
    VISIBILITY_LEVEL = .03
    RADIUS = 32
    RAYS = 16
    STEP = .5
    MAX_PROFILES = 64

    @classmethod
    def _profile(cls, plane: np.ndarray, y: int, x: int, floor: float) -> JSONReport:
        """Mesure les premières traversées radiales ; un bord tronqué est inconnu.

        Les rayons sont échantillonnés tous les 0,5 pixel par interpolation
        bilinéaire. Les niveaux 80/50/20/5 % sont relatifs au pic au-dessus du
        fond. Un voisin, une aile non monotone ou un rayon tronqué peut biaiser
        les premières traversées ; ce n'est ni une PSF ajustée ni une photométrie.
        """
        height, width = plane.shape
        if min(y, x, height-1-y, width-1-x) < cls.RADIUS:
            return dict(status='UNKNOWN', reason='TRUNCATED_PROFILE')
        peak = float(plane[y, x]) - floor
        if peak <= 0:
            return dict(status='UNKNOWN', reason='NO_POSITIVE_CONTRAST')
        radii = np.arange(0, cls.RADIUS+cls.STEP/2, cls.STEP)
        angles = np.arange(cls.RAYS) * 2*np.pi/cls.RAYS
        yy = y + np.sin(angles)[:, None]*radii
        xx = x + np.cos(angles)[:, None]*radii
        profiles = map_coordinates(plane, [yy, xx], order=1, mode='nearest') - floor
        crossings: dict[str, float | None] = {}
        for name, level in [('r80', .8*peak), ('r50', .5*peak),
                            ('r20', .2*peak), ('r05', .05*peak),
                            ('visible_radius', cls.VISIBILITY_LEVEL)]:
            values = []
            for profile in profiles:
                if peak < level:
                    continue
                indices = np.flatnonzero(profile[1:] <= level)
                if not indices.size:
                    continue
                index = int(indices[0])+1
                upper, lower = float(profile[index-1]), float(profile[index])
                if upper <= lower:
                    continue
                values.append(float(radii[index-1] + cls.STEP*(upper-level)/(upper-lower)))
            # Une médiane de trop peu de directions ne certifie pas la forme entière.
            crossings[name] = float(np.median(values)) if len(values) == cls.RAYS else None
        r80, r20 = crossings['r80'], crossings['r20']
        width = r20-r80 if r20 is not None and r80 is not None else None
        r50, r05 = crossings['r50'], crossings['r05']
        visible = crossings['visible_radius']
        return dict(
            status='MEASURED' if width is not None and width > 0 else 'UNKNOWN',
            peak_contrast=peak,
            visible_diameter_px=2*visible if visible is not None else None,
            transition_width_px=width,
            edge_gradient_per_px=.6*peak/width if width is not None and width > 0 else None,
            relative_edge_gradient_per_px=.6/width if width is not None and width > 0 else None,
            skirt_radius_ratio=r05/r50 if r05 is not None and r50 is not None and r50 > 0 else None,
        )

    @classmethod
    def evaluate(
        cls, before: np.ndarray, after: np.ndarray,
        positions: tuple[np.ndarray, np.ndarray],
        *, target_fraction: float | None = None,
    ) -> JSONReport:
        """Évalue le rendu sans modifier ses pixels ; la cible guide le moteur global.

        Le fond local est la médiane du bord d'un carré de rayon 32 pixels.
        Les candidats trop proches d'un bord restent inconnus. L'objectif 40 %
        utilise des bornes incluant ces inconnus, jamais assimilés à zéro.
        Les profils concernent au plus 64 candidats visibles répartis en amplitude.
        Les autres objectifs n'ont pas encore de seuil perceptuel calibré.
        """
        target = cls.VISIBLE_TARGET if target_fraction is None else target_fraction
        if not np.isfinite(target) or not 0 <= target <= 1:
            raise ValueError('Objectif de visibilité stellaire hors de [0,1]')
        validate_rgb(before)
        validate_rgb(after)
        if before.shape != after.shape:
            raise ValueError('Géométrie incompatible pour les indicateurs stellaires')
        yy, xx = positions
        if yy.shape != xx.shape or yy.ndim != 1:
            raise ValueError('Positions stellaires incompatibles')
        # Même luminosité human que le GHS ; proxy non calibré de la perception.
        first = .2126*before[0] + .7152*before[1] + .0722*before[2]
        last = .2126*after[0] + .7152*after[1] + .0722*after[2]
        if not np.isfinite(yy).all() or not np.isfinite(xx).all():
            raise ValueError('Positions stellaires non finies')
        count = int(yy.size)
        if np.unique(np.column_stack((yy, xx)), axis=0).shape[0] != count:
            raise ValueError('Positions stellaires répétées')
        candidates = []
        unknown = 0
        radius = cls.RADIUS
        for y0, x0 in zip(yy, xx):
            y, x = int(y0), int(x0)
            if (y != y0 or x != x0 or not 0 <= y < last.shape[0]
                    or not 0 <= x < last.shape[1]):
                raise ValueError('Position stellaire hors image')
            if min(y, x, last.shape[0]-1-y, last.shape[1]-1-x) < radius:
                unknown += 1
                continue
            floors = []
            for plane in (first, last):
                patch = plane[y-radius:y+radius+1, x-radius:x+radius+1]
                border = np.r_[patch[0], patch[-1], patch[1:-1, 0], patch[1:-1, -1]]
                floors.append(float(np.median(border)))
            contrast = float(last[y, x])-floors[1]
            if contrast >= cls.VISIBILITY_LEVEL:
                candidates.append((contrast, y, x, floors))
        visible = len(candidates)
        lower = visible/count if count else None
        upper = (visible+unknown)/count if count else None
        status = ('UNKNOWN' if count == 0 else 'PASS' if lower >= target
                  else 'FAIL' if upper < target else 'UNKNOWN')
        ordered = sorted(candidates, key=lambda item: item[0])
        if len(ordered) > cls.MAX_PROFILES:
            ordered = [ordered[i] for i in np.linspace(0, len(ordered)-1,
                                                      cls.MAX_PROFILES, dtype=int)]
        profiles = []
        for _, y, x, floors in ordered:
            profiles.append(dict(y=y, x=x,
                                 before=cls._profile(first, y, x, floors[0]),
                                 after=cls._profile(last, y, x, floors[1])))
        metrics: JSONReport = {}
        for key in ('visible_diameter_px', 'transition_width_px', 'edge_gradient_per_px',
                    'relative_edge_gradient_per_px', 'skirt_radius_ratio'):
            paired = []
            values = []
            for entry in profiles:
                a, b = entry['before'].get(key), entry['after'].get(key)
                if b is not None:
                    values.append(b)
                if a is not None and b is not None:
                    paired.append((a, b))
            metrics[key] = dict(
                median=float(np.median(values)) if values else None,
                sample_count=len(values), paired_count=len(paired),
                before_paired_median=float(np.median([p[0] for p in paired])) if paired else None,
                after_paired_median=float(np.median([p[1] for p in paired])) if paired else None,
            )
        return dict(
            blocking=False, method='fixed input candidates; luminance contrast; radial crossings',
            luminance_weights=[.2126, .7152, .0722],
            visibility=dict(status=status, blocking=False, target_fraction=target,
                            contrast_threshold=cls.VISIBILITY_LEVEL, candidate_count=count,
                            visible_count=visible, unknown_count=unknown,
                            fraction_lower_bound=lower, fraction_upper_bound=upper),
            shape=dict(status='UNKNOWN', blocking=False, reason='NO_CALIBRATED_SHAPE_TARGET',
                       sampled_visible_count=len(profiles), metrics=metrics),
            profiles=profiles,
        )
