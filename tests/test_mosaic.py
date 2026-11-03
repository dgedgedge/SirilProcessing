from pathlib import Path

import pytest

from lib.mosaic import Mosaic
from lib.processor import processor


def test_mosaic_processor_passes_image_to_next_step(tmp_path, monkeypatch, gradient_stub):
    import argparse
    import json
    from lib.postprocess import _PostProcessorSequence
    first, second, unused = [tmp_path/name for name in ('first.fit', 'second.fit', 'unused.fit')]
    for path in (first, second, unused):
        path.write_bytes(b'fits')
    mosaic = Mosaic(tmp_path/'out', tmp_path/'work', 'field', [unused])
    assert isinstance(mosaic, processor)
    observed = []
    def create(self, args=None):
        observed.extend(self.input_files)
        result = self.output_dir/'field_mosaic.fits'
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_bytes(b'mosaic')
        return result
    monkeypatch.setattr(Mosaic, 'create_mosaic', create)

    class Next(processor):
        def add_arguments(self, parser):
            pass

        def post_process(self, input_path, output_path, args=None):
            assert input_path == (tmp_path/'out/field_mosaic.fits').resolve()
            return {'received': str(input_path)}

    sequence = _PostProcessorSequence([mosaic, Next()])
    parser = argparse.ArgumentParser()
    sequence.add_arguments(parser)
    args = parser.parse_args([])
    args.mosaic_inputs = [second, first]
    report = tmp_path/'report.json'
    result = sequence.post_process(first, report, args)
    assert observed == [first, second]
    assert mosaic.input_files == [unused]
    assert json.loads(report.read_text()) == result
    individual = Path(result['mosaic']['report_saved_to'])
    assert json.loads(individual.read_text()) == result['mosaic']


def test_mosaic_processor_reports_failure(tmp_path, monkeypatch, gradient_stub):
    first, second = tmp_path/'first.fit', tmp_path/'second.fit'
    first.touch()
    second.touch()
    mosaic = Mosaic(tmp_path/'out', tmp_path/'work', 'field', [second])
    monkeypatch.setattr(Mosaic, 'create_mosaic', lambda self, args=None: None)
    with pytest.raises(RuntimeError, match='création'):
        mosaic.post_process(first, tmp_path/'report.json')
    assert not (tmp_path/'report.json').exists()


def test_mosaic_processor_rejects_duplicate_only_panels(tmp_path):
    first = tmp_path/'first.fit'
    first.touch()
    mosaic = Mosaic(tmp_path/'out', tmp_path/'work', 'field', [first])
    with pytest.raises(ValueError, match='deux images distinctes'):
        mosaic.post_process(first, tmp_path/'report.json')


@pytest.mark.parametrize('success,keep', [(True, False), (False, False), (True, True)])
def test_mosaic_uses_parsed_options_and_cleans_operation(tmp_path, monkeypatch, success, keep, gradient_stub):
    import argparse
    import json
    import lib.mosaic as module
    from unittest.mock import Mock

    first, second = tmp_path/'first.fit', tmp_path/'second.fit'
    first.touch()
    second.touch()
    mosaic = Mosaic()
    parser = argparse.ArgumentParser()
    mosaic.add_arguments(parser, include_inputs=True)
    args = parser.parse_args(['--mosaic-name', 'panels', '--mosaic-inputs', str(second)])
    args.output_dir = tmp_path/'results'
    args.work_dir = tmp_path/'work'
    args.siril_path = '/custom/siril'
    args.siril_mode = 'native'
    args.keep_intermediate = keep
    work = args.work_dir/'mosaic_panels'

    def run(script, directory):
        assert Path(directory) == work
        assert 'panels_mosaic' in script
        import numpy as np
        from astropy.io import fits
        from astropy.wcs import WCS
        wcs = WCS(naxis=2)
        wcs.wcs.ctype = ['RA---TAN', 'DEC--TAN']
        header = wcs.to_header()
        data = np.pad(np.ones((6, 8), dtype=np.float32), 2)
        fits.writeto(work/'output/panels_mosaic.fit', data, header)
        for i in (1, 2):
            fits.writeto(work/f'output/r_mosaic_{i:05d}.fit', data, header)
        return success

    service = Mock()
    service.run_siril_script.side_effect = run
    factory = Mock(return_value=service)
    monkeypatch.setattr(module, 'Siril', factory)
    report = tmp_path/'report.json'
    mosaic.set_from_args(args)
    if success:
        result = mosaic.post_process(first, report)
        assert result['output_image'] == str(work/'panels_mosaic.fits')
        assert Path(result['uncropped_image']).is_file()
        assert result['crop']['cropped_shape'] == [6, 8]
        assert json.loads(report.read_text()) == result
    else:
        with pytest.raises(RuntimeError, match='création'):
            mosaic.post_process(first, report)
        assert not report.exists()
    factory.assert_called_once_with(
        siril_path=args.siril_path, siril_mode=args.siril_mode
    )
    assert work.exists()
    assert (work/'input').exists() == keep
    assert (work/'output').exists() == keep
    assert mosaic.mosaic_name == 'mosaic' and mosaic.input_files == []


def test_mosaic_accepts_explicit_input_files(tmp_path):
    input_files = []
    for index in range(2):
        input_file = tmp_path / f"session_M31_panel_{index}.fits"
        input_file.write_text(f"fake fits panel {index}")
        input_files.append(input_file)

    mosaic = Mosaic(
        output_dir=tmp_path / "output",
        work_dir=tmp_path / "work",
        mosaic_name="M31_complete",
        input_files=input_files,
    )

    assert mosaic.mosaic_name == "M31_complete"
    assert mosaic.input_files == input_files
    assert mosaic.mosaic_work_dir == tmp_path / "work" / "mosaic_M31_complete"


def test_mosaic_prepare_input_files(tmp_path):
    input_files = []
    for index in range(2):
        input_file = tmp_path / f"session_M31_panel_{index}.fits"
        input_file.write_text(f"fake fits panel {index}")
        input_files.append(input_file)

    mosaic = Mosaic(
        output_dir=tmp_path / "output",
        work_dir=tmp_path / "work",
        mosaic_name="M31_complete",
        input_files=input_files,
    )

    prepared_files = mosaic.prepare_input_files()

    assert len(prepared_files) == 2
    assert all(prepared_file.exists() for prepared_file in prepared_files)
    assert all(prepared_file.name.startswith("panel_") for prepared_file in prepared_files)
    assert [path.read_bytes() for path in prepared_files] == [path.read_bytes() for path in input_files]


@pytest.fixture
def gradient_stub(monkeypatch):
    from lib.mosaic import GradientExtractor
    calls = []
    def process(self, panels, directory, args=None):
        calls.append((panels, directory, self._get_args(args)))
        return [{'image_path': str(p), 'output_image': str(p)} for p in panels]
    monkeypatch.setattr(GradientExtractor, 'process_mosaic', process)
    return calls


def test_mosaic_owns_and_configures_its_gradient(tmp_path, monkeypatch):
    import argparse
    import lib.mosaic as module
    from unittest.mock import Mock

    real_gradient = module.GradientExtractor
    factory = Mock(side_effect=real_gradient, wraps=real_gradient)
    monkeypatch.setattr(module, 'GradientExtractor', factory)
    mosaic = Mosaic(tmp_path/'out', tmp_path/'work', 'field')
    factory.assert_called_once_with()
    gradient = mosaic.gradient_processor
    parser = argparse.ArgumentParser()
    mosaic.add_arguments(parser, include_inputs=True)
    assert parser.parse_args([]).gradient_method == 'rbf'
    args = parser.parse_args(['--gradient-method', 'polynomial', '--gradient-max-order', '2'])
    mosaic.set_from_args(args)
    args.gradient_method = 'rbf'
    assert gradient._get_args().gradient_method == 'polynomial'
    calls = []
    def process(panels, directory, args=None):
        args = gradient._get_args(args)
        calls.extend((panel, args.gradient_method, directory) for panel in panels)
        return [{'image_path': str(p), 'output_image': str(p)} for p in panels]
    monkeypatch.setattr(gradient, 'process_mosaic', process)
    panels = [tmp_path/'a.fit', tmp_path/'b.fit']
    for panel in panels:
        panel.touch()
    mosaic.input_files = panels
    output = tmp_path/'mosaic.fit'
    output.touch()
    monkeypatch.setattr(Mosaic, 'create_mosaic', lambda self, args=None: output)
    mosaic.post_process(panels[0], tmp_path/'report.json')
    assert [call[:2] for call in calls] == [(panel, 'polynomial') for panel in panels]
    assert {call[2] for call in calls} == {tmp_path/'work/mosaic_field'}
    assert gradient._get_args().gradient_output_dir is None
    assert mosaic.gradient_processor is gradient
    factory.assert_called_once_with()


def test_mosaic_can_skip_gradient(tmp_path, monkeypatch):
    import argparse
    from unittest.mock import Mock
    panels = [tmp_path/'first.fit', tmp_path/'second.fit']
    for panel in panels:
        panel.touch()
    mosaic = Mosaic(tmp_path/'out', tmp_path/'work', 'field', panels)
    parser = argparse.ArgumentParser()
    mosaic.add_arguments(parser, include_inputs=True)
    assert parser.parse_args([]).mosaic_gradient is True
    assert parser.parse_args(['--mosaic-gradient']).mosaic_gradient is True
    mosaic.set_from_args(parser.parse_args(['--no-mosaic-gradient']))
    gradient = Mock(side_effect=AssertionError('Gradient must be skipped'))
    monkeypatch.setattr(mosaic.gradient_processor, 'process_mosaic', gradient)
    def create(self, args=None):
        assert self.input_files == panels
        output = tmp_path/'mosaic.fit'
        output.touch()
        return output
    monkeypatch.setattr(Mosaic, 'create_mosaic', create)
    result = mosaic.post_process(panels[0], tmp_path/'report.json')
    gradient.assert_not_called()
    assert result['gradient_enabled'] is False
    assert result['gradient_results'] == []
    assert not (tmp_path/'report_steps').exists()


def test_mosaic_background_options_persistence(tmp_path):
    import argparse
    import json
    from lib.config import Config
    config = Config(tmp_path/'config.json')
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(Mosaic(), parser)
    args = config.parse_args(parser, ['--mosaic-gradient-mode', 'background',
        '--mosaic-gradient-border', '.04', '--mosaic-gradient-mask-growth', '2',
        '--mosaic-gradient-masks', 'first.fits', 'second.fits', '--no-mosaic-crop', '-S'])
    assert config.save_requested(args)
    saved = json.loads(Path(config.config_file).read_text())
    assert saved['mosaic_crop'] is False
    assert saved['mosaic_gradient_mode'] == 'background'
    assert saved['mosaic_gradient_border'] == .04
    assert saved['mosaic_gradient_mask_growth'] == 2
    assert 'mosaic_gradient_masks' not in saved


@pytest.mark.parametrize('crop_enabled', [False, True])
def test_full_mosaic_survives_cleanup_and_crop_failure(tmp_path, monkeypatch, gradient_stub, crop_enabled):
    import argparse
    import numpy as np
    from astropy.io import fits
    import lib.mosaic as module
    from unittest.mock import Mock

    panels = [tmp_path/'one.fit', tmp_path/'two.fit']
    for panel in panels:
        panel.touch()
    mosaic = Mosaic(tmp_path/'out', tmp_path/'work', 'field', panels)
    parser = argparse.ArgumentParser()
    mosaic.add_arguments(parser, include_inputs=True)
    args = parser.parse_args([] if crop_enabled else ['--no-mosaic-crop'])
    args.keep_intermediate = False
    work = tmp_path/'work/mosaic_field'
    def run(*args):
        fits.writeto(work/'output/field_mosaic.fit', np.ones((5, 7)))
        return True
    service = Mock()
    service.run_siril_script.side_effect = run
    monkeypatch.setattr(module, 'Siril', lambda **kwargs: service)
    def fail(*args, **kwargs):
        raise OSError('crop failed')
    monkeypatch.setattr(module, 'crop_mosaic', fail)
    mosaic.set_from_args(args)
    if crop_enabled:
        with pytest.raises(OSError, match='crop failed'):
            mosaic.post_process(panels[0], tmp_path/'report.json')
    else:
        result = mosaic.post_process(panels[0], tmp_path/'report.json')
        assert result['output_image'] == result['uncropped_image']
        assert result['crop']['enabled'] is False
    assert (work/'field_mosaic_uncropped.fits').is_file()
    assert not (work/'input').exists()
    assert not (work/'output').exists()


def test_excessive_crop_returns_full_mosaic_and_report_after_cleanup(tmp_path, monkeypatch, gradient_stub):
    import argparse
    import json
    import numpy as np
    from astropy.io import fits
    from astropy.wcs import WCS
    from unittest.mock import Mock
    import lib.mosaic as module

    panels = [tmp_path/'one.fit', tmp_path/'two.fit']
    for path in panels:
        path.touch()
    mosaic = Mosaic(tmp_path/'out', tmp_path/'work', 'field', panels)
    parser = argparse.ArgumentParser()
    mosaic.add_arguments(parser, include_inputs=True)
    mosaic.set_from_args(parser.parse_args([]))
    work = tmp_path/'work/mosaic_field'
    def run(*args):
        wcs = WCS(naxis=2)
        wcs.wcs.ctype = ['RA---TAN', 'DEC--TAN']
        wcs.wcs.cdelt = [-.001, .001]
        header = wcs.to_header()
        data = np.zeros((10, 30))
        data[:, 10:] = 1
        fits.writeto(work/'output/field_mosaic.fit', data, header)
        for i, offset in enumerate([4, 20], 1):
            panel_header = header.copy()
            panel_header['CRPIX1'] -= offset
            fits.writeto(work/f'output/r_mosaic_{i:05d}.fit', np.ones((10, 10)), panel_header)
        return True
    service = Mock()
    service.run_siril_script.side_effect = run
    monkeypatch.setattr(module, 'Siril', lambda **kwargs: service)
    report = tmp_path/'report.json'
    result = mosaic.post_process(panels[0], report)
    assert result['output_image'] == result['uncropped_image']
    assert result['crop']['reason'] == 'panel_loss_exceeds_half'
    assert fits.getdata(result['output_image']).shape == (10, 30)
    assert not (work/'field_mosaic.fits').exists()
    assert not (work/'output').exists()
    assert json.loads(report.read_text()) == result


@pytest.mark.parametrize('include_inputs', [False, True])
def test_single_mosaic_class_declares_session_or_standalone_options(
    tmp_path: Path, include_inputs: bool,
) -> None:
    """La même classe expose les panneaux CLI uniquement pour l'usage autonome."""
    import argparse

    parser = argparse.ArgumentParser()
    Mosaic.add_arguments(parser, include_inputs=include_inputs)
    assert parser.parse_args([]).mosaic_crop is True
    assert parser.parse_args([]).mosaic_gradient is True
    panels = [tmp_path / 'first.fits', tmp_path / 'second.fits']
    options = ['--mosaic-inputs', *map(str, panels)]
    if include_inputs:
        assert parser.parse_args(options).mosaic_inputs == panels
    else:
        assert '--mosaic-inputs' not in parser.format_help()
        with pytest.raises(SystemExit) as error:
            parser.parse_args(options)
        assert error.value.code == 2


def test_mosaic_registration_binds_gradient_and_preserves_instances(
    tmp_path: Path,
) -> None:
    """Mosaic remplit seule le contrat Config avec un enfant et des captures isolées."""
    import argparse
    import json
    from lib.config import Config

    config = Config(tmp_path / 'config.json')
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(Mosaic, parser)
    args = config.parse_args(parser, [
        '--mosaic-name', 'field', '--no-mosaic-crop',
        '--gradient-smoothing', '0.4', '-S',
    ])
    config.bind_parameters(args)
    first = Mosaic()
    assert first._get_args().mosaic_name == 'field'
    assert first._get_args().mosaic_crop is False
    assert first.gradient_processor._get_args().gradient_smoothing == 0.4
    assert config.save_requested(args)
    saved = json.loads(Path(config.config_file).read_text())
    assert saved['mosaic_crop'] is False
    assert saved['gradient_smoothing'] == 0.4
    assert 'mosaic_name' not in saved

    args.mosaic_crop = True
    args.gradient_smoothing = 0.8
    config.bind_parameters(args)
    second = Mosaic()
    assert second._get_args().mosaic_crop is True
    assert second.gradient_processor._get_args().gradient_smoothing == 0.8
    assert first._get_args().mosaic_crop is False
    assert first.gradient_processor._get_args().gradient_smoothing == 0.4
