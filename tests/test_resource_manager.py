"""Point d'entrée partagé des ressources, intégrité et absence de réseau sur cache valide."""

import argparse
import bz2
import configparser
import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest

from lib.resource_manager import ResourceManager
from lib.resource_cache import ResourceCache


def test_existing_project_weight_is_used_without_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un poids local fourni avec le projet ne déclenche aucune acquisition."""
    from lib.cosmic_clarity import models

    directory = tmp_path / 'lib/weights'
    directory.mkdir(parents=True)
    payload = b'weight provided locally'
    path = directory / 'model.pth'
    path.write_bytes(payload)
    entry = {'url': 'https://invalid.example/never', 'size': len(payload),
             'sha256': hashlib.sha256(payload).hexdigest()}
    monkeypatch.setattr(models, 'manifest', lambda: {'model.pth': entry})
    monkeypatch.setattr(models, 'urlopen', lambda *args, **kwargs: pytest.fail('network'))
    manager = ResourceManager(argparse.Namespace(cosmic_model_dir=directory), tmp_path)
    assert manager.cosmic_model('model.pth') == path
    assert manager.cosmic_models() == directory
    assert path.read_bytes() == payload
    assert list(directory.iterdir()) == [path]


def test_catalogue_lookup_never_acquires_or_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """La photométrie et l'installateur partagent le service sans acquisition implicite."""
    monkeypatch.setattr(ResourceCache, 'find_gaia', lambda self: None)
    monkeypatch.setattr(ResourceCache, 'find_spcc', lambda self: None)
    monkeypatch.setattr('lib.gaia_catalogue.GaiaCatalogueInstaller.install',
                        lambda *args, **kwargs: pytest.fail('astrometry download'))
    monkeypatch.setattr('lib.gaia_spcc.GaiaSPCCInstaller.install',
                        lambda *args, **kwargs: pytest.fail('spectral download'))
    manager = ResourceManager(root=tmp_path)
    assert manager.gaia_astrometry() is None
    assert manager.gaia_spcc() is None
    assert list(tmp_path.iterdir()) == []


def test_verified_catalogues_are_shared_without_server_access(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Même en mode acquisition, un cache connu valide ne contacte pas le serveur."""
    astro, spectral = tmp_path / 'astrometry.dat', tmp_path / 'spectra'
    monkeypatch.setattr(ResourceCache, 'find_gaia', lambda self: astro)
    monkeypatch.setattr(ResourceCache, 'find_spcc', lambda self: spectral)
    monkeypatch.setattr('lib.gaia_catalogue.GaiaCatalogueInstaller.install',
                        lambda *args, **kwargs: pytest.fail('astrometry download'))
    monkeypatch.setattr('lib.gaia_spcc.GaiaSPCCInstaller.install',
                        lambda *args, **kwargs: pytest.fail('spectral download'))
    manager = ResourceManager(root=tmp_path)
    assert manager.gaia_astrometry() == manager.gaia_astrometry(acquire=True) == astro
    monkeypatch.setattr('lib.gaia_spcc.GaiaSPCCInstaller.valid_directory', lambda self, path: False)
    assert manager.gaia_spcc(coverage='all') is None
    assert manager.gaia_spcc() == spectral
    monkeypatch.setattr('lib.gaia_spcc.GaiaSPCCInstaller.valid_directory', lambda self, path: True)
    assert manager.gaia_spcc() == manager.gaia_spcc(acquire=True) == spectral
    import json
    metadata = tmp_path / 'tmp/cache/catalogues/gaia/spcc-location.json'
    assert json.loads(metadata.read_text())['coverage'] == 'north'
    assert manager.gaia_spcc(acquire=True, coverage='all') == spectral
    assert json.loads(metadata.read_text())['coverage'] == 'all'


def test_common_catalogue_acquisition_publishes_discoverable_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une vraie petite acquisition devient utilisable par un second gestionnaire."""
    import io
    from lib.gaia_catalogue import GaiaCatalogueInstaller

    payload = b'Gaia fixture' * 100
    archive = bz2.compress(payload)
    monkeypatch.setattr(GaiaCatalogueInstaller, 'size', len(payload))
    monkeypatch.setattr(GaiaCatalogueInstaller, 'sha256', hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(GaiaCatalogueInstaller, 'archive_size', len(archive))
    monkeypatch.setattr(GaiaCatalogueInstaller, 'archive_sha256', hashlib.sha256(archive).hexdigest())
    monkeypatch.setattr('lib.gaia_catalogue.urlopen', lambda *args, **kwargs: io.BytesIO(archive))
    preferences = configparser.ConfigParser()
    monkeypatch.setattr('lib.star_separation.StarNetSeparator.preferences', lambda args: (preferences, None))
    directory = tmp_path / 'custom'
    path = ResourceManager(root=tmp_path).gaia_astrometry(directory, acquire=True)
    assert path.read_bytes() == payload
    monkeypatch.setattr('lib.gaia_catalogue.urlopen', lambda *args, **kwargs: pytest.fail('network'))
    assert ResourceManager(root=tmp_path).gaia_astrometry() == path
    assert ResourceManager(root=tmp_path).gaia_astrometry(directory) == path


def test_shared_graxpert_provider_and_read_only_detection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le calcul conserve son fournisseur et la détection automatique n'installe rien."""
    from lib.graxpert_models import GraXpertModels

    local = tmp_path / 'model.onnx'
    monkeypatch.setattr(GraXpertModels, 'find_local_model', lambda self: local)
    monkeypatch.setattr(GraXpertModels, 'ensure_model', lambda self: pytest.fail('detection installed'))
    manager = ResourceManager(root=tmp_path)
    assert manager.find_graxpert() == local
    provider = Mock(spec=GraXpertModels)
    provider.ensure_model.return_value = local
    assert manager.graxpert_model(provider) == local
    provider.ensure_model.assert_called_once_with()


def test_all_adapters_share_integrity_implementation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Astrométrie, archives SPCC et deux familles de poids utilisent les mêmes contrôles."""
    from lib.gaia_catalogue import GaiaCatalogueInstaller
    from lib.gaia_spcc import GaiaSPCCInstaller
    from lib.graxpert_models import GraXpertModels
    from lib.cosmic_clarity.models import sha256_file

    path = tmp_path / 'data'
    path.write_bytes(b'payload')
    calls = []

    def checksum(candidate: Path, algorithm: str = 'sha256') -> str:
        """Trace les algorithmes utilisés par les points d'entrée compatibles."""
        calls.append((candidate, algorithm))
        return 'verified'

    monkeypatch.setattr(ResourceManager, 'file_checksum', staticmethod(checksum))
    assert GaiaCatalogueInstaller.matches(path, 7, 'verified')
    assert sha256_file(path) == 'verified'
    assert GraXpertModels.checksum(path) == 'verified'
    assert GaiaSPCCInstaller.archive_matches(path, {'archive_size': 7, 'archive_md5': 'verified'})
    assert calls == [(path, 'sha256')] * 3 + [(path, 'md5')]


def test_integrity_checks_reject_missing_truncated_and_corrupt_data(tmp_path: Path) -> None:
    """Un nom local seul ne dispense pas de vérifier taille et SHA du manifeste."""
    path = tmp_path / 'payload'
    digest = hashlib.sha256(b'valid').hexdigest()
    assert not ResourceManager.matches(path, 5, digest)
    path.write_bytes(b'bad')
    assert not ResourceManager.matches(path, 5, digest)
    path.write_bytes(b'xxxxx')
    assert not ResourceManager.matches(path, 5, digest)
    path.write_bytes(b'valid')
    assert ResourceManager.matches(path, 5, digest)
    with pytest.raises(ValueError):
        ResourceManager.file_checksum(path, 'unsupported')


def test_resource_options_are_independent_snapshots(tmp_path: Path) -> None:
    """Le gestionnaire ne dépend pas de mutations ultérieures du namespace appelant."""
    args = argparse.Namespace(cosmic_model_dir=tmp_path / 'first')
    first = ResourceManager(args, tmp_path)
    args.cosmic_model_dir = tmp_path / 'second'
    second = ResourceManager(args, tmp_path)
    assert first.args.cosmic_model_dir == tmp_path / 'first'
    assert second.args.cosmic_model_dir == tmp_path / 'second'


def test_cosmic_runtime_requests_the_shared_resource_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """L'inférence demande son poids au gestionnaire avant tout chargement Torch."""
    from lib.cosmic_clarity.inference import CosmicClarityEngine

    error = RuntimeError('resource service sentinel')
    acquire = Mock(side_effect=error)
    monkeypatch.setattr(ResourceManager, 'cosmic_model', acquire)
    engine = CosmicClarityEngine.__new__(CosmicClarityEngine)
    engine.directory = tmp_path
    with pytest.raises(RuntimeError, match='resource service sentinel') as raised:
        engine._load('requested.pth')
    assert raised.value is error
    acquire.assert_called_once_with('requested.pth', tmp_path)
