"""Tests de non-régression du module de post-traitement FITS."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from lib.postprocess import (
    DeconvolutionProcessor,
    GradientExtractor,
    NoiseReductionProcessor,
    PhotometricColorCalibrator,
    _PostProcessorSequence,
)
from lib.processor import processor
from lib.type_defs import JSONReport


def _args(*options: str) -> argparse.Namespace:
    """Construit un parseur de séquence avec les options réellement exposées."""
    parser = argparse.ArgumentParser()
    _PostProcessorSequence().add_arguments(parser)
    return parser.parse_args(options)


def test_default_postprocess_order() -> None:
    """La séquence standard conserve l'ordre gradient → photo → déconvolution → débruitage."""
    sequence = _PostProcessorSequence()
    assert [type(step) for step in sequence.processors] == [
        GradientExtractor,
        PhotometricColorCalibrator,
        DeconvolutionProcessor,
        NoiseReductionProcessor,
    ]


def test_standard_backend_selection() -> None:
    """Le backend Siril conserve les processeurs standards."""
    sequence = _PostProcessorSequence()
    selected = sequence._selected_processors(_args("--disable-clarity"))
    assert [type(step) for step in selected] == [
        GradientExtractor,
        PhotometricColorCalibrator,
        DeconvolutionProcessor,
        NoiseReductionProcessor,
    ]


def test_duplicate_prefix_is_rejected() -> None:
    """Chaque préfixe doit rester unique pour produire des rapports non ambigus."""

    class _Duplicate(DeconvolutionProcessor):
        def get_prefix(self) -> str:
            return "deconvolution"

    with pytest.raises(ValueError, match="préfixe unique"):
        _PostProcessorSequence([DeconvolutionProcessor(), _Duplicate()])


def test_report_path_must_differ_from_source(tmp_path: Path) -> None:
    """Le rapport JSON ne peut pas écraser le FITS source."""
    sequence = _PostProcessorSequence([])
    source = tmp_path / "source.fit"
    source.write_bytes(b"fits")
    with pytest.raises(ValueError, match="rapport JSON"):
        sequence.post_process(source, source)


def test_deconvolution_no_safe_improvement_continues_sequence(tmp_path: Path) -> None:
    """La séquence poursuit après une déconvolution sans gain validé."""

    class _NoSafeDeconvolution(processor):
        def add_arguments(self, parser: argparse.ArgumentParser) -> None:
            return None

        def get_prefix(self) -> str:
            return "deconvolution"

        def post_process(
            self,
            input_path: Path,
            output_path: Path,
            args: argparse.Namespace | None = None,
        ) -> JSONReport:
            raise RuntimeError(
                "Aucune déconvolution efficace sans dégradation mesurée ; "
                f"original conservé, audit : {output_path}"
            )

    class _RecordingDenoise(processor):
        seen_inputs: list[Path]

        def __init__(self) -> None:
            self.seen_inputs = []

        def add_arguments(self, parser: argparse.ArgumentParser) -> None:
            return None

        def get_prefix(self) -> str:
            return "denoise"

        def post_process(
            self,
            input_path: Path,
            output_path: Path,
            args: argparse.Namespace | None = None,
        ) -> JSONReport:
            self.seen_inputs.append(Path(input_path))
            return {"status": "accepted", "output_image": str(input_path)}

    source = tmp_path / "source.fit"
    source.write_bytes(b"fits")
    denoise = _RecordingDenoise()
    sequence = _PostProcessorSequence([_NoSafeDeconvolution(), denoise])
    report = tmp_path / "report.json"

    results = sequence.post_process(source, report, args=argparse.Namespace())

    assert denoise.seen_inputs == [source.resolve()]
    assert results["deconvolution"]["status"] == "no_safe_improvement"
    assert results["deconvolution"]["continued_with_original_image"] is True
    assert Path(results["deconvolution"]["output_image"]) == source.resolve()
    assert "denoise" in results
