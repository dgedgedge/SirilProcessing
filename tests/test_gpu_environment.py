"""Protection commune des cibles venv et des graphes de diagnostic GPU."""

from pathlib import Path

import pytest

from lib.gpu_environment import GpuEnvironmentInstaller


def test_installer_rejects_file_as_venv(tmp_path: Path) -> None:
    """Une cible fichier n'est jamais recyclée ou remplacée par un environnement."""
    target = tmp_path / 'observation.fits'
    target.write_bytes(b'preserved')
    with pytest.raises(ValueError, match='répertoire'):
        GpuEnvironmentInstaller(tmp_path, target)
    assert target.read_bytes() == b'preserved'


def test_probe_rejects_unknown_operator() -> None:
    """Le générateur protobuf limité ne fabrique pas un graphe arbitraire invalide."""
    with pytest.raises(ValueError, match='Opération de contrôle'):
        GpuEnvironmentInstaller.onnx_probe('unsupported')
