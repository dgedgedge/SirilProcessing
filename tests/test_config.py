"""Identité, initialisation et sélection du fichier de configuration partagé."""
import json

import pytest

from lib.config import Config


def test_singleton_preserves_unsaved_changes(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'custom': 'initial'}))
    config = Config(path)
    config.set('custom', 'modified')

    assert Config() is config
    assert Config(str(path)) is config
    assert Config().get('custom') == 'modified'
    assert json.loads(path.read_text())['custom'] == 'initial'

    config.load()
    assert Config().get('custom') == 'initial'


def test_singleton_default_file(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    path = tmp_path / '.siril_darklib_config.json'
    path.write_text(json.dumps({'custom': 42}))

    config = Config()
    assert config.config_file == str(path)
    assert config.get('custom') == 42
    assert Config() is config


def test_singleton_accepts_equivalent_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = Config('config.json')
    assert Config(tmp_path / 'config.json') is config


def test_singleton_rejects_another_file(tmp_path):
    config = Config(tmp_path / 'first.json')
    config.set('custom', 42)

    with pytest.raises(ValueError, match='Config utilise déjà'):
        Config(tmp_path / 'second.json')

    assert Config() is config
    assert config.get('custom') == 42


def test_command_line_uses_shared_instance(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'siril_mode': 'native'}))
    config = Config.from_command_line(['--config', str(path)])

    assert Config() is config
    assert Config.from_command_line([]) is config
    assert Config.from_command_line(['--config', str(path)]) is config
    assert config.get('siril_mode') == 'native'
