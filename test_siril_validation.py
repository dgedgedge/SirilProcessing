import os

import pytest

from lib.siril_utils import Siril


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
    assert script_path.read_text() == "requires 1.2\nclose\n"
