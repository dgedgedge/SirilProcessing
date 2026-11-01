"""Priorité des paramètres de validation des darks, sans appel à Siril."""

import argparse
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from lib.config import Config
from lib.darkprocess import DarkLib
from lib.lightprocessor import LightProcessor


@pytest.mark.parametrize("treatment", [DarkLib, LightProcessor])
@pytest.mark.parametrize(
    ("saved", "arguments", "expected"),
    [
        ({}, [], 0.5),
        ({"temperature_precision": 0.2}, [], 0.2),
        ({"temperature_precision": 0.2}, ["--temperature-precision", "1"], 1.0),
    ],
)
def test_temperature_precision_precedence(
    tmp_path: Path,
    treatment: type[DarkLib] | type[LightProcessor],
    saved: dict[str, float],
    arguments: list[str],
    expected: float,
) -> None:
    """Aligner darks et lights à 0,5 °C sans remplacer les choix explicites."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps(saved))
    config = Config(path)
    parser = argparse.ArgumentParser()
    config.register(treatment, parser)
    positional = [str(tmp_path)] if treatment is LightProcessor else []

    parsed = config.parse_args(parser, positional + arguments)
    assert parsed.temperature_precision == expected


@pytest.mark.parametrize(
    ("saved", "arguments", "expected"),
    [
        ({}, [], True),
        ({}, ["--no-validate-darks"], False),
        ({"validate_darks": False}, [], False),
        ({"validate_darks": False}, ["--validate-darks"], True),
        ({"validate_darks": True}, ["--no-validate-darks"], False),
    ],
)
def test_validation_precedence(
    tmp_path: Path, saved: dict[str, bool], arguments: list[str], expected: bool
) -> None:
    """Respecter CLI > JSON > validation active sans modifier le JSON."""
    path = tmp_path / "config.json"
    original = json.dumps(saved)
    path.write_text(original)
    config = Config(path)
    parser = argparse.ArgumentParser()
    config.register(DarkLib, parser)

    assert config.parse_args(parser, arguments).validate_darks is expected
    assert path.read_text() == original


def test_direct_group_processing_validates_by_default(tmp_path: Path) -> None:
    """Transmettre la validation par défaut même sans passer par le CLI."""
    darklib = DarkLib.__new__(DarkLib)
    darklib.work_dir = str(tmp_path)
    darklib.stack_and_save_master_dark = Mock()
    files = [Mock(), Mock()]

    darklib.process_all_groups({"group": files})

    assert darklib.stack_and_save_master_dark.call_args.args[-1] is True


@pytest.mark.parametrize('treatment', [DarkLib, LightProcessor])
def test_common_stacking_defaults_and_long_cli_name(
    tmp_path: Path, treatment: type[DarkLib] | type[LightProcessor],
) -> None:
    """Les deux scripts exposent winsorizedsigma/average et acceptent leur CLI."""
    config = Config(tmp_path / 'config.json')
    parser = argparse.ArgumentParser()
    config.register(treatment, parser)
    positional = [str(tmp_path)] if treatment is LightProcessor else []
    for flags in ([], ['--rejection-method', 'winsorizedsigma', '--stack-method', 'average']):
        args = config.parse_args(parser, positional + flags)
        assert args.rejection_method == 'winsorizedsigma'
        assert args.stack_method == 'average'
