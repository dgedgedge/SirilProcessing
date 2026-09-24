"""
Tests pour le module de postprocessing.
"""

import sys
import os
from pathlib import Path

import pytest
import numpy as np
from astropy.io import fits

# Ajouter le répertoire parent au path
script_dir = os.path.dirname(os.path.abspath(__file__))
project_dir = os.path.dirname(script_dir)
sys.path.insert(0, project_dir)

from lib.postprocessor import (
    PostProcessor,
    PostProcessingConfig,
    StackingConfig,
    GradientConfig,
    MosaicConfig
)


def _create_test_fits_file(path, data=None, header=None):
    if data is None:
        data = np.random.randint(1000, 5000, size=(50, 50), dtype=np.uint16)
    if header is None:
        header = fits.Header()
        header['IMAGETYP'] = 'LIGHT'
        header['EXPTIME'] = 180.0
        header['CCD-TEMP'] = -10.0
    hdu = fits.PrimaryHDU(data=data, header=header)
    hdu.writeto(path, overwrite=True)
    return path


def test_post_processing_config_defaults():
    config = PostProcessingConfig()
    assert config.stacking.method == "average"
    assert config.stacking.rejection == "sigma"
    assert config.gradient.extract_gradient == False
    assert config.gradient.min_order == 1
    assert config.gradient.max_order == 3
    assert config.mosaic.create_mosaic == False


def test_stacking_config_to_dict():
    config = StackingConfig(method="median", rejection="sigma", rejection_low=2.0, rejection_high=2.0)
    result = config.to_dict()
    assert result['method'] == "median"
    assert result['rejection'] == "sigma"
    assert result['rejection_low'] == 2.0
    assert result['rejection_high'] == 2.0


def test_gradient_config_to_dict():
    config = GradientConfig(extract_gradient=True, min_order=1, max_order=3, n_points=100)
    result = config.to_dict()
    assert result['extract_gradient'] == True
    assert result['min_order'] == 1
    assert result['max_order'] == 3
    assert result['n_points'] == 100
    assert 'output_dir' not in result


def test_post_processor_initialization(tmp_path):
    post_processor = PostProcessor(work_dir=tmp_path / "work", output_dir=tmp_path / "output")
    assert post_processor.work_dir == tmp_path / "work"
    assert post_processor.output_dir == tmp_path / "output"
    assert post_processor.config is not None


def test_post_processor_get_summary():
    post_processor = PostProcessor(work_dir="/tmp/work", output_dir="/tmp/output")
    post_processor.stack_outputs = {
        Path("/path/to/target1"): Path("/path/to/target1_combined.fit"),
        Path("/path/to/target2"): Path("/path/to/target2_combined.fit")
    }
    post_processor.gradient_reports = [{'results': {'order_1': {'r_squared': 0.95}}}, {'results': {'order_1': {'r_squared': 0.92}}}]
    post_processor.mosaic_result = Path("/path/to/mosaic.fit")
    summary = post_processor.get_summary()
    assert summary['stack_outputs_count'] == 2
    assert summary['gradient_reports_count'] == 2
    assert summary['mosaic_created'] == True
