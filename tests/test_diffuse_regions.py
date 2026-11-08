"""Échantillon diffus fixe, classes d'aire, couverture spatiale et mesures inchangées."""

import argparse
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from lib.config import Config
from lib.diffuse_regions import select_diffuse_regions
from lib.quality_processors import DiffuseSignalProcessor, QualityContext, RestorationEvaluationProcessor


def catalogue() -> tuple[list[tuple[slice, slice]], np.ndarray]:
    """Construit des tailles très déséquilibrées, réparties dans les quatre quadrants."""
    bounds, sizes = [], [0]
    for area, count in ((64, 160), (256, 24), (4096, 4), (65536, 1)):
        side = int(np.ceil(np.sqrt(area)))
        for index in range(count):
            y = 50 + (index % 2) * 1100 + (index // 4) * 8
            x = 50 + ((index // 2) % 2) * 1100 + (index // 4) * 8
            bounds.append((slice(y, y+side), slice(x, x+side)))
            sizes.append(area)
    return bounds, np.asarray(sizes)


def test_selection_covers_sizes_space_and_largest_regions() -> None:
    """Les grandes régions rares sont conservées et les quadrants sont couverts par taille."""
    bounds, sizes = catalogue()
    first, report = select_diffuse_regions(bounds, sizes, (2000, 2000), 32)
    second, repeated = select_diffuse_regions(bounds, sizes, (2000, 2000), 32)
    assert first == second and report == repeated
    assert len(first) == 32
    assert len({region.identifier for region in first}) == 32
    assert first == tuple(sorted(first, key=lambda region: region.identifier))
    assert max(region.area for region in first) == max(sizes)
    for stratum in report['area_strata']:
        assert stratum['evaluated_regions'] >= 1
        assert stratum['evaluated_quadrants'] == stratum['available_quadrants']
    assert report['available_regions'] == len(bounds)
    assert not report['exhaustive']
    # Les quatre grandes régions et la plus grande, rares, sont toutes gardées.
    assert sum(region.area >= 4096 for region in first) == 5


@pytest.mark.parametrize('maximum', [0, 1000])
def test_exhaustive_selection_retains_original_order_and_skips_small(maximum: int) -> None:
    """Zéro et un plafond suffisant ne changent pas les régions historiques admissibles."""
    bounds, sizes = catalogue()
    bounds.append((slice(0, 7), slice(0, 9)))
    sizes = np.r_[sizes, 63]
    selected, report = select_diffuse_regions(bounds, sizes, (2000, 2000), maximum)
    assert [region.identifier for region in selected] == list(range(1, len(bounds)))
    assert report['exhaustive'] and report['skipped_regions'] == 0


def test_empty_selection_and_duplicate_positions() -> None:
    """Les champs vides et les centres confondus restent déterministes sans doublons."""
    selected, report = select_diffuse_regions([], np.array([0]), (100, 100), 10)
    assert selected == () and report['available_regions'] == 0
    bounds = [(slice(10, 18), slice(10, 18))] * 20
    selected, _ = select_diffuse_regions(bounds, np.array([0] + [64] * 20), (100, 100), 5)
    assert [region.identifier for region in selected] == [1, 2, 3, 4, 5]


@pytest.mark.parametrize('maximum', [-1, True, 1.5])
def test_invalid_limit_is_rejected(maximum: int) -> None:
    """Un plafond négatif, booléen ou non entier ne modifie pas silencieusement le budget."""
    with pytest.raises(ValueError, match='quality-max-diffuse-regions'):
        select_diffuse_regions([], np.array([0]), (100, 100), maximum)


def test_insufficient_budget_for_size_classes_is_explicit() -> None:
    """Un échantillon ne peut pas prétendre couvrir quatre tailles avec trois régions."""
    bounds, sizes = catalogue()
    with pytest.raises(ValueError, match='4 classes'):
        select_diffuse_regions(bounds, sizes, (2000, 2000), 3)


def test_config_option_belongs_to_diffuse_processor(tmp_path: Path) -> None:
    """La récursion conserve la propriété locale, la priorité CLI/JSON et le mode exhaustif."""
    config = Config(tmp_path / 'settings.json')
    config.set('quality_max_diffuse_regions', 64)
    parser = argparse.ArgumentParser()
    config.register(RestorationEvaluationProcessor, parser)
    assert config.parse_args(parser, []).quality_max_diffuse_regions == 64
    assert config.parse_args(parser, ['--quality-max-diffuse-regions', '0']).quality_max_diffuse_regions == 0
    assert config._parameter_owner(
        RestorationEvaluationProcessor, 'quality_max_diffuse_regions',
    ) is DiffuseSignalProcessor
    parser = argparse.ArgumentParser()
    DiffuseSignalProcessor.add_arguments(parser)
    options = parser.parse_args([])
    assert options.quality_max_diffuse_regions == 256
    assert options.quality_max_flux_loss == .05  # options de la base partagée préservées


def test_sampled_metrics_equal_exhaustive_metrics_and_selection_is_fixed(tmp_path: Path) -> None:
    """Changer le candidat ne change pas les régions ; leurs mesures gardent les mêmes formules."""
    mask = np.zeros((400, 400), dtype=bool)
    for y in (50, 150, 250):
        for x in (50, 150, 250, 350):
            side = 8 if x < 200 else 16
            mask[y:y+side, x:x+side] = True
    before = np.full((1, 400, 400), .1, dtype='f4')
    before[:, mask] += .02
    after = before.copy()
    after[:, mask] -= .015
    reference = SimpleNamespace(
        data=before, object_mask=mask, star_mask=np.zeros_like(mask),
        invalid_mask=np.zeros_like(mask), sky_mask=~mask, growth=1,
        scales=(2,), sigmas=np.full_like(before, .0001), tile_size=8,
        source_sigma=4., flux_loss=.05, enforce_diffuse=False, max_diffuse_regions=4,
    )
    source = tmp_path / 'source.fits'
    context = QualityContext(source, None, before, after, reference)
    processor = DiffuseSignalProcessor(context)
    full = processor.post_process(source, None, argparse.Namespace(quality_max_diffuse_regions=0))
    sampled = processor.post_process(source, None, argparse.Namespace(quality_max_diffuse_regions=4))
    expected = {(entry['region'], entry['channel']): entry for entry in full['per_region']}
    assert len(sampled['sampling']['selected_regions']) == 4
    for entry in sampled['per_region']:
        assert entry == expected[(entry['region'], entry['channel'])]
    changed = DiffuseSignalProcessor(QualityContext(source, None, before, before * 1.1, reference))
    repeated = changed.post_process(source, None, argparse.Namespace(quality_max_diffuse_regions=4))
    assert sampled['sampling'] == repeated['sampling']
    np.testing.assert_array_equal(reference.object_mask, mask)
