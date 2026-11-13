"""Classement de rendu : suffisance, pénalités continues et mesures spatiales."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from lib.auto_ghs import GHSOptimizer
from lib.config import Config
from lib.ghs_final_stretch import ProtectedGHSFinalizer
from lib.ghs_histogram_shape import histogram_selection_key
from lib.ghs_render_quality import HistogramRenderQuality as Quality
from lib.type_defs import JSONReport


@pytest.fixture
def profile() -> JSONReport:
    """Fournit une enveloppe descendante mesurable avec objectifs lumineux désactivés."""
    return dict(measurable=True, peak=.04, score=0.,
                shape=dict(log_density=np.linspace(4, 1, 61).tolist()),
                shape_preservation=dict(measurable=True, achieved=True, peak_shift=.03,
                                        rejections=[], penalty=0.),
                regression_floor=dict(minimum_count=0.), tail=dict(target=0.))


@pytest.fixture
def measure() -> JSONReport:
    """Mesures de régions fixes dans l'échelle normalisée de l'image."""
    return dict(sky_median=.05, diffuse_median=.15, noise=.01,
                highlight_contrast=.2, local_contrast=.1)


def test_cnr_and_visibility_rewards_saturate(profile: JSONReport, measure: JSONReport) -> None:
    """Un contraste/bruit sept fois supérieur ne gagne rien après la suffisance."""
    opened = dict(measure, diffuse_median=.25, noise=.02)
    sufficient = Quality.attach(profile, measure, opened, profile, bright_retention=.45)
    excessive = Quality.attach(profile, measure, dict(opened, noise=.02/7), profile,
                               bright_retention=.45)
    assert sufficient['render_quality']['cnr_retention'] == pytest.approx(1.)
    assert excessive['render_quality']['cnr_retention'] == pytest.approx(7.)
    assert excessive['render_quality']['score'] == pytest.approx(sufficient['render_quality']['score'])
    assert sufficient['render_quality']['visibility_reward'] == pytest.approx(.2)


@pytest.mark.parametrize('missing', [None, 0.])
def test_missing_noise_never_certifies_quality(
    profile: JSONReport, measure: JSONReport, missing: float | None,
) -> None:
    """Une mesure absente ou non exploitable ne devient pas un score de qualité nul."""
    output = Quality.attach(profile, measure, dict(measure, noise=missing), profile,
                            bright_retention=.45)
    assert not output['render_quality']['available']
    assert output['render_quality']['score'] is None
    assert not histogram_selection_key(output)[0]
    json.dumps(output, allow_nan=False)


def test_light_targets_do_not_override_useful_contrast(
    profile: JSONReport, measure: JSONReport,
) -> None:
    """Atteindre les deux niveaux ne compense pas une conservation utile de 56 %."""
    faint = Quality.attach(profile, measure, dict(measure, noise=.01/.96), profile,
                           bright_retention=.45)
    pushed = deepcopy(profile)
    pushed.update(regression_floor=dict(minimum_count=10., predicted_log10_count=3.,
                                        reference_covered=True, achieved=True),
                  tail=dict(target=.25, height=.9, achieved=True))
    pushed = Quality.attach(pushed, measure, dict(measure, noise=.01/.56), profile,
                            bright_retention=.45)
    assert pushed['render_quality']['light_reward'] == pytest.approx(.1)
    assert histogram_selection_key(faint) > histogram_selection_key(pushed)
    assert pushed['render_quality']['cnr_penalty'] > 0


def test_peak_only_breaks_quality_ties(profile: JSONReport, measure: JSONReport) -> None:
    """La qualité prime ; une cible de pic absolue n'est pas imposée."""
    near = Quality.attach(profile, measure, measure, profile, bright_retention=.45)
    far = deepcopy(near)
    far['peak'] = .15
    far['shape_preservation']['peak_shift'] = .14
    assert histogram_selection_key(near) > histogram_selection_key(far)
    far['render_quality']['score'] += .02
    assert histogram_selection_key(far) > histogram_selection_key(near)
    far['shape_preservation']['rejections'] = ['histogram peak displacement']
    assert not histogram_selection_key(far)[0]


def test_bright_compression_is_progressive_including_fusion(
    profile: JSONReport, measure: JSONReport,
) -> None:
    """Un écart infime au seuil reste bénin ; une compression à 8 % coûte davantage."""
    mild = Quality.attach(profile, measure, dict(measure, highlight_contrast=.2*.451), profile,
                          bright_retention=.4523)
    strong = Quality.attach(profile, measure, measure, profile, bright_retention=.4523,
                            composite_before=measure,
                            composite_after=dict(measure, highlight_contrast=.2*.08))
    assert 0 < mild['render_quality']['bright_penalty'] < .001
    assert strong['render_quality']['bright_penalty'] > .3
    assert histogram_selection_key(mild) > histogram_selection_key(strong)
    absent = Quality.attach(profile, measure, dict(measure, highlight_contrast=None), profile,
                            bright_retention=.45)
    assert absent['render_quality']['bright_retention']['starless_highlight_contrast'] is None


def test_shape_excess_is_soft_and_increases_penalty(
    profile: JSONReport, measure: JSONReport,
) -> None:
    """La chute sous 65 % pénalise progressivement sans devenir un rejet de sécurité."""
    mild = deepcopy(profile)
    mild['shape_preservation'].update(achieved=False, rejections=['global histogram flattening'],
                                     drop_retention=.64, minimum_drop_retention=.65)
    strong = deepcopy(mild)
    strong['shape_preservation']['drop_retention'] = .2
    first = Quality.attach(mild, measure, measure, profile, bright_retention=.45)
    last = Quality.attach(strong, measure, measure, profile, bright_retention=.45)
    assert histogram_selection_key(first)[0]
    assert 0 < first['render_quality']['shape_penalty'] < last['render_quality']['shape_penalty']
    assert Quality.hard_shape_rejections(first) == []


def test_middle_rise_uses_smoothed_support_and_fixed_reference(
    profile: JSONReport, measure: JSONReport,
) -> None:
    """Une bosse intermédiaire nouvelle coûte ; la queue et les bosses natives sont exclues."""
    candidate = deepcopy(profile)
    candidate['shape']['log_density'][30:40] = np.linspace(2.5, 2.8, 10).tolist()
    p = Quality.attach(candidate, measure, measure, profile, bright_retention=.45)
    assert p['render_quality']['excess_middle_rise_dex'] == pytest.approx(.3)
    assert p['render_quality']['rise_penalty'] == pytest.approx(4.5)
    native = Quality.attach(candidate, measure, measure, candidate, bright_retention=.45)
    assert native['render_quality']['rise_penalty'] == 0
    tail = deepcopy(profile)
    tail['shape']['log_density'][-5:] = [10., -10., 10., -10., 10.]
    assert Quality.middle_rise(tail) == 0


def test_spatial_measure_matches_full_region_definitions() -> None:
    """Le sondage conserve les voisins spatiaux et les masques de la mesure complète."""
    light = np.random.default_rng(38).uniform(.01, .99, (100, 100)).astype('f4')
    image = np.repeat(light[None], 3, axis=0)
    transformed = np.sqrt(image)
    thresholds = ProtectedGHSFinalizer.thresholds(image, 'human')
    thresholds.update(bright=.75, core=.95)
    from lib.auto_ghs import luminance

    spatial = Quality.sample_measure(luminance(image, 'human'), transformed, thresholds, 'human')
    full = ProtectedGHSFinalizer.measure(image, transformed, thresholds, 'human')
    for key, value in spatial.items():
        assert value == pytest.approx(full[key], rel=1e-5)
    with pytest.raises(ValueError, match='Grille'):
        Quality.sample_measure(light[:2], image, thresholds, 'human')


@pytest.mark.parametrize('name,value', [
    ('ghs_quality_cnr_floor', 0.), ('ghs_quality_cnr_floor', 1.1),
    ('ghs_quality_cnr_floor', True), ('ghs_quality_cnr_floor', float('nan')),
    ('ghs_quality_tail_bonus', -.1), ('ghs_quality_tail_bonus', float('inf')),
])
def test_invalid_quality_options_are_rejected(name: str, value: float | bool) -> None:
    """Les domaines et les valeurs non finies sont vérifiés pour CLI et configuration."""
    with pytest.raises(ValueError, match=name.replace('_', '-')):
        Quality.validate_options(argparse.Namespace(**{name: value}))


def test_quality_options_follow_saved_and_cli_priority(tmp_path: Path) -> None:
    """Les options appartiennent au moteur qualité et respectent CLI > JSON > défauts."""
    config = Config(tmp_path/'settings.json')
    parser = argparse.ArgumentParser()
    config.register(GHSOptimizer, parser)
    args = config.parse_args(parser, [])
    assert args.ghs_quality_cnr_floor == .9
    assert args.ghs_quality_tail_bonus == .1
    config.set('ghs_quality_cnr_floor', .85)
    parser = argparse.ArgumentParser()
    config.register(GHSOptimizer, parser)
    assert config.parse_args(parser, []).ghs_quality_cnr_floor == .85
    assert config.parse_args(parser, ['--ghs-quality-cnr-floor', '.95']).ghs_quality_cnr_floor == .95


@pytest.mark.parametrize('key', ['sky_median', 'diffuse_median', 'noise', 'highlight_contrast'])
def test_nonfinite_measurement_is_rejected(
    profile: JSONReport, measure: JSONReport, key: str,
) -> None:
    """Une mesure non finie ne devient ni un bonus valide ni un zéro implicite."""
    with pytest.raises(ValueError, match='non finie'):
        Quality.attach(profile, measure, dict(measure, **{key: float('nan')}), profile,
                       bright_retention=.45)


def test_sky_compression_cannot_masquerade_as_useful_cnr(
    profile: JSONReport, measure: JSONReport,
) -> None:
    """Un GHS qui écrase vingt fois le bruit écrase aussi les détails du ciel."""
    retained = Quality.attach(profile, measure, dict(measure, diffuse_median=.25, noise=.02),
                              profile, bright_retention=.45)
    crushed = Quality.attach(profile, measure, dict(measure, diffuse_median=.25, noise=.0005),
                             profile, bright_retention=.45)
    assert crushed['render_quality']['cnr_reward'] == retained['render_quality']['cnr_reward']
    assert crushed['render_quality']['sky_compression_penalty'] > .3
    assert histogram_selection_key(retained) > histogram_selection_key(crushed)


def test_a_new_secondary_mode_cannot_hide_the_left_valley(
    profile: JSONReport, measure: JSONReport,
) -> None:
    """Déplacer le pic dominant après un creux ne permet pas d'ignorer ce creux."""
    reference = dict(profile, prepeak_valley_dex=.05)
    distorted = dict(profile, prepeak_valley_dex=.5)
    first = Quality.attach(reference, measure, measure, reference, bright_retention=.45)
    last = Quality.attach(distorted, measure, measure, reference, bright_retention=.45)
    assert first['render_quality']['prepeak_valley_penalty'] == 0
    assert last['render_quality']['prepeak_valley_penalty'] > 5
    assert histogram_selection_key(first) > histogram_selection_key(last)
    unknown = Quality.attach(distorted, measure, measure, profile, bright_retention=.45)
    assert unknown['render_quality']['reference_prepeak_valley_dex'] is None
    assert unknown['render_quality']['excess_prepeak_valley_dex'] is None


def test_the_first_post_peak_region_is_also_checked(profile: JSONReport) -> None:
    """Une bosse dans les premiers 15 % ne peut pas être sautée une seconde fois."""
    profile['shape']['log_density'][:8] = [4., 3.5, 3., 3.1, 3.2, 3.3, 3.4, 3.5]
    profile['shape']['log_density'][8:] = np.linspace(3.45, 1., 53).tolist()
    assert Quality.middle_rise(profile) == pytest.approx(.5)


def test_a_double_initial_peak_is_penalized_even_when_the_body_spline_is_monotone(
    profile: JSONReport, measure: JSONReport,
) -> None:
    """Deux bosses initiales ne peuvent gagner grâce aux seuls bonus de queue."""
    reference = dict(profile, initial_peak=dict(secondary_rise_fraction=0.))
    double = dict(profile, initial_peak=dict(secondary_rise_fraction=.16),
                  regression_floor=dict(minimum_count=10., predicted_log10_count=2.,
                                        reference_covered=True),
                  tail=dict(target=.25, height=.3))
    before = Quality.attach(reference, measure, measure, reference, bright_retention=.45)
    after = Quality.attach(double, measure, measure, reference, bright_retention=.45)
    assert after['render_quality']['rise_penalty'] == 0
    assert after['render_quality']['initial_peak_penalty'] == pytest.approx(1.28)
    assert histogram_selection_key(before) > histogram_selection_key(after)
    native = Quality.attach(double, measure, measure, double, bright_retention=.45)
    assert native['render_quality']['initial_peak_penalty'] == 0
    unknown = Quality.attach(double, measure, measure, profile, bright_retention=.45)
    assert unknown['render_quality']['reference_initial_peak_rise_fraction'] is None
    assert unknown['render_quality']['excess_initial_peak_rise_fraction'] is None
