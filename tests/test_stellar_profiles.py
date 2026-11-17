"""Profils étendus, protection des halos et recettes de gains par étoile."""

import numpy as np
import pytest

from lib.stellar_profiles import (
    StellarProfileBoost, crossing_radii, profile_gain_map, radial_samples,
)
from lib.star_rendering import ConservativeStarRenderer
from lib.stretch_transfer import CumulativeStretchTransform, stellar_operation


@pytest.fixture
def isolated_profiles() -> tuple[np.ndarray, dict[str, object]]:
    """Une étoile faible, une forte et une exclue, avec des supports séparés."""
    yy, xx = np.indices((161, 241))
    light = np.zeros(yy.shape, dtype=np.float32)
    for y, x, peak, sigma in ((80, 50, .004, 2), (80, 190, .5, 8),
                              (80, 110, .002, 2)):
        light += peak * np.exp(-((yy-y)**2 + (xx-x)**2) / (2*sigma**2))
    selection = dict(mode='spatial', shape=list(light.shape), selected_count=2,
                     selected=[[80, 50], [80, 190]], others=[[80, 110]])
    return light, selection


def test_constant_gain_and_large_halo_protection(
    isolated_profiles: tuple[np.ndarray, dict[str, object]],
) -> None:
    """Le supplément préserve le profil faible et laisse tout le halo fort intact."""
    base, selection = isolated_profiles
    original = base.copy()
    plan = StellarProfileBoost.build(base/5, base, selection, np.array([.08, .8]), 1.5)
    assert len(plan['profiles']) == 1
    gain = profile_gain_map(plan, base.shape)
    y, x, core, outer, multiplier = plan['profiles'][0]
    yy, xx = np.indices(base.shape)
    inside = (yy-y)**2 + (xx-x)**2 <= core**2
    np.testing.assert_allclose(gain[inside], multiplier, rtol=1e-7)
    np.testing.assert_array_equal(gain[:, 110:], 1.)
    assert gain[y, x] > 1
    assert gain[0, 0] == 1
    radii, before = radial_samples(base, y, x)
    _, after = radial_samples(base*gain, y, x)
    for level in (.5, .1):
        np.testing.assert_allclose(crossing_radii(radii, after, level),
                                   crossing_radii(radii, before, level), atol=.01)
    np.testing.assert_array_equal(base, original)
    assert 'profiles' not in selection
    # Le raccord est continu, monotone et ne produit pas d'anneau.
    ray = (base*gain)[y, x:x+int(np.ceil(outer))+2]
    assert (np.diff(ray) <= 1e-8).all()


def test_crowded_profiles_reduce_gain_and_truncated_profiles_keep_base() -> None:
    """Réduire progressivement les cas confondus ; ne pas inventer les ailes coupées."""
    yy, xx = np.indices((81, 81))
    light = (.01*np.exp(-((yy-40)**2+(xx-40)**2)/50)).astype('f4')
    plan = StellarProfileBoost.build(
        light, light, dict(selected=[[40, 40]], others=[[40, 46]], shape=[81, 81]),
        np.array([.08]), 1.5,
    )
    assert len(plan['profiles']) == 1
    check = plan['profile_checks'][0]
    assert check['reason'] == 'crowded profile with reduced gain'
    assert 1 < check['gain'] < check['requested_gain']
    assert check['gain_reductions'] > 0
    gain = profile_gain_map(plan, light.shape)
    radii, before = radial_samples(light, 40, 40)
    _, after = radial_samples(light*gain, 40, 40)
    for level in (.5, .1):
        ratio = crossing_radii(radii, after, level)/crossing_radii(radii, before, level)
        assert ratio.min() >= .9
    halo_ratio = crossing_radii(radii, after, .01)/crossing_radii(radii, before, .01)
    assert halo_ratio.min() >= .8
    assert gain[40, 46] == 1
    edge = StellarProfileBoost.build(
        light, light, dict(selected=[[0, 0]], others=[[40, 40]], shape=[81, 81]),
        np.array([.08]), 1.5,
    )
    assert not edge['profiles']
    assert crossing_radii(np.arange(5), np.ones((16, 5)), .1) is None


@pytest.mark.parametrize('entry', [
    [3, 3, 2, 1, 5], [3, 3, 1, 2, .5], [3, 3, 1, 2, float('inf')],
    [3.2, 3, 1, 2, 5], [-1, 3, 1, 2, 5], [3, 3, 1, 257, 5],
])
def test_invalid_profile_recipe(entry: list[float]) -> None:
    """Une recette incohérente n'est pas rejouée silencieusement."""
    with pytest.raises(ValueError, match='Profil'):
        profile_gain_map(dict(shape=[10, 10], profiles=[entry]), (10, 10))


def test_fractional_histogram_recipe_has_exact_nontrivial_replay() -> None:
    """La courbe quantile renforce les pics faibles et se rejoue pixel à pixel."""
    yy, xx = np.indices((201, 201))
    light = np.zeros(yy.shape, dtype='f4')
    for (y, x), peak in zip(
        ((y, x) for y in range(20, 190, 30) for x in range(20, 190, 30)),
        np.geomspace(.0001, .01, 36),
    ):
        light += peak * np.exp(-((yy-y)**2+(xx-x)**2)/2)
    data = np.array([light, .6*light, .3*light])
    expected, report = ConservativeStarRenderer.render(data)
    assert report['boost_selection']['mode'] == 'histogram_quantile'
    assert report['boost_selection']['actually_boosted_count'] > 0
    operation = stellar_operation(report, report['histogram_balance'])
    transform = CumulativeStretchTransform((0., float(data.max())), [operation])
    np.testing.assert_array_equal(transform.apply_rgb(data), expected)
    assert transform.scalar_role == 'neutral_rgb_restriction'
    assert np.isfinite(transform.evaluate(np.linspace(0, 1, 100))).all()
    np.testing.assert_array_equal(
        transform.apply_rgb(data[:, :50, :50]), expected[:, :50, :50],
    )


def test_bright_halo_with_neighbouring_peaks_stays_identical() -> None:
    """Des pics voisins ne découpent plus le halo d'une grosse étoile en pétales."""
    yy, xx = np.indices((401, 401))
    light = (.6*np.exp(-((yy-200)**2+(xx-200)**2)/(2*18**2))).astype('f4')
    for (y, x), peak in zip(
        ((y, x) for y in range(30, 390, 45) for x in range(30, 390, 45)),
        np.geomspace(.0001, .015, 64),
    ):
        light += peak*np.exp(-((yy-y)**2+(xx-x)**2)/2)
    source = np.array([light*.6, light*.8, light])
    base, _ = ConservativeStarRenderer.render(source, boost_fraction=0)
    rendered, report = ConservativeStarRenderer.render(source)
    assert report['boost_selection']['mode'] == 'histogram_quantile'
    protected = base.max(axis=0) >= .15
    np.testing.assert_array_equal(rendered[:, protected], base[:, protected])
    np.testing.assert_array_equal(rendered > 0, source > 0)
    base_sum = base.sum(axis=0)
    rendered_sum = rendered.sum(axis=0)
    active = (base_sum > 1e-7) & (rendered_sum > 1e-7)
    np.testing.assert_allclose(
        rendered[:, active] / rendered_sum[active],
        base[:, active] / base_sum[active], atol=2e-7,
    )


def test_extended_wings_reduce_gain_individually() -> None:
    """La recette historique à profils conserve au moins 90 % du halo."""
    yy, xx = np.indices((121, 121))
    light = (.004*(1+((yy-60)**2+(xx-60)**2)/4)**-2).astype('f4')
    selection = dict(shape=[121, 121], selected=[[60, 60]], others=[[60, 74]])
    plan = StellarProfileBoost.build(light, light, selection, np.array([.6]), 1.5)
    assert len(plan['profiles']) == 1
    check = plan['profile_checks'][0]
    assert check['halo_minimum_ratio'] >= .9
    assert check['gain_reductions'] > 0
    assert 1 < check['gain'] < check['requested_gain']
    gain = profile_gain_map(plan, light.shape)
    radii, before = radial_samples(light, 60, 60)
    _, after = radial_samples(light*gain, 60, 60)
    ratio = crossing_radii(radii, after, .01)/crossing_radii(radii, before, .01)
    assert ratio.min() >= .9


@pytest.mark.parametrize('case', ['empty', 'constant', 'single', 'border', 'noise'])
def test_generic_fields_have_finite_conservative_outputs(case: str) -> None:
    """Une même règle couvre couches vides, rares, tronquées et bruitées."""
    yy, xx = np.indices((81, 81))
    if case == 'empty':
        light = np.zeros(yy.shape, dtype='f4')
    elif case == 'constant':
        light = np.full(yy.shape, .001, dtype='f4')
    elif case == 'noise':
        generator = np.random.default_rng(827)
        light = np.abs(generator.normal(0, 1e-5, yy.shape)).astype('f4')
    else:
        y, x = (0, 0) if case == 'border' else (40, 40)
        light = (.02*np.exp(-((yy-y)**2+(xx-x)**2)/8)).astype('f4')
    source = np.array([light, .6*light, .3*light])
    before = source.copy()
    rendered, report = ConservativeStarRenderer.render(source)
    assert np.isfinite(rendered).all()
    assert 0 <= rendered.min() <= rendered.max() <= .85
    selection = report['boost_selection']
    assert selection['selected_count'] <= selection['budget']
    np.testing.assert_array_equal(source, before)
    np.testing.assert_allclose(rendered[1], .6*rendered[0], atol=1e-7)
