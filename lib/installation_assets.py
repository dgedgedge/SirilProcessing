"""Préchargement des modèles vérifiés et des catalogues Gaia hors venv.

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
        """Active toutes les ressources par défaut et expose leurs désactivations."""
        parser.add_argument('--models', action=argparse.BooleanOptionalAction, default=True,
                            help='Précharger les modèles Cosmic Clarity et GraXpert (défaut : oui)')
        parser.add_argument('--disable-models', dest='models', action='store_false',
                            help='Ne pas précharger les modèles (alias --no-models)')
        parser.add_argument('--disable-gaia', action='store_true',
                            help='Ne préparer aucun catalogue Gaia, astrométrique ni SPCC')
        parser.add_argument('--disable-gaia-astrometry', dest='with_gaia', action='store_false',
                            help='Ne pas préparer le catalogue Gaia astrométrique')
        parser.add_argument('--disable-gaia-spcc', dest='with_gaia_spcc', action='store_false',
                            help='Ne pas préparer le catalogue spectral Gaia SPCC')
        parser.add_argument('--with-gaia', action='store_true', default=True,
                            help='Préparer Gaia astrométrique (activé par défaut ; SPCC activé séparément)')
        parser.add_argument('--with-gaia-spcc', action='store_true', default=True,
                            help='Préparer les régions SPCC sélectionnées (nord par défaut)')
        parser.add_argument('--gaia-spcc-coverage', choices=('north', 'south', 'remaining', 'all'),
                            default='north', help='Couverture SPCC : nord par défaut, sud, complément du nord ou ciel entier')
        parser.add_argument('--gaia-spcc-dir', type=Path,
                            help='Dossier spectral ; défaut tmp/cache/catalogues/gaia/spcc')
        parser.add_argument('--config', type=Path, default=Path.home() / '.siril_darklib_config.json',
                            help='Configuration des chemins de modèles, lue sans modification')
        parser.add_argument('--cosmic-model-dir', type=Path)
        parser.add_argument('--graxpert-model-dir', type=Path)
        parser.add_argument('--graxpert-path', help='Application GraXpert pour acquérir un poids absent')
        parser.add_argument('--gaia-dir', type=Path,
                            help='Dossier Gaia ; défaut tmp/cache/catalogues/gaia du projet')

    @staticmethod
    def catalogue_selection(args: argparse.Namespace) -> tuple[bool, bool]:
        """Résout la désactivation globale puis les sélections indépendantes.

        --disable-gaia prime sur les deux réglages individuels. Les anciens
        namespaces sans option SPCC conservent leur sélection Gaia commune.
        """
        enabled = not getattr(args, 'disable_gaia', False)
        astrometry = getattr(args, 'with_gaia', True)
        spectral = getattr(args, 'with_gaia_spcc', astrometry)
        return enabled and astrometry, enabled and spectral

    @staticmethod
    def worker_arguments(args: argparse.Namespace) -> list[str]:
        """Transmet les options au processus du venv, sans interprétation shell."""
        flags = ['--models' if args.models else '--no-models']
        astrometry, spectral = InstallationAssets.catalogue_selection(args)
        flags.append('--with-gaia' if astrometry else '--disable-gaia-astrometry')
        flags.append('--with-gaia-spcc' if spectral else '--disable-gaia-spcc')
        for name in ('gaia_spcc_coverage', 'gaia_spcc_dir', 'config', 'cosmic_model_dir', 'graxpert_model_dir', 'graxpert_path', 'gaia_dir'):
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
        Les deux catalogues Gaia sont préparés par défaut, sauf désactivation explicite.
        """
        from lib.resource_manager import ResourceManager

        resolved = InstallationAssets.resolved_options(root, args)
        resources = ResourceManager(resolved, root)
        result = {}
        if args.models:
            print('Préchargement des six modèles Cosmic Clarity (~94 Mo)', flush=True)
            result['cosmic_model_dir'] = str(resources.cosmic_models())
            print('Préchargement du modèle GraXpert 3.0.2 (~478 Mo)', flush=True)
            result['graxpert_model'] = str(resources.graxpert_model())
            print(f'Modèle GraXpert vérifié : {result["graxpert_model"]}', flush=True)
        astrometry, spectral = InstallationAssets.catalogue_selection(args)
        if astrometry:
            result['gaia_astrometric'] = str(resources.gaia_astrometry(args.gaia_dir, acquire=True))
            print(f'Gaia dans le cache : {result["gaia_astrometric"]}', flush=True)
        if spectral:
            result['gaia_spcc'] = str(resources.gaia_spcc(
                getattr(args, 'gaia_spcc_dir', None), acquire=True,
                coverage=getattr(args, 'gaia_spcc_coverage', 'north'),
            ))
            print(f'SPCC local prêt pour la sélection : {result["gaia_spcc"]}', flush=True)
        return result
