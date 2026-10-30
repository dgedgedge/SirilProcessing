"""Arborescence commune des modèles et catalogues, indépendante du venv.

Voir docs/scripts/install_dependances/resources.md. Les anciens chemins par
défaut sont reconnus ; les surcharges de dossiers restent explicites.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
from tempfile import NamedTemporaryFile


class ResourceCache:
    """Centralise tmp/cache, la découverte Gaia et le nettoyage des modèles."""

    def __init__(self, root: Path | None = None) -> None:
        """Résout le dépôt et son cache sans créer de dossier."""
        self.root = (root or Path(__file__).resolve().parents[1]).resolve()
        self.directory = self.root / 'tmp/cache'

    def model_directory(self, name: str, value: str | Path | None = None) -> Path:
        """Résout un cache modèle ; migre uniquement l'ancien défaut du projet."""
        if name not in ('cosmicclarity', 'graxpert'):
            raise ValueError('Cache de modèles inconnu')
        destination = self.directory / 'models' / name
        if value is None:
            return destination
        if not isinstance(value, (str, Path)):
            raise ValueError('Un chemin de cache doit être une chaîne ou un Path')
        supplied = Path(value).expanduser().resolve()
        return destination if supplied == self.root / 'tmp/models' / name else supplied

    @staticmethod
    def cosmic_argument(value: str) -> str:
        """Normalise aussi les anciens chemins Cosmic des CLI et JSON."""
        return str(ResourceCache().model_directory('cosmicclarity', value))

    @staticmethod
    def graxpert_argument(value: str) -> Path:
        """Normalise aussi les anciens chemins GraXpert des CLI et JSON."""
        return ResourceCache().model_directory('graxpert', value)

    @property
    def gaia_directory(self) -> Path:
        """Retourne le dossier Gaia commun, hors modèles nettoyés par reset."""
        return self.directory / 'catalogues/gaia'

    def register_gaia(self, path: Path) -> None:
        """Mémorise atomiquement le chemin acquis pour les futures résolutions Siril.

        Le fichier a été vérifié par l'installateur Gaia. Le JSON n'est pas une
        attestation : find_gaia vérifie à nouveau taille et SHA avant utilisation.
        """
        self.gaia_directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode='w', dir=self.gaia_directory, delete=False,
                                    encoding='utf-8', suffix='.tmp') as stream:
                temporary = Path(stream.name)
                json.dump({'path': str(path.resolve())}, stream)
            os.replace(temporary, self.gaia_directory / 'location.json')
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def find_gaia(self) -> Path | None:
        """Retourne un catalogue local vérifié, sans réseau ni écriture de préférences."""
        from lib.gaia_catalogue import GaiaCatalogueInstaller

        catalogue = GaiaCatalogueInstaller()
        candidates = [self.gaia_directory / catalogue.filename,
                      self.root / 'tmp/catalogues/gaia' / catalogue.filename]
        metadata = self.gaia_directory / 'location.json'
        if metadata.is_file():
            try:
                value = json.loads(metadata.read_text())
                if isinstance(value, dict) and isinstance(value.get('path'), str):
                    candidates.append(Path(value['path']))
            except (OSError, ValueError):
                pass
        for candidate in dict.fromkeys(candidates):
            try:
                if catalogue.matches(candidate, catalogue.size, catalogue.sha256):
                    return candidate.resolve()
            except OSError:
                continue
        return None

    def reset_models(self, custom: tuple[Path, Path]) -> None:
        """Nettoie les modèles du projet et les poids connus des caches personnalisés.

        Les répertoires modèles dédiés du projet, courant et ancien, sont
        supprimés entièrement après validation. Dans un dossier personnalisé,
        seuls les fichiers de poids épinglés sont supprimés. Gaia et les caches
        des applications externes restent intacts. Refuse les cibles racine,
        domicile, dépôt et liens de dossiers du cache projet.
        """
        from lib.cosmic_clarity.models import manifest

        owned = (self.directory / 'models', self.root / 'tmp/models')
        for parent in (self.root / 'tmp', self.directory, *owned):
            if parent.is_symlink():
                raise ValueError(f'Nettoyage des modèles refusé : lien de dossier {parent}')
        for directory in custom:
            resolved = directory.resolve()
            if (self.root.is_relative_to(resolved) or resolved == Path.home().resolve()
                    or Path(sys.base_prefix).resolve().is_relative_to(resolved)):
                raise ValueError(f'Nettoyage des modèles refusé : {directory}')
        for directory in owned:
            if directory.exists():
                print(f'Nettoyage des modèles : {directory}', flush=True)
                shutil.rmtree(directory)
        cosmic, graxpert = custom
        for name in manifest():
            (cosmic / name).unlink(missing_ok=True)
        for version in json.loads(Path(__file__).with_name('graxpert_models.json').read_text()):
            (graxpert / version / 'model.onnx').unlink(missing_ok=True)
