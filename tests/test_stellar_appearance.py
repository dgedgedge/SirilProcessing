"""Visibilité non bloquante et mesures de diamètre, transition et jupe."""
import numpy as np
import pytest

from lib.stellar_appearance import StellarAppearanceQuality as Quality


def rgb(light: np.ndarray) -> np.ndarray:
    """Crée un RGB neutre normalisé pour isoler la mesure radiale."""
    return np.repeat(light[None], 3, axis=0).astype('float32')


@pytest.mark.parametrize('count,total,status', [(1, 4, 'FAIL'), (2, 10, 'FAIL'), (3, 10, 'FAIL'), (4, 10, 'PASS')])
def test_visibility_target_is_forty_percent(count: int, total: int, status: str) -> None:
    """40 % suffit exactement ; 30 % ne suffit plus, sans changement des pixels."""
    y, x = np.indices((401, 401))
    before = np.zeros(y.shape)
    after = np.zeros(y.shape)
    positions = [(cy, cx) for cy in (90, 250) for cx in (60, 130, 200, 270, 340)][:total]
    for index, (cy, cx) in enumerate(positions):
        amplitude = .04 if index < count else .01
        star = np.exp(-((y-cy)**2+(x-cx)**2)/8)
        before += .001*star
        after += amplitude*star
    first, last = rgb(before), rgb(after)
    saved = last.copy()
    report = Quality.evaluate(first, last, tuple(np.array(positions).T))
    visible = report['visibility']
    assert visible['status'] == status and not visible['blocking']
    assert visible['visible_count'] == count
    assert visible['fraction_lower_bound'] == visible['fraction_upper_bound'] == count/total
    np.testing.assert_array_equal(last, saved)
    assert report['shape']['status'] == 'UNKNOWN'
    assert report['shape']['reason'] == 'NO_CALIBRATED_SHAPE_TARGET'


def test_gaussian_crossings_match_analytic_radii() -> None:
    """Les rayons gaussiens et leur gradient retrouvent les valeurs analytiques."""
    y, x = np.indices((129, 129))
    sigma, amplitude = 5., .4
    source = rgb(amplitude*np.exp(-((y-64)**2+(x-64)**2)/(2*sigma**2)))
    report = Quality.evaluate(source, source, (np.array([64]), np.array([64])))
    metrics = report['shape']['metrics']
    width = sigma*(np.sqrt(2*np.log(5))-np.sqrt(2*np.log(1.25)))
    assert metrics['transition_width_px']['median'] == pytest.approx(width, rel=.03)
    assert metrics['edge_gradient_per_px']['median'] == pytest.approx(.6*amplitude/width, rel=.03)
    assert metrics['visible_diameter_px']['median'] == pytest.approx(
        2*sigma*np.sqrt(2*np.log(amplitude/.03)), rel=.02)
    assert metrics['skirt_radius_ratio']['median'] == pytest.approx(
        np.sqrt(np.log(20)/np.log(2)), rel=.03)


def test_extended_skirt_is_distinguished() -> None:
    """Une aile Moffat étendue donne un rapport de jupe supérieur au gaussien."""
    y, x = np.indices((129, 129))
    radius2 = (y-64)**2+(x-64)**2
    before = rgb(.4*np.exp(-radius2/18))
    after = rgb(.4*(1+radius2/9)**-2.)
    report = Quality.evaluate(before, after, (np.array([64]), np.array([64])))
    skirt = report['shape']['metrics']['skirt_radius_ratio']
    assert skirt['after_paired_median'] > skirt['before_paired_median']
    assert skirt['after_paired_median'] > 2.5


def test_broader_star_has_wider_transition_and_lower_relative_gradient() -> None:
    """La mesure distingue un contour diffus d'un contour plus abrupt."""
    y, x = np.indices((129, 129))
    r2 = (y-64)**2+(x-64)**2
    report = Quality.evaluate(rgb(.4*np.exp(-r2/8)), rgb(.4*np.exp(-r2/32)),
                              (np.array([64]), np.array([64])))
    metrics = report['shape']['metrics']
    assert metrics['transition_width_px']['after_paired_median'] > metrics['transition_width_px']['before_paired_median']
    assert metrics['relative_edge_gradient_per_px']['after_paired_median'] < metrics['relative_edge_gradient_per_px']['before_paired_median']


def test_borders_and_empty_fields_are_unknown_not_zero() -> None:
    """Les inconnus ne deviennent pas des étoiles invisibles certifiées."""
    source = np.zeros((3, 65, 65), dtype='float32')
    empty = Quality.evaluate(source, source, (np.array([], dtype=int), np.array([], dtype=int)))
    assert empty['visibility']['fraction_lower_bound'] is None
    assert empty['visibility']['status'] == 'UNKNOWN'
    border = Quality.evaluate(source, source, (np.array([0]), np.array([0])))
    assert border['visibility']['unknown_count'] == 1
    assert border['visibility']['status'] == 'UNKNOWN'
    assert border['visibility']['fraction_upper_bound'] == 1
    assert border['shape']['metrics']['visible_diameter_px']['median'] is None
    invisible = Quality.evaluate(source, source, (np.array([32]), np.array([32])))
    assert invisible['visibility']['status'] == 'FAIL'
    assert not invisible['visibility']['blocking']


def test_invalid_input_is_explicit() -> None:
    """Les erreurs de domaine et de géométrie ne produisent pas de verdict trompeur."""
    source = np.zeros((3, 65, 65), dtype='float32')
    with pytest.raises(ValueError):
        Quality.evaluate(source, source[:, :20], (np.array([32]), np.array([32])))
    with pytest.raises(ValueError):
        Quality.evaluate(source, source, (np.array([100]), np.array([32])))


def test_visibility_uses_luminance_not_brightest_channel() -> None:
    """Un pic bleu ne vaut pas un pic vert de même maximum RGB pour ce proxy."""
    y, x = np.indices((129, 129))
    star = .1*np.exp(-((y-64)**2+(x-64)**2)/18)
    green = np.zeros((3, 129, 129), dtype='float32')
    green[1] = star
    blue = np.zeros_like(green)
    blue[2] = star
    positions = (np.array([64]), np.array([64]))
    assert Quality.evaluate(green, green, positions)['visibility']['visible_count'] == 1
    assert Quality.evaluate(blue, blue, positions)['visibility']['visible_count'] == 0
