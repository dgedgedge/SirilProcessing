"""Épaulement, signe, ordre des pentes et cohérence des aplatissements."""

from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from lib.ghs_histogram_shape import compare_shoulder_slope_ratios
from lib.type_defs import JSONReport


def geometry(slopes: list[float], shoulder: float = .1) -> JSONReport:
    """Construit des mesures de segments contrôlées, séparées de leur détection."""
    return dict(measurable=True, shoulder_present=True, shoulder_position=shoulder,
                slopes=slopes, slope_floor=1e-6,
                ratios=[last/first for first, last in zip(slopes, slopes[1:])])


@pytest.mark.parametrize('candidate,reason', [
    ([-1, 2, -4, -8, -16], 'histogram slope sign inversion'),
    ([-2, -1, -4, -8, -16], 'histogram slope ratio inversion'),
    ([-1, -4, -4, -8, -16], 'histogram slope ratio distortion'),
    ([-1, -2, -40, -80, -160], 'histogram slope ratio distortion'),
])
def test_forbidden_sign_order_and_incoherent_flattening(
    candidate: list[float], reason: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Les gardes interdisent croissance, inversion et aplatissement isolé."""
    import lib.ghs_histogram_shape as module

    first, last = geometry([-1, -2, -4, -8, -16]), geometry(candidate)
    monkeypatch.setattr(module, 'shoulder_slope_geometry', lambda profile: profile)
    result = compare_shoulder_slope_ratios(first, last, .15, .5)
    assert not result['achieved']
    assert reason in result['rejections']


@pytest.mark.parametrize('factor', [.2, .5, 1., 3.])
def test_common_scaling_does_not_change_ratios_or_penalty(
    factor: float, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une modification commune de toutes les pentes est libre dans les deux sens."""
    import lib.ghs_histogram_shape as module

    first = geometry([-1, -2, -4, -8, -16])
    saved = deepcopy(first)
    last = geometry([factor*value for value in first['slopes']])
    monkeypatch.setattr(module, 'shoulder_slope_geometry', lambda profile: profile)
    result = compare_shoulder_slope_ratios(first, last, .15, .5)
    assert result['achieved']
    assert result['penalty'] == 0
    np.testing.assert_allclose(result['ratio_errors'], 0, atol=1e-14)
    assert first == saved


def test_terminal_steepening_has_reduced_weight_but_sign_remains_protected(monkeypatch: pytest.MonkeyPatch) -> None:
    """La queue descendante peut se raidir ; elle ne peut pas devenir croissante."""
    import lib.ghs_histogram_shape as module

    monkeypatch.setattr(module, 'shoulder_slope_geometry', lambda profile: profile)
    reference = geometry([-1, -2, -4, -8, -16])
    terminal = compare_shoulder_slope_ratios(
        reference, geometry([-1, -2, -4, -8, -160]), .15, .5,
    )
    assert terminal['achieved']
    assert terminal['terminal_weight'] == .2
    assert terminal['ratio_errors'][-1] == pytest.approx(.2*np.log(10))
    rising = compare_shoulder_slope_ratios(
        reference, geometry([-1, -2, -4, -8, 16]), .15, .5,
    )
    assert 'histogram slope sign inversion' in rising['rejections']


def test_shoulder_must_remain_present_and_not_advance_too_far(monkeypatch: pytest.MonkeyPatch) -> None:
    """L'avance est mesurée en intensité absolue ; un recul n'est pas puni."""
    import lib.ghs_histogram_shape as module

    monkeypatch.setattr(module, 'shoulder_slope_geometry', lambda profile: profile)
    reference = geometry([-1, -2, -4, -8, -16])
    lost = geometry([-1, -2, -4, -8, -16])
    lost.update(shoulder_present=False, shoulder_position=None)
    assert 'histogram shoulder lost' in compare_shoulder_slope_ratios(reference, lost, .15, .5)['rejections']
    moved = geometry([-1, -2, -4, -8, -16], shoulder=.3)
    assert 'histogram shoulder advance' in compare_shoulder_slope_ratios(reference, moved, .15, .5)['rejections']
    earlier = geometry([-1, -2, -4, -8, -16], shoulder=.05)
    assert compare_shoulder_slope_ratios(reference, earlier, .15, .5)['achieved']


def test_one_segment_can_flatten_without_its_predecessor(monkeypatch: pytest.MonkeyPatch) -> None:
    """La dernière clarification autorise -1/-2 vers -1/-1,5 sans inversion."""
    import lib.ghs_histogram_shape as module

    monkeypatch.setattr(module, 'shoulder_slope_geometry', lambda profile: profile)
    reference = geometry([-1, -2, -2, -2, -2])
    candidate = geometry([-1, -1.5, -1.5, -1.5, -1.5])
    result = compare_shoulder_slope_ratios(reference, candidate, .15, .5)
    assert result['achieved']
    assert result['penalty'] == 0


def test_inversion_and_excessive_ratio_change_are_very_costly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Les deux cas refusés par l'utilisateur coûtent plus que les bonus lumineux."""
    import lib.ghs_histogram_shape as module

    monkeypatch.setattr(module, 'shoulder_slope_geometry', lambda profile: profile)
    reference = geometry([-1, -2, -2, -2, -2])
    inverted = compare_shoulder_slope_ratios(
        reference, geometry([-2, -1, -1, -1, -1]), .15, .5,
    )
    exaggerated = compare_shoulder_slope_ratios(
        reference, geometry([-1, -4, -4, -4, -4]), .15, .5,
    )
    assert inverted['penalty'] >= 8
    assert exaggerated['penalty'] > 4
    assert 'histogram slope ratio inversion' in inverted['rejections']
    assert 'histogram slope ratio distortion' in exaggerated['rejections']


@pytest.mark.parametrize('name,value', [
    ('ghs_shape_ratio_tolerance', 0), ('ghs_shape_ratio_tolerance', 4.1),
    ('ghs_shape_ratio_tolerance', float('nan')),
    ('ghs_shape_shoulder_advance', -.01), ('ghs_shape_shoulder_advance', 1.01),
    ('ghs_shape_terminal_weight', -.01), ('ghs_shape_terminal_weight', 1.01),
    ('ghs_shape_terminal_weight', True),
])
def test_ratio_options_reject_invalid_values(name: str, value: float) -> None:
    """Les seuils nouveaux sont validés avant toute recherche GHS."""
    import argparse
    from lib.auto_ghs import GHSOptimizer

    with pytest.raises(ValueError):
        GHSOptimizer.options(argparse.Namespace(**{name: value}))


def test_ratio_options_are_resolved_from_cli() -> None:
    """Les trois réglages atteignent le comparateur, y compris zéro avance et poids."""
    import argparse
    from lib.auto_ghs import GHSOptimizer
    from lib.ghs_histogram_shape import shape_comparison_options

    parser = argparse.ArgumentParser()
    GHSOptimizer.add_arguments(parser)
    defaults = GHSOptimizer.options(parser.parse_args([]))
    assert shape_comparison_options(defaults) == dict(
        ratio_tolerance=.5, max_shoulder_advance=.15, terminal_weight=.2,
    )
    args = GHSOptimizer.options(parser.parse_args([
        '--ghs-shape-ratio-tolerance', '.25', '--ghs-shape-shoulder-advance', '0',
        '--ghs-shape-terminal-weight', '0',
    ]))
    assert shape_comparison_options(args) == dict(
        ratio_tolerance=.25, max_shoulder_advance=0., terminal_weight=0.,
    )


@pytest.mark.parametrize('level,count', [('1', 10.), ('2', 100.), ('1.5', 31.622776601683793),
                                         ('0', 1.), ('off', 0.)])
def test_logarithmic_reference_option_has_no_linear_alias(level: str, count: float) -> None:
    """La cible est déclarée uniquement en log10, avec les exemples utilisateur."""
    import argparse
    from lib.auto_ghs import GHSOptimizer

    parser = argparse.ArgumentParser()
    GHSOptimizer.add_arguments(parser)
    args = GHSOptimizer.options(parser.parse_args(['--ghs-regression-log-level', level]))
    assert args.ghs_regression_min_count == pytest.approx(count)
    assert '--ghs-regression-min-count' not in parser._option_string_actions
    assert '--ghs-shape-retention' not in parser._option_string_actions


def test_logarithmic_level_persists_without_a_linear_configuration_key(tmp_path: Path) -> None:
    """La cible sauvegardée est log10 ; off se mémorise sans ancienne clé linéaire."""
    import argparse
    import json
    from lib.auto_ghs import GHSOptimizer
    from lib.config import Config

    path = tmp_path / 'config.json'
    config = Config(path)
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(GHSOptimizer, parser)
    args = config.parse_args(parser, ['--ghs-regression-log-level', '1.5', '--save-config'])
    assert config.save_requested(args)
    contents = json.loads(path.read_text())
    assert contents['ghs_regression_log_level'] == 1.5
    assert 'ghs_regression_min_count' not in contents
    assert not any(key in contents for key in ('ghs_shape_retention', 'ghs_shape_distance',
                                               'ghs_shape_local_retention', 'ghs_shape_stiffness'))
    args = config.parse_args(parser, ['--ghs-regression-log-level', 'off', '--save-config'])
    assert config.save_requested(args)
    assert json.loads(path.read_text())['ghs_regression_log_level'] is None


def test_geometry_memoization_does_not_share_mutable_results() -> None:
    """La mémorisation des mesures ne partage pas l'état des audits entre couches."""
    from lib.ghs_histogram_shape import shoulder_slope_geometry

    x = np.linspace(.02, .87, 61)
    y = -6*x+5*np.maximum(x-.3, 0)-6*np.maximum(x-.6, 0)
    profile = dict(shape=dict(x=x.tolist(), log_density=y.tolist(),
                              drop_dex=float(y[0]-y[-1])))
    first = shoulder_slope_geometry(profile)
    expected = deepcopy(first)
    first['slopes'][0] = 999
    first['boundaries'][0] = 999
    assert shoulder_slope_geometry(profile) == expected
