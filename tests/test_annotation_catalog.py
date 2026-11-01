"""Vérifie les positions WCS et les identifications du catalogue local OpenNGC."""

from pathlib import Path

from astropy.io import fits
from astropy.wcs import WCS
import numpy as np
from PIL import Image
import pytest

from lib.annotation_catalog import AnnotationCatalog, SkyObject
from lib.final_annotation import FinalImageAnnotator


def celestial_header(row_order: str = 'TOP-DOWN') -> fits.Header:
    """Construit un champ TAN avec rotation, centré sur l'origine des ascensions."""
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [251, 201]
    wcs.wcs.crval = [359.9, 30]
    wcs.wcs.cd = [[-0.01, 0.003], [0.003, 0.01]]
    wcs.wcs.ctype = ['RA---TAN', 'DEC--TAN']
    header = wcs.to_header()
    header['NAXIS1'], header['NAXIS2'] = 500, 400
    header['ROWORDER'] = row_order
    return header


@pytest.mark.parametrize('row_order', ['TOP-DOWN', 'BOTTOM-UP'])
def test_projection_rotation_wrap_and_orientation(
    row_order: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Les positions respectent la rotation, le passage RA=0 et l'ordre des lignes."""
    header = celestial_header(row_order)
    wcs = WCS(header)
    ra, dec = wcs.all_pix2world([100, 350, 800], [70, 300, 100], 0)
    objects = [SkyObject(f'Test{index}', float(r), float(d), mag, '')
               for index, (r, d, mag) in enumerate(zip(ra, dec, [10, 20, 10]))]
    # Le Messier sans magnitude reste éligible, contrairement à l'objet faible.
    objects.append(SkyObject('M031', float(ra[1]), float(dec[1]), None, '31'))
    monkeypatch.setattr(AnnotationCatalog, 'read', staticmethod(lambda: objects))
    selected, audit = AnnotationCatalog.project(header, 14)
    assert audit['status'] == 'SUCCESS'
    assert len(selected) == 2
    assert selected[0][0].name == 'M031'
    actual = {obj.name: (x, y) for obj, x, y in selected}
    assert np.allclose(actual['Test0'], (100, 70 if row_order == 'TOP-DOWN' else 329))
    assert np.allclose(actual['M031'], (350, 300 if row_order == 'TOP-DOWN' else 99))


def test_bundled_catalog_identifications() -> None:
    """L'extrait conserve les coordonnées et les trois identifiants Messier de M31."""
    objects = {obj.name: obj for obj in AnnotationCatalog.read()}
    assert len(objects) > 12000
    assert objects['NGC0224'].label == 'Galaxie d’Andromède\nM31 / NGC0224'
    assert objects['NGC0598'].label == 'Galaxie du Triangle\nM33 / NGC0598'
    assert objects['NGC1976'].label == 'Nébuleuse d’Orion\nM42 / NGC1976'
    assert objects['NGC0221'].label == 'M32 / NGC0221'
    assert objects['NGC0205'].label == 'M110 / NGC0205'
    assert objects['NGC0224'].ra == pytest.approx(10.68479167, abs=1e-7)
    assert objects['NGC0224'].dec == pytest.approx(41.26905556, abs=1e-7)
    assert any(obj.messier == '45' for obj in objects.values())


def test_common_name_fallback_keeps_catalog_identifiers() -> None:
    """Sans traduction, le nom original reste disponible ; sans nom, garder NGC/M."""
    obj = SkyObject('NGC1234', 10, 20, 10, '', common_names=('Example Nebula', 'Alias'))
    assert obj.label == 'Example Nebula\nNGC1234'
    assert SkyObject('NGC1234', 10, 20, 10, '').label == 'NGC1234'


def test_no_wcs_does_not_invent_positions() -> None:
    """Sans solution astrométrique, l'audit explique l'absence de noms."""
    selected, audit = AnnotationCatalog.project(fits.Header(), 14)
    assert selected == []
    assert audit['status'] == 'UNAVAILABLE'
    assert 'WCS' in audit['reason']


def test_label_layout_avoids_panel_and_other_labels() -> None:
    """Le placement reste dans l'image et omet les objets sans place disponible."""
    objects = [(SkyObject(f'NGC{n}', 10, 40, 10, ''), 230.0, 150.0 + n)
               for n in range(12)]
    panel = (10, 240, 180, 390)
    with Image.new('RGB', (500, 400)) as image:
        audit = FinalImageAnnotator.draw_objects(image, objects, panel, 3)
    assert 0 < len(audit['labels']) <= 3
    assert len(audit['labels']) + len(audit['omitted']) == len(objects)
    occupied = [panel]
    for label in audit['labels']:
        left, top, right, bottom = label['label_bounds']
        assert 0 <= left < right < 500 and 0 <= top < bottom < 400
        for x0, y0, x1, y1 in occupied:
            assert right < x0 or left > x1 or bottom < y0 or top > y1
        occupied.append(label['label_bounds'])


def test_corrupt_catalog_is_rejected(tmp_path: Path) -> None:
    """Une coordonnée hors domaine n'est jamais projetée."""
    path = tmp_path / 'bad.csv'
    path.write_text('name,ra_deg,dec_deg,magnitude,messier\nbad,400,0,10,\n')
    with pytest.raises(ValueError, match='Coordonnées'):
        AnnotationCatalog.read(path)


@pytest.mark.parametrize('order', ['TOP-DOWN', 'BOTTOM-UP'])
def test_catalog_extent_uses_diameters_rotation_and_screen_orientation(order: str) -> None:
    """Les extrémités projetées conservent les demi-axes célestes et le PA."""
    from astropy.coordinates import SkyCoord
    from astropy.wcs.utils import pixel_to_skycoord
    import astropy.units as u

    header = celestial_header(order)
    wcs = WCS(header)
    obj = SkyObject('Extent', 359.9, 30, 10, '', 60, 30, 37)
    outline = AnnotationCatalog.project_outline(obj, wcs, 400, order)
    assert len(outline) == 97
    for index, radius, angle in ((0, 30, 37), (24, 15, 127)):
        x, y = outline[index]
        if order == 'BOTTOM-UP':
            y = 399 - y
        point = pixel_to_skycoord(x, y, wcs)
        center = SkyCoord(obj.ra, obj.dec, unit=u.deg)
        assert center.separation(point).arcmin == pytest.approx(radius, abs=1e-7)
        assert center.position_angle(point).degree == pytest.approx(angle, abs=1e-7)
    assert AnnotationCatalog.project_outline(SkyObject('Unknown', 0, 0, 10, ''),
                                              wcs, 400, order) == ()


def test_catalog_contains_actual_object_sizes() -> None:
    """M31 et M32 doivent avoir des emprises différentes, sans dépendre de la police."""
    objects = {obj.name: obj for obj in AnnotationCatalog.read()}
    assert objects['NGC0224'].major_arcmin > 100
    assert objects['NGC0221'].major_arcmin < 15
    assert objects['NGC0224'].position_angle_deg is not None
