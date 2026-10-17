"""Validation des étoiles seules et de la fusion bornée des deux variantes."""

from pathlib import Path

import numpy as np
import pytest

from lib.star_rendering import ConservativeStarRenderer, recompose


def test_asinh_monotone_and_color_preserving() -> None:
    """La courbe préserve les ratios RGB et les étoiles faibles, sans noyau blanc."""
    light = np.linspace(0, 1, 1024).reshape(32, 32)
    data = np.array([light, 0.6 * light, 0.3 * light], dtype="float32")
    result, report = ConservativeStarRenderer.render(data)
    assert np.all(np.diff(result[0].ravel()) > 0)
    assert result.max() == pytest.approx(0.85)
    assert result[0, 0, 1] > data[0, 0, 1]
    np.testing.assert_allclose(result[1], 0.6 * result[0], atol=1e-7)
    assert report["color"]["chromaticity_mean_abs"] < 1e-6
    assert report["after"]["white_fraction_rgb"] == [0, 0, 0]


@pytest.mark.parametrize("boost,max_ratio", [(3, 1.25), (5, 1.5)])
def test_gaussian_stars_width_and_subpixels(boost: float, max_ratio: float) -> None:
    """Les étoiles fortes/faibles et décentrées gardent une largeur modérée."""
    y, x = np.indices((80, 80))
    profile = 0.8 * np.exp(-((x - 25.3) ** 2 + (y - 25.7) ** 2) / 4)
    profile += 0.02 * np.exp(-((x - 60.1) ** 2 + (y - 60.4) ** 2) / 3)
    data = np.array([profile, 0.7 * profile, 0.4 * profile], dtype="float32")
    result, report = ConservativeStarRenderer.render(data, faint_boost=boost)
    assert report["fwhm"]["count"] >= 2
    assert 1 <= report["fwhm"]["median_ratio"] < max_ratio
    assert np.count_nonzero(result > 0) == np.count_nonzero(data > 0)


def test_recomposition_screen_has_stars_and_no_clip() -> None:
    """Les deux fonds réinsèrent la même couche positive sans écrêtage destructif."""
    stars = np.ones((3, 16, 16), dtype="float32") * 0.85
    for level in (0.1, 0.9):
        background = np.full_like(stars, level)
        final, report = recompose(background, stars)
        np.testing.assert_allclose(
            final, 1 - (1 - 0.95 * background) * (1 - stars), atol=1e-7
        )
        assert final.max() < 1
        assert report["star_contribution_sum"] > 0


@pytest.mark.parametrize(
    "data", [np.full((3, 4, 4), -0.01), np.full((3, 4, 4), np.nan)]
)
def test_invalid_stars_fail(data: np.ndarray) -> None:
    """Un masque signé n'est jamais écrêté silencieusement."""
    with pytest.raises(ValueError):
        ConservativeStarRenderer.render(data)


def test_star_processor_fits_contract(tmp_path: Path) -> None:
    """Le processeur autonome garde métadonnées et sortie non linéaire étoilée seule."""
    import argparse

    from astropy.io import fits

    source = tmp_path / "stars.fits"
    data = np.full((3, 8, 8), 0.1, dtype=np.float32)
    fits.writeto(source, data, fits.Header({"OBJECT": "Test"}))
    result = ConservativeStarRenderer().post_process(
        source, tmp_path / "report.json", argparse.Namespace()
    )
    header = fits.getheader(result["output_image"])
    assert header["OBJECT"] == "Test" and header["SPNLIN"]
    assert header["SPROLE"] == "STARS"
    assert fits.getdata(result["output_image"]).mean() > data.mean()


def test_faint_boost_reveals_weak_stars_and_preserves_support() -> None:
    """Les faibles étoiles gagnent en visibilité sans créer de pixels stellaires."""
    values = np.array([[0, 1e-5, 1e-4, 0.001, 0.1, 1]], dtype=np.float32)
    data = np.array([values, 0.6 * values, 0.3 * values])
    previous, _ = ConservativeStarRenderer.render(data, faint_boost=1)
    enhanced, report = ConservativeStarRenderer.render(data, faint_boost=3)
    assert enhanced[0, 0, 2] > 2.9 * previous[0, 0, 2]
    assert enhanced.max() == pytest.approx(0.85)
    assert np.all(np.diff(enhanced[0, 0]) > 0)
    np.testing.assert_array_equal(enhanced > 0, data > 0)
    np.testing.assert_allclose(enhanced[1], 0.6 * enhanced[0], atol=1e-7)
    np.testing.assert_allclose(enhanced[2], 0.3 * enhanced[0], atol=1e-7)
    assert report["faint_intensity_gain"] > 3.5
    expected = 0.85 * np.arcsinh(2 * values) / np.arcsinh(2)
    np.testing.assert_allclose(previous[0], expected, atol=1e-7)


@pytest.mark.parametrize("boost", [0, 0.9, 10.1, float("nan")])
def test_invalid_faint_boost_is_rejected(boost: float) -> None:
    """Le facteur d'étoiles faibles doit être fini et compris entre 1 et 10."""
    with pytest.raises(ValueError):
        ConservativeStarRenderer.render(np.full((3, 8, 8), 0.1), faint_boost=boost)


def test_default_faint_boost_is_stronger_and_configurable() -> None:
    """Le défaut relève davantage les étoiles faibles ; l'option reste prioritaire."""
    import argparse

    parser = argparse.ArgumentParser()
    ConservativeStarRenderer.add_arguments(parser)
    assert parser.parse_args([]).star_faint_boost == 5
    assert parser.parse_args(["--star-faint-boost", "3"]).star_faint_boost == 3
    light = np.array([[0, 0.0001, 0.01, 0.4, 1]], dtype=np.float32)
    data = np.array([light, light * 0.6, light * 0.3])
    previous, _ = ConservativeStarRenderer.render(data, faint_boost=3)
    stronger, report = ConservativeStarRenderer.render(data)
    assert stronger[0, 0, 1] / previous[0, 0, 1] == pytest.approx(5 / 3, rel=0.001)
    np.testing.assert_array_equal(stronger > 0, data > 0)
    np.testing.assert_allclose(stronger[1], 0.6 * stronger[0], atol=1e-7)
    assert stronger.max() == pytest.approx(0.85)
    assert report["faint_boost"] == 5
    assert report["faint_intensity_gain"] == pytest.approx(5.8879, rel=1e-4)
