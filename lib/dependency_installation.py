"""Point commun d'installation des dépendances selon la plateforme détectée.

Voir docs/scripts/install_dependances/README.md. La sélection, le nettoyage
explicite et la CLI utilisent uniquement la bibliothèque standard ; les
moteurs GPU sont chargés dans le processus de vérification du venv choisi.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from lib.cuda_environment import CudaEnvironmentInstaller
from lib.macos_environment import MacOSEnvironmentInstaller
from lib.installation_assets import InstallationAssets
from lib.resource_cache import ResourceCache


class DependencyInstaller:
    """Sélectionne le moteur de plateforme et orchestre installation ou test."""

    def __init__(
        self, root: Path, venv: Path | None = None,
    ) -> None:
        """Détecte l'OS sans mutation et sélectionne son installateur spécialisé.

        root désigne le dépôt ; venv suit la sélection commune des lanceurs.
        Une plateforme non couverte ou un venv non valide lève une erreur.
        """
        system = platform.system()
        if system == 'Darwin':
            self.platform = 'macos'
            installer_class = MacOSEnvironmentInstaller
        elif system == 'Linux':
            self.platform = 'cuda'
            installer_class = CudaEnvironmentInstaller
        else:
            raise RuntimeError(f'Plateforme non prise en charge : {system}')
        installer_class.check_platform()
        self.installer = installer_class(root, venv)

    def reset_environment(self) -> None:
        """Supprime uniquement le venv reconnu et explicitement ciblé par --reset.

        Refuse les liens symboliques de cible, le dépôt et ses parents, le
        domicile, le Python de base et ses parents. Un dossier non vide doit
        contenir un pyvenv.cfg régulier, jamais un lien. Les modèles et images
        situés hors de ce venv ne sont pas touchés. Une cible absente est ignorée.
        Les erreurs d'E/S restent fatales, avant toute réinstallation.
        """
        installer = self.installer
        target = installer.venv
        if (installer.venv_path.is_symlink()
                or installer.root.is_relative_to(target)
                or Path(sys.base_prefix).resolve().is_relative_to(target)
                or target == Path.home().resolve()):
            raise ValueError(f'Remise à zéro refusée pour cette cible : {target}')
        if not target.exists():
            return
        marker = target / 'pyvenv.cfg'
        if any(target.iterdir()) and (not marker.is_file() or marker.is_symlink()):
            raise ValueError(f'Remise à zéro refusée : aucun pyvenv.cfg régulier dans {target}')
        print(f'Suppression complète du venv : {target}', flush=True)
        shutil.rmtree(target)

    def preload_assets(self, args: argparse.Namespace) -> None:
        """Précharge les ressources avec les dépendances Python du venv sélectionné."""
        subprocess.run([str(self.installer.python),
                        str(self.installer.root / 'bin/install_dependances.py'),
                        '--venv', str(self.installer.venv), '--assets-worker',
                        *InstallationAssets.worker_arguments(args)], check=True)

    def run(self, *, reset: bool = False, test: bool = False,
            install_only: bool = False, assets_only: bool = False,
            assets: argparse.Namespace | None = None) -> None:
        """Installe puis vérifie, ou teste sans mutation, avec précontrôle avant reset.

        reset est incompatible avec test. install_only saute les calculs GPU et
        le contrôle pilote Linux, mais conserve la validation de plateforme.
        Les erreurs des moteurs et de pip sont propagées sans repli CPU.
        Les modèles sont préchargés après pip, avant vérification GPU. assets_only
        saute pip et le test GPU ; test ne télécharge aucune ressource.
        reset supprime aussi les modèles du cache projet et les poids épinglés
        des caches personnalisés, mais conserve le catalogue Gaia.
        """
        if (test and (reset or install_only or assets_only)
                or assets_only and (reset or install_only)):
            raise ValueError('Modes incompatibles : test, installation seule, ressources seules ou reset')
        if assets is None:
            parser = argparse.ArgumentParser(add_help=False)
            InstallationAssets.add_arguments(parser)
            assets = parser.parse_args([])
        label = 'macOS Apple Silicon — MPS/CoreML' if self.platform == 'macos' else 'Linux — NVIDIA CUDA'
        print(f'Plateforme détectée : {label}\nVenv : {self.installer.venv}', flush=True)
        if not (test or assets_only):
            if reset:
                if self.platform == 'cuda' and not install_only:
                    self.installer.preflight()
                model_options = InstallationAssets.resolved_options(self.installer.root, assets)
                self.reset_environment()
                ResourceCache(self.installer.root).reset_models((model_options.cosmic_model_dir,
                                                                 model_options.graxpert_model_dir))
            self.installer.install(install_only)
        if not test and (assets.models or assets.with_gaia):
            self.preload_assets(assets)
        if assets_only:
            print('Ressources demandées préchargées et vérifiées.', flush=True)
        elif not install_only:
            self.installer.verify()
            print('Contrôles des accélérateurs réussis.', flush=True)
        else:
            print('Dépendances installées ; accélérateurs non vérifiés. '
                  'Relancer avec --test.', flush=True)


def main() -> int:
    """CLI commune ; retourne 0 en cas de succès, 1 en échec, argparse utilise 2.

    --reset ne supprime rien avec --test ; les options sont validées avant
    toute sélection ou mutation. L'aide n'importe aucune bibliothèque GPU.
    """
    parser = argparse.ArgumentParser(description='Installer les dépendances du venv selon la plateforme')
    parser.add_argument('--venv', type=Path, help='Venv cible ; sinon VENV_DIR, .venv ou venv')
    parser.add_argument('--reset', action='store_true',
                        help='Nettoyer le venv et les modèles puis réinstaller ; conserver Gaia')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--test', '--check-only', dest='test', action='store_true',
                       help='Tester les accélérateurs du venv existant sans installer')
    modes.add_argument('--install-only', action='store_true', help='Installer sans tester les accélérateurs')
    modes.add_argument('--assets-only', action='store_true',
                       help='Précharger les modèles/catalogues dans un venv déjà installé, sans pip')
    InstallationAssets.add_arguments(parser)
    workers = parser.add_mutually_exclusive_group()
    workers.add_argument('--verify-worker', action='store_true', help=argparse.SUPPRESS)
    workers.add_argument('--assets-worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.reset and (args.test or args.verify_worker or args.assets_worker or args.assets_only):
        parser.error('--reset est incompatible avec --test/--check-only, --assets-only et le contrôle interne')
    if (args.verify_worker or args.assets_worker) and (args.test or args.install_only or args.assets_only):
        parser.error('Le contrôle interne ne se combine pas avec un mode utilisateur')
    if args.test and args.with_gaia:
        parser.error('--test ne télécharge rien : utiliser --assets-only --with-gaia')
    try:
        manager = DependencyInstaller(Path(__file__).resolve().parents[1], args.venv)
        if args.verify_worker:
            manager.installer.verify_worker()
        elif args.assets_worker:
            InstallationAssets.install(manager.installer.root, args)
        else:
            manager.run(reset=args.reset, test=args.test, install_only=args.install_only,
                        assets_only=args.assets_only, assets=args)
        return 0
    except (OSError, ValueError, RuntimeError, ImportError, AssertionError,
            subprocess.CalledProcessError) as error:
        print(f'Échec de la préparation des dépendances : {error}', file=sys.stderr)
        return 1
