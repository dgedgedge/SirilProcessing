"""Rendu global actuel et rejeu des anciennes recettes locales archivées."""
from pathlib import Path

import numpy as np
import pytest

from lib.star_rendering import ConservativeStarRenderer, profile_widths
from lib.stellar_profiles import StellarContrastBoost, apply_contrast_profiles
from lib.stretch_transfer import CumulativeStretchTransform, stellar_operation


@pytest.fixture
def contrast_field() -> np.ndarray:
    """Seize étoiles résolues, de faibles à brillantes, sur un fond non nul."""
    y, x = np.indices((161, 161))
    light = np.full(y.shape, 2e-5, dtype=np.float32)
    for (cy, cx), peak in zip(
        ((cy, cx) for cy in (20, 60, 100, 140) for cx in (20, 60, 100, 140)),
        np.geomspace(1e-4, .5, 16),
    ):
        light += peak*np.exp(-((y-cy)**2+(x-cx)**2)/2)
    return np.array([light, .7*light, .4*light], dtype=np.float32)


def test_default_is_a_global_histogram_transform(contrast_field: np.ndarray) -> None:
    """La courbe est unique, bornée et rejouable sans coordonnées stellaires."""
    original = contrast_field.copy()
    rendered, report = ConservativeStarRenderer.render(contrast_field)
    assert report['boost_selection']['mode'] == 'histogram_quantile'
    assert report['contrast_mode'] == 'pointwise'
    assert report['histogram_only'] and not report['spatially_varying']
    assert report['noise_suppression']['threshold'] == 0
    assert 'profiles' not in report['boost_selection']
    assert 0 <= rendered.min() <= rendered.max() <= .85+1e-7
    linked = rendered[0] < .849
    np.testing.assert_allclose(rendered[1][linked], .7*rendered[0][linked], atol=1e-7)
    np.testing.assert_allclose(rendered[2][linked], .4*rendered[0][linked], atol=1e-7)
    np.testing.assert_array_equal(contrast_field, original)


def test_contrast_boost_leaves_the_pedestal_and_outside_support_unchanged() -> None:
    """Le supplément porte sur le signal stellaire, sans relever le voile local."""
    y, x = np.indices((41, 41))
    floor = .01
    light = floor+.02*np.exp(-((y-20)**2+(x-20)**2)/2)
    source = np.array([light, .7*light, .4*light], dtype=np.float32)
    selection = dict(shape=[41, 41], profiles=[[20, 20, 4., 8., 5., floor]])
    output = apply_contrast_profiles(source, selection)
    assert output[0, 20, 20] == pytest.approx(floor+5*.02)
    np.testing.assert_array_equal(output[:, 0], source[:, 0])
    np.testing.assert_array_equal(output[:, 20, 0], source[:, 20, 0])
    np.testing.assert_allclose(output[1], .7*output[0], atol=1e-7)
    for level in (.5, .1):
        assert profile_widths(source, output, level)['median_ratio'] == pytest.approx(1, rel=.02)
    constant = np.full_like(source, floor)
    np.testing.assert_array_equal(apply_contrast_profiles(constant, selection), constant)
    np.testing.assert_array_equal(apply_contrast_profiles(source, selection, 0), source)


def test_overlapping_contrast_profiles_use_the_maximum_not_the_sum() -> None:
    """Deux supports superposés n'ajoutent pas deux fois le renforcement."""
    source = np.full((3, 21, 21), .02, dtype=np.float32)
    first = dict(shape=[21, 21], profiles=[[9, 10, 3., 6., 2., .01]])
    second = dict(shape=[21, 21], profiles=[[11, 10, 3., 6., 3., .01]])
    both = dict(shape=[21, 21], profiles=first['profiles']+second['profiles'])
    np.testing.assert_allclose(
        apply_contrast_profiles(source, both),
        np.maximum(apply_contrast_profiles(source, first), apply_contrast_profiles(source, second)),
        atol=1e-8,
    )


def test_global_recipe_replays_exactly_without_native_geometry(
    contrast_field: np.ndarray, tmp_path: Path,
) -> None:
    """Une recette globale conserve sa courbe après permutation, découpe ou déplacement."""
    output, report = ConservativeStarRenderer.render(contrast_field)
    operation = stellar_operation(report, report['histogram_balance'])
    assert 'profiles' not in operation['boost_selection']
    assert operation['noise_threshold'] == 0
    transform = CumulativeStretchTransform((0, 1), [operation])
    np.testing.assert_array_equal(transform.apply_rgb(contrast_field), output)
    assert transform.scalar_role == 'neutral_rgb_restriction'
    curve, _ = transform.export(tmp_path/'global')
    assert curve['scalar_role'] == transform.scalar_role
    np.testing.assert_array_equal(
        transform.apply_rgb(contrast_field[:, :80, :80]), output[:, :80, :80])
    shifted = np.roll(contrast_field, (13, 27), axis=(1, 2))
    np.testing.assert_array_equal(
        transform.apply_rgb(shifted), np.roll(output, (13, 27), axis=(1, 2)))


@pytest.mark.parametrize('entry', [
    [10, 10, 3, 6, 2], [10, 10, 3, 6, 2, np.nan],
    [10.5, 10, 3, 6, 2, 0], [10, 10, 6, 3, 2, 0],
    [10, 10, 3, 6, .5, 0], [10, 10, 3, 6, 2, -.1],
    [-1, 10, 3, 6, 2, 0],
])
def test_contrast_replay_rejects_invalid_profiles(entry: list[float]) -> None:
    """Une recette invalide ne devient pas une correction apparemment valide."""
    with pytest.raises(ValueError, match='Profil'):
        apply_contrast_profiles(np.ones((3, 21, 21), dtype='f4')*.01,
                                dict(shape=[21, 21], profiles=[entry]))


@pytest.mark.parametrize('strength', [-1, 2, np.nan])
def test_contrast_replay_rejects_invalid_strength(strength: float) -> None:
    """La force doit être finie et comprise entre zéro et un."""
    with pytest.raises(ValueError):
        apply_contrast_profiles(np.ones((3, 21, 21))*.01,
                                dict(shape=[21, 21], profiles=[]), strength)


def test_profile_guard_also_measures_faint_stars() -> None:
    """Les petites étoiles sous l'ancien seuil 0,005 participent aux gardes."""
    y, x = np.indices((41, 41))
    star = 1e-4*np.exp(-((y-20)**2+(x-20)**2)/4)
    source = np.repeat(star[None], 3, axis=0)
    widened = 1e-4*np.exp(-((y-20)**2+(x-20)**2)/8)
    result = profile_widths(source, np.repeat(widened[None], 3, axis=0))
    assert result['count'] == 1
    assert result['median_ratio'] > 1.3


def test_profile_builder_does_not_boost_a_nearby_bright_core() -> None:
    """Un voisin brillant rend le renforcement local ambigu et reste protégé."""
    y, x = np.indices((41, 41))
    light = (.001*np.exp(-((y-20)**2+(x-20)**2)/2)
             + .4*np.exp(-((y-20)**2+(x-27)**2)/2))
    plan = StellarContrastBoost.build(light, light, (np.array([20]), np.array([20])),
                                     1e-5, .25, 1.15, .85)
    assert not plan['profiles']
    assert all(not check['accepted'] for check in plan['profile_checks'])


def test_unknown_contrast_mode_fails() -> None:
    """Une préférence inconnue n'est pas interprétée comme un mode historique."""
    with pytest.raises(ValueError, match='Mode de contraste'):
        ConservativeStarRenderer.render(np.zeros((3, 21, 21)), contrast_mode='unknown')


def test_contrast_mode_obeys_cli_json_and_instance_snapshots(tmp_path: Path) -> None:
    """Le nouveau mode suit la priorité CLI > JSON > défaut sans état partagé."""
    import argparse
    import json
    from lib.config import Config

    path = tmp_path/'parameters.json'
    path.write_text(json.dumps(dict(star_contrast_mode='pointwise')))
    config = Config(path)
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(ConservativeStarRenderer, parser)
    args = config.parse_args(parser, [])
    config.bind_parameters(args)
    first = ConservativeStarRenderer()
    assert first._get_args().star_contrast_mode == 'pointwise'
    args = config.parse_args(parser, ['--star-contrast-mode', 'profiles', '-S'])
    config.bind_parameters(args)
    assert config.save_requested(args)
    assert json.loads(path.read_text())['star_contrast_mode'] == 'profiles'
    assert first._get_args().star_contrast_mode == 'pointwise'
    assert ConservativeStarRenderer()._get_args().star_contrast_mode == 'profiles'


@pytest.mark.parametrize('mode', ['pointwise', 'profiles'])
def test_main_star_branch_is_always_global(
    contrast_field: np.ndarray, tmp_path: Path, mode: str,
) -> None:
    """La branche AutoGHS neutralise aussi les anciennes options locales sauvegardées."""
    import argparse
    from astropy.io import fits
    from lib.stretch_branches import StarStretchProcessor

    source = tmp_path/'linear.fits'
    fits.writeto(source, contrast_field)
    report = StarStretchProcessor().post_process(
        source, tmp_path/'stars'/'report.json',
        argparse.Namespace(compute_backend='cpu', star_contrast_mode=mode, star_noise_sigma=8),
    )
    assert report['boost_selection']['mode'] == 'histogram_quantile'
    assert report['histogram_only'] and not report['spatially_varying']
    assert report['noise_suppression']['threshold'] == 0
    expected, _ = ConservativeStarRenderer.render(contrast_field, contrast_mode='pointwise')
    np.testing.assert_array_equal(fits.getdata(report['output_image']), expected)


def test_equal_rgb_pixels_are_equal_everywhere(contrast_field: np.ndarray) -> None:
    """La proximité d'une étoile ou le fond local ne modifie pas la transformation."""
    data = contrast_field.copy()
    positions = [(0, 0), (20, 20), (20, 21), (60, 60), (160, 160)]
    for y, x in positions:
        data[:, y, x] = [.001, .0007, .0004]
    actual, _ = ConservativeStarRenderer.render(data)
    for y, x in positions[1:]:
        np.testing.assert_array_equal(actual[:, y, x], actual[:, 0, 0])


def test_legacy_options_cannot_restore_local_processing(contrast_field: np.ndarray) -> None:
    """Les anciens paramètres sauvegardés restent inoffensifs pour le rendu global."""
    actual, report = ConservativeStarRenderer.render(
        contrast_field, contrast_mode='profiles', noise_sigma=8)
    expected, _ = ConservativeStarRenderer.render(
        contrast_field, contrast_mode='pointwise', noise_sigma=0)
    np.testing.assert_array_equal(actual, expected)
    assert report['contrast_mode'] == 'pointwise'
    assert not report['spatially_varying']
