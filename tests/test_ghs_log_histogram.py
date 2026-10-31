"""Régressions robustes en semi-log, support incomplet et score secondaire borné."""

import numpy as np
import pytest

from lib.ghs_log_histogram import (
    HISTOGRAM_BINS, fit_log_tail, fit_log_tail_counts, log_tail_score,
)


def exponential_counts() -> np.ndarray:
    """Produit une densité dont la pente analytique vaut -3 dex par unité Y."""
    x = (np.arange(HISTOGRAM_BINS) + .5) / HISTOGRAM_BINS
    return np.rint(1e6 * 10 ** (-3 * x)).astype(int)


def test_exponential_density_has_known_slope_and_scale_invariance() -> None:
    """Une densité exponentielle donne sa pente exacte, indépendamment du support."""
    counts = exponential_counts()
    report = fit_log_tail_counts(counts)
    repeated = fit_log_tail_counts(counts * 10)
    assert report['slope'] == pytest.approx(-3, abs=1e-4)
    assert report['r_squared'] == pytest.approx(1, abs=1e-6)
    assert report['rms_dex'] < 1e-5
    assert report['window_end'] == .875
    assert report['fit_end'] < .875
    assert repeated['slope'] == pytest.approx(report['slope'])
    assert repeated['intercept'] == pytest.approx(report['intercept'])


def test_robust_regression_resists_a_secondary_bump() -> None:
    """Une bosse secondaire modérée pèse moins qu'en moindres carrés ordinaires."""
    counts = exponential_counts()
    counts[55:60] *= 3
    robust = fit_log_tail_counts(counts)
    x = (np.arange(HISTOGRAM_BINS) + .5) / HISTOGRAM_BINS
    selected = (x > robust['peak']) & (x <= robust['window_end'])
    ordinary = np.polyfit(x[selected], np.log10(counts[selected]), 1)[0]
    assert abs(robust['slope'] + 3) < abs(ordinary + 3) / 3


@pytest.mark.parametrize('light', [np.array([]), np.zeros(200), np.full(200, .2)])
def test_missing_or_narrow_support_has_no_invented_regression(light: np.ndarray) -> None:
    """Le logarithme d'un zéro et les distributions ponctuelles ne donnent pas de pente."""
    report = fit_log_tail(light)
    assert report['slope'] is None and report['r_squared'] is None
    assert log_tail_score(report, report) == 0


def test_padding_does_not_modify_log_density() -> None:
    """Ajouter des zéros de bordure ne change ni densité normalisée ni régression."""
    light = np.random.default_rng(42).exponential(.15, 50000)
    light = light[light <= 1]
    assert fit_log_tail(light) == fit_log_tail(np.pad(light, (0, 100000)))


def test_flatter_negative_slope_is_rewarded_without_an_absolute_target() -> None:
    """Moins négative signifie plus élevée ; le bonus reste borné malgré un gain extrême."""
    before = fit_log_tail_counts(exponential_counts())
    after = dict(before, slope=-1.5)
    assert log_tail_score(before, after) > 0
    assert log_tail_score(after, before) < 0
    assert log_tail_score(before, dict(before, slope=-1e-5, rms_dex=0)) <= 2.15
    assert log_tail_score(before, dict(before, slope=1)) == 0


@pytest.mark.parametrize('counts', [np.zeros(10), np.full(128, -1), np.full(128, np.nan)])
def test_invalid_counts_are_refused(counts: np.ndarray) -> None:
    """Les tableaux qui ne décrivent pas les classes attendues sont refusés."""
    with pytest.raises(ValueError, match='Effectifs invalides'):
        fit_log_tail_counts(counts)


def test_smoother_tail_outweighs_a_flatter_but_more_irregular_candidate() -> None:
    """Une réduction du RMS doit primer sur le seul aplatissement de la pente."""
    before = dict(fit_log_tail_counts(exponential_counts()), slope=-5, rms_dex=.2)
    smoother = dict(before, slope=-6, rms_dex=.15)
    flatter = dict(before, slope=-2.5, rms_dex=.24)
    assert log_tail_score(before, smoother) > 0 > log_tail_score(before, flatter)
    assert log_tail_score(before, smoother) > log_tail_score(before, flatter)
    assert log_tail_score(before, smoother, rms_weight=0) < log_tail_score(
        before, flatter, rms_weight=0,
    )


def test_rms_weight_controls_the_preference_without_singularities() -> None:
    """Le poids zéro désactive le RMS et une droite presque parfaite reste stable."""
    before = dict(fit_log_tail_counts(exponential_counts()), rms_dex=.2)
    after = dict(before, rms_dex=.15)
    assert log_tail_score(before, after, rms_weight=0) == 0
    assert log_tail_score(before, after, rms_weight=2) > log_tail_score(
        before, after, rms_weight=.5,
    ) > 0
    before['rms_dex'] = 1e-6
    after['rms_dex'] = 0
    assert 0 <= log_tail_score(before, after) < .001


@pytest.mark.parametrize('populated,expected', [(7, .75), (8, .875)])
def test_tail_extension_requires_populated_high_intensities(
    populated: int, expected: float,
) -> None:
    """Le dernier huitième n'est inclus que si huit classes sont réellement peuplées."""
    counts = exponential_counts()
    counts[96:] = 0
    counts[96:96 + populated] = 3
    report = fit_log_tail_counts(counts)
    assert report['window_end'] == expected
    forced = fit_log_tail_counts(exponential_counts(), window_end=.75)
    assert forced['window_end'] == .75 and forced['fit_end'] < .75


def test_different_tail_endpoints_are_not_compared() -> None:
    """Étendre la fenêtre ne doit pas créer artificiellement un gain de régularité."""
    before = fit_log_tail_counts(exponential_counts(), window_end=.75)
    after = fit_log_tail_counts(exponential_counts(), window_end=.875)
    assert log_tail_score(before, after) == 0


@pytest.mark.parametrize('weight', [-1, 10.1, float('nan')])
def test_invalid_rms_weight_is_refused(weight: float) -> None:
    """Les poids négatifs, excessifs et non finis sont refusés à la frontière du calcul."""
    metrics = fit_log_tail_counts(exponential_counts())
    with pytest.raises(ValueError, match='ghs-log-rms-weight'):
        log_tail_score(metrics, metrics, rms_weight=weight)
