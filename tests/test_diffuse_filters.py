"""Filtres diffus CPU/CUDA, parité des mesures et propagation des erreurs."""

import argparse
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

import torch
from lib.config import Config
from lib.diffuse_filters import DiffuseFilterBackend
from lib.quality_processors import RestorationEvaluationProcessor, DiffuseSignalProcessor


def inputs(shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Produit du signal diffus et des résidus connus sur une ouverture intérieure."""
    rng = np.random.default_rng(8)
    yy, xx = np.indices(shape)
    radius = ((yy-shape[0]/2)/(shape[0]/3))**2 + ((xx-shape[1]/2)/(shape[1]/3))**2
    signal = .008 * np.exp(-radius)
    before = (signal + rng.normal(0, .0001, shape)).astype('f4')
    difference = (.2 * signal + rng.normal(0, .00002, shape)).astype('f4')
    return before, difference, radius < 2, np.full(shape, .0001, dtype='f4')


def test_cpu_retains_original_multiscale_formulas() -> None:
    """Le moteur CPU restitue exactement les résultats SciPy des anciennes formules."""
    before, difference, mask, sigma_map = inputs((101, 93))
    scales = [2, 4, 8, 16, 32]
    backend = DiffuseFilterBackend('cpu', before.size)
    actual, corroborated = backend.multiscale(before, difference, mask, sigma_map, scales, 4., .0001)
    expected = []
    for scale in scales:
        structure = gaussian_filter(before, scale)
        removed = gaussian_filter(difference, scale)
        valid = mask & (structure > 4 * sigma_map / 8)
        if valid.sum() >= 32:
            amplitude = float(np.median(removed[valid]))
            detectable = abs(amplitude) > 3 * .0001 / max(1, min(2*np.sqrt(np.pi)*scale, 8))
            expected.append(dict(scale_px=scale, residual_median=amplitude, significant=bool(detectable)))
    assert actual == expected
    assert corroborated == any(item['significant'] for item in expected)
    assert backend.report()['name'] == 'cpu'
    assert backend.report()['filtered_scales'] == 5


@pytest.mark.parametrize('shape', [(63, 91), (129, 128), (256, 320)])
def test_cuda_multiscale_matches_cpu_when_available(shape: tuple[int, int]) -> None:
    """Les mêmes résidus et décisions sont obtenus sur des fenêtres de formes variées."""
    if not torch.cuda.is_available():
        pytest.skip('PyTorch CUDA indisponible')
    for mixed in (False, True):
        arrays = inputs(shape)
        if mixed:
            arrays = (arrays[0], arrays[1].astype('float64'), arrays[2], arrays[3])
        before_copy = arrays[0].copy()
        cpu = DiffuseFilterBackend('cpu', arrays[0].size)
        gpu = DiffuseFilterBackend('cuda', arrays[0].size)
        scales = [1.125, 2.125, 4, 8, 16, 32]
        expected, expected_flag = cpu.multiscale(*arrays, scales, 4., .0001)
        actual, flag = gpu.multiscale(*arrays, scales, 4., .0001)
        assert gpu.report()['name'] == 'cuda'
        assert flag == expected_flag
        assert len(actual) == len(expected)
        for first, last in zip(expected, actual):
            assert first['scale_px'] == last['scale_px']
            assert first['significant'] == last['significant']
            assert last['residual_median'] == pytest.approx(first['residual_median'], rel=3e-5, abs=3e-9)
        np.testing.assert_array_equal(arrays[0], before_copy)


@pytest.mark.parametrize('shape', [(1, 9), (7, 1), (17, 23)])
@pytest.mark.parametrize('dtype', [np.float32, np.float64])
def test_torch_reflection_matches_scipy(shape: tuple[int, int], dtype: type) -> None:
    """Le filtre reproduit les bords SciPy, même avec une marge dépassant l'image."""
    backend = DiffuseFilterBackend('cpu', 1000)
    backend.torch = torch
    backend.device = torch.device('cpu')
    array = np.random.default_rng(3).normal(size=shape).astype(dtype)
    for scale in (1.125, 2.125, 8.):
        result = backend._torch_gaussian(torch.as_tensor(array)[None, None], scale)[0, 0].numpy()
        np.testing.assert_allclose(result, gaussian_filter(array, scale), rtol=3e-5, atol=2e-7)


@pytest.mark.parametrize('values', [[1., 4., 2., 3.], [1., 4., 2.], [1., float('nan')]])
def test_torch_median_matches_numpy(values: list[float]) -> None:
    """Les effectifs pairs et les NaN conservent la convention NumPy."""
    backend = DiffuseFilterBackend('cpu', 100)
    backend.torch = torch
    assert backend._torch_median(torch.tensor(values)) == pytest.approx(np.median(values), nan_ok=True)


def test_missing_torch_falls_back_only_during_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """auto autorise CPU avant calcul ; un moteur GPU imposé exige PyTorch."""
    import importlib
    original = importlib.import_module

    def missing(name: str, *args: object, **kwargs: object) -> ModuleType:
        """Simule une installation sans PyTorch."""
        if name == 'torch':
            raise ImportError('torch missing')
        return original(name, *args, **kwargs)

    monkeypatch.setattr('lib.torch_backend.importlib.import_module', missing)
    assert DiffuseFilterBackend('auto', 100).report()['name'] == 'cpu'
    for policy in ('cuda', 'mps'):
        with pytest.raises(RuntimeError, match='Filtres PyTorch'):
            DiffuseFilterBackend(policy, 100)


def test_mps_selection_and_float64_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """MPS est choisi sur Mac ; float64 conserve sa précision sur CPU en auto."""
    monkeypatch.setattr(torch.cuda, 'is_available', lambda: False)
    monkeypatch.setattr(torch.backends.mps, 'is_available', lambda: True)
    backend = DiffuseFilterBackend('cpu', 100)
    backend.torch = torch
    assert backend._select_device('auto') == 'mps'
    assert backend._select_device('mps') == 'mps'
    fallback = DiffuseFilterBackend('auto', 100, requires_float64=True)
    assert fallback.name == 'cpu' and 'float64' in fallback.reason
    with pytest.raises(RuntimeError, match='float64'):
        DiffuseFilterBackend('mps', 100, requires_float64=True)


def test_real_mps_when_available() -> None:
    """Compare les décisions MPS et CPU sur un vrai Mac lorsque disponible."""
    if not torch.backends.mps.is_available():
        pytest.skip('Matériel Apple MPS indisponible')
    arrays = inputs((63, 91))
    cpu = DiffuseFilterBackend('cpu', arrays[0].size)
    mps = DiffuseFilterBackend('mps', arrays[0].size)
    expected, expected_flag = cpu.multiscale(*arrays, [2, 4, 8, 16], 4., .0001)
    actual, flag = mps.multiscale(*arrays, [2, 4, 8, 16], 4., .0001)
    assert flag == expected_flag
    for first, last in zip(expected, actual, strict=True):
        assert first['significant'] == last['significant']
        assert last['residual_median'] == pytest.approx(first['residual_median'], rel=3e-5, abs=3e-9)


def test_runtime_gpu_failure_is_not_retried_on_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une panne après initialisation conserve l'exception, sans reprise CPU."""
    backend = DiffuseFilterBackend('cpu', 10000)
    backend.name, backend.torch, backend.device = 'cuda', torch, torch.device('cpu')
    error = RuntimeError('GPU execution failed')

    def fail(*args: object, **kwargs: object) -> None:
        """Échoue après un contrôle initial réussi."""
        raise error

    monkeypatch.setattr(backend, '_torch_gaussian', fail)
    monkeypatch.setattr('lib.diffuse_filters.gaussian_filter',
                        lambda *args, **kwargs: pytest.fail('CPU restart'))
    with pytest.raises(RuntimeError, match='GPU execution failed') as raised:
        backend.multiscale(*inputs((64, 64)), [2], 4., .0001)
    assert raised.value is error


def test_backend_override_is_local_and_recursive(tmp_path: Path) -> None:
    """Le réglage GPU est déclaré par le processeur diffus et conservé par Config."""
    config = Config(tmp_path / 'settings.json')
    parser = argparse.ArgumentParser()
    config.register(RestorationEvaluationProcessor, parser)
    result = config.parse_args(parser, ['--quality-diffuse-backend', 'mps'])
    assert result.quality_diffuse_backend == 'mps'
    assert config._parameter_owner(RestorationEvaluationProcessor, 'quality_diffuse_backend') is DiffuseSignalProcessor


def test_no_structure_and_invalid_shapes() -> None:
    """L'absence de pixels structurés reste sans mesure ; des formes incompatibles échouent."""
    backend = DiffuseFilterBackend('cpu', 10000)
    before, difference, mask, sigma = inputs((64, 64))
    result, flag = backend.multiscale(np.zeros_like(before), difference, mask, sigma, [2], 4., .0001)
    assert result == [] and not flag
    with pytest.raises(ValueError, match='budget mémoire'):
        backend.multiscale(*inputs((101, 101)), [2], 4., .0001)
    with pytest.raises(ValueError, match='dimensions'):
        backend.multiscale(before[:2], difference, mask, sigma, [2], 4., .0001)


@pytest.mark.parametrize('policy', ['cuda', 'mps'])
def test_forced_unavailable_device_fails(policy: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un GPU imposé indisponible échoue sans substituer CPU ou un autre GPU."""
    monkeypatch.setattr(torch.cuda, 'is_available', lambda: False)
    monkeypatch.setattr(torch.backends.mps, 'is_available', lambda: False)
    assert DiffuseFilterBackend('auto', 100).name == 'cpu'
    with pytest.raises(RuntimeError, match='indisponible'):
        DiffuseFilterBackend(policy, 100)


@pytest.mark.parametrize('policy', ['cuda', 'mps'])
def test_memory_budget_before_processing(policy: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le contrôle initial réserve une marge de 20 % sur CUDA et MPS."""
    backend = DiffuseFilterBackend('cpu', 100)
    backend.torch, backend.name, backend.device = torch, policy, torch.device(policy)
    monkeypatch.setattr(torch.cuda, 'mem_get_info', lambda device: (10000, 20000))
    monkeypatch.setattr(torch.mps, 'recommended_max_memory', lambda: 20000)
    monkeypatch.setattr(torch.mps, 'driver_allocated_memory', lambda: 10000)
    with pytest.raises(RuntimeError, match='Mémoire'):
        backend._check_memory()
    backend.required_bytes = 8000
    backend._check_memory()
    assert backend.device_info['torch_version'] == torch.__version__
