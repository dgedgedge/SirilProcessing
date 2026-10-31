"""Recherche générique D/B/SP/HP, histogramme et garde de recomposition."""

import argparse
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from lib.auto_ghs import ComputeBackend, GHSOptimizer, HistogramAnalyzer, ghs_rgb
from lib.ghs_final_stretch import ProtectedGHSFinalizer


def synthetic_field(kind: str) -> np.ndarray:
    """Construit trois distributions spatiales distinctes avec fond bruité et zones brillantes."""
    yy, xx = np.mgrid[-1:1:128j, -1:1:160j]
    radius = xx ** 2 + yy ** 2
    rng = np.random.default_rng(31)
    sky = .035 + rng.normal(0, .001, radius.shape)
    if kind == 'galaxy':
        signal = .2 * np.exp(-radius * 3) + .5 * np.exp(-radius * 35)
    elif kind == 'nebula':
        signal = .18 * (1 + np.sin(xx * 4)) * np.exp(-yy ** 2 * 5)
        signal += .25 * np.exp(-((xx + .4) ** 2 + (yy - .3) ** 2) * 25)
    else:
        signal = .08 * np.exp(-radius * 2)
        for x, y in ((-.4, -.2), (.1, .3), (.5, -.4), (.5, .5)):
            signal += .5 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) * 100)
    base = sky + signal
    return np.array([base * 1.05, base, base * .9], dtype='f4')


@pytest.mark.parametrize('kind', ['galaxy', 'nebula', 'cluster'])
def test_search_generic_fields_preserves_highlight_detail(kind: str) -> None:
    """Améliore le diffus sous gardes, ou conserve un champ déjà presque rectiligne."""
    source = synthetic_field(kind)
    original = source.copy()
    output, report = GHSOptimizer().final_stretch(
        source, HistogramAnalyzer(256), ComputeBackend('cpu'), 1.25,
    )
    assert report['mode'] == 'auto' and len(report['candidates']) >= 125
    np.testing.assert_array_equal(source, original)
    assert np.isfinite(output).all() and output.min() >= 0 and output.max() <= 1
    if kind == 'nebula':
        # Cette référence est déjà proche d'une droite (RMS < 0,04 dex).
        # Renforcer le poids du RMS peut légitimement conserver cette image.
        assert report['status'] == 'no admissible improvement'
        assert report['sample_before']['log_histogram']['rms_dex'] < .04
        np.testing.assert_array_equal(output, original)
        assert all(record['rejections'] for record in report['candidates'])
        return
    assert report['status'] == 'applied'
    audit = report['full_validation'][report['selected_validation']]
    assert audit['rejections'] == []
    assert len(report['full_validation']) <= 3
    assert audit['score'] == max(item['score'] for item in report['full_validation']
                                 if not item['rejections'])
    first, last = audit['before'], audit['after']
    assert last['highlight_contrast'] >= .9 * first['highlight_contrast']
    assert last['local_contrast'] >= .9 * first['local_contrast']
    assert (last['diffuse_median'] - last['sky_median']
            > first['diffuse_median'] - first['sky_median'])
    assert last['core_contrast'] >= .9 * first['core_contrast']
    assert last['core_color_drift'] <= .01
    assert 0 <= report['parameters']['SP'] <= report['parameters']['HP'] <= 1
    assert any(record['parameters']['SP'] > 0 for record in report['candidates'])
    assert last['diffuse_visibility'] >= min(.5, first['diffuse_visibility']) - 1e-7
    assert last['log_histogram']['slope'] is not None
    assert (first['log_histogram']['window_end'] == last['log_histogram']['window_end']
            == report['histogram_windows']['starless'])
    assert all(record['starless']['log_histogram']['window_end']
               == report['histogram_windows']['starless'] for record in report['candidates'])
    np.testing.assert_allclose(output, ghs_rgb(source, **report['parameters']), atol=1e-7)


def test_composite_fixed_masks_and_validation() -> None:
    """Les étoiles restent fixes et les contrôles s'appliquent aussi à la fusion finale."""
    data = synthetic_field('galaxy')
    stars = np.zeros_like(data)
    stars[:, 30:33, 40:43] = .6
    original_stars = stars.copy()
    output, report = GHSOptimizer().final_stretch(
        data, HistogramAnalyzer(256), ComputeBackend('cpu'), 1.25, stars=stars,
    )
    assert report['status'] == 'applied'
    checked = report['full_validation'][report['selected_validation']]
    assert checked['rejections'] == []
    assert (checked['composite_after']['highlight_contrast']
            >= .9 * checked['composite_before']['highlight_contrast'])
    assert checked['composite_before']['color_drift'] == 0
    assert (checked['composite_before']['log_histogram']['window_end']
            == checked['composite_after']['log_histogram']['window_end']
            == report['histogram_windows']['composite'])
    np.testing.assert_array_equal(stars, original_stars)
    assert np.any(output != data)


def test_identity_if_no_candidate_can_preserve_contrast() -> None:
    """Une conservation stricte refuse l'étirement plutôt que forcer un rendu dégradé."""
    data = synthetic_field('galaxy')
    output, report = GHSOptimizer().final_stretch(
        data, HistogramAnalyzer(256), ComputeBackend('cpu'), 1.25, retention=1,
    )
    assert report['status'] == 'no admissible improvement'
    np.testing.assert_array_equal(output, data)
    assert any(record['rejections'] for record in report['candidates'])


def test_padding_does_not_bias_thresholds() -> None:
    """Les bordures nulles n'entrent pas dans l'estimation des quantiles du champ."""
    source = synthetic_field('galaxy')
    padded = np.pad(source, ((0, 0), (10, 10), (20, 20)))
    assert (ProtectedGHSFinalizer.thresholds(source, 'human')
            == ProtectedGHSFinalizer.thresholds(padded, 'human'))


def test_dim_midtones_are_not_classified_as_protected_highlights() -> None:
    """P98 sombre ne bloque pas le gain des tons moyens ; le petit cœur reste protégé."""
    data = synthetic_field('galaxy') * .5
    thresholds = ProtectedGHSFinalizer.thresholds(data, 'human')
    assert thresholds['bright'] > thresholds['q98']
    assert thresholds['bright'] <= thresholds['core']
    assert thresholds['diffuse_low'] > thresholds['median']


def test_histogram_bonus_cannot_admit_a_curve_without_diffuse_gain() -> None:
    """Une queue artificiellement idéale ne suffit pas à autoriser un rendu inchangé."""
    engine = ProtectedGHSFinalizer()
    data = synthetic_field('galaxy')
    thresholds = engine.thresholds(data, 'human')
    before = engine.measure(data, data, thresholds, 'human')
    after = dict(before, log_histogram=dict(before['log_histogram'], slope=-.01, rms_dex=0))
    _, rejected = engine.assess(before, after, .9)
    assert 'no useful diffuse gain' in rejected


def test_measure_cuda_parity_when_available() -> None:
    """Les contrôles et la régression depuis 128 effectifs concordent sur CPU/CUDA."""
    try:
        backend = ComputeBackend('cuda')
    except RuntimeError as error:
        pytest.skip(str(error))
    source = synthetic_field('galaxy')
    candidate = ghs_rgb(source, D=.75, B=5, SP=.05, HP=.6)
    engine = ProtectedGHSFinalizer()
    thresholds = engine.thresholds(source, 'human')
    cpu = engine.measure(source, candidate, thresholds, 'human')
    cuda = engine.measure(backend.xp.asarray(source), backend.xp.asarray(candidate),
                          thresholds, 'human', backend.xp)
    for key in ('core_contrast', 'local_contrast', 'noise', 'diffuse_visibility'):
        assert cuda[key] == pytest.approx(cpu[key], rel=2e-5, abs=1e-7)
    assert cuda['log_histogram']['slope'] == pytest.approx(cpu['log_histogram']['slope'], abs=1e-3)


@pytest.mark.parametrize('options', [
    {'ghs_final_mode': 'invalid'}, {'ghs_highlight_retention': 0},
    {'ghs_highlight_retention': 1.1}, {'ghs_highlight_retention': float('nan')},
    {'ghs_log_rms_weight': -1}, {'ghs_log_rms_weight': 10.1},
    {'ghs_log_rms_weight': float('nan')},
])
def test_invalid_search_options(options: dict[str, str | float]) -> None:
    """Les mauvais modes et tolérances sont refusés avant de lancer le calcul."""
    with pytest.raises(ValueError, match='ghs-'):
        GHSOptimizer.options(argparse.Namespace(**options))


def test_standalone_engine_preserves_source(tmp_path: Path) -> None:
    """L'interface processor écrit un FITS distinct et les paramètres retenus."""
    source, report = tmp_path / 'input.fits', tmp_path / 'final.json'
    fits.writeto(source, synthetic_field('galaxy'), fits.Header({'SPNLIN': True}))
    original = source.read_bytes()
    result = ProtectedGHSFinalizer().post_process(
        source, report, argparse.Namespace(compute_backend='cpu', ghs_bins=256),
    )
    assert source.read_bytes() == original
    assert Path(result['output_image']).is_file() and report.is_file()
    assert result['status'] == 'applied'
