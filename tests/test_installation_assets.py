"""Préchargement des ressources, priorités de chemins et Gaia explicitement optionnel."""

import argparse
import configparser
import json
from pathlib import Path

import pytest

from lib.installation_assets import InstallationAssets


def options(tmp_path: Path, *flags: str) -> argparse.Namespace:
    """Parse les options réelles sans lire la configuration personnelle."""
    parser = argparse.ArgumentParser()
    InstallationAssets.add_arguments(parser)
    return parser.parse_args(['--config', str(tmp_path / 'config.json'), *flags])


def test_models_default_and_gaia_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Les sept poids sont préchargés par défaut ; Gaia ne l'est jamais implicitement."""
    from lib.cosmic_clarity import models
    from lib.graxpert_models import GraXpertModels
    from lib.gaia_catalogue import GaiaCatalogueInstaller

    calls = []
    monkeypatch.setattr(models, 'install_models', lambda directory: calls.append(('cosmic', directory)))
    monkeypatch.setattr(GraXpertModels, 'ensure_model',
                        lambda self: self.directory / self.version / 'model.onnx')

    def forbidden(*args: object, **kwargs: object) -> None:
        """Gaia ne doit pas être acquis dans l'installation par défaut."""
        raise AssertionError('Gaia downloaded implicitly')

    monkeypatch.setattr(GaiaCatalogueInstaller, 'install', forbidden)
    result = InstallationAssets.install(tmp_path, options(tmp_path))
    assert calls == [('cosmic', tmp_path / 'tmp/cache/models/cosmicclarity')]
    assert result['graxpert_model'].endswith('3.0.2/model.onnx')
    assert 'gaia_astrometric' not in result


def test_model_paths_cli_over_saved_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Les caches personnalisés suivent CLI > JSON et la configuration n'est pas écrite."""
    from lib.cosmic_clarity import models
    from lib.graxpert_models import GraXpertModels

    config = tmp_path / 'config.json'
    original = json.dumps({'cosmic_model_dir': str(tmp_path / 'saved'),
                           'graxpert_model_dir': str(tmp_path / 'graxpert saved')})
    config.write_text(original)
    calls = []
    monkeypatch.setattr(models, 'install_models', calls.append)
    monkeypatch.setattr(GraXpertModels, 'ensure_model', lambda self: self.directory / 'model.onnx')
    result = InstallationAssets.install(tmp_path, options(tmp_path, '--cosmic-model-dir', str(tmp_path / 'cli')))
    assert calls == [tmp_path / 'cli']
    assert result['graxpert_model'] == str(tmp_path / 'graxpert saved/model.onnx')
    assert config.read_text() == original


def test_optional_gaia_reuses_siril_preference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le catalogue Siril déjà installé est proposé à la vérification avant téléchargement."""
    from lib.gaia_catalogue import GaiaCatalogueInstaller
    from lib.star_separation import StarNetSeparator

    prefs = configparser.ConfigParser()
    prefs['core'] = {'catalogue_gaia_astro': str(tmp_path / 'existing.dat')}
    monkeypatch.setattr(StarNetSeparator, 'preferences', lambda args: (prefs, None))
    calls = []

    def install(self: GaiaCatalogueInstaller, directory: Path, sources: tuple[Path, ...]) -> Path:
        """Enregistre le chemin et la source sans télécharger la base réelle."""
        calls.append((directory, sources))
        return sources[0]

    monkeypatch.setattr(GaiaCatalogueInstaller, 'install', install)
    result = InstallationAssets.install(tmp_path, options(tmp_path, '--no-models', '--with-gaia'))
    assert result == {'gaia_astrometric': str(tmp_path / 'existing.dat')}
    assert calls == [(tmp_path / 'tmp/cache/catalogues/gaia', (tmp_path / 'existing.dat',))]


def test_invalid_config_and_worker_argument_transport(tmp_path: Path) -> None:
    """Une configuration invalide échoue ; les chemins sont transmis sans shell."""
    args = options(tmp_path, '--no-models', '--with-gaia', '--gaia-dir', str(tmp_path / 'cache with spaces'))
    flags = InstallationAssets.worker_arguments(args)
    assert '--no-models' in flags and '--with-gaia' in flags
    assert flags[flags.index('--gaia-dir') + 1] == str(tmp_path / 'cache with spaces')
    args.config.write_text('[]')
    with pytest.raises(ValueError, match='objet JSON'):
        InstallationAssets.install(tmp_path, args)
