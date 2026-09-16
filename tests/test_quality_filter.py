"""Tests des critères de sélection, indépendants du mode Drizzle."""
import numpy as np
import pytest

from lib.drizzle import read_registration
from lib.quality_filter import quality_mask


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


