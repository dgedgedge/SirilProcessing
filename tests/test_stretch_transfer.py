import json
from pathlib import Path
import numpy as np
import pytest
from lib.stretch_transfer import CumulativeStretchTransform, stellar_operation
from lib.star_rendering import ConservativeStarRenderer

def test_stellar_recipe_replays_the_accepted_peak_lookup_table() -> None:
    """Les pics ne sont pas redétectés et les quantiles restent ceux de l'essai."""
    y, x = np.indices((144, 144))
    light = np.zeros_like(x, dtype='f4')
    for (cy, cx), amplitude in zip(
        ((cy, cx) for cy in range(18, 130, 20) for cx in range(18, 130, 20)),
        np.geomspace(1e-4, .7, 36),
    ):
        light += amplitude*np.exp(-((x-cx)**2+(y-cy)**2)/2)
    source = np.array([light, .6*light, .3*light])
    expected, report = ConservativeStarRenderer.render(source, histogram_strength=.8)
    operation = stellar_operation(report, report['histogram_balance'])
    replay = CumulativeStretchTransform((float(source.min()), float(source.max())), [operation])
    np.testing.assert_array_equal(replay.apply_rgb(source), expected)


def test_stellar_noise_gate_recipe_is_monotone_and_reproducible() -> None:
    """Le seuil enregistré est rejoué sans estimation sur la rampe ou les RGB."""
    from lib.stretch_transfer import stellar_noise_gate

    light = np.linspace(0, 1, 20001, dtype=np.float32).reshape(1, -1)
    source = np.array([light, .6 * light, .3 * light])
    parameters = dict(strength=2., ceiling=.85, faint_boost=3.,
                      noise_suppression=dict(threshold=.001))
    operation = stellar_operation(parameters)
    replay = CumulativeStretchTransform((0, 1), [operation])
    target = .85 * np.arcsinh(2 * light) / np.arcsinh(2)
    target = 3 * target / (1 + 2 * target / .85)
    target *= stellar_noise_gate(light, .001)
    gain = np.divide(target, light, out=np.zeros_like(light), where=light > 0)
    actual = replay.apply_rgb(source)
    np.testing.assert_array_equal(actual, source * gain[None])
    assert actual[0, 0, 0] == 0
    assert np.all(np.diff(actual[0, 0]) >= 0)
    assert actual.max() == pytest.approx(.85)
    curve = replay.curve()
    assert curve['precision_achieved']


@pytest.mark.parametrize('threshold', [0, 1e-100, .001, 1])
def test_stellar_noise_gate_is_finite_at_zero_and_one(threshold: float) -> None:
    """Les seuils extrêmes gardent les bornes finies, sans division indéfinie."""
    from lib.stretch_transfer import stellar_noise_gate

    light = np.array([0, 1e-200, 1e-100, .001, 1], dtype=np.float64)
    with np.errstate(divide='raise', invalid='raise', over='raise'):
        gate = stellar_noise_gate(light, threshold)
    assert np.isfinite(gate).all()
    assert gate[-1] == 1
    assert np.all(np.diff(gate) >= 0)
    assert gate[0] == (1 if threshold == 0 else 0)


def test_spatial_recipe_replays_selection_and_labels_envelope(
    tmp_path: Path,
) -> None:
    """Une sélection figée impose sa géométrie et distingue enveloppe et recette RGB."""
    from lib.stellar_selection import select_stellar_peaks

    data = np.full((3, 10, 10), .01, dtype='f4')
    coordinates = np.array([2, 7])
    selection = select_stellar_peaks((10, 10), (coordinates, coordinates),
                                     np.array([.01, .1]), np.ones(2, dtype=bool), .5)
    parameters = dict(strength=2., ceiling=.85, faint_boost=3., boost_selection=selection)
    balance = dict(accepted_strength=.5, input_knots=[.01, 1.], target_knots=[.1, .85])
    transform = CumulativeStretchTransform((.001, 1), [stellar_operation(parameters, balance)])
    rendered = transform.apply_rgb(data)
    assert rendered[0, 7, 7] > rendered[0, 2, 2]
    curve, metadata = transform.export(tmp_path / 'spatial')
    assert curve['scalar_role'] == 'selected_star_neutral_envelope'
    assert metadata['transform_curve_role'] == curve['scalar_role']
    recipe = json.loads(Path(metadata['transform_recipe']).read_text())
    np.testing.assert_array_equal(
        CumulativeStretchTransform(tuple(recipe['domain']), recipe['operations']).apply_rgb(data),
        rendered,
    )
    with pytest.raises(ValueError, match='Géométrie'):
        transform.apply_rgb(np.ones((3, 5, 5), dtype='f4') * .01)
