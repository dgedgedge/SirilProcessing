"""Comptes de poses/panneaux issus des rapports associés à l'image scientifique.

Les comptes décrivent les sources indépendantes proposées au moteur de stacking,
jamais les entrées répétées par pondération. Voir postProcess/annotations.md.
"""

from __future__ import annotations

import json
from pathlib import Path

from lib.output_layout import OutputLayout
from lib.type_defs import JSONReport

MAX_REPORT_BYTES = 32 * 1024 * 1024  # Éviter les volumineux audits de restauration.


class StackingAnnotation:
    """Résout les rapports explicites d'un stack ou d'une mosaïque, sans les modifier."""

    @staticmethod
    def _read(path: Path, source: Path, output_key: str) -> JSONReport:
        """Refuse un rapport étranger ou antérieur à son image source existante."""
        if path.stat().st_size > MAX_REPORT_BYTES:
            raise ValueError(f'Rapport de provenance trop volumineux : {path}')
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or not isinstance(data.get(output_key), str):
            raise ValueError(f'Rapport de provenance invalide : {path}')
        output = Path(data[output_key])
        output = (output if output.is_absolute() else path.parent / output).resolve()
        if output != source.resolve():
            raise ValueError(f'Rapport associé à une autre image : {path}')
        if source.exists() and source.stat().st_mtime_ns > path.stat().st_mtime_ns:
            raise ValueError(f'Image modifiée après le rapport de stacking : {source}')
        return data

    @classmethod
    def _stack(cls, source: Path, latest_ns: int | None = None) -> JSONReport:
        """Lit retenues/total pour un stack terminé, ou conserve un compte inconnu."""
        report = source.with_suffix('.drizzle.json')
        result: JSONReport = {'source': str(source), 'report_path': str(report),
                              'retained': None, 'total': None, 'status': 'UNAVAILABLE'}
        if not report.is_file():
            return result
        try:
            if latest_ns is not None and report.stat().st_mtime_ns > latest_ns:
                raise ValueError('Le rapport du panneau est postérieur à la mosaïque')
            data = cls._read(report, source, 'output')
            if data.get('status') != 'completed':
                raise ValueError('Le rapport ne décrit pas un empilement terminé')
            total = data.get('metadata', {}).get('unique_inputs')
            retained = data.get('final_selection', {}).get('independent_retained')
            if (type(total) is not int or type(retained) is not int
                    or not 0 < retained <= total):
                raise ValueError('Comptes de poses indépendantes invalides ou absents')
            result.update(status='AVAILABLE', retained=retained, total=total)
        except (OSError, ValueError, TypeError, AttributeError) as error:
            result.update(status='INVALID', reason=str(error))
        return result

    @classmethod
    def describe(cls, source: Path) -> JSONReport:
        """Prépare les lignes d'encart depuis l'image d'origine du post-traitement.

        Parameters
        ----------
        source : Path
            Stack ou mosaïque scientifique, avant restauration/rendu. Les seuls
            rapports consultés sont ses voisins .drizzle.json ou .json et les
            rapports des panneaux explicitement référencés par la mosaïque.

        Returns
        -------
        JSONReport
            Lignes françaises, statut et provenance des comptes. Un panneau
            sans compte fiable reste « inconnu », jamais zéro. Les absences et
            incohérences de métadonnées n'empêchent pas l'annotation du ciel.
        """
        source = source.resolve()
        stack = cls._stack(source)
        if stack['status'] == 'AVAILABLE':
            return {'status': 'AVAILABLE', 'kind': 'stack', 'panels': [stack],
                    'lines': [
                        f"Images empilées (retenues/total) : {stack['retained']}/{stack['total']}"
                    ]}
        layout = OutputLayout.for_source(source)
        report = layout.mosaic / 'mosaic.json' if layout else source.with_suffix('.json')
        result: JSONReport = {'status': stack['status'], 'kind': 'unknown',
                              'lines': [], 'panels': [], 'source': str(source)}
        if not report.is_file():
            if stack.get('reason'):
                result['reason'] = stack['reason']
            return result
        try:
            data = cls._read(report, source, 'output_image')
            if data.get('assembled') is False:
                original = data.get('image_path')
                if not isinstance(original, str):
                    raise ValueError('Référence du stack calibré absente du relais')
                path = Path(original)
                path = (path if path.is_absolute() else report.parent / path).resolve()
                if path == source:
                    raise ValueError('Relais de stacking cyclique')
                count = cls._stack(path)
                lines = ([f"Images empilées (retenues/total) : {count['retained']}/{count['total']}"]
                         if count['status'] == 'AVAILABLE' else [])
                return dict(status=count['status'], kind='stack', panels=[count], lines=lines,
                            relay_source=str(source), relay_report=str(report))
            paths = data.get('input_files')
            if (not isinstance(paths, list) or len(paths) < 2
                    or not all(isinstance(p, str) and p for p in paths)):
                raise ValueError('Liste des panneaux absente ou invalide')
            panels = [(Path(p) if Path(p).is_absolute() else report.parent / p).resolve()
                      for p in paths]
            if len(set(panels)) != len(panels) or source in panels:
                raise ValueError('Panneaux de mosaïque répétés ou cycliques')
            counts = [cls._stack(p, report.stat().st_mtime_ns) for p in panels]
            lines = [f'Mosaïque : {len(panels)} panneaux',
                     'Images empilées par panneau (retenues/total) :']
            for index, (panel, count) in enumerate(zip(panels, counts), 1):
                ratio = (f"{count['retained']}/{count['total']}"
                         if count['status'] == 'AVAILABLE' else 'inconnu')
                lines.append(f'P{index} · {panel.parent.name} : {ratio}')
            result.update(
                status='AVAILABLE' if all(c['status'] == 'AVAILABLE' for c in counts)
                else 'PARTIAL', kind='mosaic', panel_count=len(panels),
                report_path=str(report), panels=counts, lines=lines,
            )
        except (OSError, ValueError, TypeError, AttributeError) as error:
            result.update(status='INVALID', reason=str(error))
        return result
