"""Énumération exhaustive, mutualisation et décision sur la sortie finale."""

import argparse
from pathlib import Path

from astropy.io import fits
import numpy as np
import pytest

from lib.restoration_chains import RestorationChainProcessor
from lib.restoration import RestorationComparisonProcessor
from lib.processor import processor
from lib.postprocess import _PostProcessorSequence
from lib.type_defs import JSONReport


@pytest.mark.parametrize(
    "reverse,variants,operations", [(False, 12, 11), (True, 18, 17)]
)
def test_all_chains_are_evaluated_and_intermediates_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reverse: bool,
    variants: int, operations: int,
) -> None:
    """Trois débruiteurs/deux moteurs de netteté, aucun top-N ni score local sommé."""
    source = tmp_path / "source.fits"
    data = np.random.default_rng(1).normal(0.1, 0.001, (128, 128)).astype("float32")
    data[0, 0] = 0
    fits.writeto(source, data)
    calls, finals, masks = [], [], []

    def engine(code):
        """Fabrique un processor déterministe dont la sortie encode son parcours."""

        class Fake(processor):
            """Moteur sans GPU, avec score local volontairement trompeur."""

            @classmethod
            def add_arguments(cls, parser):
                """Aucune option propre au faux moteur."""

            def post_process(
                self, input_path: Path, output_path: Path,
                args: argparse.Namespace | None = None,
            ) -> JSONReport:
                """Encode l’entrée effective et l’opération dans un vrai FITS."""
                assert Path(output_path).parent == tmp_path / "report_work"
                calls.append((code, Path(input_path)))
                changed = fits.getdata(input_path).copy()
                changed[0, 0] = changed[0, 0] * 10 + code
                destination = Path(output_path).with_suffix(".fits")
                fits.writeto(destination, changed, fits.getheader(input_path))
                return dict(
                    status="accepted",
                    output_image=str(destination),
                    selection_score=code,
                )

        Fake.__name__ = "Fake" + str(code)
        return Fake

    denoisers = [(str(code), engine(code)) for code in (1, 2, 3)]
    sharpeners = [(str(code), engine(code)) for code in (4, 5)]

    def engines(cls, args):
        """Expose tous les moteurs disponibles selon l’opération."""
        return denoisers if cls.operation == "denoise" else sharpeners

    def final(self, original, candidate, reference, work, args, operations):
        """La meilleure sortie finale n’est pas celle des meilleurs scores locaux."""
        assert original == source
        masks.append(reference.mask_fingerprint)
        value = int(round(fits.getdata(candidate)[0, 0]))
        finals.append(value)
        return dict(
            status="accepted",
            selection_score=10 if value == 14 else 0.1,
            rejection_reasons=[],
        )

    monkeypatch.setattr(RestorationComparisonProcessor, "engines", classmethod(engines))
    monkeypatch.setattr(RestorationChainProcessor, "_final_evaluation", final)
    args = argparse.Namespace(restoration_reverse_order=reverse)
    result = RestorationChainProcessor().post_process(
        source, tmp_path / "report.json", args
    )
    assert len(result["variants"]) == variants
    assert result["operation_count"] == operations == len(calls)
    assert len(finals) == variants - 1
    assert result["selected_chain"] == ["denoise:1", "sharpen:4"]
    assert len(set(masks)) == 1
    assert fits.getdata(source)[0, 0] == 0
    assert fits.getdata(result["output_image"])[0, 0] == 14
    rows = result["summary"]["rows"]
    assert len(rows) == variants
    assert sum(row["decision"] == "selected" for row in rows) == 1
    assert any(
        step["reused"] for variant in result["variants"] for step in variant["steps"]
    )
    assert "CHAÎNES COMPLÈTES" in Path(result["summary_path"]).read_text()
    assert Path(result["quality_preview_path"]).is_file()
    assert Path(result["quality_preview_path"]).name == "common_masks.png"
    work = tmp_path / "report_work"
    assert Path(result["quality_preview_path"]).parent == work
    assert not (work / "001").exists()
    assert any(work.glob("*_denoise_*.fits"))


def test_sequence_uses_one_restoration_after_gradient_and_photometry() -> None:
    """Les deux modes gardent une seule restauration avant AutoGHS facultatif."""
    sequence = _PostProcessorSequence()
    historic = sequence._selected_processors(
        argparse.Namespace(restoration_compare_chains=False)
    )
    assert [step.get_prefix() for step in historic] == [
        "gradient",
        "photometry",
        "deconvolution",
        "denoise",
        "autoghs",
    ]
    competitive = sequence._selected_processors(argparse.Namespace())
    assert [step.get_prefix() for step in competitive] == [
        "gradient",
        "photometry",
        "restoration",
        "autoghs",
    ]


def test_identity_remains_selectable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Préserve l'identité et la source, même après nettoyage d'une ancienne arborescence."""
    source = tmp_path / "source.fits"
    fits.writeto(
        source,
        np.random.default_rng(3).normal(0.1, 0.001, (128, 128)).astype("float32"),
    )
    monkeypatch.setattr(
        RestorationComparisonProcessor, "engines", classmethod(lambda cls, args: [])
    )
    result = RestorationChainProcessor().post_process(source, tmp_path / "report.json")
    assert result["output_image"] == str(source)
    assert result["selected_chain"] == []
    assert result["operation_count"] == 0
    work = tmp_path / "report_work"
    legacy = work / "001"
    legacy.mkdir()
    (legacy / "obsolete.fits").write_bytes(b"obsolete")
    result = RestorationChainProcessor().post_process(source, tmp_path / "report.json")
    assert not legacy.exists()
    assert Path(result["quality_preview_path"]).parent == work
    assert source.is_file()
    protected = work / "source.fits"
    protected.write_bytes(source.read_bytes())
    with pytest.raises(ValueError, match="réinitialisé"):
        RestorationChainProcessor().post_process(protected, tmp_path / "report.json")
    assert protected.is_file()


def test_final_catalogs_use_relative_siril_output_names(tmp_path, monkeypatch):
    """Siril ne retire pas les guillemets des flags : les sorties restent relatives."""
    from lib.restoration_quality import QualityReference

    source = tmp_path / "source.fits"
    candidate = tmp_path / "candidate.fits"
    data = np.random.default_rng(4).normal(0.1, 0.001, (128, 128)).astype("float32")
    fits.writeto(source, data)
    fits.writeto(candidate, data)
    scripts = []

    class Runner:
        """Simule seulement l’écriture des catalogues annoncés par findstar."""

        def run_siril_script(self, script, directory, name):
            """Refuse les flags à chemins absolus ou entourés de guillemets."""
            scripts.append(script)
            for line in script.splitlines():
                if line.startswith("findstar "):
                    output = line.split("-out=")[1]
                    assert '"' not in output and "/" not in output
                    (Path(directory) / output).write_text("catalog")
            return True

    monkeypatch.setattr("lib.siril_utils.Siril", lambda **kwargs: Runner())
    comparison = dict(
        status="validated",
        pairs=[],
        before_fwhm_px={"mean": 4},
        after_fwhm_px={"mean": 4},
        paired_ratio={"median": 1},
        before_roundness={"mean": 1},
        after_roundness={"mean": 1},
    )
    monkeypatch.setattr(
        "lib.deconvolution.compare_star_catalogs", lambda *a, **k: comparison
    )
    monkeypatch.setattr(
        "lib.deconvolution_quality.artifact_metrics",
        lambda *a, **k: dict(
            max_noise_ratio=0.5, max_ring_fraction=0, noise={"proven_gain": 0.4}
        ),
    )
    reference = QualityReference(source)
    treatment = RestorationChainProcessor()
    for name in ("final_1", "final_2"):
        result = treatment._final_evaluation(
            source,
            candidate,
            reference,
            tmp_path / name,
            argparse.Namespace(),
            ("denoise",),
        )
        assert result["status"] == "accepted"
        assert result["criteria"]["noise"]["status"] == "PASS"
        assert result["criteria"]["global_noise_reduction"]["value"] == 0.5
    assert (tmp_path / "common_reference.tsv").is_file()
    assert sum("reference.tsv" in script for script in scripts) == 1


def test_chain_parameters_bind_to_late_children(tmp_path):
    """La nouvelle classe respecte la liaison hiérarchique et les défauts sauvegardés."""
    from lib.config import Config
    from lib.denoising import SirilNoiseReductionProcessor

    config = Config(tmp_path / "settings.json")
    config.set("quality_mask_growth", 7)
    parser = argparse.ArgumentParser()
    config.register(RestorationChainProcessor, parser)
    args = config.parse_args(parser, ["--denoise-modulation", ".4"])
    config.bind_parameters(args)
    assert RestorationChainProcessor()._get_args().quality_mask_growth == 7
    assert SirilNoiseReductionProcessor()._get_args().quality_mask_growth == 7
    assert SirilNoiseReductionProcessor()._get_args().denoise_modulation == 0.4


@pytest.mark.parametrize(
    "failure_status,decision",
    [("failed", "technical_error"), ("no_safe_improvement", "local_rejection")],
)
def test_local_and_global_exclusions_keep_child_audit_and_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_status: str,
    decision: str,
) -> None:
    """Un rejet local réutilisé et un rejet global gardent leurs causes distinctes."""
    import json

    source = tmp_path / "source.fits"
    fits.writeto(
        source,
        np.random.default_rng(8).normal(0.1, 0.001, (128, 128)).astype("float32"),
    )
    calls = []

    def engine(name: str, status: str) -> type[processor]:
        """Fabrique un moteur simulant rejet, incident audité ou réussite locale."""

        class Fake(processor):
            """Moteur indépendant avec audit scientifique avant erreur éventuelle."""

            @classmethod
            def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
                """Ne déclare aucune option supplémentaire."""

            def post_process(
                self,
                input_path: Path,
                output_path: Path,
                args: argparse.Namespace | None = None,
            ) -> JSONReport:
                """Écrit un audit puis produit une image ou une erreur technique."""
                calls.append(name)
                criteria = {
                    "roundness": dict(status="FAIL", value=0.03, threshold=0.01)
                }
                if status in {"failed", "no_safe_improvement"}:
                    output_path.write_text(
                        json.dumps(dict(status=status, criteria=criteria))
                    )
                    raise RuntimeError("checkpoint incompatible")
                if status == "rejected":
                    return dict(
                        status=status,
                        criteria=criteria,
                        rejection_reasons=["roundness_degraded"],
                    )
                destination = output_path.with_suffix(".fits")
                fits.writeto(destination, fits.getdata(input_path) * 0.99)
                return dict(status=status, output_image=str(destination))

        Fake.__name__ = name
        return Fake

    denoisers = [
        ("reject", engine("Reject", "rejected")),
        ("fail", engine("Fail", failure_status)),
    ]
    sharpeners = [("pass", engine("Pass", "accepted"))]
    monkeypatch.setattr(
        RestorationComparisonProcessor,
        "engines",
        classmethod(
            lambda cls, args: denoisers if cls.operation == "denoise" else sharpeners
        ),
    )
    monkeypatch.setattr(
        RestorationChainProcessor,
        "_final_evaluation",
        lambda *a, **k: dict(
            status="rejected",
            rejection_reasons=["stellar_rings"],
            criteria={"rings": dict(status="FAIL", value=0.12, threshold=0.1)},
        ),
    )
    result = RestorationChainProcessor().post_process(source, tmp_path / "report.json")
    assert len(result["variants"]) == 6 and result["operation_count"] == len(calls) == 3
    assert result["selected_chain"] == []
    assert result["operations"][1]["criteria"]["roundness"]["value"] == 0.03
    rows = result["summary"]["rows"]
    assert [row["decision"] for row in rows] == [
        "selected",
        "local_rejection",
        decision,
        "final_rejection",
        "local_rejection",
        decision,
    ]
    text = Path(result["summary_path"]).read_text()
    assert "checkpoint incompatible" in text and "stellar_rings" in text
    assert "rings=FAIL ; value=0.12 ; threshold=0.1" in text
    assert "calcul #1 réutilisé" in text
