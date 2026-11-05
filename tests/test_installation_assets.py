"""Préchargement des ressources, priorités de chemins et catalogues activés par défaut."""

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


def test_models_and_catalogues_are_installed_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans option, les modèles et les deux catalogues sont préparés."""
    from lib.cosmic_clarity import models
    from lib.graxpert_models import GraXpertModels
    from lib.gaia_catalogue import GaiaCatalogueInstaller

    calls = []
    monkeypatch.setattr(models, 'install_models', lambda directory: calls.append(('cosmic', directory)))
    monkeypatch.setattr(GraXpertModels, 'ensure_model',
                        lambda self: self.directory / self.version / 'model.onnx')

    monkeypatch.setattr(GaiaCatalogueInstaller, 'install',
                        lambda self, directory, sources: directory / self.filename)
    monkeypatch.setattr('lib.gaia_spcc.GaiaSPCCInstaller.install',
                        lambda self, directory, sources: directory)
    preferences = configparser.ConfigParser()
    monkeypatch.setattr('lib.star_separation.StarNetSeparator.preferences',
                        lambda args: (preferences, None))
    result = InstallationAssets.install(tmp_path, options(tmp_path))
    assert calls == [('cosmic', tmp_path / 'tmp/cache/models/cosmicclarity')]
    assert result['graxpert_model'].endswith('3.0.2/model.onnx')
    assert result['gaia_astrometric'].endswith('siril_cat_healpix8_astro.dat')
    assert result['gaia_spcc'].endswith('/catalogues/gaia/spcc')


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
    result = InstallationAssets.install(tmp_path, options(tmp_path, '--disable-gaia', '--cosmic-model-dir', str(tmp_path / 'cli')))
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
    monkeypatch.setattr('lib.gaia_spcc.GaiaSPCCInstaller.install',
                        lambda self, directory, sources: directory)
    result = InstallationAssets.install(tmp_path, options(tmp_path, '--no-models', '--with-gaia'))
    assert result == {'gaia_astrometric': str(tmp_path / 'existing.dat'),
                      'gaia_spcc': str(tmp_path / 'tmp/cache/catalogues/gaia/spcc')}
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


def test_spcc_only_option_and_worker_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un venv existant peut préparer seulement SPCC dans un cache personnalisé."""
    from lib.star_separation import StarNetSeparator
    from lib.gaia_catalogue import GaiaCatalogueInstaller

    preferences = configparser.ConfigParser()
    preferences['core'] = {'catalogue_gaia_photo': str(tmp_path / 'siril spectra')}
    monkeypatch.setattr(StarNetSeparator, 'preferences', lambda args: (preferences, None))
    monkeypatch.setattr(GaiaCatalogueInstaller, 'install', lambda *args, **kwargs: pytest.fail('astrometry'))
    calls = []

    def install(self: object, directory: Path, sources: tuple[Path, ...]) -> Path:
        """Capture les sources proposées sans télécharger les bases réelles."""
        calls.append((directory, sources))
        return directory

    monkeypatch.setattr('lib.gaia_spcc.GaiaSPCCInstaller.install', install)
    selected = options(tmp_path, '--no-models', '--disable-gaia-astrometry', '--with-gaia-spcc',
                       '--gaia-spcc-dir', str(tmp_path / 'custom spectral'))
    result = InstallationAssets.install(tmp_path, selected)
    assert result == {'gaia_spcc': str(tmp_path / 'custom spectral')}
    assert calls[0][1][0] == tmp_path / 'siril spectra'
    flags = InstallationAssets.worker_arguments(selected)
    assert '--with-gaia-spcc' in flags
    assert flags[flags.index('--gaia-spcc-dir') + 1] == str(tmp_path / 'custom spectral')


@pytest.mark.parametrize('flags, expected', [
    ([], (True, True)),
    (['--disable-gaia'], (False, False)),
    (['--disable-gaia-astrometry'], (False, True)),
    (['--disable-gaia-spcc'], (True, False)),
    (['--disable-gaia', '--with-gaia', '--with-gaia-spcc'], (False, False)),
])
def test_catalogue_defaults_and_worker_roundtrip(
    tmp_path: Path, flags: list[str], expected: tuple[bool, bool],
) -> None:
    """Le worker reçoit les désactivations et ne réactive aucune acquisition."""
    selected = options(tmp_path, *flags)
    assert InstallationAssets.catalogue_selection(selected) == expected
    forwarded = options(tmp_path, *InstallationAssets.worker_arguments(selected))
    assert InstallationAssets.catalogue_selection(forwarded) == expected


def test_disabling_all_resources_never_calls_installers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le retrait explicite des modèles et catalogues interdit toute acquisition."""
    def forbidden(*args: object, **kwargs: object) -> None:
        """Échoue si une ressource désactivée est tout de même préparée."""
        pytest.fail('disabled installer called')

    monkeypatch.setattr('lib.cosmic_clarity.models.install_models', forbidden)
    monkeypatch.setattr('lib.graxpert_models.GraXpertModels.ensure_model', forbidden)
    monkeypatch.setattr('lib.gaia_catalogue.GaiaCatalogueInstaller.install', forbidden)
    monkeypatch.setattr('lib.gaia_spcc.GaiaSPCCInstaller.install', forbidden)
    selected = options(tmp_path, '--disable-models', '--disable-gaia')
    assert InstallationAssets.install(tmp_path, selected) == {}
    forwarded = options(tmp_path, *InstallationAssets.worker_arguments(selected))
    assert not forwarded.models


@pytest.mark.parametrize('coverage', ['north', 'south', 'remaining', 'all'])
def test_spcc_coverage_is_forwarded_to_worker(tmp_path: Path, coverage: str) -> None:
    """Le nord est le défaut et le worker reçoit la sélection explicitement."""
    assert options(tmp_path).gaia_spcc_coverage == 'north'
    args = options(tmp_path, '--gaia-spcc-coverage', coverage)
    received = options(tmp_path, *InstallationAssets.worker_arguments(args))
    assert received.gaia_spcc_coverage == coverage
