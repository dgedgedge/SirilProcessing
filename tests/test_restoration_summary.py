"""Synthèse des motifs effectifs, du classement et des contrôles inconnus."""

import argparse
from copy import deepcopy
import json
import logging
from pathlib import Path

import pytest

from lib.restoration_summary import RestorationSummary


def test_explanation_translates_unknown_diffuse_and_selected_chain(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """La conclusion traduit NO_LOCAL_SKY sans promettre la conservation du signal."""
    caplog.set_level(logging.INFO)
    diffuse = dict(
        status="UNKNOWN",
        blocking=False,
        per_region=[
            dict(status="UNKNOWN", reason="NO_LOCAL_SKY"),
            dict(status="UNKNOWN", reason="LOW_DIFFUSE_SNR"),
            dict(status="PASS"),
        ],
    )
    result = dict(
        status="accepted",
        selected_chain=["denoise:siril"],
        selection_score=0.4,
        variants=[
            dict(chain=[], status="reference"),
            dict(
                chain=["denoise:siril"],
                status="accepted",
                selection_score=0.4,
                criteria={"diffuse": diffuse},
            ),
            dict(chain=["sharpen:siril"], status="excluded", rejection_stage="local"),
        ],
        output_image="/candidate.fits",
        operations=[],
        operation_count=2,
    )
    RestorationSummary.write(result, tmp_path / "report.json", mode="chains")
    explanation = "\n".join(result["summary"]["explanation"])
    assert "débruitage avec siril" in explanation
    assert "1 rejet(s) local(aux)" in explanation
    assert (
        "3 mesure(s) de régions ; 1 conforme(s), 0 hors tolérance, 2 non concluante(s)"
        in explanation
    )
    assert "NO_LOCAL_SKY signifie" in explanation
    assert "Sa variation de flux ne peut donc pas être évaluée" in explanation
    assert "ne prouve ni une dégradation ni la conservation" in explanation
    assert "diagnostique" in explanation
    assert "pas un pourcentage de qualité" in explanation
    assert Path(result["summary_path"]).read_text().endswith(explanation + "\n")
    assert explanation.splitlines()[-1] in caplog.text


def test_explanation_of_identity_and_isolated_mode(tmp_path: Path) -> None:
    """Conserver l’entrée de l’étape ne signifie pas annuler les traitements amont."""
    result = dict(
        status="rejected",
        selected_engine=None,
        output_image="/source.fits",
        evaluations={"siril": dict(status="rejected")},
    )
    RestorationSummary.write(
        result, tmp_path / "report.json", mode="isolated", operation="denoise"
    )
    explanation = "\n".join(result["summary"]["explanation"])
    assert "L’image reçue à l’entrée de la restauration est conservée" in explanation
    assert "ne remet pas en cause le gradient ou la photométrie" in explanation
    assert "uniquement l’étape courante" in explanation


@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
def test_diffuse_regions_are_logged_only_in_debug(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, level: int
) -> None:
    """Le journal INFO reste synthétique ; DEBUG, texte et JSON gardent les régions."""
    caplog.set_level(level)
    region = dict(status="UNKNOWN", value=None, sample_count=345, reason="NO_LOCAL_SKY")
    result = dict(
        status="accepted",
        output_image="/candidate.fits",
        criteria={"diffuse": dict(status="UNKNOWN", per_region=[region])},
    )
    RestorationSummary.write(
        result, tmp_path / "report.json", mode="isolated", operation="denoise"
    )
    regional = [r for r in caplog.records if "diffuse.région[" in r.getMessage()]
    assert len(regional) == (1 if level == logging.DEBUG else 0)
    assert all(r.levelno == logging.DEBUG for r in regional)
    assert any(
        r.levelno == logging.INFO and r.getMessage().strip() == "diffuse=UNKNOWN"
        for r in caplog.records
    )
    assert "diffuse.région[0]=UNKNOWN" in Path(result["summary_path"]).read_text()
    assert result["summary"]["rows"][0]["criteria"]["diffuse"]["per_region"] == [region]


def test_chain_summary_distinguishes_all_decisions(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Un score inférieur, une indisponibilité et un UNKNOWN ne deviennent pas FAIL."""
    caplog.set_level("INFO")
    unknown = dict(
        status="UNKNOWN", reason="NO_RELIABLE_SKY", value=None, threshold=1.15
    )
    variants = [
        dict(chain=[], status="reference", selection_score=0),
        dict(
            chain=["good"],
            status="accepted",
            selection_score=1,
            criteria={"noise": unknown},
        ),
        dict(chain=["lower"], status="accepted", selection_score=0.5),
        dict(chain=["tie"], status="accepted", selection_score=1),
        dict(chain=["zero"], status="accepted", selection_score=0),
        dict(
            chain=["local"],
            status="excluded",
            reason=["roundness_degraded"],
            rejection_stage="local",
            steps=[
                dict(
                    operation="denoise",
                    engine="siril",
                    status="rejected",
                    operation_id=1,
                    reused=True,
                )
            ],
        ),
        dict(
            chain=["global"],
            status="rejected",
            rejection_reasons=["stellar_rings"],
            rejection_stage="final",
        ),
        dict(
            chain=["absent"],
            status="excluded",
            reason={"reason": "GPU_UNAVAILABLE"},
            rejection_stage="local",
            steps=[dict(status="unavailable")],
        ),
        dict(
            chain=["failed"],
            status="excluded",
            reason="checkpoint incompatible",
            rejection_stage="local",
            steps=[dict(status="failed")],
        ),
        dict(
            chain=["unknown"],
            status="unknown",
            error="catalogue impossible",
            rejection_stage="final",
        ),
    ]
    original = deepcopy(variants)
    operation = dict(
        status="rejected",
        report_path="/audit/siril.json",
        criteria={"roundness": dict(status="FAIL", value=0.03, threshold=0.01)},
        attempts=[
            dict(
                number=1,
                step=0.0003,
                label="initial",
                quality=dict(
                    accepted=False,
                    reasons=["insufficient_sharpening"],
                    criteria={
                        "fwhm_mean": dict(status="FAIL", value=0.001, threshold=0.02)
                    },
                ),
            )
        ],
    )
    result = dict(
        variants=variants,
        operations=[operation],
        operation_count=1,
        selected_chain=["good"],
        selection_score=1,
        image_path="/source.fits",
        output_image="/final.fits",
    )
    RestorationSummary.write(
        result,
        tmp_path / "report.json",
        mode="chains",
        args=argparse.Namespace(quality_min_tiles=4, quality_enforce_diffuse=False),
    )
    decisions = [row["decision"] for row in result["summary"]["rows"]]
    assert decisions == [
        "reference",
        "selected",
        "lower_score",
        "tie",
        "no_gain",
        "local_rejection",
        "final_rejection",
        "unavailable",
        "technical_error",
        "unknown",
    ]
    assert variants == original
    assert result["summary"]["rows"][1]["criteria"]["noise"]["value"] is None
    text = Path(result["summary_path"]).read_text()
    assert "noise=UNKNOWN ; value=UNKNOWN ; threshold=1.15" in text
    assert "calcul #1 réutilisé" in text
    assert "Essai interne #1 (initial)" in text
    assert "fwhm_mean=FAIL ; value=0.001 ; threshold=0.02" in text
    assert "quality_enforce_diffuse=False" in text
    assert "REJET GLOBAL" in caplog.text
    json.dumps(result, allow_nan=False)


def test_identity_and_isolated_mode_are_explicit(tmp_path: Path) -> None:
    """L’entrée est retenue sans faux classement global ni score inventé."""
    result = dict(
        status="rejected",
        selected_engine=None,
        output_image="/source.fits",
        evaluations={
            "siril": dict(
                status="rejected",
                rejection_reasons=["noise_unknown"],
                criteria={"noise": dict(status="UNKNOWN", value=None)},
            )
        },
    )
    RestorationSummary.write(
        result, tmp_path / "report.json", mode="isolated", operation="denoise"
    )
    text = Path(result["summary_path"]).read_text()
    assert "ÉTAPE ISOLÉE" in text
    assert "Pas de classement global des chaînes" in text
    assert "Choix final : image inchangée" in text
    assert result["summary"]["rows"][0]["criteria"]["noise"]["status"] == "UNKNOWN"
    assert result["summary"]["parameters"] == {}


def test_legacy_siril_selection_has_no_circular_report(tmp_path: Path) -> None:
    """L’appel direct Siril accepté reste correctement identifié dans la synthèse."""
    result = dict(status="accepted", output_image="/siril.fits")
    RestorationSummary.write(
        result,
        tmp_path / "report.json",
        mode="isolated",
        operation="denoise",
        legacy=True,
    )
    assert result["summary"]["selected_engine"] == "siril"
    assert result["summary"]["rows"][0]["decision"] == "selected"
    assert "Choix final : siril" in Path(result["summary_path"]).read_text()
    json.dumps(result, allow_nan=False)


def test_checks_text_describes_verdicts_thresholds_and_missing_measurements(
    tmp_path: Path,
) -> None:
    """Le texte expose les critères disponibles sans présenter UNKNOWN comme PASS."""
    audit = dict(
        status='rejected', selection_score=0.0,
        criteria={
            'stellar_flux': dict(status='FAIL', value=.2, threshold=.1, reason='FLUX_LOSS'),
            'noise': dict(status='PASS', value=.7, threshold=1),
            'diffuse': dict(status='UNKNOWN', reason='NO_LOCAL_SKY'),
        },
        error='mesure incomplète', rejection_reasons=['FLUX_LOSS'],
    )
    path = tmp_path / 'verification_finale/verifications.txt'
    RestorationSummary.write_checks(
        audit, path, source=tmp_path / 'source.fits',
        candidate=tmp_path / 'candidate.fits', label='Contrôle final',
    )
    text = path.read_text()
    assert 'Conservation du flux photométrique' in text
    assert 'stellar_flux=FAIL ; value=0.2 ; threshold=0.1' in text
    assert 'noise=PASS ; value=0.7 ; threshold=1' in text
    assert 'diffuse=UNKNOWN' in text and 'NO_LOCAL_SKY' in text
    assert 'Erreur : mesure incomplète' in text
    assert str(tmp_path / 'source.fits') in text


def test_accepted_ranking_explains_ties_gaps_and_reference_choice(tmp_path: Path) -> None:
    """Les acceptations sont classées, les rejets exclus, les égalités explicites."""
    result = dict(selected_chain=['first'], selection_score=.8, variants=[
        dict(chain=['first'], status='accepted', selection_score=.8),
        dict(chain=['lower'], status='accepted', selection_score=.3),
        dict(chain=['equal'], status='accepted', selection_score=.8),
        dict(chain=['rejected'], status='rejected', selection_score=10),
        dict(chain=['zero'], status='accepted', selection_score=0),
    ])
    RestorationSummary.write(result, tmp_path / 'restoration.json', mode='chains')
    ranking = result['summary']['accepted_ranking']
    assert [row['label'] for row in ranking['rows']] == ['first', 'equal', 'lower', 'zero']
    assert [row['rank'] for row in ranking['rows']] == [1, 1, 3, 4]
    assert ranking['rows'][0]['decision'] == 'RETENUE'
    assert 'ÉGALITÉ' in ranking['rows'][1]['decision']
    assert ranking['rows'][2]['gap_to_retained_score'] == pytest.approx(.5)
    text = Path(result['summary_path']).read_text()
    assert 'COMPARATIF DES CHAÎNES ACCEPTÉES' in text
    assert 'strictement positif' in text and 'première évaluée' in text
    negative = dict(selected_chain=[], selection_score=0, variants=[
        dict(chain=['no_gain'], status='accepted', selection_score=-.1),
        dict(chain=['unknown'], status='accepted'),
    ])
    ranking = RestorationSummary.accepted_ranking(negative)
    assert ranking['rows'][0]['gap_to_retained_score'] == pytest.approx(.1)
    assert ranking['rows'][0]['decision'] == 'NE BAT PAS L’IMAGE INCHANGÉE'
    assert ranking['missing_score_count'] == 1
