"""BP après courbe : pied de montée, budget cumulatif et poids de population."""

import json
from pathlib import Path

import numpy as np
import pytest

from lib.ghs_blackpoint import HistogramBlackpointNormalizer as Normalizer
from lib.ghs_final_stretch import ProtectedGHSFinalizer


def test_post_blackpoint_moves_the_rise_and_fixes_white_without_excess_clipping() -> None:
    """Un décalage sombre est retiré avant évaluation, en respectant 0,1 % de perte."""
    level = np.linspace(.2, 1, 10000, dtype='f4').reshape(100, 100)
    data = np.repeat(level[None], 3, axis=0)
    original = data.copy()
    result, audit = Normalizer.apply(data, .001, reference=np.ones_like(data, dtype=bool))
    assert .19 < audit['BP'] < .21
    assert np.count_nonzero(np.any(result <= 0, axis=0)) <= 10
    assert audit['loss_fraction'] <= .001
    assert result.max() == 1
    np.testing.assert_array_equal(data, original)
    np.testing.assert_array_equal(result[0], result[1])
    json.dumps(audit, allow_nan=False)


def test_tied_dark_values_cannot_exceed_the_blackpoint_budget() -> None:
    """Un groupe sombre de 20 % n'est pas annulé par un budget de 0,1 %."""
    values = np.r_[np.full(200, .1), np.linspace(.2, .8, 800)].astype('f4')
    data = np.broadcast_to(values, (3, 1, 1000)).copy()
    result, audit = Normalizer.apply(data, .001)
    assert audit['BP'] < .1
    assert audit['loss_fraction'] == 0
    assert result.min() > 0


def test_population_weights_determine_the_rise_quantile() -> None:
    """Un représentant pesant 999 pixels ne vaut pas un seul pixel pour le BP."""
    data = np.broadcast_to(np.array([.1, .2], dtype='f4'), (3, 1, 2)).copy()
    result, audit = Normalizer.apply(data, .001, weights=np.array([[1., 999.]]))
    assert .19 < audit['BP'] < .2
    assert result[0, 0, 0] == 0 and result[0, 0, 1] > 0
    assert audit['loss_fraction'] == .001
    assert audit['estimated']


def test_existing_losses_consume_the_cumulative_budget() -> None:
    """La phase ne récupère pas un nouveau budget après chaque transformation."""
    values = np.linspace(.1, .8, 1000, dtype='f4')
    values[:5] = 0
    data = np.broadcast_to(values, (3, 1, 1000)).copy()
    reference = np.ones_like(data, dtype=bool)
    output, audit = Normalizer.apply(data, .01, reference=reference)
    assert np.count_nonzero(np.any(output <= 0, axis=0)) <= 10
    exhausted, audit = Normalizer.apply(output, .001, reference=reference)
    np.testing.assert_array_equal(exhausted, output)
    assert audit['status'] == 'budget exhausted'


@pytest.mark.parametrize('value,status', [(0., 'empty'), (.2, 'constant')])
def test_empty_and_constant_images_remain_unchanged(value: float, status: str) -> None:
    """Aucune montée informative n'est inventée dans un fond constant."""
    data = np.full((3, 8, 8), value, dtype='f4')
    output, audit = Normalizer.apply(data, .001)
    np.testing.assert_array_equal(output, data)
    assert audit['status'] == status and audit['BP'] == 0


@pytest.mark.parametrize('budget', [-.1, .02, np.nan, np.inf])
def test_invalid_budget_is_rejected(budget: float) -> None:
    """Une fraction non finie ou hors domaine ne déclenche aucun recalage."""
    with pytest.raises(ValueError, match='Budget'):
        Normalizer.apply(np.ones((3, 8, 8), dtype='f4'), budget)


def test_invalid_weights_and_reference_geometry_are_rejected() -> None:
    """Les poids et la référence doivent désigner les mêmes pixels que l'essai."""
    data = np.ones((3, 8, 8), dtype='f4')
    with pytest.raises(ValueError, match='Poids'):
        Normalizer.apply(data, .001, weights=np.zeros((8, 8)))
    with pytest.raises(ValueError, match='géométrie'):
        Normalizer.apply(data, .001, reference=np.ones((3, 2, 2)))


def test_noise_relative_to_useful_contrast_accepts_joint_amplification() -> None:
    """Un étirement amplifiant signal et bruit de neuf fois conserve leur rapport."""
    before = dict(noise=.001, sky_median=.02, diffuse_median=.03)
    after = dict(noise=.009, sky_median=.02, diffuse_median=.11)
    assert ProtectedGHSFinalizer.noise_assessment(before, after)['rejections']
    audit = ProtectedGHSFinalizer.noise_assessment(before, after, relative=True)
    assert not audit['rejections']
    assert audit['effective_gain'] == pytest.approx(1.)
    after['diffuse_median'] = .04
    assert ProtectedGHSFinalizer.noise_assessment(before, after, relative=True)['rejections']


def test_standalone_blackpoint_preserves_source_and_writes_its_audit(tmp_path: Path) -> None:
    """L'interface processor écrit un FITS distinct et décrit le BP appliqué."""
    import argparse
    from astropy.io import fits

    data = np.broadcast_to(np.linspace(.1, 1, 1024, dtype='f4'), (3, 1, 1024)).copy()
    source = tmp_path/'source.fits'
    fits.writeto(source, data, fits.Header({'SPNLIN': True}))
    before = source.read_bytes()
    path = tmp_path/'aligned.json'
    report = Normalizer().post_process(source, path, argparse.Namespace(ghs_blackpoint_loss=.001))
    assert json.loads(path.read_text()) == report
    assert report['BP'] > 0
    assert source.read_bytes() == before
    assert fits.getheader(report['output_image'])['SPNLIN']


def test_piecewise_precalibration_keeps_tied_minima_within_the_global_budget() -> None:
    """Les recalages préalables et l'expansion ne contournent pas le budget par des minima liés."""
    import argparse
    from lib.auto_ghs import GHSOptimizer, ComputeBackend

    data = np.full((3, 32, 32), .02, dtype='f4')
    data[:, 0] = np.linspace(.02, .4, 32, dtype='f4')
    output, _ = GHSOptimizer().optimize(data, 'range', argparse.Namespace(
        ghs_max_iterations=1, ghs_final_strength=0, ghs_histogram_strength=0,
    ), ComputeBackend('cpu'))
    lost = np.any((data > 0) & (output <= 0), axis=0)
    assert np.count_nonzero(lost)/lost.size <= .001


def test_a_fully_clipped_candidate_cannot_hide_behind_constant_status() -> None:
    """Un candidat annulant tous les pixels positifs épuise le budget, même constant."""
    data = np.zeros((3, 8, 8), dtype='f4')
    _, audit = Normalizer.apply(data, .001, reference=np.ones_like(data, dtype=bool))
    assert audit['status'] == 'budget exhausted'
    assert audit['loss_fraction'] == 1


def test_blackpoint_refines_a_bin_containing_existing_losses() -> None:
    """Le premier bin peut contenir neuf pertes et une marge réelle d'un pixel."""
    values = np.r_[np.zeros(9), np.linspace(1e-6, 1, 9991)].astype('f4')
    data = np.broadcast_to(values, (3, 1, values.size)).copy()
    result, audit = Normalizer.apply(data, .001, reference=np.ones_like(data, dtype=bool))
    assert audit['BP'] > 1e-6
    assert audit['precision'] == 'bounded_order_statistic'
    assert audit['loss_fraction_before'] == pytest.approx(.0009)
    assert audit['remaining_loss_budget'] == pytest.approx(.0001)
    assert np.count_nonzero(np.any(result <= 0, axis=0)) == 10
    assert audit['loss_fraction'] <= .001


def test_weighted_blackpoint_refinement_deducts_existing_losses() -> None:
    """Le tri de précision utilise les poids et le budget restant, sans réinitialisation."""
    values = np.array([0., .0001, .0002, .5], dtype='f4')
    data = np.broadcast_to(values, (3, 1, 4)).copy()
    weights = np.array([[9., 1., 100., 9890.]])
    _, audit = Normalizer.apply(data, .001, weights=weights,
                                reference=np.ones_like(data, dtype=bool))
    assert .0001 < audit['BP'] < .0002
    assert audit['loss_fraction'] == .001
    assert audit['remaining_loss_budget'] == pytest.approx(.0001)


def test_blackpoint_precision_never_sorts_an_unbounded_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un grand groupe lié garde la borne conservative sans tri de mosaïque."""
    monkeypatch.setattr('lib.ghs_blackpoint.MAX_BLACKPOINT_SORT_PIXELS', 2)
    values = np.r_[np.full(200, .1), np.linspace(.2, 1., 800)].astype('f4')
    data = np.broadcast_to(values, (3, 1, 1000)).copy()
    _, audit = Normalizer.apply(data, .001)
    assert audit['precision'] == 'histogram_bound'
    assert audit['precision_prefix_pixels'] > 2
    assert audit['BP'] < .1 and audit['loss_fraction'] == 0


@pytest.mark.parametrize('dtype', [np.dtype('f4'), np.dtype('f8')])
def test_repeated_blackpoint_refinements_do_not_erode_positive_channels(dtype: np.dtype) -> None:
    """Un quantile exact ne doit pas pousser les derniers positifs vers le sous-normal."""
    values = np.linspace(.001, 1., 1000, dtype=dtype)
    data = np.broadcast_to(values, (3, 1, 1000)).copy()
    reference = np.ones_like(data, dtype=bool)
    first, _ = Normalizer.apply(data, .001, reference=reference)
    lost = np.any(first <= 0, axis=0)
    current = first
    for _ in range(20):
        current, audit = Normalizer.apply(current, .001, reference=reference)
        np.testing.assert_array_equal(np.any(current <= 0, axis=0), lost)
        assert audit['loss_fraction'] <= .001
        assert current[current > 0].min() >= .99*np.finfo(dtype).eps
    assert Normalizer.safe_threshold(float(np.finfo(dtype).tiny), dtype) == 0


@pytest.mark.parametrize('limit', [float('nan'), float('inf'), -.01, 1.01])
def test_safe_threshold_rejects_invalid_limits(limit: float) -> None:
    """Un BP invalide ne doit pas être silencieusement converti en zéro."""
    with pytest.raises(ValueError, match='Limite'):
        Normalizer.safe_threshold(limit, np.dtype('f4'))
