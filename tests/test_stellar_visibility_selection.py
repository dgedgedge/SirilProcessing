"""L'objectif de visibilité choisit une courbe globale sous les gardes de profils."""
import argparse
from pathlib import Path

import numpy as np
import pytest

from lib.star_rendering import ConservativeStarRenderer as Renderer
from lib.star_rendering import stellar_peak_positions
from lib.stellar_appearance import StellarAppearanceQuality
from lib.stretch_transfer import CumulativeStretchTransform, stellar_operation


@pytest.fixture
def field() -> np.ndarray:
    """Répartit 36 étoiles faibles isolées, mesurables loin des bords."""
    y, x = np.indices((301, 301))
    light = np.zeros(x.shape, dtype='f4')
    centers = ((cy, cx) for cy in range(50, 251, 40) for cx in range(50, 251, 40))
    for (cy, cx), amplitude in zip(centers, np.geomspace(1e-4, .01, 36)):
        light += amplitude*np.exp(-((x-cx)**2+(y-cy)**2)/2)
    return np.array([light, .6*light, .3*light])


def test_goal_changes_pixels_and_visibility_without_excessive_profile_changes(field: np.ndarray) -> None:
    """20 % et 70 % sélectionnent réellement des rendus différents, avec ailes contrôlées."""
    original = field.copy()
    baseline, base_report = Renderer.render(field, visible_fraction=0)
    low, low_report = Renderer.render(field, visible_fraction=.2)
    high, high_report = Renderer.render(field, visible_fraction=.7)
    low_fraction = low_report['appearance_quality']['visibility']['fraction_lower_bound']
    high_fraction = high_report['appearance_quality']['visibility']['fraction_lower_bound']
    assert base_report['appearance_quality']['visibility']['fraction_lower_bound'] < .2
    assert .2 <= low_fraction < .7 <= high_fraction
    assert not np.array_equal(baseline, low)
    assert not np.array_equal(low, high)
    assert high_report['visibility_selection']['target_reached']
    assert high_report['histogram_only'] and not high_report['spatially_varying']
    for key in ('fwhm', 'wings', 'skirt'):
        widths = high_report[key]
        assert widths['maximum_group_median_ratio'] <= high_report['max_width_ratio']
        assert widths['count'] == widths['reference_count']
        assert all(group['median_ratio'] >= .9 for group in widths['brightness_groups'])
    np.testing.assert_array_equal(field, original)


def test_selected_curve_is_exactly_replayable_and_spatially_uniform(field: np.ndarray) -> None:
    """Deux RGB identiques ont la même sortie, et la recette globale reproduit tout le FITS."""
    field[:, 10, 10] = field[:, 50, 50]
    result, report = Renderer.render(field, visible_fraction=.4)
    np.testing.assert_array_equal(result[:, 10, 10], result[:, 50, 50])
    replay = CumulativeStretchTransform(
        (float(field.min()), float(field.max())),
        [stellar_operation(report, report['histogram_balance'])],
    )
    np.testing.assert_array_equal(replay.apply_rgb(field), result)


def test_unreachable_goal_keeps_safe_render_and_records_failure(field: np.ndarray) -> None:
    """Une garde stricte prime sur la visibilité sans faire échouer tout le rendu."""
    baseline, _ = Renderer.render(field, visible_fraction=0, max_width_ratio=1.03)
    selected, report = Renderer.render(field, visible_fraction=.95, max_width_ratio=1.03)
    assert not report['visibility_selection']['target_reached']
    assert report['visibility_selection']['stop_reason'] == 'best_safe_visibility'
    assert report['visibility_selection']['profile_guard']['maximum_ratio'] == 1.03
    assert report['appearance_quality']['visibility']['fraction_lower_bound'] < .95
    assert np.isfinite(selected).all()
    assert selected.shape == baseline.shape


def test_bright_wing_degradation_cannot_win_visibility_search(
    field: np.ndarray, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un bon taux de visibilité ne contourne pas une jupe dégradée du groupe brillant."""
    baseline, report = Renderer.render(field, visible_fraction=0)
    original = Renderer._render_global

    def excessive_skirt(*args: object, **kwargs: object) -> tuple[np.ndarray, dict]:
        """Simule seulement une garde d'ailes excessive sur tous les nouveaux essais."""
        image, audit = original(*args, **kwargs)
        audit['wings']['maximum_group_median_ratio'] = 99.
        return image, audit

    monkeypatch.setattr(Renderer, '_render_global', excessive_skirt)
    positions = stellar_peak_positions(field)
    appearance = StellarAppearanceQuality.evaluate(field, baseline, positions)
    selected, audit, _ = Renderer._select_visibility(
        field, baseline, report, appearance, positions, .4,
        2., .85, 2., 5., 1., .25, 'human', None,
    )
    np.testing.assert_array_equal(selected, baseline)
    assert not audit['visibility_selection']['target_reached']


@pytest.mark.parametrize('target', [-.1, 1.1, np.nan])
def test_invalid_target_fails(target: float, field: np.ndarray) -> None:
    """Les cibles hors domaine échouent avant une recherche d'étirement."""
    with pytest.raises(ValueError, match='visibilité'):
        Renderer.render(field, visible_fraction=target)


def test_cli_exposes_forty_percent_target() -> None:
    """Le défaut et sa surcharge sont déclarés localement au moteur."""
    parser = argparse.ArgumentParser()
    Renderer.add_arguments(parser)
    assert parser.parse_args([]).star_visible_fraction == .4
    assert parser.parse_args(['--star-visible-fraction', '.6']).star_visible_fraction == .6


def test_visibility_target_configuration_and_cli_priority(tmp_path: Path) -> None:
    """Une cible mémorisée reste prioritaire sur le défaut, la CLI sur la configuration."""
    from lib.config import Config

    config = Config(tmp_path / 'settings.json')
    config.set('star_visible_fraction', .6)
    parser = argparse.ArgumentParser()
    config.register(Renderer, parser)
    assert config.parse_args(parser, []).star_visible_fraction == .6
    assert config.parse_args(parser, ['--star-visible-fraction', '.8']).star_visible_fraction == .8
