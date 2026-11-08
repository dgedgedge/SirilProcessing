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



@pytest.mark.parametrize('mode', ['native', 'appimage', 'flatpak'])
def test_session_factory_selects_launcher_without_starting_gui(tmp_path, monkeypatch, mode):
    """La factory conserve les modes historiques et prépare le bon lanceur CLI."""
    import subprocess
    executable = _fake_executable(tmp_path)
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, stdout='org.siril.Siril'))
    service = Siril(str(executable), mode)
    session = service.session(tmp_path, 'registration')
    assert session.process is None
    assert session.log_path == tmp_path / 'registration.log'
    if mode == 'flatpak':
        assert session.launcher[:2] == ['flatpak', 'run']
        assert '--command=siril-cli' in session.launcher
    else:
        assert session.launcher == [str(executable)]


def test_prepare_commands_applies_one_api_header_with_current_cpu_limit():
    """Les appels typés gardent la priorité à la version et au réglage CPU communs."""
    from lib.siril_api import command
    prepared = Siril.prepare_commands([command('requires', '1.2'), command('load', 'image.fits')])
    assert [operation.name for operation in prepared] == ['requires', 'setcpu', 'setext', 'load']
    assert prepared[0].arguments == ('1.4.4',)
    assert prepared[1].arguments == (str(CpuConfig.get_limit()),)
    assert prepared[2].arguments == ('fits',)
    with pytest.raises(TypeError):
        Siril.prepare_commands(['load image'])
