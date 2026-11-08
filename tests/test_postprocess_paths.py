"""Isolation des dossiers de restauration et protection des fichiers sources."""
from pathlib import Path

import pytest

from lib.postprocess_paths import reset_autoghs_work_dir, reset_restoration_work_dir


@pytest.mark.parametrize('relative,expected', [
    ('report.json', 'report_work'),
    ('04_restoration/restoration.json', '04_restoration'),
    ('04_restoration/01_denoise_siril.json',
     '04_restoration/01_denoise_siril'),
    ('04_restoration/02_sharpen_cosmic_clarity.json',
     '04_restoration/02_sharpen_cosmic_clarity'),
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


def test_numbered_restoration_flattens_work_and_cleans_only_owned_products(
    tmp_path: Path,
) -> None:
    """La racine reste intacte, seuls les anciens produits et essais sont nettoyés."""
    root = tmp_path / '04_restoration'
    root.mkdir()
    (tmp_path / '01_calibration').mkdir()
    source = tmp_path / 'input.fits'
    source.write_bytes(b'original')
    report = root / 'restoration.json'
    report.write_text('{}')
    neighbor = root / 'notes.txt'
    neighbor.write_text('preserved')
    for name in ('01_chains', 'diagnostics', '00_mask_origine', '03_denoise_siril'):
        old = root / name
        old.mkdir()
        (old / 'obsolete.txt').write_text('old')
    assert reset_restoration_work_dir(report, source) == root
    assert sorted(path.name for path in root.iterdir()) == ['notes.txt', 'restoration.json']
    assert neighbor.read_text() == 'preserved'
    assert report.read_text() == '{}'
    assert source.read_bytes() == b'original'


@pytest.mark.parametrize('report_name', ['05_autoghs.json', '05_autoghs/autoghs.json'])
def test_autoghs_stages_are_direct_and_preserve_neighboring_files(
    tmp_path: Path, report_name: str,
) -> None:
    """La relance nettoie ses produits et l'ancienne enveloppe, sans effacer ses voisins."""
    report = tmp_path / report_name
    report.parent.mkdir(parents=True, exist_ok=True)
    source = report.parent / 'input.fits'
    source.write_bytes(b'source')
    report.write_text('{}')
    neighbor = report.parent / 'render.fits'
    neighbor.write_bytes(b'render')
    for name in ('01_separation', '02_stars', '03_ghs', '04_recomposition',
                 '05_diagnostics', f'{report.stem}_work'):
        old = report.parent / name
        old.mkdir()
        (old / 'obsolete.fits').write_bytes(b'old')
    unrelated = report.parent / '06_color'
    unrelated.mkdir()
    (unrelated / 'color.fits').write_bytes(b'color')
    assert reset_autoghs_work_dir(report, source) == report.parent
    assert sorted(path.name for path in report.parent.iterdir()) == sorted([
        report.name, source.name, neighbor.name, unrelated.name,
    ])
    assert source.read_bytes() == b'source'
    assert neighbor.read_bytes() == b'render'
    assert report.read_text() == '{}'
    assert (unrelated / 'color.fits').read_bytes() == b'color'


@pytest.mark.parametrize('subdirectory', [
    '01_separation', '02_stars', '03_ghs', '04_recomposition', '05_diagnostics', 'autoghs_work',
])
def test_autoghs_rejects_source_in_cleaned_products_before_any_removal(
    tmp_path: Path, subdirectory: str,
) -> None:
    """Aucune étape n'est effacée si la source appartient à un dossier nettoyé."""
    first = tmp_path / '01_separation'
    first.mkdir()
    marker = first / 'preserved.fits'
    marker.write_bytes(b'preserved')
    source = tmp_path / subdirectory / 'input.fits'
    source.parent.mkdir(exist_ok=True)
    source.write_bytes(b'source')
    with pytest.raises(ValueError, match='réinitialisé'):
        reset_autoghs_work_dir(tmp_path / 'autoghs.json', source)
    assert source.read_bytes() == b'source'
    assert marker.read_bytes() == b'preserved'


def test_autoghs_cleanup_removes_symlink_without_removing_target(tmp_path: Path) -> None:
    """Le nettoyage retire le lien d'étape, sans suivre sa cible extérieure."""
    target = tmp_path / 'external'
    target.mkdir()
    marker = target / 'preserved.fits'
    marker.write_bytes(b'preserved')
    work = tmp_path / '05_autoghs'
    work.mkdir()
    link = work / '01_separation'
    link.symlink_to(target, target_is_directory=True)
    source = tmp_path / 'input.fits'
    source.write_bytes(b'source')
    assert reset_autoghs_work_dir(work / 'autoghs.json', source) == work
    assert not link.exists()
    assert marker.read_bytes() == b'preserved'
