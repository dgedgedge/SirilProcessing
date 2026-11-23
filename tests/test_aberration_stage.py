"""Déclenchement optique, comparaison appariée et passage de l'image retenue."""
import argparse
import json
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from lib.aberration_stage import AberrationCorrectionStage
from lib.aberration import AberrationEngine
from lib.quality_star_processors import (
    SirilStarDetectionProcessor, StarComparisonProcessor, ReferenceStarMeasurementProcessor,
)


def options(*values: str) -> argparse.Namespace:
    """Résout les défauts déclarés par la nouvelle étape, sans configuration globale."""
    parser = argparse.ArgumentParser()
    AberrationCorrectionStage.add_arguments(parser)
    return parser.parse_args(values)


def stars(roundness: float = .7) -> list[dict]:
    """Répartit vingt étoiles entre un bord et le centre d'un champ 100²."""
    return [dict(id=i, layer=0, x=10. if i < 10 else 50., y=5.+9*(i % 10),
                 fwhm_x=4., fwhm_y=4.*roundness, fwhm=4.*np.sqrt(roundness))
            for i in range(20)]


def comparison(delta: float, gain: float) -> dict:
    """Fournit des paires connues et indépendantes de la lecture des catalogues."""
    return dict(status='validated', pairs=[dict(
        x_before=s['x'], y_before=s['y'], roundness_before=.7, roundness_after=.7+delta,
        ratio_after_before=1-gain, fwhm_before_px=4., fwhm_after_px=4.*(1-gain),
    ) for s in stars()])


def test_quality_triggers_are_dimensionless_unless_configured() -> None:
    """Une forme saine évite l'IA ; une largeur absolue n'est pas supposée mauvaise."""
    args = options()
    before = AberrationCorrectionStage.baseline(stars(.95), (100, 100), .2)
    assert AberrationCorrectionStage.trigger(before, args) == []
    args.aberration_max_fwhm_px = 2.
    assert 'all:fwhm_absolute' in AberrationCorrectionStage.trigger(before, args)
    before['edge']['fwhm_px'] = 2*before['center']['fwhm_px']
    assert 'edge:fwhm_relative_to_center' in AberrationCorrectionStage.trigger(before, options())
    assert 'edge:roundness' in AberrationCorrectionStage.trigger(
        AberrationCorrectionStage.baseline(stars(), (100, 100), .2), options())


def test_disabled_model_skips_detection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un modèle désactivé transmet la source sans appeler Siril ni l'IA."""
    source, report = tmp_path / 'source.fits', tmp_path / 'aberration.json'
    fits.writeto(source, np.ones((10, 10), dtype='f4'))
    stale = tmp_path / 'aberration_work/candidate.fits'
    stale.parent.mkdir()
    stale.write_bytes(b'previous result')

    def forbidden(*args: object, **kwargs: object) -> None:
        """Refuse toute détection pendant une désactivation explicite."""
        raise AssertionError('Un modèle désactivé ne doit pas être évalué')

    monkeypatch.setattr(SirilStarDetectionProcessor, 'post_process', forbidden)
    result = AberrationCorrectionStage().post_process(source, report, options('--aberration-model', ''))
    assert result['status'] == 'skipped'
    assert result['output_image'] == str(source)
    assert not stale.exists()


@pytest.mark.parametrize('delta,gain,accepted', [
    (.08, 0., True), (0., .1, True), (.01, .01, False),
    (.1, -.1, False), (-.04, .2, False),
])
def test_paired_gains_and_non_regression(delta: float, gain: float, accepted: bool) -> None:
    """Rondeur et finesse sont alternatives, mais ne compensent pas une régression."""
    before = AberrationCorrectionStage.baseline(stars(), (100, 100), .2)
    verdict = AberrationCorrectionStage.assess(
        comparison(delta, gain), before, (100, 100), ['edge:roundness'], options())
    assert verdict['accepted'] is accepted


def test_center_gain_cannot_hide_bad_edges_or_missing_stars() -> None:
    """Les étoiles perdues et les bords sans gain ne sont pas masqués par le centre."""
    before = AberrationCorrectionStage.baseline(stars(), (100, 100), .2)
    audit = comparison(.1, .1)
    for pair in audit['pairs']:
        if pair['x_before'] == 10:
            pair.update(roundness_after=.7, ratio_after_before=1.)
    assert not AberrationCorrectionStage.assess(
        audit, before, (100, 100), ['edge:roundness'], options())['accepted']
    audit['pairs'] = [p for p in audit['pairs'] if p['x_before'] != 10]
    verdict = AberrationCorrectionStage.assess(
        audit, before, (100, 100), ['edge:roundness'], options())
    assert 'edge:insufficient_pairs' in verdict['reasons']


@pytest.mark.parametrize('roundness,gain,status,force', [
    (.95, .1, 'skipped', False), (.7, .1, 'accepted', False),
    (.7, .01, 'retained_input', False), (.95, .01, 'retained_input', True),
])
def test_stage_does_not_load_model_without_defect_and_preserves_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    roundness: float, gain: float, status: str, force: bool,
) -> None:
    """Exécute la vraie décision et écrit le rapport, en simulant seulement les moteurs."""
    source, report = tmp_path / 'source.fits', tmp_path / 'stage.json'
    fits.writeto(source, np.full((100, 100), .01, dtype='f4'))
    original, calls = source.read_bytes(), []
    monkeypatch.setattr(SirilStarDetectionProcessor, 'post_process', lambda *a, **k: {})
    monkeypatch.setattr('lib.aberration_stage.read_star_catalog', lambda _: (stars(roundness), []))
    monkeypatch.setattr(ReferenceStarMeasurementProcessor, 'post_process',
                        lambda *a, **k: {'before': stars(roundness), 'audit': {}})
    monkeypatch.setattr(StarComparisonProcessor, 'post_process', lambda *a, **k: comparison(.01, gain))
    monkeypatch.setattr(AberrationCorrectionStage, 'measure_flux',
                        lambda *a: {'status': 'FAIL', 'median_relative_change': -.2})

    def infer(self: AberrationEngine, before: Path, after: Path) -> dict:
        """Écrit un vrai FITS distinct pour tester la transmission du chemin retenu."""
        calls.append(before)
        fits.writeto(after, fits.getdata(before)*1.01, overwrite=True)
        return {'test': True}

    monkeypatch.setattr(AberrationEngine, 'process', infer)
    resolved = options('--aberration-force') if force else options()
    result = AberrationCorrectionStage().post_process(source, report, resolved)
    assert result['status'] == status
    assert bool(calls) is (status != 'skipped')
    assert Path(result['output_image']) == (report.parent / 'stage_work/candidate.fits'
                                            if status == 'accepted' else source)
    assert source.read_bytes() == original
    assert json.loads(report.read_text()) == result

    from lib.postprocess import _PostProcessorSequence
    from lib.restoration_chains import RestorationChainProcessor

    received = []

    def restore(self: RestorationChainProcessor, input_path: Path, output_path: Path,
                args: argparse.Namespace | None = None) -> dict:
        """Contrôle le relais effectif vers la restauration de la séquence standard."""
        received.append(input_path)
        return {'image_path': str(input_path), 'output_image': str(input_path)}

    monkeypatch.setattr(RestorationChainProcessor, 'post_process', restore)
    args = resolved
    args.enable_gradient = args.enable_photometry = args.enable_autoghs = False
    pipeline = _PostProcessorSequence().post_process(source, tmp_path / 'pipeline.json', args)
    assert received == [Path(pipeline['aberration']['output_image'])]
    assert (received[0] == source) is (status != 'accepted')
    assert source.read_bytes() == original


@pytest.mark.parametrize('arguments', [
    ('--aberration-edge-fraction', '.5'), ('--aberration-roundness-target', 'nan'),
    ('--aberration-max-fwhm-px', '-1'), ('--aberration-min-fwhm-gain', '0'),
])
def test_invalid_thresholds(arguments: tuple[str, ...]) -> None:
    """Refuse les domaines invalides avant tout appel externe."""
    with pytest.raises(ValueError):
        AberrationCorrectionStage.validate(options(*arguments))


def test_real_gaussian_measurements_retain_rounder_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mesure réellement les PSF avant/après, avec seulement Siril et ONNX simulés."""
    centers = [(x, y) for y in (30, 100, 170, 240) for x in (30, 100, 170, 240, 300)]
    yy, xx = np.indices((340, 340))

    def scene(sx: float, sy: float) -> np.ndarray:
        """Construit vingt étoiles isolées gaussiennes sur un fond bruité connu."""
        image = .02 + np.random.default_rng(7).normal(0, .0001, xx.shape)
        for x, y in centers:
            image += .2*np.exp(-.5*(((xx-x)/sx)**2 + ((yy-y)/sy)**2))
        return image.astype('f4')

    source, report = tmp_path / 'stars.fits', tmp_path / 'aberration.json'
    fits.writeto(source, scene(2.5, 1.5))
    original = source.read_bytes()

    def detect(self: SirilStarDetectionProcessor, *args: object, **kwargs: object) -> dict:
        """Fournit les positions connues au lecteur TSV et aux ajustements réels."""
        for _, catalog in self.images:
            lines = ['# star#\tlayer\tX\tY\tFWHMx [px]\tFWHMy [px]\tSat\tProfile\tA']
            lines += [f'{i}\t0\t{x+.5}\t{y+.5}\t5.887\t3.532\t0\tGaussian\t0.2'
                      for i, (x, y) in enumerate(centers)]
            catalog.write_text('\n'.join(lines))
        return {}

    def infer(self: AberrationEngine, before: Path, after: Path) -> dict:
        """Remplace uniquement le modèle par des étoiles rondes à largeur connue."""
        fits.writeto(after, scene(1.7, 1.7), overwrite=True)
        return {'synthetic': True}

    monkeypatch.setattr(SirilStarDetectionProcessor, 'post_process', detect)
    monkeypatch.setattr(AberrationEngine, 'process', infer)
    args = options()
    args.compute_backend = 'cpu'
    result = AberrationCorrectionStage().post_process(source, report, args)
    assert result['status'] == 'accepted'
    assert result['before']['edge']['roundness'] == pytest.approx(.6, abs=.02)
    assert result['quality']['groups']['edge']['roundness_after'] > .98
    assert result['quality']['groups']['edge']['fwhm_gain'] > .1
    assert source.read_bytes() == original


@pytest.mark.parametrize('factor', [.5, 1.5])
def test_diffuse_flux_signed_summary(tmp_path: Path, factor: float) -> None:
    """Le diagnostic local conserve le signe, la formule et les mesures détaillées."""
    yy, xx = np.indices((384, 384))
    diffuse = .012*np.exp(-((xx-192)**2/1800+(yy-192)**2/700))
    diffuse += .006*np.exp(-((yy-170)**2)/12-(xx-192)**2/1600)
    noise = np.random.default_rng(190).normal(0, .001, xx.shape)
    source, candidate, report = [tmp_path / name for name in
                                 ('source.fits', 'candidate.fits', 'diffuse.json')]
    fits.writeto(source, (.1+diffuse+noise).astype('f4'))
    fits.writeto(candidate, (.1+factor*diffuse+noise).astype('f4'))
    original = source.read_bytes()
    summary = AberrationCorrectionStage.measure_flux(
        source, candidate, report, argparse.Namespace(compute_backend='cpu'),
    )
    assert summary['measured_count'] > 0
    assert summary['median_relative_change'] == pytest.approx(factor-1, abs=.05)
    assert summary['formula'] == '(flux_after - flux_before) / flux_before'
    assert json.loads(report.read_text())['per_region']
    assert source.read_bytes() == original


def test_diffuse_flux_without_detected_structure_is_missing(tmp_path: Path) -> None:
    """Un champ uniforme n'invente pas une variation de flux égale à zéro."""
    source, report = tmp_path / 'source.fits', tmp_path / 'diffuse.json'
    fits.writeto(source, np.full((64, 64), .1, dtype='f4'))
    summary = AberrationCorrectionStage.measure_flux(
        source, source, report, argparse.Namespace(compute_backend='cpu'),
    )
    assert summary['median_relative_change'] is None
    assert summary['measured_count'] == 0
