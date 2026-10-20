"""Validations opt-in avec les vrais exécutables Siril/StarNet et la CLI existante."""

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from lib.auto_ghs import ghs_rgb, linear_blackpoint
from lib.star_rendering import recompose

pytestmark = pytest.mark.skipif(
    os.environ.get("SIRIL_AUTOGHS_INTEGRATION") != "1",
    reason="Définir SIRIL_AUTOGHS_INTEGRATION=1 avec Siril/StarNet réels ; "
    "SIRIL_MODE (flatpak par défaut) et SIRIL_PATH pour un Siril natif ou AppImage",
)


def siril_cli_command() -> list[str]:
    """Commande Siril du mode SIRIL_MODE (flatpak, native ou appimage)."""
    if os.environ.get("SIRIL_MODE", "flatpak") == "flatpak":
        return ["flatpak", "run", "--command=siril-cli", "org.siril.Siril"]
    return [os.environ["SIRIL_PATH"]]


def test_real_cli_both_and_saved_starnet_path(tmp_path: Path) -> None:
    """La CLI réelle publie les deux variantes, et -S conserve le chemin effectif."""
    root = Path(__file__).resolve().parents[1]
    data = fits.getdata(root / "imagesExample/M_33_combined.fits")[
        :, 1000:1512, 1000:1512
    ]
    source = tmp_path / "m33.fits"
    fits.writeto(
        source, data, fits.getheader(root / "imagesExample/M_33_combined.fits")
    )
    original = source.read_bytes()
    config = tmp_path / "config.json"
    command = [
        sys.executable,
        str(root / "bin/postProcess.py"),
        str(source),
        str(tmp_path / "output"),
        "--config",
        str(config),
        "--disable-gradient",
        "--disable-photometry",
        "--disable-deconvolution",
        "--disable-denoise",
        "--enable-autoghs",
        "--compute-backend",
        "cpu",
        "-S",
    ]
    if os.environ.get("SIRIL_MODE"):
        command += ["--siril-mode", os.environ["SIRIL_MODE"]]
    if os.environ.get("SIRIL_PATH"):
        command += ["--siril-path", os.environ["SIRIL_PATH"]]
    if os.environ.get("STARNET_PATH"):
        command += ["--starnet-path", os.environ["STARNET_PATH"]]
    process = subprocess.run(
        command, check=False, capture_output=True, text=True, timeout=180
    )
    assert process.returncode == 0, process.stderr
    saved = json.loads(config.read_text())
    assert Path(saved["starnet_path"]).is_file()
    report = json.loads(
        (tmp_path / "output/m33_postprocess.json").read_text()
    )["autoghs"]
    assert report["status"] == "SUCCESS"
    assert report["separation"]["reconstruction_max_abs"] < 1e-6
    assert report["backend"]["selected"] == "cpu"
    assert report["stars"]["after"]["white_fraction_rgb"] == [0, 0, 0]
    stars = fits.getdata(report["stars"]["output_image"])
    for strategy, output in report["output_images"].items():
        background = fits.getdata(report["strategies"][strategy]["starless_image"])
        final = fits.getdata(output)
        np.testing.assert_allclose(
            final, 0.95 * background + stars * (1 - 0.95 * background), atol=1e-7
        )
        assert report["strategies"][strategy]["status"] == "SUCCESS"
        assert (
            report["strategies"][strategy]["recomposition"]["star_contribution_sum"] > 0
        )
        assert fits.getheader(output)["SPNLIN"]
        assert final.shape == data.shape
        assert np.isfinite(final).all() and final.min() >= 0 and final.max() <= 1
    assert source.read_bytes() == original
    assert Path(report["histogram_path"]).stat().st_size > 0
    assert set(report["output_jpegs"]) == {"range", "peak"}
    assert all(Path(path).is_file() for path in report["comparison_paths"].values())
    assert (tmp_path / "output/m33_postprocess.jpg").is_file()


def test_live_siril_math_oracle(tmp_path: Path) -> None:
    """Recompare le GHS et le point noir à Siril en utilisant une config isolée."""
    reference = json.loads(
        (Path(__file__).parent / "data/auto_ghs_siril_1_4_4.json").read_text()
    )
    data = np.asarray(reference["input"], dtype=np.float32)
    fits.writeto(tmp_path / "input.fits", data, fits.Header({"ROWORDER": "BOTTOM-UP"}))
    (tmp_path / "siril.ini").write_text("[core]\nforce_16bit=false\n")
    lines = ["requires 1.4", f'cd "{tmp_path}"', "set32bits", "setcpu 2"]
    for index, case in enumerate(reference["cases"]):
        params = " ".join(
            f"-{key}={value}" for key, value in case["parameters"].items()
        )
        lines += [
            "load input.fits",
            f"ght {params} -human -clipmode=rgbblend",
            f"save oracle_{index}.fits",
            "close",
        ]
    lines += [
        "load input.fits",
        "linstretch -BP=.01 -clipmode=rgbblend",
        "save oracle_bp.fits",
        "close",
    ]
    script = tmp_path / "oracle.sps"
    script.write_text("\n".join(lines) + "\n")
    background = data * 0.8
    stars = data[:, ::-1, ::-1] * 0.6
    fits.writeto(
        tmp_path / "background.fits", background, fits.Header({"ROWORDER": "BOTTOM-UP"})
    )
    fits.writeto(tmp_path / "stars.fits", stars, fits.Header({"ROWORDER": "BOTTOM-UP"}))
    lines += [
        "setext fits",
        "load input.fits",
        'pm "1-(1-0.95*$background$)*(1-$stars$)" -nosum',
        "save oracle_screen.fits",
        "close",
    ]
    script.write_text("\n".join(lines) + "\n")
    command = [
        *siril_cli_command(),
        "-i",
        str(tmp_path / "siril.ini"),
        "-s",
        str(script),
    ]
    process = subprocess.run(
        command, check=False, capture_output=True, text=True, timeout=60
    )
    assert process.returncode == 0, process.stdout + process.stderr
    errors = []
    for index, case in enumerate(reference["cases"]):
        oracle = fits.getdata(tmp_path / f"oracle_{index}.fits")
        actual = ghs_rgb(data, **case["parameters"])
        errors.append(float(np.max(abs(actual - oracle))))
        np.testing.assert_allclose(actual, oracle, atol=2e-6, rtol=0)
    np.testing.assert_array_equal(
        linear_blackpoint(data, 0.01), fits.getdata(tmp_path / "oracle_bp.fits")
    )
    rendered, _ = recompose(background, stars)
    screen = fits.getdata(tmp_path / "oracle_screen.fits")
    np.testing.assert_allclose(rendered, screen, atol=2e-6, rtol=0)
    errors.append(float(np.max(abs(rendered - screen))))
    (tmp_path / "measured-errors.json").write_text(
        json.dumps({"max_abs": errors}, indent=2)
    )
