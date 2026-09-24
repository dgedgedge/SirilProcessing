"""Acceptation du débruitage et conservation de l'entrée en cas de flou."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from astropy.io import fits

from lib.denoising import post_process


@pytest.mark.parametrize('noise,width,status', [
    (.8, 1.01, 'accepted'), (1.01, 1, 'rejected'), (.7, 1.08, 'rejected'),
])
def test_denoise_validation_and_handoff(tmp_path, monkeypatch, noise, width, status):
    source = tmp_path/'input.fit'
    fits.writeto(source, np.ones((80, 80), dtype='float32'))
    original = source.read_bytes()
    def run(script, work, script_name):
        assert 'denoise -vst -mod=0.5' in script
        assert '-nocosmetic' not in script
        assert 'set32bits' in script
        fits.writeto(Path(work)/'candidate.fit', fits.getdata(source)*.99)
        return True
    monkeypatch.setattr('lib.denoising.create_siril_from_args',
                        lambda args: SimpleNamespace(run_siril_script=run))
    monkeypatch.setattr('lib.denoising.compare_star_catalogs', lambda *args:
                        dict(status='validated', matched_count=20, pairs=[],
                             before_fwhm_px={'mean': 4}, after_fwhm_px={'mean': 4*width},
                             paired_ratio={'median': width}, before_roundness={'mean': .9},
                             after_roundness={'mean': .9}))
    monkeypatch.setattr('lib.denoising.artifact_metrics', lambda *args:
                        dict(max_noise_ratio=noise, max_ring_fraction=0))
    result = post_process(source, tmp_path/'03_denoise.json')
    assert result['status'] == status
    assert source.read_bytes() == original
    assert Path(result['output_image']) == (tmp_path/'03_denoise.fit' if status == 'accepted' else source)
    assert (tmp_path/'03_denoise.fit').exists() == (status == 'accepted')


def test_failed_siril_is_reported(tmp_path, monkeypatch):
    source = tmp_path/'input.fit'
    fits.writeto(source, np.ones((30, 30)))
    monkeypatch.setattr('lib.denoising.create_siril_from_args',
                        lambda args: SimpleNamespace(run_siril_script=Mock(return_value=False)))
    with pytest.raises(RuntimeError, match='Échec Siril'):
        post_process(source, tmp_path/'report.json')
    assert '"status": "failed"' in (tmp_path/'report.json').read_text()
