"""Installation Apple Silicon simulée et contrôle réel du graphe ONNX portable."""

import json
from pathlib import Path
import platform
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from lib.macos_environment import MacOSEnvironmentInstaller


def apple_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simule un Python natif sur un Mac récent ; aucun GPU n'est simulé ici."""
    monkeypatch.setattr(platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(platform, 'machine', lambda: 'arm64')
    monkeypatch.setattr(platform, 'mac_ver', lambda: ('14.6', (), ''))


@pytest.mark.parametrize('invalid', ['os', 'arch', 'version', 'python'])
def test_rejects_unsupported_platform_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str,
) -> None:
    """Linux, Rosetta, ancien macOS ou Python non couvert ne créent aucun venv."""
    apple_platform(monkeypatch)
    if invalid == 'os':
        monkeypatch.setattr(platform, 'system', lambda: 'Linux')
    elif invalid == 'arch':
        monkeypatch.setattr(platform, 'machine', lambda: 'x86_64')
    elif invalid == 'version':
        monkeypatch.setattr(platform, 'mac_ver', lambda: ('13.6', (), ''))
    else:
        monkeypatch.setattr(sys, 'version_info', (3, 10))
    with pytest.raises(RuntimeError):
        MacOSEnvironmentInstaller(tmp_path).install()
    assert not (tmp_path / '.venv').exists()


@pytest.mark.parametrize('fail', [False, True])
def test_install_resolves_before_removing_onnx_gpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: bool,
) -> None:
    """La résolution native précède le retrait des variantes ONNX et ne mêle aucun CUDA."""
    apple_platform(monkeypatch)
    commands = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        """Simule les processus, avec inventaire du seul paquet ONNX incompatible."""
        commands.append(command)
        if fail and '--dry-run' in command:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(['onnxruntime-gpu']))

    monkeypatch.setattr(subprocess, 'run', run)
    installer = MacOSEnvironmentInstaller(tmp_path)
    if fail:
        with pytest.raises(subprocess.CalledProcessError):
            installer.install()
        assert len(commands) == 2
    else:
        installer.install()
        pip = [command for command in commands if 'pip' in command]
        assert '--dry-run' in pip[0]
        assert pip[0][-1].endswith('requirements-macos.txt')
        assert pip[1][-3:] == ['uninstall', '-y', 'onnxruntime-gpu']
        assert '--force-reinstall' in pip[-2] and pip[-1][-1] == 'check'
        assert all(command[command.index('--python') + 1] == str(installer.python)
                   for command in pip)
        assert not any('requirements-cuda.txt' in part for command in pip for part in command)
        assert not any('requirements-power.txt' in part for command in pip for part in command)


def test_coreml_probe_is_valid_on_cpu() -> None:
    """Le graphe Add est valide indépendamment de la disponibilité de CoreML."""
    ort = pytest.importorskip('onnxruntime')
    session = ort.InferenceSession(MacOSEnvironmentInstaller.onnx_probe('Add'),
                                   providers=['CPUExecutionProvider'])
    result = session.run(None, {'X': np.ones((2, 2), dtype=np.float32)})[0]
    np.testing.assert_array_equal(result, 2)


def test_help_and_check_only_without_installation(tmp_path: Path) -> None:
    """L'aide commune est portable ; tester un venv absent ne le crée pas."""
    root = Path(__file__).resolve().parents[1]
    help_result = subprocess.run([str(root / 'bin/install_dependances.sh'), '--help'],
                                 capture_output=True, text=True)
    assert help_result.returncode == 0
    assert '--check-only' in help_result.stdout
    if platform.system() != 'Darwin':
        result = subprocess.run([str(root / 'bin/install_dependances.sh'), '--test',
                                 '--venv', str(tmp_path / 'env')], capture_output=True, text=True)
        assert result.returncode == 1
        assert not (tmp_path / 'env').exists()


def test_native_pins_have_no_cuda_index() -> None:
    """Les roues Mac proviennent du seul PyPI et couvrent les deux moteurs IA."""
    text = (Path(__file__).resolve().parents[1] / 'requirements-macos.txt').read_text()
    assert '--index-url https://pypi.org/simple' in text
    assert 'torch==2.10.0' in text and 'onnxruntime==1.26.0' in text
    assert '--extra-index-url' not in text and '+cu' not in text


@pytest.mark.parametrize('mode', ['success', 'missing', 'cpu_only', 'incorrect'])
def test_worker_checks_mps_and_executed_coreml_nodes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    """Des fournisseurs annoncés seuls ne suffisent pas ; les doubles simulent le matériel."""
    from contextlib import nullcontext

    monkeypatch.setattr(MacOSEnvironmentInstaller, 'check_platform', lambda: None)
    tensor = Mock()
    network = Mock()
    network.eval.return_value = network
    network.to.return_value = network
    network.return_value = tensor
    tensor.cpu.return_value = tensor
    tensor.numpy.return_value = np.ones((1, 3, 16, 16), dtype=np.float32)
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(
        __version__='test', full=lambda *args: tensor, inference_mode=nullcontext,
    ))
    # Les deux modules Cosmic sont remplacés, sans import dépendant de PyTorch.
    selector = Mock(return_value='mps')
    monkeypatch.setitem(sys.modules, 'lib.cosmic_clarity.inference',
                        SimpleNamespace(select_device=selector))
    monkeypatch.setitem(sys.modules, 'lib.cosmic_clarity.network',
                        SimpleNamespace(RestorationCNN=lambda: network))
    trace = tmp_path / 'profile.json'
    provider = 'CPUExecutionProvider' if mode == 'cpu_only' else 'CoreMLExecutionProvider'
    trace.write_text(json.dumps([{'args': {'provider': provider}}]))
    session = Mock()
    session.end_profiling.return_value = str(trace)
    session.run.return_value = [np.full((2, 2), 0 if mode == 'incorrect' else 2)]
    monkeypatch.setitem(sys.modules, 'onnxruntime', SimpleNamespace(
        __version__='test', disable_telemetry_events=lambda: None,
        get_available_providers=lambda: [] if mode == 'missing' else ['CoreMLExecutionProvider'],
        SessionOptions=Mock, InferenceSession=Mock(return_value=session),
    ))
    if mode == 'success':
        MacOSEnvironmentInstaller.verify_worker()
        selector.assert_called_once_with('mps')
        network.to.assert_called_once_with('mps')
    else:
        with pytest.raises(AssertionError if mode == 'incorrect' else RuntimeError):
            MacOSEnvironmentInstaller.verify_worker()


def test_verify_uses_selected_venv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le contrôle ne prépare pas le venv et lance son Python, pas celui du système."""
    apple_platform(monkeypatch)
    installer = MacOSEnvironmentInstaller(tmp_path)
    with pytest.raises(ValueError, match='Venv absent'):
        installer.verify()
    installer.venv.mkdir()
    (installer.venv / 'pyvenv.cfg').write_text('')
    run = Mock()
    monkeypatch.setattr(subprocess, 'run', run)
    installer.verify()
    assert run.call_args.args[0][0] == str(installer.python)
    assert run.call_args.args[0][1] == str(tmp_path / 'bin/install_dependances.py')
    assert run.call_args.args[0][2:4] == ['--venv', str(installer.venv)]
    assert run.call_args.args[0][-1] == '--verify-worker'


@pytest.mark.skipif(platform.system() != 'Darwin' or platform.machine() != 'arm64',
                    reason='Contrôle matériel Apple Silicon uniquement')
def test_actual_apple_accelerators() -> None:
    """Sur Apple Silicon, exige le calcul MPS et la trace d'exécution CoreML."""
    MacOSEnvironmentInstaller.verify_worker()
