"""Cartes des populations GHS : masques exacts, recouvrements et sources préservées."""
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from lib.auto_ghs import luminance
from lib.ghs_final_stretch import ProtectedGHSFinalizer
from lib.ghs_weak_structure import WeakStructureQuality
from lib.starless_zones import StarlessZoneMap


def scene() -> np.ndarray:
    """Fournit une structure étendue faible sur fond bruité connu."""
    y, x = np.indices((160, 160))
    light = (.02+.001*x/160+.035*np.exp(-((x-80)**2+(y-80)**2)/250)
             + np.random.default_rng(73).normal(0, .001, x.shape))
    return np.repeat(light[None], 3, axis=0).astype('f4')


def test_map_uses_the_actual_quality_masks_and_thresholds() -> None:
    """Les classes colorées sont reconstructibles en masques de mesure sans approximation."""
    source = scene()
    original = source.copy()
    global_map, spatial, report = StarlessZoneMap.classify(source)
    light = luminance(source, 'human')
    measured = WeakStructureQuality.measure(light, light)
    _, masks = WeakStructureQuality.classify_grid(light)
    assert report['thresholds'] == ProtectedGHSFinalizer.thresholds(source, 'human')
    assert report['weak_structure']['reference_sha256'] == measured['reference_sha256']
    np.testing.assert_array_equal(spatial == 1, masks['sky'])
    np.testing.assert_array_equal(spatial == 5, masks['faint'])
    np.testing.assert_array_equal(np.isin(spatial, [3, 5]), masks['objects'])
    thresholds = report['thresholds']
    np.testing.assert_array_equal(np.isin(global_map, [1, 6]),
                                  (light > 1e-6) & (light <= thresholds['sky']))
    np.testing.assert_array_equal(np.isin(global_map, [3, 6]),
                                  (light >= thresholds['diffuse_low']) &
                                  (light <= thresholds['diffuse_high']))
    np.testing.assert_array_equal(source, original)


def test_uniform_reference_keeps_overlap_and_unavailable_detection() -> None:
    """Des quantiles confondus ne masquent pas le recouvrement fond/diffus."""
    global_map, spatial, report = StarlessZoneMap.classify(np.full((3, 64, 64), .1, dtype='f4'))
    assert np.all(global_map == 6)
    assert np.all(spatial == 2)
    assert not report['weak_structure']['available']


def test_png_and_report_are_written_without_changing_sources(tmp_path: Path) -> None:
    """Un contexte différent ne change aucun masque ni pixel scientifique."""
    source = scene()
    rendered = np.sqrt(source)
    original = source.copy()
    before = rendered.copy()
    report = StarlessZoneMap.write(source, tmp_path / 'zones.png', rendered=rendered)
    assert report['scientific_output'] is False
    with Image.open(report['output_png']) as image:
        assert image.format == 'PNG' and image.width > image.height
    json.dumps(report, allow_nan=False)
    np.testing.assert_array_equal(source, original)
    np.testing.assert_array_equal(rendered, before)


def test_large_reference_uses_the_same_regular_stride_as_quality() -> None:
    """L'échantillonnage garde les coordonnées, sans rééchantillonner les structures."""
    source = np.tile(scene(), (1, 3, 3))
    _, spatial, report = StarlessZoneMap.classify(source)
    light = luminance(source, 'human')
    quality = WeakStructureQuality.measure(light, light)
    assert report['stride'] == quality['stride'] == 3
    assert report['weak_structure']['reference_sha256'] == quality['reference_sha256']
    assert list(spatial.shape) == quality['grid_shape']


def test_invalid_display_geometry_fails(tmp_path: Path) -> None:
    """Une superposition sur un autre cadrage est refusée au lieu d'être trompeuse."""
    with pytest.raises(ValueError, match='géométrie'):
        StarlessZoneMap.write(scene(), tmp_path / 'zones.png', rendered=np.zeros((3, 32, 32)))
