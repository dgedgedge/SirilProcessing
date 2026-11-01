"""Comptes indépendants et provenance de l'encart d'empilement/mosaïque."""

import json
import os
from pathlib import Path

import pytest

from lib.stacking_annotation import StackingAnnotation


def stack(tmp_path: Path, name: str, retained: int = 12, total: int = 20) -> Path:
    """Écrit une image symbolique et un rapport terminé avec pondérations."""
    source = tmp_path / name / f'{name}_combined.fits'
    source.parent.mkdir(parents=True)
    source.touch()
    source.with_suffix('.drizzle.json').write_text(json.dumps({
        'status': 'completed', 'output': str(source),
        'metadata': {'unique_inputs': total, 'effective_inputs': total},
        'final_selection': {'independent_retained': retained,
                            'effective_stack_entries': retained * 3},
    }))
    return source


def mosaic(tmp_path: Path, panels: list[Path]) -> Path:
    """Référence les panneaux dans leur ordre de construction."""
    source = tmp_path / 'target_mosaic.fits'
    source.touch()
    source.with_suffix('.json').write_text(json.dumps({
        'output_image': str(source), 'input_files': [str(p) for p in panels],
    }))
    return source


def test_independent_frames_are_used_instead_of_weights(tmp_path: Path) -> None:
    """Afficher 12/20 et non les 36 entrées répétées de pondération."""
    source = stack(tmp_path, 'session')
    report = source.with_suffix('.drizzle.json')
    original = report.read_bytes()
    result = StackingAnnotation.describe(source)
    assert result['lines'] == ['Images empilées (retenues/total) : 12/20']
    assert result['panels'][0]['retained'] == 12
    assert report.read_bytes() == original


def test_mosaic_counts_each_panel_and_keeps_order(tmp_path: Path) -> None:
    """Conserver les deux rapports distincts sans inventer un cumul d'intégration."""
    first, second = stack(tmp_path, 'west', 12, 20), stack(tmp_path, 'east', 8, 15)
    result = StackingAnnotation.describe(mosaic(tmp_path, [second, first]))
    assert result['status'] == 'AVAILABLE'
    assert result['panel_count'] == 2
    assert result['lines'] == [
        'Mosaïque : 2 panneaux', 'Images empilées par panneau (retenues/total) :',
        'P1 · east : 8/15', 'P2 · west : 12/20',
    ]


def test_missing_panel_report_stays_unknown(tmp_path: Path) -> None:
    """Un rapport absent ne transforme pas un panneau en zéro pose."""
    first, second = stack(tmp_path, 'west'), stack(tmp_path, 'east')
    second.with_suffix('.drizzle.json').unlink()
    result = StackingAnnotation.describe(mosaic(tmp_path, [first, second]))
    assert result['status'] == 'PARTIAL' and result['panel_count'] == 2
    assert result['lines'][-1] == 'P2 · east : inconnu'


@pytest.mark.parametrize(
    'case', ['wrong_output', 'failed', 'invalid_count', 'modified', 'bad_json'],
)
def test_inconsistent_stack_metadata_is_omitted(tmp_path: Path, case: str) -> None:
    """Ne pas présenter un compte étranger, obsolète ou incomplet comme fiable."""
    source = stack(tmp_path, 'session')
    report = source.with_suffix('.drizzle.json')
    data = json.loads(report.read_text())
    if case == 'wrong_output':
        data['output'] = str(tmp_path / 'another.fits')
    elif case == 'failed':
        data['status'] = 'failed'
    elif case == 'invalid_count':
        data['final_selection']['independent_retained'] = True
    if case == 'modified':
        stamp = report.stat().st_mtime_ns + 1_000_000_000
        os.utime(source, ns=(stamp, stamp))
    else:
        report.write_text('{' if case == 'bad_json' else json.dumps(data))
    result = StackingAnnotation.describe(source)
    assert result['status'] == 'INVALID'
    assert result['lines'] == []


def test_panel_restacked_after_mosaic_is_not_counted(tmp_path: Path) -> None:
    """Éviter d'associer une nouvelle sélection à une ancienne mosaïque."""
    first, second = stack(tmp_path, 'west'), stack(tmp_path, 'east')
    source = mosaic(tmp_path, [first, second])
    report = second.with_suffix('.drizzle.json')
    stamp = source.with_suffix('.json').stat().st_mtime_ns + 1_000_000_000
    os.utime(report, ns=(stamp, stamp))
    result = StackingAnnotation.describe(source)
    assert result['status'] == 'PARTIAL'
    assert result['lines'][-1].endswith('inconnu')


def test_repeated_mosaic_panels_are_invalid(tmp_path: Path) -> None:
    """Ne pas compter deux fois un panneau identique."""
    first = stack(tmp_path, 'west')
    result = StackingAnnotation.describe(mosaic(tmp_path, [first, first]))
    assert result['status'] == 'INVALID' and not result['lines']


def test_absent_provenance_does_not_invent_counts(tmp_path: Path) -> None:
    """Une image sans rapport reste annotable sans compteur supposé."""
    result = StackingAnnotation.describe(tmp_path / 'image.fits')
    assert result['status'] == 'UNAVAILABLE' and not result['lines']


def test_oversized_report_is_not_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ne pas charger par erreur un audit volumineux de post-traitement."""
    source = stack(tmp_path, 'session')
    monkeypatch.setattr('lib.stacking_annotation.MAX_REPORT_BYTES', 1)
    result = StackingAnnotation.describe(source)
    assert result['status'] == 'INVALID'
    assert 'volumineux' in result['reason']
