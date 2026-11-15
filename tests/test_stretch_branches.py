"""Contrats autonomes, paramètres et ressources des branches d'étirement."""

import argparse
import json
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from lib.auto_ghs import ComputeBackend, GHSOptimizer
from lib.config import Config
from lib.final_render import AutoGHSFinalRenderer
from lib.ghs_diagnostics import HistogramDiagnostics
from lib.star_rendering import ConservativeStarRenderer
from lib.stretch_branches import (
    LayerStretchProcessor, StarStretchProcessor, StarlessStretchProcessor,
)
from lib.type_defs import JSONReport


@pytest.fixture
def layer_source(tmp_path: Path) -> Path:
    """Fournit une couche RGB linéaire synthétique et ses métadonnées."""
    rng = np.random.default_rng(48)
    data = rng.uniform(.001, .04, (3, 24, 24)).astype(np.float32)
    source = tmp_path / 'layer.fits'
    fits.writeto(source, data, fits.Header({'SPNLIN': False, 'ROWORDER': 'BOTTOM-UP'}))
    return source


@pytest.mark.parametrize('cls,subject', [(StarStretchProcessor, 'stars'),
                                         (StarlessStretchProcessor, 'starless')])
def test_standalone_branch_publishes_its_own_subject(
    layer_source: Path, tmp_path: Path, cls: type[LayerStretchProcessor], subject: str,
) -> None:
    """Chaque classe est un traitement complet, utilisable sans l'orchestrateur."""
    before = layer_source.read_bytes()
    path = tmp_path / subject / f'{subject}.json'
    args = argparse.Namespace(
        compute_backend='cpu', ghs_max_iterations=1, ghs_dynamic_max_iterations=1,
        ghs_final_strength=0, ghs_histogram_strength=0, star_faint_boost=1,
        star_noise_sigma=8, star_stretch_max_width_ratio=1.4, star_boost_fraction=.5,
    )
    result = cls().post_process(layer_source, path, args)
    data = fits.getdata(layer_source).astype(np.float32)
    if subject == 'stars':
        expected, _ = ConservativeStarRenderer.render(
            data, faint_boost=1, noise_sigma=8, max_width_ratio=1.4, boost_fraction=.5,
        )
    else:
        expected, _ = GHSOptimizer().optimize(data, 'range', args, ComputeBackend('cpu'))
    np.testing.assert_array_equal(fits.getdata(result['output_image']), expected)
    assert json.loads(path.read_text()) == result
    assert layer_source.read_bytes() == before
    assert fits.getheader(result['output_image'])['SPNLIN']
    assert Path(result['output_jpeg']).is_file()
    assert Path(result['histogram_path']).is_file()
    assert all(item['population'] == subject for item in result['histogram_series'])
    assert 'recomposition' not in result and 'recomposed_histogram' not in result
    assert all(Path(item['path']).parent == path.parent / 'histogram_trials'
               for item in result['histogram_series'])


@pytest.mark.parametrize('cls', [StarStretchProcessor, StarlessStretchProcessor])
def test_branch_rejects_invalid_data_and_source_report_collision(
    layer_source: Path, tmp_path: Path, cls: type[LayerStretchProcessor],
) -> None:
    """Une entrée invalide ou un rapport sur la source échoue sans la modifier."""
    before = layer_source.read_bytes()
    data = fits.getdata(layer_source)
    backend = ComputeBackend('cpu')
    with pytest.raises(ValueError, match='source'):
        cls().process(data, layer_source, layer_source, backend, argparse.Namespace())
    data[0, 0, 0] = np.nan
    with pytest.raises(ValueError):
        cls().process(data, layer_source, tmp_path / 'invalid.json', backend, argparse.Namespace())
    assert layer_source.read_bytes() == before
    assert not (tmp_path / 'invalid.json').exists()


@pytest.mark.parametrize('cls', [StarStretchProcessor, StarlessStretchProcessor])
def test_branch_closes_its_collector_after_failure(
    layer_source: Path, tmp_path: Path, cls: type[LayerStretchProcessor],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L'échec d'une branche ne laisse pas son cache JPEG temporaire ouvert."""
    collectors = []

    def fail(
        self: LayerStretchProcessor, data: np.ndarray, source: Path, path: Path,
        backend: ComputeBackend, args: argparse.Namespace, recorder: HistogramDiagnostics,
    ) -> tuple[np.ndarray, JSONReport]:
        """Ouvre une capture puis simule une panne moteur locale."""
        recorder.capture({}, data)
        collectors.append(recorder)
        raise OSError('branch failure')

    monkeypatch.setattr(cls, 'render_layer', fail)
    with pytest.raises(OSError, match='branch failure'):
        cls().process(fits.getdata(layer_source), layer_source, tmp_path / 'failure.json',
                      ComputeBackend('cpu'), argparse.Namespace(ghs_save_trial_jpegs=True))
    assert collectors and all(item._cache is None for item in collectors)
    assert not (tmp_path / 'failure.json').exists()


def test_configuration_binds_disjoint_independent_branch_snapshots(tmp_path: Path) -> None:
    """Les instances conservent leurs paramètres propres après une nouvelle résolution."""
    config = Config(tmp_path / 'config.json')
    parser = argparse.ArgumentParser()
    config.register(AutoGHSFinalRenderer, parser)
    args = config.parse_args(parser, ['--star-strength', '2', '--ghs-final-strength', '.4',
                                      '--star-noise-sigma', '8', '--star-boost-fraction', '.4',
                                      '--star-stretch-max-width-ratio', '1.3',
                                      '--ghs-save-trial-jpegs'])
    config.bind_parameters(args)
    first_star, first_background = StarStretchProcessor(), StarlessStretchProcessor()
    args = config.parse_args(parser, ['--star-strength', '4', '--ghs-final-strength', '.8'])
    config.bind_parameters(args)
    second_star, second_background = StarStretchProcessor(), StarlessStretchProcessor()
    assert first_star._get_args().star_strength == 2
    assert first_star._get_args().star_noise_sigma == 8
    assert first_star._get_args().star_boost_fraction == .4
    assert first_star._get_args().star_stretch_max_width_ratio == 1.3
    assert second_star._get_args().star_strength == 4
    assert first_background._get_args().ghs_final_strength == .4
    assert second_background._get_args().ghs_final_strength == .8
    assert not hasattr(first_star._get_args(), 'ghs_final_strength')
    assert not hasattr(first_background._get_args(), 'star_strength')
    assert not hasattr(first_background._get_args(), 'star_noise_sigma')
    assert not hasattr(first_background._get_args(), 'star_boost_fraction')
    assert not hasattr(first_background._get_args(), 'star_stretch_max_width_ratio')
    assert first_star._get_args().ghs_save_trial_jpegs
    assert first_background._get_args().ghs_save_trial_jpegs
    assert not second_star._get_args().ghs_save_trial_jpegs
