"""Diagnostics indépendants, orchestration récursive et durées sans changer les mesures."""
from lib.siril_api import format_commands as _format_commands, SirilCommand as _SirilCommand

import argparse
from itertools import count
import json
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest
from astropy.io import fits

from lib.config import Config
from lib.processor import processor
from lib.quality_processors import (
    ArtifactQualityProcessor, DiffuseSignalProcessor, GeometryQualityProcessor,
    QualityContext, ReferenceEvaluationProcessor, ReferencePreparationProcessor,
    RestorationEvaluationProcessor, SaturationQualityProcessor, SkyNoiseProcessor,
    StellarFluxProcessor, SkyTileProcessor,
)
from lib.quality_star_processors import StarComparisonProcessor
from lib.restoration_quality import QualityReference, normalized_planes


@pytest.fixture
def context(tmp_path: Path) -> QualityContext:
    """Construit un ciel connu dont le candidat divise exactement le bruit par deux."""
    noise = np.random.default_rng(42).normal(0, .001, (192, 192))
    source, candidate = tmp_path / 'source.fits', tmp_path / 'candidate.fits'
    fits.writeto(source, (.1 + noise).astype('float32'))
    fits.writeto(candidate, (.1 + .5 * noise).astype('float32'))
    reference = QualityReference(source)
    return QualityContext(source, candidate, reference.data, normalized_planes(candidate), reference)


def test_independent_noise_processor_preserves_science_and_sources(
    context: QualityContext, tmp_path: Path,
) -> None:
    """La feuille seule mesure le gain attendu, avec rapport et pixels inchangés."""
    source_bytes = context.source.read_bytes()
    candidate_bytes = context.candidate_path.read_bytes()
    report = tmp_path / 'noise.json'
    step = SkyNoiseProcessor(context)
    result = step.post_process(context.candidate_path, report)
    assert isinstance(step, processor)
    assert result['max_noise_ratio'] == pytest.approx(.5, abs=1e-5)
    assert result['status'] == 'PASS'
    assert result['processing']['processor'] == 'SkyNoiseProcessor'
    assert result['processing']['children'] == []
    assert json.loads(report.read_text()) == result
    assert context.source.read_bytes() == source_bytes
    assert context.candidate_path.read_bytes() == candidate_bytes


def test_reference_preparation_has_separate_mask_and_tile_processors(context: QualityContext) -> None:
    """La préparation unique expose ses deux phases sans rejouer les cartes ensuite."""
    audit = context.reference.preparation_processing
    assert audit['processor'] == 'ReferencePreparationProcessor'
    assert [entry['stage'] for entry in audit['children']] == ['masks', 'tiles']
    assert all(entry['duration_seconds'] >= 0 for entry in audit['children'])
    assert context.reference.data.flags.writeable is False
    assert context.reference.sky_mask.flags.writeable is False


def test_reference_orchestrator_has_distinct_timings_and_reuses_arrays(
    context: QualityContext, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chaque feuille est appelée une fois avec les mêmes données, sans relire les plans."""
    ticks = count()
    monkeypatch.setattr('lib.logging_utils.perf_counter', lambda: float(next(ticks)))
    loader = Mock(side_effect=AssertionError('Les plans partagés ne doivent pas être relus'))
    monkeypatch.setattr('lib.restoration_quality.normalized_planes', loader)
    step = ReferenceEvaluationProcessor(context)
    for _ in range(2):
        result = step.post_process(context.candidate_path, None)
        tree = result['processing']
        assert [child['stage'] for child in tree['children']] == [
            'geometry', 'noise', 'diffuse', 'stellar_flux', 'clipping',
        ]
        assert [child['duration_seconds'] for child in tree['children']] == [1] * 5
        assert tree['duration_seconds'] == 11
        assert result['noise']['max_noise_ratio'] == pytest.approx(.5, abs=1e-5)
        assert len(tree['children']) == 5
    loader.assert_not_called()


def test_unknown_noise_does_not_skip_other_independent_diagnostics(
    context: QualityContext, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une mesure inconnue n'empêche pas les feuilles indépendantes de s'exécuter."""
    monkeypatch.setattr(SkyNoiseProcessor, '_process', lambda *args: {
        'status': 'UNKNOWN', 'max_noise_ratio': None,
    })
    result = ReferenceEvaluationProcessor(context).post_process(context.candidate_path, None)
    assert result['noise']['max_noise_ratio'] is None
    assert result['diffuse']['status'] == 'UNKNOWN'
    assert result['stellar_flux']['status'] == 'UNKNOWN'
    assert result['clipping']['per_channel'][0]['status'] == 'PASS'
    assert len(result['processing']['children']) == 5


def test_recursive_failure_keeps_child_time_and_original_exception(
    context: QualityContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L'échec est audité à chaque niveau sans exécuter d'autres étapes après l'erreur."""
    error = RuntimeError('noise failed')
    monkeypatch.setattr(SkyNoiseProcessor, '_process', Mock(side_effect=error))
    diffuse = Mock(side_effect=AssertionError('Une erreur fatale doit interrompre la séquence'))
    monkeypatch.setattr(DiffuseSignalProcessor, '_process', diffuse)
    report = tmp_path / 'failed.json'
    with pytest.raises(RuntimeError, match='noise failed') as raised:
        ReferenceEvaluationProcessor(context).post_process(context.candidate_path, report)
    assert raised.value is error
    audit = json.loads(report.read_text())
    assert audit['status'] == 'failed'
    assert audit['processing']['execution_status'] == 'failed'
    assert len(audit['processing']['children']) == 2
    failed = audit['processing']['children'][-1]
    assert failed['stage'] == 'noise' and failed['execution_status'] == 'failed'
    assert failed['duration_seconds'] >= 0
    diffuse.assert_not_called()


@pytest.mark.parametrize('star_status', ['validated', 'insufficient_matches'])
def test_complete_orchestrator_recurses_and_caches_reference_catalog(
    context: QualityContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    star_status: str,
) -> None:
    """Le même orchestrateur prépare puis appelle les branches stellaire et pixels."""
    scripts = []

    def detect(script: str, directory: str, name: str) -> bool:
        """Simule seulement les catalogues, en gardant les vrais calculs sur tuiles."""
        script = _format_commands(script)
        scripts.append(script)
        for line in script.splitlines():
            if line.startswith('findstar '):
                (Path(directory) / line.split('-out=')[1]).write_text('catalog')
        return True

    monkeypatch.setattr('lib.siril.Siril', lambda **kwargs: Mock(run_commands=detect))
    monkeypatch.setattr(StarComparisonProcessor, '_process', lambda *args: {
        'status': star_status, 'pairs': [],
    })
    unprepared = QualityContext(
        context.source, context.candidate_path, context.before, context.candidate,
    )
    step = RestorationEvaluationProcessor(unprepared, tmp_path / 'final')
    first = step.post_process(context.candidate_path, None)
    assert first['reference_preparation_reused'] is False
    assert [child['stage'] for child in first['processing']['children']] == [
        'reference_preparation', 'stellar_assessment', 'artifacts',
    ]
    stellar = first['processing']['children'][1]
    assert [child['stage'] for child in stellar['children']] == ['star_detection', 'star_comparison']
    pixels = first['processing']['children'][2]
    assert [child['stage'] for child in pixels['children']] == ['legacy_noise', 'rings', 'reference_evaluation']
    assert len(pixels['children'][2]['children']) == 5
    assert first['artifacts']['max_noise_ratio'] == pytest.approx(.5, abs=1e-5)
    second = step.post_process(context.candidate_path, None)
    assert second['reference_preparation_reused'] is True
    assert len(second['processing']['children']) == 2
    assert len(scripts) == 2
    assert sum('reference.tsv' in script for script in scripts) == 1


def test_processor_tree_declares_and_binds_common_options(tmp_path: Path) -> None:
    """Config recense les enfants récursifs sans instancier les traitements."""
    config = Config(tmp_path / 'config.json')
    parser = argparse.ArgumentParser()
    config.register(RestorationEvaluationProcessor, parser)
    args = config.parse_args(parser, ['--quality-min-noise-gain', '.08'])
    config.bind_parameters(args)
    assert args.quality_min_noise_gain == .08
    assert GeometryQualityProcessor in config._parameter_classes
    assert SkyNoiseProcessor in config._parameter_classes
    assert ReferencePreparationProcessor in config._parameter_classes
    assert StellarFluxProcessor in config._parameter_classes
    assert SaturationQualityProcessor in config._parameter_classes


def test_tile_processor_can_run_on_prepared_read_only_masks(context: QualityContext) -> None:
    """La sélection des tuiles seule ne modifie pas une référence déjà scellée."""
    original = context.reference.tiles
    result = SkyTileProcessor(context).post_process(context.candidate_path, None)
    tiles = tuple(
        (slice(tile['y_start'], tile['y_stop']), slice(tile['x_start'], tile['x_stop']))
        for tile in result['tiles']
    )
    assert tiles == original
    assert result['tile_count'] == len(original)
    assert context.reference.tiles is original


def test_noise_sampling_reports_effective_tiles_and_keeps_quadrants(context: QualityContext) -> None:
    """Le plafond réduit les calculs, pas les masques ; les tuiles sont les mêmes en RGB."""
    before_tiles = context.reference.tiles
    options = argparse.Namespace(quality_max_noise_tiles=4, quality_noise_batch_size=2, compute_backend='cpu')
    first = SkyNoiseProcessor(context).post_process(context.candidate_path, None, options)
    second = SkyNoiseProcessor(context).post_process(context.candidate_path, None, options)
    assert first['sampling'] == second['sampling']
    assert first['sampling']['available_tiles'] == len(before_tiles)
    assert first['sampling']['evaluated_tiles'] == 4
    assert first['sampling']['quadrant_count'] == 4
    assert first['backend']['name'] == 'cpu'
    assert first['backend']['batch_size'] == 2
    assert first['backend']['batches_per_channel'] == 2
    assert first['per_channel'][0]['sample_count'] == 4
    assert first['max_noise_ratio'] == pytest.approx(.5, abs=1e-5)
    assert context.reference.tiles is before_tiles


def test_zero_noise_tile_limit_keeps_exhaustive_evaluation(context: QualityContext) -> None:
    """Le mode sans plafond conserve toutes les tuiles de ciel disponibles."""
    result = SkyNoiseProcessor(context).post_process(context.candidate_path, None, argparse.Namespace(
        quality_max_noise_tiles=0, compute_backend='cpu',
    ))
    assert result['sampling']['evaluated_tiles'] == len(context.reference.tiles)
    assert result['sampling']['method'] == 'all'


@pytest.mark.parametrize('maximum,batch', [(-1, 32), (2, 32), (256, 0), (256, 257)])
def test_noise_settings_are_validated_before_preparing_reference(
    tmp_path: Path, maximum: int, batch: int,
) -> None:
    """Les réglages incohérents ne permettent pas un sous-échantillon trop petit."""
    source = tmp_path / 'image.fits'
    fits.writeto(source, np.ones((32, 32), dtype='float32'))
    with pytest.raises(ValueError, match='quality-'):
        QualityReference(source, argparse.Namespace(
            quality_max_noise_tiles=maximum, quality_noise_batch_size=batch,
        ))


def test_noise_options_cli_json_precedence_and_explicit_save(tmp_path: Path) -> None:
    """Le processeur propriétaire déclare ses défauts et respecte la rémanence Config."""
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'quality_max_noise_tiles': 64, 'quality_noise_batch_size': 8}))
    config = Config(path)
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(RestorationEvaluationProcessor, parser)
    assert config.parse_args(parser, []).quality_max_noise_tiles == 64
    args = config.parse_args(parser, ['--quality-max-noise-tiles', '128', '--quality-noise-batch-size', '16'])
    config.bind_parameters(args)
    assert args.quality_max_noise_tiles == 128
    assert config.save_requested(args)
    assert json.loads(path.read_text())['quality_max_noise_tiles'] == 64
    args.save_config = True
    assert config.save_requested(args)
    saved = json.loads(path.read_text())
    assert saved['quality_max_noise_tiles'] == 128 and saved['quality_noise_batch_size'] == 16


def test_noise_gpu_execution_error_does_not_restart_on_cpu(
    context: QualityContext, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Après choix du backend, une panne est propagée et chronométrée sans reprise."""
    from types import SimpleNamespace

    backend_calls = []

    def backend(requested: str, required_bytes: int, **kwargs: str) -> SimpleNamespace:
        """Simule un précontrôle GPU réussi avant une panne du calcul."""
        backend_calls.append((requested, required_bytes))
        return SimpleNamespace(name='cuda', reason='test', report=lambda: {'name': 'cuda'},
                               statistics=calculation)

    monkeypatch.setattr(SkyNoiseProcessor, 'MIN_GPU_TILES', 1)
    monkeypatch.setattr('lib.noise_tiles.NoiseTileBackend', backend)
    failure = RuntimeError('GPU execution failed')
    calculation = Mock(side_effect=failure)
    step = SkyNoiseProcessor(context)
    with pytest.raises(RuntimeError, match='GPU execution failed') as raised:
        step.post_process(context.candidate_path, None, argparse.Namespace(compute_backend='auto'))
    assert raised.value is failure
    assert len(backend_calls) == calculation.call_count == 1
    assert backend_calls[0][0] == 'auto' and backend_calls[0][1] > 0
    assert step.last_processing['execution_status'] == 'failed'


def test_noise_sampling_is_shared_across_rgb_channels(tmp_path: Path) -> None:
    """Le même échantillon mesure les gains connus des trois canaux, sans modifier les FITS."""
    noise = np.random.default_rng(719).normal(0, .001, (192, 192))
    source, candidate = tmp_path / 'rgb.fits', tmp_path / 'candidate.fits'
    fits.writeto(source, np.repeat((.1 + noise)[None], 3, axis=0).astype('float32'))
    fits.writeto(candidate, np.array([.1 + gain * noise for gain in [.4, .6, .8]], dtype='float32'))
    reference = QualityReference(source, argparse.Namespace(quality_max_noise_tiles=4, compute_backend='cpu'))
    result = reference.evaluate(candidate)['noise']
    assert result['sampling']['evaluated_tiles'] == 4
    assert len(result['per_channel']) == 3
    for channel, expected in zip(result['per_channel'], [.4, .6, .8]):
        assert channel['sample_count'] == 4
        assert channel['noise_ratio'] == pytest.approx(expected, abs=1e-5)
    assert result['max_noise_ratio'] == pytest.approx(.8, abs=1e-5)


def test_noise_does_not_turn_flat_sky_into_a_measured_gain(tmp_path: Path) -> None:
    """Le plan constant reste UNKNOWN malgré l'échantillonnage et les calculs par lots."""
    source = tmp_path / 'flat.fits'
    fits.writeto(source, np.full((192, 192), .1, dtype='float32'))
    reference = QualityReference(source, argparse.Namespace(compute_backend='cpu'))
    result = reference.noise(reference.data)
    assert result['status'] == 'UNKNOWN'
    assert result['max_noise_ratio'] is None


@pytest.mark.parametrize('owner', [
    SkyTileProcessor, SkyNoiseProcessor, DiffuseSignalProcessor,
    StellarFluxProcessor, StarComparisonProcessor,
])
def test_options_are_owned_locally_and_collected_recursively(owner: type[processor]) -> None:
    """Une feuille et la racine exposent les mêmes défauts sans doublon."""
    leaf = argparse.ArgumentParser()
    owner.add_arguments(leaf)
    root = argparse.ArgumentParser()
    RestorationEvaluationProcessor.add_arguments(root)
    RestorationEvaluationProcessor.add_arguments(root)
    owner.add_arguments(root)
    defaults = owner.argument_defaults()
    assert defaults
    for destination, value in defaults.items():
        assert getattr(leaf.parse_args([]), destination) == value
        assert getattr(root.parse_args([]), destination) == value
        assert sum(action.dest == destination for action in root._actions) == 1


def test_mask_defaults_are_independent_between_parsers() -> None:
    """Les listes de défauts appartiennent à chaque déclaration de paramètres."""
    from lib.quality_processors import MaskPreparationProcessor

    first, second = argparse.ArgumentParser(), argparse.ArgumentParser()
    MaskPreparationProcessor.add_arguments(first)
    first.parse_args([]).quality_scales.append(99)
    MaskPreparationProcessor.add_arguments(second)
    assert second.parse_args([]).quality_scales == [2, 4, 8, 16, 32]
    assert MaskPreparationProcessor.CONFIG_DEFAULTS['quality_scales'] == [2, 4, 8, 16, 32]


def test_root_collects_new_leaf_option_recursively(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une déclaration ajoutée à une feuille remonte sans modifier l'orchestrateur."""
    original = SkyTileProcessor.add_arguments

    def declare(cls: type[SkyTileProcessor], parser: argparse.ArgumentParser) -> None:
        """Ajoute un témoin au propriétaire des tuiles."""
        original(parser)
        if not cls.has_argument(parser, 'tile_probe'):
            parser.add_argument('--tile-probe', default=17, type=int)

    monkeypatch.setattr(SkyTileProcessor, 'add_arguments', classmethod(declare))
    parser = argparse.ArgumentParser()
    RestorationEvaluationProcessor.add_arguments(parser)
    assert parser.parse_args(['--tile-probe', '23']).tile_probe == 23


def test_recursive_config_resolves_local_owner_and_priorities(tmp_path: Path) -> None:
    """Config identifie la feuille et conserve CLI > JSON > défaut local."""
    config = Config(tmp_path / 'quality.json')
    config.set('quality_tile_size', 32)
    config.set('deconvolution_match_radius', 2.5)
    parser = argparse.ArgumentParser()
    config.register(RestorationEvaluationProcessor, parser)
    saved = config.parse_args(parser, [])
    assert saved.quality_tile_size == 32
    assert saved.deconvolution_match_radius == 2.5
    explicit = config.parse_args(parser, [
        '--quality-tile-size', '48', '--deconvolution-match-radius', '3',
    ])
    assert explicit.quality_tile_size == 48
    assert explicit.deconvolution_match_radius == 3
    assert config._parameter_owner(
        RestorationEvaluationProcessor, 'quality_tile_size',
    ) is SkyTileProcessor
    assert config._parameter_owner(
        RestorationEvaluationProcessor, 'deconvolution_match_radius',
    ) is StarComparisonProcessor
