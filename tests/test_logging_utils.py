"""Chronométrage monotone sans attente, cumuls et propagation des exceptions."""

import logging

import pytest

from lib.logging_utils import log_duration


def test_duration_accumulates_and_logs_each_call(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Deux appels ajoutent leurs durées réelles, sans remettre le compteur à zéro."""
    ticks = iter([10.0, 10.25, 20.0, 21.5])
    monkeypatch.setattr('lib.logging_utils.perf_counter', lambda: next(ticks))
    caplog.set_level(logging.INFO)
    durations: dict[str, float] = {}
    with log_duration('Siril', durations, 'siril_seconds'):
        pass
    with log_duration('Siril', durations, 'siril_seconds'):
        pass
    assert durations == {'siril_seconds': 1.75}
    assert 'Durée — Siril : 0.250 s' in caplog.text
    assert 'Durée — Siril : 1.500 s' in caplog.text


@pytest.mark.parametrize('error', [RuntimeError('failure'), KeyboardInterrupt()])
def test_duration_survives_failure_or_interruption(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    error: BaseException,
) -> None:
    """Le temps déjà passé est conservé et l'exception originale est propagée."""
    ticks = iter([5.0, 7.5])
    monkeypatch.setattr('lib.logging_utils.perf_counter', lambda: next(ticks))
    caplog.set_level(logging.INFO)
    durations: dict[str, float] = {}
    with pytest.raises(type(error)) as raised:
        with log_duration('Moteur', durations, 'processing_seconds'):
            raise error
    assert raised.value is error
    assert durations == {'processing_seconds': 2.5}
    assert '2.500 s (interrompu ou en échec)' in caplog.text
