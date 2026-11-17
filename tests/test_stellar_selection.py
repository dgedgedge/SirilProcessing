"""Budgets, ex aequo, coordonnées et poids de la sélection stellaire."""

import numpy as np
import pytest

from lib.stellar_selection import select_stellar_peaks, stellar_selection_weights


def test_budget_ties_and_spatial_peak_endpoints() -> None:
    """Les pics non retenus ont un poids exactement nul, sans dépasser le budget."""
    yy = np.array([2, 2, 7, 7])
    xx = np.array([2, 7, 2, 7])
    amplitudes = np.array([.01, .02, .03, .04])
    recipe = select_stellar_peaks((10, 10), (yy, xx), amplitudes, amplitudes > 0, .25)
    assert recipe['budget'] == recipe['selected_count'] == 1
    assert recipe['selected'] == [[7, 7]]
    weights = stellar_selection_weights(recipe, (10, 10))
    np.testing.assert_array_equal(weights[yy, xx], [0, 0, 0, 1])
    assert np.isfinite(weights).all() and weights.min() >= 0 and weights.max() <= 1
    assert weights.dtype == np.float32
    tied = select_stellar_peaks((10, 10), (yy, xx), np.ones(4), amplitudes > 0, .25)
    assert tied['selected_count'] == 0
    assert not stellar_selection_weights(tied, (10, 10)).any()


@pytest.mark.parametrize('fraction,value', [(0, 0), (1, 1)])
def test_endpoints_and_empty_selection(fraction: float, value: int) -> None:
    """Les modes constants restent finis, y compris sans pics détectés."""
    empty = np.array([], dtype=int)
    recipe = select_stellar_peaks((4, 4), (empty, empty), empty, empty > 0, fraction)
    np.testing.assert_array_equal(stellar_selection_weights(recipe, (4, 4)), value)


@pytest.mark.parametrize('fraction', [-.1, 1.1, float('nan'), float('inf')])
def test_invalid_fraction(fraction: float) -> None:
    """Les budgets hors domaine sont refusés avant toute estimation de distance."""
    with pytest.raises(ValueError, match='Fraction stellaire'):
        select_stellar_peaks((4, 4), (np.array([1]), np.array([1])),
                             np.array([.1]), np.array([True]), fraction)


@pytest.mark.parametrize('change', [
    {'selected': [[-1, 0]]}, {'selected': [[10, 0]]},
    {'selected': [[1.5, 0]]}, {'selected': []},
    {'others': [[1, 1]]}, {'others': [[2, 2], [2, 2]]},
    {'shape': [11, 10]}, {'mode': 'unknown'},
])
def test_invalid_saved_geometry(change: dict[str, object]) -> None:
    """Une recette invalide ne produit pas une carte apparemment exploitable."""
    recipe = dict(mode='spatial', shape=[10, 10], selected=[[1, 1]], others=[[2, 2]])
    with pytest.raises(ValueError):
        stellar_selection_weights(recipe | change, (10, 10))
