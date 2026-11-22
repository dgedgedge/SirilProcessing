"""Contrôles du lecteur progressif et des comparatifs sans dépendance à Siril."""
import csv
import importlib.util
import io
import json
from pathlib import Path

import pytest

from lib.restoration_comparison import ReportReader, discover, export, read_report


class SmallChunks(io.StringIO):
    """Simule les frontières de blocs, même au milieu d'une chaîne ou d'un nombre."""

    def read(self, size: int = -1) -> str:
        """Renvoie au plus trois caractères par lecture."""
        return super().read(min(size, 3) if size >= 0 else 3)


@pytest.fixture
def report(tmp_path: Path) -> Path:
    """Crée deux variantes et une opération dont l'alerte reste non bloquante."""
    path = tmp_path / 'Objet' / '04_restoration' / 'restoration.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        'engine': 'chains', 'status': 'accepted', 'selected_chain': ['denoise:graxpert'],
        'image_path': '/origine.fits',
        'variants': [
            {'number': 1, 'chain': [], 'status': 'reference', 'selection_score': 0},
            {'number': 2, 'chain': ['denoise:graxpert'], 'status': 'accepted',
             'selection_score': .4, 'steps': [{'operation_id': 1}],
             'artifacts': {'noise': {'proven_gain': .4}, 'max_noise_ratio': .6},
             'criteria': {'diffuse': {'status': 'FAIL', 'blocking': False,
                                      'value': 0, 'threshold': .1, 'raw': [1, 2, 3]}}},
        ],
        'operations': [{'operation_id': 1, 'operation': 'denoise',
                        'engine_name': 'graxpert', 'status': 'accepted',
                        'input_image': '/origine.fits', 'output_image': '/candidate.fits',
                        'comparison': {'paired_ratio': {'median': 1.01}},
                        'quality': {'criteria': {'blur': {'status': 'PASS', 'value': .01}}}}],
    }), encoding='utf-8')
    return path


def test_streaming_arbitrary_chunks() -> None:
    """Le découpage du flux ne change pas les nombres, Unicode ou échappements."""
    payload = {'image': 'étoile "a" \\ b', 'n': 1.234e-12,
               'variants': [{'chain': [], 'value': -1234.567}], 'operations': []}
    fields = list(ReportReader(SmallChunks(json.dumps(payload))).fields())
    assert fields == [('image', payload['image']), ('n', payload['n']),
                      ('variants', payload['variants'][0])]


@pytest.mark.parametrize('text', [
    '{', '{"variants":[{}', '{"variants":[{},]}', '{"variants":[3]}',
    '{"variants":[]},', '{"a":1,"a":2}', '{"a":1,}', '{"a":1e}',
])
def test_invalid_json(text: str) -> None:
    """Les données incomplètes ou ambiguës sont rejetées explicitement."""
    with pytest.raises(ValueError):
        list(ReportReader(SmallChunks(text)).fields())


def test_scope_selection_and_missing_values(report: Path) -> None:
    """Aucun critère local ni aucune valeur absente ne devient un verdict final."""
    rows = read_report(report)
    assert len(rows) == 3
    assert rows[0]['score'] == 0
    assert rows[0]['gain_bruit_utile'] is None
    assert rows[1]['retenu'] is True
    assert rows[1]['criteria']['diffuse']['blocking'] is False
    assert 'raw' not in rows[1]['criteria']['diffuse']
    assert 'blur' not in rows[1]['criteria']
    assert rows[2]['chaine'] == ['denoise:graxpert']
    assert rows[2]['portee'] == 'locale'
    assert rows[2]['ratio_fwhm_mediane'] == 1.01
    assert rows[2]['entree'] == '/origine.fits'


def test_discovery_and_deduplication(report: Path) -> None:
    """Les chemins fichier, restauration, objet et collection se résolvent ensemble."""
    assert discover([report, report.parent, report.parent.parent,
                     report.parent.parent.parent]) == [report]
    assert discover([report.parent.parent.parent], recursive=True) == [report]
    with pytest.raises(FileNotFoundError):
        discover([report.parent / 'absent'])
    empty = report.parent / 'empty'
    empty.mkdir()
    with pytest.raises(ValueError, match='Aucun'):
        discover([empty])


def test_exports_escape_and_preserve_values(report: Path, tmp_path: Path) -> None:
    """HTML échappé, CSV précis, alertes non bloquantes et absences sont conservés."""
    rows = read_report(report)
    rows[1]['motifs'] = '<script>alert(1)</script>'
    rows[1]['objet'] = '=1+1'
    rows[1]['score'] = .12345678912345678
    rows[1]['criteria']['diffuse']['uncertainty'] = None
    rows[1]['criteria']['diffuse']['reason'] = ''
    rows[1]['criteria']['empty'] = {'status': None, 'value': None}
    html_path, csv_path = export(rows, tmp_path / 'comparatif')
    page = html_path.read_text()
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in page
    assert '<script>alert(1)</script>' not in page
    assert 'data-scope="locale"' in page
    assert 'blocking : False' in page
    assert page.count('<th>Critères de qualité</th>') == 1
    assert '<th>diffuse</th>' not in page
    assert '<th>blur</th>' not in page
    assert '<div class="criterion"><strong>diffuse : </strong>' in page
    assert '<div class="criterion"><strong>blur : </strong>' in page
    assert 'class="verdict-fail">FAIL</strong>' in page
    assert 'raw :' not in page
    assert 'uncertainty :' not in page
    assert 'reason :' not in page
    assert '<strong>empty : </strong>' not in page
    assert 'value : 0;' in page
    with csv_path.open(encoding='utf-8-sig', newline='') as stream:
        records = list(csv.DictReader(stream, delimiter=';'))
    for key in ('entree', 'sortie', 'rapport', 'audit'):
        assert key not in records[0]
        assert '<th>' + key + '</th>' not in page
    assert '/origine.fits' not in page
    assert records[0]['gain_bruit_utile'] == ''
    assert records[0]['score'] == '0'
    assert records[1]['objet'] == "'=1+1"
    assert records[1]['score'] == str(rows[1]['score'])
    assert records[1]['diffuse.value'] == '0'
    assert records[1]['diffuse.blocking'] == 'False'


def test_non_chain_report_refused(tmp_path: Path) -> None:
    """Un ancien rapport par moteur n'est pas présenté comme une comparaison complète."""
    path = tmp_path / 'restoration.json'
    path.write_text('{"engine":"siril"}')
    with pytest.raises(ValueError, match='Aucune variante'):
        read_report(path)


def test_changed_report_refused(report: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Une écriture concurrente empêche de publier une synthèse incohérente."""
    original = ReportReader.fields

    def modified(reader: ReportReader):
        """Modifie le rapport après avoir émis ses champs."""
        yield from original(reader)
        with report.open('a') as stream:
            stream.write(' ')

    monkeypatch.setattr(ReportReader, 'fields', modified)
    with pytest.raises(ValueError, match='modifié'):
        read_report(report)


def test_cli_partial_failure(report: Path, tmp_path: Path) -> None:
    """Un export partiel existe mais retourne un échec si une entrée est absente."""
    script = Path(__file__).parents[1] / 'bin/compareRestoration.py'
    spec = importlib.util.spec_from_file_location('compare_cli', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main([str(report), '-o', str(tmp_path / 'ok')]) == 0
    assert module.main([str(report), str(tmp_path / 'absent'),
                        '-o', str(tmp_path / 'partial')]) == 1
    assert (tmp_path / 'partial.html').is_file()


def test_early_stop_notice_without_fake_rejections(report: Path, tmp_path: Path) -> None:
    """Le tableau annonce les variantes non exécutées sans inventer leurs lignes."""
    payload = json.loads(report.read_text())
    payload['early_stop'] = dict(triggered=True, evaluated_variant_count=2,
                                 planned_variant_count=16, skipped_variant_count=14)
    report.write_text(json.dumps(payload))
    rows = read_report(report)
    page, _ = export(rows, tmp_path / 'early')
    text = page.read_text()
    assert text.count(' : arrêt anticipé.') == 1
    assert '2 variantes évaluées sur 16' in text
    assert 'les autres variantes n’ont pas été exécutées' in text
    assert len(rows) == 3
