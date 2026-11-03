"""Identité, initialisation et sélection du fichier de configuration partagé."""
import json
import argparse
from pathlib import Path

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


class ExampleTreatment:
    parameter_persistence = {'output': False, 'force': False}
    config_keys = {'age': 'max_age_days'}

    @staticmethod
    def add_arguments(parser):
        parser.add_argument('--amount', type=int, default=3)
        parser.add_argument('--age', type=int, default=182)
        parser.add_argument('--output', type=Path)
        parser.add_argument('--force', action='store_true')


def configured_parser(tmp_path, saved=None):
    path = tmp_path / 'settings.json'
    if saved is not None:
        path.write_text(json.dumps(saved))
    config = Config(path)
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(ExampleTreatment, parser)
    return config, parser, path


def test_registered_precedence_and_selective_save(tmp_path):
    config, parser, path = configured_parser(tmp_path, {'amount': 7, 'force': True, 'custom': 42})
    assert config.parse_args(parser, []).amount == 7
    assert config.get('force') is False
    args = config.parse_args(parser, ['--amount', '9', '--output', 'result.fit', '--force'])
    assert config.get('amount') == 9
    assert config.arguments().output == Path('result.fit')
    assert config.save_requested(args)
    assert json.loads(path.read_text())['amount'] == 7
    args.save_config = True
    assert config.save_requested(args)
    saved = json.loads(path.read_text())
    assert saved['amount'] == 9
    assert saved['custom'] == 42
    assert saved['max_age_days'] == 182
    assert not {'force', 'output', 'age', 'config', 'save_config'} & saved.keys()


def test_alias_updates_remain_consistent(tmp_path):
    config, parser, path = configured_parser(tmp_path)
    config.parse_args(parser, ['--age', '12'])
    config.set('max_age_days', 15)
    assert config.get('age') == 15
    assert config.arguments().age == 15
    assert config.save()
    assert json.loads(path.read_text())['max_age_days'] == 15
    config.set('age', 20)
    assert config.get('max_age_days') == 20
    assert config.save()
    assert 'age' not in json.loads(path.read_text())


def test_failed_serialization_preserves_existing_file(tmp_path):
    config, _, path = configured_parser(tmp_path, {'amount': 7})
    original = path.read_bytes()
    config.set('unserializable', object())
    assert config.save() is False
    assert path.read_bytes() == original


@pytest.mark.parametrize('invalid', [[], None, 'text', 12])
def test_invalid_json_root_falls_back_to_defaults(tmp_path, invalid):
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps(invalid))
    config = Config(path)
    from lib.siril import Siril

    parser = argparse.ArgumentParser()
    config.register(Siril, parser)
    args = config.parse_args(parser, [])
    assert args.siril_mode == Siril.CONFIG_DEFAULTS['siril_mode']
    assert config.get('siril_mode') == Siril.CONFIG_DEFAULTS['siril_mode']


def test_legacy_set_from_args_normalizes_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = Config(tmp_path / 'config.json')
    from lib.darkprocess import DarkLib

    config.register(DarkLib, argparse.ArgumentParser())
    config.set_from_args(argparse.Namespace(work_dir='work', input_dirs=['darks'], max_age=12))
    assert config.get('work_dir') == str(tmp_path / 'work')
    assert config.get('input_dirs') == [str(tmp_path / 'darks')]
    assert config.get('max_age_days') == 12


def test_set_from_args_requires_registered_parameters(tmp_path):
    config = Config(tmp_path / 'config.json')

    with pytest.raises(RuntimeError, match='Config.register'):
        config.set_from_args(argparse.Namespace(custom='value'))


def test_generic_config_save_does_not_need_registered_processors(tmp_path):
    config = Config(tmp_path / 'config.json')
    config.set('custom', 'value')

    assert config.save()
    assert json.loads(Path(config.config_file).read_text()) == {'custom': 'value'}


def test_subclass_can_select_file_in_init(tmp_path):
    class LocalConfig(Config):
        def __init__(self):
            super().__init__(tmp_path / 'local.json')

    config = LocalConfig()
    config.set('custom', 42)
    assert LocalConfig() is config
    assert config.config_file == str(tmp_path / 'local.json')
    assert LocalConfig().get('custom') == 42


def test_atomic_replace_failure_keeps_file_and_cleans_temporary(tmp_path, monkeypatch):
    config, _, path = configured_parser(tmp_path, {'amount': 7})
    original = path.read_bytes()
    config.set('amount', 9)

    def fail_replace(*args):
        raise OSError('replacement failed')

    monkeypatch.setattr(Path, 'replace', fail_replace)
    assert not config.save()
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_config_import_does_not_load_application_classes(tmp_path: Path) -> None:
    """Le stockage fonctionne sans importer les composants ni leurs dépendances."""
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent('''\
        import builtins
        import json
        import sys

        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            """Refuse tout chargement applicatif autre que le stockage testé."""
            if name.startswith('lib.') and name != 'lib.config':
                raise ImportError(f'Import applicatif interdit : {name}')
            return original_import(name, *args, **kwargs)

        builtins.__import__ = guarded_import
        from lib.config import Config

        config = Config(sys.argv[1])
        config.set('custom', 42)
        assert config.save()
        with open(sys.argv[1]) as stream:
            assert json.load(stream) == {'custom': 42}
    ''')
    result = subprocess.run(
        [sys.executable, '-c', script, str(tmp_path / 'isolated.json')],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_binding_accepts_independent_provider_classes(tmp_path: Path) -> None:
    """Le contrat déclaratif fonctionne sans hériter d'une classe applicative."""
    class IndependentChild:
        """Fournisseur externe déclarant ses propres métadonnées de persistance."""

        parameter_persistence = {'temporary': False}
        config_keys = {'value': 'saved_value'}
        parameter_prefixes = ('external_',)
        captured: argparse.Namespace | None = None

        @classmethod
        def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
            """Déclare une valeur rémanente et une option d'exécution."""
            parser.add_argument('--value', type=int, default=3)
            parser.add_argument('--temporary', action='store_true')

        @classmethod
        def parameter_children(cls) -> tuple[type, ...]:
            """Déclare l'absence d'enfant pour cette feuille."""
            return ()

        @classmethod
        def bind_parameters(cls, args: argparse.Namespace) -> None:
            """Conserve le Namespace indépendant transmis par le stockage."""
            cls.captured = args

    class IndependentParent(IndependentChild):
        """Expose les options de l'enfant sans dépendance à nos composants."""

        @classmethod
        def parameter_children(cls) -> tuple[type, ...]:
            """Déclare le fournisseur enfant transmis par le programme principal."""
            return (IndependentChild,)

    path = tmp_path / 'independent.json'
    path.write_text(json.dumps({'saved_value': 7, 'temporary': True}))
    config = Config(path)
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(IndependentParent, parser)
    assert config.parse_args(parser, []).value == 7
    args = config.parse_args(parser, ['--value', '9', '--temporary', '-S'])
    args.external_items = ['original']
    config.bind_parameters(args)
    assert IndependentChild.captured.value == 9
    assert IndependentChild.captured.temporary is True
    args.external_items.append('changed')
    assert IndependentChild.captured.external_items == ['original']
    assert json.loads(path.read_text())['saved_value'] == 7
    assert config.save_requested(args)
    assert json.loads(path.read_text()) == {'saved_value': 9}
