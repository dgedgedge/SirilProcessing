"""Acquisition atomique de l'extrait Gaia DR3 astrométrique pour Siril 1.4+.

Source CC-BY-4.0 : https://zenodo.org/records/14692304. Les empreintes des
archives et du fichier extrait sont publiées par Siril sur ce même dépôt.
Ce catalogue ne contient pas les spectres nécessaires à SPCC.
"""

from __future__ import annotations

import bz2
import hashlib
import errno
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from urllib.request import urlopen


class GaiaCatalogueInstaller:
    """Télécharge 1,14 Go puis valide le fichier Gaia de 1,52 Go, sans lancer Siril."""

    filename = 'siril_cat_healpix8_astro.dat'
    url = 'https://zenodo.org/records/14692304/files/siril_cat_healpix8_astro.dat.bz2?download=1'
    archive_size = 1142276749
    size = 1521132640
    archive_sha256 = '846ad4b12c50865df0cb8c5b23453f22eec78bbe9969e17d669ae19eb49d421f'
    sha256 = '2fa40c93fe115235d35c5050757f2ef60a326a6f3030f87be1598c016fcb2388'

    @staticmethod
    def matches(path: Path, size: int, digest: str) -> bool:
        """Vérifie taille et SHA-256 par blocs sans charger le catalogue en mémoire."""
        if not path.is_file() or path.stat().st_size != size:
            return False
        checksum = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                checksum.update(block)
        return checksum.hexdigest() == digest

    def install(self, directory: Path, sources: tuple[Path, ...] = ()) -> Path:
        """Réutilise un fichier vérifié ou télécharge/décompresse avant remplacement.

        directory accueille le fichier, sources contient les catalogues déjà
        configurés dans Siril. Une source valide est liée au cache (lien dur),
        ou copiée si elle est sur un autre disque. Un échec préserve la
        destination existante et supprime les fichiers temporaires. Aucun
        paramètre Siril n'est modifié ; le chemin utilisé est retourné.
        """
        destination = directory.expanduser().resolve() / self.filename
        for candidate in (destination, *sources):
            if self.matches(candidate, self.size, self.sha256):
                print(f'Gaia astrométrique déjà vérifié : {candidate}', flush=True)
                if candidate.resolve() == destination.resolve():
                    return destination
                destination.parent.mkdir(parents=True, exist_ok=True)
                with TemporaryDirectory(dir=destination.parent, prefix='gaia-') as temporary:
                    staged = Path(temporary) / self.filename
                    try:
                        os.link(candidate, staged)
                    except OSError as error:
                        if error.errno != errno.EXDEV:
                            raise
                        shutil.copyfile(candidate, staged)
                    if not self.matches(staged, self.size, self.sha256):
                        raise ValueError('Catalogue Gaia source modifié pendant l’import')
                    os.replace(staged, destination)
                return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(destination.parent).free < self.archive_size + self.size:
            raise OSError('Gaia : environ 2,7 Go libres sont nécessaires pendant l’installation')
        print('Gaia astrométrique : téléchargement de 1,14 Go, installation de 1,52 Go', flush=True)
        with TemporaryDirectory(dir=destination.parent, prefix='gaia-') as temporary:
            archive, unpacked = Path(temporary) / 'catalogue.bz2', Path(temporary) / self.filename
            with urlopen(self.url, timeout=120) as source, archive.open('wb') as target:
                received = 0
                while block := source.read(1024 * 1024):
                    received += len(block)
                    if received > self.archive_size:
                        raise ValueError('Archive Gaia plus grande que la taille épinglée')
                    target.write(block)
            if not self.matches(archive, self.archive_size, self.archive_sha256):
                raise ValueError('Taille ou SHA-256 de l’archive Gaia incorrect')
            with bz2.open(archive, 'rb') as source, unpacked.open('wb') as target:
                extracted = 0
                while block := source.read(1024 * 1024):
                    extracted += len(block)
                    if extracted > self.size:
                        raise ValueError('Catalogue Gaia décompressé plus grand que prévu')
                    target.write(block)
            if not self.matches(unpacked, self.size, self.sha256):
                raise ValueError('Taille ou SHA-256 du catalogue Gaia incorrect')
            os.replace(unpacked, destination)
        print(f'Gaia astrométrique installé et vérifié : {destination}', flush=True)
        return destination
