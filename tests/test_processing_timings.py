"""Audit des durées des étapes actives et des échecs du post-traitement."""

import argparse
import json
import logging
from pathlib import Path

import pytest

from lib.postprocess import _PostProcessorSequence
from lib.processor import processor
from lib.type_defs import JSONReport


class TimedStep(processor):
    """Étape sans calcul astronomique transmettant son entrée ou levant une erreur."""

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Ne déclare aucune option pour ce double de traitement."""

    def post_process(
        self, input_path: Path, output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Simule le moteur pour isoler le chronométrage de la séquence."""
        if args is not None and getattr(args, 'fail', False):
            raise OSError('engine failed')
        return {'status': 'accepted', 'output_image': str(input_path)}


@pytest.mark.parametrize('enabled', [False, True])
def test_sequence_records_only_active_step_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, enabled: bool,
) -> None:
    """La durée mesurée figure dans le rapport enfant, le rapport global et le log."""
    caplog.set_level(logging.INFO)
    ticks = iter([10.0, 13.5])
    monkeypatch.setattr('lib.logging_utils.perf_counter', lambda: next(ticks))
    source = tmp_path / 'source.fits'
    source.touch()
    report = tmp_path / 'report.json'
    result = _PostProcessorSequence([TimedStep()]).post_process(
        source, report, argparse.Namespace(enable_timedstep=enabled),
    )
    assert json.loads(report.read_text()) == result
    if enabled:
        assert result['timedstep']['processing_duration_seconds'] == 3.5
        child = tmp_path / 'report_steps/01_timedstep.json'
        assert json.loads(child.read_text()) == result['timedstep']
        assert '3.500 s' in caplog.text
    else:
        assert result == {}
        assert 'Durée —' not in caplog.text


def test_sequence_logs_time_before_propagating_engine_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Un échec d'étape laisse sa durée dans le log et conserve la cause réelle."""
    caplog.set_level(logging.INFO)
    ticks = iter([10.0, 12.0])
    monkeypatch.setattr('lib.logging_utils.perf_counter', lambda: next(ticks))
    with pytest.raises(RuntimeError, match='engine failed') as raised:
        _PostProcessorSequence([TimedStep()]).post_process(
            tmp_path / 'source.fits', tmp_path / 'report.json',
            argparse.Namespace(fail=True),
        )
    assert isinstance(raised.value.__cause__, OSError)
    assert '2.000 s (interrompu ou en échec)' in caplog.text
