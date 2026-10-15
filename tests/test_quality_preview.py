"""Couleurs, orientation et conservation des masques dans l’aperçu de qualité."""

from pathlib import Path
import argparse
import json

import numpy as np
import pytest
from PIL import Image
from astropy.io import fits

from lib.quality_preview import QualityMaskPreview


def test_gradient_positions_follow_vertical_flip_not_astrometry_shift(
    tmp_path: Path,
) -> None:
    """Un nouveau CRPIX ne déplace pas les pixels ; l’inversion WCS retourne les points."""
    original = tmp_path / "original.fits"
    target = tmp_path / "flipped.fits"
    header = fits.Header(
        dict(
            CTYPE1="RA---TAN",
            CTYPE2="DEC--TAN",
            CRVAL1=23.0,
            CRVAL2=30.0,
            CRPIX1=16.0,
            CRPIX2=16.0,
            CDELT1=-0.001,
            CDELT2=0.001,
        )
    )
    fits.writeto(original, np.ones((32, 32), dtype=np.float32), header)
    header["CDELT2"] = -0.001
    header["CRPIX2"] = 20.0
    fits.writeto(target, np.ones((32, 32), dtype=np.float32), header)
    gradient = dict(
        method="rbf",
        image_path=str(original),
        correction=dict(channels=[dict(measurement_points=[[5, 7], [24, 20]])]),
    )
    points = QualityMaskPreview.gradient_positions(gradient, target)
    np.testing.assert_allclose(points, [[5, 24], [24, 11]])


def test_gradient_marks_only_tiles_without_points(tmp_path: Path) -> None:
    """Les points cyan et croix blanches sont distincts des contours bleus."""
    data = np.ones((1, 32, 32), dtype=np.float32)
    masks = {
        name: np.zeros((32, 32), dtype=bool)
        for name in ("sky", "star", "object", "uncertain")
    }
    masks["uncertain"][3:5, 25:29] = True
    path = QualityMaskPreview.write(
        data,
        masks,
        [],
        tmp_path / "points.png",
        top_down=True,
        tile_size=16,
        gradient_points=np.array([[8.0, 8.0]]),
    )
    with Image.open(path) as image:
        assert image.getpixel((8, 8)) == (0, 255, 255)
        assert image.getpixel((3, 3)) == (0, 0, 0)
        assert image.getpixel((20, 20)) == (255, 255, 255)
        assert image.getpixel((26, 3)) == QualityMaskPreview.COLORS["uncertain"]


def test_second_debug_image_uses_recorded_gradient_points(tmp_path: Path) -> None:
    """Les coordonnées enregistrées du gradient original sont utilisées sans recalcul."""
    source = tmp_path / "source.fits"
    fits.writeto(source, np.zeros((64, 64), dtype=np.float32))
    results = {
        "gradient": {
            "image_path": str(source),
            "correction": {"channels": [{"measurement_points": [[20, 20], [40, 40]]}]},
        }
    }
    path = QualityMaskPreview.write_gradient_points(results, tmp_path)
    assert path.name == "quality_gradient_points.png"
    with Image.open(path) as image:
        assert image.getpixel((20, 20)) == (255, 0, 0)
        assert image.getpixel((40, 40)) == (255, 0, 0)
        assert image.getpixel((60, 60)) == (0, 0, 0)
    assert results["gradient"]["quality_gradient_points_path"] == str(path)
    assert QualityMaskPreview.write_gradient_points({}, tmp_path) is None


def test_grid_and_exact_contours_do_not_enclose_empty_regions(tmp_path: Path) -> None:
    """Une exclusion en L laisse son intérieur libre, avec une grille indépendante."""
    data = np.ones((1, 64, 64), dtype=np.float32)
    masks = {
        name: np.zeros((64, 64), dtype=bool)
        for name in ("sky", "star", "object", "uncertain")
    }
    masks["uncertain"][5:16, 5] = True
    masks["uncertain"][15, 5:16] = True
    path = QualityMaskPreview.write(
        data, masks, [], tmp_path / "contours.png", top_down=True, tile_size=16
    )
    with Image.open(path) as image:
        assert image.getpixel((15, 5)) == (0, 0, 0)
        assert image.getpixel((5, 10)) == QualityMaskPreview.COLORS["uncertain"]
        assert image.getpixel((16, 30)) == QualityMaskPreview.GRID_COLOR
        assert image.getpixel((30, 32)) == QualityMaskPreview.GRID_COLOR


@pytest.mark.parametrize("mode", ["reliable", "legacy"])
def test_script_preview_without_restoration(tmp_path: Path, mode: str) -> None:
    """Un diagnostic est publié même sans moteur, sur l’image amont disponible."""
    source = tmp_path / "source.fits"
    calibrated = tmp_path / "calibrated.fits"
    fits.writeto(source, np.zeros((64, 64), dtype=np.float32))
    fits.writeto(
        calibrated,
        np.random.default_rng(1).normal(0.1, 0.001, (64, 64)).astype("float32"),
    )
    steps = tmp_path / "steps"
    preview = QualityMaskPreview.ensure_for_run(
        {"photometry": {"output_image": str(calibrated)}},
        source,
        steps,
        argparse.Namespace(quality_mode=mode),
    )
    assert preview.is_file()
    report = json.loads((steps / "quality_masks.json").read_text())
    assert report["diagnostic_only"] is True
    assert report["quality_reference"]["reference_image"] == str(calibrated)
    assert report["quality_preview_path"] == str(preview)


def test_script_reuses_existing_preview(tmp_path: Path) -> None:
    """L’aperçu commun est conservé sans reconstruire un autre masque."""
    preview = tmp_path / "common_masks.png"
    preview.write_bytes(b"existing preview")
    result = QualityMaskPreview.ensure_for_run(
        {"restoration": {"quality_preview_path": str(preview)}},
        tmp_path / "unused.fits",
        tmp_path / "steps",
        argparse.Namespace(),
    )
    assert result == preview
    assert not (tmp_path / "steps").exists()


@pytest.mark.parametrize("top_down", [True, False])
def test_preview_colors_tiles_and_orientation(tmp_path: Path, top_down: bool) -> None:
    """Les cadres correspondent aux masques natifs, avec retournement commun au fond."""
    data = np.ones((1, 64, 64), dtype=np.float32)
    masks = {
        name: np.zeros((64, 64), dtype=bool)
        for name in ("sky", "star", "object", "uncertain")
    }
    for name, y in (("star", 3), ("object", 20), ("uncertain", 35)):
        masks[name][y : y + 5, 5:10] = True
    masks["sky"][48:64, :16] = True
    originals = {name: value.copy() for name, value in masks.items()}
    path = QualityMaskPreview.write(
        data,
        masks,
        [(slice(48, 64), slice(0, 16))],
        tmp_path / "preview.png",
        top_down=top_down,
    )
    with Image.open(path) as image:
        for name, y in (("star", 3), ("object", 20), ("uncertain", 35)):
            assert (
                image.getpixel((5, y if top_down else 63 - y))
                == QualityMaskPreview.COLORS[name]
            )
        assert image.getpixel((0, 48 if top_down else 15)) == (0, 255, 80)
    for name in masks:
        np.testing.assert_array_equal(masks[name], originals[name])
    np.testing.assert_array_equal(data, 1)


def test_empty_sky_still_produces_preview(tmp_path: Path) -> None:
    """Un ciel vide conserve la représentation des exclusions, sans cadres verts."""
    data = np.ones((1, 32, 32), dtype=np.float32)
    masks = {
        name: np.zeros((32, 32), dtype=bool)
        for name in ("sky", "star", "object", "uncertain")
    }
    masks["uncertain"][:] = True
    path = QualityMaskPreview.write(
        data, masks, [], tmp_path / "empty.png", top_down=True
    )
    with Image.open(path) as image:
        assert image.getpixel((0, 0)) == QualityMaskPreview.COLORS["uncertain"]
        assert not np.any(np.all(np.asarray(image)[:32, :32] == (0, 255, 80), axis=2))
