"""Vérité terrain synthétique pour masques, bruit et perte de signal diffus."""

import argparse
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from scipy.ndimage import gaussian_filter

from lib.restoration_quality import QualityReference, RestorationQuality
from lib.deconvolution_quality import assess_denoising, assess_quality, artifact_metrics
from lib.config import Config


def test_dense_stars_do_not_turn_sky_into_diffuse_signal(tmp_path: Path) -> None:
    """Le lissage des étoiles ne doit pas fabriquer une nébulosité dans un ciel connu."""
    rng = np.random.default_rng(42)
    yy, xx = np.indices((384, 384))
    stars = np.zeros(xx.shape)
    for y in range(24, 384, 32):
        for x in range(24, 384, 32):
            stars += 0.03 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.5**2))
    noise = gaussian_filter(rng.normal(0, 0.001, xx.shape), 0.7)
    source = tmp_path / "stars.fits"
    fits.writeto(source, (0.1 + stars + noise).astype("float32"))
    reference = QualityReference(source)
    assert reference.star_mask.mean() > 0.1
    assert reference.object_mask.mean() < 0.2
    assert reference.sky_mask.mean() > 0.35
    candidate = tmp_path / "denoised.fits"
    fits.writeto(candidate, (0.1 + stars + 0.6 * noise).astype("float32"))
    result = reference.noise(fits.getdata(candidate)[None])
    assert result["max_noise_ratio"] == pytest.approx(0.6, abs=0.03)
    assert result["status"] == "PASS"
    assert reference.audit()["metrics_version"] == 3
    assert (
        reference.audit()["segmentation"]["per_channel"][0][0]["observed_dispersion"]
        > 0
    )


def test_large_object_does_not_veto_known_corner_sky(tmp_path: Path) -> None:
    """Une forte couverture du signal ne rend pas automatiquement incertain le ciel des coins."""
    emission = np.full((512, 512), 0.02)
    for ys in (slice(0, 144), slice(368, 512)):
        for xs in (slice(0, 144), slice(368, 512)):
            emission[ys, xs] = 0
    source = tmp_path / "large.fits"
    noise = np.random.default_rng(19).normal(0, 0.0005, emission.shape)
    fits.writeto(source, (0.1 + emission + noise).astype("float32"))
    reference = QualityReference(source)
    occupied = reference.star_mask | reference.object_mask | reference.uncertain_mask
    assert occupied.mean() > 0.65
    assert reference.sky_mask[emission > 0].mean() < 0.01
    assert len(reference.tiles) >= 4
    assert reference.audit()["quadrant_count"] == 4
    assert reference.noise(reference.data)["max_noise_ratio"] == pytest.approx(1)


@pytest.fixture
def galaxy(tmp_path):
    """Galaxie diffuse sur un ciel blanc connu, avec détail cohérent en filament."""
    rng = np.random.default_rng(190)
    yy, xx = np.indices((384, 384))
    diffuse = 0.012 * np.exp(-((xx - 192) ** 2 / 1800 + (yy - 192) ** 2 / 700))
    diffuse += 0.006 * np.exp(-((yy - 170) ** 2) / 12 - (xx - 192) ** 2 / 1600)
    noise = rng.normal(0, 0.001, diffuse.shape)
    source = tmp_path / "galaxy.fits"
    fits.writeto(source, (0.1 + diffuse + noise).astype("float32"))
    return source, diffuse, noise


def test_sky_excludes_galaxy_and_masks_are_fixed(galaxy, tmp_path):
    """Un débruitage propre baisse le bruit sans redéfinir le ciel admissible."""
    source, diffuse, noise = galaxy
    reference = QualityReference(source)
    core = diffuse > 0.004
    assert reference.object_mask[core].mean() > 0.8
    assert reference.sky_mask[core].mean() < 0.05
    candidate = tmp_path / "denoised.fits"
    fits.writeto(candidate, (0.1 + diffuse + noise * 0.6).astype("float32"))
    audit = reference.evaluate(candidate)
    assert audit["noise"]["status"] == "PASS"
    assert audit["noise"]["max_noise_ratio"] == pytest.approx(0.6, abs=0.03)
    assert audit["diffuse"]["status"] != "FAIL"
    assert deepcopy(reference) is reference
    assert not reference.sky_mask.flags.writeable
    assert audit["reference"]["masks_sha256"] == reference.mask_fingerprint
    json.dumps(audit, allow_nan=False)


def test_erased_diffuse_signal_is_corrobated(galaxy, tmp_path):
    """La suppression du signal ne peut pas être compensée par un bon bruit."""
    source, diffuse, noise = galaxy
    reference = QualityReference(
        source, argparse.Namespace(quality_enforce_diffuse=True)
    )
    candidate = tmp_path / "erased.fits"
    fits.writeto(candidate, (0.1 + 0.2 * diffuse + 0.5 * noise).astype("float32"))
    audit = reference.evaluate(candidate)
    assert audit["noise"]["max_noise_ratio"] < 0.7
    assert audit["diffuse"]["status"] == "FAIL"
    metrics = dict(max_noise_ratio=0.5, max_ring_fraction=0, diffuse=audit["diffuse"])
    comparison = dict(
        status="validated",
        before_fwhm_px={"mean": 4},
        after_fwhm_px={"mean": 4},
        paired_ratio={"median": 1},
        before_roundness={"mean": 1},
        after_roundness={"mean": 1},
    )
    assert "diffuse_signal_loss" in assess_denoising(comparison, metrics)


def test_red_only_signal_is_excluded(galaxy, tmp_path):
    """L’union des exclusions protège une source invisible en vert/bleu."""
    _, diffuse, noise = galaxy
    source = tmp_path / "red.fits"
    fits.writeto(
        source,
        np.stack([0.1 + diffuse + noise, 0.1 + noise, 0.1 + noise]).astype("float32"),
    )
    reference = QualityReference(source)
    assert reference.sky_mask[diffuse > 0.004].mean() < 0.05


def test_nebula_dominated_field_yields_unknown(tmp_path):
    """Un champ couvert d’émission cohérente n’est pas artificiellement du ciel."""
    yy, xx = np.indices((256, 256))
    emission = 0.03 * np.exp(-((xx - 128) ** 2 + (yy - 128) ** 2) / 90000)
    noise = np.random.default_rng(5).normal(0, 0.0002, xx.shape)
    source = tmp_path / "nebula.fits"
    fits.writeto(source, (0.1 + emission + noise).astype("float32"))
    reference = QualityReference(source)
    result = reference.noise(reference.data)
    assert result["status"] == "UNKNOWN"
    assert result["max_noise_ratio"] is None
    assert reference.sky_mask.mean() < 0.3
    comparison = dict(
        status="validated",
        before_fwhm_px={"mean": 4},
        after_fwhm_px={"mean": 4},
        paired_ratio={"median": 1},
        before_roundness={"mean": 1},
        after_roundness={"mean": 1},
    )
    assert "noise_unknown" in assess_denoising(
        comparison, dict(max_noise_ratio=None, max_ring_fraction=0)
    )


def test_correlated_noise_is_audited(tmp_path):
    """La corrélation et le bootstrap accompagnent la dispersion, hors médiane3x3."""
    rng = np.random.default_rng(90)
    noise = gaussian_filter(rng.normal(0, 0.001, (256, 256)), 0.7)
    source = tmp_path / "correlated.fits"
    fits.writeto(source, (0.1 + noise).astype("float32"))
    reference = QualityReference(source)
    result = reference.noise(reference.data)
    assert result["max_noise_ratio"] == pytest.approx(1)
    assert result["status"] == "UNKNOWN"  # Aucun gain démontré.
    correlations = result["per_channel"][0]["lag1_correlation_before_after"]
    assert np.median([pair[0] for pair in correlations]) > 0.3


def test_metadata_and_invalid_pixels_are_rejected(galaxy, tmp_path):
    """Les métriques ne masquent pas un retournement ou une altération du FITS."""
    source, _, _ = galaxy
    with fits.open(source, mode="update") as hdul:
        hdul[0].header["ROWORDER"] = "TOP-DOWN"
    reference = QualityReference(source)
    candidate = tmp_path / "flipped.fits"
    fits.writeto(
        candidate, fits.getdata(source), fits.Header({"ROWORDER": "BOTTOM-UP"})
    )
    with pytest.raises(ValueError, match="Orientation"):
        reference.evaluate(candidate)
    fits.writeto(candidate, fits.getdata(source)[:20], overwrite=True)
    with pytest.raises(ValueError, match="Géométrie"):
        reference.evaluate(candidate)


def test_quality_configuration_priority_and_persistence(tmp_path):
    """Les nouvelles options suivent CLI > JSON > défauts existants."""
    config = Config(tmp_path / "settings.json")
    config.set("quality_min_noise_gain", 0.1)
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(RestorationQuality, parser)
    args = config.parse_args(parser, ["--quality-min-noise-gain", ".07", "-S"])
    assert args.quality_min_noise_gain == 0.07
    assert args.quality_mode == "reliable"
    assert config.save_requested(args)
    assert (
        json.loads((tmp_path / "settings.json").read_text())["quality_min_noise_gain"]
        == 0.07
    )


def test_unknown_noise_does_not_claim_quality():
    """Une déconvolution peut avoir du gain stellaire mais un bruit UNKNOWN."""
    comparison = dict(
        status="validated",
        before_fwhm_px={"mean": 4},
        after_fwhm_px={"mean": 3},
        paired_ratio={"median": 0.75},
        before_roundness={"mean": 1},
        after_roundness={"mean": 1},
    )
    result = assess_quality(comparison, dict(max_noise_ratio=None, max_ring_fraction=0))
    assert result["criteria"]["noise"]["status"] == "UNKNOWN"
    assert result["accepted"]  # Pas de dégradation inventée à partir d’une absence.


def test_common_matching_and_legacy_policy():
    """Tous les moteurs utilisent les mêmes seuils ; l’auto historique reste explicite."""
    from lib.cosmic_clarity.processors import CosmicClaritySharpenProcessor

    args = argparse.Namespace(
        deconvolution_match_radius=1.3,
        deconvolution_min_stars=12,
        deconvolution_min_match_fraction=0.8,
    )
    radius, minimum, fraction, audit = (
        CosmicClaritySharpenProcessor._matching_thresholds(
            args, Path("/nonexistent.tsv"), 0
        )
    )
    assert (radius, minimum, fraction) == (1.3, 12, 0.8)
    assert audit["mode"] == "common"
    args.quality_mode = "legacy"
    args.deconvolution_force_manual_matching = True
    assert (
        CosmicClaritySharpenProcessor._matching_thresholds(args, Path("/none.tsv"), 0)[
            3
        ]["mode"]
        == "forced-manual"
    )


def test_segmentation_does_not_execute_gradient(galaxy, monkeypatch):
    """Les diagnostics ne réexécutent ni le RBF ni un deuxième gradient."""
    from lib.postprocess import GradientExtractor

    def forbidden(*args, **kwargs):
        """Détecte une confusion entre diagnostic du ciel et correction."""
        raise AssertionError("Aucune extraction de gradient autorisée")

    monkeypatch.setattr(GradientExtractor, "correct_gradient", forbidden)
    source, _, _ = galaxy
    original = source.read_bytes()
    QualityReference(source)
    assert source.read_bytes() == original


def test_invalid_quality_parameters_are_rejected(galaxy):
    """Les seuils ne sont ni silencieusement corrigés ni ignorés."""
    source, _, _ = galaxy
    with pytest.raises(ValueError, match="qualité"):
        QualityReference(source, argparse.Namespace(quality_scales=[float("nan")]))


def test_auxiliary_fits_extensions_are_preserved(tmp_path):
    """Le contrôle de géométrie inclut les HDU, sans modifier une table auxiliaire."""
    source, candidate = tmp_path / "source.fits", tmp_path / "candidate.fits"
    data = np.random.default_rng(7).normal(0.1, 0.001, (128, 128)).astype("float32")
    table = fits.BinTableHDU.from_columns(
        [fits.Column(name="index", format="J", array=[1, 2])]
    )
    fits.HDUList([fits.PrimaryHDU(data), table]).writeto(source)
    reference = QualityReference(source)
    with fits.open(source) as hdul:
        hdul.writeto(candidate)
    assert reference.evaluate(candidate)["noise"]["max_noise_ratio"] == pytest.approx(1)
    fits.writeto(candidate, data, overwrite=True)
    with pytest.raises(ValueError, match="Extensions"):
        reference.evaluate(candidate)


def test_weak_wings_can_contaminate_historic_dark_pixels(galaxy):
    """Le percentile de luminosité n’établit pas l’absence de signal connu."""
    source, diffuse, _ = galaxy
    data = fits.getdata(source)
    historic = data <= np.percentile(data, 40)
    weak_wings = (diffuse > 0.0003) & (diffuse < 0.001)
    assert (historic & weak_wings).sum() > 100
    reference = QualityReference(source)
    assert (reference.object_mask & weak_wings).sum() > 100
