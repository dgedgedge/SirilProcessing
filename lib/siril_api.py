"""Sessions Siril autonomes et accès à l'API officielle sirilpy.

Le processus Siril lance son interpréteur Python et sa version de sirilpy.
Une connexion locale authentifiée rend cette API disponible au programme
appelant sans dépendre des chemins privés de l'installation Siril.
Voir docs/scripts/lightProcess/reference/SIRIL_API.md.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import base64
import dataclasses
import json
import math
import secrets
import shlex
import signal
import shutil
import os
import socket
import struct
import subprocess
import tempfile
import time
import threading
from typing import BinaryIO
from types import TracebackType

import numpy as np


MAX_MESSAGE_BYTES = 512 * 1024 * 1024
STARTUP_TIMEOUT_SECONDS = 120.0
SHUTDOWN_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class SirilCommand:
    """Appel cmd explicite : nom de commande et arguments sans quoting shell."""

    name: str
    arguments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Refuse les noms invalides et l'injection de plusieurs commandes."""
        if not self.name or not self.name.replace('_', '').isalnum():
            raise ValueError(f'Commande Siril invalide : {self.name!r}')
        if any('\n' in item or '\r' in item or '\x00' in item for item in self.arguments):
            raise ValueError('Un argument Siril doit tenir sur une seule ligne')

    @classmethod
    def create(cls, name: str, *arguments: str | Path | int | float) -> SirilCommand:
        """Construit un appel depuis des valeurs explicites, chemins compris."""
        return cls(name, tuple(str(argument) for argument in arguments))

    @classmethod
    def from_text(cls, command: str) -> SirilCommand:
        """Lit une seule commande atomique existante, jamais un script SSF."""
        if '\n' in command.strip() or '\r' in command.strip():
            raise ValueError('Une commande atomique ne peut pas contenir de script')
        parts = shlex.split(command)
        if not parts:
            raise ValueError('Commande Siril vide')
        return cls(parts[0], tuple(parts[1:]))

    @staticmethod
    def quote(argument: str) -> str:
        """Protège un argument selon la syntaxe Siril, indépendamment du shell."""
        if '"' in argument:
            raise ValueError('Un argument Siril ne peut pas contenir de guillemet double')
        return '"' + argument + '"' if not argument or any(c.isspace() for c in argument) else argument

    def api_arguments(self) -> tuple[str, ...]:
        """Retourne les arguments attendus par SirilInterface.cmd."""
        return (self.name, *(self.quote(argument) for argument in self.arguments))

    def render(self) -> str:
        """Décrit l'appel pour les journaux et les tests, sans produire de SSF."""
        return ' '.join(self.api_arguments())


def command(name: str, *arguments: str | Path | int | float) -> SirilCommand:
    """Construit une commande typée, par exemple command('load', image_path)."""
    return SirilCommand.create(name, *arguments)


def format_commands(operations: Sequence[SirilCommand] | str) -> str:
    """Décrit un lot pour les journaux et les diagnostics, sans fichier SSF."""
    if isinstance(operations, str):
        return operations
    return '\n'.join(operation.render() for operation in operations) + '\n'


def encode(value: object) -> object:
    """Encode les résultats API et tableaux sans pickle ni perte de dtype."""
    if isinstance(value, np.ndarray):
        if value.dtype.hasobject:
            raise TypeError('Les tableaux Python object ne sont pas transportables')
        array = np.ascontiguousarray(value)
        return dict(_type='ndarray', dtype=array.dtype.str, shape=list(array.shape),
                    data=base64.b64encode(array.tobytes()).decode('ascii'))
    if isinstance(value, datetime):
        return dict(_type='datetime', value=value.isoformat())
    if isinstance(value, date):
        return dict(_type='date', value=value.isoformat())
    if isinstance(value, bytes):
        return dict(_type='bytes', data=base64.b64encode(value).decode('ascii'))
    if isinstance(value, np.generic):
        return encode(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return dict(_type='float', value=str(value))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dict(_type='record', model=type(value).__name__,
                    fields={field.name: encode(getattr(value, field.name))
                            for field in dataclasses.fields(value)})
    if isinstance(value, dict):
        return {str(key): encode(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [encode(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f'Type de résultat API non pris en charge : {type(value).__name__}')


class SirilRecord:
    """Vue des champs d'un modèle sirilpy reçu depuis le processus Siril.

    Les noms et valeurs des champs restent ceux de l'API ; le transport ne
    prétend pas recréer une instance locale du modèle C/Python distant.
    """

    def __init__(self, model: str, fields: dict[str, object]) -> None:
        """Conserve le type source et ses champs décodés."""
        self.model = model
        self.fields = fields

    def __getattr__(self, name: str) -> object:
        """Expose les champs comme les attributs du modèle source."""
        try:
            return self.fields[name]
        except KeyError as error:
            raise AttributeError(name) from error


def decode(value: object) -> object:
    """Reconstruit tableaux et vues de modèles à partir du protocole JSON."""
    if isinstance(value, list):
        return [decode(item) for item in value]
    if not isinstance(value, dict):
        return value
    kind = value.get('_type')
    if kind == 'ndarray':
        dtype = np.dtype(value['dtype'])
        if dtype.hasobject:
            raise ValueError('dtype object interdit dans le transport Siril')
        raw = base64.b64decode(value['data'], validate=True)
        return np.frombuffer(raw, dtype=dtype).reshape(value['shape']).copy()
    if kind == 'datetime':
        return datetime.fromisoformat(value['value'])
    if kind == 'date':
        return date.fromisoformat(value['value'])
    if kind == 'bytes':
        return base64.b64decode(value['data'], validate=True)
    if kind == 'float':
        return float(value['value'])
    if kind == 'record':
        return SirilRecord(value['model'], {key: decode(item) for key, item in value['fields'].items()})
    return {key: decode(item) for key, item in value.items()}


def receive(connection: socket.socket) -> object:
    """Lit un message JSON encadré ; une fermeture ou taille excessive échoue."""
    def exact(size: int) -> bytes:
        """Lit exactement size octets, sans accepter un message tronqué."""
        parts = bytearray()
        while len(parts) < size:
            chunk = connection.recv(min(size - len(parts), 1024 * 1024))
            if not chunk:
                raise ConnectionError('Connexion sirilpy interrompue')
            parts.extend(chunk)
        return bytes(parts)
    size = struct.unpack('!I', exact(4))[0]
    if size > MAX_MESSAGE_BYTES:
        raise ValueError('Réponse sirilpy trop volumineuse')
    return decode(json.loads(exact(size)))


def send(connection: socket.socket, value: object) -> None:
    """Transmet un message JSON de taille bornée, sans exécuter de code reçu."""
    payload = json.dumps(encode(value), allow_nan=False).encode('utf-8')
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ValueError('Message sirilpy trop volumineux')
    connection.sendall(struct.pack('!I', len(payload)) + payload)


class SirilApi:
    """Accès distant aux méthodes publiques du SirilInterface de cette session."""

    def __init__(self, session: SirilSession) -> None:
        """Lie le proxy à une session possédée, sans démarrer de processus."""
        self._session = session

    def cmd(self, name: str, *arguments: str | Path | int | float) -> object:
        """Appelle SirilInterface.cmd avec des arguments protégés pour Siril."""
        call = command(name, *arguments)
        return self._session.call('cmd', *call.api_arguments())

    def __getattr__(self, name: str) -> Callable[..., object]:
        """Expose les autres méthodes publiques, dont get_seq_regdata."""
        if name.startswith('_') or name in {'connect', 'disconnect'}:
            raise AttributeError(name)
        def invoke(*arguments: object, **keywords: object) -> object:
            """Transmet un appel au SirilInterface réel dans son environnement."""
            return self._session.call(name, *arguments, **keywords)
        return invoke


class SirilSession:
    """Possède Siril, sa connexion API et son journal jusqu'à la fermeture.

    Seule une instruction pyscript d'amorçage est envoyée sur stdin. Les
    traitements sont ensuite des appels RPC à sirilpy, sans fichier SSF/SPS.
    Deux sessions ont des processus, connexions et préférences indépendants.
    """

    def __init__(
        self, launcher: Sequence[str], directory: Path, log_path: Path, *,
        initfile: Path | None = None, startup_timeout: float = STARTUP_TIMEOUT_SECONDS,
        setup: Sequence[SirilCommand] = (),
    ) -> None:
        """Prépare le lancement ; la connexion est établie par le context manager."""
        if not math.isfinite(startup_timeout) or startup_timeout <= 0:
            raise ValueError('Délai de connexion Siril strictement positif requis')
        self.launcher, self.directory = list(launcher), directory.resolve()
        self.log_path, self.initfile = log_path, initfile
        self.startup_timeout = startup_timeout
        self.setup = tuple(setup)
        self._call_lock = threading.Lock()
        self.exit_code: int | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self.connection: socket.socket | None = None
        self._log: BinaryIO | None = None
        self._temporary: tempfile.TemporaryDirectory | None = None
        self.api = SirilApi(self)
        self.info: dict[str, object] = {}

    def __enter__(self) -> SirilSession:
        """Lance Siril sans fenêtre, attend sa connexion et authentifie le worker."""
        self.directory.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            listener.settimeout(.2)
            token = secrets.token_hex(32)
            self._temporary = tempfile.TemporaryDirectory(prefix='.siril-api-', dir=self.directory)
            config = Path(self._temporary.name) / 'connection.json'
            config.write_text(json.dumps(dict(port=listener.getsockname()[1], token=token)), encoding='utf-8')
            config.chmod(0o600)
            worker = Path(__file__).with_name('siril_api_worker.py').resolve()
            self._log = self.log_path.open('wb')
            isolated = Path(self._temporary.name) / 'preferences.ini'
            if self.initfile is not None:
                shutil.copyfile(self.initfile, isolated)
            else:
                sources = ([Path.home() / '.var/app/org.siril.Siril/config/siril/config.1.4.ini']
                           if self.launcher[0] == 'flatpak' else
                           [Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'siril/config.1.4.ini',
                            Path.home() / 'Library/Application Support/siril/config.1.4.ini'])
                source = next((path for path in sources if path.is_file()), None)
                if source is not None:
                    shutil.copyfile(source, isolated)
                else:
                    isolated.write_text('[core]\n', encoding='utf-8')
            options = [*self.launcher, '-d', str(self.directory), '-i', str(isolated), '-s', '-']
            self.process = subprocess.Popen(options, cwd=self.directory, stdin=subprocess.PIPE,
                                            stdout=self._log, stderr=subprocess.STDOUT,
                                            start_new_session=True)
            bootstrap = 'requires 1.4.4\n' + command('pyscript', worker, config).render() + '\n'
            assert self.process.stdin is not None
            self.process.stdin.write(bootstrap.encode('utf-8'))
            self.process.stdin.close()
            deadline = time.monotonic() + self.startup_timeout
            while True:
                if self.process.poll() is not None:
                    raise RuntimeError(f'Siril a quitté avant connexion (code {self.process.returncode}) ; {self.log_path}')
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'Connexion sirilpy non établie ; {self.log_path}')
                try:
                    self.connection, _ = listener.accept()
                    break
                except socket.timeout:
                    continue
            self.connection.settimeout(max(.1, deadline - time.monotonic()))
            hello = receive(self.connection)
            if not isinstance(hello, dict) or not secrets.compare_digest(str(hello.get('token', '')), token):
                raise RuntimeError('Authentification du worker sirilpy refusée')
            if hello.get('error'):
                raise RuntimeError(str(hello['error']))
            self.info = {key: value for key, value in hello.items() if key != 'token'}
            self.api.cmd('cd', self.directory)
            for operation in self.setup:
                self.api.cmd(operation.name, *operation.arguments)
            self.connection.settimeout(None)
            return self
        except BaseException:
            self.close(abort=True)
            raise
        finally:
            listener.close()

    def call(self, method: str, *arguments: object, **keywords: object) -> object:
        """Exécute un appel API ; les erreurs Siril restent fatales et contextualisées."""
        if self.connection is None or self.process is None or self.process.poll() is not None:
            raise RuntimeError(f'Session Siril non connectée ; {self.log_path}')
        if method.startswith('_') or method in {'connect', 'disconnect'}:
            raise ValueError('Méthode privée ou cycle de connexion interdit')
        with self._call_lock:
            send(self.connection, dict(method=method, args=list(arguments), kwargs=keywords))
            response = receive(self.connection)
        if not isinstance(response, dict) or 'ok' not in response:
            raise RuntimeError('Réponse du worker sirilpy invalide')
        if not response['ok']:
            raise RuntimeError(f"Sirilpy {method} : {response.get('error')} ; consulter {self.log_path}")
        return response.get('result')

    def close(self, *, abort: bool = False) -> None:
        """Déconnecte puis termine uniquement le processus Siril possédé."""
        try:
            if self.connection is not None:
                if not abort:
                    try:
                        send(self.connection, dict(shutdown=True))
                    except OSError:
                        pass
                self.connection.close()
                self.connection = None
            if self.process is not None:
                if abort and self.process.poll() is None:
                    try:
                        os.killpg(self.process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                try:
                    self.process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    if self.process.poll() is None:
                        try:
                            os.killpg(self.process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    self.process.wait()
                self.exit_code = self.process.returncode
                self.process = None
        finally:
            if self._log is not None:
                self._log.close()
                self._log = None
            if self._temporary is not None:
                self._temporary.cleanup()
                self._temporary = None

    def __exit__(self, kind: type[BaseException] | None, value: BaseException | None,
                 traceback: TracebackType | None) -> None:
        """Ferme la session, y compris après erreur ou interruption clavier."""
        self.close(abort=kind is not None)
