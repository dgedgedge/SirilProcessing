"""Sessions API gérées : transport réel local, erreurs, isolation et fermeture."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import socket
import subprocess
import sys

import numpy as np
import pytest

from lib.siril import Siril
from lib.siril_api import (
    SirilCommand, SirilRecord, SirilSession, command, decode, encode, receive, send,
)


@pytest.fixture
def fake_launcher(tmp_path: Path) -> list[str]:
    """Fournit un Siril simulé qui lance le vrai worker avec une API de test."""
    program = tmp_path / 'fake_siril.py'
    program.write_text('''
import dataclasses
import importlib.metadata
import json
import os
from pathlib import Path
import runpy
import shlex
import sys
import types
import numpy as np

@dataclasses.dataclass
class ImgData:
    incl: bool = True
    filenum: int = 1

class Interface:
    def __init__(self):
        self.connected = False
    def connect(self):
        self.connected = True
    def disconnect(self):
        self.connected = False
    def cmd(self, *args):
        decoded = shlex.split(' '.join(args))
        print('CMD ' + decoded[0], flush=True)
        with Path('calls.jsonl').open('a') as stream:
            stream.write(json.dumps(decoded) + '\\n')
        if decoded[0] == 'fail':
            raise ValueError('rejet explicite')
    def get_seq_imgdata(self, frame):
        return ImgData(frame == 0, frame + 1)
    def get_image_pixeldata(self):
        return np.array([[1, float('nan')]], dtype='float32')
    def get_seq_frame_filename(self, frame):
        return str(frame) + '.fits'

module = types.ModuleType('sirilpy')
module.SirilInterface = Interface
module.check_module_version = lambda spec: None
sys.modules['sirilpy'] = module
original_version = importlib.metadata.version
importlib.metadata.version = lambda name: '1.0.25' if name == 'sirilpy' else original_version(name)
init_path = Path(sys.argv[sys.argv.index('-i') + 1])
lines = sys.stdin.read().splitlines()
assert lines[0] == 'requires 1.4.4'
parts = shlex.split(lines[1])
assert parts[0] == 'pyscript'
sys.argv = parts[1:]
runpy.run_path(parts[1], run_name='__main__')
init_path.write_text('[core]\\nchanged=true\\n')
''')
    return [sys.executable, str(program)]


def test_real_worker_proxy_roundtrips_models_arrays_and_quoted_paths(
    tmp_path: Path, fake_launcher: list[str],
) -> None:
    """Le worker utilise l'API, préserve les types et ferme son processus."""
    work = tmp_path / 'work with spaces'
    session = SirilSession(fake_launcher, work, work / 'api.log')
    with session:
        process = session.process
        assert session.info['sirilpy_version'] == '1.0.25'
        session.api.cmd('load', work / 'image with spaces.fits')
        record = session.api.get_seq_imgdata(0)
        assert isinstance(record, SirilRecord)
        assert record.model == 'ImgData' and record.incl is True
        pixels = session.api.get_image_pixeldata()
        assert pixels.dtype == np.float32 and np.isnan(pixels[0, 1])
        assert session.api.get_seq_frame_filename(3) == '3.fits'
    assert process.poll() == 0 and session.exit_code == 0
    assert not list(work.glob('.siril-api-*'))
    calls = [json.loads(line) for line in (work / 'calls.jsonl').read_text().splitlines()]
    assert ['load', str(work / 'image with spaces.fits')] in calls
    assert not list(work.glob('*.sps')) and not list(work.glob('*.ssf'))


def test_api_failure_is_fatal_and_session_is_closed(tmp_path: Path, fake_launcher: list[str]) -> None:
    """Un rejet API n'est jamais transformé en succès du processus hôte."""
    session = SirilSession(fake_launcher, tmp_path, tmp_path / 'error.log')
    with pytest.raises(RuntimeError, match='rejet explicite'):
        with session:
            process = session.process
            session.api.cmd('fail')
    assert process.poll() is not None
    assert session.connection is None
    assert not list(tmp_path.glob('.siril-api-*'))


def test_sessions_are_independent_and_private_methods_are_rejected(
    tmp_path: Path, fake_launcher: list[str],
) -> None:
    """Deux sessions actives possèdent des processus et états distincts."""
    with SirilSession(fake_launcher, tmp_path/'a', tmp_path/'a.log') as first:
        with SirilSession(fake_launcher, tmp_path/'b', tmp_path/'b.log') as second:
            assert first.process.pid != second.process.pid
            first.api.cmd('setcpu', 2)
            second.api.cmd('setcpu', 3)
            with pytest.raises(ValueError, match='interdit'):
                first.call('_cleanup')
            with pytest.raises(AttributeError):
                first.api.disconnect()


def test_startup_failure_and_timeout_cleanup_owned_process(tmp_path: Path) -> None:
    """Une absence de connexion échoue explicitement et ne laisse pas de worker."""
    with pytest.raises(RuntimeError, match='avant connexion'):
        with SirilSession([sys.executable, '-c', 'pass'], tmp_path, tmp_path/'exit.log'):
            pytest.fail('Session non connectée acceptée')
    session = SirilSession([sys.executable, '-c', 'import time;time.sleep(30)'],
                           tmp_path, tmp_path/'timeout.log', startup_timeout=.2)
    with pytest.raises(TimeoutError, match='non établie'):
        with session:
            pytest.fail('Session bloquée acceptée')
    assert session.process is None and session.connection is None
    assert not list(tmp_path.glob('.siril-api-*'))


def test_service_records_api_plan_and_keeps_preferences_isolated(
    tmp_path: Path, fake_launcher: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le service exécute un lot typé, avec journal et plan JSON consultables."""
    monkeypatch.setattr(Siril, '_validate_configuration', lambda self: True)
    service = Siril('/bin/true', 'native')
    service._validated = True
    preferences = tmp_path/'user.ini'
    preferences.write_text('[core]\nsetting=original\n')
    original = preferences.read_bytes()
    def session(directory, name, *, initfile=None):
        """Utilise le vrai transport avec le lanceur de test."""
        return SirilSession(fake_launcher, Path(directory), Path(directory)/f'{name}.log', initfile=initfile)
    monkeypatch.setattr(service, 'session', session)
    assert service.run_commands([command('load', 'image name.fits'), command('close')],
                                tmp_path/'run', 'calibration', initfile=preferences)
    plan = json.loads((tmp_path/'run/calibration.commands.json').read_text())
    assert [step['command'] for step in plan] == ['requires', 'setcpu', 'setext', 'load', 'close']
    assert plan[3]['arguments'] == ['image name.fits']
    assert preferences.read_bytes() == original
    with pytest.raises(TypeError):
        service.run_commands('load image\nsave result', tmp_path)


def test_command_validation_and_codec_preserve_numeric_information() -> None:
    """Aucun script multiligne ni tableau objet ne passe dans le protocole."""
    with pytest.raises(ValueError):
        SirilCommand.from_text('load x\nsave y')
    with pytest.raises(ValueError):
        command('load', 'x\nquit')
    with pytest.raises(ValueError):
        command('load', 'bad"path').api_arguments()
    with pytest.raises(TypeError):
        encode(np.array([object()], dtype=object))
    @dataclass
    class Sample:
        """Modèle imbriqué de test."""
        value: float
    from datetime import datetime, timezone
    observed = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc)
    result = decode(encode({'record': Sample(float('inf')), 'array': np.arange(4, dtype='uint16'),
                            'date_obs': observed, 'blob': b'ICC data'}))
    assert result['date_obs'] == observed
    assert result['blob'] == b'ICC data'
    assert result['record'].value == float('inf')
    assert result['array'].dtype == np.uint16
    left, right = socket.socketpair()
    try:
        send(left, [1, 'étoile'])
        assert receive(right) == [1, 'étoile']
    finally:
        left.close()
        right.close()
