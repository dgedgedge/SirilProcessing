"""Validation des étoiles seules et de la fusion bornée des deux variantes."""

from pathlib import Path

import numpy as np
import pytest

from lib.star_rendering import (
    ConservativeStarRenderer, recompose, stellar_peak_positions,
)


def test_asinh_human_is_monotone_and_uses_rgbblend() -> None:
    """Le mode human reste monotone et RGBBlend protège le gamut des noyaux."""
    light = np.linspace(0, 1, 1024).reshape(32, 32)
    data = np.array([light, 0.6 * light, 0.3 * light], dtype="float32")
    result, report = ConservativeStarRenderer.render(data)
    assert np.all(np.diff(result[0].ravel()) >= 0)
    assert result.max() == pytest.approx(0.85)
    assert result[0, 0, 1] > data[0, 0, 1]
    linked = result[0] < .849
    np.testing.assert_allclose(result[1][linked], 0.6 * result[0][linked], atol=1e-7)
    assert report['color_mode'] == 'human'
    assert result[1, -1, -1] > .6 * result[0, -1, -1]
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
    assert Path(result["output_jpeg"]).is_file()
    assert Path(result["diagnostic_path"]).is_file()


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
    linked = enhanced[0] < .849
    np.testing.assert_allclose(enhanced[1][linked], 0.6 * enhanced[0][linked], atol=1e-7)
    np.testing.assert_allclose(enhanced[2][linked], 0.3 * enhanced[0][linked], atol=1e-7)
    assert report["faint_intensity_gain"] > 3.5
    expected, _ = ConservativeStarRenderer.render(data, faint_boost=1)
    np.testing.assert_array_equal(previous, expected)


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
    assert parser.parse_args([]).star_max_width_ratio == 2
    assert parser.parse_args([]).star_stretch_max_width_ratio is None
    assert parser.parse_args([]).star_histogram_strength == 1
    assert parser.parse_args([]).star_noise_sigma == 0
    assert parser.parse_args([]).star_boost_fraction == .25
    assert parser.parse_args(["--star-max-width-ratio", "1.5"]).star_max_width_ratio == 1.5
    assert parser.parse_args(["--star-faint-boost", "3"]).star_faint_boost == 3
    light = np.array([[0, 0.0001, 0.01, 0.4, 1]], dtype=np.float32)
    data = np.array([light, light * 0.6, light * 0.3])
    previous, _ = ConservativeStarRenderer.render(data, faint_boost=3)
    stronger, report = ConservativeStarRenderer.render(data)
    assert stronger[0, 0, 1] / previous[0, 0, 1] == pytest.approx(5 / 3, rel=0.001)
    np.testing.assert_array_equal(stronger > 0, data > 0)
    linked = stronger[0] < .849
    np.testing.assert_allclose(stronger[1][linked], 0.6 * stronger[0][linked], atol=1e-7)
    assert stronger.max() == pytest.approx(0.85)
    assert report["faint_boost"] == 5
    assert report["max_width_ratio"] == 2
    assert report["faint_intensity_gain"] == pytest.approx(5.8879, rel=1e-4)


def test_adaptive_histogram_reveals_faint_peaks_with_bounded_width() -> None:
    """Un champ de pics faibles gagne en contraste sous les mêmes gardes RGB/FWHM."""
    y, x = np.indices((144, 144))
    light = np.zeros_like(x, dtype=np.float32)
    for (cy, cx), amplitude in zip(
        ((cy, cx) for cy in range(18, 130, 20) for cx in range(18, 130, 20)),
        np.geomspace(1e-4, 0.01, 36),
    ):
        light += amplitude * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / 2)
    data = np.array([light, 0.6 * light, 0.3 * light])
    fixed, previous = ConservativeStarRenderer.render(data, histogram_strength=0)
    default, default_report = ConservativeStarRenderer.render(data, visible_fraction=0)
    assert default_report["histogram_balance"]["requested_strength"] == 1
    assert default_report['visibility']['above_threshold'] >= previous['visibility']['above_threshold']
    assert default_report['boost_selection']['fraction'] == .25
    assert default_report['boost_selection']['selected_count'] <= 36
    assert default_report['boost_selection']['mode'] == 'histogram_quantile'
    assert np.all(default >= fixed - 1e-7)
    adapted, report = ConservativeStarRenderer.render(
        data, histogram_strength=0.8, boost_fraction=1,
    )
    assert report["visibility"]["candidate_count"] == 36
    assert report["visibility"]["above_threshold"] > previous["visibility"]["above_threshold"]
    assert report["histogram_balance"]["accepted_strength"] > 0
    assert report["histogram_balance"]["iterations"][-1]["accepted"]
    assert report["fwhm"]["median_ratio"] <= 2
    assert np.all(adapted >= fixed - 1e-7)
    assert adapted.max() <= 0.85
    order = np.argsort(light.ravel())
    assert np.all(np.diff(adapted[0].ravel()[order]) >= -1e-7)
    np.testing.assert_array_equal(adapted > 0, data > 0)
    fixed_sum = fixed.sum(axis=0)
    adapted_sum = adapted.sum(axis=0)
    active = (fixed_sum > 1e-7) & (adapted_sum > 1e-7)
    np.testing.assert_allclose(
        adapted[:, active] / adapted_sum[active],
        fixed[:, active] / fixed_sum[active], atol=2e-7,
    )
    assert report["faint_intensity_gain"] > report["fixed_curve_faint_intensity_gain"]


@pytest.mark.parametrize("level", [0, 0.1, 1])
def test_constant_stars_do_not_invent_peaks(level: float) -> None:
    """Un champ constant n'est pas égalisé et ne devient pas une liste d'étoiles."""
    data = np.full((3, 20, 20), level, dtype=np.float32)
    result, report = ConservativeStarRenderer.render(data)
    assert report["visibility"]["candidate_count"] == 0
    assert report["histogram_balance"]["accepted_strength"] == 0
    if level == 0:
        np.testing.assert_array_equal(result, data)


def test_flat_peak_is_counted_once() -> None:
    """Un plateau connexe ne multiplie pas artificiellement les pics candidats."""
    data = np.zeros((3, 24, 24), dtype=np.float32)
    data[:, 10:13, 10:13] = 0.1
    positions = stellar_peak_positions(data)
    assert len(positions[0]) == 1


@pytest.mark.parametrize("strength", [-0.1, 1.1, float("nan"), float("inf")])
def test_invalid_histogram_strength(strength: float) -> None:
    """L'égalisation stellaire refuse les forces non finies ou hors [0,1]."""
    with pytest.raises(ValueError):
        ConservativeStarRenderer.render(
            np.full((3, 8, 8), 0.1), histogram_strength=strength,
        )


def width_limited_star() -> np.ndarray:
    """Construit une étoile brillante dont le renforcement élargit le profil."""
    y, x = np.indices((80, 80))
    light = 0.8 * np.exp(-((x - 25.3) ** 2 + (y - 25.7) ** 2) / 4)
    return np.array([light, 0.7 * light, 0.4 * light], dtype=np.float32)


def test_first_admissible_base_boost_is_selected() -> None:
    """S'arrêter à 2 après les rejets 5/4/3, en préservant source et couleurs."""
    data = width_limited_star()
    original = data.copy()
    rendered, report = ConservativeStarRenderer.render(
        data, max_width_ratio=1.4, histogram_strength=0,
    )
    iterations = report['width_adaptation']['iterations']
    assert [item['faint_boost'] for item in iterations] == [5, 4, 3, 2]
    assert [item['accepted'] for item in iterations] == [False, False, False, True]
    assert report['requested_faint_boost'] == 5
    assert report['faint_boost'] == 2
    assert report['fwhm']['median_ratio'] == pytest.approx(1.30794, abs=1e-4)
    assert report['width_adaptation']['stop_reason'] == 'width limited'
    reference, _ = ConservativeStarRenderer.render(
        data, faint_boost=2, max_width_ratio=1.4, histogram_strength=0,
    )
    np.testing.assert_array_equal(rendered, reference)
    np.testing.assert_array_equal(data, original)
    np.testing.assert_array_equal(rendered > 0, data > 0)
    assert report['color_mode'] == 'human'
    assert rendered.max() <= 0.85


def test_admissible_requested_boost_is_unchanged() -> None:
    """Ne pas essayer de force inférieure lorsque la première satisfait la garde."""
    _, report = ConservativeStarRenderer.render(width_limited_star(), max_width_ratio=2, contrast_mode='pointwise')
    assert report['faint_boost'] == report['requested_faint_boost'] == 5
    assert len(report['width_adaptation']['iterations']) == 1
    assert report['width_adaptation']['stop_reason'] == 'requested boost'


@pytest.mark.parametrize('boost, attempts', [(5, 5), (1.4, 2), (1, 1), (10, 10)])
def test_no_admissible_boost_fails_with_bounded_diagnostics(
    boost: float, attempts: int,
) -> None:
    """Inclure le gain minimal et les mesures dans une erreur bornée et explicite."""
    with pytest.raises(ValueError) as failure:
        ConservativeStarRenderer.render(
            width_limited_star(), faint_boost=boost,
            max_width_ratio=1, histogram_strength=0,
        )
    message = str(failure.value)
    assert f'après {attempts} essai(s)' in message
    assert f'gain {boost:g} : ratio' in message
    assert 'gain 1 : ratio 1.077613' in message


def test_unmeasurable_width_remains_explicit() -> None:
    """Ne pas inventer de mesure ou de validation sur un champ constant."""
    _, report = ConservativeStarRenderer.render(np.zeros((3, 20, 20)))
    assert report['fwhm']['median_ratio'] is None
    assert report['width_adaptation']['stop_reason'] == 'unmeasurable width'
    assert len(report['width_adaptation']['iterations']) == 1


def test_stellar_trial_recording_preserves_render_and_source() -> None:
    """Collecter les courbes et essais adaptatifs ne modifie aucun calcul stellaire."""
    from lib.ghs_diagnostics import HistogramDiagnostics

    y, x = np.indices((144, 144))
    light = np.zeros_like(x, dtype=np.float32)
    for (cy, cx), amplitude in zip(
        ((cy, cx) for cy in range(18, 130, 20) for cx in range(18, 130, 20)),
        np.geomspace(1e-4, 0.01, 36),
    ):
        light += amplitude * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / 2)
    source = np.array([light, .6 * light, .3 * light])
    reference = source.copy()
    expected, expected_report = ConservativeStarRenderer.render(source, histogram_strength=.8)
    recorder = HistogramDiagnostics()
    try:
        actual, report = ConservativeStarRenderer.render(
            source, histogram_strength=.8, diagnostics=recorder,
        )
        trials = recorder.stellar_histogram_report()['histogram_stages']
        assert trials[0]['status'] == 'input'
        assert trials[-1]['status'] == 'selected'
        assert any(trial['stage'].startswith('Courbe fixe') for trial in trials)
        assert any(trial['stage'].startswith('Courbe quantile') for trial in trials)
        assert all('histogram' in trial for trial in trials)
        np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(source, reference)
        assert report == expected_report
    finally:
        recorder.close()


@pytest.fixture
def noisy_star_field() -> np.ndarray:
    """Construit quatre étoiles isolées sur un résidu gaussien positif reproductible."""
    y, x = np.indices((160, 160))
    light = np.maximum(np.random.default_rng(42).normal(0, 0.0005, x.shape), 0)
    for cy, cx, amplitude in ((30, 30, .015), (30, 85, .08),
                              (85, 30, .4), (85, 85, .9)):
        light += amplitude * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / 4)
    return np.array([light, .7 * light, .4 * light], dtype=np.float32)


def test_global_histogram_stretch_does_not_denoise_stars(
    noisy_star_field: np.ndarray,
) -> None:
    """Le rendu global ne filtre pas le résidu et conserve une recette exacte."""
    source = noisy_star_field.copy()
    rendered, report = ConservativeStarRenderer.render(source, max_width_ratio=1.2)
    noise = report['noise_suppression']
    assert report['histogram_balance']['calibration_peak_count'] >= 4
    assert report['histogram_only']
    assert noise['threshold'] == 0
    expected, _ = ConservativeStarRenderer.render(source, max_width_ratio=1.2, noise_sigma=0)
    np.testing.assert_array_equal(rendered, expected)
    assert rendered[0, 30, 30] > source[0, 30, 30]
    assert report['fwhm']['median_ratio'] <= 1.2
    assert report['fwhm']['p10_ratio'] >= .9
    assert report['wings']['p10_ratio'] >= .9
    assert report['after']['white_fraction_rgb'] == [0, 0, 0]
    assert report['faint_intensity_gain'] > 0
    linked = rendered[0] < .849
    np.testing.assert_allclose(rendered[1][linked], .7 * rendered[0][linked], atol=1e-7)
    np.testing.assert_array_equal(source, noisy_star_field)
    np.testing.assert_array_equal(rendered[source == 0], 0)
    from lib.stretch_transfer import CumulativeStretchTransform, stellar_operation

    replay = CumulativeStretchTransform(
        (float(source.min()), float(source.max())),
        [stellar_operation(report, report['histogram_balance'])],
    )
    np.testing.assert_array_equal(replay.apply_rgb(source), rendered)


def test_saved_noise_setting_cannot_reactivate_denoising(noisy_star_field: np.ndarray) -> None:
    """Même un ancien facteur de bruit élevé laisse la même courbe globale."""
    y, x = np.indices(noisy_star_field.shape[1:])
    removed = .009 * np.exp(-((x - 30) ** 2 + (y - 30) ** 2) / 4)
    removed += .072 * np.exp(-((x - 85) ** 2 + (y - 30) ** 2) / 4)
    source = np.maximum(noisy_star_field - np.array([removed, .7 * removed, .4 * removed]), 0)
    _, report = ConservativeStarRenderer.render(source.astype('f4'), noise_sigma=20)
    noise = report['noise_suppression']
    assert noise['threshold'] == noise['requested_threshold'] == 0
    assert noise['requested_sigma_factor'] == 20
    actual, _ = ConservativeStarRenderer.render(source.astype('f4'), noise_sigma=20)
    expected, _ = ConservativeStarRenderer.render(source.astype('f4'), noise_sigma=0)
    np.testing.assert_array_equal(actual, expected)
    assert report['wings']['p10_ratio'] >= .9


@pytest.mark.parametrize('sigma', [-1, 21, float('nan'), float('inf')])
def test_invalid_noise_sigma_is_rejected(sigma: float) -> None:
    """La porte refuse les facteurs non finis ou hors domaine même sur fond nul."""
    with pytest.raises(ValueError, match='bruit stellaire'):
        ConservativeStarRenderer.render(np.zeros((3, 16, 16)), noise_sigma=sigma)


def test_noise_gate_without_profiles_remains_unvalidated() -> None:
    """Un bruit sans étoile mesurable ne fournit pas de validation des ailes."""
    light = np.random.default_rng(7).uniform(0, .0001, (32, 32)).astype('f4')
    _, report = ConservativeStarRenderer.render(np.array([light, light, light]))
    assert report['noise_suppression']['requested_threshold'] == 0
    assert report['noise_suppression']['threshold'] == 0
    assert report['wings']['count'] == 0


def test_profile_width_matches_gaussian_reference() -> None:
    """La largeur interpolée retrouve 2 sigma sqrt(2 ln 2) sur une gaussienne."""
    from lib.star_rendering import profile_widths

    y, x = np.indices((40, 40))
    sigma = 1.4
    light = .1 * np.exp(-((x - 20) ** 2 + (y - 20) ** 2) / (2 * sigma ** 2))
    source = np.array([light, .7 * light, .4 * light])
    measured = profile_widths(source, source)
    assert measured['count'] == 1
    expected = 2 * sigma * np.sqrt(2 * np.log(2))
    assert measured['before_median_px'] == pytest.approx(expected, rel=.04)
    assert measured['median_ratio'] == pytest.approx(1)



@pytest.mark.parametrize('height', [0, 1, float('nan')])
def test_invalid_profile_height_is_rejected(height: float) -> None:
    """Les hauteurs non finies ou aux bornes ne définissent pas de largeur."""
    from lib.star_rendering import profile_widths

    source = np.zeros((3, 16, 16))
    with pytest.raises(ValueError, match='Hauteur relative'):
        profile_widths(source, source, relative_height=height)


def test_stellar_noise_options_follow_cli_json_and_instance_snapshots(tmp_path: Path) -> None:
    """Les réglages stellaires persistent sans changer la garde de recomposition."""
    import argparse
    import json

    from lib.config import Config

    path = tmp_path / 'stars.json'
    path.write_text(json.dumps(dict(star_noise_sigma=8, star_stretch_max_width_ratio=1.3)))
    config = Config(path)
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(ConservativeStarRenderer, parser)
    args = config.parse_args(parser, [])
    config.bind_parameters(args)
    first = ConservativeStarRenderer()
    assert first._get_args().star_noise_sigma == 8
    assert first._get_args().star_stretch_max_width_ratio == 1.3
    args = config.parse_args(parser, ['--star-noise-sigma', '0',
                                      '--star-stretch-max-width-ratio', '1.4',
                                      '--star-boost-fraction', '.5', '-S'])
    config.bind_parameters(args)
    assert config.save_requested(args)
    saved = json.loads(path.read_text())
    assert saved['star_noise_sigma'] == 0
    assert saved['star_stretch_max_width_ratio'] == 1.4
    assert saved['star_max_width_ratio'] == 2
    assert saved['star_boost_fraction'] == .5
    assert first._get_args().star_boost_fraction == .25
    assert first._get_args().star_noise_sigma == 8
    assert first._get_args().star_stretch_max_width_ratio == 1.3


@pytest.mark.parametrize('ratio', [.9, float('nan'), float('inf')])
def test_invalid_stellar_stretch_width_limit_is_rejected(ratio: float) -> None:
    """La nouvelle garde stellaire doit être finie et au moins égale à un."""
    with pytest.raises(ValueError, match='Paramètres stellaires'):
        ConservativeStarRenderer.render(np.zeros((3, 16, 16)), max_width_ratio=ratio)


@pytest.mark.parametrize('parameters,expected', [
    ({}, 2),
    ({'star_max_width_ratio': 1.5}, 1.5),
    ({'star_max_width_ratio': 1.5, 'star_stretch_max_width_ratio': None}, 1.5),
    ({'star_max_width_ratio': 1.5, 'star_stretch_max_width_ratio': 1.2}, 1.2),
])
def test_stretch_width_limit_inherits_without_overriding_existing_setting(
    parameters: dict[str, float | None], expected: float,
) -> None:
    """Le défaut propre absent ne doit pas imposer 1,2 à une limite existante de 1,5."""
    import argparse

    assert ConservativeStarRenderer.stretch_width_limit(
        argparse.Namespace(**parameters),
    ) == expected


@pytest.fixture
def fractional_star_field() -> np.ndarray:
    """Champ de 36 gaussiennes permettant une sélection par amplitude sans bruit."""
    y, x = np.indices((144, 144))
    light = np.zeros_like(x, dtype='f4')
    for (cy, cx), amplitude in zip(
        ((cy, cx) for cy in range(18, 130, 20) for cx in range(18, 130, 20)),
        np.geomspace(1e-4, .01, 36),
    ):
        light += amplitude * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / 2)
    return np.array([light, .6 * light, .3 * light])


def test_default_fraction_builds_pointwise_quantile_curve(
    fractional_star_field: np.ndarray,
) -> None:
    """Les 25 % calent une courbe globale sans coordonnées ni masque spatial."""
    source = fractional_star_field
    original = source.copy()
    base, _ = ConservativeStarRenderer.render(source, boost_fraction=0, contrast_mode='pointwise')
    rendered, report = ConservativeStarRenderer.render(source, contrast_mode='pointwise', visible_fraction=0)
    selection = report['boost_selection']
    assert selection['fraction'] == .25
    assert selection['selected_count'] <= selection['budget'] == 9
    assert selection['mode'] == 'histogram_quantile'
    assert selection['bright_protection_policy'] == 'monotone_cap_v1'
    assert 'selected' not in selection and 'others' not in selection
    assert selection['visible_target'] == pytest.approx(.08)
    assert selection['actually_boosted_count'] > selection['selected_count']
    assert report['fwhm']['median_ratio'] <= 2
    assert report['after']['white_fraction_rgb'] == [0, 0, 0]
    from lib.stretch_transfer import CumulativeStretchTransform, stellar_operation
    replay = CumulativeStretchTransform(
        (float(source.min()), float(source.max())),
        [stellar_operation(report, report['histogram_balance'])],
    )
    np.testing.assert_array_equal(replay.apply_rgb(source), rendered)
    ramp = np.geomspace(1e-8, 1., 65536).astype(np.float64)
    neutral_curve = replay.apply_rgb(np.stack((ramp, ramp, ramp)), ideal=True)[0]
    assert np.min(np.diff(neutral_curve)) >= -1e-7
    np.testing.assert_array_equal(source, original)
    assert report['width_adaptation']['iterations'][-1]['histogram_balance']['requested_strength'] == 0


@pytest.mark.parametrize('fraction', [-.1, 1.1, float('nan'), float('inf')])
def test_invalid_boost_fraction(fraction: float) -> None:
    """Refuser une fraction invalide même sur une couche vide d'étoiles."""
    with pytest.raises(ValueError, match='Fraction stellaire'):
        ConservativeStarRenderer.render(np.zeros((3, 16, 16)), boost_fraction=fraction)
