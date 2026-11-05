"""Gestion commune des catalogues et poids pour installation et traitements.

Les adaptateurs conservent leurs protocoles (archives Gaia, CLI GraXpert,
manifeste Cosmic Clarity). Ce gestionnaire partage les chemins, la découverte,
les contrôles et l'acquisition sans importer les moteurs de calcul au chargement.
Voir docs/scripts/install_dependances/resources.md.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from lib.resource_cache import ResourceCache

if TYPE_CHECKING:
    from lib.graxpert_models import GraXpertModels


class ResourceManager:
    """Accès partagé aux ressources locales vérifiées et aux acquisitions explicites."""

    def __init__(self, args: argparse.Namespace | None = None, root: Path | None = None) -> None:
        """Capture les chemins résolus de l'appelant et le cache commun hors venv."""
        self.args = deepcopy(args) if args is not None else argparse.Namespace()
        self.cache = ResourceCache(root)

    @staticmethod
    def file_checksum(path: Path, algorithm: Literal['sha256', 'md5'] = 'sha256') -> str:
        """Calcule une empreinte par blocs de 1 Mio, sans modifier le fichier.

        MD5 ne sert qu'au contrôle publié des archives ; le SHA-256 des données
        extraites reste obligatoire. Un fichier fourni avec Git est traité comme
        tout fichier local : pas de réseau s'il est conforme au manifeste.
        """
        if algorithm not in ('sha256', 'md5'):
            raise ValueError('Algorithme d’intégrité non pris en charge')
        digest = hashlib.new(algorithm, usedforsecurity=algorithm != 'md5')
        with path.open('rb') as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def matches(
        path: Path, size: int, digest: str, algorithm: Literal['sha256', 'md5'] = 'sha256',
    ) -> bool:
        """Contrôle présence, taille en octets et empreinte avant réutilisation."""
        return (path.is_file() and path.stat().st_size == size
                and ResourceManager.file_checksum(path, algorithm) == digest)

    def cosmic_model(self, name: str, directory: Path | None = None) -> Path:
        """Réutilise ou acquiert le seul poids Cosmic Clarity nécessaire au calcul."""
        from lib.cosmic_clarity.models import ensure_model

        selected = self.cache.model_directory(
            'cosmicclarity', directory if directory is not None
            else getattr(self.args, 'cosmic_model_dir', None),
        )
        return ensure_model(selected, name)

    def cosmic_models(self, directory: Path | None = None) -> Path:
        """Prépare les six poids via le même adaptateur que l'inférence."""
        from lib.cosmic_clarity.models import install_models

        selected = self.cache.model_directory(
            'cosmicclarity', directory if directory is not None
            else getattr(self.args, 'cosmic_model_dir', None),
        )
        install_models(selected)
        return selected

    def graxpert_model(self, provider: GraXpertModels | None = None) -> Path:
        """Réutilise, importe ou acquiert le poids par le mécanisme officiel GraXpert."""
        from lib.graxpert_models import GraXpertModels

        if provider is None:
            args = deepcopy(self.args)
            args.graxpert_model_dir = self.cache.model_directory(
                'graxpert', getattr(args, 'graxpert_model_dir', None),
            )
            provider = GraXpertModels(args)
        return provider.ensure_model()

    def find_graxpert(self) -> Path | None:
        """Recherche un poids conforme sans acquisition pour l'activation automatique."""
        from lib.graxpert_models import GraXpertModels

        args = deepcopy(self.args)
        args.graxpert_model_dir = self.cache.model_directory(
            'graxpert', getattr(args, 'graxpert_model_dir', None),
        )
        return GraXpertModels(args).find_local_model()

    def catalogue_sources(self, spectral: bool) -> tuple[Path, ...]:
        """Partage la découverte des catalogues déjà configurés ou acquis par Siril."""
        from lib.star_separation import StarNetSeparator

        preferences, _ = StarNetSeparator.preferences(self.args)
        key = 'catalogue_gaia_photo' if spectral else 'catalogue_gaia_astro'
        configured = preferences.get('core', key, fallback='')
        sources: tuple[Path, ...] = ()
        if configured:
            path = Path(configured).expanduser()
            sources = (path.parent if spectral and path.suffix == '.dat' else path,)
        if spectral:
            mode = getattr(self.args, 'siril_mode', 'flatpak')
            data = (Path.home() / '.var/app/org.siril.Siril/data' if mode == 'flatpak'
                    else Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')))
            sources += (data / 'siril/siril_cat1_healpix8_xpsamp',)
        return sources

    def gaia_astrometry(self, directory: Path | None = None, *, acquire: bool = False) -> Path | None:
        """Retourne Gaia vérifié ; acquire autorise l'import ou l'installation.

        Sans acquire, seule la découverte existante est effectuée, sans réseau
        ni écriture. Une destination explicite d'installation prime sur le cache
        enregistré ; les fichiers et préférences Siril sources restent intacts.
        """
        from lib.gaia_catalogue import GaiaCatalogueInstaller

        if directory is None:
            cached = self.cache.find_gaia()
            if cached is not None or not acquire:
                return cached
        if not acquire:
            path = directory.expanduser().resolve() / GaiaCatalogueInstaller.filename
            return path if GaiaCatalogueInstaller.matches(
                path, GaiaCatalogueInstaller.size, GaiaCatalogueInstaller.sha256,
            ) else None
        path = GaiaCatalogueInstaller().install(
            directory if directory is not None else self.cache.gaia_directory,
            self.catalogue_sources(False),
        )
        self.cache.register_gaia(path)
        return path

    def gaia_spcc(
        self, directory: Path | None = None, *, acquire: bool = False, coverage: str | None = None,
    ) -> Path | None:
        """Retourne une sélection vérifiée ; son installation cible north par défaut.

        coverage=None consulte la sélection enregistrée ; une acquisition sans
        sélection explicite cible north. Une sélection explicitement demandée
        est toujours vérifiée, même en lecture seule.
        L'adaptateur SPCC conserve la reprise HTTP, les contrôles d'archives et
        la publication atomique. Sans acquire, aucune requête ou écriture n'est
        permise et une sélection incomplète n'est pas proposée au traitement.
        """
        from lib.gaia_spcc import GaiaSPCCInstaller

        installer = GaiaSPCCInstaller()
        installer.coverage = 'north' if coverage is None else coverage
        installer.selected_files()  # Valider même si un cache connu est présent.
        if directory is None:
            cached = self.cache.find_spcc()
            if not acquire:
                if cached is not None and coverage is not None and not installer.valid_directory(cached):
                    return None
                return cached
            if cached is not None:
                if installer.valid_directory(cached):
                    self.cache.register_spcc(cached, installer.coverage)
                    return cached
                directory = cached
        if not acquire:
            path = directory.expanduser().resolve()
            return path if installer.valid_directory(path) else None
        path = installer.install(
            directory if directory is not None else self.cache.gaia_spcc_directory,
            self.catalogue_sources(True),
        )
        self.cache.register_spcc(path, installer.coverage)
        return path
