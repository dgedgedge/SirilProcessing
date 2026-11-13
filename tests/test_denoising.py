"""Acceptation du débruitage et conservation de l'entrée en cas de flou."""
from lib.siril_api import format_commands as _format_commands, SirilCommand as _SirilCommand
from pathlib import Path
import json
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from astropy.io import fits

from lib.denoising import SirilNoiseReductionProcessor


@pytest.mark.parametrize('noise,width,status', [
    (.8, 1.01, 'accepted'), (1.01, 1, 'rejected'), (.7, 1.08, 'rejected'),
])
def test_denoise_validation_and_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    noise: float, width: float, status: str,
) -> None:
    """La validation conserve les sources et enregistre les trois durées mesurées."""
    ticks = iter([1.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0])
    monkeypatch.setattr('lib.logging_utils.perf_counter', lambda: next(ticks))
    source = tmp_path/'input.fit'
    fits.writeto(source, np.ones((80, 80), dtype='float32'))
    original = source.read_bytes()
    stale_parent = tmp_path / '03_denoise_work'
    stale_parent.mkdir(parents=True, exist_ok=True)
    (stale_parent / 'stale.txt').write_text('stale')
    def run(script, work, name):
        script = _format_commands(script)
        if name == 'quality':
            assert 'findstar' in script and 'denoise -vst' not in script
            return True
        assert 'findstar' not in script
        assert 'denoise -vst -mod=0.5' in script
        assert '-nocosmetic' not in script
        assert 'set32bits' in script
        fits.writeto(Path(work)/'candidate.fits', fits.getdata(source)*.99)
        return True
    monkeypatch.setattr('lib.denoising.Siril',
                        lambda **kwargs: SimpleNamespace(run_commands=run))
    monkeypatch.setattr('lib.denoising.compare_star_catalogs', lambda *args, **kwargs:
                        dict(status='validated', matched_count=20, pairs=[],
                             before_fwhm_px={'mean': 4}, after_fwhm_px={'mean': 4*width},
                             paired_ratio={'median': width}, before_roundness={'mean': .9},
                             after_roundness={'mean': .9}))
    monkeypatch.setattr('lib.denoising.artifact_metrics', lambda *args, **kwargs:
                        dict(max_noise_ratio=noise, max_ring_fraction=0))
    result = SirilNoiseReductionProcessor().post_process(source, tmp_path/'03_denoise.json')
    assert result['status'] == status
    assert result['timings'] == {
        'siril_seconds': 2.0, 'quality_seconds': 3.0, 'publication_seconds': 1.0,
    }
    assert json.loads((tmp_path / '03_denoise.json').read_text())['timings'] == result['timings']
    assert not (stale_parent / 'stale.txt').exists()
    candidate = Path(result['candidate_image_path'])
    assert candidate.parent == stale_parent
    assert not (candidate.parent / '001').exists()
    assert source.read_bytes() == original
    assert Path(result['output_image']) == (tmp_path/'03_denoise.fits' if status == 'accepted' else source)
    assert (tmp_path/'03_denoise.fits').exists() == (status == 'accepted')


def test_failed_siril_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un calcul Siril en échec laisse son statut et sa durée dans le JSON."""
    ticks = iter([1.0, 4.0])
    monkeypatch.setattr('lib.logging_utils.perf_counter', lambda: next(ticks))
    source = tmp_path/'input.fit'
    fits.writeto(source, np.ones((30, 30)))
    monkeypatch.setattr('lib.denoising.Siril',
                        lambda **kwargs: SimpleNamespace(run_commands=Mock(return_value=False)))
    with pytest.raises(RuntimeError, match='Échec Siril'):
        SirilNoiseReductionProcessor().post_process(source, tmp_path/'report.json')
    assert '"status": "failed"' in (tmp_path/'report.json').read_text()
    assert json.loads((tmp_path / 'report.json').read_text())['timings'] == {'siril_seconds': 3.0}


def test_execution_options_are_independent(tmp_path: Path) -> None:
    """Deux calculs débruitent avec des paramètres et rapports indépendants."""
    import argparse
    from lib.denoising import _DenoisingExecution

    source = tmp_path / 'input.fits'
    fits.writeto(source, np.ones((20, 20), dtype='float32'))
    args = argparse.Namespace(denoise_modulation=.25)
    first = _DenoisingExecution(source, tmp_path / 'first.json', args)
    args.denoise_modulation = .75
    second = _DenoisingExecution(source, tmp_path / 'second.json', args)
    assert first.args.denoise_modulation == .25
    assert second.args.denoise_modulation == .75
    assert '-mod=0.25' in _format_commands(first._build_commands())
    assert '-mod=0.75' in _format_commands(second._build_commands())
    first.result['error'] = 'first only'
    assert 'error' not in second.result
    assert first.work != second.work
