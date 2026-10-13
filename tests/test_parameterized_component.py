"""Configuration des services sans contrat de traitement d’image."""

import argparse

import pytest

from lib.config import Config
from lib.parameterized_component import ParameterizedComponent
from lib.processor import processor
from lib.siril_utils import Siril


class ExampleService(ParameterizedComponent):
    """Service sans post_process avec une option mutable indépendante."""

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare la liste propre au service."""
        parser.add_argument('--items', nargs='+', default=['default'])


class ParentService(ParameterizedComponent):
    """Service déclarant un enfant construit après la résolution."""

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Expose les options de l’enfant."""
        ExampleService.add_arguments(parser)

    @classmethod
    def parameter_children(cls) -> tuple[type[ParameterizedComponent], ...]:
        """Déclare le service enfant sans l’instancier."""
        return (ExampleService,)


def test_service_hierarchy_and_snapshot(tmp_path):
    """CLI et sauvegarde se résolvent avant les captures isolées des enfants."""
    config = Config(tmp_path / 'settings.json')
    config.set('items', ['saved'])
    parser = argparse.ArgumentParser()
    config.register(ParentService, parser)
    args = config.parse_args(parser, [])
    config.bind_parameters(args)
    first = ExampleService()
    assert first._get_args().items == ['saved']
    args = config.parse_args(parser, ['--items', 'cli'])
    config.bind_parameters(args)
    second = ExampleService()
    second._get_args().items.append('changed')
    assert first._get_args().items == ['saved']
    assert ExampleService()._get_args().items == ['cli']
    assert args.items == ['cli']
    assert not isinstance(first, processor)


def test_processor_still_requires_image_contract():
    """La base service exige une déclaration et processor exige post_process."""
    with pytest.raises(TypeError):
        ParameterizedComponent()
    with pytest.raises(TypeError):
        processor()


def test_siril_bound_constructor_and_validation(tmp_path):
    """Siril utilise la configuration liée, puis les surcharges explicites."""
    executable = tmp_path / 'siril'
    executable.write_text('#!/bin/sh\nexit 0\n')
    executable.chmod(0o755)
    config = Config(tmp_path / 'settings.json')
    config.set('siril_path', str(executable))
    config.set('siril_mode', 'native')
    parser = argparse.ArgumentParser()
    config.register(Siril, parser)
    args = config.parse_args(parser, [])
    config.bind_parameters(args)
    first = Siril()
    assert first.siril_path == str(executable)
    assert first.siril_mode == 'native'
    assert first.is_validated
    assert Siril(siril_mode='appimage').siril_mode == 'appimage'
    args.siril_mode = 'appimage'
    config.bind_parameters(args)
    assert first.siril_mode == 'native'
    assert Siril().siril_mode == 'appimage'
    # L’API demandant les défauts globaux reste indépendante des paramètres liés.
    original_path, original_mode = Siril.get_default_config()
    try:
        Siril.configure_defaults(siril_path=str(executable), siril_mode='native')
        assert Siril.create_with_defaults().siril_mode == 'native'
        assert Siril().siril_mode == 'appimage'
    finally:
        Siril._default_siril_path = original_path
        Siril._default_siril_mode = original_mode
    with pytest.raises(ValueError):
        Siril(siril_mode='invalid')
