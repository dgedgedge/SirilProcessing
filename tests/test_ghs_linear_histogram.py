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
