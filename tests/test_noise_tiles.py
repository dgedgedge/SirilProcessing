"""Statistiques par lots et sélection spatiale sans dépendance à un GPU."""

from pathlib import Path

import numpy as np
import pytest

from lib.noise_tiles import batch_noise_statistics, spatial_noise_tiles
from lib.restoration_quality import QualityReference, robust_sigma


@pytest.mark.parametrize('shape', [(4, 32, 48), (3, 1, 160), (2, 160, 1)])
def test_batch_matches_independent_least_squares_and_correlations(
    shape: tuple[int, int, int],
) -> None:
    """Les lots reproduisent les mesures scalaires, y compris aux bords minces."""
    rng = np.random.default_rng(200)
    _, height, width = shape
    yy, xx = np.indices((height, width))
    before = .1 + .0002 * xx + .0003 * yy + rng.normal(0, .001, shape)
    after = .1 + .0001 * xx + .0004 * yy + rng.normal(0, .0005, shape)
    masks = rng.random(shape) > .2
    original_before, original_after = before.copy(), after.copy()
    sigma, correlation = batch_noise_statistics(before, after, masks)
    expected_sigma, expected_correlation = [], []
    for b, a, mask in zip(before, after, masks):
        residuals = [QualityReference._detrend(image, mask) for image in (b, a)]
        expected_sigma.append([robust_sigma(residual[mask]) for residual in residuals])
        adjacent = mask[:, :-1] & mask[:, 1:]
        expected_correlation.append([
            np.corrcoef(residual[:, :-1][adjacent], residual[:, 1:][adjacent])[0, 1]
            if adjacent.sum() > 3 else float('nan') for residual in residuals
        ])
    np.testing.assert_allclose(sigma, expected_sigma, rtol=1e-10, atol=1e-12)
    np.testing.assert_allclose(correlation, expected_correlation, rtol=1e-10, atol=1e-12)
    np.testing.assert_array_equal(before, original_before)
    np.testing.assert_array_equal(after, original_after)


def test_padding_and_excluded_invalid_pixels_do_not_change_statistics() -> None:
    """Les pixels hors masque, même NaN, n'entrent pas dans les statistiques."""
    rng = np.random.default_rng(1)
    before = rng.normal(.1, .001, (2, 32, 32))
    after = .1 + .5 * (before - .1)
    masks = np.ones(before.shape, dtype=bool)
    masks[1, 20:] = False
    masks[1, :, 27:] = False
    expected = batch_noise_statistics(before, after, masks)
    before[~masks] = np.nan
    after[~masks] = np.inf
    measured = batch_noise_statistics(before, after, masks)
    for actual, reference in zip(measured, expected):
        np.testing.assert_allclose(actual, reference, atol=1e-12)


def test_constant_tiles_have_noise_below_measurement_floor() -> None:
    """Une dispersion nulle ne devient pas artificiellement un gain de bruit."""
    values = np.full((3, 32, 32), .1)
    sigma, correlation = batch_noise_statistics(values, values, np.ones(values.shape, bool))
    assert np.max(abs(sigma)) < 1e-10
    assert correlation.shape == (3, 2)


def test_empty_masks_and_wrong_shapes_are_refused() -> None:
    """Les frontières du calcul refusent les lots invalides avant toute mesure."""
    values = np.ones((2, 8, 8))
    with pytest.raises(ValueError, match='sans pixel admissible'):
        batch_noise_statistics(values, values, np.zeros(values.shape, bool))
    with pytest.raises(ValueError, match='même forme'):
        batch_noise_statistics(values, values[:, :-1], np.ones(values.shape, bool))


@pytest.mark.parametrize('maximum', [4, 17, 64, 0])
def test_spatial_sampling_is_deterministic_bounded_and_covers_quadrants(maximum: int) -> None:
    """Le plafond conserve une couverture du champ et le même ordre reproductible."""
    tiles = tuple((slice(y, y + 32), slice(x, x + 32))
                  for y in range(0, 512, 32) for x in range(0, 512, 32))
    selected = spatial_noise_tiles(tiles, (512, 512), maximum)
    assert selected == spatial_noise_tiles(tiles, (512, 512), maximum)
    assert len(selected) == (maximum or len(tiles))
    assert {2 * int(ys.start >= 256) + int(xs.start >= 256) for ys, xs in selected} == {0, 1, 2, 3}
    assert [tiles.index(tile) for tile in selected] == sorted(tiles.index(tile) for tile in selected)


def test_sampling_keeps_all_tiles_below_limit_and_handles_missing_quadrant() -> None:
    """Une zone absente n'est pas inventée ; les petites références restent exhaustives."""
    tiles = tuple((slice(y, y + 32), slice(x, x + 32))
                  for y in range(0, 256, 32) for x in range(0, 256, 32)
                  if not (y >= 128 and x >= 128))
    assert spatial_noise_tiles(tiles, (256, 256), 256) == tiles
    selected = spatial_noise_tiles(tiles, (256, 256), 8)
    assert len(selected) == 8
    assert {2 * int(ys.start >= 128) + int(xs.start >= 128) for ys, xs in selected} == {0, 1, 2}


@pytest.mark.parametrize('shape', [(7, 32, 48), (2, 1, 160), (2, 160, 1)])
def test_cuda_batches_match_numpy_when_available(shape: tuple[int, int, int]) -> None:
    """Les mêmes lots et masques produisent les mêmes statistiques sur CUDA réel."""
    from lib.noise_tiles import NoiseTileBackend

    try:
        backend = NoiseTileBackend('cuda')
    except RuntimeError as error:
        pytest.skip(str(error))
    rng = np.random.default_rng(917)
    before = rng.normal(.1, .001, shape)
    after = rng.normal(.1, .0007, before.shape)
    masks = rng.random(before.shape) > .2
    cpu = batch_noise_statistics(before, after, masks)
    gpu = backend.statistics(before, after, masks)
    for expected, measured in zip(cpu, gpu):
        np.testing.assert_allclose(measured, expected, rtol=1e-9, atol=1e-11)


def test_non_finite_values_inside_the_mask_are_refused() -> None:
    """Seuls les pixels exclus peuvent être non finis, y compris sur le backend GPU."""
    before = np.ones((1, 16, 16))
    after = before.copy()
    after[0, 4, 4] = np.nan
    with pytest.raises(ValueError, match='doivent être finis'):
        batch_noise_statistics(before, after, np.ones(before.shape, bool))


def test_noise_math_source_is_fingerprinted_before_lazy_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L'audit de dépendances inclut le moteur mathématique même avant son chargement."""
    import argparse
    import sys
    from lib.restoration_chains import RestorationChainProcessor

    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    monkeypatch.delitem(sys.modules, 'lib.noise_tiles')
    result = RestorationChainProcessor()._dependency_manifest(argparse.Namespace(
        cosmic_model_dir=tmp_path / 'cosmic', graxpert_model_dir=tmp_path / 'graxpert',
        siril_path='nonexistent-siril',
    ))
    source = Path(__file__).resolve().parents[1] / 'lib/noise_tiles.py'
    assert str(source) in result['files_sha256']
    assert 'lib.noise_tiles' not in sys.modules
