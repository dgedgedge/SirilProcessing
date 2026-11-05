"""Acquisition SPCC atomique, reprise HTTP et découverte sans accès réseau."""

import bz2
import hashlib
import io
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request

import pytest

from lib.gaia_spcc import GaiaSPCCInstaller, SPCCInstallationError
from lib.resource_cache import ResourceCache

Sample = tuple[GaiaSPCCInstaller, bytes, bytes]


class Response(io.BytesIO):
    """Réponse HTTP contrôlée, compatible avec le téléchargement par blocs."""

    def __init__(self, data: bytes, status: int = 200, content_range: str = '') -> None:
        """Fixe le corps, le statut et l'en-tête de reprise pour chaque essai."""
        super().__init__(data)
        self.status = status
        self.headers = {'Content-Range': content_range}


@pytest.fixture
def sample(monkeypatch: pytest.MonkeyPatch) -> tuple[GaiaSPCCInstaller, bytes, bytes]:
    """Épingle une véritable petite archive et interdit tout accès Internet."""
    data = bytes(range(256)) * 40
    archive = bz2.compress(data)
    item = dict(name='siril_cat1_healpix8_xpsamp_0.dat', size=len(data),
                sha256=hashlib.sha256(data).hexdigest(), archive_size=len(archive),
                archive_md5=hashlib.md5(archive, usedforsecurity=False).hexdigest())

    def initialize(self: GaiaSPCCInstaller) -> None:
        """Remplace le ciel complet par une région miniature aux mêmes contrôles."""
        self.files = [item.copy()]

    monkeypatch.setattr(GaiaSPCCInstaller, '__init__', initialize)
    monkeypatch.setattr('lib.gaia_spcc.urlopen', lambda *args, **kwargs: Response(archive))
    return GaiaSPCCInstaller(), data, archive


def test_install_reuse_and_discover_offline(
    tmp_path: Path, sample: Sample, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une base validée est sélectionnable sans mise à jour réseau obligatoire."""
    installer, data, _ = sample
    cache = ResourceCache(tmp_path)
    assert cache.find_spcc() is None
    path = installer.install(cache.gaia_spcc_directory)
    assert (path / installer.files[0]['name']).read_bytes() == data
    assert not list(path.glob('*.part'))

    def forbidden(*args: object, **kwargs: object) -> None:
        """Un cache complet doit fonctionner même avec les serveurs indisponibles."""
        raise AssertionError('Network access despite valid cache')

    monkeypatch.setattr('lib.gaia_spcc.urlopen', forbidden)
    assert installer.install(path) == path
    assert cache.find_spcc() == path
    assert installer.valid_directory(path)
    (path / installer.files[0]['name']).write_bytes(b'x' * len(data))
    assert cache.find_spcc() is None


def test_import_custom_directory_preserves_source_and_pointer(
    tmp_path: Path, sample: Sample, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Les ressources Siril existantes sont importées et la surcharge est retrouvée."""
    installer, data, _ = sample
    source = tmp_path / 'siril'
    source.mkdir()
    original = source / installer.files[0]['name']
    original.write_bytes(data)
    monkeypatch.setattr('lib.gaia_spcc.urlopen', lambda *args, **kwargs: pytest.fail('network'))
    custom = tmp_path / 'custom cache'
    assert installer.install(custom, (source,)) == custom
    assert original.read_bytes() == data
    cache = ResourceCache(tmp_path)
    cache.register_spcc(custom)
    assert cache.find_spcc() == custom
    (cache.gaia_directory / 'spcc-location.json').write_text('invalid')
    assert cache.find_spcc() is None


def test_resume_imported_partial_archive(tmp_path: Path, sample: Sample, monkeypatch: pytest.MonkeyPatch) -> None:
    """Une archive Siril incomplète est copiée puis reprise, sans modifier la source."""
    installer, data, archive = sample
    source = tmp_path / 'siril'
    source.mkdir()
    name = installer.files[0]['name']
    partial = source / (name + '.bz2.part')
    partial.write_bytes(archive[:19])
    requests = []

    def fetch(request: Request, **kwargs: object) -> Response:
        """Fournit exactement le suffixe demandé avec une réponse HTTP 206."""
        requests.append(request.get_header('Range'))
        return Response(archive[19:], 206, f'bytes 19-{len(archive)-1}/{len(archive)}')

    monkeypatch.setattr('lib.gaia_spcc.urlopen', fetch)
    result = installer.install(tmp_path / 'cache', (source,))
    assert requests == ['bytes=19-']
    assert (result / name).read_bytes() == data
    assert partial.read_bytes() == archive[:19]


def test_server_ignoring_range_restarts_cleanly(tmp_path: Path, sample: Sample) -> None:
    """Une réponse 200 remplace le préfixe, elle n'est jamais ajoutée à celui-ci."""
    installer, data, archive = sample
    cache = tmp_path / 'cache'
    cache.mkdir()
    (cache / (installer.files[0]['name'] + '.bz2.part')).write_bytes(archive[:23])
    installer.install(cache)
    assert (cache / installer.files[0]['name']).read_bytes() == data


def test_network_failure_preserves_catalogue_and_partial(
    tmp_path: Path, sample: Sample, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une erreur 502 simulée ne supprime ni la base précédente ni le préfixe reçu."""
    installer, _, archive = sample
    name = installer.files[0]['name']
    destination = tmp_path / name
    destination.write_bytes(b'previous catalogue')
    partial = tmp_path / (name + '.bz2.part')
    partial.write_bytes(archive[:31])

    def unavailable(*args: object, **kwargs: object) -> None:
        """Simule l'indisponibilité complète du serveur."""
        raise URLError('HTTP 502')

    monkeypatch.setattr('lib.gaia_spcc.urlopen', unavailable)
    with pytest.raises(RuntimeError, match='Acquisition SPCC'):
        installer.install(tmp_path)
    assert destination.read_bytes() == b'previous catalogue'
    assert partial.read_bytes() == archive[:31]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([name, name + '.bz2.part'])


def test_bad_http_range_is_rejected_without_appending(tmp_path: Path, sample: Sample, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un suffixe portant le mauvais décalage ne corrompt pas le préfixe valide."""
    installer, _, archive = sample
    path = tmp_path / 'archive.part'
    path.write_bytes(archive[:10])
    monkeypatch.setattr('lib.gaia_spcc.urlopen', lambda *args, **kwargs:
                        Response(archive[20:], 206, f'bytes 20-{len(archive)-1}/{len(archive)}'))
    with pytest.raises(RuntimeError):
        installer.download(path, installer.files[0])
    assert path.read_bytes() == archive[:10]


def test_corrupt_complete_archive_is_reacquired(tmp_path: Path, sample: Sample) -> None:
    """Une taille correcte seule ne rend pas l'archive réutilisable."""
    installer, data, archive = sample
    partial = tmp_path / (installer.files[0]['name'] + '.bz2.part')
    partial.write_bytes(b'x' * len(archive))
    installer.install(tmp_path)
    assert (tmp_path / installer.files[0]['name']).read_bytes() == data


def test_wrong_extracted_digest_preserves_previous_file(tmp_path: Path, sample: Sample) -> None:
    """Le .dat n'est publié qu'après son SHA-256 et les temporaires sont nettoyés."""
    installer, _, _ = sample
    installer.files[0]['sha256'] = '0' * 64
    destination = tmp_path / installer.files[0]['name']
    destination.write_bytes(b'previous')
    with pytest.raises(SPCCInstallationError, match='SHA-256'):
        installer.install(tmp_path)
    assert destination.read_bytes() == b'previous'
    assert len(list(tmp_path.iterdir())) == 2


def test_disk_shortage_is_reported_before_download(tmp_path: Path, sample: Sample, monkeypatch: pytest.MonkeyPatch) -> None:
    """L'installation refuse une capacité insuffisante avant de contacter le serveur."""
    from types import SimpleNamespace

    installer, _, _ = sample
    monkeypatch.setattr('lib.gaia_spcc.shutil.disk_usage', lambda path: SimpleNamespace(free=0))
    monkeypatch.setattr('lib.gaia_spcc.urlopen', lambda *args, **kwargs: pytest.fail('network'))
    with pytest.raises(SPCCInstallationError, match='octets libres'):
        installer.install(tmp_path)


def test_interrupted_transfer_resumes_received_prefix(
    tmp_path: Path, sample: Sample, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une coupure en cours de lecture reprend les octets effectivement écrits."""
    installer, data, archive = sample
    requests = []

    class Interrupted(Response):
        """Livre un préfixe puis simule une coupure du flux HTTP."""

        def read(self, size: int = -1) -> bytes:
            """Échoue après les 17 premiers octets, conservés sur disque."""
            if self.tell() >= 17:
                raise URLError('connection reset')
            return super().read(17)

    def fetch(request: Request, **kwargs: object) -> Response:
        """Livre ensuite le suffixe correspondant exactement au Range demandé."""
        requests.append(request.get_header('Range'))
        if len(requests) == 1:
            return Interrupted(archive)
        return Response(archive[17:], 206, f'bytes 17-{len(archive)-1}/{len(archive)}')

    monkeypatch.setattr('lib.gaia_spcc.urlopen', fetch)
    installer.install(tmp_path)
    assert requests == [None, 'bytes=17-']
    assert (tmp_path / installer.files[0]['name']).read_bytes() == data


def test_incomplete_set_is_not_a_complete_catalogue(
    tmp_path: Path, sample: Sample, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une panne sur une région ultérieure conserve les régions déjà publiées."""
    installer, data, archive = sample
    first = installer.files[0]
    second = dict(first, name='siril_cat1_healpix8_xpsamp_1.dat')
    installer.files.append(second)

    def fetch(request: Request, **kwargs: object) -> Response:
        """Permet la première région, puis simule un serveur indisponible."""
        if second['name'] in request.full_url:
            raise URLError('HTTP 502')
        return Response(archive)

    monkeypatch.setattr('lib.gaia_spcc.urlopen', fetch)
    with pytest.raises(RuntimeError):
        installer.install(tmp_path)
    assert (tmp_path / first['name']).read_bytes() == data
    assert not installer.valid_directory(tmp_path)


def test_official_manifest_covers_all_regions() -> None:
    """Le manifeste distribué couvre exactement les 48 HEALpixels du ciel."""
    installer = GaiaSPCCInstaller()
    assert {item['name'] for item in installer.files} == {
        f'siril_cat1_healpix8_xpsamp_{index}.dat' for index in range(48)
    }
    assert sum(item['size'] for item in installer.files) == 20867715872
    assert sum(item['archive_size'] for item in installer.files) == 10551868571


def test_regional_sets_cover_both_hemispheres_with_equatorial_overlap() -> None:
    """Les faces NESTED équatoriales 1 et 2 appartiennent aux deux hémisphères."""
    installer = GaiaSPCCInstaller()
    selected = {}
    for coverage in ('north', 'south', 'remaining', 'all'):
        installer.coverage = coverage
        selected[coverage] = {item['name'] for item in installer.selected_files()}
    assert len(selected['north']) == len(selected['south']) == 28
    assert len(selected['remaining']) == 20
    assert len(selected['north'] & selected['south']) == 8
    assert selected['north'] | selected['south'] == selected['all']
    assert selected['north'].isdisjoint(selected['remaining'])
    assert selected['north'] | selected['remaining'] == selected['all']
    assert 'siril_cat1_healpix8_xpsamp_5.dat' in selected['north']
    assert 'siril_cat1_healpix8_xpsamp_17.dat' in selected['north'] & selected['south']
    assert 'siril_cat1_healpix8_xpsamp_16.dat' in selected['remaining']
    installer.coverage = 'invalid'
    with pytest.raises(ValueError, match='Couverture'):
        installer.selected_files()


def test_failed_region_does_not_block_later_regions(tmp_path: Path, sample: Sample, monkeypatch: pytest.MonkeyPatch) -> None:
    """Une erreur de taille sur la première région n'empêche pas de publier les suivantes."""
    installer, data, archive = sample
    first = installer.files[0]
    # L'archive est valide, mais le premier manifeste de test sous-estime la taille.
    first['size'] = 10
    second = dict(first, name='siril_cat1_healpix8_xpsamp_1.dat', size=len(data))
    installer.files.append(second)
    monkeypatch.setattr('lib.gaia_spcc.urlopen', lambda *args, **kwargs: Response(archive))
    with pytest.raises(SPCCInstallationError) as raised:
        installer.install(tmp_path)
    assert set(raised.value.failures) == {first['name']}
    assert raised.value.completed == (second['name'],)
    assert (tmp_path / second['name']).read_bytes() == data
    assert not (tmp_path / first['name']).exists()
    assert (tmp_path / (first['name'] + '.bz2.part')).is_file()
    first['size'] = len(data)
    monkeypatch.setattr('lib.gaia_spcc.urlopen', lambda *args, **kwargs: pytest.fail('network'))
    assert installer.install(tmp_path) == tmp_path
    assert installer.valid_directory(tmp_path)


def test_northern_cache_is_discovered_without_southern_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Une couverture nord complète est utilisable ; une région nord altérée l'invalide."""
    original = GaiaSPCCInstaller().files
    payload = b'synthetic regional data'
    files = [dict(item, size=len(payload), sha256=hashlib.sha256(payload).hexdigest())
             for item in original]

    def initialize(self: GaiaSPCCInstaller) -> None:
        """Conserve les 48 identifiants, avec des données légères aux vrais contrôles."""
        self.files = files

    monkeypatch.setattr(GaiaSPCCInstaller, '__init__', initialize)
    cache = ResourceCache(tmp_path)
    directory = cache.gaia_spcc_directory
    directory.mkdir(parents=True)
    installer = GaiaSPCCInstaller()
    installer.coverage = 'north'
    for item in installer.selected_files():
        (directory / item['name']).write_bytes(payload)
    cache.register_spcc(directory, 'north')
    assert cache.find_spcc() == directory
    from lib.resource_manager import ResourceManager
    assert ResourceManager(root=tmp_path).gaia_spcc() == directory
    installer.coverage = 'all'
    assert not installer.valid_directory(directory)
    # Même si toutes les régions sud sont aussi présentes, ne pas masquer
    # une corruption dans la sélection nord déclarée en validant le sud seul.
    for item in files:
        (directory / item['name']).write_bytes(payload)
    (directory / files[0]['name']).write_bytes(b'corrupt')
    assert cache.find_spcc() is None
