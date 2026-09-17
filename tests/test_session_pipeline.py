"""Orchestration CLI : calibration par sous-session, stack par session, mosaïque."""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    import lib.lightprocessor as processing
    import lib.mosaic as mosaic
    from lib.config import Config
    from lib.siril_utils import Siril

    spec = importlib.util.spec_from_file_location('light_process_cli', Path(__file__).parents[1] / 'bin/lightProcess.py')
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    class TestConfig(Config):
        def __init__(self):
            super().__init__(str(tmp_path / 'config.json'))

    monkeypatch.setattr(cli, 'Config', TestConfig)
    monkeypatch.setattr(cli, 'setup_logging', lambda level: None)
    monkeypatch.setattr(Siril, 'configure_defaults', Mock())
    calibrated = {}

    class Processor:
        def __init__(self, session_dir, output_dir, **kwargs):
            self.session_dir = session_dir
            directory = output_dir / 'group_calibrated'
            directory.mkdir(parents=True, exist_ok=True)
            self.outputs = [directory / f'pp_light_{i}.fits' for i in range(2)]
            for path in self.outputs:
                path.touch()
            calibrated[session_dir] = self.outputs

        def process_session(self, params):
            return True

        def get_session_stats(self):
            return {}

        def get_output_files(self):
            return self.outputs

    def stack(**kwargs):
        result = kwargs['output_dir'] / (kwargs['target_name'] + '_combined.fits')
        result.touch()
        return result

    stack_mock = Mock(side_effect=stack)
    mosaic_mock = Mock()
    mosaic_mock.return_value.create_mosaic.return_value = tmp_path / 'mosaic.fits'
    monkeypatch.setattr(processing, 'LightProcessor', Processor)
    monkeypatch.setattr(processing, 'stack_session_outputs', stack_mock)
    monkeypatch.setattr(mosaic, 'Mosaic', mosaic_mock)

    def run(roots, *options):
        monkeypatch.setattr(sys, 'argv', ['lightProcess.py', *map(str, roots), '--no-dark', '--output', str(tmp_path / 'out'), '--work-dir', str(tmp_path / 'work'), *options])
        return cli.main()

    return cli, run, stack_mock, mosaic_mock, calibrated


def make_session(tmp_path, name, count):
    root = tmp_path / name
    children = [root] if count == 0 else [root / f'night_{i}' for i in range(count)]
    for child in children:
        (child / 'light').mkdir(parents=True)
    return root, children


@pytest.mark.parametrize('count', [0, 1, 3])
@pytest.mark.parametrize('force', [False, True])
def test_every_session_is_stacked_with_all_its_calibrations(tmp_path, pipeline, count, force):
    cli, run, stack, mosaic, calibrated = pipeline
    root, children = make_session(tmp_path, 'field', count)
    directories, parents = cli.build_session_target_map([root])
    assert directories == children
    assert all(parents[child] == root for child in children)
    assert run([root], *(['--force-stacking'] if force else [])) == 0
    stack.assert_called_once()
    call = stack.call_args.kwargs
    assert call['output_dir'] == tmp_path / 'out' / 'field' / 'stack'
    assert set(call['output_files']) == {path for child in children for path in calibrated[child]}
    mosaic.assert_not_called()


def test_mosaic_uses_only_one_stack_per_input_session(tmp_path, pipeline):
    _, run, stack, mosaic, calibrated = pipeline
    roots = [make_session(tmp_path, name, count)[0] for name, count in [('north', 0), ('south', 1), ('east', 3)]]
    assert run(roots, '--mosaic', '--mosaic-name', 'field') == 0
    assert stack.call_count == 3
    inputs = mosaic.call_args.kwargs['input_files']
    assert inputs == [tmp_path / 'out' / root.name / 'stack' / f'{root.name}_combined.fits' for root in roots]
    assert not set(inputs).intersection(path for files in calibrated.values() for path in files)
    mosaic.return_value.create_mosaic.assert_called_once()


def test_one_session_with_many_subsessions_is_not_a_mosaic(tmp_path, pipeline):
    _, run, stack, mosaic, _ = pipeline
    root, _ = make_session(tmp_path, 'field', 3)
    assert run([root], '--mosaic', '--mosaic-name', 'field') == 1
    stack.assert_called_once()
    mosaic.assert_not_called()


@pytest.mark.parametrize('failure', [None, RuntimeError('Siril failed')])
def test_failed_stack_is_not_replaced_by_calibrations_in_mosaic(tmp_path, pipeline, failure):
    _, run, stack, mosaic, _ = pipeline
    roots = [make_session(tmp_path, name, 1)[0] for name in ['north', 'south']]
    stack.side_effect = [failure, tmp_path / 'south_combined.fits']
    assert run(roots, '--mosaic', '--mosaic-name', 'field') == 1
    assert stack.call_count == 2
    mosaic.assert_not_called()


def test_dry_run_never_executes_stack_or_mosaic(tmp_path, pipeline):
    _, run, stack, mosaic, _ = pipeline
    roots = [make_session(tmp_path, name, 1)[0] for name in ['north', 'south']]
    assert run(roots, '--mosaic', '--mosaic-name', 'field', '--dry-run') == 0
    stack.assert_not_called()
    mosaic.assert_not_called()


def test_mosaic_accepts_cached_session_stacks(tmp_path, pipeline, monkeypatch):
    import lib.drizzle as drizzle
    cli, run, stack, mosaic, _ = pipeline
    roots = [make_session(tmp_path, name, 1)[0] for name in ['north', 'south']]
    expected = []
    for root in roots:
        result = tmp_path / 'out' / root.name / 'stack' / f'{root.name}_combined.fits'
        result.parent.mkdir(parents=True)
        result.touch()
        expected.append(result)
    monkeypatch.setattr(drizzle, 'cache_matches', lambda *args: True)
    monkeypatch.setattr(cli, 'should_rebuild_stack', lambda *args: False)
    assert run(roots, '--mosaic', '--mosaic-name', 'field') == 0
    stack.assert_not_called()
    assert mosaic.call_args.kwargs['input_files'] == expected


def test_mosaic_failure_is_reported_as_failure(tmp_path, pipeline):
    _, run, stack, mosaic, _ = pipeline
    roots = [make_session(tmp_path, name, 1)[0] for name in ['north', 'south']]
    mosaic.return_value.create_mosaic.return_value = None
    assert run(roots, '--mosaic', '--mosaic-name', 'field') == 1
    assert stack.call_count == 2
    mosaic.return_value.cleanup.assert_called_once()
