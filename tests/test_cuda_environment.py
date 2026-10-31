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
    """Les index sont séparés et la résolution PyPI précède tout retrait CPU."""
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
    assert pip_calls[0][-1].endswith('requirements-cosmic-clarity.txt')
    assert '--dry-run' not in pip_calls[1]
    assert pip_calls[1][-1].endswith('requirements-cosmic-clarity.txt')
    assert '--dry-run' in pip_calls[2]
    assert pip_calls[2][-1].endswith('requirements-cuda.txt')
    assert pip_calls[3][-4:] == ['uninstall', '-y', 'onnxruntime', 'cupy-cuda13x']
    assert pip_calls[4][-1].endswith('requirements-cuda.txt')
    assert '--force-reinstall' in pip_calls[-3]
    assert pip_calls[-2][-5:] == [
        '--upgrade', '--index-url', 'https://pypi.org/simple',
        '-r', str(tmp_path / 'requirements-power.txt'),
    ]
    assert '--only-binary=:all:' not in pip_calls[-2]
    assert pip_calls[-1][-1] == 'check'
    assert all(command[command.index('--python') + 1] == str(installer.python)
               for command in pip_calls)
    assert commands[0][1:4] == ['-m', 'venv', '--without-pip']


@pytest.mark.parametrize('failed_resolution', [1, 2])
def test_resolver_failure_keeps_existing_conflicting_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_resolution: int,
) -> None:
    """Un échec de l'une des résolutions ne retire pas ONNX CPU ou une variante CuPy."""
    commands = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        """Échoue avant toute désinstallation ou installation GPU."""
        commands.append(command)
        resolutions = sum('--dry-run' in previous for previous in commands)
        if '--dry-run' in command and resolutions == failed_resolution:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(subprocess.CalledProcessError):
        CudaEnvironmentInstaller(tmp_path).install(install_only=True)
    assert not any('uninstall' in command for command in commands)
    assert len(commands) == (2 if failed_resolution == 1 else 4)


def test_cuda_requirements_do_not_merge_package_indexes() -> None:
    """PyTorch et PyPI sont isolés et partagent exactement le même pin torch."""
    root = Path(__file__).resolve().parents[1]
    torch = (root / 'requirements-cosmic-clarity.txt').read_text()
    cuda = (root / 'requirements-cuda.txt').read_text()
    assert '--index-url https://download.pytorch.org/whl/cu128' in torch
    assert '--index-url https://pypi.org/simple' in cuda
    assert '--extra-index-url' not in torch + cuda
    assert not any(line.startswith('-r ') for line in cuda.splitlines())
    torch_pins = [line for line in torch.splitlines() if line.startswith('torch==')]
    cuda_pins = [line for line in cuda.splitlines() if line.startswith('torch==')]
    assert torch_pins == cuda_pins == ['torch==2.10.0+cu128']


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
    result = subprocess.run([str(root / 'bin/install_dependances.sh'), '--help'],
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


def test_verify_uses_generic_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le contrôle CUDA lance l'entrée commune avec le Python du venv choisi."""
    from unittest.mock import Mock

    installer = CudaEnvironmentInstaller(tmp_path)
    installer.venv.mkdir()
    (installer.venv / 'pyvenv.cfg').write_text('')
    monkeypatch.setattr(installer, 'preflight', lambda: None)
    run = Mock()
    monkeypatch.setattr(subprocess, 'run', run)
    installer.verify()
    assert run.call_args.args[0] == [str(installer.python),
                                   str(tmp_path / 'bin/install_dependances.py'),
                                   '--venv', str(installer.venv), '--verify-worker']


def test_powerguard_install_failure_is_propagated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ne pas annoncer une installation réussie si la compilation D-Bus échoue."""
    commands = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        """Simule une erreur de pip limitée aux dépendances PowerGuard."""
        commands.append(command)
        if any(part.endswith('requirements-power.txt') for part in command):
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, stdout='[]')

    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(subprocess.CalledProcessError):
        CudaEnvironmentInstaller(tmp_path).install(install_only=True)
    assert commands[-1][-1] == str(tmp_path / 'requirements-power.txt')
    assert not any(command[-1] == 'check' for command in commands)
