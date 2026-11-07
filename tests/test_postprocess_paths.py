"""Isolation des dossiers de restauration et protection des fichiers sources."""
from pathlib import Path

import pytest

from lib.postprocess_paths import reset_restoration_work_dir


@pytest.mark.parametrize('relative,expected', [
    ('report.json', 'report_work'),
    ('04_restoration/restoration.json', '04_restoration/01_chains'),
    ('04_restoration/01_chains/01_denoise_siril.json',
     '04_restoration/01_chains/01_denoise_siril'),
    ('04_restoration/01_chains/02_sharpen_cosmic_clarity.json',
     '04_restoration/01_chains/02_sharpen_cosmic_clarity'),
    ('04_restoration/02_denoise/denoise_graxpert.json',
     '04_restoration/02_denoise/denoise_graxpert'),
    ('04_restoration/01_deconvolution/deconvolution_siril.json',
     '04_restoration/01_deconvolution/deconvolution_siril'),
])
def test_restoration_work_is_direct_and_protects_sources(
    tmp_path: Path, relative: str, expected: str,
) -> None:
    """Une relance nettoie son dossier, conserve les voisins et refuse son entrée."""
    (tmp_path / '01_calibration').mkdir()
    source = tmp_path / 'source.fits'
    source.write_bytes(b'source')
    report = tmp_path / relative
    work = reset_restoration_work_dir(report, source)
    assert work == tmp_path / expected
    old = work / '001/obsolete.fits'
    old.parent.mkdir()
    old.write_bytes(b'obsolete')
    report.write_text('{}')
    neighbor = report.with_name('other.fits')
    neighbor.write_bytes(b'other')
    assert reset_restoration_work_dir(report, source) == work
    assert not old.parent.exists()
    assert report.read_text() == '{}'
    assert neighbor.read_bytes() == b'other'
    assert source.read_bytes() == b'source'
    protected = work / 'source.fits'
    protected.write_bytes(b'protected')
    with pytest.raises(ValueError, match='réinitialisé'):
        reset_restoration_work_dir(report, protected)
    assert protected.read_bytes() == b'protected'


def test_prepared_operation_directory_keeps_masks_and_rejects_wrong_paths(
    tmp_path: Path,
) -> None:
    """Le moteur réutilise le dossier préparé sans effacer ses masques."""
    work = tmp_path / '01_denoise_siril'
    work.mkdir()
    mask = work / 'masks.fits'
    mask.write_bytes(b'masks')
    report = work / '01_denoise_siril.json'
    source = tmp_path / 'source.fits'
    source.write_bytes(b'source')
    assert reset_restoration_work_dir(
        report, source, prepared_directory=work,
    ) == work
    assert mask.read_bytes() == b'masks'
    with pytest.raises(ValueError, match='incompatible'):
        reset_restoration_work_dir(report, source, prepared_directory=tmp_path)
    with pytest.raises(ValueError, match='incompatible'):
        reset_restoration_work_dir(report, mask, prepared_directory=work)
    assert mask.read_bytes() == b'masks'


@pytest.mark.parametrize('engine', ['denoise', 'sharpen'])
def test_siril_execution_uses_prepared_operation_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: str,
) -> None:
    """Les moteurs Siril rangent scripts et candidats à côté des masques préparés."""
    import argparse
    from unittest.mock import Mock

    import numpy as np
    from astropy.io import fits
    from lib.deconvolution import _DeconvolutionExecution
    from lib.denoising import _DenoisingExecution

    monkeypatch.setattr('lib.deconvolution.Siril', Mock())
    source = tmp_path / 'source.fits'
    fits.writeto(source, np.ones((32, 32), dtype='f4'))
    work = tmp_path / f'01_{engine}_siril'
    work.mkdir()
    mask = work / 'masks.fits'
    mask.write_bytes(b'masks')
    args = argparse.Namespace(_restoration_work_dir=work)
    cls = _DenoisingExecution if engine == 'denoise' else _DeconvolutionExecution
    execution = cls(source, work / f'{work.name}.json', args)
    assert execution.work == work
    assert mask.read_bytes() == b'masks'
    assert not (work / work.name).exists()
