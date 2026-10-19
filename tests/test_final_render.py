"""Contrat both, publication complète, config persistante et erreurs sans reprise."""

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
def test_both_contract_and_published_artifacts(
    linear_rgb: Path, fake_separation, tmp_path: Path, monkeypatch, chroma: str
) -> None:
    """Une séparation, un rendu stellaire, deux fonds et deux fusions effectives."""
    import lib.final_render as module

    original = linear_rgb.read_bytes()
    render_calls, optimize_calls, fusion_calls = [], [], []
    real_render = module.ConservativeStarRenderer.render
    real_optimize, real_fusion = GHSOptimizer.optimize, module.recompose

    def stars(*args, **kwargs):
        """Compte le rendu commun des étoiles."""
        render_calls.append(1)
        return real_render(*args, **kwargs)

    def optimize(self, data, strategy, *args):
        """Vérifie que les deux fonds partent des mêmes pixels."""
        optimize_calls.append((strategy, data.copy()))
        return real_optimize(self, data, strategy, *args)

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
    assert [call[0] for call in optimize_calls] == ["range", "peak"]
    np.testing.assert_array_equal(optimize_calls[0][1], optimize_calls[1][1])
    assert len(fusion_calls) == 2 and fusion_calls[0] is fusion_calls[1]
    assert set(result["output_images"]) == {"range", "peak"}
    assert set(result["output_jpegs"]) == {"range", "peak"}
    assert result["output_jpeg"] == result["output_jpegs"]["range"]
    from PIL import Image
    for path in result["output_jpegs"].values():
        with Image.open(path) as image:
            assert image.format == "JPEG" and image.size == (48, 48)
    assert set(result["comparison_paths"]) == {"starless", "with_stars"}
    assert all(Path(path).parent == tmp_path / "05_autoghs_work/003_ghs"
               and Path(path).is_file() for path in result["comparison_paths"].values())
    assert result["comparison_previous"] == {"starless": [], "with_stars": []}
    assert result["histogram_paths"]["starless"] == result["histogram_path"]
    assert len(set(result["histogram_paths"].values())) == 2
    assert all(Path(path).is_file() for path in result["histogram_paths"].values())
    work = tmp_path / "05_autoghs_work"
    assert sorted(path.name for path in work.iterdir()) == [
        "001", "002_stars", "003_ghs", "004_recomposition", "005_diagnostics"
    ]
    assert not any(path.name.startswith(".") for path in work.rglob("*"))
    assert all(Path(path).parent == work / "004_recomposition"
               for path in result["output_images"].values())
    stars_on_disk = fits.getdata(result["stars"]["output_image"])
    assert Path(result["stars"]["diagnostic_path"]).parent == work / "002_stars"
    assert Path(result["stars"]["diagnostic_path"]).is_file()
    assert Path(result["stars"]["output_jpeg"]).is_file()
    assert result["parameters"]["star_histogram_strength"] == 0.8
    for strategy, path in result["output_images"].items():
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


@pytest.mark.parametrize("failure", ["separation", "optimize", "fusion", "publication"])
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


def test_both_renderings_survive_local_starnet_overshoot(
    linear_rgb: Path, fake_separation, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La correction rejoint les deux GHS, les exports et les diagnostics réels."""
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
    assert result["status"] in ("SUCCESS", "NO_IMPROVEMENT")
    assert result["residual_compensation"]["tolerance_exceeded"]
    assert set(result["output_images"]) == {"range", "peak"}
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
    assert saved["enable_autoghs"] and saved["ghs_strategy"] == "peak"
    assert saved["ghs_dynamic_target"] == 0.8
    assert saved["ghs_histogram_strength"] == 0.7
    assert saved["star_faint_boost"] == 4
    assert saved["star_histogram_strength"] == 0.6
    parser = argparse.ArgumentParser()
    config.register(_PostProcessorSequence, parser)
    assert parser.parse_args([]).ghs_strategy == "peak"
    assert parser.parse_args([]).ghs_dynamic_target == 0.8
    assert parser.parse_args([]).ghs_histogram_strength == 0.7
    assert parser.parse_args([]).star_faint_boost == 4
    assert parser.parse_args([]).star_histogram_strength == 0.6
    updated = config.parse_args(
        parser, ["--ghs-strategy", "range", "--starnet-path", "/other/starnet++",
                 "--ghs-dynamic-target", "0.9"]
    )
    assert updated.ghs_strategy == "range"
    assert updated.ghs_dynamic_target == 0.9
    assert renderer._get_args().ghs_dynamic_target == 0.8
    assert renderer._get_args().ghs_strategy == "peak"
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


def test_second_branch_failure_never_publishes_first(
    linear_rgb: Path, fake_separation, tmp_path: Path, monkeypatch
) -> None:
    """Une panne au second rendu conserve le premier FITS avec un rapport ERROR."""
    import lib.final_render as module

    real_fusion = module.recompose
    calls = []

    def fusion(*args, **kwargs):
        """Échoue après une première recomposition entièrement calculée."""
        calls.append(1)
        if len(calls) == 2:
            raise OSError("second branch disk failure")
        return real_fusion(*args, **kwargs)

    monkeypatch.setattr(module, "recompose", fusion)
    with pytest.raises(OSError, match="second branch"):
        AutoGHSFinalRenderer().post_process(
            linear_rgb,
            tmp_path / "05_autoghs.json",
            argparse.Namespace(
                compute_backend="cpu", ghs_bins=256, ghs_max_iterations=1
            ),
        )
    assert len(calls) == 2
    assert json.loads((tmp_path / "05_autoghs.json").read_text())["status"] == "ERROR"
    assert len(list((tmp_path / "05_autoghs_work/004_recomposition").glob("*.fits"))) == 1
    assert not list(tmp_path.glob("05_autoghs_render_*"))


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
    """Chaque figure isole sa population et conserve un véritable axe log."""
    from matplotlib.figure import Figure
    from lib.final_render import histogram_figure

    captured = []
    savefig = Figure.savefig

    def capture(figure: Figure, *args, **kwargs) -> None:
        """Inspecte les axes réels au moment d'écrire le PNG."""
        left, right = figure.axes[:2]
        assert left.get_yscale() == 'linear'
        assert right.get_yscale() == 'log'
        assert right.get_xscale() == 'linear'
        assert left.get_xlim() == right.get_xlim() == (0, 1)
        assert left.get_title() == "1. RANGE — étendue globale\nEffectifs en échelle linéaire"
        assert right.get_title() == "2. RANGE — étendue globale\nEffectifs en échelle logarithmique"
        assert figure.axes[2].get_title() == "3. PEAK — pic principal\nEffectifs en échelle linéaire"
        assert figure.axes[3].get_title() == "4. PEAK — pic principal\nEffectifs en échelle logarithmique"
        assert len({axis.get_title() for axis in figure.axes[:4]}) == 4
        assert left.get_legend() is None and right.get_legend() is None
        assert len(figure.legends) == 1
        assert right.get_legend_handles_labels()[1] == [
            "Image finale — étoiles recomposées"
            if with_stars else "Sans étoiles — entrée linéaire"
        ]
        assert len(right.lines) == len(left.lines) == 1
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
    histogram_figure(
        {strategy: {"histograms": [{"counts": counts}],
                    "recomposed_histogram": {"counts": [2 * count for count in counts]}}
         for strategy in ("range", "peak")},
        destination, with_stars=with_stars,
    )
    assert captured == [True] and destination.stat().st_size > 0


@pytest.mark.parametrize("strategy", ["range", "peak"])
def test_strategy_annotation_counts_searches_and_applications(strategy: str) -> None:
    """Distingue recherches, applications, candidats et BP sans compter la chroma."""
    from lib.final_render import strategy_annotation

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
    text = strategy_annotation(strategy, report)
    for expected in ["Recherches effectuées : 3 / 8", "GHS appliqués : 2",
                     "Recherches sans application : 1", "Candidats examinés : 6",
                     "Corrections du point noir : 1", "+100.0 %"]:
        assert expected in text
    assert "aucune amélioration admissible" in text
    assert ("quantiles" if strategy == "range" else "demi-hauteur") in text
    report["iterations"] = []
    report["stop_reason"] = "constant distribution"
    assert "Recherches effectuées : 0 / 8" in strategy_annotation(strategy, report)
    assert "distribution constante" in strategy_annotation(strategy, report)
    assert "non renseignées" in strategy_annotation(strategy, {})
    report["dynamic_range"] = {
        "iterations": [{"BP": 0.01}, {"BP": 0.002}], "max_iterations": 32,
        "initial_width": 0.12, "final_width": 0.8, "target": 0.8,
        "target_reached": True, "stop_reason": "target reached",
    }
    report["histogram_balance"] = {"strength": 0.5, "clip_limit": 4}
    annotated = strategy_annotation(strategy, report)
    assert "ÉGALISATION PARTIELLE" in annotated
    assert "Force : 0.50 — plafond : 4.0" in annotated
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
    stale = tmp_path / "05_autoghs_work/obsolete.fits"
    stale.write_bytes(b"obsolete")
    second = renderer.post_process(linear_rgb, report, args)
    assert first["output_images"] == second["output_images"]
    assert second["comparison_previous"] == {"starless": ["range"], "with_stars": ["range"]}
    assert all(Path(path).is_file() for path in second["comparison_paths"].values())
    assert not stale.exists()
    assert len(fake_separation) == 2
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "05_autoghs.json", "05_autoghs_work", "linear.fits"
    ]
    source = tmp_path / "05_autoghs_work/linear_input.fits"
    source.write_bytes(linear_rgb.read_bytes())
    before = source.read_bytes()
    with pytest.raises(ValueError, match="réinitialisé"):
        renderer.post_process(source, report, args)
    assert source.read_bytes() == before
