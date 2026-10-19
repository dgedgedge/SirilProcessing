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
from lib.restoration_chains import RestorationChainProcessor
from lib.final_render import AutoGHSFinalRenderer


def _args(*options: str) -> argparse.Namespace:
    """Construit un parseur de séquence avec les options réellement exposées."""
    parser = argparse.ArgumentParser()
    _PostProcessorSequence().add_arguments(parser)
    return parser.parse_args(options)


def test_default_postprocess_order() -> None:
    """AutoGHS facultatif suit les traitements linéaires sans changer leur ordre."""
    sequence = _PostProcessorSequence()
    assert [type(step) for step in sequence.processors] == [
        GradientExtractor,
        PhotometricColorCalibrator,
        DeconvolutionProcessor,
        NoiseReductionProcessor,
        AutoGHSFinalRenderer,
    ]


def test_standard_backend_selection() -> None:
    """Le backend Siril compare les chaînes ; le mode par étape reste accessible."""
    sequence = _PostProcessorSequence()
    selected = sequence._selected_processors(_args("--disable-clarity"))
    assert [type(step) for step in selected] == [
        GradientExtractor, PhotometricColorCalibrator, RestorationChainProcessor,
        AutoGHSFinalRenderer,
    ]
    selected = sequence._selected_processors(
        _args("--disable-clarity", "--no-restoration-compare-chains")
    )
    assert [type(step) for step in selected] == [
        GradientExtractor,
        PhotometricColorCalibrator,
        DeconvolutionProcessor,
        NoiseReductionProcessor,
        AutoGHSFinalRenderer,
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


@pytest.mark.parametrize("options, expected", [
    ((), [(1, "gradient"), (2, "photometry"), (3, "restoration"), (5, "autoghs")]),
    (("--disable-photometry",), [(1, "gradient"), (3, "restoration"), (5, "autoghs")]),
    (("--no-restoration-compare-chains",), [
        (1, "gradient"), (2, "photometry"), (3, "deconvolution"),
        (4, "denoise"), (5, "autoghs"),
    ]),
])
def test_standard_step_numbers_survive_restoration_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    options: tuple[str, ...], expected: list[tuple[int, str]],
) -> None:
    """Les rapports et bandeaux suivent les étapes actives, dont AutoGHS en 05."""
    paths: list[str] = []

    def record(
        self: processor, input_path: Path, output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Enregistre les chemins sans invoquer les moteurs externes."""
        paths.append(Path(output_path).name)
        return {"output_image": str(input_path)}

    for provider in (GradientExtractor, PhotometricColorCalibrator,
                     RestorationChainProcessor, DeconvolutionProcessor,
                     NoiseReductionProcessor, AutoGHSFinalRenderer):
        monkeypatch.setattr(provider, "post_process", record)
    source = tmp_path / "source.fits"
    source.write_bytes(b"source")
    with caplog.at_level("INFO"):
        _PostProcessorSequence().post_process(
            source, tmp_path / "report.json", _args(*options),
        )
    assert paths == [f"{number:02d}_{prefix}.json" for number, prefix in expected]
    messages = [record.getMessage() for record in caplog.records]
    titles = [message for message in messages if message.startswith("Post-traitement — étape")]
    labels = {
        "gradient": "Correction du gradient",
        "photometry": "Étalonnage photométrique des couleurs",
        "restoration": "Comparaison des chaînes de restauration",
        "deconvolution": "Déconvolution", "denoise": "Débruitage",
        "autoghs": "Rendu final AutoGHS et étoiles",
    }
    assert titles == [
        f"Post-traitement — étape {number:02d} : {labels[prefix]}"
        for number, prefix in expected
    ]
    for title in titles:
        position = messages.index(title)
        assert messages[position - 1] == messages[position + 1] == "=" * 60
