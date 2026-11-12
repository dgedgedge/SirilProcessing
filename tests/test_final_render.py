"""Rendu RANGE unique, publication complète, configuration et erreurs sans reprise."""

import argparse
import configparser
import json
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from lib.auto_ghs import GHSOptimizer
from lib.config import Config
from lib.final_render import AutoGHSFinalRenderer
from lib.postprocess import _PostProcessorSequence
from lib.star_separation import StarNetSeparator


@pytest.fixture
def linear_rgb(tmp_path: Path) -> Path:
    """Crée un RGB linéaire avec gradient diffus et une étoile colorée."""
    y, x = np.indices((48, 48))
    sky = 0.01 + 0.003 * x / 48 + np.random.default_rng(7).normal(0, 0.0003, x.shape)
    stars = 0.4 * np.exp(-((x - 24) ** 2 + (y - 24) ** 2) / 4)
    data = np.array(
        [sky + stars, sky + 0.7 * stars, sky + 0.4 * stars], dtype="float32"
    )
    path = tmp_path / "linear.fits"
    header = fits.Header(
        {"ROWORDER": "TOP-DOWN", "OBJECT": "Synthetic", "SPNLIN": False}
    )
    fits.HDUList(
        [fits.PrimaryHDU(data, header), fits.ImageHDU(np.arange(6), name="AUX")]
    ).writeto(path)
    return path


@pytest.fixture
def fake_separation(monkeypatch):
    """Remplace uniquement le programme externe et garde les vraies branches de rendu."""
    calls = []

    def separate(self, source, report, args):
        """Extrait l'étoile synthétique connue et conserve la géométrie."""
        data = fits.getdata(source)
        y, x = np.indices(data.shape[1:])
        stars = (
            np.array([1, 0.7, 0.4])[:, None, None]
            * 0.4
            * np.exp(-((x - 24) ** 2 + (y - 24) ** 2) / 4)
        )
        starless = data - stars
        calls.append(str(source))
        root = Path(report).parent
        fits.writeto(root / "starless.fits", starless.astype("float32"))
        fits.writeto(root / "stars.fits", stars.astype("float32"))
        return {
            "starless_linear": str(root / "starless.fits"),
            "stars_linear": str(root / "stars.fits"),
            "executable": "/fake/starnet",
        }

    monkeypatch.setattr(StarNetSeparator, "post_process", separate)
    return calls


@pytest.mark.parametrize("chroma", ["off", "balanced", "enhanced"])
def test_single_range_contract_and_published_artifacts(
    linear_rgb: Path, fake_separation, tmp_path: Path, monkeypatch, chroma: str
) -> None:
    """Une séparation, un rendu stellaire, un fond RANGE et une seule fusion effective."""
    import lib.final_render as module

    original = linear_rgb.read_bytes()
    render_calls, optimize_calls, fusion_calls = [], [], []
    real_render = module.ConservativeStarRenderer.render
    real_optimize, real_fusion = GHSOptimizer.optimize, module.recompose

    def stars(*args, **kwargs):
        """Compte le rendu commun des étoiles."""
        render_calls.append(1)
        return real_render(*args, **kwargs)

    def optimize(self, data, strategy, *args, **kwargs):
        """Capture le seul appel d’optimisation du fond."""
        optimize_calls.append((strategy, data.copy()))
        return real_optimize(self, data, strategy, *args, **kwargs)

    def fusion(background, stars, *args):
        """Conserve l'identité de la couche d'étoiles effectivement incorporée."""
        fusion_calls.append(stars)
        return real_fusion(background, stars, *args)

    monkeypatch.setattr(module.ConservativeStarRenderer, "render", staticmethod(stars))
    monkeypatch.setattr(GHSOptimizer, "optimize", optimize)
    monkeypatch.setattr(module, "recompose", fusion)
    result = AutoGHSFinalRenderer().post_process(
        linear_rgb,
        tmp_path / "05_autoghs.json",
        argparse.Namespace(
            compute_backend="cpu", ghs_bins=512, ghs_max_iterations=2, ghs_chroma=chroma
        ),
    )
    assert len(fake_separation) == len(render_calls) == 1
    assert [call[0] for call in optimize_calls] == ["range"]
    assert len(fusion_calls) == 1
    assert set(result["output_images"]) == {"range"}
    assert set(result["output_jpegs"]) == {"range"}
    assert result["output_jpeg"] == result["output_jpegs"]["range"]
    assert not result['trial_jpegs_enabled']
    assert not list((tmp_path/'05_diagnostics/histogram_trials').glob('image_*.jpg'))
    quality = result['strategies']['range']['quartile_stretch']
    assert result['targets_achieved'] == quality.get('targets_achieved')
    assert result['unmet_targets'] == quality.get('unmet_targets', [])
    if quality.get('targets_achieved') is False and quality['status'] != 'disabled':
        assert result['status'] == 'TARGETS_NOT_REACHED'
        assert result['unmet_targets']
    from PIL import Image
    for path in result["output_jpegs"].values():
        with Image.open(path) as image:
            assert image.format == "JPEG" and image.size == (48, 48)
    assert "comparison_paths" not in result and "comparison_previous" not in result
    assert not list(tmp_path.rglob("*comparison*"))
    assert not list(tmp_path.rglob("*peak*"))
    assert result["histogram_paths"]["starless"] == result["histogram_path"]
    assert len(set(result["histogram_paths"].values())) == 2
    assert all(Path(path).is_file() for path in result["histogram_paths"].values())
    work = tmp_path
    assert sorted(path.name for path in work.iterdir() if path.is_dir()) == [
        "01_separation", "02_stars", "03_ghs", "04_recomposition", "05_diagnostics"
    ]
    assert not any(path.name.startswith(".") for path in work.rglob("*"))
    assert all(Path(path).parent == work / "04_recomposition"
               for path in result["output_images"].values())
    stars_on_disk = fits.getdata(result["stars"]["output_image"])
    assert Path(result["stars"]["diagnostic_path"]).parent == work / "02_stars"
    assert Path(result["stars"]["diagnostic_path"]).is_file()
    assert Path(result["stars"]["output_jpeg"]).is_file()
    assert result["parameters"]["star_histogram_strength"] == 0.8
    assert result["parameters"]["ghs_final_strength"] == 1.25
    for strategy, path in result["output_images"].items():
        presentation = result["strategies"][strategy]["presentation_stretch"]
        assert presentation["mode"] == "auto"
        assert presentation["status"] in ("applied", "no admissible improvement")
        assert 0 <= presentation["parameters"]["D"] <= 1.25
        if presentation["status"] == "applied":
            assert presentation["full_validation"][presentation["selected_validation"]]["rejections"] == []
        final = fits.getdata(path)
        background = fits.getdata(result["strategies"][strategy]["starless_image"])
        np.testing.assert_allclose(
            final,
            0.95 * background + stars_on_disk * (1 - 0.95 * background),
            atol=1e-7,
        )
        assert (final > 0.95 * background + 1e-4).any()
        with fits.open(path) as hdul:
            assert hdul[0].header["SPNLIN"]
            assert hdul[0].header["ROWORDER"] == "TOP-DOWN"
            assert hdul[0].header["OBJECT"] == "Synthetic"
            assert hdul["AUX"].data.tolist() == list(range(6))
    assert linear_rgb.read_bytes() == original
    assert json.loads((tmp_path / "05_autoghs.json").read_text()) == result


@pytest.mark.parametrize(
    "failure", ["separation", "optimize", "final_stretch", "fusion", "publication"],
)
def test_failure_publishes_nothing(
    linear_rgb: Path, fake_separation, tmp_path: Path, monkeypatch, failure: str
) -> None:
    """Une panne conserve les diagnostics sans rapport de réussite ni reprise."""
    import lib.final_render as module

    def fail(*args, **kwargs):
        """Panne technique injectée sans résultat utilisable."""
        raise RuntimeError("injected failure")

    if failure == "separation":
        monkeypatch.setattr(StarNetSeparator, "post_process", fail)
    elif failure == "optimize":
        monkeypatch.setattr(GHSOptimizer, "optimize", fail)
    elif failure == "final_stretch":
        monkeypatch.setattr(GHSOptimizer, "final_stretch", fail)
    elif failure == "fusion":
        monkeypatch.setattr(module, "recompose", fail)
    else:
        replace = module.os.replace

        def fail_success(source: Path, target: Path) -> None:
            """Refuse la publication réussie mais autorise le rapport d’erreur."""
            if json.loads(Path(source).read_text()).get("status") != "ERROR":
                fail()
            replace(source, target)

        monkeypatch.setattr(module.os, "replace", fail_success)
    with pytest.raises(RuntimeError, match="injected failure"):
        AutoGHSFinalRenderer().post_process(
            linear_rgb,
            tmp_path / "05_autoghs.json",
            argparse.Namespace(
                compute_backend="cpu", ghs_max_iterations=1, ghs_bins=256
            ),
        )
    assert not list(tmp_path.glob("05_autoghs_render_*"))
    report = json.loads((tmp_path / "05_autoghs.json").read_text())
    assert report["status"] == "ERROR" and report["no_automatic_retry"]
    assert len(fake_separation) <= 1


def test_residual_compensation_is_explicit_and_conservative() -> None:
    """Le transfert conserve la somme ; seuls les domaines invalides sont refusés."""
    background = np.full((3, 8, 8), 0.1, dtype=np.float32)
    residual = np.full_like(background, -0.0001)
    corrected, stars, report = AutoGHSFinalRenderer.compensate_residual(
        background, residual, 0.005
    )
    np.testing.assert_allclose(corrected + stars, background + residual, atol=1e-8)
    assert report["negative_fraction"] == 1
    assert report["transferred_absolute_flux"] > 0
    assert report["tolerance_exceeded"] is False
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        AutoGHSFinalRenderer.compensate_residual(background, residual - 0.1, 0.005)


def test_large_residual_is_compensated_and_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Un dépassement local de type M31 n’interrompt pas une séparation corrigible."""
    original = np.full((3, 8, 8), 0.01, dtype=np.float32)
    background = original.copy()
    original[0, 3, 4] = 0.604377627
    background[0, 3, 4] = 0.966697633
    background[1, 2, 3] = 0.005
    residual = original - background
    initial_background, initial_residual = background.copy(), residual.copy()
    corrected, stars, report = AutoGHSFinalRenderer.compensate_residual(
        background, residual, 0.005,
    )
    np.testing.assert_allclose(corrected + stars, original, atol=3e-8, rtol=0)
    np.testing.assert_array_equal(background, initial_background)
    np.testing.assert_array_equal(residual, initial_residual)
    assert corrected[0, 3, 4] == pytest.approx(original[0, 3, 4])
    assert stars[0, 3, 4] == 0 and stars[1, 2, 3] > 0
    assert report["status"] == "CORRECTED" and report["tolerance_exceeded"]
    assert report["above_tolerance_count"] == 1
    assert report["above_tolerance_fraction"] == pytest.approx(1 / residual.size)
    assert report["sum_preserved_max_abs"] < 3e-8
    assert "somme linéaire" in caplog.text


@pytest.mark.parametrize("tolerance", [-1, 0.1, float("nan")])
def test_invalid_residual_tolerance_is_rejected(tolerance: float) -> None:
    """Un seuil invalide reste une erreur de configuration explicite."""
    data = np.full((3, 4, 4), 0.1, dtype=np.float32)
    with pytest.raises(ValueError, match="ghs-negative-tolerance"):
        AutoGHSFinalRenderer.compensate_residual(data, data * 0, tolerance)


def test_range_rendering_survives_local_starnet_overshoot(
    linear_rgb: Path, fake_separation, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La correction rejoint le GHS RANGE, les exports et les diagnostics réels."""
    separate = StarNetSeparator.post_process

    def with_overshoot(
        self: StarNetSeparator, source: Path, report: Path, args: argparse.Namespace,
    ) -> dict[str, str]:
        """Ajoute à la séparation synthétique un dépassement positif du fond."""
        result = separate(self, source, report, args)
        background = fits.getdata(result["starless_linear"])
        background[0, 24, 24] = 0.9
        residual = fits.getdata(source) - background
        fits.writeto(result["starless_linear"], background, overwrite=True)
        fits.writeto(result["stars_linear"], residual, overwrite=True)
        return result

    monkeypatch.setattr(StarNetSeparator, "post_process", with_overshoot)
    result = AutoGHSFinalRenderer().post_process(
        linear_rgb, tmp_path / "05_autoghs.json",
        argparse.Namespace(compute_backend="cpu", ghs_bins=256, ghs_max_iterations=1),
    )
    assert result["status"] in ("SUCCESS", "NO_IMPROVEMENT", "TARGETS_NOT_REACHED")
    assert result["residual_compensation"]["tolerance_exceeded"]
    assert set(result["output_images"]) == {"range"}
    assert all(Path(path).is_file() for path in result["output_jpegs"].values())


def test_configuration_persistence_precedence_and_instance_snapshot(
    tmp_path: Path,
) -> None:
    """Le chemin et les paramètres GHS suivent CLI > JSON > défaut, avec -S explicite."""
    path = tmp_path / "config.json"
    config = Config(path)
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(_PostProcessorSequence, parser)
    args = config.parse_args(
        parser,
        [
            "--starnet-path",
            str(tmp_path / "starnet++"),
            "--ghs-strategy",
            "peak",
            "--ghs-dynamic-target",
            "0.8",
            "--ghs-histogram-strength",
            "0.7",
            "--ghs-final-strength",
            "1.0",
            "--ghs-log-rms-weight",
            "3.0",
            "--star-faint-boost",
            "4",
            "--star-histogram-strength",
            "0.6",
            "--enable-autoghs",
            "-S",
        ],
    )
    config.bind_parameters(args)
    renderer = AutoGHSFinalRenderer()
    assert config.save_requested(args)
    saved = json.loads(path.read_text())
    assert saved["starnet_path"] == str(tmp_path / "starnet++")
    assert saved["enable_autoghs"] and saved["ghs_strategy"] == "range"
    assert saved["ghs_dynamic_target"] == 0.8
    assert saved["ghs_histogram_strength"] == 0.7
    assert saved["ghs_final_strength"] == 1.0
    assert saved["ghs_log_rms_weight"] == 3.0
    assert saved["star_faint_boost"] == 4
    assert saved["star_histogram_strength"] == 0.6
    parser = argparse.ArgumentParser()
    config.register(_PostProcessorSequence, parser)
    assert parser.parse_args([]).ghs_strategy == "range"
    assert parser.parse_args([]).ghs_dynamic_target == 0.8
    assert parser.parse_args([]).ghs_histogram_strength == 0.7
    assert parser.parse_args([]).ghs_final_strength == 1.0
    assert parser.parse_args([]).ghs_log_rms_weight == 3.0
    assert parser.parse_args([]).star_faint_boost == 4
    assert parser.parse_args([]).star_histogram_strength == 0.6
    updated = config.parse_args(
        parser, ["--ghs-strategy", "range", "--starnet-path", "/other/starnet++",
                 "--ghs-dynamic-target", "0.9", "--ghs-final-strength", "1.25",
                 "--ghs-log-rms-weight", "1.0"]
    )
    assert updated.ghs_strategy == "range"
    assert updated.ghs_dynamic_target == 0.9
    assert updated.ghs_final_strength == 1.25
    assert updated.ghs_log_rms_weight == 1.0
    assert renderer._get_args().ghs_dynamic_target == 0.8
    assert renderer._get_args().ghs_strategy == "range"
    assert renderer._get_args().ghs_final_strength == 1.0
    assert renderer._get_args().ghs_log_rms_weight == 3.0
    assert json.loads(path.read_text()) == saved


def test_discovered_path_can_be_saved(tmp_path: Path, monkeypatch) -> None:
    """Le chemin issu des préférences Siril est concret et sauvegardable avec -S."""
    executable = tmp_path / "starnet++"
    executable.touch()
    executable.chmod(0o755)
    preferences = configparser.ConfigParser()
    preferences["core"] = {"starnet_exe": str(executable)}
    monkeypatch.setattr(
        StarNetSeparator, "preferences", staticmethod(lambda args: (preferences, None))
    )
    args = argparse.Namespace(starnet_path=None)
    args.starnet_path = StarNetSeparator.resolve_executable(args)
    config = Config(tmp_path / "settings.json")
    parser = argparse.ArgumentParser()
    config.register(StarNetSeparator, parser)
    args.save_config = True
    assert config.save_requested(args)
    assert json.loads(Path(config.config_file).read_text())["starnet_path"] == str(
        executable
    )


def test_disabled_stage_preserves_existing_pipeline(
    linear_rgb: Path, tmp_path: Path, monkeypatch
) -> None:
    """Désactiver tous les traitements n'appelle ni StarNet ni GHS et garde l'entrée."""

    def forbidden(*args, **kwargs):
        """Signale toute exécution indue de la phase facultative."""
        pytest.fail("AutoGHS exécuté alors que désactivé")

    monkeypatch.setattr(AutoGHSFinalRenderer, "post_process", forbidden)
    parser = argparse.ArgumentParser()
    _PostProcessorSequence.add_arguments(parser)
    args = parser.parse_args(
        [
            "--disable-gradient",
            "--disable-photometry",
            "--disable-deconvolution",
            "--disable-denoise",
            "--disable-autoghs",
        ]
    )
    assert not args.enable_autoghs
    result = _PostProcessorSequence().post_process(
        linear_rgb, tmp_path / "all.json", args
    )
    assert result == {}


@pytest.mark.parametrize("header", [{"SPNLIN": True}, {"HISTORY": "GHS (pivot: 0.1)"}])
def test_preflight_failure_is_reported(
    linear_rgb: Path, tmp_path: Path, header: dict
) -> None:
    """Le refus d'une entrée déjà étirée écrit aussi un rapport ERROR sans extraction."""
    with fits.open(linear_rgb, mode="update") as hdul:
        hdul[0].header.update(header)
    report = tmp_path / "05_autoghs.json"
    with pytest.raises(ValueError, match="non linéaire"):
        AutoGHSFinalRenderer().post_process(linear_rgb, report, argparse.Namespace())
    assert json.loads(report.read_text())["status"] == "ERROR"
    assert not list(tmp_path.glob("05_autoghs_render_*"))


def test_default_activation_and_explicit_disable(
    linear_rgb: Path, tmp_path: Path, monkeypatch
) -> None:
    """Sans option d'activation, la séquence termine par AutoGHS ; disable reste prioritaire."""
    calls = []

    def render(
        self, input_path: Path, output_path: Path, args: argparse.Namespace
    ) -> dict[str, str]:
        """Enregistre le rendu par défaut sans exécuter les moteurs externes."""
        assert output_path.name == "05_autoghs.json"
        calls.append(input_path)
        return {"output_image": str(input_path)}

    monkeypatch.setattr(AutoGHSFinalRenderer, "post_process", render)
    parser = argparse.ArgumentParser()
    _PostProcessorSequence.add_arguments(parser)
    options = [
        "--disable-gradient",
        "--disable-photometry",
        "--disable-denoise",
        "--disable-deconvolution",
    ]
    args = parser.parse_args(options)
    assert args.enable_autoghs is True
    result = _PostProcessorSequence().post_process(
        linear_rgb, tmp_path / "default.json", args
    )
    assert list(result) == ["autoghs"] and calls == [linear_rgb]
    args = parser.parse_args([*options, "--disable-autoghs"])
    result = _PostProcessorSequence().post_process(
        linear_rgb, tmp_path / "disabled.json", args
    )
    assert result == {} and len(calls) == 1


@pytest.mark.parametrize("with_stars", [False, True])
@pytest.mark.parametrize("counts", [[0, 1, 10, 1000], [0, 0, 0, 0]])
def test_histogram_figure_uses_real_log_axis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, counts: list[int],
    with_stars: bool,
) -> None:
    """Chaque population conserve ses données et axes log sur un fond noir local."""
    from matplotlib import rc_context, rcParams
    from matplotlib.colors import to_rgba
    from matplotlib.figure import Figure
    from lib.final_render import histogram_figure

    captured = []
    savefig = Figure.savefig

    def capture(figure: Figure, *args, **kwargs) -> None:
        """Inspecte les axes réels au moment d'écrire le PNG."""
        right = figure.axes[0]
        assert right.get_yscale() == 'log'
        assert right.get_xscale() == 'linear'
        assert right.get_xlim() == (0, 1)
        assert right.get_title() == "Effectifs en échelle logarithmique"
        assert len(figure.axes) == 2
        assert not figure.axes[1].axison
        assert right.get_legend() is None
        assert len(figure.legends) == 1
        assert figure.get_facecolor() == to_rgba('black')
        assert right.get_facecolor() == to_rgba('black')
        for text in (right.title, right.xaxis.label, right.yaxis.label,
                     figure._suptitle, *figure.legends[0].get_texts(),
                     *figure.axes[1].texts):
            assert min(to_rgba(text.get_color())[:3]) > .7
        assert max(figure.legends[0].get_frame().get_facecolor()[:3]) < .1
        assert max(figure.axes[1].texts[0].get_bbox_patch().get_facecolor()[:3]) < .1
        assert right.get_legend_handles_labels()[1] == [
            "Image finale — étoiles recomposées"
            if with_stars else "Sans étoiles — entrée linéaire"
        ]
        assert len(right.lines) == 1
        assert ("avec étoiles" if with_stars else "sans étoiles") in figure._suptitle.get_text()
        for line in right.lines:
            values = line.get_ydata()
            np.testing.assert_array_equal(
                values.data, np.asarray(counts) * (2 if with_stars else 1)
            )
            np.testing.assert_array_equal(
                np.ma.getmaskarray(values), np.asarray(counts) == 0
            )
        assert right.get_ylim()[0] > 0
        captured.append(True)
        savefig(figure, *args, **kwargs)

    monkeypatch.setattr(Figure, 'savefig', capture)
    destination = tmp_path / "histograms.png"
    with rc_context({'savefig.facecolor': 'white', 'axes.facecolor': 'white',
                     'savefig.transparent': True}):
        histogram_figure(
            {"histograms": [{"counts": counts}],
             "recomposed_histogram": {"counts": [2 * count for count in counts]}},
            destination, with_stars=with_stars,
        )
        assert rcParams['savefig.facecolor'] == rcParams['axes.facecolor'] == 'white'
        assert rcParams['savefig.transparent'] is True
    assert captured == [True] and destination.stat().st_size > 0
    from PIL import Image
    with Image.open(destination) as image:
        assert image.getpixel((0, 0))[:3] == (0, 0, 0)
        if image.mode == 'RGBA':
            assert image.getpixel((0, 0))[3] == 255


def test_smoothed_profile_and_threshold_remain_visible_on_black_background(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L'enveloppe et le repère noirs de l'ancien thème deviennent clairs."""
    from matplotlib.figure import Figure
    from matplotlib.colors import to_rgba
    from lib.final_render import histogram_figure
    from lib.ghs_histogram_shape import piecewise_histogram_profile
    from lib.ghs_linear_histogram import add_regression_floor_objective

    x = (np.arange(128)+.5)/128
    counts = 10**(5-6*x+5*np.maximum(x-.3, 0)-6*np.maximum(x-.6, 0))
    counts[4] = 1e9
    profile = add_regression_floor_objective(piecewise_histogram_profile(counts), 4096)
    save = Figure.savefig

    def inspect(figure: Figure, *args: object, **kwargs: object) -> None:
        """Contrôle les deux repères indépendamment des couleurs des données."""
        legend = figure.legends[0]
        lines = dict(zip((text.get_text() for text in legend.get_texts()), legend.legend_handles))
        for name in ('Enveloppe lissée', 'Seuil à 0,8'):
            assert min(to_rgba(lines[name].get_color())[:3]) > .7
        save(figure, *args, **kwargs)

    monkeypatch.setattr(Figure, 'savefig', inspect)
    histogram_figure(dict(histograms=[dict(counts=counts.tolist())],
                          quartile_stretch={'after': profile}), tmp_path/'shape.png')


def test_ghs_annotation_counts_searches_and_applications() -> None:
    """Distingue recherches, applications, candidats et BP sans compter la chroma."""
    from lib.final_render import ghs_annotation

    report = {
        "iterations": [
            {"accepted": {"D": 1}, "candidates": [{}, {}, {}], "BP": 0.01},
            {"accepted": {"D": 2}, "candidates": [{}, {}], "BP": 0},
            {"accepted": None, "candidates": [{}]},
        ],
        "parameters": {"ghs_max_iterations": 8},
        "stop_reason": "no admissible improvement",
        "initial": {"range_width": 0.01, "peak_width": 0.02},
        "final": {"range_width": 0.02, "peak_width": 0.04},
    }
    text = ghs_annotation(report)
    for expected in ["Recherches effectuées : 3 / 8", "GHS appliqués : 2",
                     "Recherches sans application : 1", "Candidats examinés : 6",
                     "Corrections du point noir : 1", "+100.0 %"]:
        assert expected in text
    assert "aucune amélioration admissible" in text
    assert "quantiles" in text
    report["iterations"] = []
    report["stop_reason"] = "constant distribution"
    assert "Recherches effectuées : 0 / 8" in ghs_annotation(report)
    assert "distribution constante" in ghs_annotation(report)
    assert "non renseignées" in ghs_annotation({})
    report["dynamic_range"] = {
        "iterations": [{"BP": 0.01}, {"BP": 0.002}], "max_iterations": 32,
        "initial_width": 0.12, "final_width": 0.8, "target": 0.8,
        "target_reached": True, "stop_reason": "target reached",
    }
    report["histogram_balance"] = {"strength": 0.5, "clip_limit": 4}
    annotated = ghs_annotation(report)
    assert "ÉGALISATION PARTIELLE" in annotated
    assert "Force : 0.50 — plafond : 4.0" in annotated
    report["presentation_stretch"] = {
        "parameters": {"D": 1.25}, "status": "applied",
    }
    final_annotation = ghs_annotation(report)
    assert "GHS FINAL — SANS ÉTOILES" in final_annotation
    assert "D : 1.25 — appliqué" in final_annotation
    for expected in ["BOUCLE D’EXPANSION RGB", "Passages : 2 / 32",
                     "0.120 → 0.800", "Cible : 0.80 — atteinte"]:
        assert expected in annotated



def test_ordered_autoghs_directory_is_reset_on_rerun(
    linear_rgb: Path, fake_separation, tmp_path: Path
) -> None:
    """Deux lancements réutilisent la même arborescence sans produits périmés."""
    renderer = AutoGHSFinalRenderer()
    args = argparse.Namespace(
        compute_backend="cpu", ghs_strategy="range", ghs_bins=256,
        ghs_max_iterations=1,
    )
    report = tmp_path / "05_autoghs.json"
    first = renderer.post_process(linear_rgb, report, args)
    legacy_work = tmp_path / "05_autoghs_work"
    legacy_work.mkdir()
    stale = legacy_work / "obsolete.fits"
    stale.write_bytes(b"obsolete")
    work = tmp_path
    legacy_stage = legacy_work / "001"
    legacy_stage.mkdir()
    (legacy_stage / "obsolete.fits").write_bytes(b"obsolete")
    (work / "03_ghs/linear_starless_ghs_peak.fits").write_bytes(b"obsolete")
    (work / "03_ghs/linear_ghs_comparison_starless.png").write_bytes(b"obsolete")
    (work / "04_recomposition/linear_render_peak.fits").write_bytes(b"obsolete")
    (work / "04_recomposition/linear_render_peak.jpg").write_bytes(b"obsolete")
    legacy = json.loads(report.read_text())
    legacy["strategies"]["peak"] = dict(legacy["strategies"]["range"])
    report.write_text(json.dumps(legacy))
    second = renderer.post_process(linear_rgb, report, args)
    assert first["output_images"] == second["output_images"]
    assert "comparison_previous" not in second and "comparison_paths" not in second
    assert not list(tmp_path.rglob("*peak*"))
    assert not list(tmp_path.rglob("*comparison*"))
    assert not stale.exists()
    assert not legacy_stage.exists()
    assert not list(work.rglob("001"))
    assert len(fake_separation) == 2
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "01_separation", "02_stars", "03_ghs", "04_recomposition",
        "05_autoghs.json", "05_diagnostics", "linear.fits"
    ]
    source = tmp_path / "03_ghs/linear_input.fits"
    source.write_bytes(linear_rgb.read_bytes())
    before = source.read_bytes()
    with pytest.raises(ValueError, match="réinitialisé"):
        renderer.post_process(source, report, args)
    assert source.read_bytes() == before


def test_starless_overshoot_cannot_hide_invalid_reconstructed_source() -> None:
    """Refuser une décomposition dont la somme est réellement hors domaine."""
    background = np.full((3, 8, 8), 1.00004, dtype=np.float32)
    with pytest.raises(ValueError, match=r'\[0, 1\]'):
        AutoGHSFinalRenderer.compensate_residual(
            background, np.zeros_like(background), .005,
        )


def test_ghs_annotation_reports_regression_floor() -> None:
    """L'encart distingue un seuil atteint d'une droite non mesurable."""
    from lib.final_render import ghs_annotation

    floor = dict(minimum_count=10, predicted_log10_count=1.2, achieved=True)
    report = {'quartile_stretch': {'after': {'regression_floor': floor}}}
    text = ghs_annotation(report)
    assert 'DROITE LOG À 0,8' in text
    assert 'Seuil : 10 pixels/classe' in text
    assert 'Prédiction log10 : 1.200' in text
    assert 'Seuil atteint' in text
    floor.update(predicted_log10_count=None, achieved=False)
    text = ghs_annotation(report)
    assert 'non mesurable' in text and 'Seuil non atteint' in text


def test_annotation_shows_adaptive_and_core_contrast_separately() -> None:
    """L'encart distingue le contraste brillant adapté du cœur protégé."""
    from lib.final_render import ghs_annotation

    report = {'quartile_stretch': {'sparse_tail': {
        'effective_retention': .502165, 'requested_retention': .9,
    }}}
    text = ghs_annotation(report)
    assert 'Brillant : 50.2 %' in text
    assert 'Cœur : 90.0 %' in text


def test_annotation_cannot_hide_an_unsupported_floor_and_unreached_tail() -> None:
    """L'encart expose la portion mesurée, l'extrapolation et la cible manquée."""
    from lib.final_render import ghs_annotation

    report = {'quartile_stretch': {'targets_achieved': False, 'after': {
        'measurable': True, 'line_start': .015625, 'line_end': .4453125,
        'tail': {'target': .25, 'height': 0},
        'regression_floor': {'minimum_count': 10, 'predicted_log10_count': 1.23,
                             'achieved': False, 'extrapolated': True},
    }}}
    text = ghs_annotation(report)
    assert 'Objectifs : incomplets' in text
    assert '0.016 → 0.445' in text
    assert '0.0 % / 25.0 %' in text
    assert 'Seuil non atteint' in text and 'Extrapolation hors du segment' in text


@pytest.mark.parametrize('location', ['profile', 'after', 'shape_preservation', 'summary'])
def test_annotation_shows_peak_shift_and_its_limit(location: str) -> None:
    """Essais, présentation et synthèse exposent la même mesure de sélection."""
    from lib.final_render import ghs_annotation

    audit = {'peak_shift': .0546875, 'maximum_peak_shift': .25}
    if location == 'summary':
        report = {'quartile_stretch': {'after': {'shape_preservation': audit, 'peak': .1}}}
    else:
        trial = {'stage': 'Essai', 'status': 'rejected'}
        trial[location] = audit if location == 'shape_preservation' else {'shape_preservation': audit, 'peak': .1}
        report = {'diagnostic_trial': trial}
    text = ghs_annotation(report)
    assert 'Déplacement du pic : 0.054688' in text
    assert 'Plafond du déplacement : 0.250000' in text
    assert 'Intensité 0–1 ; référence de forme' in text
    assert '(estimé)' not in text
    if location != 'shape_preservation':
        assert 'Position du pic : 0.100000' in text


def test_summary_annotation_identifies_the_selected_series_image() -> None:
    """Les synthèses principales reprennent le numéro résolu par la série."""
    from lib.final_render import ghs_annotation

    assert 'Essai sélectionné : 054' in ghs_annotation({'selected_trial_index': 54})
    assert 'Essai sélectionné : non renseigné' in ghs_annotation({'selected_trial_index': None})


@pytest.mark.parametrize('shift', [None, float('nan'), float('inf'), 0.])
def test_annotation_distinguishes_estimated_and_unavailable_peak_shifts(shift: float | None) -> None:
    """Une absence ou une mesure non finie ne devient jamais un pic immobile."""
    from lib.final_render import ghs_annotation

    report = {'diagnostic_trial': {
        'stage': 'Essai', 'status': 'rejected', 'estimated': True,
        'profile': {'shape_preservation': {'peak_shift': shift}},
    }}
    text = ghs_annotation(report)
    value = '0.000000' if shift == 0. else 'non renseigné'
    assert f'Déplacement du pic (estimé) : {value}' in text
    assert 'Plafond du déplacement' not in text


def test_decline_retention_rejection_does_not_claim_the_curve_is_horizontal() -> None:
    """Le rejet de M33 031 explique le ratio global, sans confondre droite et plateau."""
    from lib.final_render import ghs_annotation

    trial = dict(stage='Essai', status='rejected', estimated=True,
                 rejections=['global histogram flattening'], profile=dict(shape_preservation=dict(
                     drop_retention=.5751766792560526, minimum_drop_retention=.65)))
    text = ghs_annotation({'diagnostic_trial': trial})
    assert 'Conservation de décroissance' in text
    assert '57.5 % < 65.0 %' in text
    assert 'aplatie' not in text and 'horizontal' not in text


@pytest.mark.parametrize('model', ['line', 'piecewise'])
def test_optional_trial_jpegs_are_published_for_each_histogram(
    linear_rgb: Path, fake_separation: list[str], tmp_path: Path, model: str,
) -> None:
    """Le pipeline complet publie la galerie et les JPEG des mêmes pixels finaux."""
    from PIL import Image

    original = linear_rgb.read_bytes()
    result = AutoGHSFinalRenderer().post_process(
        linear_rgb, tmp_path/'trial_run.json', argparse.Namespace(
            compute_backend='cpu', ghs_bins=512, ghs_max_iterations=1,
            ghs_histogram_model=model, ghs_quartile_iterations=1,
            ghs_quartile_validations=1, ghs_save_trial_jpegs=True,
        ),
    )
    assert result['trial_jpegs_enabled']
    assert result['parameters']['ghs_save_trial_jpegs']
    series = result['histogram_series']
    assert series[-1]['population'] == 'with_stars'
    selected = result['selected_trial_index']
    assert selected is not None and 1 <= selected < len(series)-1
    assert series[-1]['selected_trial_index'] == series[-2]['selected_trial_index'] == selected
    assert result['strategies']['range']['selected_trial_index'] == selected
    assert Path(series[-1]['image_jpeg']).read_bytes() == Path(result['output_jpeg']).read_bytes()
    for index, item in enumerate(series, 1):
        assert Path(item['image_jpeg']).name == f'image_{index:03d}.jpg'
        with Image.open(item['image_jpeg']) as image:
            assert image.format == 'JPEG' and image.size == (48, 48)
        assert ('with_stars_jpeg' in item) == (item['population'] == 'starless')
    assert linear_rgb.read_bytes() == original


def test_trial_jpeg_write_error_closes_the_disk_cache(
    linear_rgb: Path, fake_separation: list[str], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un échec d'écriture reste fatal et ferme le cache temporaire des captures."""
    from lib.ghs_diagnostics import HistogramDiagnostics
    from lib.type_defs import JSONReport

    recorders = []

    def fail_capture(self: HistogramDiagnostics, record: JSONReport, data: np.ndarray) -> None:
        """Simule un disque plein dès la première capture de la phase GHS."""
        recorders.append(self)
        raise OSError('trial JPEG disk full')

    monkeypatch.setattr(HistogramDiagnostics, 'capture', fail_capture)
    report = tmp_path/'failure.json'
    with pytest.raises(OSError, match='disk full'):
        AutoGHSFinalRenderer().post_process(
            linear_rgb, report, argparse.Namespace(compute_backend='cpu', ghs_save_trial_jpegs=True),
        )
    assert recorders and all(recorder._cache is None for recorder in recorders)
    assert json.loads(report.read_text())['status'] == 'ERROR'


@pytest.mark.parametrize('trial', [False, True])
def test_annotation_explains_render_quality_without_turning_penalties_into_rejections(trial: bool) -> None:
    """Le score et ses contributions sont visibles sur la série comme sur la synthèse."""
    from lib.final_render import ghs_annotation

    quality = dict(available=True, estimated=False, score=1.12, cnr_retention=.96,
                   cnr_reward=.96, visibility_reward=.2, light_reward=.01,
                   cnr_penalty=0., shape_penalty=.02, rise_penalty=.01, bright_penalty=.02)
    profile = dict(render_quality=quality,
                   shape_preservation=dict(rejections=['global histogram flattening']))
    report = (dict(diagnostic_trial=dict(stage='Profil', status='admissible', profile=profile))
              if trial else dict(quartile_stretch=dict(after=profile)))
    text = ghs_annotation(report)
    assert 'Score : 1.1200' in text
    assert 'Contraste/bruit conservé : 96.0 %' in text
    assert 'Pénalité compression brillante : 0.020' in text
    assert 'rejeté' not in text
    quality['available'] = False
    assert 'QUALITÉ : non mesurable' in ghs_annotation(report)


def test_trial_annotation_explains_the_rgb_blackpoint_budget() -> None:
    """La montée en luminosité ne doit pas être confondue avec le minimum RGB contrôlé."""
    from lib.final_render import ghs_annotation

    audit = dict(BP=1.7e-5, budget=.001, loss_fraction_before=.0009933669,
                 remaining_loss_budget=.0000066331,
                 threshold_basis='minimum_initially_positive_rgb_channel')
    text = ghs_annotation(dict(diagnostic_trial=dict(
        stage='Essai', status='admissible', post_BP=audit['BP'], post_blackpoint=audit,
    )))
    assert 'BP après courbe : 1.7e-05' in text
    assert 'Perte avant BP : 0.09934 %' in text
    assert 'Marge avant BP : 0.00066 %' in text
    assert 'Budget BP total : 0.10000 %' in text
    assert 'BP commun RGB' in text
