from pathlib import Path

import pytest

from lib.mosaic import Mosaic, calculate_common_basename
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
    args = parser.parse_args(['--mosaic-inputs', str(second), str(first)])
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
    mosaic.add_arguments(parser)
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
        (work/'output/panels_mosaic.fit').write_bytes(b'mosaic')
        return success

    service = Mock()
    service.run_siril_script.side_effect = run
    factory = Mock(return_value=service)
    monkeypatch.setattr(module, 'create_siril_from_args', factory)
    report = tmp_path/'report.json'
    mosaic.set_from_args(args)
    if success:
        result = mosaic.post_process(first, report)
        assert result['output_image'] == str(work/'panels_mosaic.fits')
        assert json.loads(report.read_text()) == result
    else:
        with pytest.raises(RuntimeError, match='création'):
            mosaic.post_process(first, report)
        assert not report.exists()
    factory.assert_called_once_with(args)
    assert work.exists()
    assert (work/'input').exists() == keep
    assert (work/'output').exists() == keep
    assert mosaic.mosaic_name == 'mosaic' and mosaic.input_files == []


@pytest.mark.parametrize("session_names, expected", [
    (["session_M31_nord", "session_M31_sud"], "session_M31"),
    (["NGC7000_panel1", "NGC7000_panel2", "NGC7000_panel3"], "NGC7000_panel"),
    (["M42_rouge", "M42_vert", "M42_bleu"], "M42"),
    (["IC1396_A", "IC1396_B"], "IC1396"),
    (["session1", "session2"], "session"),
    (["A", "B"], ""),
    (["completely_different", "names_here"], ""),
    (["single_session"], "single_session"),
    (["M31_mosaic_part_1", "M31_mosaic_part_2"], "M31_mosaic_part"),
    (["2023_10_15_M31", "2023_10_15_M42"], "2023_10_15_M"),
    ([], ""),
    (["north_M31", "south_M31"], "M31"),
])
def test_calculate_common_basename(session_names, expected):
    session_dirs = [Path(name) for name in session_names]
    assert calculate_common_basename(session_dirs) == expected


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
    factory = Mock(side_effect=real_gradient)
    monkeypatch.setattr(module, 'GradientExtractor', factory)
    mosaic = Mosaic(tmp_path/'out', tmp_path/'work', 'field')
    factory.assert_called_once_with()
    gradient = mosaic.gradient_processor
    parser = argparse.ArgumentParser()
    mosaic.add_arguments(parser)
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
    mosaic.add_arguments(parser)
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
