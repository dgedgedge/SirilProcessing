"""Chemins partagés, sélection Gaia vérifiée et remise à zéro limitée aux modèles."""

import hashlib
from pathlib import Path

import pytest

from lib.resource_cache import ResourceCache
from lib.gaia_catalogue import GaiaCatalogueInstaller


def test_shared_paths_and_legacy_defaults(tmp_path: Path) -> None:
    """Modèles et Gaia partagent un cache ; les surcharges ne sont pas redirigées."""
    cache = ResourceCache(tmp_path)
    assert cache.model_directory('cosmicclarity') == tmp_path / 'tmp/cache/models/cosmicclarity'
    assert cache.model_directory('graxpert', tmp_path / 'tmp/models/graxpert') == cache.model_directory('graxpert')
    assert cache.model_directory('graxpert', tmp_path / 'custom') == tmp_path / 'custom'
    assert cache.gaia_directory == tmp_path / 'tmp/cache/catalogues/gaia'
    with pytest.raises(ValueError):
        cache.model_directory('unknown')


def test_gaia_pointer_is_verified_not_trusted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un catalogue personnalisé est découvert ; une modification l'exclut du cache."""
    data = b'synthetic Gaia catalogue'
    monkeypatch.setattr(GaiaCatalogueInstaller, 'size', len(data))
    monkeypatch.setattr(GaiaCatalogueInstaller, 'sha256', hashlib.sha256(data).hexdigest())
    source = tmp_path / 'custom/catalogue.dat'
    source.parent.mkdir()
    source.write_bytes(data)
    cache = ResourceCache(tmp_path)
    assert cache.find_gaia() is None
    cache.register_gaia(source)
    assert cache.find_gaia() == source
    source.write_bytes(b'x' * len(data))
    assert cache.find_gaia() is None
    (cache.gaia_directory / 'location.json').write_text('invalid')
    assert cache.find_gaia() is None


def test_reset_removes_models_and_keeps_gaia_and_other_custom_files(tmp_path: Path) -> None:
    """Les modèles communs/anciens disparaissent, les données étrangères sont conservées."""
    from lib.cosmic_clarity.models import manifest

    cache = ResourceCache(tmp_path)
    for directory in (cache.directory / 'models', tmp_path / 'tmp/models', cache.gaia_directory):
        directory.mkdir(parents=True)
        (directory / 'payload').write_bytes(b'preserved Gaia or old weights')
    cosmic, graxpert = tmp_path / 'custom cosmic', tmp_path / 'custom graxpert'
    cosmic.mkdir()
    (graxpert / '3.0.2').mkdir(parents=True)
    known = cosmic / next(iter(manifest()))
    known.write_bytes(b'old weights')
    (cosmic / 'notes.txt').write_bytes(b'keep')
    (graxpert / '3.0.2/model.onnx').write_bytes(b'old weights')
    cache.reset_models((cosmic, graxpert))
    assert not (cache.directory / 'models').exists()
    assert not (tmp_path / 'tmp/models').exists()
    assert not known.exists() and not (graxpert / '3.0.2/model.onnx').exists()
    assert (cosmic / 'notes.txt').read_bytes() == b'keep'
    assert (cache.gaia_directory / 'payload').is_file()


def test_reset_refuses_linked_model_tree(tmp_path: Path) -> None:
    """Un lien sous le cache ne permet pas de nettoyer un dossier extérieur."""
    cache = ResourceCache(tmp_path)
    cache.directory.mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'keep').write_text('keep')
    (cache.directory / 'models').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='refusé'):
        cache.reset_models((tmp_path / 'cosmic', tmp_path / 'graxpert'))
    assert (outside / 'keep').read_text() == 'keep'


def test_processing_configuration_normalizes_old_model_defaults(tmp_path: Path) -> None:
    """Les options consommées par les moteurs suivent le nouveau défaut commun."""
    import argparse
    import json
    from lib.config import Config
    from lib.postprocess import _PostProcessorSequence

    cache = ResourceCache()
    config_file = tmp_path / 'config.json'
    config_file.write_text(json.dumps({
        'cosmic_model_dir': str(cache.root / 'tmp/models/cosmicclarity'),
        'graxpert_model_dir': str(cache.root / 'tmp/models/graxpert'),
    }))
    config = Config(config_file)
    parser = argparse.ArgumentParser()
    config.register(_PostProcessorSequence, parser)
    args = config.parse_args(parser, [])
    assert Path(args.cosmic_model_dir) == cache.model_directory('cosmicclarity')
    assert args.graxpert_model_dir == cache.model_directory('graxpert')


def test_cosmic_imports_legacy_weights_without_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le changement d'arborescence ne force aucun téléchargement de poids conformes."""
    from lib.cosmic_clarity import models

    legacy, destination = tmp_path / 'old', tmp_path / 'new'
    legacy.mkdir()
    data = b'verified weights'
    (legacy / 'sample.pth').write_bytes(data)
    monkeypatch.setattr(models, 'LEGACY_MODEL_DIR', legacy)
    monkeypatch.setattr(models, 'manifest', lambda: {'sample.pth': {
        'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'url': 'https://example.invalid',
    }})

    def forbidden(*args: object, **kwargs: object) -> None:
        """Interdit une acquisition distante alors que le cache ancien est disponible."""
        raise AssertionError('network')

    monkeypatch.setattr(models, 'urlopen', forbidden)
    result = models.ensure_model(destination, 'sample.pth')
    assert result.read_bytes() == data
    assert (legacy / 'sample.pth').read_bytes() == data
