"""Sélection fixe des régions diffuses par tailles et dispersion spatiale.

Les décisions dépendent uniquement du masque de référence. Voir
``docs/scripts/postProcess/diffuse-regions.md`` pour les quotas et limites.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import math

import numpy as np

from lib.type_defs import JSONReport

MIN_REGION_AREA = 64


@dataclass(frozen=True)
class DiffuseRegion:
    """Région identifiée sur la référence, aire en pixels et fenêtre native."""

    identifier: int
    bounds: tuple[slice, slice]
    area: int

    @property
    def size_class(self) -> int:
        """Retourne l'octave d'aire : k représente [2**k, 2**(k+1)[ pixels."""
        return self.area.bit_length() - 1

    def position(self, shape: tuple[int, int]) -> tuple[float, float]:
        """Centre de la boîte englobante, en coordonnées relatives au champ."""
        ys, xs = self.bounds
        return ((ys.start + ys.stop) / (2 * shape[0]),
                (xs.start + xs.stop) / (2 * shape[1]))

    def quadrant(self, shape: tuple[int, int]) -> int:
        """Classe le centre de la boîte parmi les quatre quadrants du champ."""
        y, x = self.position(shape)
        return 2 * int(y >= .5) + int(x >= .5)


def validate_region_limit(maximum: int) -> None:
    """Accepte un entier positif ou zéro pour désactiver l'échantillonnage."""
    if type(maximum) is not int or maximum < 0:
        raise ValueError('quality-max-diffuse-regions doit être un entier positif ou nul')


def _spread(
    regions: Sequence[DiffuseRegion], count: int, shape: tuple[int, int],
) -> list[DiffuseRegion]:
    """Conserve le plus grand objet puis couvre les quadrants et espaces libres.

    Si le quota le permet, chaque quadrant présent reçoit un représentant.
    Les suivants maximisent la distance minimale aux centres retenus. Les
    égalités favorisent l'aire puis l'identifiant initial, sans hasard.
    """
    ranked = sorted(regions, key=lambda region: (-region.area, region.identifier))
    if count >= len(ranked):
        return ranked
    coordinates = np.asarray([region.position(shape) for region in ranked])
    selected = [0]
    quadrants = [region.quadrant(shape) for region in ranked]
    if count >= len(set(quadrants)):
        for quadrant in sorted(set(quadrants)):
            if quadrant not in {quadrants[index] for index in selected}:
                selected.append(quadrants.index(quadrant))
    distance = np.full(len(ranked), np.inf)
    for index in selected:
        distance = np.minimum(distance, np.sum((coordinates - coordinates[index]) ** 2, axis=1))
    distance[selected] = -1
    while len(selected) < count:
        index = int(np.argmax(distance))
        selected.append(index)
        distance = np.minimum(distance, np.sum((coordinates - coordinates[index]) ** 2, axis=1))
        distance[selected] = -1
    return [ranked[index] for index in selected]


def select_diffuse_regions(
    bounds: Sequence[tuple[slice, slice] | None], sizes: np.ndarray,
    shape: tuple[int, int], maximum: int,
) -> tuple[tuple[DiffuseRegion, ...], JSONReport]:
    """Répartit un budget entre octaves d'aire puis dans le champ de référence.

    Un représentant minimum par octave occupée est obligatoire. Chaque quadrant
    de chaque octave est couvert si le budget le permet ; le reste
    suit des quotas proportionnels à la racine de l'effectif, plafonnés par
    l'effectif disponible. Les classes rares sont ainsi surreprésentées par
    rapport à un tirage uniforme. Le plus grand objet de chaque classe reste
    présent. Un plafond trop petit pour couvrir les classes lève ValueError.
    Zéro ou un effectif inférieur au plafond conserve toutes les régions.
    Les régions de moins de 64 pixels restent exclues comme auparavant.
    """
    validate_region_limit(maximum)
    if len(shape) != 2 or min(shape) <= 0:
        raise ValueError('Dimensions de référence invalides')
    regions = [DiffuseRegion(index, window, int(sizes[index]))
               for index, window in enumerate(bounds, 1)
               if window is not None and sizes[index] >= MIN_REGION_AREA]
    groups: dict[int, list[DiffuseRegion]] = {}
    for region in regions:
        groups.setdefault(region.size_class, []).append(region)
    target = min(maximum, len(regions)) if maximum else len(regions)
    if target < len(groups):
        raise ValueError(f'quality-max-diffuse-regions doit couvrir les {len(groups)} '
                         'classes de taille présentes ; augmenter le plafond ou utiliser 0')
    spatial_minimum = {
        key: len({region.quadrant(shape) for region in group}) for key, group in groups.items()
    }
    quotas = (spatial_minimum if sum(spatial_minimum.values()) <= target
              else {key: 1 for key in groups})
    for _ in range(target - sum(quotas.values())):
        candidates = [key for key in groups if quotas[key] < len(groups[key])]
        key = max(candidates, key=lambda key: (math.sqrt(len(groups[key])) / quotas[key], key))
        quotas[key] += 1
    selected = []
    strata = []
    for key in sorted(groups):
        sample = _spread(groups[key], quotas[key], shape)
        selected.extend(sample)
        strata.append(dict(min_area_pixels=2**key, max_area_pixels=2**(key+1)-1,
                           available_regions=len(groups[key]), evaluated_regions=len(sample),
                           available_quadrants=sorted({r.quadrant(shape) for r in groups[key]}),
                           evaluated_quadrants=sorted({r.quadrant(shape) for r in sample})))
    selected.sort(key=lambda region: region.identifier)
    report: JSONReport = dict(
        method='area_octaves_spatial_farthest_points', maximum_regions=maximum,
        available_regions=len(regions), evaluated_regions=len(selected),
        skipped_regions=len(regions)-len(selected), minimum_area_pixels=MIN_REGION_AREA,
        exhaustive=len(selected) == len(regions), area_strata=strata,
        selected_regions=[dict(region=r.identifier, area_pixels=r.area,
                               y_start=r.bounds[0].start, y_stop=r.bounds[0].stop,
                               x_start=r.bounds[1].start, x_stop=r.bounds[1].stop)
                          for r in selected],
    )
    return tuple(selected), report
