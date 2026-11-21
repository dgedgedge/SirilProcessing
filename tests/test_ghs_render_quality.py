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
    """Atteindre les deux niveaux ne compense pas une conservation utile de 20 %."""
    faint = Quality.attach(profile, measure, dict(measure, noise=.01/.96), profile,
                           bright_retention=.45)
    pushed = deepcopy(profile)
    pushed.update(regression_floor=dict(minimum_count=10., predicted_log10_count=3.,
                                        reference_covered=True, achieved=True),
                  tail=dict(target=.25, height=.9, achieved=True))
    pushed = Quality.attach(pushed, measure, dict(measure, noise=.01/.2), profile,
                            bright_retention=.45)
    assert pushed['render_quality']['light_reward'] == pytest.approx(1.1)
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
    ('ghs_quality_reference_weight', -.1), ('ghs_quality_reference_weight', 10.1),
    ('ghs_quality_reference_weight', True), ('ghs_quality_reference_weight', float('nan')),
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
    assert args.ghs_quality_reference_weight == 1.
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


def test_new_ratio_policy_does_not_charge_obsolete_global_shape_metrics(
    profile: JSONReport, measure: JSONReport,
) -> None:
    """Une variation globale conforme aux ratios ne reçoit aucun coût caché."""
    candidate = deepcopy(profile)
    candidate['shape_preservation'].update(
        policy='shoulder_and_slope_ratios', penalty=0.,
        drop_retention=.2, minimum_drop_retention=.65,
        distance=.8, maximum_shape_distance=.15,
    )
    measured = Quality.attach(candidate, measure, measure, profile, bright_retention=.45)
    assert measured['render_quality']['shape_penalty'] == 0


@pytest.mark.parametrize('prediction,progress', [(0., 0.), (1., .5), (1.5, .75), (2., 1.), (3., 1.)])
def test_reference_level_progress_is_logarithmic_and_has_high_weight(
    prediction: float, progress: float, profile: JSONReport, measure: JSONReport,
) -> None:
    """À cible 2 dex, une hauteur de 1 dex rapporte la moitié du point dédié."""
    candidate = deepcopy(profile)
    candidate['regression_floor'] = dict(minimum_count=100., predicted_log10_count=prediction,
                                         reference_covered=True)
    result = Quality.attach(candidate, measure, measure, profile, bright_retention=.45)
    quality = result['render_quality']
    assert quality['reference_progress'] == pytest.approx(progress)
    assert quality['reference_reward'] == pytest.approx(progress)
    assert quality['reference_weight'] == 1.
    assert quality['tail_reward'] == 0


def test_reference_height_can_drive_selection_inside_ratio_tolerances(
    profile: JSONReport, measure: JSONReport,
) -> None:
    """Deux rendus conformes aux ratios sont départagés fortement par le niveau à 0,8."""
    low, high = deepcopy(profile), deepcopy(profile)
    for candidate, prediction in ((low, 1.), (high, 1.5)):
        candidate['shape_preservation'].update(policy='shoulder_and_slope_ratios', penalty=0.)
        candidate['regression_floor'] = dict(minimum_count=100., predicted_log10_count=prediction,
                                             reference_covered=True)
    first = Quality.attach(low, measure, measure, profile, bright_retention=.45)
    last = Quality.attach(high, measure, measure, profile, bright_retention=.45)
    assert last['render_quality']['score']-first['render_quality']['score'] == pytest.approx(.25)
    assert histogram_selection_key(last) > histogram_selection_key(first)


@pytest.mark.parametrize('penalty,reason', [
    (8., 'histogram slope ratio inversion'),
    (10., 'histogram slope ratio distortion'),
])
def test_full_reference_bonus_cannot_hide_a_strong_ratio_penalty(
    penalty: float, reason: str, profile: JSONReport, measure: JSONReport,
) -> None:
    """Le poids fort à 0,8 guide les rendus conformes sans sauver les ratios interdits."""
    low, high = deepcopy(profile), deepcopy(profile)
    low['shape_preservation'].update(policy='shoulder_and_slope_ratios', penalty=0.)
    high['shape_preservation'].update(policy='shoulder_and_slope_ratios', penalty=penalty,
                                      achieved=False, rejections=[reason])
    low['regression_floor'] = dict(minimum_count=100., predicted_log10_count=1., reference_covered=True)
    high['regression_floor'] = dict(minimum_count=100., predicted_log10_count=2., reference_covered=True)
    first = Quality.attach(low, measure, measure, profile, bright_retention=.45)
    last = Quality.attach(high, measure, measure, profile, bright_retention=.45)
    assert last['render_quality']['reference_reward'] == 1.
    assert histogram_selection_key(first) > histogram_selection_key(last)


def test_dark_mode_cannot_be_compensated_by_any_quality_bonus(
    profile: JSONReport, measure: JSONReport,
) -> None:
    """Une excellente note ne rend pas admissible une nouvelle seconde bosse."""
    reference = dict(profile, low_light_modes=dict(measurable=True, prominence_fraction=0.))
    candidate = dict(profile, low_light_modes=dict(measurable=True, prominence_fraction=.8))
    candidate = Quality.attach(candidate, measure, measure, reference, bright_retention=.45)
    candidate['render_quality']['score'] = 100.
    assert Quality.hard_shape_rejections(candidate) == ['new low-light histogram mode']
    assert not histogram_selection_key(candidate)[0]
    assert 'low_light_mode_guard' not in profile


@pytest.mark.parametrize('initial,final,rejected', [
    (0., .05, False), (0., .1, False), (0., .1001, True),
    (.6, .6, False), (.6, .65, False), (.6, .71, True), (.8, .2, False),
])
def test_dark_mode_guard_compares_to_the_fixed_reference(
    initial: float, final: float, rejected: bool,
) -> None:
    """Les bosses natives sont tolérées, mais pas leur aggravation cumulative."""
    reference = dict(low_light_modes=dict(measurable=True, prominence_fraction=initial))
    candidate = dict(low_light_modes=dict(measurable=True, prominence_fraction=final))
    guard = Quality.low_light_mode_guard(reference, candidate)
    assert bool(guard['rejections']) is rejected
    assert guard['excess_prominence_fraction'] == pytest.approx(max(0., final-initial))


def test_dark_mode_guard_missing_reference_remains_unknown() -> None:
    """La lecture d'un ancien rapport ne certifie pas une référence absente."""
    candidate = dict(low_light_modes=dict(measurable=True, prominence_fraction=.8))
    guard = Quality.low_light_mode_guard({}, candidate)
    assert guard['achieved'] is None
    assert guard['reference_prominence_fraction'] is None
    assert guard['rejections'] == []


@pytest.mark.parametrize('value', [np.nan, np.inf, -.1, 1.1])
def test_dark_mode_guard_rejects_invalid_prominence(value: float) -> None:
    """Une mesure invalide ne peut pas neutraliser silencieusement la garde."""
    candidate = dict(low_light_modes=dict(measurable=True, prominence_fraction=value))
    with pytest.raises(ValueError, match='Proéminence'):
        Quality.low_light_mode_guard({}, candidate)


def test_dark_mode_annotation_distinguishes_a_hard_rejection(
    profile: JSONReport, measure: JSONReport,
) -> None:
    """L'encart annonce la garde et son rejet, au lieu d'une simple pénalité."""
    from lib.ghs_diagnostics import _render_quality_annotation

    reference = dict(profile, low_light_modes=dict(measurable=True, prominence_fraction=0.))
    candidate = dict(profile, low_light_modes=dict(measurable=True, prominence_fraction=.8))
    graded = Quality.attach(candidate, measure, measure, reference, bright_retention=.45)
    annotation = '\n'.join(_render_quality_annotation(graded))
    assert '80.0 % (maximum 10.0 %)' in annotation
    assert 'REJET : nouvelle bosse' in annotation
