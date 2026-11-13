"""Validation de Siril et de son API Python, sans import numérique à la CLI.

Siril installe et fournit lui-même sirilpy dans son environnement. Le projet
vérifie la version et une vraie connexion ; il n'installe pas de paquet PyPI
homonyme dans le venv hôte.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import platform
import shutil


class SirilDependencies:
    """Contrôle le moteur Siril et son environnement Python intégré."""

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare la sélection du moteur sans importer ses dépendances."""
        group = parser.add_argument_group('Moteur Siril et API Python')
        group.add_argument('--siril-mode', choices=('native', 'flatpak', 'appimage'))
        group.add_argument('--siril-path', help='Exécutable Siril natif ou AppImage')

    @classmethod
    def verify(cls, python: Path, root: Path, options: argparse.Namespace | None = None) -> None:
        """Vérifie l'API dans le Python installé, avec erreurs et journaux conservés."""
        arguments = [str(python), str(root / 'bin/install_dependances.py'), '--siril-worker']
        for name in ('siril_mode', 'siril_path'):
            value = getattr(options, name, None)
            if value is not None:
                arguments += ['--' + name.replace('_', '-'), str(value)]
        subprocess.run(arguments, check=True)

    @classmethod
    def verify_worker(cls, root: Path, options: argparse.Namespace) -> None:
        """Démarre Siril sans image et contrôle la connexion au sirilpy fourni."""
        from lib.siril import Siril
        mode, path = options.siril_mode, options.siril_path
        if mode is None and platform.system() == 'Darwin':
            mode = 'native'
            if path is None and not shutil.which('siril'):
                candidates = [Path('/Applications/Siril.app/Contents/MacOS/siril-cli'),
                              Path('/Applications/Siril.app/Contents/MacOS/siril')]
                path = next((str(candidate) for candidate in candidates if candidate.is_file()), None)
        service = Siril(siril_path=path, siril_mode=mode)
        work = root / 'tmp/siril-dependency-check'
        with service.session(work, 'siril-api-check') as session:
            session.api.cmd('requires', '1.4.4')
            session.api.cmd('setext', 'fits')
            print('Siril et sirilpy accessibles : ' + str(session.info), flush=True)
