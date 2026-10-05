"""Contrat PCC, raccordement CLI et erreurs sans dépendre des catalogues réseau."""
import argparse
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS

from lib.postprocess import PhotometricColorCalibrator, _PostProcessorSequence


@pytest.fixture
def rgb(tmp_path):
    path = tmp_path / 'source RGB.fit'
    data = np.arange(1200, dtype=np.float32).reshape(3, 20, 20)
    fits.writeto(path, data)
    return path


@pytest.fixture
def fake_siril(monkeypatch, rgb):
    runner = Mock()

    def run(script, working_dir, script_name):
        wcs = WCS(naxis=2)
        wcs.wcs.crpix = [10, 10]
        wcs.wcs.crval = [270.6, -23.0]
        wcs.wcs.cdelt = [-0.001, 0.001]
        wcs.wcs.ctype = ['RA---TAN', 'DEC--TAN']
        fits.writeto(Path(working_dir) / (f'{Path(script_name).stem}_calibrated.fit' if script_name[0].isdigit() else 'calibrated.fit'), fits.getdata(rgb) * 0.8, wcs.to_header())
        return True

    runner.run_siril_script.side_effect = run
    factory = Mock(return_value=runner)
    monkeypatch.setattr('lib.siril_utils.Siril', factory)
    return factory, runner


def arguments(*options):
    parser = argparse.ArgumentParser()
    from lib.siril_utils import add_siril_arguments
    add_siril_arguments(parser, photometry_aliases=True)
    _PostProcessorSequence().add_arguments(parser)
    return parser.parse_args(['--disable-denoise', '--disable-deconvolution', *options])


def test_object_resolution_and_all_options(rgb, tmp_path, monkeypatch, fake_siril):
    factory, runner = fake_siril
    resolver = Mock(return_value=SkyCoord(270.6, -23.0, unit='deg'))
    monkeypatch.setattr('lib.postprocess.SkyCoord.from_name', resolver)
    original = rgb.read_bytes()
    output = tmp_path / 'colors' / 'M20.fits'
    args = arguments('--enable-photometry', '--disable-gradient', '--photometry-object', 'M20',
                     '--photometry-focal', '750', '--photometry-pixelsize', '3.76',
                     '--photometry-force', '--photometry-noflip', '--photometry-downscale',
                     '--photometry-solve-catalog', 'gaia', '--photometry-catalog', 'apass',
                     '--photometry-limitmag', '14', '--photometry-output', str(output),
                     '--photometry-siril-mode', 'native', '--photometry-siril-path', '/opt/siril-cli')
    result = _PostProcessorSequence().post_process(rgb, tmp_path / 'report.json', args)['photometry']
    resolver.assert_called_once_with('M20', cache=False)
    factory.assert_called_once_with(siril_path='/opt/siril-cli', siril_mode='native')
    script = runner.run_siril_script.call_args.args[0]
    assert script.index('load ') < script.index('platesolve ') < script.index('\npcc ') < script.index('\nsave ')
    assert 'platesolve -force 270.6000000000,-23.0000000000 -focal=750 -pixelsize=3.76 -noflip -downscale -catalog=gaia' in script
    assert '\npcc -catalog=apass -limitmag=14\n' in script
    assert f'load "{rgb}"' in script
    assert result['output_image'] == str(output)
    assert result['center_coordinates_deg'] == [270.6, -23.0]
    assert WCS(fits.getheader(output)).has_celestial
    assert rgb.read_bytes() == original


def test_existing_header_and_explicit_coordinates(rgb, tmp_path, fake_siril, monkeypatch):
    resolver = Mock(side_effect=AssertionError('No name lookup expected'))
    monkeypatch.setattr('lib.postprocess.SkyCoord.from_name', resolver)
    processor = PhotometricColorCalibrator()
    result = processor.post_process(rgb, tmp_path / 'header.json')
    assert result['platesolve_command'] == 'platesolve'
    assert result['pcc_command'] == 'pcc'
    result = processor.post_process(rgb, tmp_path / 'coordinates.json',
                                    arguments('--photometry-coordinates', '270.6', '-23'))
    assert result['platesolve_command'] == 'platesolve -force 270.6000000000,-23.0000000000'
    resolver.assert_not_called()


def test_photometry_explicitly_disabled(rgb, tmp_path, monkeypatch):
    factory = Mock(side_effect=AssertionError('Siril must not start'))
    monkeypatch.setattr('lib.siril_utils.Siril', factory)
    results = _PostProcessorSequence().post_process(rgb, tmp_path / 'report.json', arguments('--disable-photometry'))
    assert set(results) == {'gradient'}
    gradient = results['gradient']
    assert len(gradient['correction']['channels']) == 3
    corrected = Path(gradient['output_image'])
    assert corrected.is_file()
    assert fits.getdata(corrected).shape == (3, 20, 20)
    factory.assert_not_called()


@pytest.mark.parametrize('options', [
    ['--photometry-focal', '0'], ['--photometry-pixelsize', '-1'],
    ['--photometry-limitmag', 'nan'], ['--photometry-coordinates', '360', '0'],
    ['--photometry-coordinates', '180', '91'],
])
def test_invalid_options(rgb, tmp_path, options, fake_siril):
    with pytest.raises(ValueError):
        PhotometricColorCalibrator().post_process(rgb, tmp_path / 'report.json', arguments(*options))
    fake_siril[0].assert_not_called()


def test_monochrome_rejected(tmp_path, fake_siril):
    image = tmp_path / 'mono.fit'
    fits.writeto(image, np.zeros((20, 20)))
    with pytest.raises(ValueError, match='RGB'):
        PhotometricColorCalibrator().post_process(image, tmp_path / 'report.json')
    fake_siril[0].assert_not_called()


def test_name_resolution_failure(rgb, tmp_path, fake_siril, monkeypatch):
    monkeypatch.setattr('lib.postprocess.SkyCoord.from_name', Mock(side_effect=OSError('offline')))
    with pytest.raises(RuntimeError, match='Recherche CDS impossible'):
        PhotometricColorCalibrator().post_process(rgb, tmp_path / 'report.json', arguments('--photometry-object', 'M20'))
    fake_siril[1].run_siril_script.assert_not_called()


@pytest.mark.parametrize('failure', ['exit', 'missing', 'no_wcs'])
def test_siril_failure_does_not_publish_stale_output(rgb, tmp_path, fake_siril, failure):
    output = tmp_path / 'existing.fit'
    output.write_bytes(b'previous output')
    factory, runner = fake_siril

    def fail(script, working_dir, script_name):
        if failure == 'no_wcs':
            fits.writeto(Path(working_dir) / (f'{Path(script_name).stem}_calibrated.fit' if script_name[0].isdigit() else 'calibrated.fit'), fits.getdata(rgb))
        return failure != 'exit'

    runner.run_siril_script.side_effect = fail
    with pytest.raises(RuntimeError):
        _PostProcessorSequence().post_process(rgb, tmp_path / 'report.json', arguments(
            '--disable-gradient', '--enable-photometry', '--photometry-output', str(output)))
    assert output.read_bytes() == b'previous output'
    assert not (tmp_path / 'report.json').exists()


def test_cannot_overwrite_input(rgb, tmp_path, fake_siril):
    with pytest.raises(ValueError, match="différente"):
        PhotometricColorCalibrator().post_process(rgb, tmp_path / 'report.json',
                                                  arguments('--photometry-output', str(rgb)))
    fake_siril[0].assert_not_called()


def test_calibrated_image_reaches_next_processor(rgb, tmp_path, fake_siril):
    from lib.processor import processor

    class InspectImage(processor):
        def add_arguments(self, parser):
            pass

        def post_process(self, input_path, output_path, args=None):
            assert input_path != rgb
            assert WCS(fits.getheader(input_path)).has_celestial
            return {'input_received': str(input_path)}

    sequence = _PostProcessorSequence([PhotometricColorCalibrator(), InspectImage()])
    result = sequence.post_process(rgb, tmp_path / 'report.json', arguments('--enable-photometry'))
    assert result['inspectimage']['input_received'] == result['photometry']['output_image']
    script, work_dir = fake_siril[1].run_siril_script.call_args.args
    assert f'cd "{work_dir}"' in script


def test_rgb_sip_wcs_validates_spatial_axes(rgb, tmp_path, fake_siril):
    _, runner = fake_siril
    generate = runner.run_siril_script.side_effect

    def generate_sip(*args, **kwargs):
        generate(*args, **kwargs)
        with fits.open(Path(args[1]) / 'calibrated.fit', mode='update') as hdul:
            header = hdul[0].header
            header['WCSAXES'] = 3
            header['CTYPE1'] = 'RA---TAN-SIP'
            header['CTYPE2'] = 'DEC--TAN-SIP'
            header['CTYPE3'] = 'RGB'
            header['A_ORDER'] = 2
            header['B_ORDER'] = 2
            header['A_2_0'] = 1.e-5
            header['B_0_2'] = -1.e-5
        return True

    runner.run_siril_script.side_effect = generate_sip
    result = PhotometricColorCalibrator().post_process(rgb, tmp_path / 'report.json')
    with fits.open(result['output_image']) as hdul:
        assert hdul[0].shape == (3, 20, 20)
        assert hdul[0].header['A_2_0'] == 1.e-5
        spatial = WCS(hdul[0].header, naxis=2)
        assert spatial.has_celestial
        assert spatial.sip is not None
        assert np.isfinite(spatial.all_pix2world([[10, 10]], 0)).all()
