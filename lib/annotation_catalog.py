"""Projection hors ligne d'OpenNGC sur le WCS du rendu, sans détection d'objets.

L'extrait et ses crédits sont dans data/openngc ; les coordonnées sont ICRS en
degrés. Les magnitudes sont V, sinon B : le seuil est indicatif pour la lisibilité.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, replace
import math
from pathlib import Path

import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import NoConvergence, WCS
from astropy.wcs.utils import pixel_to_skycoord, skycoord_to_pixel
import numpy as np

from lib.type_defs import JSONReport

CATALOG_PATH = Path(__file__).resolve().parents[1] / 'data/openngc/objects.csv'
CATALOG_CREDIT = 'OpenNGC — Mattia Verga et contributeurs — CC BY-SA 4.0'
CATALOG_SOURCE = (
    'https://github.com/mattiaverga/OpenNGC/tree/'
    'be3cd6c5498806a2fbb2f7e0452ef802dbb02be0'
)


@dataclass(frozen=True)
class SkyObject:
    """Position ICRS, magnitude et diamètres célestes facultatifs en arcminutes.

    position_angle_deg est mesuré du nord vers l'est. outline conserve les
    points projetés en pixels écran, sans modifier les dimensions du catalogue.
    """

    name: str
    ra: float
    dec: float
    magnitude: float | None
    messier: str
    major_arcmin: float | None = None
    minor_arcmin: float | None = None
    position_angle_deg: float | None = None
    outline: tuple[tuple[float, float], ...] = ()
    common_names: tuple[str, ...] = ()
    common_name_fr: str = ''

    @property
    def label(self) -> str:
        """Affiche le nom usuel, puis les identifiants sur une seconde ligne.

        Une traduction française disponible est prioritaire ; sinon le premier
        nom OpenNGC est conservé tel quel. Sans nom usuel, seuls les identifiants
        sont affichés. Aucun nom n'est déduit de la cible globale du FITS.
        """
        identifiers = self.name
        if self.messier and self.name != f'M{self.messier.zfill(3)}':
            identifiers = f'M{self.messier} / {self.name}'
        common = self.common_name_fr or (self.common_names[0] if self.common_names else '')
        return f'{common}\n{identifiers}' if common else identifiers


class AnnotationCatalog:
    """Lit les positions publiques et sélectionne les centres projetés dans le champ."""

    @staticmethod
    def read(path: Path = CATALOG_PATH) -> list[SkyObject]:
        """Charge l'extrait CSV livré ; refuse des coordonnées ou magnitudes invalides."""
        objects = []
        with path.open(encoding='utf-8', newline='') as stream:
            for row in csv.DictReader(stream):
                ra, dec = float(row['ra_deg']), float(row['dec_deg'])
                magnitude = float(row['magnitude']) if row['magnitude'] else None
                if not (math.isfinite(ra) and 0 <= ra < 360
                        and math.isfinite(dec) and -90 <= dec <= 90):
                    raise ValueError(f"Coordonnées invalides : {row['name']}")
                if magnitude is not None and not math.isfinite(magnitude):
                    raise ValueError(f"Magnitude invalide : {row['name']}")
                dimensions = [float(row[key]) if row.get(key) else None for key in
                              ('major_arcmin', 'minor_arcmin', 'position_angle_deg')]
                if any(value is not None and (not math.isfinite(value) or value < 0)
                       for value in dimensions):
                    raise ValueError(f"Dimensions invalides : {row['name']}")
                objects.append(SkyObject(row['name'], ra, dec, magnitude, row['messier'],
                                         *dimensions,
                                         common_names=tuple(name.strip() for name in
                                             row.get('common_names', '').split(',') if name.strip()),
                                         common_name_fr=row.get('common_name_fr', '').strip()))
        return objects

    @staticmethod
    def project_outline(obj: SkyObject, wcs: WCS, height: int,
                        row_order: str) -> tuple[tuple[float, float], ...]:
        """Projette une ellipse céleste cataloguée en pixels écran.

        Les axes sont des diamètres en minutes d'arc, PA est mesuré du nord
        vers l'est. Le contour utilise 96 positions célestes, avec distorsion
        WCS et orientation écran. Une dimension/orientation manquante ne permet
        pas d'inventer une emprise : retourne alors un contour vide.
        """
        major, minor, angle = obj.major_arcmin, obj.minor_arcmin, obj.position_angle_deg
        if not major or not minor or (angle is None and major != minor):
            return ()
        theta = np.linspace(0, 2 * np.pi, 97)
        pa = np.deg2rad(angle or 0)
        north = major / 2 * np.cos(theta) * np.cos(pa) - minor / 2 * np.sin(theta) * np.sin(pa)
        east = major / 2 * np.cos(theta) * np.sin(pa) + minor / 2 * np.sin(theta) * np.cos(pa)
        center = SkyCoord(obj.ra, obj.dec, unit=u.deg, frame='icrs')
        sky = center.directional_offset_by(np.arctan2(east, north) * u.rad,
                                          np.hypot(east, north) * u.arcmin)
        x, y = skycoord_to_pixel(sky, wcs, origin=0, mode='all')
        if not (np.isfinite(x).all() and np.isfinite(y).all()):
            return ()
        if row_order == 'BOTTOM-UP':
            y = height - 1 - y
        return tuple(zip(map(float, x), map(float, y)))

    @classmethod
    def project(
        cls, header: fits.Header, limit: float,
    ) -> tuple[list[tuple[SkyObject, float, float]], JSONReport]:
        """Projette les objets brillants sur les pixels écran du FITS final.

        Le WCS céleste (distorsion SIP comprise) et son référentiel sont respectés.
        L'ordre BOTTOM-UP est inversé après projection. Un WCS absent ou invalide
        est audité ; aucun placement fondé sur le seul nom de cible n'est tenté.
        Les objets sans magnitude sont retenus uniquement s'ils ont un alias Messier.
        """
        audit: JSONReport = {
            'catalog': CATALOG_CREDIT, 'catalog_source': CATALOG_SOURCE,
            'catalog_license': 'https://creativecommons.org/licenses/by-sa/4.0/',
            'magnitude_limit': limit,
        }
        try:
            wcs = WCS(header, naxis=2).celestial
            if not wcs.has_celestial:
                return [], dict(audit, status='UNAVAILABLE', reason='WCS céleste absent')
            width, height = int(header['NAXIS1']), int(header['NAXIS2'])
            center = pixel_to_skycoord((width - 1) / 2, (height - 1) / 2, wcs)
            corners = pixel_to_skycoord(
                [0, width - 1, width - 1, 0], [0, 0, height - 1, height - 1], wcs,
            )
            radius = float(center.separation(corners).degree.max()) * 1.1 + 0.1
            if not math.isfinite(radius):
                raise ValueError('Empreinte céleste non finie')
        except (ValueError, KeyError) as error:
            return [], dict(audit, status='UNAVAILABLE', reason=str(error))
        objects = [obj for obj in cls.read() if obj.messier or (
            obj.magnitude is not None and obj.magnitude <= limit
        )]
        coordinates = SkyCoord([obj.ra for obj in objects], [obj.dec for obj in objects],
                               unit=u.deg, frame='icrs')
        nearby = center.separation(coordinates).degree <= radius
        selected = []
        failed = 0
        for obj, coord, inside in zip(objects, coordinates, nearby):
            if not inside:
                continue
            try:
                x, y = skycoord_to_pixel(coord, wcs, origin=0, mode='all')
            except NoConvergence:
                failed += 1
                continue
            x, y = float(x), float(y)
            if not (np.isfinite(x) and np.isfinite(y) and 0 <= x < width and 0 <= y < height):
                continue
            if header.get('ROWORDER', 'BOTTOM-UP') == 'BOTTOM-UP':
                y = height - 1 - y
            try:
                outline = cls.project_outline(obj, wcs, height,
                                              header.get('ROWORDER', 'BOTTOM-UP'))
            except NoConvergence:
                outline = ()
                failed += 1
            selected.append((replace(obj, outline=outline), x, y))
        selected.sort(key=lambda item: (
            not bool(item[0].messier),
            item[0].magnitude if item[0].magnitude is not None else math.inf,
            item[0].name,
        ))
        return selected, dict(audit, status='SUCCESS', in_field=len(selected),
                              projection_failures=failed)
