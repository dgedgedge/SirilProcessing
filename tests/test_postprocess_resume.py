"""Reprise de la même commande à partir de l'entrée réelle d'AutoGHS."""

import argparse
import json
from pathlib import Path

from astropy.io import fits
import numpy as np
import pytest

from lib.final_render import AutoGHSFinalRenderer
from lib.postprocessing import PostProcessingProcessor
from lib.postprocess_resume import PostProcessResume


def history(tmp_path: Path) -> tuple[Path, Path, Path, dict]:
    """Prépare un rapport complet où l'entrée initiale diffère du fond préparé."""
    source, prepared, report = (tmp_path / name for name in
                                ('source.fits', 'restored.fits', 'source_postprocess.json'))
    fits.writeto(source, np.full((3, 100, 120), .01, dtype='f4'))
    fits.writeto(prepared, np.full((3, 100, 120), .02, dtype='f4'))
    results = {'gradient': {'output_image': str(prepared)},
               'restoration': {'output_image': str(prepared), 'audit': 'preserved'},
               'autoghs': {'image_path': str(prepared)}, 'annotation': {}}
    report.write_text(json.dumps(results))
    PostProcessResume.save(source, report, results)
    return source, prepared, report, results


def test_resume_same_input_and_arguments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """L'entrée CLI reste la source et seul le dernier traitement reçoit le FITS préparé."""
    source, prepared, report, previous = history(tmp_path)
    rendered = tmp_path / 'render.fits'
    fits.writeto(rendered, np.full((3, 100, 120), .15, dtype='f4'), fits.Header({'SPNLIN': True}))
    calls = []

    def render(
        self: AutoGHSFinalRenderer, input_path: Path, output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> dict:
        """Vérifie l'entrée reprise et les réglages inchangés de la commande."""
        assert input_path == prepared
        assert args.ghs_final_strength == .8
        assert args.gradient_smoothing == .75
        calls.append(input_path)
        return {'image_path': str(input_path), 'output_image': str(rendered)}

    monkeypatch.setattr(AutoGHSFinalRenderer, 'post_process', render)
    settings = argparse.Namespace(autoghs_only=True, ghs_final_strength=.8, gradient_smoothing=.75,
                                  enable_final_color=False, final_color_explicit=True)
    for _ in range(2):
        result = PostProcessingProcessor().post_process(source, report, settings)
        assert result['image_path'] == str(source)
        assert result['treatments']['restoration'] == previous['restoration']
        assert result['treatments']['autoghs']['image_path'] == str(prepared)
    assert calls == [prepared, prepared]
    assert PostProcessResume.load(source, report)[0] == prepared


@pytest.mark.parametrize('changed', ['source', 'prepared', 'missing', 'nonlinear'])
def test_invalid_resume_fails_before_render(
    tmp_path: Path, changed: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un historique absent ou périmé n'entraîne aucun nouveau calcul."""
    source, prepared, report, _ = history(tmp_path)
    original_report = report.read_bytes()
    if changed == 'missing':
        report.unlink()
    elif changed == 'nonlinear':
        with fits.open(prepared, mode='update') as hdul:
            hdul[0].header['SPNLIN'] = True
        PostProcessResume.save(source, report, json.loads(report.read_text()))
    else:
        target = source if changed == 'source' else prepared
        target.touch()
    with pytest.raises((ValueError, FileNotFoundError)):
        PostProcessResume.load(source, report)
    if report.exists():
        assert report.read_bytes() == original_report


def test_legacy_report_uses_explicit_autoghs_input(tmp_path: Path) -> None:
    """Les rapports antérieurs contenant image_path restent reprenables."""
    source, prepared, report, _ = history(tmp_path)
    PostProcessResume.manifest_path(report).unlink()
    assert PostProcessResume.load(source, report)[0] == prepared


def test_single_exposure_final_image_provenance(tmp_path: Path) -> None:
    """Un stack à une seule pose est retrouvé même sans nom *_combined.fits."""
    source, _, _, _ = history(tmp_path)
    roots = [tmp_path / 'session']
    PostProcessResume.save_final_images(tmp_path, roots, False, [source])
    assert PostProcessResume.final_images(tmp_path, tmp_path, roots, None) == [source]
    with pytest.raises(ValueError):
        PostProcessResume.final_images(tmp_path, tmp_path, [tmp_path / 'other'], None)
