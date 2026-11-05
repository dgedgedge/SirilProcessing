"""Parité des calculs PyTorch avec NumPy, sans solliciter de GPU."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pytest
import torch

from lib.config import Config
from lib.final_color import FinalColorProcessor
from lib.noise_tiles import NoiseTileBackend, batch_noise_statistics
from lib.quality_processors import RestorationEvaluationProcessor, SkyNoiseProcessor
from lib.torch_backend import TorchComputeBackend


def tensor_backend(kind: str = 'cuda', noise: bool = False) -> TorchComputeBackend:
    """Exécute les opérations du chemin GPU sur un tenseur CPU pour la parité."""
    backend = NoiseTileBackend('cpu') if noise else TorchComputeBackend('cpu')
    backend.name, backend.torch, backend.device = kind, torch, torch.device('cpu')
    return backend


@pytest.mark.parametrize('axis', [None, 0, 1])
def test_numpy_median_conventions(axis: int | None) -> None:
    """Les effectifs pairs, impairs, NaN et vides gardent les conventions NumPy."""
    backend = tensor_backend()
    for array in (np.array([[1., 9., np.nan, 2.], [np.nan, 6., 4., np.nan]]),
                  np.array([[1., 4., 2.], [3., 6., 5.]])):
        tensor = backend.xp.asarray(array)
        np.testing.assert_allclose(backend.numpy(backend.xp.nanmedian(tensor, axis)),
                                   np.nanmedian(array, axis=axis), equal_nan=True)
        np.testing.assert_allclose(backend.numpy(backend.xp.median(tensor, axis)),
                                   np.median(array, axis=axis), equal_nan=True)
    assert np.isnan(backend.xp.nanmedian(torch.empty(0)).item())
    assert np.isnan(backend.xp.nanmedian(torch.tensor([float('nan')])).item())


def test_percentiles_match_numpy() -> None:
    """L'interpolation conserve les percentiles pairs, impairs et non entiers."""
    backend = tensor_backend()
    for size in (1, 14, 19):
        data = np.random.default_rng(43).normal(size=size).astype('f4')
        levels = [0., 25., 50., 75., 98., 99.9, 100.]
        actual = backend.numpy(backend.xp.percentile(backend.xp.asarray(data), levels))
        np.testing.assert_allclose(actual, np.percentile(data, levels), rtol=1e-6, atol=2e-7)


@pytest.mark.parametrize('mode', ['human', 'even'])
@pytest.mark.parametrize('strength', [0., 1., 3.])
def test_tensor_color_matches_reference(mode: str, strength: float) -> None:
    """Le rendu tensoriel garde la couleur, le ciel, le cœur et la luminosité."""
    yy, xx = np.mgrid[-1:1:64j, -1:1:80j]
    signal = .3 * np.exp(-5*(xx**2+yy**2))
    sky = .04 + np.random.default_rng(43).normal(0, .0005, xx.shape)
    data = np.array([sky+1.025*signal, sky+signal, sky+.975*signal], dtype='f4')
    data[:, 30, 40] = [1., .5, 0.]
    original = data.copy()
    expected, reference = FinalColorProcessor.render(data, strength, mode)
    actual, report = FinalColorProcessor.render(data, strength, mode, tensor_backend())
    np.testing.assert_allclose(actual, expected, rtol=3e-5, atol=3e-6)
    np.testing.assert_array_equal(data, original)
    assert report['status'] == reference['status']
    assert report.get('sky_median_gain', 1) == 1
    assert report.get('luminance_max_abs', 0) < 5e-7
    assert np.isfinite(actual).all() and actual.min() >= 0 and actual.max() <= 1


@pytest.mark.parametrize('kind,tolerance', [('cuda', 1e-9), ('mps', 3e-6)])
@pytest.mark.parametrize('shape', [(4, 32, 48), (2, 1, 160), (2, 160, 1)])
def test_tensor_noise_matches_reference(kind: str, tolerance: float, shape: tuple[int, int, int]) -> None:
    """CUDA double et MPS hybride restituent le bruit faible malgré un fort gradient."""
    rng = np.random.default_rng(77)
    _, height, width = shape
    yy, xx = np.indices((height, width))
    before = 1000 + xx*.2 + yy*.3 + rng.normal(0, 1e-5, shape)
    after = 1000 + xx*.1 + yy*.4 + rng.normal(0, .5e-5, shape)
    masks = rng.random(shape) > .2
    before[~masks], after[~masks] = np.nan, np.inf
    expected = batch_noise_statistics(before, after, masks)
    actual = tensor_backend(kind, noise=True).statistics(before, after, masks)
    np.testing.assert_allclose(actual[0], expected[0], rtol=max(tolerance, 1e-7), atol=1e-12)
    np.testing.assert_allclose(actual[1], expected[1], rtol=tolerance, atol=1e-7, equal_nan=True)


def test_local_backend_options_are_recursive_and_persistent(tmp_path: Path) -> None:
    """CLI prime sur JSON ; chaque classe conserve la propriété de son option."""
    settings = tmp_path / 'config.json'
    settings.write_text('{"quality_noise_backend":"cpu","final_color_backend":"cpu"}')
    config = Config(settings)
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(RestorationEvaluationProcessor, parser)
    config.register(FinalColorProcessor, parser)
    result = config.parse_args(parser, ['--quality-noise-backend', 'mps', '--final-color-backend', 'mps'])
    assert result.quality_noise_backend == result.final_color_backend == 'mps'
    assert config._parameter_owner(RestorationEvaluationProcessor, 'quality_noise_backend') is SkyNoiseProcessor
    assert json.loads(settings.read_text())['quality_noise_backend'] == 'cpu'
    config.bind_parameters(result)
    first = FinalColorProcessor()
    result.final_color_backend = 'cpu'
    assert first._get_args().final_color_backend == 'mps'
    result.save_config = True
    config.save_requested(result)
    assert json.loads(settings.read_text())['quality_noise_backend'] == 'mps'


@pytest.mark.parametrize('kind', ['cuda', 'mps'])
def test_real_devices_when_available(kind: str) -> None:
    """Exécute les deux moteurs sur matériel réel lorsque celui-ci est disponible."""
    available = torch.cuda.is_available() if kind == 'cuda' else torch.backends.mps.is_available()
    if not available:
        pytest.skip(f'Matériel PyTorch {kind} indisponible')
    rng = np.random.default_rng(3)
    before = rng.normal(.1, .001, (3, 16, 32))
    after = .1 + .6*(before-.1)
    masks = rng.random(before.shape) > .2
    noise = NoiseTileBackend(kind)
    actual = noise.statistics(before, after, masks)
    expected = batch_noise_statistics(before, after, masks)
    np.testing.assert_allclose(actual[0], expected[0], rtol=3e-6, atol=1e-10)
    np.testing.assert_allclose(actual[1], expected[1], rtol=3e-6, atol=2e-7)
    data = rng.uniform(.01, .8, (3, 32, 40)).astype('f4')
    colored, report = FinalColorProcessor.render(data, 3, backend=TorchComputeBackend(kind))
    reference, audit = FinalColorProcessor.render(data, 3)
    np.testing.assert_allclose(colored, reference, rtol=3e-5, atol=3e-6)
    assert report['status'] == audit['status']


def test_noise_runtime_failure_keeps_original_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le chemin hybride MPS ne redémarre pas sur CPU après une panne GPU."""
    backend = tensor_backend('mps', noise=True)
    failure = RuntimeError('GPU statistics failed')

    def fail(*args: object, **kwargs: object) -> None:
        """Simule une panne du GPU après le retrait CPU du plan."""
        raise failure

    monkeypatch.setattr('lib.noise_tiles._residual_statistics', fail)
    with pytest.raises(RuntimeError, match='GPU statistics failed') as raised:
        backend.statistics(np.ones((1, 8, 8)), np.ones((1, 8, 8)), np.ones((1, 8, 8), dtype=bool))
    assert raised.value is failure


def test_tensor_color_gray_black_and_constant_support() -> None:
    """Une distribution sans couleur fiable reste inchangée sur le chemin tensoriel."""
    for values in (np.zeros((3, 12, 16), dtype='f4'), np.full((3, 12, 16), .1, dtype='f4')):
        result, report = FinalColorProcessor.render(values, 3, backend=tensor_backend())
        np.testing.assert_array_equal(result, values)
        assert report['status'] == 'unchanged'


def test_tensor_gamut_guard_restores_whole_pixel() -> None:
    """La correction numérique tensorielle conserve les trois canaux d'origine."""
    backend = tensor_backend()
    image = backend.xp.asarray(np.full((3, 128, 160), .1, dtype='f4'))
    output, gain = image.clone(), backend.xp.asarray(np.ones((128, 160), dtype='f4'))
    output[0, 2, 3] = -1e-9
    report = FinalColorProcessor._guard_rendered_gamut(image, output, gain, backend)
    np.testing.assert_array_equal(backend.numpy(output), backend.numpy(image))
    assert report['restored_pixels'] == report['corrected_rgb_values'] == 1


def test_hybrid_noise_empty_mask_and_excluded_invalid_pixels() -> None:
    """La préparation MPS refuse un masque vide et exclut les valeurs non finies hors masque."""
    backend = tensor_backend('mps', noise=True)
    before = np.ones((1, 16, 16))
    masks = np.zeros(before.shape, dtype=bool)
    with pytest.raises(ValueError, match='sans pixel admissible'):
        backend.statistics(before, before, masks)
    masks[:, :, :8] = True
    before[~masks] = np.nan
    sigma, _ = backend.statistics(before, before, masks)
    assert np.isfinite(sigma).all() and sigma.max() < 1e-10
