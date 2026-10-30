"""Téléchargement Gaia simulé : SHA-256, décompression et préservation des caches."""

import bz2
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.gaia_catalogue import GaiaCatalogueInstaller


@pytest.fixture
def catalogue(monkeypatch: pytest.MonkeyPatch) -> tuple[GaiaCatalogueInstaller, bytes]:
    """Épingle un catalogue miniature pour tester le vrai parcours d'installation."""
    data = b'Gaia synthetic catalogue' * 150
    archive = bz2.compress(data)
    for key, value in {
        'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
        'archive_size': len(archive), 'archive_sha256': hashlib.sha256(archive).hexdigest(),
    }.items():
        monkeypatch.setattr(GaiaCatalogueInstaller, key, value)
    monkeypatch.setattr('lib.gaia_catalogue.urlopen', lambda *args, **kwargs: io.BytesIO(archive))
    return GaiaCatalogueInstaller(), data


def test_installs_and_reuses_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, catalogue: tuple[GaiaCatalogueInstaller, bytes],
) -> None:
    """L'installation décompresse et la deuxième passe vérifie sans accès réseau."""
    installer, data = catalogue
    result = installer.install(tmp_path)
    assert result.read_bytes() == data

    def forbidden(*args: object, **kwargs: object) -> None:
        """Interdit tout téléchargement d'un fichier déjà conforme."""
        raise AssertionError('network')

    monkeypatch.setattr('lib.gaia_catalogue.urlopen', forbidden)
    assert installer.install(tmp_path) == result
    other = tmp_path / 'unused'
    imported = installer.install(other, (result,))
    assert imported == other / installer.filename
    assert imported.samefile(result)


@pytest.mark.parametrize('failure', ['archive_sha', 'archive_size', 'file_sha', 'file_size', 'network', 'disk'])
def test_failed_install_keeps_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, catalogue: tuple[GaiaCatalogueInstaller, bytes],
    failure: str,
) -> None:
    """Les erreurs d'intégrité, d'espace ou de réseau conservent la destination."""
    installer, _ = catalogue
    destination = tmp_path / installer.filename
    destination.write_bytes(b'previous')
    if failure == 'archive_sha':
        monkeypatch.setattr(installer, 'archive_sha256', '0' * 64)
    elif failure == 'archive_size':
        monkeypatch.setattr(installer, 'archive_size', 1)
    elif failure == 'file_sha':
        monkeypatch.setattr(installer, 'sha256', '0' * 64)
    elif failure == 'file_size':
        monkeypatch.setattr(installer, 'size', 1)
    elif failure == 'network':
        def fail(*args: object, **kwargs: object) -> None:
            """Simule une interruption avant réception des données."""
            raise OSError('network')
        monkeypatch.setattr('lib.gaia_catalogue.urlopen', fail)
    else:
        monkeypatch.setattr('lib.gaia_catalogue.shutil.disk_usage', lambda path: SimpleNamespace(free=0))
    with pytest.raises((ValueError, OSError)):
        installer.install(tmp_path)
    assert destination.read_bytes() == b'previous'
    assert list(tmp_path.iterdir()) == [destination]
