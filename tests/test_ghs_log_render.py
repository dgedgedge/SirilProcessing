"""Sélection de rendus indépendants et validation réelle des objectifs GHS."""

import argparse
import json
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from lib.auto_ghs import ComputeBackend, GHSOptimizer, HistogramAnalyzer
from lib.ghs_log_render import LogHistogramRenderer, LogSpanGHSProcessor
from lib.ghs_quartiles import QuartileGHSProcessor
from lib.type_defs import JSONReport


def assessment(met: bool, span: float) -> JSONReport:
    """Fournit un audit simulé pour isoler le classement du sélecteur."""
    profile = dict(measurable=True, score=span,
                   regression_floor=dict(achieved=met), tail=dict(achieved=met))
    return dict(profile=profile, longest_line=dict(line_span=span),
                targets_achieved=met, met_count=2 if met else 0)


@pytest.mark.parametrize('dark_guided', [True, False])
def test_selector_keeps_valid_reference_over_darker_or_unqualified_candidate(
    monkeypatch: pytest.MonkeyPatch, dark_guided: bool,
) -> None:
    """La longueur seule ne remplace pas un rendu valide par une sortie dégradée."""
    original = np.full((3, 32, 32), .1, dtype='f4')
    reference = np.full_like(original, .2)
    guided = np.full_like(original, .05 if dark_guided else .3)
    args = GHSOptimizer.options(argparse.Namespace(ghs_quartile_iterations=1))

    def reference_search(
        self: LogSpanGHSProcessor, data: np.ndarray, analyzer: HistogramAnalyzer,
        backend: ComputeBackend, args: argparse.Namespace,
        stars: np.ndarray | None, headroom: float,
    ) -> tuple[np.ndarray, JSONReport]:
        """Simule le candidat propre du moteur de longueur."""
        return reference, dict(status='applied', iterations=[])

    def guided_search(
        self: QuartileGHSProcessor, data: np.ndarray, analyzer: HistogramAnalyzer,
        backend: ComputeBackend, args: argparse.Namespace,
        stars: np.ndarray | None, headroom: float,
    ) -> tuple[np.ndarray, JSONReport]:
        """Simule une sortie plus longue mais sombre ou incomplète."""
        return guided, dict(status='applied', iterations=[])

    def evaluate(
        image: np.ndarray, args: argparse.Namespace, stars: np.ndarray | None,
        headroom: float,
    ) -> JSONReport:
        """Isole les objectifs des mesures de luminosité réellement calculées."""
        if image is reference:
            return assessment(True, .7)
        if image is guided:
            return assessment(dark_guided, .9)
        return assessment(False, .2)

    monkeypatch.setattr(LogSpanGHSProcessor, 'search', reference_search)
    monkeypatch.setattr(QuartileGHSProcessor, 'search', guided_search)
    monkeypatch.setattr(LogHistogramRenderer, 'assess', staticmethod(evaluate))
    output, report = LogHistogramRenderer().search(
        original, HistogramAnalyzer(), ComputeBackend('cpu'), args,
    )
    np.testing.assert_array_equal(output, reference)
    np.testing.assert_array_equal(original, .1*np.ones_like(original))
    assert report['selected_search'] == 'length'
    assert report['targets_achieved']
    if dark_guided:
        assert 'diffuse brightness loss' in report['searches']['reference_level']['output_rejections']
    json.dumps(report, allow_nan=False)


def test_assessment_requires_actual_supported_reference_segment() -> None:
    """Une queue pleine permet de vérifier le point ; un plateau court ne le peut pas."""
    counts = np.full(128, 640, dtype=int)
    counts[4] = 100000
    pixels = np.repeat((np.arange(128, dtype='f4')+.5)/128, counts)
    image = np.repeat(pixels[None, None, :], 3, axis=0)
    args = GHSOptimizer.options(argparse.Namespace(ghs_tail_height=0))
    result = LogHistogramRenderer.assess(image, args, None, .95)
    assert result['targets_achieved']
    assert result['profile']['regression_floor']['reference_covered']
    assert not result['profile']['regression_floor']['extrapolated']
    image = image[:, :, :int(counts[:64].sum())]
    result = LogHistogramRenderer.assess(image, args, None, .95)
    assert not result['targets_achieved']
    assert not result['profile']['regression_floor']['achieved']


def test_disabled_selector_preserves_source_and_standalone_interface(tmp_path: Path) -> None:
    """Le sélecteur désactivé conserve les pixels et publie des sorties distinctes."""
    image = np.full((3, 32, 32), .1, dtype='f4')
    args = argparse.Namespace(ghs_quartile_iterations=0, compute_backend='cpu')
    source = tmp_path/'source.fits'
    destination = tmp_path/'report.json'
    fits.writeto(source, image)
    report = LogHistogramRenderer().post_process(source, destination, args)
    assert report['status'] == 'disabled'
    assert report['selected_search'] == 'unchanged'
    np.testing.assert_array_equal(fits.getdata(destination.with_suffix('.fits')), image)
    np.testing.assert_array_equal(fits.getdata(source), image)
    with pytest.raises(ValueError, match='distincte'):
        LogHistogramRenderer().post_process(source, source.with_suffix('.json'), args)


def test_length_engine_checks_its_own_final_diffuse_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une exploration de longueur terminée trop sombre ne produit aucun candidat valide."""
    source = np.full((3, 32, 32), .1, dtype='f4')
    args = GHSOptimizer.options(argparse.Namespace())

    def explored(
        self: QuartileGHSProcessor, data: np.ndarray, analyzer: HistogramAnalyzer,
        backend: ComputeBackend, options: argparse.Namespace,
        stars: np.ndarray | None, headroom: float,
    ) -> tuple[np.ndarray, JSONReport]:
        """Simule une suite d'essais qui augmente la longueur mais perd le diffus."""
        return data*.5, dict(status='applied', before={'score': 0}, after={'score': 1},
                            protection_before=dict(sky_median=.04, diffuse_median=.08),
                            iterations=[dict(accepted=dict(protection=dict(
                                starless=dict(sky_median=.01, diffuse_median=.02),
                            )))])

    monkeypatch.setattr(QuartileGHSProcessor, 'search', explored)
    output, report = LogSpanGHSProcessor().search(
        source, HistogramAnalyzer(), ComputeBackend('cpu'), args,
    )
    np.testing.assert_array_equal(output, source)
    assert report['status'] == 'rejected final diffuse loss'
    assert 'diffuse brightness loss' in report['final_output_rejections']


def test_assessment_does_not_certify_a_peak_above_the_allowed_quarter() -> None:
    """Un pic trop clair reste une cible non atteinte malgré une queue peuplée."""
    counts = np.full(128, 640, dtype=int)
    counts[60] = 100000
    pixels = np.repeat((np.arange(128, dtype='f4')+.5)/128, counts)
    image = np.repeat(pixels[None, None, :], 3, axis=0)
    args = GHSOptimizer.options(argparse.Namespace(ghs_tail_height=0))
    result = LogHistogramRenderer.assess(image, args, None, .95)
    assert result['profile']['regression_floor']['achieved']
    assert result['profile']['peak'] >= .25
    assert not result['targets_achieved']
