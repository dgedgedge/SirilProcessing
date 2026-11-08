"""Worker exécuté par Siril : transmet les appels à son SirilInterface officiel.

Auteur : SirilProcessing contributors. Licence : celle du projet.
Ce module ne démarre aucun Siril et n'accède qu'à la session qui l'a lancé.
"""
from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
import socket
import sys

# Le Python appartient à Siril ; le projet n'a pas à être installé dans son venv.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.siril_api import receive, send


def serve(config_path: Path) -> None:
    """Connecte sirilpy et sert les appels publics jusqu'à la fermeture du parent."""
    config = json.loads(config_path.read_text(encoding='utf-8'))
    with socket.create_connection(('127.0.0.1', config['port']), timeout=120) as connection:
        api = None
        try:
            import sirilpy
            sirilpy.check_module_version('>=1.0.25')
            api = sirilpy.SirilInterface()
            api.connect()
            send(connection, dict(token=config['token'], sirilpy_version=importlib.metadata.version('sirilpy')))
            connection.settimeout(None)
            while True:
                request = receive(connection)
                if not isinstance(request, dict):
                    raise ValueError('Requête API invalide')
                if request.get('shutdown'):
                    break
                method = request.get('method', '')
                if not isinstance(method, str) or method.startswith('_') or method in {'connect', 'disconnect'}:
                    raise ValueError('Méthode API interdite')
                try:
                    function = getattr(api, method)
                    if not callable(function):
                        raise ValueError('Une méthode publique est requise')
                    result = function(*request.get('args', []), **request.get('kwargs', {}))
                    send(connection, dict(ok=True, result=result))
                except Exception as error:
                    send(connection, dict(ok=False, error=f'{type(error).__name__}: {error}'))
        except (ConnectionError, BrokenPipeError):
            pass
        except Exception as error:
            send(connection, dict(token=config['token'], error=f'{type(error).__name__}: {error}'))
            raise
        finally:
            if api is not None and api.connected:
                api.disconnect()


if __name__ == '__main__':
    serve(Path(sys.argv[1]))
