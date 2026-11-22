"""Prétraitement, tuilage FITS et non-régression des vrais poids sur CUDA."""

import os
from pathlib import Path
from unittest.mock import Mock
from numpy.typing import NDArray

import numpy as np
import pytest
from astropy.io import fits


torch = pytest.importorskip('torch')

from lib.cosmic_clarity.inference import (  # noqa: E402
    CosmicClarityEngine, midtone, restore_plane, select_device,
)
from lib.cosmic_clarity.models import DEFAULT_MODEL_DIR, manifest  # noqa: E402


@pytest.mark.parametrize('shape', [(1, 1), (17, 35), (224, 224), (257, 401)])
def test_tile_identity_including_edges(shape: tuple[int, int]) -> None:
    """Un réseau identité retrouve tous les pixels, sans couture ni bord perdu."""
    plane = np.random.default_rng(24).random(shape, dtype=np.float32)
    output = restore_plane(plane, torch.nn.Identity(), torch.device('cpu'))
    np.testing.assert_allclose(output, plane, atol=1e-7)


def test_midtone_inverse() -> None:
    """Le transfert de médiane est réversible et conserve les bornes."""
    data = np.linspace(0, 1, 257, dtype=np.float32)
    transformed = midtone(data, .02, .25)
    np.testing.assert_allclose(midtone(transformed, .25, .02), data, atol=3e-6)
    assert transformed[0] == 0
    assert transformed[-1] == pytest.approx(1)
    np.testing.assert_array_equal(midtone(data, 0, .25), data)


def test_cuda_has_no_implicit_cpu_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un CUDA indisponible échoue explicitement ; CPU reste un choix possible."""
    monkeypatch.setattr(torch.cuda, 'is_available', lambda: False)
    with pytest.raises(RuntimeError, match='CUDA indisponible'):
        select_device('cuda')
    assert select_device('cpu').type == 'cpu'


@pytest.mark.parametrize('rgb,unsigned', [(False, False), (True, False), (False, True)])
def test_fits_units_headers_and_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rgb: bool, unsigned: bool,
) -> None:
    """Le calcul identité conserve échelle Siril, dimensions, WCS, HDU et source."""
    data = np.linspace(.01, .035, 81, dtype=np.float32).reshape(9, 9)
    if rgb:
        data = np.stack((data, data * 1.2, data * 1.4))
    if unsigned:
        data = (data * 65535).astype(np.uint16)
    source = tmp_path / 'source.fit'
    fits.HDUList([fits.PrimaryHDU(data, fits.Header({'CRVAL1': 123.0})),
                  fits.ImageHDU(np.ones((2, 2)), name='EXTRA')]).writeto(source)
    original = source.read_bytes()
    monkeypatch.setattr(CosmicClarityEngine, '_apply', lambda self, planes, name: planes.copy())
    engine = CosmicClarityEngine(tmp_path, 'cpu')
    output = tmp_path / 'result.fit'
    result = engine.process(source, output, 'denoise')
    assert result['temporary_stretch'] == ([True] * (3 if rgb else 1))
    with fits.open(output) as hdul:
        assert hdul[0].data.shape == data.shape
        expected = data / 65535 if unsigned else data
        np.testing.assert_allclose(hdul[0].data, expected, rtol=2e-5, atol=1e-6)
        assert hdul[0].header['CRVAL1'] == 123
        np.testing.assert_array_equal(hdul['EXTRA'].data, np.ones((2, 2)))
    assert source.read_bytes() == original


@pytest.mark.parametrize('bad', ['constant', 'nan', 'cfa', 'shape'])
def test_invalid_fits_fails_before_inference(tmp_path: Path, bad: str) -> None:
    """Les entrées incompatibles sont rejetées avant le chargement des poids."""
    data = np.linspace(0, .1, 64, dtype=np.float32).reshape(8, 8)
    header = fits.Header()
    if bad == 'constant':
        data[:] = 0
    elif bad == 'nan':
        data[0, 0] = np.nan
    elif bad == 'cfa':
        header['BAYERPAT'] = 'RGGB'
    else:
        data = data[None]
    source = tmp_path / 'source.fit'
    fits.writeto(source, data, header)
    with pytest.raises(ValueError):
        CosmicClarityEngine(tmp_path, 'cpu').process(source, tmp_path / 'out.fit', 'sharpen')
    assert not (tmp_path / 'out.fit').exists()


@pytest.mark.skipif(not torch.cuda.is_available(),
                    reason='CUDA indisponible dans cet environnement')
def test_real_weights_cuda(tmp_path: Path) -> None:
    """Vérifie automatiquement les vrais poids lorsque CUDA est disponible.

    Les poids manquants sont téléchargés par le moteur. Une erreur de chargement
    ou de calcul échoue au lieu d’ignorer le test ; aucune activation manuelle.
    """
    assert torch.cuda.is_available()
    directory = Path(os.environ.get('COSMIC_MODEL_DIR', DEFAULT_MODEL_DIR))
    engine = CosmicClarityEngine(directory, 'cuda')
    for name in manifest():
        model = engine._load(name)
        with torch.inference_mode():
            result = model(torch.full((1, 3, 32, 32), .1, device='cuda'))
        assert tuple(result.shape) == (1, 3, 32, 32)
        assert torch.isfinite(result).all()
        assert result.min() >= 0 and result.max() <= 1
        del model
    rng = np.random.default_rng(41)
    yy, xx = np.indices((64, 64))
    image = (.015 + .25 * np.exp(-((xx - 32)**2 + (yy - 32)**2) / 8)
             + rng.normal(0, .001, (64, 64))).astype(np.float32)
    source = tmp_path / 'synthetic.fit'
    fits.writeto(source, image)
    original = source.read_bytes()
    sharpened = tmp_path / 'sharpened.fit'
    denoised = tmp_path / 'denoised.fit'
    sharp = engine.process(source, sharpened, 'sharpen')
    denoise = engine.process(sharpened, denoised, 'denoise')
    for output in (sharpened, denoised):
        pixels = fits.getdata(output)
        assert pixels.shape == image.shape
        assert np.isfinite(pixels).all()
        assert not np.array_equal(pixels, image)
    assert sharp['device'] == denoise['device'] == 'cuda'
    assert source.read_bytes() == original


def test_integer_candidate_uses_siril_scale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le bruit avant/après ne change pas d'échelle en passant d'entier à float32."""
    from lib.deconvolution_quality import _normalized_data
    data = np.arange(1024, dtype=np.uint16).reshape(32, 32) + 1000
    source, target = tmp_path / 'source.fit', tmp_path / 'out.fit'
    fits.writeto(source, data)
    monkeypatch.setattr(CosmicClarityEngine, '_apply', lambda self, planes, name: planes.copy())
    CosmicClarityEngine(tmp_path, 'cpu').process(source, target, 'denoise')
    np.testing.assert_allclose(_normalized_data(fits.getdata(source)),
                               _normalized_data(fits.getdata(target)), atol=1e-7)


def test_radius_interpolation_and_stellar_then_diffuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vérifie numériquement l'interpolation 2/4 et le mélange séquentiel."""
    from lib.cosmic_clarity.models import STELLAR_MODEL, NONSTELLAR_MODELS
    data = np.linspace(.2, .6, 64, dtype=np.float32).reshape(8, 8)
    source, target = tmp_path / 'source.fit', tmp_path / 'out.fit'
    fits.writeto(source, data)
    calls = []

    def apply(self: CosmicClarityEngine, planes: np.ndarray, name: str) -> np.ndarray:
        """Simule trois prédictions constantes avec contrôle des entrées."""
        calls.append((name, planes.copy()))
        value = {STELLAR_MODEL: .6, NONSTELLAR_MODELS[2]: .2,
                 NONSTELLAR_MODELS[4]: .4}[name]
        return np.full_like(planes, value)

    monkeypatch.setattr(CosmicClarityEngine, '_apply', apply)
    CosmicClarityEngine(tmp_path, 'cpu').process(source, target, 'sharpen', radius=3)
    stellar = .5 * data + .5 * .6
    np.testing.assert_allclose(calls[1][1][0], stellar)
    np.testing.assert_allclose(calls[2][1][0], stellar)
    np.testing.assert_allclose(fits.getdata(target), .5 * stellar + .5 * .3)
    assert [c[0] for c in calls] == [STELLAR_MODEL, NONSTELLAR_MODELS[2], NONSTELLAR_MODELS[4]]


@pytest.mark.parametrize('operation,mode,plane_count', [
    ('sharpen', 'luminance', 1), ('sharpen', 'separate', 3),
    ('denoise', 'luminance', 1), ('denoise', 'full', 1),
    ('denoise', 'separate', 3),
])
def test_author_color_modes_and_neural_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    operation: str, mode: str, plane_count: int,
) -> None:
    """Vérifie les plans transmis au réseau et la chrominance après modification de Y."""
    from lib.cosmic_clarity.color import extract_luminance
    base = np.linspace(.25, .45, 64, dtype=np.float32).reshape(8, 8)
    rgb = np.stack((base, base + .05, base + .1))
    source = tmp_path / 'rgb.fit'
    output = tmp_path / 'result.fit'
    fits.writeto(source, rgb)
    original = source.read_bytes()
    calls = []

    def apply(
        self: CosmicClarityEngine, planes: NDArray[np.float32], name: str,
    ) -> NDArray[np.float32]:
        """Décale les plans pour rendre visible le routage luminance/canaux séparés."""
        calls.append(planes.copy())
        return planes + .04

    monkeypatch.setattr(CosmicClarityEngine, '_apply', apply)
    result = CosmicClarityEngine(tmp_path, 'cpu').process(
        source, output, operation, sharpen_mode=mode if operation == 'sharpen' else 'luminance',
        denoise_mode=mode if operation == 'denoise' else 'luminance',
        stellar_amount=.5, nonstellar_amount=0, denoise_amount=.5,
        color_denoise_amount=0,
    )
    assert len(calls) == 1 and calls[0].shape == (plane_count, 8, 8)
    reference = extract_luminance(rgb)
    if mode != 'separate':
        np.testing.assert_allclose(calls[0][0], reference[0], atol=1e-7)
    else:
        np.testing.assert_array_equal(calls[0], rgb)
    restored = fits.getdata(output)
    np.testing.assert_allclose(restored, rgb + .02, atol=1e-6)
    for actual, expected in zip(extract_luminance(restored)[1:], reference[1:]):
        np.testing.assert_allclose(actual, expected, atol=1e-6)
    assert result['channels'] == mode
    assert result['temporary_stretch'] == [False] * 3
    assert source.read_bytes() == original


def test_full_mode_reduces_chroma_noise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le mode full lisse la chrominance même avec une intensité neuronale nulle."""
    from lib.cosmic_clarity.color import extract_luminance, merge_luminance
    rng = np.random.default_rng(7)
    y = np.linspace(.3, .6, 1024, dtype=np.float32).reshape(32, 32)
    cb = rng.uniform(.48, .52, y.shape).astype(np.float32)
    cr = np.full_like(y, .5)
    source = tmp_path / 'rgb.fit'
    fits.writeto(source, merge_luminance(y, cb, cr))
    loader = Mock()
    monkeypatch.setattr(CosmicClarityEngine, '_load', loader)
    target = tmp_path / 'full.fit'
    CosmicClarityEngine(tmp_path, 'cpu').process(
        source, target, 'denoise', denoise_mode='full',
        denoise_amount=0, color_denoise_amount=.5,
    )
    result_y, result_cb, _ = extract_luminance(fits.getdata(target))
    full_target = tmp_path / 'full_color.fit'
    CosmicClarityEngine(tmp_path, 'cpu').process(
        source, full_target, 'denoise', denoise_mode='full',
        denoise_amount=0, color_denoise_amount=1.,
    )
    np.testing.assert_allclose(
        fits.getdata(target), .5 * (fits.getdata(source) + fits.getdata(full_target)),
        atol=2e-7,
    )
    assert result_cb.std() < cb.std()
    np.testing.assert_allclose(result_y, y, atol=1e-6)
    loader.assert_not_called()


@pytest.mark.parametrize('settings', [
    {'sharpen_mode': 'rgb'}, {'denoise_mode': 'unknown'},
    {'color_denoise_amount': -1}, {'color_denoise_amount': float('nan')},
])
def test_invalid_color_modes_fail_before_output(
    tmp_path: Path, settings: dict[str, str | float],
) -> None:
    """Valide les modes et l’intensité couleur avant toute lecture ou inférence."""
    target = tmp_path / 'result.fit'
    with pytest.raises(ValueError):
        CosmicClarityEngine(tmp_path, 'cpu').process(
            tmp_path / 'missing.fit', target, 'denoise', **settings,
        )
    assert not target.exists()


@pytest.mark.parametrize('channel_medians,stretch', [
    ((.02, .3, .4), False), ((.02, .03, .4), True),
])
def test_stretch_decision_is_global_with_common_minimum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    channel_medians: tuple[float, float, float], stretch: bool,
) -> None:
    """Le seuil porte sur le RGB complet et tous les canaux partagent le minimum."""
    ramp = np.linspace(-.01, .01, 64, dtype=np.float32).reshape(8, 8)
    rgb = np.stack([ramp + median for median in channel_medians])
    source, target = tmp_path / 'rgb.fit', tmp_path / 'out.fit'
    fits.writeto(source, rgb)
    captured = []

    def apply(
        self: CosmicClarityEngine, planes: NDArray[np.float32], name: str,
    ) -> NDArray[np.float32]:
        """Capture le RGB préparé sans modifier les valeurs pour tester l’inverse."""
        captured.append(planes.copy())
        return planes.copy()

    monkeypatch.setattr(CosmicClarityEngine, '_apply', apply)
    result = CosmicClarityEngine(tmp_path, 'cpu').process(
        source, target, 'denoise', denoise_mode='separate',
    )
    assert result['temporary_stretch'] == [stretch] * 3
    if stretch:
        minimum = float(rgb.min())
        for i, plane in enumerate(rgb):
            median = float(np.median(plane - minimum))
            np.testing.assert_allclose(captured[0][i],
                                       midtone(plane - minimum, median, .25), atol=1e-7)
    else:
        np.testing.assert_array_equal(captured[0], rgb)
    # La médiane d’un échantillon pair ne commute pas exactement avec T.
    np.testing.assert_allclose(fits.getdata(target), rgb, atol=3e-6)


def test_load_downloads_missing_weight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le chargement récupère seulement le poids requis et réutilise sa copie conforme."""
    import hashlib
    import io
    from lib.cosmic_clarity import models, inference

    payload = b'checkpoint'
    entry = {'url': 'https://example.invalid/model', 'size': len(payload),
             'sha256': hashlib.sha256(payload).hexdigest()}
    monkeypatch.setattr(models, 'manifest', lambda: {'test.pth': entry})
    monkeypatch.setattr(inference, 'manifest', models.manifest)
    download = Mock(side_effect=lambda *args, **kwargs: io.BytesIO(payload))
    monkeypatch.setattr(models, 'urlopen', download)
    load = Mock(return_value={'model_state_dict': {}})
    monkeypatch.setattr(inference.torch, 'load', load)
    network = Mock()
    monkeypatch.setattr(inference, 'RestorationCNN', Mock(return_value=network))
    engine = CosmicClarityEngine(tmp_path / 'custom', 'cpu')
    engine._load('test.pth')
    engine._load('test.pth')
    download.assert_called_once()
    assert (tmp_path / 'custom/test.pth').read_bytes() == payload
    load.assert_called_with(tmp_path / 'custom/test.pth', map_location='cpu', weights_only=True)
    assert engine.used_models['test.pth'] == entry['sha256']


@pytest.mark.parametrize('mode', ['mono', 'luminance', 'full', 'separate'])
@pytest.mark.parametrize('units', ['faint_float', 'bright_float', 'signed_float', 'uint16'])
def test_denoise_strength_blends_full_endpoint_in_input_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, units: str,
) -> None:
    """Le curseur interpole l'entrée et la sortie complète après inversion MTF."""
    base = np.random.default_rng(513).uniform(.001, .004, (16, 16)).astype(np.float32)
    base[7:9, 7:9] = .03
    data = base if mode == 'mono' else np.stack((base, base * 1.2, base * .8))
    if units == 'bright_float':
        data = data * 3 + .2
    elif units == 'signed_float':
        data = data * 1000 - 2
    elif units == 'uint16':
        data = (data * 65535).astype(np.uint16)
    source = tmp_path / 'source.fits'
    fits.HDUList([
        fits.PrimaryHDU(data, fits.Header({'CRPIX1': 12.5, 'OBJECT': 'Synthetic'})),
        fits.ImageHDU(np.arange(6), name='AUX'),
    ]).writeto(source)
    original = source.read_bytes()
    calls = []

    def infer(
        self: CosmicClarityEngine, planes: NDArray[np.float32], name: str,
    ) -> NDArray[np.float32]:
        """Lisse fortement les résidus préparés pour rendre la MTF non commutative."""
        calls.append(name)
        median = np.median(planes, axis=(1, 2), keepdims=True)
        return (.3 * planes + .7 * median).astype(np.float32)

    monkeypatch.setattr(CosmicClarityEngine, '_apply', infer)
    engine = CosmicClarityEngine(tmp_path, 'cpu')
    kwargs = {'denoise_mode': 'luminance' if mode == 'mono' else mode}
    endpoint = tmp_path / 'full.fits'
    engine.process(source, endpoint, 'denoise', denoise_amount=1, **kwargs)
    full = fits.getdata(endpoint)
    normalized_input = data.astype(np.float32) / (65535 if units == 'uint16' else 1)
    assert not np.allclose(full, normalized_input, atol=1e-7)
    for strength in (0., .25, .5, 1.):
        destination = tmp_path / f'blend_{strength}.fits'
        before_calls = len(calls)
        audit = engine.process(source, destination, 'denoise', denoise_amount=strength, **kwargs)
        expected = (1 - strength) * normalized_input + strength * full
        np.testing.assert_allclose(fits.getdata(destination), expected, rtol=2e-6, atol=2e-7)
        assert audit['blend_domain'] == 'linear_input_units'
        assert audit['denoise_amount'] == strength
        if strength == 0:
            np.testing.assert_array_equal(fits.getdata(destination), normalized_input)
            assert len(calls) == before_calls
        with fits.open(destination) as hdul:
            assert hdul[0].header['CRPIX1'] == 12.5
            np.testing.assert_array_equal(hdul['AUX'].data, np.arange(6))
    assert source.read_bytes() == original


@pytest.mark.parametrize('amount,color', [(0., .5), (.25, .75), (.75, .25), (.5, 0.)])
def test_independent_chroma_strength_is_blended_after_inverse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, amount: float, color: float,
) -> None:
    """Les intensités Y et couleur pondèrent leurs résidus en unités d'entrée."""
    base = np.random.default_rng(514).uniform(.001, .004, (3, 16, 16)).astype(np.float32)
    source = tmp_path / 'source.fits'
    fits.writeto(source, base)

    def infer(
        self: CosmicClarityEngine, planes: NDArray[np.float32], name: str,
    ) -> NDArray[np.float32]:
        """Renvoie une contraction déterministe autour de la médiane préparée."""
        return .5 * planes + .5 * np.median(planes, axis=(1, 2), keepdims=True)

    monkeypatch.setattr(CosmicClarityEngine, '_apply', infer)
    engine = CosmicClarityEngine(tmp_path, 'cpu')
    luminance, full, mixed = [tmp_path / name for name in ('luma.fits', 'full.fits', 'mixed.fits')]
    # À Y nul, les endpoints de couleur ne doivent pas charger le modèle Y.
    endpoint_amount = 0. if amount == 0 else 1.
    engine.process(source, luminance, 'denoise', denoise_mode='full',
                   denoise_amount=endpoint_amount, color_denoise_amount=0.)
    engine.process(source, full, 'denoise', denoise_mode='full',
                   denoise_amount=endpoint_amount, color_denoise_amount=1.)
    engine.process(source, mixed, 'denoise', denoise_mode='full',
                   denoise_amount=amount, color_denoise_amount=color)
    # Pour amount=0, le seul résidu non nul est celui de couleur.
    luma_data = fits.getdata(luminance)
    expected = base + amount * (luma_data - base) + color * (fits.getdata(full) - luma_data)
    np.testing.assert_allclose(fits.getdata(mixed), expected, atol=2e-7, rtol=2e-5)
