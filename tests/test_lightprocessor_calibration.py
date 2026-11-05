from argparse import Namespace
import importlib.util
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest
import numpy as np
from astropy.io import fits

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.light_calibration import (
    CalibrationProfile,
    MasterFlatProcessor,
    SequenceCalibrationProcessor,
)
from lib.lightprocessor import LightProcessor
from lib.light_stacking import SessionStackingProcessor
from lib.processor import processor

_light_process_spec = importlib.util.spec_from_file_location(
    "light_process_cli_tests",
    Path(__file__).parents[1] / "bin" / "lightProcess.py",
)
assert _light_process_spec is not None and _light_process_spec.loader is not None
_light_process_cli = importlib.util.module_from_spec(_light_process_spec)
_light_process_spec.loader.exec_module(_light_process_cli)
discover_session_roots = _light_process_cli.discover_session_roots


def _write_fits_frame(path, image_type, exptime=180.0, temperature=-10.0, gain=125.0):
    data = np.full((12, 12), 100, dtype=np.uint16)
    header = fits.Header()
    header["IMAGETYP"] = image_type
    header["EXPTIME"] = exptime
    header["CCD-TEMP"] = temperature
    header["GAIN"] = gain
    header["INSTRUME"] = "TestCamera"
    header["XBINNING"] = 1
    header["YBINNING"] = 1
    header["DATE-OBS"] = "2026-09-13T20:00:00"
    fits.PrimaryHDU(data=data, header=header).writeto(path, overwrite=True)


def test_discover_session_roots(tmp_path):
    target_root = tmp_path / "target"
    session_a = target_root / "session_a"
    session_b = target_root / "session_b"
    session_c = target_root / "ignore_me"

    (session_a / "light").mkdir(parents=True)
    (session_b / "Light").mkdir(parents=True)
    session_c.mkdir(parents=True)

    discovered = discover_session_roots(target_root)

    assert discovered == [session_a, session_b]


def test_flat_calibration_uses_dark_only():
    profile = CalibrationProfile()

    command = profile.build_command(
        target="flat",
        dark_path="/tmp/master_dark.fit",
        flat_path=None,
        sequence_name="flat_seq",
    )

    assert command == "calibrate flat_seq -dark=/tmp/master_dark.fit -cc=dark -cfa"


def test_light_calibration_uses_dark_and_flat():
    profile = CalibrationProfile()

    command = profile.build_command(
        target="light",
        dark_path="/tmp/master_dark.fit",
        flat_path="/tmp/master_flat.fit",
        sequence_name="light_seq",
    )

    assert "-dark=/tmp/master_dark.fit -flat=/tmp/master_flat.fit -cc=dark -cfa -equalize_cfa -debayer" in command
    assert command.startswith("calibrate light_seq")


def test_sequence_calibration_uses_shared_processor_hierarchy():
    assert issubclass(SequenceCalibrationProcessor, processor)
    assert issubclass(MasterFlatProcessor, processor)
    assert issubclass(SessionStackingProcessor, processor)
    assert LightProcessor.parameter_children() == (
        SequenceCalibrationProcessor,
        SessionStackingProcessor,
    )
    assert SequenceCalibrationProcessor.parameter_children() == (MasterFlatProcessor,)


def test_child_options_are_declared_and_bound_by_their_processor(tmp_path):
    import argparse

    from lib.config import Config

    session_dir = tmp_path / "session"
    (session_dir / "light").mkdir(parents=True)
    config = Config(tmp_path / "config.json")
    parser = argparse.ArgumentParser()
    config.register(LightProcessor, parser)
    config.add_arguments(parser)
    args = config.parse_args(
        parser,
        [
            str(session_dir),
            "--drizzle",
            "force",
            "--fwhm-filter",
            "none",
            "--force-stacking",
            "--no-dark",
            "--dark-lib",
            str(tmp_path / "configured-dark-library"),
            "--temperature-precision",
            "0.2",
            "--save-config",
        ],
    )
    config.bind_parameters(args)
    stacking = SessionStackingProcessor(siril=Mock())
    stacking_args = stacking._get_args()

    assert args.stack_method == SessionStackingProcessor.CONFIG_DEFAULTS["stack_method"]
    assert stacking_args.drizzle == "force"
    assert stacking_args.fwhm_filter == "none"
    assert stacking_args.force_stacking is True
    calibration = SequenceCalibrationProcessor(
        Mock(),
        tmp_path / "calibration-work",
    )
    assert calibration.use_dark is False
    assert calibration.temp_precision == 0.2
    assert calibration.dark_library_path == str(
        tmp_path / "configured-dark-library"
    )
    light_processor = LightProcessor(
        session_dir,
        tmp_path / "out",
        tmp_path / "work",
        siril=Mock(),
        calibration_processor=SequenceCalibrationProcessor(
            Mock(),
            tmp_path / "work",
            dark_library_path=str(tmp_path / "missing-dark-library"),
            args=Namespace(no_dark=True, temperature_precision=0.2),
        ),
    )
    assert light_processor.calibration_processor.use_dark is False
    assert light_processor.calibration_processor.dark_library_path == str(
        tmp_path / "missing-dark-library"
    )
    assert "force_stacking" not in LightProcessor.parameter_persistence
    assert SessionStackingProcessor.parameter_persistence["force_stacking"] is False
    assert config.save_requested(args)
    assert "force_stacking" not in config.to_dict()
    assert config.to_dict()["no_dark"] is True
    assert config.to_dict()["dark_library_path"] == str(
        tmp_path / "configured-dark-library"
    )
    assert config.to_dict()["temperature_precision"] == 0.2


def test_light_processor_does_not_accept_calibration_dark_toggle(tmp_path):
    import inspect

    assert "use_dark" not in inspect.signature(LightProcessor).parameters
    assert "temp_precision" not in inspect.signature(LightProcessor).parameters
    assert "dark_library_path" not in inspect.signature(LightProcessor).parameters


def test_light_processor_from_config_applies_dark_setting_to_calibration(
    tmp_path, monkeypatch
):
    from lib.config import Config
    from lib.siril import Siril

    session_dir = tmp_path / "session"
    (session_dir / "light").mkdir(parents=True)
    config = Config(tmp_path / "config.json")
    config.set("no_dark", True)
    config.set("temperature_precision", 0.25)
    siril = Mock()
    monkeypatch.setattr(Siril, "create_with_defaults", lambda: siril)

    light_processor = LightProcessor.from_config(
        session_dir,
        dark_library_path=str(tmp_path / "missing-dark-library"),
        output_dir=tmp_path / "out",
        work_dir=tmp_path / "work",
    )

    assert light_processor.calibration_processor.use_dark is False
    assert light_processor.calibration_processor.temp_precision == 0.25
    assert light_processor.calibration_processor.dark_library_path == str(
        tmp_path / "missing-dark-library"
    )
    assert light_processor.siril is siril
    assert light_processor.calibration_processor.siril is siril


def test_master_flat_processor_rejects_empty_sequence(tmp_path):
    siril = Mock()
    processor = MasterFlatProcessor(
        siril, tmp_path, CalibrationProfile(), keep_intermediate=False
    )

    with pytest.raises(ValueError, match="aucune image"):
        processor.post_process(
            tmp_path / "flat.fit",
            tmp_path / "master_flat",
            args=Namespace(
                sequence_name="flat_group",
                flat_files=[],
                flat_dark_path=None,
                group_key="group",
                dry_run=False,
            ),
        )

    siril.run_siril_script.assert_not_called()


def test_discover_session_roots_for_target_with_multiple_sessions(tmp_path):
    target_root = tmp_path / "M_33"
    session_01 = target_root / "session_01"
    session_02 = target_root / "session_02"
    notes_dir = target_root / "notes"

    (session_01 / "light").mkdir(parents=True)
    (session_02 / "Light").mkdir(parents=True)
    notes_dir.mkdir()

    discovered = discover_session_roots(target_root)

    assert discovered == [session_01, session_02]


def test_generate_siril_script_rebuilds_calibrated_sequence(tmp_path):
    calibration = SequenceCalibrationProcessor(
        Mock(),
        tmp_path / "work",
    )

    script = calibration._generate_script(
        sequence_name="light_seq",
        dark_path="/tmp/master_dark.fit",
        flat_path="/tmp/master_flat.fit",
        stack_params={"method": "average", "rejection": "sigma", "rejection_low": 2.5, "rejection_high": 3.0},
    )

    assert "convert pp_light_seq" not in script
    assert "register pp_light_seq" not in script
    assert "stack r_pp_light_seq" not in script


def test_generate_flat_master_script_stacks_calibrated_sequence_without_reconvert(tmp_path):
    processor = MasterFlatProcessor(
        Mock(),
        tmp_path / "work",
        CalibrationProfile(),
    )

    script = processor._generate_script(
        sequence_name="flat_seq",
        output_base=tmp_path / "work" / "master_flat",
        dark_path="/tmp/master_dark.fit",
    )

    assert "calibrate flat_seq -dark=/tmp/master_dark.fit -cc=dark -cfa" in script
    assert "convert pp_flat_seq" not in script
    assert "stack pp_flat_seq median -norm=mul -out=" in script


def test_export_calibrated_outputs_from_process_copies_only_pp_sequence(tmp_path):
    processor = SequenceCalibrationProcessor(
        Mock(),
        tmp_path / "work",
    )

    process_dir = tmp_path / "work" / "process"
    process_dir.mkdir(parents=True)
    for index in range(1, 4):
        (process_dir / f"light_seq_{index:05d}.fits").write_bytes(b"raw")
        fits.writeto(process_dir / f"pp_light_seq_{index:05d}.fits", np.full((4,4), index, dtype=np.float32))
    (process_dir / "pp_other_seq_00001.fits").write_bytes(b"other")

    calibrated_output_dir = tmp_path / "calibrated"
    processor.export_calibrated_outputs("light_seq", calibrated_output_dir)

    exported = sorted(path.name for path in calibrated_output_dir.glob("*.fits"))

    assert exported == [
        "pp_light_seq_00001.fits",
        "pp_light_seq_00002.fits",
        "pp_light_seq_00003.fits",
    ]
    assert all(not (calibrated_output_dir / name).is_symlink() for name in exported)
    seq = calibrated_output_dir / "pp_light_seq_.seq"
    assert seq.exists()
    assert "I 3 1" in seq.read_text()
    import shutil
    shutil.rmtree(process_dir)
    assert all(fits.getdata(calibrated_output_dir / name).shape == (4,4) for name in exported)


@pytest.mark.parametrize('disable_flats', [False, True])
def test_light_process_wrapper_runs_dry_run_pipeline(
    tmp_path: Path,
    light_process_sh: Path,
    light_process_env: dict[str, str],
    disable_flats: bool,
) -> None:
    """Le lanceur simule la calibration avec ou sans les flats présents."""
    session_dir = tmp_path / "M_33" / "20260913_e180"
    light_dir = session_dir / "light"
    light_dir.mkdir(parents=True)
    _write_fits_frame(light_dir / "light_001.fit", "Light")
    flat_dir = session_dir / "Flat"
    flat_dir.mkdir()
    _write_fits_frame(flat_dir / "flat_001.fit", "Flat", exptime=0.1)

    dark_lib = tmp_path / "dark_lib"
    dark_lib.mkdir()
    _write_fits_frame(dark_lib / "master_dark.fit", "dark")

    command = [
        str(light_process_sh),
        str(session_dir),
        "--dark-lib",
        str(dark_lib),
        "--output",
        str(tmp_path / "out"),
        "--work-dir",
        str(tmp_path / "work"),
        "--dry-run",
        "--log-level",
        "INFO",
    ]

    if disable_flats:
        command.append('--no-flat')
    result = subprocess.run(
        command,
        cwd=light_process_sh.parent.parent,
        env=light_process_env,
        text=True,
        capture_output=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert Path(result.args[0]).name == "lightProcess.sh"
    assert "[DRY-RUN]" in output
    assert "Sous-session stats" in output
    assert "Sous-sessions calibrées avec succès: 1/1" in output
    if disable_flats:
        assert 'Flats désactivés' in output
        assert 'Création du master flat' not in output
    assert not list((tmp_path / 'out').rglob('calibration_settings.json'))

    session_log = tmp_path / "work" / "20260913_e180" / "sessions" / "20260913_e180" / "20260913_e180_lightProcess.log"
    assert session_log.exists()
    assert "Début du traitement de la sous-session" in session_log.read_text(encoding="utf-8")


@pytest.mark.parametrize('reject_percent,retained,effective', [(0, 3, 6), (50, 2, 2)])
@pytest.mark.parametrize('method,roundness,platesolve', [
    ('average', '1.8k', None), ('average', 'none', True), ('median', '1.8k', False),
])
def test_stack_selection_only_after_registration(tmp_path, monkeypatch, method, roundness, platesolve, reject_percent, retained, effective, simulated_registered_sequence):
    import json
    import lib.drizzle as drizzle
    from lib.quality_filter import quality_mask

    sources = [tmp_path / f'source_{i}.fits' for i in range(4)]
    for source in sources:
        _write_fits_frame(source, 'Light')
    calls = []
    selections = []
    def observed_selection(rows, cfg, included=None):
        selections.append(len(rows))
        return quality_mask(rows, cfg, included)
    monkeypatch.setattr(drizzle, 'quality_mask', observed_selection)

    class DummySiril:
        @staticmethod
        def create_with_defaults():
            return DummySiril()

        def run_siril_script(self, script_content, working_dir, script_name=None):
            directory = Path(working_dir)
            calls.append(script_name)
            assert '-filter-wfwhm' not in script_content
            assert '-filter-round' not in script_content
            if script_name == '01_registration.sps':
                assert not selections
                inputs = sorted((directory.parent/'00_inputs').glob('target_name_*.fits'))
                assert len(inputs) == 4  # No rejection or duplication before registration.
                assert 'convert target_name_ -out=' in script_content
                assert 'seqfindstar target_name_' in script_content
                assert 'register target_name_ -2pass -transf=affine' in script_content
                assert ('seqplatesolve target_name_' in script_content) == (platesolve is not False)
                assert 'seqapplyreg' not in script_content and '\nstack ' not in script_content
                for source in inputs:
                    (directory/source.name).symlink_to(source.resolve())
                (directory/'target_name_.seq').write_text(
                    "S 'target_name_' 1 4 4 3 0 6 0 0 0\n"
                    + ''.join(f'I {i+1} 1\n' for i in range(4))
                    + ''.join(f'R0 {f} {f} .9 1 0 200 H 1 0 {i*.2} 0 1 0 0 0 1\n'
                              for i, f in enumerate([2,3,4,20])))
            elif script_name == '04_realign.sps':
                framing = 'min'
                assert f'seqapplyreg target_name_ -filter-included -framing={framing}' in script_content
                assert 'register r_target_name_ -2pass -transf=affine' in script_content
                assert '\nstack ' not in script_content
                simulated_registered_sequence(directory, 'target_name_', 'r_target_name_')
            elif script_name == '04_applyreg.sps':
                assert 'seqapplyreg r_target_name_ -filter-included -framing=min' in script_content
                simulated_registered_sequence(directory, 'r_target_name_', 'r_r_target_name_')
            elif script_name == '04_stacking.sps':
                assert selections == [4]
                assert 'seqapplyreg' not in script_content
                assert '\nregister ' not in script_content
                assert '-maximize' not in script_content
                stack_method = 'median' if method == 'median' else 'rej 3.0 3.0'
                assert f'stack r_r_target_name_ {stack_method} -output_norm -out=' in script_content
                report = json.loads((tmp_path/'out/target_name_combined.drizzle.json').read_text())
                assert report['quality_selection']['independent_retained'] == retained
                assert report['quality_selection']['effective_stack_entries'] == effective
                assert [r['fwhm_multiplicity'] for r in report['frames']] == ([3,2,1] if retained == 3 else [1,1])
                assert '-out=' + str(tmp_path/'out/target_name_combined.fits') in script_content
                (tmp_path/'out/target_name_combined.fits').touch()
            else:
                raise AssertionError(script_name)
            return True

    stacker = SessionStackingProcessor(siril=DummySiril())
    cfg = dict(method=method, roundness_filter=roundness, fwhm_filter='1.8k',
               fwhm_reject_percent=reject_percent, fwhm_weighted=True, fwhm_weight_max_extra=2,
               drizzle='off')
    if platesolve is not None:
        cfg['enable_stack_platesolve'] = platesolve
    report = {}
    result = stacker.stack_outputs(
        sources,
        tmp_path / "out",
        tmp_path / "work",
        "target_name",
        cfg,
        stack_report=report,
    )
    assert result == tmp_path/'out/target_name_combined.fits'
    assert calls == ['01_registration.sps', '04_realign.sps', '04_applyreg.sps', '04_stacking.sps']
    assert selections == [4]
    assert report['total_input'] == 4
    assert report['kept_unique_for_stack'] == retained
    assert report['kept_effective_for_stack'] == effective


def test_stacking_processor_force_cleans_previous_artifacts(tmp_path):
    source_a = tmp_path / "source_a.fits"
    source_b = tmp_path / "source_b.fits"
    source_a.write_bytes(b"dummy-a")
    source_b.write_bytes(b"dummy-b")

    output_dir = tmp_path / "out"
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = tmp_path / "work"

    target_name = "target_name"
    session_stack_dir = work_dir / target_name / "stacking"
    stale_file = session_stack_dir / "stale.marker"
    stale_file.parent.mkdir(parents=True, exist_ok=True)
    stale_file.write_text("old")

    stale_output_fit = output_dir / f"{target_name}_combined.fit"
    stale_output_fit.write_bytes(b"old-fit")

    class DummySiril:
        @staticmethod
        def create_with_defaults():
            return DummySiril()

        def run_siril_script(self, script_content, working_dir, script_name=None):
            assert not stale_file.exists()
            assert not stale_output_fit.exists()
            return False

    result = SessionStackingProcessor(siril=DummySiril()).stack_outputs(
        [source_a, source_b],
        output_dir,
        work_dir,
        target_name,
        {
            "method": "average",
            "rejection": "sigma",
            "rejection_low": 3.0,
            "rejection_high": 3.0,
        },
        force_stacking=True,
    )
    assert result is None


def test_stacking_processor_rebuilds_transient_stack_dirs(tmp_path):
    source_a = tmp_path / "source_a.fits"
    source_b = tmp_path / "source_b.fits"
    source_a.write_bytes(b"dummy-a")
    source_b.write_bytes(b"dummy-b")

    output_dir = tmp_path / "out"
    work_dir = tmp_path / "work"
    target_name = "M33"
    session_stack_dir = work_dir / target_name / "stacking"
    input_dir = session_stack_dir / "input"
    output_stack_dir = session_stack_dir / "output"

    input_dir.mkdir(parents=True)
    output_stack_dir.mkdir(parents=True)
    (input_dir / "M33_999.fits").write_bytes(b"old-input")
    (output_stack_dir / "M33_999.fits").write_bytes(b"old-convert")
    (output_stack_dir / "r_M33_999.fits").write_bytes(b"old-register")
    (session_stack_dir / "M33_stacking.log").write_text("keep log")
    (session_stack_dir / "stack_M33.sps").write_text("old script")

    class DummySiril:
        @staticmethod
        def create_with_defaults():
            return DummySiril()

        def run_siril_script(self, script_content, working_dir, script_name=None):
            assert script_name == "01_registration.sps"
            assert not (input_dir / "M33_999.fits").exists()
            assert not (output_stack_dir / "M33_999.fits").exists()
            assert not (output_stack_dir / "r_M33_999.fits").exists()
            assert (session_stack_dir / "M33_stacking.log").exists()
            prepared_inputs = sorted(path.name for path in (session_stack_dir / "00_inputs").glob("M33_*.fits"))
            assert prepared_inputs == ["M33_001.fits", "M33_002.fits"]
            return False

    result = SessionStackingProcessor(siril=DummySiril()).stack_outputs(
        [source_a, source_b],
        output_dir,
        work_dir,
        target_name,
        {
            "method": "average",
            "rejection": "sigma",
            "rejection_low": 3.0,
            "rejection_high": 3.0,
        },
    )
    assert result is None


def test_stacking_processor_post_process_reuses_single_calibrated_image(tmp_path):
    source = tmp_path / "calibrated.fits"
    source.touch()
    report = {}
    stacker = SessionStackingProcessor(siril=Mock())

    result = stacker.post_process(
        input_path=source,
        output_path=tmp_path / "out" / "target_combined.fits",
        args=Namespace(
            output_files=[source],
            work_dir=tmp_path / "work",
            target_name="target",
            stack_params={},
            force_stacking=False,
            stack_report=report,
        ),
    )

    assert result == {
        "status": "ok",
        "input_image": str(source),
        "output_image": str(source),
    }


def test_stacking_processor_requires_execution_context(tmp_path):
    stacker = SessionStackingProcessor(siril=Mock())

    with pytest.raises(ValueError, match="Paramètres d'exécution"):
        stacker.post_process(
            input_path=tmp_path / "input.fits",
            output_path=tmp_path / "output.fits",
            args=Namespace(),
        )


def test_stacking_processor_filters_incompatible_and_unreadable_fits(tmp_path):
    stacker = SessionStackingProcessor(siril=Mock())
    matching = [tmp_path / f"matching_{index}.fits" for index in range(2)]
    incompatible = tmp_path / "incompatible.fits"
    unreadable = tmp_path / "unreadable.fits"
    for path in matching:
        fits.writeto(path, np.ones((4, 4), dtype=np.float32))
    fits.writeto(incompatible, np.ones((5, 5), dtype=np.float32))
    unreadable.write_text("not a FITS file")

    kept, stats = stacker._filter_compatible_stack_inputs(
        [*matching, incompatible, unreadable]
    )

    assert kept == matching
    assert stats == {
        "rejected_incompatible_layout": 1,
        "rejected_unreadable_layout": 1,
    }


def test_stacking_processor_keeps_unreadable_files_if_no_layout_can_be_read(
    tmp_path,
):
    stacker = SessionStackingProcessor(siril=Mock())
    files = [tmp_path / "one.fits", tmp_path / "two.fits"]
    for path in files:
        path.write_text("not a FITS file")

    kept, stats = stacker._filter_compatible_stack_inputs(files)

    assert kept == files
    assert stats == {
        "rejected_incompatible_layout": 0,
        "rejected_unreadable_layout": 0,
    }


def test_calibrated_sequence_preserves_siril_selection(tmp_path):
    from lib.light_calibration import SequenceCalibrationProcessor

    source=tmp_path/'process';source.mkdir()
    output=tmp_path/'export';output.mkdir()
    original="S 'pp_light_' 1 2 1 5 0 4 0\nL 1\nI 1 1\nI 2 0\n"
    (source/'pp_light_.seq').write_text(original)
    result = SequenceCalibrationProcessor.save_calibrated_sequence(
        'light', output, source
    )
    assert result.read_text()==original
    assert not result.is_symlink()
    (source/'pp_light_.seq').unlink()
    assert result.read_text()==original


def test_calibrated_sequence_uses_actual_indices_and_rgb_layers(tmp_path):
    from lib.light_calibration import SequenceCalibrationProcessor

    for index in (2,5):
        fits.writeto(tmp_path/f'pp_light_{index:05d}.fit', np.ones((3,4,4), dtype=np.float32))
    result = SequenceCalibrationProcessor.save_calibrated_sequence(
        'light', tmp_path
    )
    assert "S 'pp_light_' 2 2 2 5 0 4 0" in result.read_text()
    assert 'L 3\nI 2 1\nI 5 1\n' in result.read_text()


def test_list_calibrated_outputs_is_owned_by_sequence_processor(tmp_path):
    from lib.light_calibration import SequenceCalibrationProcessor

    expected = tmp_path / "pp_light_00001.fit"
    ignored = tmp_path / "light_00001.fit"
    expected.touch()
    ignored.touch()

    assert SequenceCalibrationProcessor.list_calibrated_outputs(tmp_path) == [
        expected
    ]


def test_flat_option_priorities_persistence_and_snapshots(tmp_path: Path) -> None:
    """CLI > JSON > défaut et sauvegarde explicite s'appliquent au mode flats."""
    import argparse
    import json
    from lib.config import Config

    path = tmp_path / 'config.json'
    config = Config(path)
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(LightProcessor, parser)
    assert config.parse_args(parser, ['session']).use_flat is True
    config.set('use_flat', False)
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(LightProcessor, parser)
    assert config.parse_args(parser, ['session']).use_flat is False
    args = config.parse_args(parser, ['session', '--flat'])
    config.bind_parameters(args)
    first = SequenceCalibrationProcessor(Mock(), tmp_path)
    assert first.use_flat is True
    args = config.parse_args(parser, ['session', '--no-flat'])
    config.bind_parameters(args)
    second = SequenceCalibrationProcessor(Mock(), tmp_path)
    assert second.use_flat is False
    assert second.calibration.use_flat is False
    assert first.use_flat is True
    assert config.save_requested(args)
    assert not path.exists()
    args.save_config = True
    assert config.save_requested(args)
    assert json.loads(path.read_text())['use_flat'] is False


@pytest.mark.parametrize('supplied', [False, True])
def test_from_config_applies_flat_override(
    tmp_path: Path, supplied: bool,
) -> None:
    """La fabrique transmet les flats configurés et accepte une surcharge locale."""
    from lib.config import Config

    session = tmp_path / 'session'
    (session / 'Light').mkdir(parents=True)
    config = Config(tmp_path / 'settings.json')
    config.set('use_flat', False)
    siril = Mock()
    options = dict(siril=siril, output_dir=tmp_path / 'out', work_dir=tmp_path / 'work')
    first = LightProcessor.from_config(session, **options)
    assert first.calibration_processor.use_flat is False
    child = SequenceCalibrationProcessor(siril, tmp_path)
    if supplied:
        options['calibration_processor'] = child
    second = LightProcessor.from_config(session, use_flat=True, **options)
    assert second.calibration_processor.use_flat is True
    assert second.calibration_processor.calibration.use_flat is True
    assert first.calibration_processor.use_flat is False


@pytest.mark.parametrize('use_flat, marker, expected', [
    (True, None, True), (False, None, False),
    (True, '{"use_flat": true}', True), (False, '{"use_flat": false}', True),
    (True, '{"use_flat": false}', False), (False, '{"use_flat": true}', False),
    (False, 'invalid', False), (True, '[]', False), (True, '{}', False),
])
def test_flat_calibration_cache_requires_compatible_settings(
    tmp_path: Path, use_flat: bool, marker: str | None, expected: bool,
) -> None:
    """Un cache historique, incompatible ou invalide suit les règles de reprise."""
    processor = SequenceCalibrationProcessor(
        Mock(), tmp_path, args=Namespace(use_flat=use_flat),
    )
    if marker is not None:
        (tmp_path / 'calibration_settings.json').write_text(marker)
    assert processor.flat_cache_matches(tmp_path) is expected
