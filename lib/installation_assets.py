"""Préchargement des modèles vérifiés et du catalogue Gaia facultatif hors venv.

Voir docs/scripts/install_dependances/README.md. Les téléchargements utilisent
les mécanismes existants Cosmic Clarity/GraXpert ; aucun identifiant GraXpert
n'est extrait et aucune préférence Siril n'est modifiée.
"""

from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path

from lib.resource_cache import ResourceCache


class InstallationAssets:
    """Résout CLI > JSON > défaut et précharge les ressources de traitement."""

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        """Déclare les caches et les acquisitions ; Gaia reste optionnel."""
        parser.add_argument('--models', action=argparse.BooleanOptionalAction, default=True,
                            help='Précharger les modèles Cosmic Clarity et GraXpert (défaut : oui)')
        parser.add_argument('--with-gaia', action='store_true',
                            help='Acquérir aussi Gaia astrométrique (1,14 Go / 1,52 Go installé)')
        parser.add_argument('--config', type=Path, default=Path.home() / '.siril_darklib_config.json',
                            help='Configuration des chemins de modèles, lue sans modification')
        parser.add_argument('--cosmic-model-dir', type=Path)
        parser.add_argument('--graxpert-model-dir', type=Path)
        parser.add_argument('--graxpert-path', help='Application GraXpert pour acquérir un poids absent')
        parser.add_argument('--gaia-dir', type=Path,
                            help='Dossier Gaia ; défaut tmp/cache/catalogues/gaia du projet')

    @staticmethod
    def worker_arguments(args: argparse.Namespace) -> list[str]:
        """Transmet les options au processus du venv, sans interprétation shell."""
        flags = ['--models' if args.models else '--no-models']
        if args.with_gaia:
            flags.append('--with-gaia')
        for name in ('config', 'cosmic_model_dir', 'graxpert_model_dir', 'graxpert_path', 'gaia_dir'):
            value = getattr(args, name, None)
            if value is not None:
                flags.extend(['--' + name.replace('_', '-'), str(value)])
        return flags

    @staticmethod
    def resolved_options(root: Path, args: argparse.Namespace) -> argparse.Namespace:
        """Lit les chemins CLI > JSON > cache commun, sans importer de moteur scientifique."""
        config_path = args.config.expanduser().resolve()
        config = json.loads(config_path.read_text()) if config_path.is_file() else {}
        if not isinstance(config, dict):
            raise ValueError('La configuration des ressources doit être un objet JSON')
        values = {key: config[key] for key in (
            'cosmic_model_dir', 'graxpert_model_dir', 'graxpert_model_version', 'graxpert_path', 'siril_mode',
        ) if config.get(key) is not None}
        values.setdefault('siril_mode', 'native' if platform.system() == 'Darwin' else 'flatpak')
        if platform.system() == 'Darwin':
            values.setdefault('graxpert_path', 'GraXpert')
        for key in ('cosmic_model_dir', 'graxpert_model_dir', 'graxpert_path'):
            if getattr(args, key, None) is not None:
                values[key] = getattr(args, key)
        cache = ResourceCache(root)
        values['cosmic_model_dir'] = cache.model_directory('cosmicclarity', values.get('cosmic_model_dir'))
        values['graxpert_model_dir'] = cache.model_directory('graxpert', values.get('graxpert_model_dir'))
        return argparse.Namespace(**values)

    @staticmethod
    def install(root: Path, args: argparse.Namespace) -> dict[str, str]:
        """Précharge les ressources demandées et retourne leurs chemins vérifiés.

        La configuration n'est lue que pour les chemins/version des modèles et
        le mode Siril. Les caches conformes sont réutilisés. Une erreur empêche
        l'annonce d'installation complète. GraXpert exige son application ou
        un cache local valide ; StarNet conserve le modèle livré avec son CLI.
        Gaia n'est téléchargé que sur demande, sans catalogue spectral SPCC.
        """
        from lib.cosmic_clarity.models import install_models
        from lib.graxpert_models import GraXpertModels
        from lib.gaia_catalogue import GaiaCatalogueInstaller
        from lib.star_separation import StarNetSeparator

        cache = ResourceCache(root)
        resolved = InstallationAssets.resolved_options(root, args)
        result = {}
        if args.models:
            cosmic = Path(resolved.cosmic_model_dir).expanduser().resolve()
            print('Préchargement des six modèles Cosmic Clarity (~94 Mo)', flush=True)
            install_models(cosmic)
            result['cosmic_model_dir'] = str(cosmic)
            print('Préchargement du modèle GraXpert 3.0.2 (~478 Mo)', flush=True)
            result['graxpert_model'] = str(GraXpertModels(resolved).ensure_model())
            print(f'Modèle GraXpert vérifié : {result["graxpert_model"]}', flush=True)
        if args.with_gaia:
            preferences, _ = StarNetSeparator.preferences(resolved)
            configured = preferences.get('core', 'catalogue_gaia_astro', fallback='')
            sources = (Path(configured).expanduser(),) if configured else ()
            directory = args.gaia_dir or cache.gaia_directory
            result['gaia_astrometric'] = str(GaiaCatalogueInstaller().install(directory, sources))
            cache.register_gaia(Path(result['gaia_astrometric']))
            print(f'Gaia dans le cache : {result["gaia_astrometric"]}', flush=True)
            print('Gaia local sera utilisé automatiquement pour le plate solving. '
                  'Les spectres SPCC ne sont pas inclus.', flush=True)
        return result
