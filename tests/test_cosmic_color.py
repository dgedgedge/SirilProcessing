"""Conversions et chrominance des modes couleur Cosmic Clarity AI3."""

import numpy as np
import pytest
from numpy.typing import NDArray

from lib.cosmic_clarity.color import (
    extract_luminance, guided_chroma, merge_luminance,
)


def test_bt601_reference_primaries_and_roundtrip() -> None:
    """Les primaires suivent BT.601 et la recomposition retrouve le RGB initial."""
    rgb = np.eye(3, dtype=np.float32)[:, None, :]
    y, cb, cr = extract_luminance(rgb)
    np.testing.assert_allclose(y, [[.299, .587, .114]], atol=1e-7)
    np.testing.assert_allclose(cb, [[.331264, .168736, 1]], atol=1e-7)
    np.testing.assert_allclose(cr, [[1, .081312, .418688]], atol=1e-7)
    np.testing.assert_allclose(merge_luminance(y, cb, cr), rgb, atol=7e-7)


def test_luminance_changes_keep_chroma_until_clipping() -> None:
    """Une hausse de Y conserve Cb/Cr hors écrêtage ; les bornes restent valides."""
    rgb = np.array([.3, .4, .5], dtype=np.float32)[:, None, None]
    y, cb, cr = extract_luminance(rgb)
    changed = merge_luminance(y + .1, cb, cr)
    actual_y, actual_cb, actual_cr = extract_luminance(changed)
    np.testing.assert_allclose(actual_y, y + .1, atol=1e-6)
    np.testing.assert_allclose(actual_cb, cb, atol=1e-6)
    np.testing.assert_allclose(actual_cr, cr, atol=1e-6)
    clipped = merge_luminance(y + 1, cb, cr)
    assert clipped.min() >= 0 and clipped.max() <= 1
    assert not np.allclose(extract_luminance(clipped)[1], cb)


def test_guided_chroma_reference_and_zero_strength() -> None:
    """Compare le filtre à des moyennes explicites, y compris les bords réfléchis."""
    rng = np.random.default_rng(18)
    guide = rng.uniform(.2, .8, (13, 17)).astype(np.float32)
    chroma = rng.uniform(.4, .6, guide.shape).astype(np.float32)
    np.testing.assert_array_equal(guided_chroma(guide, chroma, 0), chroma)
    # Force effective 0,2 : rayon 4 px, fenêtre 9×9, epsilon 0,011².
    def box_mean(plane: NDArray[np.float32]) -> NDArray[np.float32]:
        """Référence directe des fenêtres avec la symétrie de BORDER_REFLECT."""
        padded = np.pad(plane, 4, mode='symmetric')
        windows = np.lib.stride_tricks.sliding_window_view(padded, (9, 9))
        return windows.mean(axis=(-2, -1))

    mean_y, mean_c = box_mean(guide), box_mean(chroma)
    a = ((box_mean(guide * chroma) - mean_y * mean_c)
         / (box_mean(guide * guide) - mean_y**2 + .011**2))
    b = mean_c - a * mean_y
    expected = .8 * chroma + .2 * (box_mean(a) * guide + box_mean(b))
    np.testing.assert_allclose(guided_chroma(guide, chroma, .1), expected, atol=2e-6)
    constant = np.full_like(chroma, .55)
    np.testing.assert_allclose(guided_chroma(guide, constant, 1), constant, atol=1e-6)


@pytest.mark.parametrize('invalid', ['shape', 'nan', 'range', 'strength'])
def test_invalid_color_inputs(invalid: str) -> None:
    """Refuse explicitement formes incompatibles, pixels invalides et intensités."""
    rgb = np.full((3, 4, 4), .5, dtype=np.float32)
    if invalid == 'strength':
        with pytest.raises(ValueError):
            guided_chroma(rgb[0], rgb[1], float('nan'))
        return
    if invalid == 'shape':
        rgb = rgb[:2]
    elif invalid == 'nan':
        rgb[0, 0, 0] = np.nan
    else:
        rgb[0, 0, 0] = 2
    with pytest.raises(ValueError):
        extract_luminance(rgb)
    with pytest.raises(ValueError):
        merge_luminance(np.zeros((2, 2), dtype=np.float32), rgb[0], rgb[1])
