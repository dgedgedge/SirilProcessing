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
    light = (.02+.001*x/160+.035*np.exp(-((x-80)**2+(y-80)**2)/600)
             + np.random.default_rng(73).normal(0, .001, x.shape))
    return np.repeat(light[None], 3, axis=0).astype('f4')


def test_custom_weak_threshold_is_shared_by_map_and_quality() -> None:
    """Le seuil personnalisé pilote les classes, leur légende et les mesures publiées."""
    source = scene()
    _, spatial, report = StarlessZoneMap.classify(source, weak_sigma=1.8)
    light = luminance(source, 'human')
    audit, masks = WeakStructureQuality.classify_grid(light, weak_sigma=1.8)
    np.testing.assert_array_equal(spatial == 5, masks['faint'])
    unclassified = ~masks['sky'] & ~masks['objects']
    np.testing.assert_array_equal(spatial == 7, unclassified & masks['subthreshold'])
    np.testing.assert_array_equal(spatial == 8, unclassified & masks['above_threshold'])
    assert report['weak_structure']['sigma_thresholds']['weak'] == 1.8
    assert report['weak_structure']['faint_count'] == audit['faint_count']
    assert '1,8–3' in report['legend']['spatial']['5']['label']
    assert '1 < S < 1,8' in report['legend']['spatial']['7']['label']
    assert '1,8 < S' in report['legend']['spatial']['8']['label']
    levels = []
    for identifier in (2, 7):
        color = report['legend']['spatial'][str(identifier)]['color'].lstrip('#')
        red, green, blue = (int(color[i:i+2], 16) for i in (0, 2, 4))
        assert red == green == blue
        levels.append(red)
    assert levels[0] < levels[1]
    assert report['legend']['spatial']['8']['color'] == '#fff29a'


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
    unclassified = ~masks['sky'] & ~masks['objects']
    np.testing.assert_array_equal(spatial == 7, unclassified & masks['subthreshold'])
    np.testing.assert_array_equal(spatial == 8, unclassified & masks['above_threshold'])
    np.testing.assert_array_equal(spatial == 2, unclassified & masks['below_one'])
    np.testing.assert_array_equal(spatial == 5, masks['faint'])
    np.testing.assert_array_equal(np.isin(spatial, [3, 4, 5]), masks['objects'])
    np.testing.assert_array_equal(spatial == 4, masks['seeds'])
    assert np.all(light[masks['seeds']] > report['weak_structure']['seed_luminance_threshold'])
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


def test_fallback_green_map_shows_the_actual_lowest_centile() -> None:
    """Sans bande faible à seuil 3, le vert et sa légende exposent le repli réellement mesuré."""
    source = scene()
    _, spatial, report = StarlessZoneMap.classify(source, weak_sigma=3.)
    light = luminance(source, 'human')
    audit, masks = WeakStructureQuality.classify_grid(light, weak_sigma=3.)
    assert audit['available']
    assert audit['faint_selection']['mode'] == 'lowest_percentile'
    assert not masks['detected_faint'].any()
    np.testing.assert_array_equal(spatial == 5, masks['faint'])
    assert 'centile inférieur' in report['legend']['spatial']['5']['label']


def test_png_and_report_are_written_without_changing_sources(tmp_path: Path) -> None:
    """Un contexte différent ne change aucun masque ni pixel scientifique."""
    source = scene()
    rendered = np.sqrt(source)
    original = source.copy()
    before = rendered.copy()
    report = StarlessZoneMap.write(source, tmp_path / 'zones.png', rendered=rendered,
                                  weak_sigma=1.8)
    assert report['scientific_output'] is False
    assert report['weak_structure']['sigma_thresholds']['weak'] == 1.8
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
