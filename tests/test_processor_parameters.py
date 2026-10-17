"""Instantanés de classe, hiérarchie et isolation des traitements configurés."""
import argparse
from pathlib import Path
from unittest.mock import Mock

from lib.config import Config
from lib.postprocess import NoiseReductionProcessor, GradientExtractor, _PostProcessorSequence
from lib.cosmic_clarity.processors import CosmicClarityDenoiseProcessor


def test_chain_mode_defaults_and_configuration_priority(tmp_path: Path) -> None:
    """Les chaînes sont actives par défaut ; configuration et CLI restent prioritaires."""
    config = Config(tmp_path / 'settings.json')
    parser = argparse.ArgumentParser()
    config.register(_PostProcessorSequence, parser)
    assert config.parse_args(parser, []).restoration_compare_chains is True
    disabled = config.parse_args(parser, ['--no-restoration-compare-chains'])
    assert disabled.restoration_compare_chains is False
    config.set('restoration_compare_chains', False)
    parser = argparse.ArgumentParser()
    config.register(_PostProcessorSequence, parser)
    assert config.parse_args(parser, []).restoration_compare_chains is False
    enabled = config.parse_args(parser, ['--restoration-compare-chains'])
    assert enabled.restoration_compare_chains is True


def test_scoped_snapshot_and_overrides(tmp_path, monkeypatch):
    """Les instances capturent leurs valeurs et ignorent les liaisons suivantes."""
    config = Config(tmp_path / 'settings.json')
    parser = argparse.ArgumentParser()
    config.register(_PostProcessorSequence, parser)
    args = config.parse_args(parser, ['--denoise-modulation', '0.25'])
    config.bind_parameters(args)
    first = NoiseReductionProcessor()
    args.denoise_modulation = 0.8
    config.bind_parameters(args)
    second = NoiseReductionProcessor()
    assert first._get_args().denoise_modulation == 0.25
    assert second._get_args().denoise_modulation == 0.8
    assert not hasattr(first._get_args(), 'gradient_method')
    first.set_from_args(argparse.Namespace(
        denoise_modulation=0.4, enable_clarity=False,
        enable_graxpert=False, quality_mode="legacy",
    ))
    delegate = Mock(return_value={})
    monkeypatch.setattr('lib.denoising.SirilNoiseReductionProcessor._execute', delegate)
    first.post_process(tmp_path / 'image.fit', tmp_path / 'report.json')
    assert delegate.call_args.args[2].denoise_modulation == 0.4
    first.post_process(
        tmp_path / 'image.fit', tmp_path / 'report.json',
        argparse.Namespace(
            denoise_modulation=0.6, enable_clarity=False,
            enable_graxpert=False, quality_mode="legacy",
        ),
    )
    assert delegate.call_args.args[2].denoise_modulation == 0.6
    assert first._get_args().denoise_modulation == 0.4


def test_late_cosmic_children_and_layout(tmp_path):
    """Le moteur tardif reçoit couleur, seuils communs et services Siril."""
    config = Config(tmp_path / 'settings.json')
    config.set('cosmic_denoise_amount', 0.3)
    parser = argparse.ArgumentParser()
    config.register(_PostProcessorSequence, parser)
    parser.add_argument('--siril-path', default='/example/siril')
    args = config.parse_args(parser, ['--deconvolution-max-rings', '0.05'])
    args.postprocess_steps_dir = tmp_path / 'steps'
    config.bind_parameters(args)
    sequence = _PostProcessorSequence()
    denoise = next(child for child in sequence.processors
                   if isinstance(child, NoiseReductionProcessor))
    cosmic = denoise.cosmic_processor()()
    assert cosmic._get_args().cosmic_denoise_amount == 0.3
    assert cosmic._get_args().deconvolution_max_rings == 0.05
    assert cosmic._get_args().siril_path == '/example/siril'
    assert sequence._get_args().postprocess_steps_dir == tmp_path / 'steps'
    assert not hasattr(GradientExtractor()._get_args(), 'cosmic_denoise_amount')


def test_legacy_instance_constructor_defaults(tmp_path):
    """L’enregistrement d’une instance conserve les réglages du constructeur."""
    config = Config(tmp_path / 'settings.json')
    parser = argparse.ArgumentParser()
    treatment = GradientExtractor(method='polynomial', sample_fraction=0.15)
    config.register(treatment, parser)
    args = config.parse_args(parser, [])
    assert args.gradient_method == 'polynomial'
    assert args.gradient_sample_fraction == 0.15


def test_bound_sequence_executes_with_saved_activation(tmp_path):
    """Une séquence configurée par classe respecte les activations et écrit le JSON."""
    import json

    config = Config(tmp_path / 'settings.json')
    for prefix in ('gradient', 'photometry', 'deconvolution', 'denoise', 'autoghs'):
        config.set(f'enable_{prefix}', False)
    config.set('enable_clarity', False)
    parser = argparse.ArgumentParser()
    config.register(_PostProcessorSequence, parser)
    args = config.parse_args(parser, [])
    args.postprocess_steps_dir = tmp_path / 'steps'
    config.bind_parameters(args)
    sequence = _PostProcessorSequence()
    report = tmp_path / 'report.json'
    assert sequence.post_process(tmp_path / 'unused.fits', report) == {}
    assert json.loads(report.read_text()) == {}
