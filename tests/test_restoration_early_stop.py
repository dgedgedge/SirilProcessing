"""Seuils stricts et parcours prioritaire avec reprise exhaustive et cache."""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from astropy.io import fits
import numpy as np
import pytest

from lib.config import Config
from lib.processor import processor
from lib.restoration import RestorationComparisonProcessor
from lib.restoration_chains import RestorationChainProcessor
from lib.restoration_early_stop import RestorationEarlyStop as Policy
from lib.type_defs import JSONReport


@pytest.fixture
def passing_audit() -> JSONReport:
    """Décrit une chaîne validée à 31 % de gain FWHM et 36 % de bruit utile."""
    return dict(
        status='accepted', selection_score=1.6,
        score_details=dict(fwhm_median_ratio=.69, useful_noise_gain=.36),
        criteria={name: dict(status='PASS') for name in Policy.REQUIRED_CRITERIA},
    )


@pytest.mark.parametrize('ratio,noise,eligible', [
    (.69, .36, True), (.7, .36, False), (.69, .35, False),
    (.70001, .36, False), (.69999, .35001, True),
    (None, .36, False), (.69, None, False),
    (float('nan'), .36, False), (.69, float('inf'), False),
    (0, .36, False), (-1, .36, False), (True, .36, False),
])
def test_strict_thresholds(
    passing_audit: JSONReport, ratio: float | None, noise: float | None, eligible: bool,
) -> None:
    """L'égalité aux seuils, les valeurs manquantes et non finies ne déclenchent rien."""
    passing_audit['score_details'].update(fwhm_median_ratio=ratio, useful_noise_gain=noise)
    assert Policy.assess(passing_audit, argparse.Namespace())['eligible'] is eligible


@pytest.mark.parametrize('status', ['FAIL', 'UNKNOWN', None])
def test_diagnostic_criteria_must_pass(passing_audit: JSONReport, status: str | None) -> None:
    """Même non bloquant pour l'acceptation, un diagnostic interdit l'arrêt anticipé."""
    passing_audit['criteria']['diffuse'] = dict(status=status, blocking=False)
    result = Policy.assess(passing_audit, argparse.Namespace())
    assert not result['eligible']
    assert 'diffuse' in result['non_pass_criteria']


def test_missing_and_future_criteria(passing_audit: JSONReport) -> None:
    """Les critères attendus manquants et les futurs diagnostics inconnus sont refusés."""
    missing = deepcopy(passing_audit)
    del missing['criteria']['stars']
    result = Policy.assess(missing, argparse.Namespace())
    assert result['missing_criteria'] == ['stars']
    assert not result['eligible']
    passing_audit['criteria']['future'] = dict(status='UNKNOWN')
    assert not Policy.assess(passing_audit, argparse.Namespace())['eligible']


@pytest.mark.parametrize('value', [-.1, 1, float('nan'), float('inf'), True, '0.3'])
def test_invalid_thresholds(value: object) -> None:
    """Les seuils invalides sont refusés avant tout traitement d'image."""
    with pytest.raises(ValueError, match='restoration_early_stop_fwhm_gain'):
        Policy.thresholds(argparse.Namespace(restoration_early_stop_fwhm_gain=value))


def test_config_cli_priority_and_snapshot(tmp_path: Path) -> None:
    """Les options appartiennent au service et respectent CLI > JSON > défauts."""
    config = Config(tmp_path / 'settings.json')
    config.set('restoration_early_stop', False)
    config.set('restoration_early_stop_fwhm_gain', .4)
    parser = argparse.ArgumentParser()
    config.register(RestorationChainProcessor, parser)
    args = config.parse_args(parser, ['--restoration-early-stop',
                                     '--restoration-early-stop-noise-gain', '.45'])
    config.bind_parameters(args)
    first = Policy()
    assert first._get_args().restoration_early_stop is True
    assert Policy.thresholds(first._get_args()) == (.4, .45)
    assert RestorationChainProcessor()._get_args().restoration_early_stop is True
    args = config.parse_args(parser, ['--no-restoration-early-stop'])
    config.bind_parameters(args)
    assert not Policy()._get_args().restoration_early_stop
    assert first._get_args().restoration_early_stop


@pytest.mark.parametrize('mode', ['stop', 'fail', 'unknown', 'local_rejection', 'disabled',
                                   'fwhm_boundary', 'noise_boundary', 'missing'])
def test_priority_full_fallback_and_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, passing_audit: JSONReport, mode: str,
) -> None:
    """Le candidat complet est premier ; les autres essais réutilisent ses préfixes.

    De vrais FITS portent la signature de leur parcours, sans lancer Siril ou
    un réseau neuronal. Un rejet local ne lance pas GraXpert sur le candidat
    refusé ; le débruitage seul reste essayé sur l'original.
    """
    source = tmp_path / 'source.fits'
    data = np.random.default_rng(42).normal(.1, .001, (96, 96)).astype('float32')
    data[0, 0] = 0
    fits.writeto(source, data)
    calls: list[tuple[int, int]] = []

    def engine(code: int) -> type[processor]:
        """Fabrique un moteur avec signature de pixels et journal des entrées."""
        class Fake(processor):
            """Moteur déterministe acceptant ou refusant son candidat."""

            @classmethod
            def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
                """Le faux moteur n'expose aucun paramètre scientifique."""

            def post_process(
                self, input_path: Path, output_path: Path,
                args: argparse.Namespace | None = None,
            ) -> JSONReport:
                """Encode l'entrée effective puis le code du traitement dans le FITS."""
                pixels = fits.getdata(input_path).copy()
                previous = int(round(float(pixels[0, 0])))
                calls.append((code, previous))
                if mode == 'local_rejection' and code == 1:
                    return dict(status='rejected', output_image=str(input_path),
                                rejection_reasons=['insufficient_matches'])
                pixels[0, 0] = previous * 10 + code
                destination = output_path.with_suffix('.fits')
                fits.writeto(destination, pixels, fits.getheader(input_path))
                return dict(status='accepted', output_image=str(destination))
        return Fake

    ar, graxpert, cosmic = engine(1), engine(2), engine(3)

    def engines(cls: type, args: argparse.Namespace) -> list:
        """Expose AR et deux débruiteurs, sans moteur supplémentaire."""
        return [('aberration_remover', ar)] if cls.operation == 'sharpen' else [
            ('graxpert', graxpert), ('cosmic_clarity', cosmic)]

    def final(self: RestorationChainProcessor, original: Path, candidate: Path,
              reference: object, work: Path, args: argparse.Namespace,
              operations: tuple[str, ...]) -> JSONReport:
        """Accepte les sorties ; le candidat de référence conserve ses vraies conditions."""
        result = deepcopy(passing_audit)
        value = int(round(float(fits.getdata(candidate)[0, 0])))
        if value == 12:
            if mode == 'fwhm_boundary':
                result['score_details']['fwhm_median_ratio'] = .7
            elif mode == 'noise_boundary':
                result['score_details']['useful_noise_gain'] = .35
            elif mode == 'missing':
                del result['criteria']['stellar_flux']
            if mode in ('fail', 'unknown'):
                result['criteria']['diffuse'] = dict(
                    status='FAIL' if mode == 'fail' else 'UNKNOWN', blocking=False)
        else:
            result['selection_score'] = .1
        return result

    monkeypatch.setattr(RestorationComparisonProcessor, 'engines', classmethod(engines))
    monkeypatch.setattr(RestorationChainProcessor, '_final_evaluation', final)
    report = tmp_path / 'restoration.json'
    args = argparse.Namespace(restoration_early_stop=mode != 'disabled',
                              restoration_previews=False)
    result = RestorationChainProcessor().post_process(source, report, args)
    early = result['early_stop']
    if mode == 'stop':
        assert calls == [(1, 0), (2, 1)]
        assert early['triggered']
        assert early['skipped_variant_count'] == 4
        assert len(result['variants']) == 2
        assert fits.getdata(result['output_image'])[0, 0] == 12
        assert 'ARRÊT ANTICIPÉ' in Path(result['summary_path']).read_text()
        assert 'Arrêt anticipé' in (report.parent / 'index.html').read_text()
    else:
        assert not early['triggered']
        assert early['skipped_variant_count'] == 0
        assert len(result['variants']) == early['planned_variant_count'] == 6
        assert len(calls) == len(set(calls))
        assert (2, 0) in calls
        if mode == 'local_rejection':
            assert (2, 1) not in calls
        else:
            assert (2, 1) in calls
    if mode != 'disabled':
        assert calls[0] == (1, 0)
    else:
        assert calls[0] == (2, 0)
    assert fits.getdata(source)[0, 0] == 0
    assert early['evaluated_variant_count'] == len(result['variants'])


def test_continuation_messages_are_explicit(passing_audit: JSONReport) -> None:
    """Les diagnostics FAIL, absences et seuils manqués sont nommés sans ambiguïté."""
    passing_audit['criteria']['diffuse'] = dict(status='FAIL', blocking=False,
                                               reason='DIFFUSE_AUDIT')
    del passing_audit['criteria']['stars']
    passing_audit['score_details']['fwhm_median_ratio'] = .7
    passing_audit['score_details']['useful_noise_gain'] = None
    verdict = Policy.assess(passing_audit, argparse.Namespace())
    messages = '\n'.join(Policy.continuation_messages(passing_audit, verdict))
    assert 'diffuse : FAIL' in messages
    assert 'diagnostique non bloquant' in messages
    assert 'PASS requis' in messages
    assert 'stars : absent' in messages
    assert '30 % ; strictement supérieur à 30 %' in messages
    assert 'gain bruit utile : non mesuré' in messages
    assert '35 % requis' in messages
