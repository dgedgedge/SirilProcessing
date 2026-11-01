"""JPEG orientés, conversion directe et copie du rendu final par le CLI."""

import json
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from PIL import Image

from lib.render_exports import (
    read_render_image,
    write_render_jpeg,
)


@pytest.mark.parametrize("order", ["TOP-DOWN", "BOTTOM-UP"])
def test_render_image_orientation_and_direct_intensities(tmp_path: Path, order: str) -> None:
    """Respecte ROWORDER sans gamma ajouté ni normalisation par les extrema."""
    data = np.full((3, 16, 20), 0.1, dtype=np.float32)
    data[:, 0, 0] = (1, 0, 0)
    data[:, -1, 0] = (0, 0, 1)
    source = tmp_path / "render.fits"
    fits.writeto(source, data, fits.Header({"SPNLIN": True, "ROWORDER": order}))
    before = source.read_bytes()
    with read_render_image(source) as image:
        assert image.size == (20, 16)
        assert image.getpixel((10, 8)) == (26, 26, 26)
        assert image.getpixel((0, 0)) == ((255, 0, 0) if order == "TOP-DOWN" else (0, 0, 255))
    assert source.read_bytes() == before


def test_final_jpeg_keeps_size_color_and_source(tmp_path: Path) -> None:
    """Le JPEG est lisible, à pleine résolution, avec des intensités proches du FITS."""
    data = np.full((3, 40, 64), 0.2, dtype=np.float32)
    data[0] = 0.6
    source, destination = tmp_path / "render.fits", tmp_path / "render.jpg"
    fits.writeto(source, data, fits.Header({"SPNLIN": True}))
    before = source.read_bytes()
    write_render_jpeg(source, destination)
    with Image.open(destination) as image:
        assert image.format == "JPEG" and image.mode == "RGB"
        assert image.size == (64, 40)
        np.testing.assert_allclose(image.getpixel((30, 20)), [153, 51, 51], atol=3)
    assert source.read_bytes() == before
    with pytest.raises(ValueError, match="remplacer"):
        write_render_jpeg(source, source)


@pytest.mark.parametrize("nonlinear,level", [(False, 0.2), (True, 1.1), (True, np.nan)])
def test_invalid_render_is_not_exported(
    tmp_path: Path, nonlinear: bool, level: float,
) -> None:
    """Refuse les images linéaires et les données invalides sans JPEG trompeur."""
    source, destination = tmp_path / "input.fits", tmp_path / "output.jpg"
    fits.writeto(source, np.full((3, 8, 8), level), fits.Header({"SPNLIN": nonlinear}))
    with pytest.raises(ValueError):
        write_render_jpeg(source, destination)
    assert not destination.exists()


@pytest.mark.parametrize("with_jpeg", [False, True])
def test_cli_copies_primary_jpeg_without_running_external_engines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_jpeg: bool,
) -> None:
    """Le CLI exporte l'alias JPEG déclaré par AutoGHS, sans inventer de JPEG linéaire."""
    import argparse
    from contextlib import nullcontext
    import importlib.util
    import sys

    from lib.postprocess import _PostProcessorSequence
    import lib.postprocessing as postprocessing

    source, rendered = tmp_path / "source.fits", tmp_path / "rendered.fits"
    data = np.full((3, 16, 20), 0.2, dtype=np.float32)
    fits.writeto(source, data)
    fits.writeto(rendered, data, fits.Header({"SPNLIN": True}))
    jpeg = rendered.with_suffix(".jpg")
    write_render_jpeg(rendered, jpeg)
    entry = Path(__file__).resolve().parents[1] / "bin/postProcess.py"
    spec = importlib.util.spec_from_file_location("postprocess_export_cli", entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "PowerGuard", nullcontext)

    def render(
        self: _PostProcessorSequence, input_path: Path, output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> dict[str, dict[str, str]]:
        """Fournit des artefacts réels sans relancer les moteurs scientifiques."""
        result = {"autoghs": {"output_image": str(rendered)}}
        if with_jpeg:
            result["autoghs"]["output_jpeg"] = str(jpeg)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result))
        return result

    monkeypatch.setattr(_PostProcessorSequence, "post_process", render)
    monkeypatch.setattr(postprocessing.QualityMaskPreview, "ensure_for_run", lambda *args: None)
    monkeypatch.setattr(postprocessing.QualityMaskPreview, "write_gradient_points", lambda *args: None)
    monkeypatch.setattr(sys, "argv", [str(entry), str(source), str(tmp_path / "out"),
                                      "--config", str(tmp_path / "settings.json")])
    assert module.main() == 0
    exported = tmp_path / "out/source_postprocess.jpg"
    assert exported.exists() is with_jpeg
    if with_jpeg:
        assert exported.read_bytes() == jpeg.read_bytes()
