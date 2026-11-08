"""Journal des étapes de calibration sur petits FITS, avec exécution Siril simulée."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest
from astropy.io import fits

from lib.lightprocessor import LightProcessor
from lib.light_calibration import SequenceCalibrationProcessor
from lib.siril import Siril


def write_frame(path: Path, image_type: str, exposure: float) -> None:
    """Écrit une acquisition synthétique avec les métadonnées de calibration."""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = fits.Header({
        "IMAGETYP": image_type, "EXPTIME": exposure, "CCD-TEMP": -10.0,
        "GAIN": 125.0, "INSTRUME": "TestCamera", "XBINNING": 1,
        "YBINNING": 1, "DATE-OBS": "2026-10-18T20:00:00",
    })
    fits.writeto(path, np.full((12, 12), 100, dtype=np.uint16), header)


@pytest.fixture
def calibration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[LightProcessor, Mock]:
    """Prépare lights, flats et darks ; simule uniquement les produits Siril."""
    session = tmp_path / "session"
    for index in range(2):
        write_frame(session / "Light" / f"light_{index}.fit", "Light", 180)
        write_frame(session / "Flat" / f"flat_{index}.fit", "Flat", 0.1)
    dark_library = tmp_path / "darks"
    write_frame(dark_library / "dark_light.fit", "Dark", 180)
    write_frame(dark_library / "dark_flat.fit", "Dark", 0.1)

    def run_script(
        content: str, working_dir: str, script_name: str,
    ) -> bool:
        """Produit les FITS attendus et vérifie les vrais arguments de calibration."""
        directory = Path(working_dir)
        if script_name.startswith("master_flat_"):
            if "calibrate flat_" in content:
                assert "stack pp_flat_" in content
                assert str(dark_library / "dark_flat.fit") in content
            else:
                assert "stack flat_" in content
            fits.writeto(
                directory / script_name.replace(".sps", ".fit"),
                np.ones((12, 12), dtype=np.float32), overwrite=True,
            )
        else:
            sequence = script_name.removeprefix("calibrate_").removesuffix(".sps")
            for index in range(2):
                fits.writeto(
                    directory / "process" / f"pp_{sequence}_{index:05d}.fit",
                    np.full((12, 12), 0.1, dtype=np.float32), overwrite=True,
                )
        return True

    siril = Mock()
    siril.run_siril_script.side_effect = run_script
    monkeypatch.setattr(Siril, "create_with_defaults", lambda: siril)
    processor = LightProcessor(
        session,
        output_dir=tmp_path / "out",
        work_dir=tmp_path / "work",
        keep_intermediate=True,
        siril=siril,
        calibration_processor=SequenceCalibrationProcessor(
            siril,
            tmp_path / "work",
            keep_intermediate=True,
            dark_library_path=dark_library,
        ),
    )
    return processor, siril.run_siril_script


@pytest.mark.parametrize("use_dark, use_flat, expected", [
    (True, True, "avec master dark + master flat"),
    (True, False, "avec master dark"),
    (False, True, "avec master flat"),
    (False, False, "sans master dark ni master flat"),
])
def test_calibration_stages_match_actual_masters(
    calibration: tuple[LightProcessor, Mock], caplog: pytest.LogCaptureFixture,
    use_dark: bool, use_flat: bool, expected: str,
) -> None:
    """Les titres suivent l’ordre des opérations et nomment les masters utilisés."""
    processor, run_script = calibration
    processor.calibration_processor.use_dark = use_dark
    processor.calibration_processor.calibration.use_dark = use_dark
    if not use_flat:
        for path in (processor.session_dir / "Flat").iterdir():
            path.unlink()
    caplog.set_level(logging.DEBUG)
    assert processor.process_session({"drizzle": "off"})
    info = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    stages = [message for message in info if message.startswith("===")]
    assert len(stages) == (2 if use_flat else 1)
    assert stages[-1].startswith(f"=== Calibration des lights {expected} : 2 images")
    if use_flat:
        assert stages[0].startswith("=== Création du master flat : 2 flats")
        assert info.index(stages[0]) < info.index(stages[-1])
        assert "-flat=" in run_script.call_args.args[0]
    else:
        assert "-flat=" not in run_script.call_args.args[0]
    assert ("-dark=" in run_script.call_args.args[0]) == use_dark
    assert any(message.startswith("Calibration terminée : 2 FITS") for message in info)
    details = ["Séquence préparée", "Séquence calibrée conservée"]
    if use_dark:
        details.append("  Light: T=")
    for detail in details:
        assert any(detail in r.getMessage() for r in caplog.records)
        assert not any(detail in message for message in info)


@pytest.mark.parametrize("dry_run", [False, True])
def test_calibration_reuse_does_not_announce_new_processing(
    calibration: tuple[LightProcessor, Mock], caplog: pytest.LogCaptureFixture,
    dry_run: bool,
) -> None:
    """Une relance indique les sorties réutilisées sans annoncer un nouveau flat."""
    processor, run_script = calibration
    assert processor.process_session({"drizzle": "off"})
    run_script.reset_mock()
    processor.dry_run = dry_run
    caplog.clear()
    caplog.set_level(logging.INFO)
    assert processor.process_session({"drizzle": "off"})
    run_script.assert_not_called()
    assert "=== Réutilisation des lights déjà calibrées" in caplog.text
    assert "=== Création du master flat" not in caplog.text
    assert "=== Calibration des lights" not in caplog.text
    assert ("[DRY-RUN] === Réutilisation" in caplog.text) == dry_run


def test_dry_run_keeps_script_details_in_debug(
    calibration: tuple[LightProcessor, Mock], caplog: pytest.LogCaptureFixture,
) -> None:
    """La simulation annonce les deux étapes sans noyer INFO dans le script."""
    processor, run_script = calibration
    processor.dry_run = True
    caplog.set_level(logging.DEBUG)
    assert processor.process_session({"drizzle": "off"})
    run_script.assert_not_called()
    info = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("[DRY-RUN] === Création du master flat" in message for message in info)
    assert any("[DRY-RUN] === Calibration des lights" in message for message in info)
    assert not any("Script Siril qui serait généré" in message for message in info)
    assert "Script Siril qui serait généré" in caplog.text


def test_failed_flat_does_not_announce_light_calibration(
    calibration: tuple[LightProcessor, Mock], caplog: pytest.LogCaptureFixture,
) -> None:
    """Un échec du master flat reste visible sans annoncer l’étape non exécutée."""
    processor, run_script = calibration
    run_script.side_effect = None
    run_script.return_value = False
    caplog.set_level(logging.INFO)
    assert not processor.process_session({"drizzle": "off"})
    assert "=== Création du master flat" in caplog.text
    assert "Échec de la création du master flat" in caplog.text
    assert "=== Calibration des lights" not in caplog.text


def test_missing_flat_dark_fails_before_light_calibration(
    calibration: tuple[LightProcessor, Mock],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Un dark flat absent garde l'annonce de l'étape qui ne peut aboutir."""
    processor, run_script = calibration
    flat_dark = (
        Path(processor.calibration_processor.dark_library_path) / "dark_flat.fit"
    )
    flat_dark.unlink()
    caplog.set_level(logging.INFO)

    assert not processor.process_session({"drizzle": "off"})

    assert "=== Création du master flat" in caplog.text
    assert "aucun master dark adapté pour calibrer les flats" in caplog.text
    assert "=== Calibration des lights" not in caplog.text
    run_script.assert_not_called()


@pytest.mark.parametrize('no_dark', [False, True])
def test_no_flat_ignores_existing_flats_and_their_dark(
    calibration: tuple[LightProcessor, Mock], monkeypatch: pytest.MonkeyPatch,
    no_dark: bool,
) -> None:
    """Le réglage lié par Config saute les flats présents et garde le mode dark."""
    import argparse
    from lib.config import Config

    processor, run_script = calibration
    parser = argparse.ArgumentParser()
    config = Config()
    config.register(SequenceCalibrationProcessor, parser)
    args = config.parse_args(parser, ['--no-flat', *(['--no-dark'] if no_dark else [])])
    config.bind_parameters(args)
    processor.calibration_processor = SequenceCalibrationProcessor(
        processor.siril, processor.work_dir, keep_intermediate=True,
        dark_library_path=processor.calibration_processor.dark_library_path,
    )
    selection = Mock(side_effect=AssertionError('Les flats ne doivent pas être lus'))
    monkeypatch.setattr(processor, '_select_flats_for_light_group', selection)
    assert list((processor.session_dir / 'Flat').glob('*.fit'))
    assert processor.process_session({'drizzle': 'off'})
    selection.assert_not_called()
    run_script.assert_called_once()
    script = run_script.call_args.args[0]
    assert '-flat=' not in script
    assert ('-dark=' in script) is not no_dark
    assert 'dark_flat.fit' not in script
    assert processor.get_output_files()


def test_flat_toggle_invalidates_calibration_cache(
    calibration: tuple[LightProcessor, Mock],
) -> None:
    """Chaque changement de mode recalcule ; le même mode reprend ses sorties."""
    import argparse

    processor, run_script = calibration
    dark_library = processor.calibration_processor.dark_library_path
    for use_flat, expected_calls in [(True, 2), (False, 1), (False, 0), (True, 2)]:
        processor.calibration_processor = SequenceCalibrationProcessor(
            processor.siril, processor.work_dir, keep_intermediate=True,
            dark_library_path=dark_library,
            args=argparse.Namespace(use_flat=use_flat),
        )
        run_script.reset_mock()
        assert processor.process_session({'drizzle': 'off'})
        assert run_script.call_count == expected_calls
        if expected_calls:
            script = run_script.call_args.args[0]
            assert ('-flat=' in script) is use_flat
