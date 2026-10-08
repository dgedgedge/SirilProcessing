"""Télécharge et vérifie les poids Cosmic Clarity sans importer PyTorch.

Les fichiers sont installés atomiquement après vérification SHA-256. Le manifeste
versionné fixe les noms, URL et empreintes ; aucun téléchargement à l'inférence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TypedDict
from urllib.request import urlopen


DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[2] / 'models' / 'cosmicclarity'
MANIFEST_PATH = Path(__file__).with_name('models.json')
STELLAR_MODEL = 'deep_sharp_stellar_cnn_AI3_5s.pth'
DENOISE_MODEL = 'deep_denoise_cnn_AI3_6.pth'
NONSTELLAR_MODELS = {
    radius: f'deep_nonstellar_sharp_cnn_radius_{radius}AI3_5s.pth'
    for radius in (1, 2, 4, 8)
}


class ModelEntry(TypedDict):
    """Provenance HTTPS, taille en octets et empreinte d'un poids approuvé."""

    url: str
    sha256: str
    size: int


def manifest() -> dict[str, ModelEntry]:
    """Lit le manifeste distribué avec cette version du code."""
    return json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))


def sha256_file(path: Path) -> str:
    """Calcule l'empreinte en blocs pour limiter la mémoire au téléchargement."""
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verify_model(directory: Path, name: str) -> Path:
    """Retourne un poids vérifié, ou lève une erreur avec la commande de réparation."""
    entry = manifest()[name]
    path = directory / name
    if (not path.is_file() or path.stat().st_size != entry['size']
            or sha256_file(path) != entry['sha256']):
        raise ValueError(
            f'Modèle absent ou corrompu : {path}. Exécuter '
            'bin/postProcess.sh --install-cosmic-clarity '
            f'--cosmic-model-dir "{directory}"'
        )
    return path


def install_models(directory: Path) -> None:
    """Installe les six poids ; conserve les fichiers déjà conformes.

    Une interruption ou une empreinte incorrecte ne remplace pas le fichier
    précédent. Les erreurs réseau et disque sont propagées à l'appelant.
    """
    directory.mkdir(parents=True, exist_ok=True)
    for name, entry in manifest().items():
        try:
            verify_model(directory, name)
        except ValueError:
            temporary = None
            try:
                with NamedTemporaryFile(dir=directory, suffix='.part', delete=False) as dst:
                    temporary = Path(dst.name)
                    with urlopen(entry['url'], timeout=120) as src:
                        while block := src.read(1024 * 1024):
                            dst.write(block)
                if (temporary.stat().st_size != entry['size']
                        or sha256_file(temporary) != entry['sha256']):
                    raise ValueError(f'Empreinte ou taille incorrecte : {name}')
                os.replace(temporary, directory / name)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        print(f'Modèle vérifié : {directory / name}', flush=True)


def main() -> None:
    """Installe les modèles dans le dossier du projet ou dans --directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DEFAULT_MODEL_DIR)
    args = parser.parse_args()
    install_models(args.directory.expanduser().resolve())


if __name__ == '__main__':
    main()
