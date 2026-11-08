"""Longueur rectiligne semi-log, étirements successifs et point noir borné."""

import argparse
import json
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from lib.auto_ghs import ComputeBackend, GHSOptimizer, HistogramAnalyzer
from lib.config import Config
from lib.ghs_log_histogram import HISTOGRAM_BINS
from lib.ghs_quartiles import QuartileGHSProcessor, isolated_blackpoint, quartile_profile


def test_long_line_beats_flat_then_falling_histogram() -> None:
    """Une droite longue prime sur le plateau des octiles 2–4 puis la décroissance."""
    x = (np.arange(HISTOGRAM_BINS)+.5)/HISTOGRAM_BINS
    broken = np.zeros(HISTOGRAM_BINS)
    straight = np.zeros(HISTOGRAM_BINS)
    band = (x >= .0625) & (x < .875)
    broken[band] = np.rint(1e5*10**(-4*np.maximum(x[band]-.5, 0)))
    straight[band] = np.rint(1e5*10**(-2*x[band]))
    broken[4] = straight[4] = 1e7
    first, last = quartile_profile(broken), quartile_profile(straight)
    assert last['line_start'] == .0625 and last['line_end'] == .875
    assert last['line_span'] == .8125
    assert last['line_span'] > first['line_span']
    assert last['score'] > first['score']
    assert last['slope'] == pytest.approx(-2, abs=1e-4)


def test_slope_is_not_penalized_in_favor_of_flatness() -> None:
    """Une pente descendante et un plateau aussi longs ont le même objectif principal."""
    x = (np.arange(HISTOGRAM_BINS)+.5)/HISTOGRAM_BINS
    flat = np.full(HISTOGRAM_BINS, 1e5)
    falling = np.rint(1e5*10**(-3*x))
    flat[4] = falling[4] = 1e7
    first, last = quartile_profile(flat), quartile_profile(falling)
    assert first['line_span'] == last['line_span']
    assert first['score'] == pytest.approx(last['score'], abs=1e-5)


def test_sparse_band_is_not_reported_as_a_line() -> None:
    """Une poignée de classes ne constitue pas une droite mesurable."""
    counts = np.zeros(HISTOGRAM_BINS)
    counts[40:48] = 100
    report = quartile_profile(counts)
    assert not report['measurable'] and report['loss'] is None


def test_isolated_dark_peak_has_a_limited_blackpoint() -> None:
    """Un creux réel sépare un petit pic sombre de la courbe principale."""
    rng = np.random.default_rng(3)
    pixels = np.r_[rng.normal(.006, .0001, 8), rng.normal(.09, .012, 9992)]
    data = np.repeat(pixels.reshape(1, 100, 100), 3, axis=0).astype('f4')
    point = isolated_blackpoint(data, .001)
    assert .006 < point < .08
    lost = np.any((data > 0) & (data <= point), axis=0)
    assert np.mean(lost) == .0008
    assert isolated_blackpoint(data, 0) == 0
    assert isolated_blackpoint(data, .0001) == 0
    assert isolated_blackpoint(np.full_like(data, .02), .001) == 0
    with pytest.raises(ValueError, match='Budget'):
        isolated_blackpoint(data, -.001)


@pytest.mark.parametrize('name,value', [
    ('ghs_quartile_iterations', -1), ('ghs_quartile_iterations', 65),
    ('ghs_quartile_iterations', True), ('ghs_quartile_max_strength', 0),
    ('ghs_quartile_max_strength', 11), ('ghs_quartile_max_strength', float('nan')),
    ('ghs_blackpoint_loss', -.01), ('ghs_blackpoint_loss', .02),
    ('ghs_blackpoint_loss', float('inf')),
    ('ghs_linearity_tolerance', 0), ('ghs_linearity_tolerance', .6),
    ('ghs_linearity_tolerance', float('nan')),
    ('ghs_tail_height', -.1), ('ghs_tail_height', 1.1),
    ('ghs_tail_height', float('nan')), ('ghs_tail_channel', 'yellow'),
])
def test_invalid_quartile_options_are_rejected(name: str, value: float) -> None:
    """Les paramètres invalides échouent même lorsque le calcul serait désactivé."""
    with pytest.raises(ValueError):
        GHSOptimizer.options(argparse.Namespace(**{name: value}))


def test_options_are_collected_recursively_and_configurable(tmp_path: Path) -> None:
    """La classe feuille possède ses options ; JSON et CLI gardent leur priorité."""
    config = Config(tmp_path / 'settings.json')
    config.set('ghs_quartile_iterations', 20)
    parser = argparse.ArgumentParser()
    config.register(GHSOptimizer, parser)
    assert config.parse_args(parser, []).ghs_quartile_iterations == 20
    assert config.parse_args(parser, ['--ghs-quartile-iterations', '32']).ghs_quartile_iterations == 32
    assert config._parameter_owner(GHSOptimizer, 'ghs_blackpoint_loss') is QuartileGHSProcessor
    assert config._parameter_owner(GHSOptimizer, 'ghs_linearity_tolerance') is QuartileGHSProcessor
    assert config.parse_args(parser, ['--ghs-linearity-tolerance', '.03']).ghs_linearity_tolerance == .03
    assert config._parameter_owner(GHSOptimizer, 'ghs_tail_height') is QuartileGHSProcessor
    assert config._parameter_owner(GHSOptimizer, 'ghs_tail_channel') is QuartileGHSProcessor
    config.set('ghs_tail_height', .3)
    parser = argparse.ArgumentParser()
    config.register(GHSOptimizer, parser)
    assert config.parse_args(parser, []).ghs_tail_height == .3
    assert config.parse_args(parser, ['--ghs-tail-height', '.2']).ghs_tail_height == .2


def field() -> np.ndarray:
    """Produit un signal diffus étendu et un fond bruité, sans saturation initiale."""
    yy, xx = np.mgrid[-1:1:96j, -1:1:128j]
    base = .01 + .45 * np.exp(-4 * (xx ** 2 + yy ** 2))
    base += np.random.default_rng(4).normal(0, .0002, base.shape)
    return np.array([base, base, base], dtype='f4')


def test_search_is_deterministic_bounded_and_preserves_source() -> None:
    """Chaque passe améliore le champ entier sous budget cumulé et pic < 0,25."""
    data = field()
    original = data.copy()
    args = GHSOptimizer.options(argparse.Namespace(ghs_quartile_iterations=3))
    engine = QuartileGHSProcessor()
    backend, analyzer = ComputeBackend('cpu'), HistogramAnalyzer(256)
    output, report = engine.search(data, analyzer, backend, args)
    repeated, again = engine.search(data, analyzer, backend, args)
    np.testing.assert_array_equal(output, repeated)
    np.testing.assert_array_equal(data, original)
    assert report == again
    assert report['status'] == 'applied'
    assert len(report['iterations']) <= 3
    assert np.isfinite(output).all() and output.min() >= 0 and output.max() <= 1
    assert report['blackpoint_loss'] <= args.ghs_blackpoint_loss
    assert report['thresholds']['bright'] == .75
    assert report['thresholds']['core'] == .95
    assert report['tail_height_target'] == .25 and report['tail_height_channel'] == 'red'
    assert report['after']['tail']['population_scale'] == 1
    assert set(report['tail_channels_after']) == {'red', 'green', 'blue'}
    assert report['tail_height_achieved'] == report['after']['tail']['achieved']
    for entry in report['iterations']:
        assert len(entry['validations']) <= 3
        if entry['accepted']:
            accepted = entry['accepted']
            assert not accepted['rejections']
            assert accepted['full_profile']['peak'] < .25
            assert accepted['full_gain'] > 1e-5
            assert not accepted['full_profile']['tail']['estimated']
            assert accepted['parameters']['D'] <= args.ghs_quartile_max_strength
    json.dumps(report, allow_nan=False)


def test_disabled_and_constant_inputs_are_unchanged() -> None:
    """L'arrêt explicite et l'absence de support utile ne forcent pas un rendu."""
    backend, analyzer = ComputeBackend('cpu'), HistogramAnalyzer()
    args = GHSOptimizer.options(argparse.Namespace(ghs_quartile_iterations=0))
    data = field()
    output, report = QuartileGHSProcessor().search(data, analyzer, backend, args)
    np.testing.assert_array_equal(output, data)
    assert report['status'] == 'disabled'
    args.ghs_quartile_iterations = 2
    data = np.full((3, 16, 16), .03, dtype='f4')
    output, report = QuartileGHSProcessor().search(data, analyzer, backend, args)
    np.testing.assert_array_equal(output, data)
    assert report['status'] == 'no admissible improvement'


def test_standalone_report_and_fits_do_not_replace_source(tmp_path: Path) -> None:
    """Le protocole processor publie un FITS distinct et un audit JSON."""
    source = tmp_path / 'source.fits'
    fits.writeto(source, field())
    original = source.read_bytes()
    report = tmp_path / 'quartiles.json'
    result = QuartileGHSProcessor().post_process(source, report, argparse.Namespace(
        compute_backend='cpu', ghs_quartile_iterations=1,
    ))
    assert json.loads(report.read_text()) == result
    assert report.with_suffix('.fits').is_file()
    assert source.read_bytes() == original
    with pytest.raises(ValueError, match='différente'):
        QuartileGHSProcessor().post_process(source, source.with_suffix('.json'))


def test_search_extends_the_log_linear_span() -> None:
    """La recherche gagne de la longueur mesurée, sans exiger une pente plus faible."""
    uniform = np.random.default_rng(8).random((192, 192))
    light = .01 - .15 * np.log(1 - uniform * (1 - np.exp(-.98/.15)))
    data = np.repeat(light[None].astype('f4'), 3, axis=0)
    args = GHSOptimizer.options(argparse.Namespace(ghs_quartile_iterations=4, ghs_tail_height=0))
    _, report = QuartileGHSProcessor().search(
        data, HistogramAnalyzer(), ComputeBackend('cpu'), args,
    )
    assert report['status'] == 'applied'
    before, after = report['before'], report['after']
    assert before['measurable'] and after['measurable']
    assert after['line_span'] >= before['line_span']
    assert after['score'] > before['score'] + 1e-5


def test_fusion_has_its_own_cumulative_protection() -> None:
    """Les étoiles fixes participent aux gardes sans modifier la couche fournie."""
    data = field()
    stars = np.zeros_like(data)
    stars[:, 45:48, 60:63] = .9
    original = stars.copy()
    args = GHSOptimizer.options(argparse.Namespace(ghs_quartile_iterations=2))
    output, report = QuartileGHSProcessor().search(
        data, HistogramAnalyzer(), ComputeBackend('cpu'), args, stars,
    )
    np.testing.assert_array_equal(stars, original)
    from lib.ghs_linear_histogram import TAIL_HISTOGRAM_BINS, log_tail_height
    fused = .95*output + stars*(1-.95*output)
    counts = np.histogram(fused[0], bins=TAIL_HISTOGRAM_BINS, range=(0, 1))[0]
    assert report['tail_image_role'] == 'with_stars'
    assert report['tail_channels_after']['red'] == log_tail_height(counts)
    for entry in report['iterations']:
        for validation in entry['validations']:
            assert 'composite' in validation['protection']
            if not validation['rejections']:
                assert validation['protection']['composite']['near_white_fraction'] <= .001


def test_cuda_quartile_parity_when_available() -> None:
    """Le backend GPU sélectionné produit le même rendu que le classement CPU."""
    try:
        gpu = ComputeBackend('cuda')
    except RuntimeError as error:
        pytest.skip(str(error))
    args = GHSOptimizer.options(argparse.Namespace(ghs_quartile_iterations=1))
    data = field()
    analyzer = HistogramAnalyzer()
    expected, first = QuartileGHSProcessor().search(data, analyzer, ComputeBackend('cpu'), args)
    result, last = QuartileGHSProcessor().search(gpu.xp.asarray(data), analyzer, gpu, args)
    np.testing.assert_allclose(gpu.numpy(result), expected, atol=2e-6, rtol=2e-6)
    assert first['status'] == last['status']


def test_tail_options_save_explicitly_and_instances_keep_their_snapshot(tmp_path: Path) -> None:
    """Les réglages de hauteur appartiennent à la feuille et suivent CLI > JSON > défaut."""
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'ghs_tail_height': .4, 'ghs_tail_channel': 'blue'}))
    config = Config(path)
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(GHSOptimizer, parser)
    saved = config.parse_args(parser, [])
    assert saved.ghs_tail_height == .4 and saved.ghs_tail_channel == 'blue'
    args = config.parse_args(parser, ['--ghs-tail-height', '.25', '--ghs-tail-channel', 'red', '-S'])
    config.bind_parameters(args)
    instance = QuartileGHSProcessor()
    assert config.save_requested(args)
    assert json.loads(path.read_text())['ghs_tail_height'] == .25
    assert json.loads(path.read_text())['ghs_tail_channel'] == 'red'
    args.ghs_tail_height = .9
    assert instance._get_args().ghs_tail_height == .25


def test_tail_estimation_is_remeasured_on_all_pixels(monkeypatch: pytest.MonkeyPatch) -> None:
    """La grille ne certifie pas le niveau final ; l'audit RGB vient du champ entier."""
    from lib.ghs_linear_histogram import TAIL_CHANNELS, TAIL_HISTOGRAM_BINS, log_tail_height

    monkeypatch.setattr('lib.ghs_quartiles.MAX_SAMPLE_PIXELS', 2048)
    data = field()
    args = GHSOptimizer.options(argparse.Namespace(ghs_quartile_iterations=1))
    output, report = QuartileGHSProcessor().search(
        data, HistogramAnalyzer(), ComputeBackend('cpu'), args,
    )
    assert report['sample_stride'] > 1
    assert report['iterations'][0]['before']['tail']['estimated']
    assert not report['after']['tail']['estimated']
    for index, channel in enumerate(TAIL_CHANNELS):
        counts = np.histogram(output[index], bins=TAIL_HISTOGRAM_BINS, range=(0, 1))[0]
        assert report['tail_channels_after'][channel] == log_tail_height(counts)
    assert report['tail_height_achieved'] == report['after']['tail']['achieved']
    json.dumps(report, allow_nan=False)
