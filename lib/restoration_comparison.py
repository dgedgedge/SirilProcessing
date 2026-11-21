"""Compare les rapports de restauration sans charger un rapport entier.

Le lecteur JSON accepte l'indentation libre et décode un objet de variante ou
une opération à la fois. Le pic mémoire dépend donc du plus gros objet, pas
seulement de la synthèse produite. Voir docs/scripts/compareRestoration/README.md.
Ce module d'analyse utilise uniquement la bibliothèque standard et ne calcule
aucun nouveau verdict scientifique.
"""
from __future__ import annotations

import csv
import html
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, TextIO


READ_CHUNK_CHARACTERS = 1024 * 1024
MAX_READ_CHUNK_CHARACTERS = 16 * READ_CHUNK_CHARACTERS


class ReportReader:
    """Décode progressivement les champs de racine et les listes d'essais.

    Les fichiers tronqués ou modifiés pendant la lecture sont refusés. Les
    grandes listes ``variants`` et ``operations`` sont parcourues par objet ;
    les autres champs de racine sont décodés individuellement.
    """

    def __init__(self, stream: TextIO) -> None:
        """Associe un flux texte UTF-8, dont l'appelant garde la propriété."""
        self.stream = stream
        self.buffer = ''
        self.position = 0
        self.decoder = json.JSONDecoder()

    def _fill(self, size: int = READ_CHUNK_CHARACTERS) -> bool:
        """Compacte le tampon et lit au plus ``size`` caractères supplémentaires."""
        self.buffer = self.buffer[self.position:]
        self.position = 0
        chunk = self.stream.read(size)
        self.buffer += chunk
        return bool(chunk)

    def _peek(self) -> str:
        """Retourne le prochain caractère JSON non blanc, ou la chaîne vide."""
        while True:
            while (self.position < len(self.buffer)
                   and self.buffer[self.position].isspace()):
                self.position += 1
            if self.position < len(self.buffer):
                return self.buffer[self.position]
            if not self._fill():
                return ''

    def _expect(self, character: str) -> None:
        """Consomme un séparateur obligatoire et refuse une structure invalide."""
        if self._peek() != character:
            raise ValueError(f'Séparateur JSON attendu : {character!r}')
        self.position += 1

    def _value(self) -> object:
        """Décode une valeur complète ; étend le tampon si elle est incomplète."""
        if not self._peek():
            raise ValueError('Rapport JSON tronqué')
        self.buffer = self.buffer[self.position:]
        self.position = 0
        size = READ_CHUNK_CHARACTERS
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer, self.position)
                # Un nombre coupé à la frontière du tampon doit être relu.
                if end == len(self.buffer) or self.buffer[end] not in ' \t\r\n,:]}':
                    if self._fill(size):
                        continue
                    if end != len(self.buffer):
                        raise ValueError('Valeur JSON invalide')
                self.position = end
                return value
            except json.JSONDecodeError as error:
                if not self._fill(size):
                    raise ValueError(f'Rapport JSON invalide ou tronqué : {error}') from error
                size = min(size * 2, MAX_READ_CHUNK_CHARACTERS)

    def fields(self) -> Iterator[tuple[str, object]]:
        """Émet les objets des listes d'essais et les autres champs de racine.

        Une clé de racine répétée, une entrée non objet, une virgule finale ou
        des données après la racine sont des erreurs, jamais des essais ignorés.
        """
        self._expect('{')
        seen: set[str] = set()
        first = True
        while self._peek() != '}':
            if not first:
                self._expect(',')
            key = self._value()
            if not isinstance(key, str) or key in seen:
                raise ValueError('Clé de racine JSON invalide ou répétée')
            seen.add(key)
            self._expect(':')
            if key in ('variants', 'operations'):
                self._expect('[')
                item_first = True
                while self._peek() != ']':
                    if not item_first:
                        self._expect(',')
                    item = self._value()
                    if not isinstance(item, dict):
                        raise ValueError(f'Entrée non objet dans {key}')
                    yield key, item
                    item_first = False
                self._expect(']')
            else:
                yield key, self._value()
            first = False
        self._expect('}')
        if self._peek():
            raise ValueError('Données après la racine JSON')


def mapping(value: object) -> dict[str, object]:
    """Retourne un dictionnaire lisible, sans inventer de mesures absentes."""
    return value if isinstance(value, dict) else {}


def compact_criteria(value: object) -> dict[str, dict[str, object]]:
    """Garde les verdicts et leurs paramètres scalaires sans populations brutes."""
    result = {}
    for name, raw in mapping(value).items():
        if not isinstance(raw, dict):
            continue
        result[str(name)] = {
            key: item for key, item in raw.items()
            if (item is None or isinstance(item, (str, bool, int, float)))
        }
        for key in ('interval', 'confidence_interval'):
            item = raw.get(key)
            if isinstance(item, (list, tuple)) and len(item) == 2:
                result[str(name)][key] = item
    return result


def summarize(entry: dict[str, object], report: Path, scope: str) -> dict[str, object]:
    """Extrait mesures enregistrées, critères et provenance d'un seul essai.

    Les ratios FWHM et bruit sont après/avant ; les gains sont des fractions.
    Aucun contrôle local ne remplit un contrôle final manquant. Les rapports
    anciens sans décomposition du score gardent leurs cellules vides.
    """
    if not isinstance(entry.get('steps', []), list):
        raise ValueError('Le champ steps doit être une liste')
    if entry.get('chain') is not None and not isinstance(entry['chain'], list):
        raise ValueError('Le champ chain doit être une liste')
    comparison = mapping(entry.get('comparison'))
    artifacts = mapping(entry.get('artifacts'))
    details = mapping(entry.get('score_details'))
    quality = mapping(entry.get('quality'))
    criteria = entry.get('criteria', quality.get('criteria'))
    return {
        'rapport': str(report),
        'audit': entry.get('audit_path', entry.get('report_path')),
        'objet': (report.parent.parent.name
                  if report.parent.name == '04_restoration' else report.parent.name),
        'portee': scope, 'numero': entry.get('number', entry.get('operation_id')),
        'chaine': entry.get('chain'), 'statut': entry.get('status'),
        'retenu': False, 'score': entry.get('selection_score'),
        'ratio_fwhm_moyenne': details.get('fwhm_mean_ratio'),
        'ratio_fwhm_mediane': details.get(
            'fwhm_median_ratio', mapping(comparison.get('paired_ratio')).get('median')),
        'gain_bruit_utile': details.get(
            'useful_noise_gain', mapping(artifacts.get('noise')).get('proven_gain')),
        'ratio_bruit_max': artifacts.get('max_noise_ratio'),
        'fraction_anneaux_max': artifacts.get('max_ring_fraction'),
        'duree_s': entry.get('duration_seconds'),
        'etape_rejet': entry.get('rejection_stage'),
        'motifs': (entry.get('rejection_reasons') or entry.get('reason')
                   or entry.get('error') or quality.get('reasons')),
        'entree': entry.get('input_image'), 'sortie': entry.get('output_image'),
        'criteria': compact_criteria(criteria),
        'operation_ids': [step.get('operation_id') for step in entry.get('steps', [])
                          if isinstance(step, dict)],
        'operation_id': entry.get('operation_id'),
        'moteur': entry.get('engine_name', entry.get('engine')),
        'operation': entry.get('operation'),
    }


def read_report(path: Path) -> list[dict[str, object]]:
    """Lit un rapport de chaînes stable et restitue variantes et opérations.

    Refuse les formats par étape sans liste de variantes. Une production en
    cours peut être lue entre deux écritures, mais ne constitue pas un bilan
    complet ; la date et le statut enregistrés restent visibles dans l'export.
    """
    before = path.stat()
    rows = []
    metadata = {}
    with path.open(encoding='utf-8-sig') as stream:
        for key, value in ReportReader(stream).fields():
            if key in ('variants', 'operations'):
                rows.append(summarize(value, path, 'finale' if key == 'variants' else 'locale'))
            elif key in ('selected_chain', 'status', 'image_path', 'early_stop'):
                metadata[key] = value
    after = path.stat()
    if (before.st_ino, before.st_mtime_ns, before.st_size) != (
        after.st_ino, after.st_mtime_ns, after.st_size
    ):
        raise ValueError(f'Rapport modifié pendant la lecture : {path}')
    if not any(row['portee'] == 'finale' for row in rows):
        raise ValueError(f'Aucune variante de restauration dans {path}')
    prefixes: dict[object, list] = {}
    for row in rows:
        if row['portee'] != 'finale':
            continue
        chain = row['chaine']
        if isinstance(chain, list):
            row['retenu'] = chain == metadata.get('selected_chain')
            for index, identifier in enumerate(row['operation_ids']):
                prefixes.setdefault(identifier, chain[:index + 1])
        row['entree'] = metadata.get('image_path')
    for row in rows:
        if row['portee'] == 'locale':
            row['chaine'] = prefixes.get(row['operation_id'], [
                f"{row['operation']}:{row['moteur']}"
            ])
        row['early_stop'] = metadata.get('early_stop')
        row['statut_rapport'] = metadata.get('status')
        row['date_rapport'] = datetime.fromtimestamp(
            after.st_mtime, tz=timezone.utc).isoformat()
    return rows


def discover(inputs: list[Path], recursive: bool = False) -> list[Path]:
    """Résout fichiers, dossiers de restauration, objets ou collections d'objets.

    Sans récursion, accepte ``restoration.json``, ``04_restoration/restoration.json``
    et les mêmes chemins dans les enfants immédiats. Refuse une entrée vide de
    rapports ; dédoublonne les chemins résolus et conserve l'ordre des entrées.
    """
    paths = []
    for source in inputs:
        source = source.resolve()
        if source.is_file():
            found = [source]
        elif source.is_dir():
            if recursive:
                found = sorted(source.rglob('restoration.json'))
            else:
                candidates = [source / 'restoration.json',
                              source / '04_restoration/restoration.json']
                candidates.extend(sorted(source.glob('*/restoration.json')))
                candidates.extend(sorted(source.glob('*/04_restoration/restoration.json')))
                found = [path for path in candidates if path.is_file()]
        else:
            raise FileNotFoundError(f'Entrée introuvable : {source}')
        if not found:
            raise ValueError(f'Aucun restoration.json dans {source}')
        paths.extend(path.resolve() for path in found)
    return list(dict.fromkeys(paths))


COLUMNS = (
    'objet', 'portee', 'numero', 'chaine', 'statut', 'retenu', 'score',
    'ratio_fwhm_moyenne', 'ratio_fwhm_mediane', 'gain_bruit_utile',
    'ratio_bruit_max', 'fraction_anneaux_max', 'duree_s', 'etape_rejet',
    'motifs', 'statut_rapport', 'date_rapport',
)


def display(value: object) -> str:
    """Affiche les absences par une cellule vide, en conservant zéro et False."""
    if value is None:
        return ''
    if isinstance(value, float):
        return format(value, '.7g') if math.isfinite(value) else str(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    return str(value)


def export(rows: list[dict[str, object]], output: Path) -> tuple[Path, Path]:
    """Écrit un HTML autonome filtrable et un CSV UTF-8 à séparateur point-virgule.

    ``output`` est un préfixe de fichiers ; les suffixes HTML/CSV sont ajoutés.
    Le HTML regroupe les critères dans une colonne, une ligne par critère,
    y compris leurs drapeaux bloquants. Les champs sans valeur sont masqués ;
    zéro et False restent visibles. Le CSV garde ses colonnes analytiques.
    Le CSV conserve la précision numérique native ; les formules de tableur
    provenant des champs texte sont neutralisées par une apostrophe initiale.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    html_path = Path(str(output) + '.html')
    csv_path = Path(str(output) + '.csv')
    names = sorted({name for row in rows for name in row['criteria']})
    criterion_fields = {
        name: sorted({key for row in rows for key in row['criteria'].get(name, {})})
        for name in names
    }
    fields = list(COLUMNS) + [f'{name}.{key}' for name in names
                              for key in criterion_fields[name]]
    with csv_path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter=';')
        writer.writeheader()
        for row in rows:
            flat = {key: row.get(key) for key in COLUMNS}
            flat.update({f'{name}.{key}': value for name, criterion in row['criteria'].items()
                         for key, value in criterion.items()})
            for key, value in flat.items():
                if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                    value = "'" + value
                flat[key] = (value if isinstance(value, (float, int))
                             and not isinstance(value, bool) else display(value))
            writer.writerow(flat)
    early_notices = {}
    for row in rows:
        early = mapping(row.get('early_stop'))
        if early.get('triggered'):
            early_notices[row['rapport']] = (
                '<p><strong>' + html.escape(display(row['objet']))
                + ' : arrêt anticipé.</strong> '
                + html.escape(display(early.get('evaluated_variant_count')))
                + ' variantes évaluées sur '
                + html.escape(display(early.get('planned_variant_count')))
                + ' ; les autres variantes n’ont pas été exécutées.</p>'
            )
    parts = ['<!doctype html><html lang="fr"><meta charset="utf-8">',
             '<title>Comparatif des restaurations</title><style>',
             'body{font:14px system-ui;margin:20px}table{border-collapse:collapse}',
             'td,th{border:1px solid #ccc;padding:6px;vertical-align:top}',
             'th{position:sticky;top:0;background:#eee;cursor:pointer}',
             'tr.selected{background:#e4f4e8}.scroll{overflow:auto;max-height:75vh}',
             'td{max-width:400px;overflow-wrap:anywhere}small{white-space:pre-line}',
             '.quality-criteria{min-width:420px;max-width:640px}',
             '.criterion{padding:4px 0;border-bottom:1px solid #ddd}',
             '.criterion:last-child{border-bottom:0}',
             '.verdict-pass{color:#176b35}.verdict-fail{color:#a42323}',
             '.verdict-unknown{color:#795500}',
             '</style><h1>Comparatif des restaurations</h1>',
             '<p>Contrôles finaux : comparaison à l’entrée de restauration. ',
             'Contrôles locaux : comparaison à l’entrée de chaque opération. ',
             'Ratios = après / avant ; gains et fractions : 0,02 = 2 %. ',
             'Cellule vide = mesure absente. FAIL peut être non bloquant ; ',
             'UNKNOWN ne prouve pas la préservation. Le score ne se compare ',
             'pas directement entre images. Rapports en cours : bilan provisoire.</p>',
             *early_notices.values(),
             '<label>Filtrer <input id="filter" type="search"></label> ',
             '<label>Portée <select id="scope"><option value="">Toutes</option>',
             '<option>finale</option><option>locale</option></select></label> ',
             '<span id="count"></span><div class="scroll"><table><thead><tr>']
    for name in (*COLUMNS, 'Critères de qualité'):
        parts.append('<th>' + html.escape(name) + '</th>')
    parts.append('</tr></thead><tbody>')
    for row in rows:
        css = 'selected' if row['retenu'] else ''
        parts.append(f'<tr class="{css}" data-scope="{row["portee"]}">')
        for name in COLUMNS:
            value = row.get(name)
            if name == 'chaine' and isinstance(value, list):
                value = ' → '.join(map(str, value)) or 'Original'
            parts.append('<td>' + html.escape(display(value)) + '</td>')
        parts.append('<td class="quality-criteria">')
        for name in names:
            criterion = row['criteria'].get(name)
            if not criterion:
                continue
            status = display(criterion.get('status'))
            verdict_class = ('verdict-' + status.lower()
                             if status in ('PASS', 'FAIL', 'UNKNOWN') else '')
            text = '; '.join(f'{key} : {display(value)}'
                             for key, value in criterion.items()
                             if key != 'status' and value is not None and value != '')
            if not status and not text:
                continue
            parts.append('<div class="criterion"><strong>' + html.escape(name)
                         + ' : </strong><strong class="' + verdict_class + '">'
                         + html.escape(status) + '</strong> <small>'
                         + html.escape(text) + '</small></div>')
        parts.append('</td>')
        parts.append('</tr>')
    parts.append('''</tbody></table></div><script>
const table=document.querySelector('table'), body=table.tBodies[0];
const search=document.getElementById('filter'), scope=document.getElementById('scope');
function filterRows(){let n=0;for(const row of body.rows){
row.hidden=!(row.textContent.toLowerCase().includes(search.value.toLowerCase()) &&
(!scope.value || row.dataset.scope===scope.value));if(!row.hidden)n++;}
document.getElementById('count').textContent=n+' lignes visibles';}
search.addEventListener('input',filterRows);scope.addEventListener('change',filterRows);
for(const [i,header] of [...table.tHead.rows[0].cells].entries()){
let ascending=true;header.addEventListener('click',()=>{
const value=row=>row.cells[i].textContent.trim();
const compare=(a,b)=>{const x=value(a),y=value(b);
if(!x || !y)return x? -1:y?1:0;
const d=Number.isFinite(Number(x)) && Number.isFinite(Number(y))?
Number(x)-Number(y):x.localeCompare(y,'fr',{numeric:true});
return ascending?d:-d;};
[...body.rows].sort(compare).forEach(row=>body.appendChild(row));ascending=!ascending;});}
filterRows();</script></html>''')
    html_path.write_text(''.join(parts), encoding='utf-8')
    return html_path, csv_path
