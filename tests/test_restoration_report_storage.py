"""Vérifie la projection sans perte des décisions et la conservation des audits."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from lib.restoration_comparison import read_report
from lib.processing_html import ProcessingHtmlReport
from lib.restoration_report_storage import RestorationReportStorage as Storage


@pytest.fixture
def audit() -> dict:
    """Crée des populations brutes volumineuses avec une alerte non bloquante."""
    stars = [{'id': i, 'flux': float(i)} for i in range(100)]
    flux = dict(status='FAIL', blocking=False, threshold=.1,
                proven_gain=0, per_star=stars)
    channels = [{'raw': list(range(100))}]
    return dict(
        status='accepted', selection_score=0.4, output_image='/result.fits',
        comparison=dict(status='validated', pairs=stars,
                        reference_measurements=dict(stars=stars),
                        paired_ratio=dict(median=.9), after_fwhm_px=dict(mean=2.7)),
        artifacts=dict(channels=channels, high_frequency_residual=channels,
                       stellar_flux=flux, max_noise_ratio=.6,
                       noise=dict(status='PASS', proven_gain=.4,
                                  per_channel=[dict(status='PASS', channel=0,
                                                    ratios=list(range(100)))])),
        criteria=dict(stellar_flux=flux),
        quality=dict(accepted=True, reasons=[], criteria=dict(stellar_flux=flux)),
        rejection_reasons=[], duration_seconds=3.5,
    )


def test_summary_keeps_decisions_and_references(audit: dict) -> None:
    """Score, seuils, zéro et False restent exacts, sans les mesures individuelles."""
    original = deepcopy(audit)
    summary = Storage.summary(audit, Path('/audit.json'))
    assert summary['audit_path'] == '/audit.json'
    assert summary['report_schema_version'] == 2
    assert summary['report_detail'] == 'summary'
    assert summary['selection_score'] == audit['selection_score']
    assert summary['criteria']['stellar_flux'] == dict(
        status='FAIL', blocking=False, threshold=.1, proven_gain=0)
    assert 'pairs' not in summary['comparison']
    assert 'reference_measurements' not in summary['comparison']
    assert 'channels' not in summary['artifacts']
    assert 'ratios' not in summary['artifacts']['noise']['per_channel'][0]
    assert len(json.dumps(summary)) < len(json.dumps(audit)) / 3
    assert audit == original


def test_detailed_keeps_raw_once_without_mutating_cache(audit: dict) -> None:
    """Les diagnostics restent complets dans artifacts, et les copies sont synthétiques."""
    audit['attempts'] = [deepcopy(audit)]
    original = deepcopy(audit)
    detailed = Storage.detailed(audit)
    assert detailed['comparison'] == audit['comparison']
    assert detailed['artifacts']['stellar_flux'] == audit['artifacts']['stellar_flux']
    assert detailed['artifacts']['channels'] == audit['artifacts']['channels']
    assert 'high_frequency_residual' not in detailed['artifacts']
    assert 'per_star' not in detailed['criteria']['stellar_flux']
    assert 'per_star' not in detailed['quality']['criteria']['stellar_flux']
    attempt = detailed['attempts'][0]
    assert 'high_frequency_residual' not in attempt['artifacts']
    assert attempt['artifacts']['stellar_flux']['per_star']
    assert audit == original


def test_atomic_write_preserves_previous_on_serialization_failure(tmp_path: Path) -> None:
    """Une erreur n'abîme pas la publication précédente et ne laisse pas de temporaire."""
    path = tmp_path / 'report.json'
    Storage.write(path, {'status': 'accepted'})
    previous = path.read_bytes()
    with pytest.raises(ValueError):
        Storage.write(path, {'invalid': float('nan')})
    assert path.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [path]
    Storage.write(path, {'status': 'rejected'})
    assert json.loads(path.read_text()) == {'status': 'rejected'}


def test_write_failure_propagates_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un remplacement impossible remonte l'erreur sans laisser de temporaire."""
    path = tmp_path / 'report.json'
    path.write_text('{}')

    def fail(source: Path, destination: Path) -> None:
        """Simule un volume refusant la publication."""
        raise PermissionError('read only')

    monkeypatch.setattr(Path, 'replace', fail)
    with pytest.raises(PermissionError):
        Storage.write(path, {'status': 'accepted'})
    assert path.read_text() == '{}'
    assert list(tmp_path.iterdir()) == [path]


def test_old_and_new_comparison_equivalent(tmp_path: Path, audit: dict) -> None:
    """Le comparateur lit anciens et nouveaux formats avec les mêmes critères et choix."""
    detailed_path = tmp_path / 'verification.json'
    Storage.write(detailed_path, Storage.detailed(audit))
    variant = dict(audit, number=2, chain=['denoise:graxpert'],
                   steps=[dict(operation_id=1)], audit_path=str(detailed_path))
    operation = dict(audit, operation_id=1, operation='denoise', engine_name='graxpert',
                     input_image='/source.fits', report_path=str(detailed_path))
    old = dict(engine='chains', status='accepted', image_path='/source.fits',
               selected_chain=variant['chain'], variants=[variant], operations=[operation])
    path = tmp_path / 'restoration.json'
    Storage.write(path, old)
    old_rows = read_report(path)
    new = dict(old, variants=[Storage.summary(variant)],
               operations=[Storage.summary(operation, detailed_path)])
    Storage.write(path, new)
    new_rows = read_report(path)
    for before, after in zip(old_rows, new_rows):
        for key in ('statut', 'score', 'retenu', 'chaine', 'criteria',
                    'ratio_bruit_max', 'gain_bruit_utile', 'entree', 'sortie'):
            assert before[key] == after[key]
    assert detailed_path.is_file()
    assert new_rows[0]['audit'] == str(detailed_path)
    html = ProcessingHtmlReport._restoration_view(new, tmp_path)
    assert 'href="verification.json"' in html
    assert 'Audit JSON détaillé' in html
