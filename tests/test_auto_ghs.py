"""GHS mathématique, histogrammes, convergence et sélection CPU/CUDA."""

import argparse
import json
from pathlib import Path

import numpy as np
import pytest

from lib.auto_ghs import (
    ComputeBackend,
    GHSOptimizer,
    HistogramAnalyzer,
    ghs_rgb,
    ghs_transfer,
    linear_blackpoint,
)


@pytest.mark.parametrize("B", [-5, -1, -0.2, 0, 0.1, 2, 15])
@pytest.mark.parametrize("D", [0, 0.01, 2, 10])
def test_ghs_monotonic_continuous_normalized(B: float, D: float) -> None:
    """Les quatre primitives et leurs raccords restent bornés et croissants."""
    x = np.unique(np.r_[np.linspace(0, 1, 10001), 0.01, 0.1, 0.8])
    y = ghs_transfer(x, D, B, SP=0.1, LP=0.01, HP=0.8)
    assert np.isfinite(y).all()
    assert y[0] == 0 and y[-1] == 1
    assert np.all(np.diff(y) >= -1e-12)
    if D == 0:
        np.testing.assert_array_equal(x, y)
    for boundary in (0.01, 0.1, 0.8):
        points = np.array([boundary - 1e-10, boundary, boundary + 1e-10])
        assert np.ptp(ghs_transfer(points, D, B, SP=0.1, LP=0.01, HP=0.8)) < 1e-5


def test_siril_reference_vectors() -> None:
    """Les oracles ont été exécutés dans Siril ; tolérance issue de l'écart < 1e-6 mesuré."""
    reference = json.loads(
        (Path(__file__).parent / "data/auto_ghs_siril_1_4_4.json").read_text()
    )
    data = np.asarray(reference["input"], dtype=np.float32)
    for case in reference["cases"]:
        actual = ghs_rgb(data, **case["parameters"])
        np.testing.assert_allclose(actual, case["output"], atol=2e-6, rtol=0)
    np.testing.assert_array_equal(
        linear_blackpoint(data, 0.01),
        np.asarray(reference["blackpoint"]["output"], dtype=np.float32),
    )


@pytest.mark.parametrize(
    "parameters",
    [
        {"D": -1},
        {"D": 11},
        {"D": 1, "B": 16},
        {"D": 1, "SP": 0.1, "LP": 0.2},
        {"D": np.nan},
    ],
)
def test_invalid_parameters(parameters: dict) -> None:
    """Une courbe invalide ne produit jamais un candidat apparemment valide."""
    with pytest.raises(ValueError):
        ghs_transfer(np.array([0.0, 1.0]), **parameters)


def test_histogram_peak_and_quantiles() -> None:
    """Un bruit gaussien a un pic mesurable ; les effectifs log restent verticaux."""
    rng = np.random.default_rng(9)
    data = np.array([rng.normal(0.15, 0.01, (128, 128))] * 3).astype(np.float32)
    analyzer = HistogramAnalyzer(1024)
    state = analyzer.analyze(data, ComputeBackend("cpu"))
    assert state.metrics["peak"] == pytest.approx(0.15, abs=0.005)
    assert state.metrics["range_width"] > 0.05
    assert not state.metrics["peak_ambiguous"]
    assert state.counts.sum() == 128 * 128
    assert len(state.rgb_counts) == 3


@pytest.mark.parametrize("strategy", ["range", "peak"])
def test_constant_stops_and_optimizer_is_deterministic(strategy: str) -> None:
    """Le plateau constant n'est pas une erreur ; l'optimisation est reproductible."""
    backend = ComputeBackend("cpu")
    data = np.full((3, 32, 32), 0.02, dtype=np.float32)
    optimizer = GHSOptimizer()
    result, report = optimizer.optimize(data, strategy, argparse.Namespace(), backend)
    np.testing.assert_array_equal(result, data)
    assert report["status"] == "NO_IMPROVEMENT"
    rng = np.random.default_rng(41)
    base = rng.normal(0.02, 0.002, (32, 32))
    data = np.array([base, base * 1.1, base * 0.9], dtype=np.float32)
    args = argparse.Namespace(ghs_max_iterations=2, ghs_bins=512)
    first, report = optimizer.optimize(data, strategy, args, backend)
    second, again = optimizer.optimize(data, strategy, args, backend)
    np.testing.assert_array_equal(first, second)
    assert report == again
    assert len(report["iterations"]) <= 2
    assert first.max() <= 1


def test_blackpoint_is_first_positive_rgb_value() -> None:
    """BP ignore les zéros et place exactement le premier RGB positif à zéro."""
    backend = ComputeBackend("cpu")
    analyzer = HistogramAnalyzer(1024)
    data = np.array([[[0, 0.02, 0.08]], [[0, 0.03, 0.1]],
                     [[0, 0.04, 0.12]]], dtype=np.float32)
    bp, _ = analyzer.blackpoint(data, backend)
    assert bp == float(data[data > 0].min())
    shifted = linear_blackpoint(data, bp)
    assert shifted[0, 0, 1] == 0
    assert np.all(shifted[data > bp] > 0)
    for constant in (0, 0.1, 1):
        bp, _ = analyzer.blackpoint(np.full((3, 8, 8), constant), backend)
        assert bp == 0


@pytest.mark.parametrize("strategy", ["range", "peak"])
@pytest.mark.parametrize("target", [0.8, 0.9])
def test_optimizer_completes_dynamic_range(strategy: str, target: float) -> None:
    """Les deux stratégies atteignent la cible paramétrée sur un fond peu étendu."""
    base = np.linspace(0.02, 0.03, 32 * 32).reshape(32, 32)
    data = np.array([base, base * 0.9, base * 0.8], dtype=np.float32)
    output, report = GHSOptimizer().optimize(
        data, strategy, argparse.Namespace(
            ghs_max_iterations=1, ghs_bins=512, ghs_dynamic_target=target,
        ), ComputeBackend("cpu"),
    )
    dynamic = report["dynamic_range"]
    assert dynamic["target_reached"]
    assert np.ptp(output) >= target - 1e-6
    assert 0 <= output.min() <= output.max() <= 1
    assert dynamic["iterations"]
    assert dynamic["stop_reason"] == "target reached"
    assert all(record["gain"] <= 2 for record in dynamic["iterations"])
    assert all(record["BP"] > 0 for record in dynamic["iterations"])
    assert report["iterations"][0]["BP"] == float(data.min())


def test_dynamic_range_bound_and_already_wide_input() -> None:
    """La boucle respecte sa borne, n'invente rien sur constante et préserve le gamut."""
    optimizer = GHSOptimizer()
    analyzer = HistogramAnalyzer(512)
    backend = ComputeBackend("cpu")
    narrow = np.linspace(0, 1e-6, 3 * 32 * 32).reshape(3, 32, 32).astype(np.float32)
    output, report, history = optimizer.expand_dynamic_range(
        narrow, analyzer, backend, 0.9, 1,
    )
    assert not report["target_reached"]
    assert report["stop_reason"] == "max_iterations"
    assert len(history) == len(report["iterations"]) == 1
    for data in (narrow * 1e6, np.zeros_like(narrow), np.full_like(narrow, 0.1)):
        output, report, history = optimizer.expand_dynamic_range(
            data, analyzer, backend, 0.9, 32,
        )
        np.testing.assert_array_equal(output, data)
        assert history == report["iterations"] == []


@pytest.mark.parametrize("target", [0, -0.1, 1.1, float("nan"), float("inf")])
def test_dynamic_target_validation(target: float) -> None:
    """Une cible invalide est refusée avant tout calcul."""
    with pytest.raises(ValueError, match="ghs-dynamic-target"):
        GHSOptimizer.options(argparse.Namespace(ghs_dynamic_target=target))


def test_backend_missing_cuda_and_no_midrun_fallback(monkeypatch) -> None:
    """Auto choisit CPU avant calcul ; CUDA forcé et panne de calcul sont fatals."""

    def unavailable(name):
        """Simule une installation CPU seule."""
        raise ImportError("no cupy")

    monkeypatch.setattr("lib.auto_ghs.importlib.import_module", unavailable)
    assert ComputeBackend("auto").name == "cpu"
    with pytest.raises(RuntimeError, match="CUDA demandé"):
        ComputeBackend("cuda")
    monkeypatch.undo()
    backend = ComputeBackend("cpu")

    def fail(*args, **kwargs):
        """Simule une panne après initialisation du moteur."""
        raise RuntimeError("injected CUDA failure")

    monkeypatch.setattr("lib.auto_ghs.ghs_rgb", fail)
    rng = np.random.default_rng(77)
    data = rng.uniform(0.01, 0.03, (3, 32, 32)).astype(np.float32)
    with pytest.raises(RuntimeError, match="injected CUDA failure"):
        GHSOptimizer().optimize(data, "range", argparse.Namespace(), backend)


def test_cuda_parity_when_available() -> None:
    """Compare les deux moteurs sur les mêmes pixels quand CuPy/CUDA est disponible."""
    try:
        backend = ComputeBackend("cuda")
    except RuntimeError:
        pytest.skip("CuPy/CUDA indisponible")
    data = np.random.default_rng(59).uniform(0, 1, (3, 32, 32)).astype(np.float32)
    actual = backend.numpy(
        ghs_rgb(backend.xp.asarray(data), 2, 15, 0.12, xp=backend.xp)
    )
    np.testing.assert_allclose(actual, ghs_rgb(data, 2, 15, 0.12), atol=2e-6, rtol=0)


@pytest.mark.parametrize("policy", ["off", "balanced", "enhanced"])
def test_chroma_rgb_direction_and_gamut(policy: str) -> None:
    """La chroma optionnelle conserve la direction RGB, le gris et les bornes."""
    from lib.auto_ghs import render_chroma

    data = np.random.default_rng(71).uniform(0, 1, (3, 32, 32)).astype(np.float32)
    output = render_chroma(data, policy)
    light = data.mean(axis=0)
    np.testing.assert_allclose(output.mean(axis=0), light, atol=2e-7)
    assert output.min() >= -1e-7 and output.max() <= 1 + 1e-7
    if policy == "off":
        np.testing.assert_array_equal(output, data)
    else:
        delta = data - light[None]
        rendered_delta = output - light[None]
        cross = np.cross(delta, rendered_delta, axisa=0, axisb=0)
        assert abs(cross).max() < 2e-7
        assert np.ptp(output, axis=0).mean() >= np.ptp(data, axis=0).mean()
        gray = np.full_like(data, 0.2)
        np.testing.assert_allclose(render_chroma(gray, policy), gray, atol=1e-7)


def test_auto_cuda_selection_vram_guard_and_fatal_runtime(monkeypatch) -> None:
    """Un double CUDA valide la sélection et la panne sans prétendre tester le matériel."""
    from types import SimpleNamespace

    import lib.auto_ghs as module

    original_import = module.importlib.import_module

    class CupyDouble:
        """Interface CuPy minimale appuyée sur NumPy pour le seul contrôle de sélection."""

        __version__ = "test-double"
        float32 = np.float32
        cuda = SimpleNamespace(
            runtime=SimpleNamespace(
                getDeviceCount=lambda: 1, memGetInfo=lambda: (10000, 20000)
            )
        )

        def __getattr__(self, name):
            """Délègue les opérations du précontrôle à NumPy."""
            return getattr(np, name)

        @staticmethod
        def asnumpy(array):
            """Retourne les petits résumés dans le double CPU."""
            return np.asarray(array)

    device = CupyDouble()

    def importer(name):
        """Ne remplace que l'import facultatif CuPy."""
        return device if name == "cupy" else original_import(name)

    monkeypatch.setattr(module.importlib, "import_module", importer)
    assert ComputeBackend("auto", 1000).name == "cuda"
    assert ComputeBackend("auto", 9000).name == "cpu"
    with pytest.raises(RuntimeError, match="VRAM"):
        ComputeBackend("cuda", 9000)
    backend = ComputeBackend("cuda", 1000)

    def failure(*args, **kwargs):
        """Panne après sélection : aucun moteur de remplacement n'est construit."""
        raise RuntimeError("CUDA runtime failed")

    monkeypatch.setattr(device, "asarray", failure)
    with pytest.raises(RuntimeError, match="CUDA runtime failed"):
        GHSOptimizer().optimize(
            np.full((3, 8, 8), 0.01, dtype=np.float32),
            "range",
            argparse.Namespace(),
            backend,
        )
    assert backend.name == "cuda"


def test_single_strategy_processor_contract(tmp_path: Path) -> None:
    """Le moteur GHS seul exige une stratégie et conserve le contrat FITS/rapport."""
    from astropy.io import fits

    source = tmp_path / "background.fits"
    data = np.random.default_rng(39).uniform(0.01, 0.03, (3, 16, 16)).astype(np.float32)
    fits.writeto(source, data)
    optimizer = GHSOptimizer()
    with pytest.raises(ValueError, match="both"):
        optimizer.post_process(source, tmp_path / "both.json", argparse.Namespace())
    result = optimizer.post_process(
        source,
        tmp_path / "range.json",
        argparse.Namespace(
            ghs_strategy="range", compute_backend="cpu", ghs_max_iterations=1
        ),
    )
    assert Path(result["output_image"]).is_file()
    assert fits.getheader(result["output_image"])["SPNLIN"]
    assert fits.getheader(result["output_image"])["SPROLE"] == "STARLESS"


@pytest.mark.parametrize("limit", [0, 65, 1.5])
def test_dynamic_iteration_limit_validation(limit: float) -> None:
    """La boucle ne peut pas recevoir une limite non entière ou hors bornes."""
    with pytest.raises(ValueError, match="ghs-dynamic-max-iterations"):
        GHSOptimizer.options(argparse.Namespace(ghs_dynamic_max_iterations=limit))


@pytest.mark.parametrize("strength", [0.5, 1.0])
def test_histogram_balancing_spreads_concentrated_intensities(strength: float) -> None:
    """L'égalisation étale un histogramme concentré et garde une courbe monotone."""
    level = np.linspace(0, 1, 128 * 128, dtype=np.float32).reshape(128, 128) ** 3
    data = np.array([level] * 3)
    output, report = GHSOptimizer().balance_histogram(
        data, HistogramAnalyzer(512), ComputeBackend("cpu"), strength, 4,
    )
    before = np.histogram(data[0], bins=32, range=(0, 1))[0]
    after = np.histogram(output[0], bins=32, range=(0, 1))[0]
    assert np.std(after) < np.std(before)
    assert np.all(np.diff(output[0].ravel()) >= 0)
    assert output.min() == 0 and output.max() == 1
    assert report["after"]["range_width"] > report["before"]["range_width"]


def test_histogram_balancing_preserves_colors_and_disable() -> None:
    """Le gain lié conserve les ratios et le gamut ; zéro reproduit l'entrée."""
    level = np.linspace(0.01, 0.8, 1024, dtype=np.float32).reshape(32, 32) ** 2
    data = np.array([level, 0.7 * level, 0.3 * level])
    optimizer, analyzer, backend = GHSOptimizer(), HistogramAnalyzer(512), ComputeBackend("cpu")
    output, report = optimizer.balance_histogram(data, analyzer, backend, 0.5, 4)
    np.testing.assert_allclose(output[1], 0.7 * output[0], atol=1e-7)
    np.testing.assert_allclose(output[2], 0.3 * output[0], atol=1e-7)
    assert output.min() >= 0 and output.max() <= 1
    disabled, report = optimizer.balance_histogram(data, analyzer, backend, 0, 4)
    np.testing.assert_array_equal(disabled, data)
    assert report["status"] == "disabled"
    constant = np.full_like(data, 0.1)
    output, report = optimizer.balance_histogram(constant, analyzer, backend, 1, 4)
    np.testing.assert_array_equal(output, constant)


@pytest.mark.parametrize("option,value", [
    ("ghs_histogram_strength", -0.1), ("ghs_histogram_strength", 1.1),
    ("ghs_histogram_clip", 0.5), ("ghs_histogram_clip", float("nan")),
])
def test_histogram_parameters_are_validated(option: str, value: float) -> None:
    """Les paramètres d'égalisation sont validés avant l'optimisation."""
    with pytest.raises(ValueError, match="égalisation"):
        GHSOptimizer.options(argparse.Namespace(**{option: value}))
