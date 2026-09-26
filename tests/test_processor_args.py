"""Configuration persistante des traitements par Namespace."""
import argparse
from unittest.mock import Mock

import pytest

from lib.postprocess import (
    GradientExtractor, NoiseReductionProcessor, DeconvolutionProcessor,
    PhotometricColorCalibrator, _PostProcessorSequence,
)


def test_gradient_stored_options_and_temporary_override(tmp_path, monkeypatch):
    treatment = GradientExtractor(method='polynomial')
    args = argparse.Namespace(gradient_sample_fraction=0.12)
    treatment.set_from_args(args)
    args.gradient_sample_fraction = 0.9
    monkeypatch.setattr(GradientExtractor, 'extract_gradient_from_fits',
                        lambda self, *paths: {'fraction': self.sample_fraction, 'final_recommendation': 1})
    monkeypatch.setattr(GradientExtractor, 'correct_gradient', lambda *args: {})
    paths = tmp_path/'input.fit', tmp_path/'report.json'
    assert treatment.post_process(*paths)['fraction'] == 0.12
    assert treatment.post_process(*paths, args=args)['fraction'] == 0.9
    assert treatment.post_process(*paths)['fraction'] == 0.12


@pytest.mark.parametrize('cls,module,option', [
    (NoiseReductionProcessor, 'lib.denoising', 'denoise_modulation'),
    (DeconvolutionProcessor, 'lib.deconvolution', 'deconvolution_iterations'),
])
def test_adapters_forward_stored_options(cls, module, option, monkeypatch, tmp_path):
    delegate = Mock(return_value={})
    monkeypatch.setattr(f'{module}.post_process', delegate)
    treatment = cls()
    treatment.set_from_args(argparse.Namespace(**{option: 7}))
    treatment.post_process(tmp_path/'input.fit', tmp_path/'report.json')
    assert getattr(delegate.call_args.args[2], option) == 7


def test_photometry_uses_stored_options(tmp_path):
    import numpy as np
    from astropy.io import fits
    image = tmp_path/'rgb.fit'
    fits.writeto(image, np.zeros((3, 2, 2)))
    treatment = PhotometricColorCalibrator()
    treatment.set_from_args(argparse.Namespace(photometry_focal=-1))
    with pytest.raises(ValueError, match='photometry-focal'):
        treatment.post_process(image, tmp_path/'report.json')


def test_sequence_remembers_activation_and_configures_children(tmp_path):
    treatment = GradientExtractor(method='polynomial')
    sequence = _PostProcessorSequence([treatment])
    args = argparse.Namespace(enable_gradient=False, gradient_sample_fraction=0.12)
    sequence.set_from_args(args)
    args.enable_gradient = True
    assert sequence.post_process(tmp_path/'unused.fit', tmp_path/'report.json') == {}
    assert treatment._get_args().gradient_sample_fraction == 0.12


def test_processor_uses_shared_options_with_explicit_override(tmp_path, monkeypatch):
    from lib.config import Config
    config = Config(tmp_path / 'config.json')
    treatment = NoiseReductionProcessor()
    parser = argparse.ArgumentParser()
    config.register(treatment, parser)
    config.parse_args(parser, ['--denoise-modulation', '0.25'])
    delegate = Mock(return_value={})
    monkeypatch.setattr('lib.denoising.post_process', delegate)
    paths = tmp_path / 'image.fit', tmp_path / 'report.json'
    treatment.post_process(*paths)
    assert delegate.call_args.args[2].denoise_modulation == 0.25
    treatment.post_process(*paths, args=argparse.Namespace(denoise_modulation=0.8))
    assert delegate.call_args.args[2].denoise_modulation == 0.8
    assert config.get('denoise_modulation') == 0.25
    treatment.set_from_args(argparse.Namespace(denoise_modulation=0.4))
    treatment.post_process(*paths)
    assert delegate.call_args.args[2].denoise_modulation == 0.4


def test_sequence_registered_options_save_and_reload(tmp_path, monkeypatch):
    import json
    from lib.config import Config
    path = tmp_path / 'config.json'
    config = Config(path)
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    sequence = _PostProcessorSequence()
    config.register(sequence, parser)
    args = config.parse_args(parser, ['--disable-gradient', '--denoise-modulation', '0.2',
                                     '--gradient-output', str(tmp_path / 'output'), '-S'])
    assert config.save_requested(args)
    saved = json.loads(path.read_text())
    assert saved['enable_gradient'] is False
    assert saved['denoise_modulation'] == 0.2
    assert 'gradient_output_dir' not in saved
    monkeypatch.setattr(Config, '_instance', None)
    config = Config(path)
    parser = argparse.ArgumentParser()
    config.register(_PostProcessorSequence(), parser)
    loaded = config.parse_args(parser, [])
    assert loaded.enable_gradient is False
    assert loaded.denoise_modulation == 0.2
    overridden = config.parse_args(parser, ['--enable-gradient'])
    assert overridden.enable_gradient is True
