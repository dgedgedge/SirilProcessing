"""Sorties numérotées, relais sans mosaïque, rapports et reprises cohérentes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from lib.output_layout import OutputLayout
from lib.postprocess import PostProcessingProcessor
from lib.postprocess_resume import PostProcessResume


def test_handoff_is_a_physical_copy_and_preserves_resume_identity(tmp_path: Path) -> None:
    """Le relais uniforme ne modifie ni les pixels ni les dates d'une calibration."""
    layout = OutputLayout(tmp_path / 'M_33')
    layout.create()
    source = layout.calibration / 'stack_combined.fits'
    fits.writeto(source, np.ones((3, 12, 16), dtype='f4'), fits.Header({'OBJECT': 'M33'}))
    original = source.read_bytes()
    result = layout.handoff(source)
    before = PostProcessResume.snapshot(result)
    assert result == layout.root / '02_mosaic/image.fits'
    assert result.read_bytes() == original and not result.is_symlink()
    assert result.stat().st_ino != source.stat().st_ino
    assert fits.getheader(result)['OBJECT'] == 'M33'
    assert layout.handoff(source) == result
    assert PostProcessResume.snapshot(result) == before
    assert source.read_bytes() == original
    assert PostProcessingProcessor.report_path_for(result) == layout.report
    assert PostProcessingProcessor.report_path_for(result, layout.root) == layout.report
    assert {path.name for path in layout.root.iterdir() if path.is_dir()} == {
        '01_calibration', '02_mosaic', '03_photometrie', '04_restoration', '05_autoghs', '06_color',
    }


def test_handoff_rejects_a_symlink_to_raw_input(tmp_path: Path) -> None:
    """Un relais piégé ne permet pas de remplacer un FITS brut."""
    layout = OutputLayout(tmp_path / 'out')
    layout.create()
    raw, calibrated = tmp_path / 'raw.fits', tmp_path / 'calibrated.fits'
    raw.write_bytes(b'raw')
    calibrated.write_bytes(b'calibrated')
    layout.image.symlink_to(raw)
    with pytest.raises(ValueError, match='copie'):
        layout.handoff(calibrated)
    assert raw.read_bytes() == b'raw'


def test_reports_and_manifest_stay_inside_stages(tmp_path: Path) -> None:
    """Les synthèses JSON/TXT et l'index de reprise n'encombrent pas la racine."""
    layout = OutputLayout(tmp_path / 'M31')
    layout.create()
    layout.image.touch()
    roots = [tmp_path / 'north', tmp_path / 'south']
    payload = {'status': 'SUCCESS', 'output_image': str(layout.image)}
    for prefix in ('gradient', 'photometry', 'restoration', 'deconvolution', 'denoise',
                   'autoghs', 'final_color'):
        report = layout.stage_report(prefix)
        layout.write_summary(report, payload)
        assert json.loads(report.read_text()) == payload
        assert 'SUCCESS' in report.with_suffix('.txt').read_text()
        assert report.relative_to(layout.root).parts[0][:2] in ('03', '04', '05', '06')
    PostProcessResume.save_final_images(layout.root, roots, True, [layout.image])
    assert PostProcessResume.final_images(layout.root, tmp_path / 'work', roots, 'M31') == [layout.image]
    assert not list(layout.root.glob('*.json')) and not list(layout.root.glob('*.txt'))
    assert layout.panel(1, True) == layout.calibration / 'part_01'
    assert layout.panel(2, True) == layout.calibration / 'part_02'
    assert layout.panel(1, False) == layout.calibration


def test_interrupted_numbered_history_is_resumable_without_guessing(tmp_path: Path) -> None:
    """Les nouveaux rapports d'étapes peuvent prouver une chaîne interrompue."""
    layout = OutputLayout(tmp_path / 'M33')
    layout.create()
    fits.writeto(layout.image, np.ones((3, 12, 16), dtype='f4')*.01)
    prepared = layout.root / '04_restoration/prepared.fits'
    fits.writeto(prepared, np.ones((3, 12, 16), dtype='f4')*.02)
    layout.write_summary(layout.stage_report('restoration'), {
        'status': 'SUCCESS', 'image_path': str(layout.image), 'output_image': str(prepared),
    })
    layout.write_summary(layout.stage_report('autoghs'), {
        'status': 'ERROR', 'image_path': str(prepared),
    })
    resumed, previous = PostProcessResume.load(layout.image, layout.report)
    assert resumed == prepared and previous['restoration']['output_image'] == str(prepared)


def test_numbered_postprocess_keeps_original_sources_and_resumes_color(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La vraie orchestration range GHS/couleur et reprend leurs couches immuables."""
    from lib.final_render import AutoGHSFinalRenderer
    from lib.postprocess import _PostProcessorSequence

    layout = OutputLayout(tmp_path / 'M_31')
    layout.create()
    yy, xx = np.mgrid[-1:1:48j, -1:1:64j]
    light = .04+.3*np.exp(-5*(xx**2+yy**2))
    rendered = np.array([light*1.02, light, light*.98], dtype='f4')
    fits.writeto(layout.image, rendered*.03)
    source_bytes = layout.image.read_bytes()
    calls = []

    def render(self: AutoGHSFinalRenderer, source: Path, report: Path,
               args: argparse.Namespace | None = None) -> dict:
        """Simule seulement Siril/GHS pour contrôler le protocole et les vrais exports."""
        calls.append(report)
        assert report == layout.stage_report('autoghs')
        starless, stars, image = [report.parent / name for name in ('starless.fits', 'stars.fits', 'rendered.fits')]
        fits.writeto(starless, rendered, fits.Header({'SPNLIN': True, 'SPROLE': 'STARLESS'}), overwrite=True)
        fits.writeto(stars, np.zeros_like(rendered), fits.Header({'SPNLIN': True, 'SPROLE': 'STARS'}), overwrite=True)
        fits.writeto(image, .95*rendered, fits.Header({'SPNLIN': True}), overwrite=True)
        return {'status': 'SUCCESS', 'image_path': str(source), 'output_image': str(image),
                'strategies': {'range': {'starless_image': str(starless)}},
                'stars': {'output_image': str(stars)}, 'parameters': {'ghs_headroom': .95}}

    monkeypatch.setattr(AutoGHSFinalRenderer, 'post_process', render)
    args = argparse.Namespace(enable_gradient=False, enable_photometry=False,
                              enable_deconvolution=False, enable_denoise=False, enable_autoghs=True,
                              enable_final_color=True, final_annotation=False, compute_backend='cpu')
    result = PostProcessingProcessor().post_process(layout.image, layout.report, args)
    assert Path(result['output_image']) == layout.root / 'M_31.fits'
    assert Path(result['output_ghs_image']) == layout.root / '05_autoghs/render.fits'
    assert layout.stage_report('final_color').is_file()
    assert layout.stage_report('final_color').with_suffix('.txt').is_file()
    assert layout.report.with_suffix('.txt').is_file()
    assert layout.image.read_bytes() == source_bytes
    assert not list(layout.root.glob('*.json'))
    original_ghs = (layout.root / '05_autoghs/starless.fits').read_bytes()
    args.color_only = True

    def forbidden(*args: object, **kwargs: object) -> None:
        """La reprise couleur n'exécute aucun moteur amont."""
        raise AssertionError('GHS relancé')

    monkeypatch.setattr(_PostProcessorSequence, 'post_process', forbidden)
    resumed = PostProcessingProcessor().post_process(layout.image, layout.report, args)
    assert Path(resumed['output_image']) == layout.root / 'M_31.fits'
    assert len(calls) == 1
    assert (layout.root / '05_autoghs/starless.fits').read_bytes() == original_ghs
    assert layout.image.read_bytes() == source_bytes


def test_panel_identity_survives_reordering_and_new_sources(tmp_path: Path) -> None:
    """Réordonner les entrées ne réutilise jamais la calibration d'un autre panneau."""
    layout = OutputLayout(tmp_path / 'M31')
    layout.create()
    north, south, west = [tmp_path / name for name in ('north', 'south', 'west')]
    first = layout.calibration_plan([north, south], True)
    assert first == [layout.panel(1, True), layout.panel(2, True)]
    for source, directory in zip((north, south), first):
        layout.write_summary(directory / 'source.json', {'source': str(source.resolve())})
    assert layout.calibration_plan([south, north], True) == first[::-1]
    assert layout.calibration_plan([south, west], True) == [first[1], layout.panel(3, True)]
    layout.write_summary(layout.calibration / 'source.json', {'source': str(north.resolve())})
    with pytest.raises(ValueError, match='source'):
        layout.calibration_plan([south], False)


@pytest.mark.parametrize('compare_chains', [True, False])
def test_all_standard_engines_use_their_numbered_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, compare_chains: bool,
) -> None:
    """Le même relais suit gradient, photométrie, restauration et GHS sans fuite de rapports."""
    import shutil
    from lib.postprocess import _PostProcessorSequence, GradientExtractor, PhotometricColorCalibrator
    from lib.restoration_chains import RestorationChainProcessor
    from lib.deconvolution import DeconvolutionProcessor
    from lib.denoising import NoiseReductionProcessor
    from lib.final_render import AutoGHSFinalRenderer
    from lib.processor import processor

    layout = OutputLayout(tmp_path / 'object')
    layout.create()
    fits.writeto(layout.image, np.full((3, 8, 10), .01, dtype='f4'))
    observed = []

    def execute(self: processor, input_path: Path, output_path: Path,
                args: argparse.Namespace | None = None) -> dict:
        """Simule les moteurs, en conservant les vrais relais FITS et rapports de séquence."""
        prefix = self.get_prefix()
        assert output_path == layout.stage_report(prefix)
        image = output_path.with_suffix('.fits')
        image.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(input_path, image)
        observed.append(prefix)
        return {'status': 'SUCCESS', 'image_path': str(input_path), 'output_image': str(image)}

    for engine in (GradientExtractor, PhotometricColorCalibrator, RestorationChainProcessor,
                   DeconvolutionProcessor, NoiseReductionProcessor, AutoGHSFinalRenderer):
        monkeypatch.setattr(engine, 'post_process', execute)
    result = _PostProcessorSequence().post_process(layout.image, layout.report, argparse.Namespace(
        restoration_compare_chains=compare_chains, enable_autoghs=True,
    ))
    expected = (['gradient', 'photometry', 'restoration', 'autoghs'] if compare_chains else
                ['gradient', 'photometry', 'deconvolution', 'denoise', 'autoghs'])
    assert observed == expected and list(result) == expected
    for prefix in expected:
        assert layout.stage_report(prefix).is_file()
        assert layout.stage_report(prefix).with_suffix('.txt').is_file()
    assert not list(layout.root.glob('*.json'))
    assert PostProcessResume.load(layout.image, layout.report)[0] == layout.stage_report(expected[-2]).with_suffix('.fits')


def test_handoff_fallback_checks_original_input_identity(tmp_path: Path) -> None:
    """Sans index final, la synthèse de calibration empêche de reprendre un autre objet."""
    layout = OutputLayout(tmp_path / 'M33')
    layout.create()
    layout.image.touch()
    original, other = tmp_path / 'raw1/M33', tmp_path / 'raw2/M33'
    layout.write_summary(layout.calibration / 'calibration.json', {
        'panels': [{'source': str(original.resolve())}],
    })
    assert PostProcessResume.final_images(layout.root, tmp_path / 'work', [original], None) == [layout.image]
    with pytest.raises(ValueError, match='sessions'):
        PostProcessResume.final_images(layout.root, tmp_path / 'work', [other], None)
