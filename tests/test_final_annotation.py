"""Contrôle du dernier export annoté et de la provenance des informations."""

import argparse
import json
from pathlib import Path

from astropy.io import fits
import numpy as np
from PIL import Image
import pytest

from lib.config import Config
from lib.final_annotation import FinalImageAnnotator
from lib.postprocessing import PostProcessingProcessor
from lib.render_exports import read_render_image
from lib.type_defs import JSONReport


def test_final_panel_preserves_source_and_pixels(tmp_path: Path) -> None:
    """L'encart reste en bas à gauche, sans changer les pixels extérieurs."""
    source = tmp_path / "render.fits"
    header = fits.Header({"SPNLIN": True, "ROWORDER": "BOTTOM-UP",
                          "OBJECT": "M31", "EXPTIME": 120.0,
                          "INSTRUME": "Camera tres longue " * 12})
    data = np.random.default_rng(5).uniform(0, 1, (3, 450, 700)).astype('f4')
    fits.writeto(source, data, header)
    original = source.read_bytes()
    report = tmp_path / "final.json"
    result = FinalImageAnnotator().post_process(source, report, argparse.Namespace())
    assert result["status"] == "SUCCESS"
    assert json.loads(report.read_text()) == result
    left, top, right, bottom = result["panel_bounds"]
    assert 0 < left < right < 700 and 0 < top < bottom < 450
    assert left == 450 - bottom
    with Image.open(result["output_annotated_image"]) as image:
        actual = np.asarray(image)
    with read_render_image(source) as rendered:
        expected = np.asarray(rendered)
    mask = np.ones((450, 700), dtype=bool)
    mask[top:bottom + 1, left:right + 1] = False
    assert np.array_equal(actual[mask], expected[mask])
    assert np.any(actual[~mask] != expected[~mask])
    assert source.read_bytes() == original


def test_metadata_does_not_invent_mosaic_integration() -> None:
    """Le compteur d'un panneau ne devient pas une durée totale de mosaïque."""
    header = fits.Header({"EXPTIME": 120, "STACKCNT": 51, "LIVETIME": 6120,
                          "GAIN": 0, "FILTER": " ", "CCD-TEMP": -15.0})
    lines = FinalImageAnnotator.acquisition_lines(header)
    assert "Pose de référence : 120 s" in lines
    assert "Gain : 0" in lines
    assert "Température capteur : -15 °C" in lines
    assert not any("6120" in line or "51" in line or "Filtre" in line for line in lines)
    assert "Informations non renseignées" in FinalImageAnnotator.acquisition_lines(fits.Header())


@pytest.mark.parametrize('enabled,nonlinear,size,status', [
    (False, True, 100, 'DISABLED'), (True, False, 100, 'SKIPPED'),
    (True, True, 20, 'SKIPPED'),
])
def test_skipped_exports(
    tmp_path: Path, enabled: bool, nonlinear: bool, size: int, status: str,
) -> None:
    """Les données linéaires, miniatures et désactivations ne créent pas d'image."""
    source = tmp_path / 'source.fits'
    fits.writeto(source, np.zeros((3, size, size)), fits.Header({'SPNLIN': nonlinear}))
    result = FinalImageAnnotator().post_process(
        source, tmp_path / 'report.json', argparse.Namespace(final_annotation=enabled),
    )
    assert result['status'] == status
    assert not list(tmp_path.glob('*.png'))


def test_annotation_options_follow_config(tmp_path: Path) -> None:
    """Le défaut actif peut être remplacé par JSON puis explicitement par CLI."""
    config = Config(tmp_path / 'settings.json')
    parser = argparse.ArgumentParser()
    config.register(PostProcessingProcessor, parser)
    assert config.parse_args(parser, []).final_annotation is True
    config.set('final_annotation', False)
    parser = argparse.ArgumentParser()
    config.register(PostProcessingProcessor, parser)
    assert config.parse_args(parser, []).final_annotation is False
    args = config.parse_args(parser, ['--final-annotation'])
    config.bind_parameters(args)
    assert FinalImageAnnotator()._get_args().final_annotation is True
    args.save_config = True
    assert config.save_requested(args)
    assert json.loads(Path(config.config_file).read_text())['final_annotation'] is True
    assert config.parse_args(parser, ['--no-final-annotation']).final_annotation is False


def test_last_stage_integration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le PNG vient du FITS exporté et l'audit final figure dans le rapport global."""
    from lib.postprocess import _PostProcessorSequence
    from lib.quality_preview import QualityMaskPreview

    source = tmp_path / 'source.fits'
    rendered = tmp_path / 'rendered.fits'
    fits.writeto(source, np.zeros((3, 120, 180), dtype='f4'))
    fits.writeto(rendered, np.full((3, 120, 180), 0.4, dtype='f4'),
                 fits.Header({'SPNLIN': True, 'OBJECT': 'M31'}))

    def sequence(
        self: _PostProcessorSequence, input_path: Path, output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Fournit une sortie scientifique déterministe sans exécuter Siril."""
        return {'autoghs': {'output_image': str(rendered)}}

    monkeypatch.setattr(_PostProcessorSequence, 'post_process', sequence)
    monkeypatch.setattr(QualityMaskPreview, 'ensure_for_run', lambda *args: None)
    monkeypatch.setattr(QualityMaskPreview, 'write_gradient_points', lambda *args: None)
    report = tmp_path / 'source_postprocess.json'
    result = PostProcessingProcessor().post_process(source, report, argparse.Namespace())
    assert result['output_image'] == str(report.with_suffix('.fits'))
    assert result['output_annotated_image'] == str(tmp_path / 'source_postprocess_annotated.png')
    assert result['treatments']['annotation']['status'] == 'SUCCESS'
    assert json.loads(report.read_text()) == result['treatments']
    assert Path(result['output_image']).read_bytes() == rendered.read_bytes()


def test_invalid_render_is_fatal(tmp_path: Path) -> None:
    """Une erreur de données ne produit ni PNG ni audit de réussite."""
    source = tmp_path / 'invalid.fits'
    fits.writeto(source, np.full((3, 100, 100), np.nan), fits.Header({'SPNLIN': True}))
    with pytest.raises(ValueError):
        FinalImageAnnotator().post_process(source, tmp_path / 'report.json', argparse.Namespace())
    assert not list(tmp_path.glob('*.png'))
    assert not (tmp_path / 'report.json').exists()


@pytest.mark.parametrize('options', [
    {'annotation_magnitude': float('nan')}, {'annotation_magnitude': -1},
    {'annotation_magnitude': 26}, {'annotation_max_objects': -1},
    {'annotation_max_objects': 201},
])
def test_invalid_annotation_options(tmp_path: Path, options: dict[str, float]) -> None:
    """Les limites invalides échouent avant toute publication du PNG."""
    source = tmp_path / 'render.fits'
    fits.writeto(source, np.zeros((3, 120, 180)), fits.Header({'SPNLIN': True}))
    with pytest.raises(ValueError, match='annotation-'):
        FinalImageAnnotator().post_process(
            source, tmp_path / 'report.json', argparse.Namespace(**options),
        )
    assert not list(tmp_path.glob('*.png'))


def test_catalog_names_written_to_final_image(tmp_path: Path) -> None:
    """Le rendu final avec WCS produit les noms et leur audit sans moteur externe."""
    from astropy.wcs import WCS

    wcs = WCS(naxis=2)
    wcs.wcs.crval = [10.68479167, 41.26905556]
    wcs.wcs.crpix = [251, 201]
    wcs.wcs.cdelt = [-0.003, 0.003]
    wcs.wcs.ctype = ['RA---TAN', 'DEC--TAN']
    header = wcs.to_header()
    header['SPNLIN'] = True
    source = tmp_path / 'm31.fits'
    fits.writeto(source, np.full((3, 400, 500), 0.1, dtype='f4'), header)
    report = tmp_path / 'annotation.json'
    result = FinalImageAnnotator().post_process(source, report, argparse.Namespace())
    assert result['objects']['status'] == 'SUCCESS'
    assert any(label['label'] == 'Galaxie d’Andromède\nM31 / NGC0224'
               for label in result['objects']['labels'])
    assert Path(result['output_annotated_image']).is_file()
    assert json.loads(report.read_text()) == result
    result = FinalImageAnnotator().post_process(
        source, report, argparse.Namespace(annotation_objects=False),
    )
    assert result['objects']['status'] == 'DISABLED'
