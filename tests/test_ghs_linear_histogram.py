"""Objectif de longueur semi-log et ruptures de pente pour les pivots GHS."""
import numpy as np
import pytest

from lib.ghs_linear_histogram import linear_histogram_profile, symmetry_points
from lib.ghs_log_histogram import HISTOGRAM_BINS


def line_counts() -> np.ndarray:
    """Produit une décroissance semi-log connue derrière un pic sombre."""
    x = (np.arange(HISTOGRAM_BINS)+.5)/HISTOGRAM_BINS
    counts = np.rint(1e6*10**(-3*x))
    counts[4] = 1e8
    return counts


def test_a_gap_is_not_bridged_and_endpoint_excludes_eighth_octile() -> None:
    """Les classes absentes cassent le segment ; les hautes lumières ne l'allongent pas."""
    counts = line_counts()
    counts[60:64] = 0
    report = linear_histogram_profile(counts)
    assert report['line_end'] <= 60/128 or report['line_start'] >= 64/128
    original = report.copy()
    counts[112:] = counts[111]
    after = linear_histogram_profile(counts)
    for key in ('line_start', 'line_end', 'line_span'):
        assert after[key] == original[key]
    assert after['score'] == pytest.approx(original['score'], abs=1e-9)


def test_local_bump_is_not_ignored_by_robust_weights() -> None:
    """Une bosse isolée importante est exclue, sans poids de Huber pour la cacher."""
    counts = line_counts()
    before = linear_histogram_profile(counts)
    counts[60] *= 30
    after = linear_histogram_profile(counts)
    assert after['line_span'] < before['line_span']
    assert not after['line_start'] <= 60/128 < after['line_end']


def test_central_change_of_slope_proposes_a_pivot_near_half() -> None:
    """La jonction plateau/décroissance près de 0,5 devient un SP candidat."""
    x = (np.arange(HISTOGRAM_BINS)+.5)/HISTOGRAM_BINS
    counts = np.rint(1e5*10**(-4*np.maximum(x-.5, 0)))
    counts[4] = 1e7
    points = symmetry_points(counts)
    assert 0 in points
    assert any(abs(point-.5) <= 1/128 for point in points)
    assert len(points) <= 4 and all(0 <= point <= .75 for point in points)


@pytest.mark.parametrize('counts', [np.zeros(128), np.ones(128), np.r_[np.ones(127), 100]])
def test_insufficient_support_has_no_invented_line(counts: np.ndarray) -> None:
    """Un support absent ou insuffisant ne produit ni pente ni qualité fictive."""
    report = linear_histogram_profile(counts)
    assert not report['measurable'] and report['slope'] is None and report['loss'] is None
    assert report['line_span'] == 0


@pytest.mark.parametrize('counts', [np.zeros(127), np.full(128, np.nan), np.full(128, -1.)])
def test_invalid_counts_are_rejected(counts: np.ndarray) -> None:
    """Les frontières de calcul refusent les effectifs incohérents."""
    with pytest.raises(ValueError, match='Effectifs'):
        linear_histogram_profile(counts)
    with pytest.raises(ValueError, match='Effectifs'):
        symmetry_points(counts)


def test_log_tail_height_reproduces_quarter_of_log_peak() -> None:
    """Deux graduations sur huit signifient log(10)/log(10000), pas 25 % des pixels."""
    from lib.ghs_linear_histogram import TAIL_HISTOGRAM_BINS, log_tail_height

    counts = np.zeros(TAIL_HISTOGRAM_BINS)
    counts[20] = 10000
    counts[447:449] = 10
    report = log_tail_height(counts)
    assert report['height'] == pytest.approx(.25)
    assert report['peak_count'] == 10000
    assert report['endpoint_count'] == 10
    assert report['measurable'] and not report['estimated']
    assert counts[20] == 10000


def test_sample_population_scaling_matches_full_histogram() -> None:
    """Un sous-échantillon proportionnel et le champ complet ont la même échelle log."""
    from lib.ghs_linear_histogram import TAIL_HISTOGRAM_BINS, log_tail_height

    counts = np.full(TAIL_HISTOGRAM_BINS, 4.)
    counts[15] = 100000
    counts[447:449] = 32
    sampled = log_tail_height(counts/4, population_scale=4)
    full = log_tail_height(counts)
    assert sampled['estimated'] and not full['estimated']
    for key in ('height', 'peak_count', 'endpoint_count'):
        assert sampled[key] == full[key]


def test_channels_have_independent_peak_normalizations() -> None:
    """Un canal fort ne sert pas de maximum à un autre canal."""
    from lib.ghs_linear_histogram import TAIL_HISTOGRAM_BINS, log_tail_height

    red = np.zeros(TAIL_HISTOGRAM_BINS)
    blue = np.zeros(TAIL_HISTOGRAM_BINS)
    red[20], red[447:449] = 10000, 10
    blue[20], blue[447:449] = 100, 10
    assert log_tail_height(red)['height'] == pytest.approx(.25)
    assert log_tail_height(blue)['height'] == pytest.approx(.5)


def test_tail_objective_rewards_target_without_overshoot_bonus() -> None:
    """La luminosité peut départager les courbes, sans récompenser un dépassement."""
    from lib.ghs_linear_histogram import add_tail_height_objective

    line = linear_histogram_profile(line_counts())
    original = line.copy()
    low = add_tail_height_objective(line, {'height': .1}, .25, 'red')
    target = add_tail_height_objective(line, {'height': .25}, .25, 'red')
    high = add_tail_height_objective(line, {'height': .6}, .25, 'red')
    disabled = add_tail_height_objective(line, {'height': .6}, 0, 'red')
    assert target['score'] > low['score']
    assert target['score'] == high['score']
    assert target['tail']['achieved'] and not low['tail']['achieved']
    assert disabled['score'] == line['score'] and disabled['tail']['achieved'] is None
    unknown = add_tail_height_objective(line, {'height': None}, .25, 'red')
    assert unknown['score'] == line['score'] and not unknown['tail']['achieved']
    slightly_shorter = dict(line, score=line['score']-.01, line_span=line['line_span']-.01)
    assert add_tail_height_objective(slightly_shorter, {'height': .25}, .25, 'red')['score'] > low['score']
    assert line == original
    shorter = dict(line, score=line['score']-.125, line_span=line['line_span']-.125)
    assert add_tail_height_objective(shorter, {'height': .25}, .25, 'red')['score'] < low['score']


@pytest.mark.parametrize('value', [0., 1.])
def test_empty_or_one_count_peak_does_not_define_a_log_scale(value: float) -> None:
    """Une échelle sans amplitude logarithmique ne fournit aucune hauteur fictive."""
    from lib.ghs_linear_histogram import TAIL_HISTOGRAM_BINS, log_tail_height

    report = log_tail_height(np.full(TAIL_HISTOGRAM_BINS, value))
    assert not report['measurable'] and report['height'] is None


@pytest.mark.parametrize('counts,scale', [(np.zeros(128), 1), (np.full(512, -1), 1),
                                          (np.full(512, np.nan), 1), (np.ones(512), 0),
                                          (np.ones(512), float('inf'))])
def test_invalid_tail_histograms_are_rejected(counts: np.ndarray, scale: float) -> None:
    """La mesure de niveau refuse forme, effectifs et population invalides."""
    from lib.ghs_linear_histogram import log_tail_height

    with pytest.raises(ValueError):
        log_tail_height(counts, scale)


@pytest.mark.parametrize('height', [-.1, 1.1, float('nan')])
def test_invalid_log_heights_cannot_enter_the_score(height: float) -> None:
    """Un niveau non fini ou hors graphique ne donne pas un bonus apparemment valide."""
    from lib.ghs_linear_histogram import add_tail_height_objective

    with pytest.raises(ValueError, match='Hauteur'):
        add_tail_height_objective(linear_histogram_profile(line_counts()), {'height': height}, .25, 'red')


def test_regression_floor_uses_display_bins_and_full_population() -> None:
    """Une droite d'effectif connu est convertie vers le graphique et le champ."""
    from lib.ghs_linear_histogram import add_regression_floor_objective

    counts = np.full(128, 320.)
    counts[4] = 100000
    line = linear_histogram_profile(counts)
    original = line.copy()
    full = add_regression_floor_objective(line, 4096)
    assert full['regression_floor']['predicted_log10_count'] == pytest.approx(1)
    assert full['regression_floor']['achieved']
    sampled = add_regression_floor_objective(linear_histogram_profile(counts / 4), 4096, 4)
    assert sampled['regression_floor']['predicted_log10_count'] == pytest.approx(1)
    assert sampled['regression_floor']['estimated']
    finer = add_regression_floor_objective(line, 8192)
    assert finer['regression_floor']['predicted_log10_count'] == pytest.approx(np.log10(5))
    assert not finer['regression_floor']['achieved']
    assert line == original


def test_regression_floor_prefers_target_and_guides_intermediate_passes() -> None:
    """La progression sous le seuil est possible, sans bonus après son atteinte."""
    from lib.ghs_linear_histogram import add_regression_floor_objective

    counts = np.full(128, 32.)
    counts[4] = 100000
    line = linear_histogram_profile(counts)
    low = add_regression_floor_objective(line, 4096)
    higher = add_regression_floor_objective(dict(line, intercept=line['intercept']+.5), 4096)
    target = add_regression_floor_objective(dict(line, intercept=line['intercept']+1.01), 4096)
    above = add_regression_floor_objective(dict(line, intercept=line['intercept']+2), 4096)
    assert low['score'] < higher['score'] < target['score']
    assert above['score'] == target['score'] == line['score']
    assert add_regression_floor_objective(line, 4096, minimum_count=0)['score'] == line['score']
    missing = add_regression_floor_objective(linear_histogram_profile(np.zeros(128)), 4096)
    assert missing['regression_floor']['predicted_log10_count'] is None
    assert not missing['regression_floor']['achieved'] and missing['loss'] is None


@pytest.mark.parametrize('bins,scale,minimum', [(0, 1, 10), (True, 1, 10),
                                               (128, 0, 10), (128, np.inf, 10),
                                               (128, 1, -1), (128, 1, np.nan)])
def test_regression_floor_rejects_invalid_units(bins: int, scale: float, minimum: float) -> None:
    """Les conversions invalides ne produisent aucun score exploitable."""
    from lib.ghs_linear_histogram import add_regression_floor_objective

    with pytest.raises(ValueError):
        add_regression_floor_objective(linear_histogram_profile(line_counts()), bins, scale, minimum)


def test_regression_floor_evaluates_falling_line_at_point_eight() -> None:
    """Le seuil utilise la pente à 0,8 même en dehors du segment mesuré."""
    from lib.ghs_linear_histogram import add_regression_floor_objective

    x = (np.arange(128) + .5) / 128
    counts = np.rint(3200 * 10 ** (-3*x))
    counts[4] = 100000
    counts[64:] = 0
    line = linear_histogram_profile(counts)
    result = add_regression_floor_objective(line, 4096)
    assert line['measurable'] and line['line_end'] <= .5
    segment = (x >= line['line_start']) & (x < line['line_end'])
    slope, intercept = np.polyfit(x[segment], np.log10(counts[segment]), 1)
    expected = intercept + slope*.8 + np.log10(128/4096)
    assert result['regression_floor']['predicted_log10_count'] == pytest.approx(expected)
    assert not result['regression_floor']['achieved']


def test_extrapolation_cannot_validate_the_reference_floor() -> None:
    """Une droite haute arrêtée à 0,5 ne prouve aucun niveau à 0,8."""
    from lib.ghs_linear_histogram import add_regression_floor_objective

    counts = line_counts()
    counts[64:] = 0
    report = add_regression_floor_objective(linear_histogram_profile(counts), 4096)
    floor = report['regression_floor']
    assert floor['predicted_log10_count'] > 1
    assert floor['extrapolated'] and not floor['reference_covered']
    assert not floor['achieved'] and report['score'] < 0
    covered = add_regression_floor_objective(linear_histogram_profile(np.full(128, 640.)), 4096)
    assert covered['regression_floor']['achieved']
    assert covered['score'] > report['score']


def test_reference_coverage_selects_a_supported_line_over_a_long_left_plateau() -> None:
    """La régression du seuil porte sur une droite qui contient réellement 0,8."""
    x = (np.arange(128)+.5)/128
    counts = np.zeros(128)
    left = (x >= .0625) & (x < .47)
    right = (x >= .6) & (x < .875)
    counts[left] = np.rint(1e5*10**(-2*x[left]))
    counts[right] = np.rint(1e6*10**(-4*x[right]))
    counts[4] = 1e9
    free = linear_histogram_profile(counts)
    supported = linear_histogram_profile(counts, reference_intensity=.8)
    assert free['line_end'] < .5
    assert supported['reference_covered']
    assert supported['line_start'] <= .8 <= supported['line_end']
    assert supported['line_span'] < free['line_span']


@pytest.mark.parametrize('reference', [-.1, .9, float('nan'), float('inf')])
def test_invalid_reference_intensity_is_rejected(reference: float) -> None:
    """La référence ne peut sortir de la fenêtre réellement analysée."""
    with pytest.raises(ValueError, match='référence'):
        linear_histogram_profile(line_counts(), reference_intensity=reference)
