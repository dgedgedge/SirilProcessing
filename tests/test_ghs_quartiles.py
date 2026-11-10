"""Longueur rectiligne semi-log, étirements successifs et point noir borné."""

import argparse
import json
import weakref
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest
from astropy.io import fits

from lib.auto_ghs import ComputeBackend, GHSOptimizer, HistogramAnalyzer
from lib.config import Config
from lib.type_defs import JSONReport
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
    ('ghs_quartile_block_pixels', 0), ('ghs_quartile_block_pixels', 4194305),
    ('ghs_quartile_block_pixels', True), ('ghs_quartile_block_pixels', 1.5),
    ('ghs_quartile_validations', 0), ('ghs_quartile_validations', 65),
    ('ghs_quartile_validations', True), ('ghs_quartile_validations', 2.5),
    ('ghs_quartile_iterations', True), ('ghs_quartile_max_strength', 0),
    ('ghs_quartile_max_strength', 11), ('ghs_quartile_max_strength', float('nan')),
    ('ghs_blackpoint_loss', -.01), ('ghs_blackpoint_loss', .02),
    ('ghs_blackpoint_loss', float('inf')),
    ('ghs_linearity_tolerance', 0), ('ghs_linearity_tolerance', .6),
    ('ghs_linearity_tolerance', float('nan')),
    ('ghs_sparse_tail_fraction', -1), ('ghs_sparse_tail_fraction', 1.1),
    ('ghs_sparse_tail_fraction', float('nan')),
    ('ghs_sparse_highlight_retention', 0), ('ghs_sparse_highlight_retention', 1.1),
    ('ghs_sparse_highlight_retention', float('inf')),
    ('ghs_regression_min_count', -1), ('ghs_regression_min_count', float('nan')),
    ('ghs_regression_min_count', float('inf')),
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
    assert report['regression_floor_achieved'] == report['after']['regression_floor']['achieved']
    assert report['after']['regression_floor']['reference_bins'] == analyzer.bins
    assert not report['after']['regression_floor']['estimated']
    assert report['tail_height_achieved'] == report['after']['tail']['achieved']
    for entry in report['iterations']:
        assert len(entry['validations']) <= args.ghs_quartile_validations
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
    args = GHSOptimizer.options(argparse.Namespace(ghs_quartile_iterations=4, ghs_tail_height=0,
                                                      ghs_regression_min_count=0))
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
    path.write_text(json.dumps({'ghs_tail_height': .4, 'ghs_tail_channel': 'blue',
                                'ghs_quartile_validations': 6,
                                'ghs_quartile_block_pixels': 32768,
                                'ghs_sparse_tail_fraction': .002,
                                'ghs_sparse_highlight_retention': .6}))
    config = Config(path)
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(GHSOptimizer, parser)
    saved = config.parse_args(parser, [])
    assert saved.ghs_tail_height == .4 and saved.ghs_tail_channel == 'blue'
    assert saved.ghs_quartile_validations == 6
    assert saved.ghs_quartile_block_pixels == 32768
    assert saved.ghs_sparse_tail_fraction == .002
    assert saved.ghs_sparse_highlight_retention == .6
    args = config.parse_args(parser, ['--ghs-tail-height', '.25', '--ghs-tail-channel', 'red',
                                      '--ghs-quartile-validations', '16',
                                      '--ghs-quartile-block-pixels', '65536',
                                      '--ghs-sparse-tail-fraction', '.001',
                                      '--ghs-sparse-highlight-retention', '.5', '-S'])
    config.bind_parameters(args)
    instance = QuartileGHSProcessor()
    assert config.save_requested(args)
    assert json.loads(path.read_text())['ghs_tail_height'] == .25
    assert json.loads(path.read_text())['ghs_tail_channel'] == 'red'
    assert json.loads(path.read_text())['ghs_quartile_validations'] == 16
    assert json.loads(path.read_text())['ghs_sparse_tail_fraction'] == .001
    assert json.loads(path.read_text())['ghs_sparse_highlight_retention'] == .5
    args.ghs_sparse_tail_fraction = 0
    assert instance._get_args().ghs_sparse_tail_fraction == .001
    assert json.loads(path.read_text())['ghs_quartile_block_pixels'] == 65536
    args.ghs_quartile_block_pixels = 1024
    assert instance._get_args().ghs_quartile_block_pixels == 65536
    args.ghs_quartile_validations = 32
    assert instance._get_args().ghs_quartile_validations == 16
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


def test_regression_floor_option_precedence(tmp_path: Path) -> None:
    """Le seuil suit défauts, JSON et CLI, avec zéro comme désactivation."""
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'ghs_regression_min_count': 20}))
    config = Config(path)
    parser = argparse.ArgumentParser()
    config.register(GHSOptimizer, parser)
    assert config.parse_args(parser, []).ghs_regression_min_count == 20
    assert config.parse_args(parser, ['--ghs-regression-min-count', '0']).ghs_regression_min_count == 0
    assert GHSOptimizer.options(argparse.Namespace()).ghs_regression_min_count == 10


def test_candidate_grid_has_gentle_and_early_tangent_curves() -> None:
    """La grille explore de faibles D et HP précoces sans violer SP <= HP."""
    counts = np.full(128, 100.)
    counts[4] = 100000
    records = list(QuartileGHSProcessor.candidate_parameters(counts, 10, (.1, .15, .2)))
    assert any(item['D'] == .0625 for item in records)
    assert {item['HP'] for item in records} >= {.1, .15, .2, .25, .5, .75, 1}
    assert sum(item['D'] == 0 for item in records) == 1
    assert all(0 <= item['LP'] <= item['SP'] <= item['HP'] <= 1 for item in records)
    assert len({tuple(item.values()) for item in records}) == len(records)
    weak = list(QuartileGHSProcessor.candidate_parameters(counts, .03, (.1,)))
    assert all(0 <= item['D'] <= .03 for item in weak)


def test_finalist_order_spreads_families_before_near_duplicates() -> None:
    """Des variantes BP/SP du meilleur gain ne prennent pas toutes les places."""
    records = []
    for gain, strength, sp, hp in [(10, 1, .18, 1), (9.9, 1, .19, 1),
                                   (9.8, 1, .18, .75), (8, .125, .1, 1),
                                   (7, 1, .1, .2)]:
        records.append(dict(gain=gain, parameters=dict(D=strength, B=5, SP=sp, HP=hp)))
    original = records.copy()
    ordered = QuartileGHSProcessor.diverse_candidates(records)
    assert ordered[:3] == [records[0], records[3], records[4]]
    assert ordered.index(records[2]) < ordered.index(records[1])
    assert records == original
    assert len(ordered) == len(records)
    assert QuartileGHSProcessor.diverse_candidates([]) == []


def test_search_continues_after_three_full_resolution_rejections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un quatrième candidat peut gagner après trois échecs du champ entier."""
    from lib.ghs_final_stretch import ProtectedGHSFinalizer

    monkeypatch.setattr('lib.ghs_quartiles.MAX_SAMPLE_PIXELS', 2048)
    original_measure = ProtectedGHSFinalizer.measure
    full_trials = 0
    data = field()

    def measure(
        original: np.ndarray, candidate: np.ndarray,
        thresholds: dict[str, float], color_mode: str, xp: ModuleType = np,
        mask_reference: np.ndarray | None = None,
        tail_endpoint: float | None = None,
    ) -> JSONReport:
        """Simule une structure rare absente de la grille aux trois premiers essais."""
        nonlocal full_trials
        result = original_measure(
            original, candidate, thresholds, color_mode, xp,
            mask_reference=mask_reference, tail_endpoint=tail_endpoint,
        )
        if original.shape == data.shape and candidate is not original:
            full_trials += 1
            if full_trials <= 3:
                result['noise'] = 1.0
        return result

    monkeypatch.setattr(ProtectedGHSFinalizer, 'measure', staticmethod(measure))
    args = GHSOptimizer.options(argparse.Namespace(ghs_quartile_iterations=1))
    _, report = QuartileGHSProcessor().search(
        data, HistogramAnalyzer(256), ComputeBackend('cpu'), args,
    )
    trials = report['iterations'][0]['validations']
    assert len(trials) > 3
    assert all('background noise amplification' in item['rejections'] for item in trials[:3])
    assert any(not item['rejections'] for item in trials[3:])
    assert report['status'] == 'applied'
    assert len(trials) <= args.ghs_quartile_validations
    entry = report['iterations'][0]
    assert entry['candidate_count'] == (len(trials) + len(entry['sample_rejections'])
                                         + entry['unexamined_candidates'])


def test_validation_budget_exhaustion_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un budget consommé ne prétend pas épuiser les courbes possibles."""
    from lib.ghs_final_stretch import ProtectedGHSFinalizer

    monkeypatch.setattr('lib.ghs_quartiles.MAX_SAMPLE_PIXELS', 2048)
    original_measure = ProtectedGHSFinalizer.measure
    data = field()

    def measure(
        original: np.ndarray, candidate: np.ndarray,
        thresholds: dict[str, float], color_mode: str, xp: ModuleType = np,
        mask_reference: np.ndarray | None = None,
        tail_endpoint: float | None = None,
    ) -> JSONReport:
        """Rejette toute transformation du champ entier pour un bruit révélé au champ entier."""
        result = original_measure(
            original, candidate, thresholds, color_mode, xp,
            mask_reference=mask_reference, tail_endpoint=tail_endpoint,
        )
        if original.shape == data.shape and candidate is not original:
            result['noise'] = 1.0
        return result

    monkeypatch.setattr(ProtectedGHSFinalizer, 'measure', staticmethod(measure))
    args = GHSOptimizer.options(argparse.Namespace(
        ghs_quartile_iterations=1, ghs_quartile_validations=1,
    ))
    output, report = QuartileGHSProcessor().search(
        data, HistogramAnalyzer(256), ComputeBackend('cpu'), args,
    )
    np.testing.assert_array_equal(output, data)
    assert report['stop_reason'] == 'full-resolution validation budget exhausted'
    entry = report['iterations'][0]
    assert len(entry['validations']) == 1
    assert entry['validation_budget_exhausted']
    assert entry['unexamined_candidates'] > 0


def test_highlight_probes_keep_rare_regions_and_horizontal_neighbors() -> None:
    """Les pixels brillants hors grille restent représentés sans joindre deux lignes."""
    light = np.zeros((20, 30))
    light[3, 7] = .8
    light[12, 29] = .9
    rows, columns = QuartileGHSProcessor.highlight_coordinates(light)
    np.testing.assert_array_equal(rows, [3, 12])
    np.testing.assert_array_equal(columns, [7, 28])
    assert np.all(light[::4, ::4] < .75)
    bounded_rows, bounded_columns = QuartileGHSProcessor.highlight_coordinates(np.ones((100, 100)))
    assert bounded_rows.size == bounded_columns.size == 2048
    assert np.all(bounded_columns + 1 < 100)
    rows, columns = QuartileGHSProcessor.highlight_coordinates(np.zeros((10, 1)))
    assert rows.size == columns.size == 0
    rows, columns = QuartileGHSProcessor.highlight_coordinates(np.ones((10, 1)))
    assert rows.size == 10 and np.all(columns == 0)


@pytest.mark.parametrize('failed_guard', ['color_drift', 'core_contrast'])
def test_highlight_probes_reject_before_full_validation(
    monkeypatch: pytest.MonkeyPatch, failed_guard: str,
) -> None:
    """Les gardes ciblées voient un petit cœur manqué par la grille régulière."""
    from lib.ghs_final_stretch import ProtectedGHSFinalizer

    monkeypatch.setattr('lib.ghs_quartiles.MAX_SAMPLE_PIXELS', 2048)
    data = field()
    data[:, 45:49, 61:65] = .96 + np.arange(16).reshape(4, 4)*.001
    original_measure = ProtectedGHSFinalizer.measure
    full_trials = 0
    probe_trials = 0

    def measure(
        original: np.ndarray, candidate: np.ndarray,
        thresholds: dict[str, float], color_mode: str, xp: ModuleType = np,
        mask_reference: np.ndarray | None = None,
        tail_endpoint: float | None = None,
    ) -> JSONReport:
        """Rend une perte de couleur visible uniquement dans le sondage ciblé."""
        nonlocal full_trials, probe_trials
        result = original_measure(
            original, candidate, thresholds, color_mode, xp,
            mask_reference=mask_reference, tail_endpoint=tail_endpoint,
        )
        if candidate is not original:
            if original.shape[1:] == (16, 2):
                probe_trials += 1
                result[failed_guard] = 1.0 if failed_guard == 'color_drift' else 0.0
            elif original.shape == data.shape:
                full_trials += 1
        return result

    monkeypatch.setattr(ProtectedGHSFinalizer, 'measure', staticmethod(measure))
    args = GHSOptimizer.options(argparse.Namespace(ghs_quartile_iterations=1))
    output, report = QuartileGHSProcessor().search(
        data, HistogramAnalyzer(256), ComputeBackend('cpu'), args,
    )
    assert report['highlight_probe_count'] == 16
    assert probe_trials > 0 and full_trials == 0
    assert not report['iterations'][0]['validations']
    reason = ('highlight probe: color_drift' if failed_guard == 'color_drift'
              else 'highlight probe: core_contrast loss')
    assert any(reason in trial['rejections']
               for trial in report['iterations'][0]['sample_rejections'])
    np.testing.assert_array_equal(output, data)


@pytest.mark.parametrize('tail_count', [0, 1, 5, 10, 20])
def test_sparse_tail_interpolates_from_real_population(tail_count: int) -> None:
    """La rareté est mesurée sur le champ actif, sans dilution par les bordures."""
    light = np.zeros((20, 1000))
    light[:10] = .1
    light[0, :tail_count] = .8
    report = QuartileGHSProcessor.sparse_tail_retention(light, .9, .001, .5)
    assert report['active_count'] == 10000
    assert report['tail_count'] == tail_count
    assert report['fraction'] == pytest.approx(tail_count/10000)
    expected = .5 + .4 * min(tail_count/10, 1)
    assert report['effective_retention'] == pytest.approx(expected)
    json.dumps(report, allow_nan=False)


def test_sparse_tail_can_be_disabled_without_tightening_loose_settings() -> None:
    """La désactivation restaure la garde demandée ; un plancher ne la durcit pas."""
    light = np.full((10, 10), .1)
    assert QuartileGHSProcessor.sparse_tail_retention(light, .9, 0, .5)['effective_retention'] == .9
    assert QuartileGHSProcessor.sparse_tail_retention(light, .4, .001, .5)['effective_retention'] == .4
    empty = QuartileGHSProcessor.sparse_tail_retention(np.zeros((10, 10)), .9, .001, .5)
    assert empty['effective_retention'] == .9 and empty['fraction'] is None


@pytest.mark.parametrize('requested,fraction,minimum', [
    (0, .001, .5), (1.1, .001, .5), (float('nan'), .001, .5),
    (.9, -.1, .5), (.9, float('inf'), .5), (.9, .001, 0), (.9, .001, 1.1),
])
def test_sparse_tail_rejects_invalid_limits(
    requested: float, fraction: float, minimum: float,
) -> None:
    """Les limites invalides ne peuvent pas relâcher silencieusement les gardes."""
    with pytest.raises(ValueError):
        QuartileGHSProcessor.sparse_tail_retention(np.ones((2, 2)), requested, fraction, minimum)


@pytest.mark.parametrize('block_pixels', [1, 7, 37, 262144])
@pytest.mark.parametrize('color_mode', ['human', 'even'])
@pytest.mark.parametrize('blackpoint', [0.0, .003])
def test_block_transform_matches_full_precision_order(
    block_pixels: int, color_mode: str, blackpoint: float,
) -> None:
    """Les blocs conservent exactement BP float32 puis GHS/RGBBlend float64."""
    from lib.auto_ghs import ghs_rgb, linear_blackpoint

    source = np.random.default_rng(15).random((3, 11, 29), dtype=np.float32)
    original = source.copy()
    params = dict(D=2.3, B=5., SP=.2, LP=.1, HP=.5)
    shifted = linear_blackpoint(source, blackpoint) if blackpoint else source
    expected = ghs_rgb(shifted.astype(np.float64), **params, color_mode=color_mode).astype('f4')
    actual = QuartileGHSProcessor.transform_candidate(
        source, params, blackpoint=blackpoint, block_pixels=block_pixels,
        color_mode=color_mode,
    )
    np.testing.assert_array_equal(actual, expected)
    np.testing.assert_array_equal(source, original)
    assert actual.dtype == source.dtype and not np.shares_memory(source, actual)


def test_block_transform_bounds_temporaries_for_wide_noncontiguous_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une ligne plus large que le budget est aussi découpée sans copie globale."""
    import lib.auto_ghs as module

    source = np.random.default_rng(5).random((3, 9, 179), dtype=np.float32)[:, ::2, ::2]
    original = module.ghs_rgb
    sizes = []

    def transfer(data: np.ndarray, **kwargs: object) -> np.ndarray:
        """Observe la taille réelle des entrées float64 du noyau mathématique."""
        assert data.dtype == np.float64
        sizes.append(data.shape[1] * data.shape[2])
        return original(data, **kwargs)

    monkeypatch.setattr(module, 'ghs_rgb', transfer)
    params = dict(D=1.2, B=15., SP=.1, LP=0., HP=.5)
    result = QuartileGHSProcessor.transform_candidate(source, params, block_pixels=7)
    expected = original(source.astype(np.float64), **params).astype(source.dtype)
    np.testing.assert_array_equal(result, expected)
    assert len(sizes) > 1 and max(sizes) <= 7


def test_search_releases_candidate_images_before_the_next_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seuls les paramètres du gagnant subsistent entre les validations complètes."""
    monkeypatch.setattr('lib.ghs_quartiles.MAX_SAMPLE_PIXELS', 2048)
    transform = QuartileGHSProcessor.transform_candidate
    source = field()
    references = []

    def track(
        image: np.ndarray, params: dict[str, float], xp: ModuleType = np,
        *, blackpoint: float = 0.0, block_pixels: int = 262144,
        color_mode: str = 'human',
    ) -> np.ndarray:
        """Vérifie la disparition des images complètes avant l'allocation suivante."""
        full = image.shape == source.shape
        if full:
            assert all(reference() is None for reference in references)
        result = transform(image, params, xp, blackpoint=blackpoint,
                           block_pixels=block_pixels, color_mode=color_mode)
        if full:
            references.append(weakref.ref(result))
        return result

    monkeypatch.setattr(QuartileGHSProcessor, 'transform_candidate', staticmethod(track))
    args = GHSOptimizer.options(argparse.Namespace(
        ghs_quartile_iterations=1, ghs_quartile_validations=4,
    ))
    output, report = QuartileGHSProcessor().search(
        source, HistogramAnalyzer(256), ComputeBackend('cpu'), args,
    )
    assert report['status'] == 'applied'
    assert report['candidate_storage'] == 'parameters_only'
    assert len(references) == len(report['iterations'][0]['validations']) + 1
    assert references[-1]() is output
    best = report['iterations'][0]['accepted']
    expected = transform(source, best['parameters'], blackpoint=best['BP'])
    np.testing.assert_array_equal(output, expected)


@pytest.mark.parametrize('block_pixels', [0, -1, True, 1.5, 4194305])
def test_invalid_transform_blocks_are_rejected(block_pixels: int) -> None:
    """Un budget invalide échoue avant d'allouer l'image de sortie."""
    with pytest.raises(ValueError, match='bloc'):
        QuartileGHSProcessor.transform_candidate(
            np.zeros((3, 2, 2), dtype='f4'), dict(D=1), block_pixels=block_pixels,
        )


def test_cuda_block_transform_limits_full_size_allocations() -> None:
    """Un vrai GPU ne reçoit aucune allocation float64 RGB de tout le champ."""
    try:
        backend = ComputeBackend('cuda')
    except RuntimeError as error:
        pytest.skip(str(error))
    from cupy.cuda import MemoryHook

    source = np.random.default_rng(6).random((3, 512, 1024), dtype=np.float32)
    device = backend.xp.asarray(source)
    params = dict(D=2.3, B=5., SP=.2, LP=.1, HP=.5)
    sizes = []

    class AllocationSizes(MemoryHook):
        """Observe les demandes réelles au pool CUDA pendant la transformation."""

        name = 'test_ghs_block_allocations'

        def malloc_postprocess(self, **kwargs: int) -> None:
            """Retient la taille demandée, avant tout arrondi du pool."""
            sizes.append(kwargs['size'])

    with AllocationSizes():
        output = QuartileGHSProcessor.transform_candidate(
            device, params, backend.xp, blackpoint=.003, block_pixels=65536,
        )
    backend.xp.cuda.get_current_stream().synchronize()
    expected = QuartileGHSProcessor.transform_candidate(
        source, params, blackpoint=.003, block_pixels=65536,
    )
    np.testing.assert_allclose(backend.numpy(output), expected, atol=2e-6, rtol=2e-6)
    np.testing.assert_array_equal(backend.numpy(device), source)
    assert sizes and max(sizes) <= source.nbytes


def test_diffuse_guard_rejects_the_m33_dimming_pattern() -> None:
    """La droite ne peut s'allonger en perdant les structures diffuses déjà révélées."""
    before = dict(sky_median=.043588, diffuse_median=.084597)
    dark = dict(sky_median=.003634, diffuse_median=.021265)
    assert QuartileGHSProcessor.diffuse_rejections(before, dark) == [
        'diffuse brightness loss', 'diffuse separation loss',
    ]
    brighter = dict(sky_median=.049430, diffuse_median=.117572)
    assert not QuartileGHSProcessor.diffuse_rejections(before, brighter)
    washed_out = dict(sky_median=.09, diffuse_median=.10)
    assert QuartileGHSProcessor.diffuse_rejections(before, washed_out) == ['diffuse separation loss']
    assert not QuartileGHSProcessor.diffuse_rejections(before, before.copy())
    assert not QuartileGHSProcessor.diffuse_rejections(dict(diffuse_median=None), dark)
    assert 'diffuse brightness loss' in QuartileGHSProcessor.diffuse_rejections(before, {})


def test_repeated_probe_neighbors_do_not_create_a_measurable_bright_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sept pixels uniques restent insuffisants même si leurs voisins sont répétés."""
    from lib.ghs_final_stretch import ProtectedGHSFinalizer

    monkeypatch.setattr('lib.ghs_quartiles.MAX_SAMPLE_PIXELS', 2048)
    data = field()
    data[:, 50, 60:67] = .8 + np.arange(7)*.001
    measure = ProtectedGHSFinalizer.measure

    def measured(
        original: np.ndarray, candidate: np.ndarray, thresholds: dict[str, float],
        color_mode: str, xp: ModuleType = np,
        mask_reference: np.ndarray | None = None, tail_endpoint: float | None = None,
    ) -> JSONReport:
        """Expose une fausse dérive seulement dans les paires qui répètent les pixels."""
        result = measure(original, candidate, thresholds, color_mode, xp,
                         mask_reference=mask_reference, tail_endpoint=tail_endpoint)
        if original.shape[1:] == (7, 2) and candidate is not original:
            result['color_drift'] = 1.0
            result['highlight_contrast'] = 0.0
            result['local_contrast'] = 0.0
        return result

    monkeypatch.setattr(ProtectedGHSFinalizer, 'measure', staticmethod(measured))
    args = GHSOptimizer.options(argparse.Namespace(
        ghs_quartile_iterations=1, ghs_quartile_validations=2,
    ))
    _, report = QuartileGHSProcessor().search(
        data, HistogramAnalyzer(256), ComputeBackend('cpu'), args,
    )
    assert report['protection_before']['bright_count'] == 7
    assert report['protection_before']['highlight_contrast'] is None
    assert report['highlight_probe_before']['highlight_contrast'] is None
    assert report['highlight_probe_before']['color_drift'] is None
    reasons = [reason for entry in report['iterations']
               for trial in entry['sample_rejections'] for reason in trial['rejections']]
    assert 'highlight probe: color_drift' not in reasons
    assert 'highlight probe: highlight_contrast loss' not in reasons
    assert report['iterations'][0]['validations']
