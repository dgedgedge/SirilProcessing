"""Courbe AutoStretch commune, JPEG de comparaison et protection des FITS."""

import argparse
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from PIL import Image

from lib.restoration_preview import RestorationPreviewProcessor, TARGET_BACKGROUND


@pytest.fixture
def linear_source(tmp_path: Path) -> Path:
    """Produit un FITS faible avec des lignes de luminosités différentes."""
    data = np.linspace(.0008, .0012, 64 * 64, dtype=np.float32).reshape(64, 64)
    source = tmp_path / 'source.fits'
    fits.writeto(source, data, fits.Header({'ROWORDER': 'TOP-DOWN'}))
    return source


def test_median_target_and_monotonicity() -> None:
    """La médiane atteint le fond cible et la courbe conserve l'ordre des niveaux."""
    data = np.linspace(.0008, .0012, 4097, dtype=np.float32)[None, None, :]
    stretch = RestorationPreviewProcessor.fit(data)
    rendered = RestorationPreviewProcessor.apply(data, stretch)
    assert np.all(np.diff(rendered.ravel()) >= 0)
    assert np.median(rendered) == pytest.approx(TARGET_BACKGROUND, abs=2e-5)
    assert rendered.mean() > .1
    assert stretch['linked_channels'] and stretch['comparison'] == 'fixed_reference_curve'


@pytest.mark.parametrize('value', [0., 1., .001])
def test_constant_images_are_finite(value: float) -> None:
    """Un champ constant, noir ou saturé ne produit jamais de division par zéro."""
    data = np.full((3, 4, 4), value, dtype=np.float32)
    result = RestorationPreviewProcessor.apply(data, RestorationPreviewProcessor.fit(data))
    assert np.isfinite(result).all() and result.min() >= 0 and result.max() <= 1
    if value in (0., 1.):
        np.testing.assert_allclose(result, value)


def test_signed_values_and_shared_rgb_curve() -> None:
    """Les valeurs hors plage sont présentables et les canaux identiques restent égaux."""
    plane = np.linspace(-.002, 1.4, 64, dtype=np.float32).reshape(8, 8)
    data = np.stack((plane, plane, plane))
    original = data.copy()
    result = RestorationPreviewProcessor.apply(data, RestorationPreviewProcessor.fit(data))
    assert result.min() >= 0 and result.max() <= 1
    np.testing.assert_array_equal(result[0], result[2])
    np.testing.assert_array_equal(data, original)


def test_reference_curve_reused_and_jpeg_orientation(
    tmp_path: Path, linear_source: Path,
) -> None:
    """Les candidats partagent les paramètres et le JPEG suit l'affichage Siril."""
    preview = RestorationPreviewProcessor(linear_source)
    original = linear_source.read_bytes()
    before_path, after_path = tmp_path / 'before.jpg', tmp_path / 'after.jpg'
    before = preview.post_process(linear_source, before_path)
    candidate = tmp_path / 'candidate.fits'
    fits.writeto(candidate, fits.getdata(linear_source) * .8)
    after = preview.post_process(candidate, after_path)
    assert before['stretch'] == after['stretch']
    assert before['reference_image'] == after['reference_image'] == str(linear_source)
    assert after['image_path'] == str(candidate)
    assert not after['scientific_output']
    with Image.open(before_path) as image:
        assert image.mode == 'RGB' and image.size == (64, 64)
        pixels = np.asarray(image)
        assert pixels[:16].mean() > pixels[-16:].mean()
        before_mean = pixels.mean()
    with Image.open(after_path) as image:
        assert np.asarray(image).mean() < before_mean
    assert linear_source.read_bytes() == original


@pytest.mark.parametrize('data', [
    np.empty((1, 0, 4)), np.ones((2, 4, 4)),
    np.full((1, 4, 4), np.nan), np.full((3, 4, 4), np.inf),
])
def test_invalid_reference_is_rejected(data: np.ndarray) -> None:
    """Les géométries invalides et pixels non finis restent des erreurs explicites."""
    with pytest.raises(ValueError):
        RestorationPreviewProcessor.fit(data)


def test_optional_failure_disabled_cleanup_and_source_protection(
    tmp_path: Path, linear_source: Path,
) -> None:
    """Un aperçu échoué ou désactivé ne laisse pas de JPEG obsolète et protège la source."""
    preview = RestorationPreviewProcessor(linear_source)
    destination = tmp_path / 'output.jpg'
    preview.post_process(linear_source, destination)
    result = preview.optional(linear_source, destination, argparse.Namespace(restoration_previews=False))
    assert result['status'] == 'disabled' and not destination.exists()
    destination.write_bytes(b'stale preview')
    bad = tmp_path / 'bad.fits'
    bad.write_bytes(b'invalid')
    result = preview.optional(bad, destination, argparse.Namespace())
    assert result['status'] == 'failed' and not destination.exists()
    original = linear_source.read_bytes()
    with pytest.raises(ValueError):
        preview.post_process(linear_source, linear_source)
    assert preview.optional(linear_source, linear_source, argparse.Namespace())['status'] == 'failed'
    assert linear_source.read_bytes() == original


def test_rejected_candidate_and_retained_input_have_distinct_previews(
    tmp_path: Path, linear_source: Path,
) -> None:
    """Un candidat rejeté reste visible sans être présenté comme la sortie retenue."""
    candidate = tmp_path / 'candidate.fits'
    fits.writeto(candidate, fits.getdata(linear_source) * .5)
    result = dict(status='rejected', output_image=str(linear_source), candidate_image_path=str(candidate))
    RestorationPreviewProcessor(linear_source).attach(result, tmp_path / 'report.json', argparse.Namespace())
    assert result['result_preview']['image_path'] == str(linear_source)
    assert result['candidate_preview']['image_path'] == str(candidate)
    assert result['candidate_preview']['stretch'] == result['result_preview']['stretch']
    assert result['status'] == 'rejected'


def test_configuration_defaults_and_override(tmp_path: Path) -> None:
    """La désactivation est persistante et une surcharge CLI reste prioritaire."""
    from lib.config import Config
    from lib.restoration_chains import RestorationChainProcessor

    parser = argparse.ArgumentParser()
    RestorationPreviewProcessor.add_arguments(parser)
    assert parser.parse_args([]).restoration_previews
    path = tmp_path / 'config.json'
    path.write_text('{"restoration_previews": false}')
    config = Config(path)
    parser = argparse.ArgumentParser()
    config.register(RestorationChainProcessor, parser)
    assert not config.parse_args(parser, []).restoration_previews
    assert config.parse_args(parser, ['--restoration-previews']).restoration_previews


def test_sampling_bound_for_elongated_images() -> None:
    """Le plafond statistique reste valable pour une image très allongée."""
    from lib.restoration_preview import MAX_SAMPLE_PIXELS

    data = np.zeros((1, 3, 349526), dtype=np.float32)
    stretch = RestorationPreviewProcessor.fit(data)
    stride = stretch['sample_stride']
    assert data[:, ::stride, ::stride].size <= MAX_SAMPLE_PIXELS


@pytest.mark.parametrize('key,value', [
    ('scale', 0.), ('midtones', 1.), ('shadows', 1.), ('offset', float('nan')),
])
def test_invalid_stretch_parameters_are_rejected(key: str, value: float) -> None:
    """Les paramètres hors contrat sont refusés sans produire de faux aperçu."""
    data = np.ones((1, 4, 4), dtype=np.float32)
    stretch = RestorationPreviewProcessor.fit(data)
    stretch[key] = value
    with pytest.raises(ValueError, match='Paramètres AutoStretch'):
        RestorationPreviewProcessor.apply(data, stretch)
