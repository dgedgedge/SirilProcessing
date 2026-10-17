"""Politique globale, disponibilité propre à chaque moteur et surcharges persistantes."""

import argparse
import json
from pathlib import Path

import pytest

from lib.compute_config import ComputeConfig
from lib.config import Config
from lib.cosmic_clarity.processors import CosmicClarityDenoiseProcessor
from lib.graxpert_inference import GraXpertEngine
from lib.postprocess import _PostProcessorSequence


def test_global_default_single_declaration_and_binding(tmp_path: Path) -> None:
    """Le choix commun est déclaré une fois et atteint les moteurs tardifs."""
    config = Config(tmp_path / "settings.json")
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(_PostProcessorSequence, parser)
    assert sum(a.dest == "compute_backend" for a in parser._actions) == 1
    defaults = config.parse_args(parser, [])
    assert defaults.compute_backend == "auto"
    assert defaults.cosmic_device is None and defaults.graxpert_gpu is None
    args = config.parse_args(parser, ["--compute-backend", "cpu", "-S"])
    config.bind_parameters(args)
    cosmic = CosmicClarityDenoiseProcessor()
    graxpert = GraXpertEngine()
    assert ComputeConfig.requested(cosmic._get_args(), "cosmic_device") == "cpu"
    assert graxpert.backend == "cpu" and graxpert.gpu is False
    assert config.save_requested(args)
    assert json.loads(Path(config.config_file).read_text())["compute_backend"] == "cpu"
    parser = argparse.ArgumentParser()
    config.register(_PostProcessorSequence, parser)
    assert parser.parse_args([]).compute_backend == "cpu"
    assert parser.parse_args(["--compute-backend", "auto"]).compute_backend == "auto"
    assert ComputeConfig.requested(cosmic._get_args(), "cosmic_device") == "cpu"


def test_specific_overrides_remain_explicit() -> None:
    """Les options historiques et leurs valeurs JSON gardent leur rôle de surcharge."""
    args = argparse.Namespace(
        compute_backend="cpu", cosmic_device="cuda", graxpert_gpu=True
    )
    assert ComputeConfig.requested(args, "cosmic_device") == "cuda"
    assert GraXpertEngine.requested_backend(args) == "gpu"
    args.graxpert_gpu = False
    assert GraXpertEngine.requested_backend(args) == "cpu"
    with pytest.raises(ValueError):
        ComputeConfig.requested(argparse.Namespace(compute_backend="invalid"))


def test_cosmic_auto_selects_only_available_cuda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le précontrôle PyTorch choisit CUDA ou CPU ; CUDA forcé reste strict."""
    torch = pytest.importorskip("torch")
    from lib.cosmic_clarity.inference import select_device

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    assert select_device("auto").type == "cuda"
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert select_device("auto").type == "cpu"
    with pytest.raises(RuntimeError, match="CUDA indisponible"):
        select_device("cuda")


def test_graxpert_auto_resolves_before_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un GPU annoncé mais inutilisable choisit CPU au démarrage seulement."""
    pytest.importorskip("onnxruntime")
    engine = GraXpertEngine(argparse.Namespace(compute_backend="auto"))
    engine.gpu = True
    calls = []
    session = object()

    def create(model: Path, gpu: bool) -> object:
        """Simule un pilote absent au chargement du fournisseur GPU."""
        calls.append(gpu)
        if gpu:
            raise RuntimeError("missing driver")
        return session

    monkeypatch.setattr(engine, "_create_session", create)
    assert engine._initialize_session(Path("model.onnx")) is session
    assert calls == [True, False] and engine.gpu is False
    engine.backend, engine.gpu = "cuda", True
    calls.clear()
    with pytest.raises(RuntimeError):
        engine._initialize_session(Path("model.onnx"))
    assert calls == [True]


def test_cli_saves_general_auto_policy(tmp_path: Path) -> None:
    """La CLI expose et mémorise auto sans exiger l'exécution d'un moteur externe."""
    import subprocess
    import sys

    import numpy as np
    from astropy.io import fits

    root = Path(__file__).resolve().parents[1]
    source = tmp_path / "image.fits"
    fits.writeto(source, np.zeros((64, 64), dtype=np.float32))
    config = tmp_path / "settings.json"
    process = subprocess.run(
        [
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
            "--disable-autoghs",
            "--compute-backend",
            "auto",
            "-S",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    saved = json.loads(config.read_text())
    assert saved["compute_backend"] == "auto"
    assert saved["cosmic_device"] is None and saved["graxpert_gpu"] is None
    assert "Politique de calcul commune : auto" in process.stderr
