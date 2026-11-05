"""Installation reprenable du catalogue spectral Gaia DR3 Siril 1.0.1.

Les 48 fichiers et leurs empreintes sont épinglés dans gaia_spcc_manifest.json.
Les données valides sont réutilisées sans réseau ; les acquisitions ne modifient
ni les préférences Siril ni les sources importées. Voir le guide des ressources.
"""
from __future__ import annotations

import bz2
import errno
import http.client
import json
import os
from pathlib import Path
import re
import shutil
from tempfile import NamedTemporaryFile
from typing import TypedDict
from urllib.error import URLError
from urllib.request import Request, urlopen

from lib.gaia_catalogue import GaiaCatalogueInstaller
from lib.resource_manager import ResourceManager


class SpectralFile(TypedDict):
    """Tailles en octets et empreintes officielles d'une région HEALpix."""

    name: str
    size: int
    sha256: str
    archive_size: int
    archive_md5: str


class SPCCInstallationError(RuntimeError):
    """Bilan d'une sélection incomplète après tentative de toutes ses régions."""

    def __init__(self, failures: dict[str, str], completed: tuple[str, ...]) -> None:
        """Conserve les fichiers validés et les raisons précises des échecs."""
        self.failures = dict(failures)
        self.completed = completed
        details = '; '.join(f'{name}: {reason}' for name, reason in failures.items())
        super().__init__(f'SPCC incomplet : {len(completed)} région(s) vérifiée(s), '
                         f'{len(failures)} en échec — {details}')


class GaiaSPCCInstaller:
    """Installe une sélection de régions du ciel avec publication atomique."""

    source = 'https://zenodo.org/records/14738271/files'
    block_size = 1024 * 1024
    attempts = 3
    coverage = 'all'
    # HEALpix NESTED, ordre 1 (NSIDE=2), repère équatorial Gaia.
    # Les sous-pixels 1 et 2 des faces équatoriales chevauchent l'équateur.
    NORTH = frozenset(range(16)) | frozenset(
        base + sub for base in (16, 20, 24, 28) for sub in (1, 2, 3)
    )
    SOUTH = frozenset(range(32, 48)) | frozenset(
        base + sub for base in (16, 20, 24, 28) for sub in (0, 1, 2)
    )

    def __init__(self) -> None:
        """Charge le manifeste distribué avec le programme, sans accès réseau."""
        self.files: list[SpectralFile] = json.loads(
            Path(__file__).with_name('gaia_spcc_manifest.json').read_text(encoding='utf-8')
        )['files']

    def selected_files(self) -> list[SpectralFile]:
        """Sélectionne le nord/sud céleste, leur complément nord ou le ciel entier.

        north et south incluent les huit régions qui chevauchent l'équateur.
        remaining contient uniquement les vingt régions non incluses dans north.
        Les limites sont les déclinaisons du ciel, pas l'horizon de l'observateur.
        """
        selections = {'north': self.NORTH, 'south': self.SOUTH,
                      'remaining': frozenset(range(48)) - self.NORTH,
                      'all': frozenset(range(48))}
        if self.coverage not in selections:
            raise ValueError('Couverture SPCC attendue : north, south, remaining ou all')
        return [item for item in self.files
                if int(item['name'].split('_')[-1].split('.')[0]) in selections[self.coverage]]

    def valid_directory(self, directory: Path) -> bool:
        """Vérifie toutes les régions de la sélection, sans charger les bases."""
        selected = self.selected_files()
        return bool(selected) and all(
            GaiaCatalogueInstaller.matches(directory / item['name'], item['size'], item['sha256'])
            for item in selected
        )

    @staticmethod
    def archive_matches(path: Path, item: SpectralFile) -> bool:
        """Vérifie taille et MD5 officiel de l'archive avant décompression.

        Le SHA-256 du fichier décompressé reste le contrôle final d'intégrité.
        """
        return ResourceManager.matches(path, item['archive_size'], item['archive_md5'], 'md5')

    def download(self, path: Path, item: SpectralFile) -> None:
        """Reprend une archive partielle ; contrôle les réponses HTTP Range.

        Un serveur ignorant Range déclenche un téléchargement depuis zéro.
        Trois essais sont permis. Les coupures conservent le préfixe reçu ; une
        archive complète corrompue est supprimée avant une nouvelle acquisition.
        Une erreur finale conserve les fichiers catalogue déjà installés.
        """
        for attempt in range(self.attempts):
            if self.archive_matches(path, item):
                return
            offset = path.stat().st_size if path.is_file() else 0
            if offset >= item['archive_size']:
                path.unlink()
                offset = 0
            request = Request(f"{self.source}/{item['name']}.bz2?download=1",
                              headers={'Range': f'bytes={offset}-'} if offset else {})
            try:
                with urlopen(request, timeout=120) as response:
                    status = getattr(response, 'status', 200)
                    if status == 206:
                        match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',
                                             response.headers.get('Content-Range', ''))
                        if (match is None or int(match[1]) != offset
                                or int(match[3]) != item['archive_size']
                                or not offset <= int(match[2]) < item['archive_size']):
                            raise ValueError('Réponse HTTP Range SPCC incohérente')
                    elif status == 200:
                        offset = 0
                    else:
                        raise OSError(f'Réponse HTTP SPCC inattendue : {status}')
                    received = offset
                    with path.open('ab' if offset else 'wb') as target:
                        while block := response.read(self.block_size):
                            received += len(block)
                            if received > item['archive_size']:
                                raise ValueError('Archive SPCC plus grande que prévu')
                            target.write(block)
                            progress_step = 16 * self.block_size
                            if received // progress_step != (received - len(block)) // progress_step:
                                print(f"SPCC {item['name']} : {received}/{item['archive_size']} octets", flush=True)
                if self.archive_matches(path, item):
                    return
                if received == item['archive_size']:
                    path.unlink(missing_ok=True)
                raise OSError('Archive SPCC incomplète ou empreinte incorrecte')
            except (OSError, URLError, http.client.HTTPException, ValueError) as error:
                print(f"SPCC : essai {attempt + 1}/{self.attempts} : {error}", flush=True)
                if attempt + 1 == self.attempts:
                    raise RuntimeError(f"Acquisition SPCC échouée pour {item['name']} ; "
                                       'les fichiers validés et téléchargements partiels sont conservés') from error

    @staticmethod
    def import_file(source: Path, destination: Path, item: SpectralFile) -> None:
        """Lie ou copie un catalogue externe vérifié sans modifier sa source."""
        with NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
            staged = Path(stream.name)
        try:
            staged.unlink()
            try:
                os.link(source, staged)
            except OSError as error:
                if error.errno != errno.EXDEV:
                    raise
                shutil.copyfile(source, staged)
            if not GaiaCatalogueInstaller.matches(staged, item['size'], item['sha256']):
                raise ValueError('Catalogue SPCC source modifié pendant l’import')
            os.replace(staged, destination)
        finally:
            staged.unlink(missing_ok=True)

    def install(self, directory: Path, sources: tuple[Path, ...] = ()) -> Path:
        """Installe la sélection en réutilisant les régions/archives existantes.

        Les fichiers sont traités séquentiellement afin de borner le besoin disque
        au catalogue installé et à une archive avec son fichier décompressé.
        Les archives partielles externes sont copiées puis reprises. Chaque .dat
        n'est publié qu'après vérification SHA-256 ; les archives complètes sont
        supprimées après publication. Chaque erreur de région est rapportée et
        les suivantes sont tentées ; SPCCInstallationError résume les échecs à
        la fin sans certifier une sélection incomplète.
        """
        directory = directory.expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        sources = tuple(path.expanduser().resolve() for path in sources)
        selected = self.selected_files()
        print(f'SPCC : préparation {self.coverage} — {len(selected)} régions, '
              f'{sum(item["size"] for item in selected) / 1e9:.2f} Go installés', flush=True)
        failures: dict[str, str] = {}
        completed = []
        for number, item in enumerate(selected, 1):
            try:
                self.install_region(directory, sources, item, number, len(selected))
            except (OSError, EOFError, ValueError, RuntimeError) as error:
                failures[item['name']] = str(error)
                print(f"SPCC {number}/{len(selected)} en échec : {item['name']} : {error}", flush=True)
                continue
            completed.append(item['name'])
        print(f'SPCC : {len(completed)}/{len(selected)} régions vérifiées ; '
              f'{len(failures)} en échec ({self.coverage})', flush=True)
        if failures:
            raise SPCCInstallationError(failures, tuple(completed))
        return directory

    def install_region(
        self, directory: Path, sources: tuple[Path, ...], item: SpectralFile,
        number: int, total: int,
    ) -> None:
        """Prépare une seule région sans modifier les autres fichiers déjà validés."""
        destination = directory / item['name']
        if GaiaCatalogueInstaller.matches(destination, item['size'], item['sha256']):
            print(f"SPCC {number}/{total} déjà vérifié : {destination.name}", flush=True)
            return
        source = next((path / item['name'] for path in sources
                       if GaiaCatalogueInstaller.matches(path / item['name'], item['size'],
                                                        item['sha256'])), None)
        if source is not None:
            self.import_file(source, destination, item)
            return
        archive = directory / (item['name'] + '.bz2.part')
        existing = archive.stat().st_size if archive.is_file() else 0
        # Le .dat précédent reste présent jusqu'au remplacement atomique.
        required = max(0, item['archive_size'] - existing) + item['size']
        if shutil.disk_usage(directory).free < required:
            raise OSError(f"SPCC : {required} octets libres nécessaires pour {item['name']}")
        if not archive.exists():
            for path in sources:
                candidate = path / (item['name'] + '.bz2.part')
                if not candidate.is_file():
                    candidate = path / (item['name'] + '.bz2')
                if candidate.is_file() and candidate.stat().st_size <= item['archive_size']:
                    shutil.copyfile(candidate, archive)
                    break
        self.download(archive, item)
        with NamedTemporaryFile(dir=directory, delete=False) as stream:
            staged = Path(stream.name)
        try:
            with bz2.open(archive, 'rb') as packed, staged.open('wb') as target:
                size = 0
                while block := packed.read(self.block_size):
                    size += len(block)
                    if size > item['size']:
                        raise ValueError(f'Catalogue SPCC décompressé plus grand que prévu : '
                                         f'{size} octets lus, {item["size"]} attendus')
                    target.write(block)
            if not GaiaCatalogueInstaller.matches(staged, item['size'], item['sha256']):
                raise ValueError('Taille ou SHA-256 du catalogue SPCC incorrect')
            os.replace(staged, destination)
            archive.unlink()
            print(f'SPCC {number}/{total} installé et vérifié : {destination.name}', flush=True)
        finally:
            staged.unlink(missing_ok=True)
