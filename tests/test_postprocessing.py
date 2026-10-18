"""Contrat du post-traitement complet partagé entre les points d’entrée."""

from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
from copy import deepcopy
import importlib.util
import json
import logging
from pathlib import Path
import sys
from typing import Iterator

import numpy as np
import pytest
from astropy.io import fits

from lib.config import Config
from lib.mosaic import Mosaic
from lib.postprocess import GradientExtractor, _PostProcessorSequence
from lib.postprocessing import PostProcessingProcessor
from lib.processor import processor
from lib.render_exports import write_render_jpeg
from lib.siril_utils import Siril
from lib.star_separation import StarNetSeparator
from lib.type_defs import JSONReport


def configured_args(config: Config, *options: str) -> argparse.Namespace:
    """Résout un parseur commun avec les mêmes déclarations qu’en CLI."""
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(Siril, parser, photometry_aliases=True)
    config.register(PostProcessingProcessor, parser, include_activation=True)
    parser.add_argument("--log-level", default="INFO")
    return config.parse_args(parser, list(options))


@pytest.mark.parametrize("mosaic_first", [False, True])
def test_shared_options_registered_once(tmp_path: Path, mosaic_first: bool) -> None:
    """Les paramètres communs du gradient restent uniques dans les deux ordres."""
    config = Config(tmp_path / "config.json")
    config.set("gradient_smoothing", 0.7)
    parser = argparse.ArgumentParser()
    providers = [Mosaic, PostProcessingProcessor]
    if not mosaic_first:
        providers.reverse()
    for provider in providers:
        config.register(provider, parser)
    args = config.parse_args(parser, ["--gradient-smoothing", "0.4"])
    assert args.gradient_smoothing == 0.4
    assert sum(action.dest == "gradient_smoothing" for action in parser._actions) == 1
    config.bind_parameters(args)
    assert PostProcessingProcessor()._get_args().gradient_smoothing == 0.4
    assert GradientExtractor()._get_args().gradient_smoothing == 0.4


def test_saved_parameters_priorities_and_snapshots(tmp_path: Path) -> None:
    """Activation et réglages suivent CLI > JSON > défaut, sans état partagé."""
    config = Config(tmp_path / "settings.json")
    defaults = configured_args(config)
    assert defaults.enable_postprocess is True
    assert defaults.enable_autoghs is True and defaults.compute_backend == "auto"
    config.set("enable_postprocess", False)
    assert configured_args(config).enable_postprocess is False
    assert configured_args(config, "--postprocess").enable_postprocess is True
    config.set("enable_postprocess", True)
    config.set("ghs_chroma", "enhanced")
    args = configured_args(config, "--no-postprocess", "--compute-backend", "cpu")
    assert args.enable_postprocess is False and args.ghs_chroma == "enhanced"
    config.bind_parameters(args)
    first = PostProcessingProcessor()
    args.ghs_chroma = "off"
    config.bind_parameters(args)
    assert first._get_args().ghs_chroma == "enhanced"
    assert PostProcessingProcessor()._get_args().ghs_chroma == "off"
    args.save_config = True
    assert config.save_requested(args)
    saved = json.loads(Path(config.config_file).read_text())
    assert saved["enable_postprocess"] is False
    assert saved["compute_backend"] == "cpu"
    assert issubclass(PostProcessingProcessor, processor)


@pytest.mark.parametrize("save,enabled", [(False, True), (True, False), (True, True)])
def test_starnet_prepared_only_for_requested_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, save: bool, enabled: bool,
) -> None:
    """Les deux scripts peuvent mémoriser le même exécutable découvert, sans calcul."""
    calls = []

    def resolve(args: argparse.Namespace) -> str:
        """Fournit un chemin déterministe sans accéder à Siril."""
        calls.append(args)
        return str(tmp_path / "starnet++")

    monkeypatch.setattr(StarNetSeparator, "resolve_executable", resolve)
    args = argparse.Namespace(save_config=save, enable_autoghs=enabled)
    PostProcessingProcessor.prepare_config_save(args)
    assert len(calls) == int(save and enabled)
    assert hasattr(args, "starnet_path") is (save and enabled)


@pytest.mark.parametrize(
    "failure", [RuntimeError("engine failed"), KeyboardInterrupt()],
)
def test_failure_releases_guard_and_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    failure: RuntimeError | KeyboardInterrupt,
) -> None:
    """Une erreur ou interruption est propagée après libération des ressources."""
    import lib.postprocessing as processing

    source = tmp_path / "source.fits"
    fits.writeto(source, np.zeros((3, 16, 16), dtype=np.float32))
    report = tmp_path / "source_postprocess.json"
    events = []

    @contextmanager
    def guard() -> Iterator[None]:
        """Trace l’entrée et la sortie même si le moteur échoue."""
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    def fail(
        self: _PostProcessorSequence, *args: object, **kwargs: object,
    ) -> JSONReport:
        """Interrompt le calcul avant publication."""
        raise failure

    monkeypatch.setattr(processing, "PowerGuard", guard)
    monkeypatch.setattr(_PostProcessorSequence, "post_process", fail)
    handlers = list(logging.getLogger().handlers)
    with pytest.raises(type(failure)):
        PostProcessingProcessor().post_process(source, report, argparse.Namespace())
    assert events == ["enter", "exit"]
    assert logging.getLogger().handlers == handlers
    assert not report.exists() and not report.with_suffix(".fits").exists()


@pytest.mark.parametrize("invalid", ["report", "same_source", "same_export", "missing"])
def test_input_and_output_validation(tmp_path: Path, invalid: str) -> None:
    """Les chemins invalides sont refusés sans modifier la source."""
    source = tmp_path / "source.fits"
    source.write_bytes(b"source preserved")
    report = tmp_path / "source_postprocess.json"
    input_path = source
    if invalid == "report":
        report = tmp_path / "report.txt"
    elif invalid == "same_source":
        input_path = report = tmp_path / "source.json"
        input_path.write_bytes(b"source preserved")
    elif invalid == "same_export":
        report = source.with_suffix(".json")
    else:
        input_path = tmp_path / "missing.fits"
    with pytest.raises((ValueError, FileNotFoundError)):
        PostProcessingProcessor().post_process(input_path, report, argparse.Namespace())
    assert source.read_bytes() == b"source preserved"


def test_direct_and_cli_share_calculation_and_exports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Même configuration : appels scientifiques, FITS et JPEG identiques."""
    import lib.postprocessing as processing

    source = tmp_path / "source.fits"
    rendered = tmp_path / "rendered.fits"
    pixels = np.full((3, 20, 24), 0.3, dtype=np.float32)
    fits.writeto(source, pixels)
    original = source.read_bytes()
    fits.writeto(rendered, pixels, fits.Header({"SPNLIN": True}))
    jpeg = rendered.with_suffix(".jpg")
    write_render_jpeg(rendered, jpeg)
    snapshots = []

    def render(
        self: _PostProcessorSequence, input_path: Path, output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Trace les arguments et fournit des artefacts pour comparer les exports."""
        snapshots.append(deepcopy(vars(args)))
        assert input_path == source
        return {"autoghs": {"output_image": str(rendered), "output_jpeg": str(jpeg)}}

    monkeypatch.setattr(_PostProcessorSequence, "post_process", render)
    monkeypatch.setattr(processing, "PowerGuard", nullcontext)
    monkeypatch.setattr(
        processing.QualityMaskPreview, "ensure_for_run", lambda *args: None,
    )
    monkeypatch.setattr(
        processing.QualityMaskPreview, "write_gradient_points", lambda *args: None,
    )
    config_path = tmp_path / "settings.json"
    config_path.write_text(json.dumps({
        "ghs_chroma": "enhanced", "star_faint_boost": 4,
        "compute_backend": "cpu", "gradient_smoothing": 0.6,
    }))
    config = Config(config_path)
    args = configured_args(config, "--ghs-dynamic-target", "0.8")
    config.bind_parameters(args)
    treatment = PostProcessingProcessor()
    direct_report = treatment.report_path_for(source, tmp_path / "direct")
    direct = treatment.post_process(source, direct_report)
    entry = Path(__file__).parents[1] / "bin/postProcess.py"
    spec = importlib.util.spec_from_file_location("common_postprocess_cli", entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "setup_logging", lambda level: None)
    monkeypatch.setattr(sys, "argv", [str(entry), str(source), str(tmp_path / "cli"),
                                      "--config", str(config_path),
                                      "--ghs-dynamic-target", "0.8"])
    assert module.main() == 0
    cli_report = treatment.report_path_for(source, tmp_path / "cli")
    assert (
        Path(direct["output_image"]).read_bytes()
        == cli_report.with_suffix(".fits").read_bytes()
    )
    assert (
        Path(direct["output_jpeg"]).read_bytes()
        == cli_report.with_suffix(".jpg").read_bytes()
    )
    assert json.loads(cli_report.read_text()) == direct["treatments"]
    for key in ("compute_backend", "ghs_chroma", "ghs_dynamic_target",
                "star_faint_boost", "gradient_smoothing", "enable_autoghs"):
        assert snapshots[0][key] == snapshots[1][key]
    assert not hasattr(treatment._get_args(), "postprocess_steps_dir")
    assert (
        direct_report.parent / direct_report.stem
        == Path(snapshots[0]["postprocess_steps_dir"])
    )
    assert source.read_bytes() == original
