#!/usr/bin/env python3
"""Prépare ou vérifie le venv CUDA complet ; voir docs/scripts/install_cuda/README.md."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.cuda_environment import CudaEnvironmentInstaller


def main() -> int:
    """Installe dans le venv choisi et retourne 1 si une installation/vérification échoue."""
    parser = argparse.ArgumentParser(description='Installer le venv CUDA complet des traitements')
    parser.add_argument('--venv', type=Path, help='Venv cible ; sinon VENV_DIR, .venv ou venv')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--check-only', action='store_true', help='Vérifier sans installer')
    group.add_argument('--install-only', action='store_true',
                       help='Installer sans contrôle GPU ; accélération non certifiée')
    parser.add_argument('--verify-worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.verify_worker:
            CudaEnvironmentInstaller.verify_worker()
            return 0
        installer = CudaEnvironmentInstaller(Path(__file__).resolve().parents[1], args.venv)
        print(f'Venv CUDA : {installer.venv}', flush=True)
        if not args.check_only:
            installer.install(args.install_only)
        if not args.install_only:
            installer.verify()
            print('Les trois moteurs CUDA sont opérationnels.')
        else:
            print('Paquets installés ; fonctionnement GPU non vérifié. '
                  'Relancer avec --check-only après activation du pilote NVIDIA.')
        return 0
    except (OSError, ValueError, RuntimeError, ImportError,
            subprocess.CalledProcessError) as error:
        print(f'Échec de la préparation CUDA : {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
