"""Ordre du traitement solaire sur images synthétiques, sans GPU ni FITS réels."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType
from unittest.mock import Mock

import numpy as np
import pytest
from astropy.io import fits
from PIL import Image


@pytest.fixture
def solar(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Charge le script autonome en conservant ses classes de données réelles."""
    path = Path(__file__).parents[1] / "bin/solarEclipseGif.py"
    spec = importlib.util.spec_from_file_location("solar_sequence_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("radii", "centers"),
    [((2.0, 5.0, 5.0), (2.0, 3.0, 4.0)),
     ((5.0, 5.0, 2.0), (2.0, 3.0, 2.0))],
)
def test_movie_loop_preserves_order_and_previous_center(
    solar: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    radii: tuple[float, ...], centers: tuple[float, ...],
) -> None:
    """Vérifie centrage, image/masque, luminosité et fond dans chaque itération.

    Les rayons imposés couvrent un départ cohérent ou incohérent, puis le
    repli sur le centre précédent. Les corrections d’image restent réelles.
    """
    image = np.full((8, 8), 100.0, dtype=np.float32)
    mask = np.ones((8, 8), dtype=bool)
    frames = [
        solar.Frame(i + 1, tmp_path / f"{i}.fits", float(i), image.copy())
        for i in range(3)
    ]
    model = solar.SolarModel(image[:4, :4], mask[:4, :4], 2.0, 2.0, 2.0, 4, [1])
    detections = [
        solar.FullSunDetection(
            2.0, 2.0, radius, mask, mask, mask, mask, False,
            (2.0, 2.0, radius), None, None, 0, 0,
        )
        for radius in radii
    ]
    trace = Mock()
    for name in (
        "crop_with_padding", "rotate_crop", "rotate_mask", "masked_luminosity",
        "luminosity_factor", "filter_background_outside_mask",
    ):
        wrapped = Mock(wraps=getattr(solar, name))
        trace.attach_mock(wrapped, name)
        monkeypatch.setattr(solar, name, wrapped)
    detection = Mock(side_effect=detections)
    shift = Mock(return_value=(1.0, 0.0))
    trace.attach_mock(detection, "detect")
    trace.attach_mock(shift, "shift")
    monkeypatch.setattr(solar, "masque_solaire_principal", detection)
    monkeypatch.setattr(solar, "relative_shift_from_masks", shift)

    processed = solar.build_movie_frames(
        frames, [0, 1, 2], model, 0.0, 0.0,
        solar.CorrelationEngine(False, "test CPU"),
        background_filter_enabled=False,
    )
    expected = []
    for index, radius in enumerate(radii):
        expected.append("detect")
        if index > 0 and radius != model.radius:
            expected.append("shift")
        expected.extend([
            "crop_with_padding", "rotate_crop", "crop_with_padding", "rotate_mask",
            "masked_luminosity", "masked_luminosity", "luminosity_factor",
            "masked_luminosity", "filter_background_outside_mask",
        ])
    assert [call[0] for call in trace.mock_calls] == expected
    assert tuple(frame.center_x for frame in processed) == centers
    for frame in processed:
        assert frame.image.shape == (4, 4)
        np.testing.assert_allclose(frame.image, 100.0)
        assert frame.luminosity_factor == 1.0


def test_movie_loop_rejects_empty_selection(solar: ModuleType) -> None:
    """L’absence de poses film arrête le traitement avant slots et GIF."""
    image = np.ones((4, 4), dtype=np.float32)
    model = solar.SolarModel(image, image > 0, 2.0, 2.0, 1.0, 4, [])
    with pytest.raises(ValueError, match="No frame left"):
        solar.build_movie_frames(
            [], [], model, 0.0, 0.0, solar.CorrelationEngine(False, "test CPU"),
        )


def test_solar_cli_runs_from_fits_to_gif(
    solar: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Exécute le parcours complet sur un disque connu, avec exports de debug.

    Un Soleil plein et un dark servent de calibration ; les trois poses film
    varient en intensité. Les calculs et écritures sont réels, sur CPU. Le GIF
    doit mesurer 64 × 64 pixels et durer une seconde, avec trois PNG de debug.
    """
    y, x = np.indices((96, 96))
    disk = (x - 48) ** 2 + (y - 48) ** 2 <= 24 ** 2
    for index in range(5):
        image = np.full((96, 96), 5.0, dtype=np.float32)
        if index != 1:
            image[disk] = 1000.0 + 50 * index
        header = fits.Header({
            "DATE-OBS": f"2026-01-01T12:00:0{index}",
            "EXPTIME": 0.01, "GAIN": 125.0,
        })
        fits.writeto(tmp_path / f"frame_{index + 1}.fits", image, header)
    output = tmp_path / "eclipse.gif"
    monkeypatch.setattr(sys, "argv", [
        "solarEclipseGif.py", "--input-dir", str(tmp_path), "--output", str(output),
        "--full-sun-frames", "1", "--dark-calib-frames", "2",
        "--target-duration", "1", "--debug-gif-frames",
    ])
    monkeypatch.setattr(
        solar, "select_correlation_engine",
        Mock(return_value=solar.CorrelationEngine(False, "test CPU")),
    )
    assert solar.main() == 0
    with Image.open(output) as gif:
        assert gif.format == "GIF"
        assert gif.size == (64, 64)
        duration = 0
        for index in range(gif.n_frames):
            gif.seek(index)
            duration += gif.info["duration"]
        assert duration == 1000
    assert len(list((tmp_path / "debug/gif_frames").glob("*.png"))) == 3
