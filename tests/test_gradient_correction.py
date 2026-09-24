import argparse
from pathlib import Path
import numpy as np
import pytest
from astropy.io import fits
from lib.postprocess import GradientExtractor


@pytest.mark.parametrize('method', ['rbf', 'polynomial'])
@pytest.mark.parametrize('rgb', [False, True])
def test_correction_preserves_stars_metadata_and_source(tmp_path, rgb, method):
    y, x = np.mgrid[:80, :80]
    background = 100 + .3*(x-40) - .2*(y-40)
    stars = np.zeros((80, 80))
    stars[10::15, 10::15] = 1000
    data = background + stars
    if rgb:
        data = np.stack([data, 200 + 2*(background-100) + stars,
                         300 - (background-100) + stars])
    data[..., 0, 0] = np.nan
    source = tmp_path/'source.fits'
    fits.HDUList([fits.PrimaryHDU(data, header=fits.Header({'CRPIX1':40., 'OBJECT':'test'})),
                  fits.ImageHDU(np.ones((2, 2)), name='AUX')]).writeto(source)
    original = source.read_bytes()
    processor = GradientExtractor(create_measurement_image=False, method=method)
    processor.set_from_args(argparse.Namespace(gradient_min_order=1, gradient_max_order=1,
                                              gradient_sample_fraction=1.))
    result = processor.post_process(source, tmp_path/'report.json')
    with fits.open(result['output_image']) as corrected:
        expected = (np.array([100, 200, 300])[:, None, None] + stars) if rgb else 100 + stars
        np.testing.assert_allclose(corrected[0].data[..., 1:, 1:], expected[..., 1:, 1:], atol=0.5 if method == 'rbf' else 1e-4)
        assert np.isnan(corrected[0].data[..., 0, 0]).all()
        assert corrected[0].header['CRPIX1'] == 40
        assert corrected[0].header['OBJECT'] == 'test'
        assert corrected['AUX'].data.shape == (2, 2)
    assert source.read_bytes() == original
    assert Path(result['output_image']).is_file()


def test_insufficient_finite_pixels_fails_without_output(tmp_path):
    source = tmp_path/'invalid.fit'
    fits.writeto(source, np.full((10, 10), np.nan))
    processor = GradientExtractor(create_measurement_image=False)
    with pytest.raises(RuntimeError):
        processor.post_process(source, tmp_path/'report.json')
    assert not list(tmp_path.glob('*corrected.fits'))


def test_rbf_default_corrects_curved_background_without_polynomial(tmp_path, monkeypatch):
    y, x = np.mgrid[:96, :96]
    background = 100 + 10*np.sin(x/30)*np.cos(y/35)
    source = tmp_path/'curved.fit'
    fits.writeto(source, background.astype(np.float32))
    processor = GradientExtractor(create_measurement_image=False)
    parser = argparse.ArgumentParser()
    processor.add_arguments(parser)
    assert parser.parse_args([]).gradient_method == 'rbf'
    processor.set_from_args(parser.parse_args([]))
    def forbidden(*args, **kwargs):
        raise AssertionError('RBF must not depend on a polynomial fit')
    monkeypatch.setattr(processor, '_fit_polynomial_surface', forbidden)
    result = processor.post_process(source, tmp_path/'report.json')
    corrected = fits.getdata(result['output_image'])
    assert np.std(corrected) < np.std(background)*0.1
    assert result['correction']['method'] == 'rbf'
    assert corrected[48, 48] == pytest.approx(background[48, 48], abs=1e-4)


def test_method_argument_overrides_default(tmp_path):
    source = tmp_path/'plane.fit'
    y, x = np.mgrid[:20, :20]
    fits.writeto(source, (100 + x + y).astype(np.float32))
    processor = GradientExtractor(create_measurement_image=False)
    parser = argparse.ArgumentParser()
    processor.add_arguments(parser)
    processor.set_from_args(parser.parse_args(['--gradient-method', 'polynomial']))
    result = processor.post_process(source, tmp_path/'report.json')
    assert result['method'] == 'polynomial'
    with pytest.raises(SystemExit):
        parser.parse_args(['--gradient-method', 'invalid'])


def test_siril_screenshot_defaults_and_complete_grid(tmp_path):
    processor = GradientExtractor()
    parser = argparse.ArgumentParser()
    processor.add_arguments(parser)
    args = parser.parse_args([])
    assert (args.gradient_method, args.gradient_smoothing,
            args.gradient_samples_per_line, args.gradient_grid_tolerance,
            args.gradient_keep_all_samples) == ('rbf', 0.5, 20, 2.0, False)
    _, fit = processor._fit_rbf_background(np.ones((80, 80)))
    assert fit['n_background_samples'] == 400
    assert len(fit['measurement_points']) == 400
    assert fit['smoothing'] == 0.5


def test_grid_tolerance_rejects_bright_regions():
    plane = np.ones((80, 80))
    plane[20:40, 20:40] = 100
    _, filtered = GradientExtractor()._fit_rbf_background(plane)
    _, unfiltered = GradientExtractor(keep_all_samples=True)._fit_rbf_background(plane)
    assert filtered['rejected_samples'] == 25
    assert filtered['n_background_samples'] == 375
    assert unfiltered['n_background_samples'] == 400


def test_rbf_options_forwarded_to_correction(tmp_path):
    source = tmp_path/'flat.fit'
    fits.writeto(source, np.ones((40, 80), dtype=np.float32))
    processor = GradientExtractor(create_measurement_image=False)
    parser = argparse.ArgumentParser()
    processor.add_arguments(parser)
    processor.set_from_args(parser.parse_args([
        '--gradient-smoothing', '0.2', '--gradient-samples-per-line', '8',
        '--gradient-grid-tolerance', '3', '--gradient-keep-all-samples']))
    result = processor.post_process(source, tmp_path/'report.json')
    correction = result['correction']
    assert correction['operation'] == 'subtraction' and correction['dither'] is False
    fit = correction['channels'][0]
    assert fit['smoothing'] == 0.2
    assert fit['grid_tolerance'] == 3
    assert fit['keep_all_samples'] is True
    assert fit['n_background_samples'] == 32


@pytest.mark.parametrize('options', [
    {'smoothing': -0.1}, {'smoothing': float('nan')}, {'smoothing': 1.1},
    {'samples_per_line': 1}, {'grid_tolerance': -1}, {'grid_tolerance': float('inf')},
])
def test_invalid_rbf_configuration(options):
    with pytest.raises(ValueError):
        GradientExtractor(**options)._validate_options()
