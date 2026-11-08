import os

import pytest

from lib.siril import Siril
from lib.cpu_config import CpuConfig


@pytest.fixture(autouse=True)
def restore_siril_defaults():
    original_path, original_mode = Siril.get_default_config()
    yield
    Siril._default_siril_path = original_path
    Siril._default_siril_mode = original_mode


def _fake_executable(tmp_path):
    executable = tmp_path / "siril"
    executable.write_text("#!/usr/bin/env sh\nexit 0\n")
    executable.chmod(executable.stat().st_mode | 0o111)
    return executable


def test_configure_defaults_validation(tmp_path):
    executable = _fake_executable(tmp_path)

    Siril.configure_defaults(siril_path=str(executable), siril_mode="native")
    assert Siril.get_default_config() == (str(executable), "native")

    with pytest.raises(ValueError):
        Siril.configure_defaults(siril_mode="invalid_mode")

    with pytest.raises(ValueError):
        Siril.configure_defaults(siril_path="/chemin/inexistant/siril", siril_mode="native")


def test_instance_creation_validation(tmp_path):
    executable = _fake_executable(tmp_path)
    Siril.configure_defaults(siril_path=str(executable), siril_mode="native")

    siril = Siril.create_with_defaults()
    assert siril.is_validated is True

    with pytest.raises(ValueError):
        Siril(siril_mode="mode_inexistant")

    with pytest.raises(ValueError):
        siril.siril_mode = "mode_invalide"

    assert siril.siril_mode == "native"


def test_appimage_validation_accepts_executable_file(tmp_path):
    executable = _fake_executable(tmp_path)

    Siril.configure_defaults(siril_path=str(executable), siril_mode="appimage")
    siril = Siril.create_with_defaults()

    assert os.path.isfile(siril.siril_path)
    assert siril.siril_mode == "appimage"
    assert siril.is_validated is True


def test_run_siril_script_keeps_command_file(tmp_path):
    executable = _fake_executable(tmp_path)
    siril = Siril(siril_path=str(executable), siril_mode="native")

    success = siril.run_siril_script(
        "requires 1.2\nclose\n",
        str(tmp_path / "work"),
        script_name="calibration_test.sps",
    )

    script_path = tmp_path / "work" / "calibration_test.sps"
    assert success is True
    assert script_path.exists()
    assert script_path.read_text() == (
        f"requires 1.4\nsetcpu {CpuConfig.get_limit()}\nsetext fits\nclose\n"
    )


def test_sigkill_reports_signal_and_memory_diagnostic(tmp_path, monkeypatch, caplog):
    import subprocess
    executable = _fake_executable(tmp_path)
    siril = Siril(siril_path=str(executable), siril_mode='native')
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: subprocess.CompletedProcess(args[0], -9))
    with pytest.raises(RuntimeError, match="Échec fatal du script Siril"):
        siril.run_siril_script('close', str(tmp_path), 'killed.sps')
    assert 'SIGKILL' in caplog.text
    assert 'pression mémoire' in caplog.text


@pytest.mark.parametrize('exit_code', [0, 1])
def test_script_output_saved_to_log_and_logged_only_on_failure(tmp_path, caplog, exit_code):
    import logging

    executable = _fake_executable(tmp_path)
    executable.write_text(
        '#!/usr/bin/env sh\nprintf "sortie normale\\n"\n'
        'printf "diagnostic erreur\\n" >&2\n'
        f'exit {exit_code}\n'
    )
    siril = Siril(siril_path=str(executable), siril_mode='native')
    log_path = tmp_path / '04_stacking.log'
    log_path.write_text('ancien résultat')
    with caplog.at_level(logging.DEBUG):
        if exit_code:
            with pytest.raises(RuntimeError, match="Échec fatal du script Siril"):
                siril.run_siril_script('close', str(tmp_path), '04_stacking.sps')
        else:
            assert siril.run_siril_script(
                'close', str(tmp_path), '04_stacking.sps',
            )
    assert log_path.read_text() == 'sortie normale\ndiagnostic erreur\n'
    output_records = [r for r in caplog.records if 'sortie normale' in r.getMessage()]
    if exit_code:
        assert len(output_records) == 1
        assert output_records[0].levelno == logging.ERROR
        assert 'diagnostic erreur' in output_records[0].getMessage()
    else:
        assert not output_records


@pytest.mark.parametrize('mode', ['native', 'appimage', 'flatpak'])
def test_all_siril_modes_set_fits_before_image_commands(tmp_path, monkeypatch, mode):
    """L'en-tête commun impose .fits pour chaque mode d'exécution Siril."""
    import subprocess

    executable = _fake_executable(tmp_path)
    calls = []

    def run(command, **kwargs):
        """Simule la validation de Flatpak et capture la commande exécutée."""
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout='org.siril.Siril', stderr='')

    monkeypatch.setattr(subprocess, 'run', run)
    siril = Siril(siril_path=str(executable), siril_mode=mode)
    assert siril.run_siril_script(
        '# Test\nrequires 1.2\nload image\nsave result\nclose\n',
        str(tmp_path / 'work'), 'default_extension.sps',
    )
    script = (tmp_path / 'work/default_extension.sps').read_text()
    assert script.count('setext fits') == 1
    assert script.index('requires 1.4') < script.index('setext fits')
    assert script.index('setext fits') < script.index('load image')
    assert script.index('setext fits') < script.index('save result')
    assert calls[-1][0] == ('flatpak' if mode == 'flatpak' else str(executable))


@pytest.mark.parametrize('body', ['close', '# Note\nrequires 1.2\nclose\n', '\n# Note\nclose'])
def test_common_script_preparation_has_a_single_extension_setting(body: str) -> None:
    """L'en-tête partagé conserve le corps et fixe une seule fois version et extension."""
    script = Siril.prepare_script(body)
    assert script.count('requires ') == 1
    assert script.count('setext fits') == 1
    assert script.index('setext fits') < script.index('close')
    assert script.endswith('close\n' if body.endswith('\n') else 'close')
    if '# Note' in body:
        assert script.index('# Note') < script.index('requires 1.4')
