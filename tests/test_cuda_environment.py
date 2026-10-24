"""Installation CUDA isolée, résolution préalable et vérifications sans faux succès."""

import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from lib.cuda_environment import CudaEnvironmentInstaller


@pytest.mark.parametrize('mode', ['default', 'env', 'explicit', 'legacy'])
def test_venv_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """L'installateur cible le même venv que les lanceurs et accepte les espaces."""
    monkeypatch.delenv('VENV_DIR', raising=False)
    selected = tmp_path / '.venv'
    explicit = None
    if mode == 'env':
        selected = tmp_path / 'custom env'
        monkeypatch.setenv('VENV_DIR', str(selected))
    elif mode == 'explicit':
        selected = tmp_path / 'explicit'
        explicit = selected
        monkeypatch.setenv('VENV_DIR', str(tmp_path / 'ignored'))
    elif mode == 'legacy':
        selected = tmp_path / 'venv'
        selected.mkdir()
    installer = CudaEnvironmentInstaller(tmp_path, explicit)
    assert installer.venv == selected
    assert installer.python == selected / 'bin/python'
    with pytest.raises(ValueError):
        CudaEnvironmentInstaller(tmp_path, Path(sys.base_prefix))


def test_installer_resolves_before_removing_conflicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La résolution précède le retrait CPU et toutes les commandes pip visent le venv."""
    commands = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        """Simule seulement les commandes externes, sans installer de paquets."""
        commands.append(command)
        output = json.dumps(['onnxruntime', 'cupy-cuda13x']) if '-c' in command else ''
        return subprocess.CompletedProcess(command, 0, stdout=output)

    monkeypatch.setattr(subprocess, 'run', run)
    installer = CudaEnvironmentInstaller(tmp_path)
    installer.install(install_only=True)
    pip_calls = [command for command in commands if 'pip' in command]
    assert '--dry-run' in pip_calls[0]
    assert pip_calls[1][-4:] == ['uninstall', '-y', 'onnxruntime', 'cupy-cuda13x']
    assert '--force-reinstall' in pip_calls[-2]
    assert pip_calls[-1][-1] == 'check'
    assert all(command[command.index('--python') + 1] == str(installer.python)
               for command in pip_calls)
    assert commands[0][1:4] == ['-m', 'venv', '--without-pip']


def test_resolver_failure_keeps_existing_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un échec de résolution ne désinstalle pas le runtime existant."""
    commands = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        """Échoue avant toute désinstallation ou installation GPU."""
        commands.append(command)
        if '--dry-run' in command:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(subprocess.CalledProcessError):
        CudaEnvironmentInstaller(tmp_path).install(install_only=True)
    assert not any('uninstall' in command for command in commands)
    assert len(commands) == 2


def test_driver_failure_prevents_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L'absence de pilote ne produit ni venv ni annonce de réussite CUDA."""
    commands = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        """Signale un pilote absent dès le précontrôle."""
        commands.append(command)
        raise FileNotFoundError('nvidia-smi')

    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(RuntimeError, match='Pilote NVIDIA inaccessible'):
        CudaEnvironmentInstaller(tmp_path).install()
    assert commands == [['nvidia-smi', '-L']]
    assert not (tmp_path / '.venv').exists()


def test_onnx_probe_is_valid() -> None:
    """Le petit modèle de contrôle produit bien X×X, sans modèle astronomique."""
    ort = pytest.importorskip('onnxruntime')
    ort.disable_telemetry_events()
    session = ort.InferenceSession(CudaEnvironmentInstaller.onnx_probe(),
                                   providers=['CPUExecutionProvider'])
    result = session.run(None, {'X': np.ones((2, 2), dtype=np.float32)})[0]
    np.testing.assert_array_equal(result, np.full((2, 2), 2, dtype=np.float32))


def test_help_has_no_installation_side_effects(tmp_path: Path) -> None:
    """L'aide du script Bash n'installe ni ne crée d'environnement."""
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([str(root / 'bin/install_cuda.sh'), '--help'],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0
    assert '--check-only' in result.stdout and '--install-only' in result.stdout
    assert not (tmp_path / '.venv').exists()


def test_non_venv_directory_is_not_overwritten(tmp_path: Path) -> None:
    """Un dossier contenant des données ordinaires n'est pas recyclé en venv."""
    target = tmp_path / 'data'
    target.mkdir()
    original = target / 'important.txt'
    original.write_text('preserved')
    with pytest.raises(ValueError, match='ne contient pas de venv'):
        CudaEnvironmentInstaller(tmp_path, target)
    assert original.read_text() == 'preserved'
