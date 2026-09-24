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
