"""Tests des critères de sélection, indépendants du mode Drizzle."""
import numpy as np
import pytest

from lib.drizzle import read_registration
from lib.quality_filter import quality_mask


def test_raw_fwhm_ceiling_rejects_blur_with_normal_star_count(caplog):
    # The two visibly blurred M33 exposures pass the other recorded thresholds.
    rows = [([7.17891, 15.259, .697373, 0, .0119828, 815], np.eye(3)),
            ([6.40424, 13.0833, .651945, 0, .0108559, 892], np.eye(3)),
            ([6, 14, .8, 0, .01, 900], np.eye(3))]
    cfg = {'fwhm_filter': '18.011', 'roundness_filter': '.575', 'nbstars_filter': '100'}
    assert quality_mask(rows, cfg).tolist() == [True, True, True]
    with caplog.at_level('INFO'):
        assert quality_mask(rows, dict(cfg, max_fwhm=6)).tolist() == [False, False, True]
    assert 'FWHM non pondérée : 2 image(s) retirée(s), 1/3 restante(s)' in caplog.text
    assert 'seuil <= 6.000 pixels' in caplog.text


def test_raw_and_weighted_fwhm_are_independent_and_cumulative():
    rows = [([raw, weighted, .9, 0, .01, 900], np.eye(3))
            for raw, weighted in [(7, 10), (5, 20), (4, 10)]]
    assert quality_mask(rows, {'max_fwhm': 6}).tolist() == [False, True, True]
    assert quality_mask(rows, {'fwhm_filter': '15'}).tolist() == [True, False, True]
    assert quality_mask(rows, {'max_fwhm': 6, 'fwhm_filter': '15'}).tolist() == [False, False, True]


@pytest.mark.parametrize('limit', [-1, float('nan'), float('inf')])
def test_raw_fwhm_ceiling_must_be_finite_and_nonnegative(limit):
    with pytest.raises(ValueError, match='Plafond FWHM invalide'):
        quality_mask([([4, 10, .9, 0, .01, 900], np.eye(3))], {'max_fwhm': limit})


@pytest.mark.parametrize('fwhm_filter,percent,expected', [
    ('5', 50, [True, True, False, False]),
    ('5', 0, [True, True, True, False]),
    ('none', 50, [True, True, False, False]),
    ('none', 0, [True, True, True, True]),
])
def test_fwhm_filters_are_independent_and_cumulative(fwhm_filter, percent, expected):
    rows = [([f, f, .9, 1, 0, 200], np.eye(3)) for f in [2, 3, 4, 20]]
    assert quality_mask(rows, {'fwhm_filter': fwhm_filter, 'fwhm_reject_percent': percent}).tolist() == expected


def test_registration_failures_are_removed_before_quality_thresholds():
    rows = [([2, 2, .9, 1, 0, n], np.eye(3)) for n in [1, 1, 199, 200, 201]]
    assert quality_mask(rows, {'nbstars_filter': '2'}, [False, False, True, True, True]).tolist() == [False, False, True, True, True]


def test_fwhm_percentage_uses_threshold_survivors_and_logs_each_rejection(caplog):
    rows = [([f, f, .9, 1, 0, 200], np.eye(3)) for f in [1, 2, 3, 4, 5, 20]]
    with caplog.at_level('INFO'):
        selected = quality_mask(rows, {'fwhm_filter': '4', 'fwhm_reject_percent': 25})
    assert selected.tolist() == [True, True, True, False, False, False]
    assert 'FWHM pondérée : 2 image(s) retirée(s), 4/6 restante(s)' in caplog.text
    assert 'FWHM proportionnelle : 1 image(s) retirée(s), 3/4 restante(s)' in caplog.text


def test_only_statistical_fwhm_filter_is_enabled_by_default():
    from lib.config import Config
    assert Config.DEFAULTS['fwhm_filter'] == '1.8k'
    assert Config.DEFAULTS['fwhm_reject_percent'] == 0
    assert Config.DEFAULTS['max_fwhm'] == 0


@pytest.mark.parametrize('percent', [-1, 96, float('nan'), float('inf')])
def test_fwhm_invalid_percentage_with_active_threshold(percent):
    with pytest.raises(ValueError, match='Pourcentage de rejet FWHM invalide'):
        quality_mask([([2, 2, .9, 1, 0, 200], np.eye(3))],
                     {'fwhm_filter': '1.8k', 'fwhm_reject_percent': percent})


def test_quality_logs_count_sequential_rejections(caplog):
    rows = [([1.7, w, r, 1, 0, n], np.eye(3)) for w, r, n in [
        (float('nan'), .9, 200), (5, .2, 20), (1.8, .2, 20),
        (1.8, .9, 70), (1.8, .9, 390), (1.8, .9, 200),
        (1.8, .9, 199), (1.8, .9, 201),
    ]]
    with caplog.at_level('INFO'):
        selected = quality_mask(rows, {'fwhm_filter': '2', 'roundness_filter': '.5', 'nbstars_filter': '5'})
    assert selected.tolist() == [False]*5 + [True]*3
    for criterion, removed, remaining, before in [
        ('mesures valides', 1, 7, 8), ('FWHM pondérée', 1, 6, 7),
        ('rondeur', 1, 5, 6), ('nombre d’étoiles', 2, 3, 5), ('bilan', 5, 3, 8),
    ]:
        assert f'{criterion} : {removed} image(s) retirée(s), {remaining}/{before} restante(s)' in caplog.text


def test_quality_logs_disabled_and_exhausted_filters(caplog):
    rows = [([1.7, 1.8, .9, 1, 0, 200], np.eye(3))]
    with caplog.at_level('INFO'):
        quality_mask(rows, {'fwhm_filter': '1', 'roundness_filter': '.5', 'nbstars_filter': 'none'})
    assert 'FWHM pondérée : 1 image(s) retirée(s), 0/1 restante(s)' in caplog.text
    assert 'rondeur : aucune image à évaluer, 0 image(s) retirée(s), 0/0 restante(s)' in caplog.text
    assert 'nombre d’étoiles : filtre désactivé, 0 image(s) retirée(s), 0/0 restante(s)' in caplog.text


def test_selection_rejects_bad_fwhm_and_roundness():
    rows=[([1.7, 1.8, .9, 1, 0, 30], np.eye(3)) for _ in range(10)]
    rows += [([4.,5.,.4,1,0,30],np.eye(3))]
    assert quality_mask(rows, {'fwhm_filter':'1.8k','roundness_filter':'1.8k'}).tolist() == [True]*10+[False]

@pytest.mark.parametrize('value', ['5', '20%', '1.8k'])
def test_nbstars_filters_reject_both_tails(value):
    rows=[([1.7,1.8,.9,1,0,count],np.eye(3)) for count in [2,28,29,30,31,32,60]]
    assert quality_mask(rows,{'nbstars_filter':value}).tolist() == [False,True,True,True,True,True,False]


def test_nbstars_default_rejects_cloud_and_doubled_counts(tmp_path):
    """Stable night with two exceptional counts; no pixel-level defect detection."""
    from lib.config import Config

    # Normal variation around 200 stars; anomalies occur within the sequence.
    counts = [198, 201, 199, 200, 202, 70, 198, 201, 200, 199,
              202, 200, 390, 198, 201, 199, 200, 202, 198, 201, 200, 199]
    names = [f'normal_{i:02d}' for i in range(len(counts))]
    names[5], names[12] = 'cloud_count', 'doubled_count'
    sequence = tmp_path / 'stable_night.seq'
    sequence.write_text(
        "S 'stable_night_' 1 22 22 3 0 6 0 0 0\n"
        + ''.join(f'I {i+1} 1\n' for i in range(len(counts)))
        + ''.join(f'R0 1.7 1.8 .9 1 0 {count} H 1 0 0 0 1 0 0 0 1\n'
                  for count in counts)
    )
    _, _, rows = read_registration(sequence)
    cfg = dict(Config.DEFAULTS)

    # All other quality measurements are identical and acceptable.
    without_star_filter = quality_mask(rows, {**cfg, 'nbstars_filter': 'none'})
    assert without_star_filter.all()
    selected = quality_mask(rows, cfg)
    rejected = [name for name, keep in zip(names, selected) if not keep]
    assert rejected == ['cloud_count', 'doubled_count']
    assert int(selected.sum()) == 20

    # Absolute and relative tolerances retain the same normal exposures.
    for tolerance in ('5', '5%'):
        assert quality_mask(rows, {**cfg, 'nbstars_filter': tolerance}).tolist() == selected.tolist()


@pytest.mark.parametrize('counts,value,expected', [
    ([30,30,30,29,60], '1.8k', [True,True,True,False,False]),
    ([20,25,30,35,40], '5', [False,True,True,True,False]),
    ([20,25,30,35,40], '0%', [False,False,True,False,False]),
    ([2,30,60], 'none', [True,True,True]),
    ([30], '1.8k', [True]),
])
def test_nbstars_boundaries_and_constant_samples(counts, value, expected):
    rows=[([1.7,1.8,.9,1,0,count],np.eye(3)) for count in counts]
    assert quality_mask(rows, {'nbstars_filter':value}).tolist() == expected


@pytest.mark.parametrize('value', ['-1', '-2k', 'nan', 'inf%', 'bad'])
def test_nbstars_invalid_tolerance(value):
    with pytest.raises(ValueError):
        quality_mask([([1.7,1.8,.9,1,0,30],np.eye(3))], {'nbstars_filter':value})
