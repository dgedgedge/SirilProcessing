"""Mesures fixes, préférence de ciel sobre et repli sans segmentation fiable."""
from copy import deepcopy

import numpy as np
import pytest

from lib.ghs_weak_structure import WeakStructureQuality as Weak
from lib.ghs_final_stretch import ProtectedGHSFinalizer
from lib.ghs_quartiles import QuartileGHSProcessor
from lib.type_defs import JSONReport


def scene() -> np.ndarray:
    """Construit une émission étendue faible sur ciel bruité avec un gradient doux."""
    y, x = np.indices((160, 160))
    noise = np.random.default_rng(73).normal(0, .001, x.shape)
    return (.02+.001*x/160+.035*np.exp(-((x-80)**2+(y-80)**2)/250)+noise)


def measurements(sky: float, faint: float, contrast: float, cnr: float) -> JSONReport:
    """Fabrique une mesure appariée dont la provenance est explicite et fixe."""
    return dict(weak_structure=dict(
        available=True, reference_sha256='fixed', grid_shape=[100, 100],
        object_count=200, faint_count=50, sky_count=2000, faint_reference_range=[.1, .2],
        candidate=dict(sky_upper_median=sky, sky_median=sky, faint_median=faint,
                       contrast=contrast, cnr=cnr)))


def test_fixed_masks_are_independent_of_candidate_and_source_unchanged() -> None:
    """Deux rendus ont exactement les mêmes populations de mesure et signature."""
    source = scene()
    saved = source.copy()
    first = Weak.measure(source, source)
    after = Weak.measure(source, np.sqrt(source))
    assert first['available'] and after['available']
    for key in ('reference_sha256', 'faint_count', 'object_count', 'sky_count',
                'faint_reference_range'):
        assert first[key] == after[key]
    assert first['reference'] == after['reference']
    np.testing.assert_array_equal(source, saved)


def test_lower_sky_wins_at_preserved_visibility() -> None:
    """Le ciel plus sombre est préféré si le signal faible demeure lisible."""
    before = measurements(.1, .2, .3, 4)
    dark = Weak.compare(before, measurements(.06, .14, .4, 4.1))
    bright = Weak.compare(before, measurements(.2, .3, .2, 4.1))
    assert dark['lower_background_preferred']
    assert dark['score_adjustment'] > bright['score_adjustment']
    assert not bright['sufficient']


@pytest.mark.parametrize('contrast,cnr,faint', [(.1, 4, .15), (.3, 1, .15), (.3, 4, .001)])
def test_darkening_cannot_hide_signal_loss(contrast: float, cnr: float, faint: float) -> None:
    """Une perte de contraste, de CNR ou de visibilité ne reçoit aucun bonus de ciel."""
    result = Weak.compare(measurements(.1, .2, .3, 4),
                          measurements(.01, faint, contrast, cnr))
    assert not result['sufficient']
    assert not result['lower_background_preferred']


def test_linear_input_gets_an_initial_visibility_budget() -> None:
    """Un objet initialement sombre peut être révélé sans pénaliser l'ouverture utile."""
    before = measurements(.001, .002, .02, 2)
    after = measurements(.025, .08, .3, 2)
    result = Weak.compare(before, after)
    assert result['visibility_gain_budget'] == 40
    assert result['sky_penalty'] == 0
    assert result['score_adjustment'] > 0


def test_missing_or_different_reference_does_not_change_legacy_score() -> None:
    """Un champ uniforme ou une référence incompatible n'invente aucun signal."""
    uniform = np.full((100, 100), .1)
    assert not Weak.measure(uniform, uniform)['available']
    assert Weak.compare({}, {})['score_adjustment'] == 0
    before = measurements(.1, .2, .3, 4)
    other = deepcopy(before)
    other['weak_structure']['reference_sha256'] = 'different'
    assert not Weak.compare(before, other)['available']


def test_legacy_diffuse_guard_only_relaxes_when_requested() -> None:
    """Le mode historique garde sa règle ; le piecewise peut préférer un ciel sobre."""
    before = measurements(.1, .2, .3, 4)
    after = measurements(.06, .14, .4, 4.1)
    before.update(diffuse_median=.2, sky_median=.1)
    after.update(diffuse_median=.14, sky_median=.06)
    assert QuartileGHSProcessor.diffuse_rejections(before, after)
    assert not QuartileGHSProcessor.diffuse_rejections(
        before, after, allow_weak_preservation=True)


def test_curvature_softening_does_not_apply_to_rising_slopes() -> None:
    """La pénalité ne se réduit que pour un profil toujours descendant et préservé."""
    weak = dict(available=True, sufficient=True)
    audit = dict(penalty=8., candidate_geometry=dict(slopes=[-3., -2., -4.]))
    assert Weak.shape_penalty(audit, weak) == .25
    audit['candidate_geometry']['slopes'][1] = .2
    assert Weak.shape_penalty(audit, weak) == 8
    assert Weak.shape_penalty(audit, {}) == 8


def test_bad_geometry_or_nonfinite_light_is_refused() -> None:
    """Les entrées incohérentes ne deviennent pas une qualité apparente valide."""
    source = scene()
    with pytest.raises(ValueError):
        Weak.measure(source, source[:5])
    candidate = source.copy()
    candidate[0, 0] = np.nan
    with pytest.raises(ValueError):
        Weak.measure(source, candidate)


def test_new_modes_remain_blocking_even_with_weak_structure_bonus() -> None:
    """Un bonus de ciel ne peut jamais rendre admissible une nouvelle bosse sombre."""
    from lib.ghs_histogram_shape import histogram_selection_key
    from lib.ghs_render_quality import HistogramRenderQuality
    before = measurements(.1, .2, .3, 4)
    after = measurements(.06, .14, .4, 4.1)
    for measured in (before, after):
        measured.update(sky_median=.05, diffuse_median=.15, noise=.01)
    profile = dict(measurable=True, peak=.1, shape_preservation=dict(
        measurable=True, achieved=True, rejections=[], penalty=0.,
        candidate_geometry=dict(slopes=[-3., -2., -4.])),
        low_light_modes=dict(measurable=True, prominence_fraction=.8))
    reference = dict(low_light_modes=dict(measurable=True, prominence_fraction=0.))
    result = HistogramRenderQuality.attach(profile, before, after, reference,
                                           bright_retention=.9)
    assert result['render_quality']['weak_structure']['lower_background_preferred']
    assert not histogram_selection_key(result)[0]
    assert 'new low-light histogram mode' in result['low_light_mode_guard']['rejections']


def test_audit_annotations_expose_measured_quality() -> None:
    """L'encart montre les mesures et contribution du nouveau contrôle."""
    from lib.ghs_diagnostics import _weak_structure_annotation
    comparison = Weak.compare(measurements(.1, .2, .3, 4), measurements(.06, .14, .4, 4.1))
    text = '\n'.join(_weak_structure_annotation(comparison))
    assert 'Ciel régional Q90 : 0.0600' in text
    assert 'Contraste faible : 0.400' in text
    assert 'Contribution au score' in text
    assert 'politique antérieure' in '\n'.join(_weak_structure_annotation({'available': False}))


def test_histogram_bonus_cannot_reward_wasted_sky_lift() -> None:
    """Un objet déjà visible ne justifie pas un ciel doublé à CNR inchangé."""
    before = measurements(.05, .15, .3, 4)
    after = measurements(.1, .25, .35, 4)
    result = Weak.compare(before, after)
    assert result['unnecessary_sky_lift']
    assert result['unnecessary_sky_penalty'] == pytest.approx(2.)
    assert result['score_adjustment'] < -2
