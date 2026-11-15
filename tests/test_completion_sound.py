"""Synthèse WAV portable et lecture de fin de traitement sans dépendance audio."""

import argparse
import json
from array import array
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import wave

import pytest

from lib.completion_sound import CompletionSound, DURATION, SAMPLE_RATE
from lib.config import Config


def test_dong_is_finite_bounded_pcm_with_decay(tmp_path: Path) -> None:
    """Le dong mono 16 bits a la durée prévue, une amplitude bornée et décroît."""
    path = tmp_path / 'dong.wav'
    CompletionSound.write_dong(path)
    with wave.open(str(path), 'rb') as stream:
        assert stream.getnchannels() == 1
        assert stream.getsampwidth() == 2
        assert stream.getframerate() == SAMPLE_RATE
        assert stream.getnframes() == round(SAMPLE_RATE * DURATION)
        samples = array('h', stream.readframes(stream.getnframes()))
    if sys.byteorder != 'little':
        samples.byteswap()
    assert samples[0] == 0
    assert 1000 < max(abs(value) for value in samples) <= 16383
    first = sum(value * value for value in samples[:SAMPLE_RATE//4])
    last = sum(value * value for value in samples[-SAMPLE_RATE//4:])
    assert last < first / 10


@pytest.mark.parametrize('options', [dict(completion_sound=False), dict(dry_run=True)])
def test_disabled_and_simulated_runs_are_silent(options: dict[str, bool], monkeypatch) -> None:
    """Ni simulation ni désactivation ne synthétisent de son."""
    write = Mock()
    monkeypatch.setattr(CompletionSound, 'write_dong', write)
    assert not CompletionSound().play(argparse.Namespace(**options))
    write.assert_not_called()


@pytest.mark.parametrize('platform,player', [('linux', 'paplay'), ('darwin', 'afplay')])
def test_unix_player_gets_a_real_temporary_wav(
    platform: str, player: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linux et macOS utilisent une liste d'arguments sans shell et nettoient le WAV."""
    import lib.completion_sound as module

    paths = []
    monkeypatch.setattr(module.sys, 'platform', platform)
    monkeypatch.setattr(module.shutil, 'which', lambda name: '/audio/'+name if name == player else None)

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        """Vérifie le fichier et le délai sans faire de lecture audio réelle."""
        assert command[0] == '/audio/'+player
        path = Path(command[-1])
        assert path.is_file()
        assert kwargs['timeout'] == 5
        assert kwargs.get('shell', False) is False
        paths.append(path)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module.subprocess, 'run', run)
    assert CompletionSound().play(argparse.Namespace())
    assert paths and all(not path.exists() for path in paths)


def test_windows_uses_standard_library_winsound(monkeypatch: pytest.MonkeyPatch) -> None:
    """La branche Windows appelle winsound et supprime le WAV après lecture synchrone."""
    import lib.completion_sound as module

    paths = []

    def play(path: str, flags: int) -> None:
        """Simule l'API Windows indisponible dans cet environnement Linux."""
        assert flags == 123
        assert Path(path).is_file()
        paths.append(Path(path))

    monkeypatch.setattr(module.sys, 'platform', 'win32')
    monkeypatch.setitem(sys.modules, 'winsound', SimpleNamespace(PlaySound=play, SND_FILENAME=123))
    assert CompletionSound().play(argparse.Namespace())
    assert all(not path.exists() for path in paths)


@pytest.mark.parametrize('failure', ['missing', 'error', 'timeout'])
def test_audio_failure_is_nonfatal(failure: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """L'absence de lecteur, un échec ou un blocage restent sans exception."""
    import lib.completion_sound as module

    monkeypatch.setattr(module.sys, 'platform', 'linux')
    monkeypatch.setattr(module.shutil, 'which', lambda name: None if failure == 'missing' else '/audio/'+name)
    run = Mock(return_value=subprocess.CompletedProcess([], 1))
    if failure == 'timeout':
        run.side_effect = subprocess.TimeoutExpired('audio', 5)
    monkeypatch.setattr(module.subprocess, 'run', run)
    assert not CompletionSound().play(argparse.Namespace())


def test_completion_sound_configuration_priority(tmp_path: Path) -> None:
    """Le signal est actif par défaut, avec priorité CLI sur JSON et sauvegarde explicite."""
    config = Config(tmp_path / 'settings.json')
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(CompletionSound, parser)
    assert config.parse_args(parser, []).completion_sound
    config.set('completion_sound', False)
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(CompletionSound, parser)
    assert not config.parse_args(parser, []).completion_sound
    assert config.parse_args(parser, ['--completion-sound']).completion_sound
    assert not config.parse_args(parser, ['--no-completion-sound']).completion_sound
    args = config.parse_args(parser, ['--no-completion-sound', '--save-config'])
    assert config.save_requested(args)
    assert json.loads((tmp_path / 'settings.json').read_text())['completion_sound'] is False
