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
from unittest.mock import Mock

import numpy as np
import pytest
from astropy.io import fits

from lib.config import Config
from lib.mosaic import Mosaic
from lib.postprocess import GradientExtractor, _PostProcessorSequence
from lib.postprocessing import PostProcessingProcessor
from lib.power_guard import PowerGuard
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
def test_failure_releases_log_without_activating_power_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    failure: RuntimeError | KeyboardInterrupt,
) -> None:
    """La bibliothèque ferme son journal sans demander de protection système."""
    source = tmp_path / "source.fits"
    fits.writeto(source, np.zeros((3, 16, 16), dtype=np.float32))
    report = tmp_path / "source_postprocess.json"

    def fail(
        self: _PostProcessorSequence, *args: object, **kwargs: object,
    ) -> JSONReport:
        """Interrompt le calcul avant publication."""
        raise failure

    guard = Mock(side_effect=AssertionError("Library must not activate PowerGuard"))
    monkeypatch.setattr(PowerGuard, "__enter__", guard)
    monkeypatch.setattr(_PostProcessorSequence, "post_process", fail)
    handlers = list(logging.getLogger().handlers)
    with pytest.raises(type(failure)):
        PostProcessingProcessor().post_process(source, report, argparse.Namespace())
    guard.assert_not_called()
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Même configuration : calculs, exports et bandeau initial communs."""
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
    guard = Mock(side_effect=AssertionError("Library must not activate PowerGuard"))
    monkeypatch.setattr(PowerGuard, "__enter__", guard)
    caplog.set_level(logging.INFO)
    direct = treatment.post_process(source, direct_report)
    guard.assert_not_called()
    entry = Path(__file__).parents[1] / "bin/postProcess.py"
    spec = importlib.util.spec_from_file_location("common_postprocess_cli", entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "PowerGuard", nullcontext)
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
    title = f"Début du post-traitement : {source.name}"
    assert [r.getMessage() for r in caplog.records].count(title) == 2
    for report_path in (direct_report, cli_report):
        log = report_path.parent / report_path.stem / "00_source_postProcess.log"
        lines = log.read_text(encoding="utf-8").splitlines()
        assert lines[0].endswith("=" * 60)
        assert lines[1].endswith(title)
        assert lines[2].endswith("=" * 60)
    for key in ("compute_backend", "ghs_chroma", "ghs_dynamic_target",
                "star_faint_boost", "gradient_smoothing", "enable_autoghs"):
        assert snapshots[0][key] == snapshots[1][key]
    assert not hasattr(treatment._get_args(), "postprocess_steps_dir")
    assert (
        direct_report.parent / direct_report.stem
        == Path(snapshots[0]["postprocess_steps_dir"])
    )
    assert source.read_bytes() == original


@pytest.mark.parametrize("failure,expected", [
    (None, 0), (RuntimeError("engine failed"), 1), (KeyboardInterrupt(), 130),
])
def test_postprocess_cli_owns_and_releases_power_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    failure: RuntimeError | KeyboardInterrupt | None, expected: int,
) -> None:
    """Le script détient un seul contexte et le libère sur tous les chemins."""
    entry = Path(__file__).parents[1] / "bin/postProcess.py"
    spec = importlib.util.spec_from_file_location("guard_postprocess_cli", entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "source.fits"
    source.write_bytes(b"source preserved")
    events: list[str] = []

    @contextmanager
    def guard() -> Iterator[None]:
        """Trace la portée du contexte autour du processeur complet."""
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    def process(
        self: PostProcessingProcessor, input_path: Path, output_path: Path,
    ) -> JSONReport:
        """Vérifie la protection au moment de l’appel, puis simule son résultat."""
        assert events == ["enter"]
        assert input_path == source
        events.append("process")
        if failure is not None:
            raise failure
        return {"status": "SUCCESS"}

    monkeypatch.setattr(module, "PowerGuard", guard)
    monkeypatch.setattr(module, "setup_logging", lambda level: None)
    monkeypatch.setattr(PostProcessingProcessor, "post_process", process)
    monkeypatch.setattr(sys, "argv", [str(entry), str(source),
                                      "--config", str(tmp_path / "settings.json")])
    assert module.main() == expected
    assert events == ["enter", "process", "exit"]
