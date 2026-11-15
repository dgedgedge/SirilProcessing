"""Recettes cumulées RGB, précision des courbes et chemins d'essais indépendants."""

import json
from pathlib import Path

import numpy as np
import pytest

from lib.auto_ghs import ghs_rgb, linear_blackpoint
from lib.stretch_transfer import CumulativeStretchTransform, TrialTransformResolver, stellar_operation
from lib.star_rendering import ConservativeStarRenderer


def curve_parameters(strength: float) -> dict[str, float]:
    """Retourne une courbe GHS valide commune aux exemples de composition."""
    return dict(D=strength, B=5., SP=.01, LP=0., HP=.8)


def test_rgb_recipe_matches_all_successive_operations() -> None:
    """La composition reproduit les RGB colorés, pas seulement une luminosité moyenne."""
    source = np.random.default_rng(8).uniform(.0001, .02, (3, 32, 32)).astype('f4')
    first, last = curve_parameters(1.5), curve_parameters(.5)
    operations = [dict(kind='blackpoint', value=.00005),
                  dict(kind='ghs', parameters=first, color_mode='human', float64=False),
                  dict(kind='gain', value=1.2),
                  dict(kind='ghs', parameters=last, color_mode='human', float64=True),
                  dict(kind='blackpoint', value=.0001)]
    expected = linear_blackpoint(source, .00005)
    expected = ghs_rgb(expected, **first)
    expected = expected*1.2
    expected = ghs_rgb(expected.astype('f8'), **last).astype('f4')
    expected = linear_blackpoint(expected, .0001)
    transform = CumulativeStretchTransform((float(source.min()), float(source.max())), operations)
    np.testing.assert_array_equal(transform.apply_rgb(source), expected)
    untouched = np.random.default_rng(8).uniform(.0001, .02, (3, 32, 32)).astype('f4')
    np.testing.assert_array_equal(source, untouched)


def test_neutral_curve_is_precise_on_a_very_narrow_original_domain(tmp_path: Path) -> None:
    """L'abscisse conserve les vrais min/max et l'interpolation suit la composition."""
    domain = (.00012345, .00012876)
    operations = [dict(kind='ghs', parameters=curve_parameters(5.), float64=True)]*3
    transform = CumulativeStretchTransform(domain, operations)
    curve, metadata = transform.export(tmp_path / 'transform_013')
    assert curve['x'][0] == domain[0] and curve['x'][-1] == domain[1]
    assert curve['precision_achieved']
    probes = np.random.default_rng(8).uniform(*domain, 5000)
    np.testing.assert_allclose(np.interp(probes, curve['x'], curve['y']),
                               transform.evaluate(probes), atol=3e-7, rtol=0)
    with np.load(metadata['transform_samples']) as samples:
        assert samples['input'].dtype == np.float64
        np.testing.assert_array_equal(samples['output'], curve['y'])
    recipe = json.loads(Path(metadata['transform_recipe']).read_text())
    assert recipe['domain'] == list(domain)
    assert recipe['scalar_role'] == 'neutral_rgb_restriction'


def test_recipe_cdf_uses_saved_table_and_gamut_limit() -> None:
    """La CDF n'est pas réestimée sur les pixels rejoués ; son gain reste lié aux RGB."""
    source = np.array([[[.01, .02]], [[.005, .01]], [[.002, .005]]], dtype='f4')
    table = np.linspace(0, 1, 65)**.4
    recipe = CumulativeStretchTransform((0., .02), [
        dict(kind='cdf', table=table.tolist(), strength=.7, color_mode='human'),
    ])
    from lib.auto_ghs import luminance
    light = luminance(source)
    target = .3*light+.7*np.interp(light, np.linspace(0, 1, 65), table).astype('f4')
    gain = np.minimum(target/np.maximum(light, 1e-30), 1/np.maximum(source.max(axis=0), 1e-30))
    np.testing.assert_array_equal(recipe.apply_rgb(source), source*gain[None])


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


def test_resolver_keeps_failed_range_blackpoint_and_separate_branch_parents() -> None:
    """Un BP avant arrêt et un chemin alternatif font partie de la bonne composition."""
    parameters = curve_parameters(.5)
    first = dict(iteration=1, BP=.0001, accepted=dict(parameters, post_BP=.0002))
    second = dict(iteration=2, BP=.0003, accepted=None)
    child = dict(branch_id='b1', parent_branch_id='root', parameters=parameters, BP=0., post_BP=0.)
    other = dict(branch_id='b2', parent_branch_id='root',
                 parameters=curve_parameters(1.), BP=0., post_BP=0.)
    report = dict(iterations=[first, second], histogram_balance=dict(status='disabled'),
                  dynamic_range=dict(iterations=[]), presentation_stretch=dict(status='disabled'),
                  quartile_stretch=dict(selected_search='shape', searches=dict(shape=dict(report=dict(
                      status='applied', iterations=[dict(verified_candidates=[child, other])],
                      selected_path=[child],
                  )))))
    resolver = TrialTransformResolver(report)
    assert resolver.balance_entry[-1] == dict(kind='blackpoint', value=.0003)
    assert resolver.range_stages['RANGE — passe 1'][-1]['value'] == .0002
    first_plan = resolver.for_trial(dict(stage='essai', search_name='shape', parent_branch_id='b1',
                                        parameters=parameters, BP=0., post_BP=0.))
    other_plan = resolver.for_trial(dict(stage='essai', search_name='shape', parent_branch_id='b2',
                                        parameters=parameters, BP=0., post_BP=0.))
    assert first_plan[-2]['parameters']['D'] == .5
    assert other_plan[-2]['parameters']['D'] == 1.
    assert resolver.final()[-1]['parameters']['D'] == .5


@pytest.mark.parametrize('domain', [(0., 0.), (.01, .01)])
def test_constant_source_has_a_defined_curve_without_inventing_dynamic_range(domain: tuple[float, float]) -> None:
    """Une source constante donne un point exact, sans étendue artificielle."""
    result = CumulativeStretchTransform(domain, []).curve()
    assert len(result['x']) == 1
    assert result['x'][0] == result['y'][0] == domain[0]


@pytest.mark.parametrize('domain', [(-.1, .1), (.2, .1), (0., float('nan')), (0., 1.1)])
def test_invalid_original_domain_is_rejected(domain: tuple[float, float]) -> None:
    """Les bornes invalides ne produisent pas une courbe apparemment valide."""
    with pytest.raises(ValueError):
        CumulativeStretchTransform(domain, [])


def test_neutral_ramp_remains_continuous_at_rounding_gamut_boundary() -> None:
    """Un dépassement infinitésimal du gain ne crée pas une chute RGBBlend à zéro."""
    transform = CumulativeStretchTransform((.99, 1.), [
        dict(kind='gain', value=1.0000001),
        dict(kind='ghs', parameters=curve_parameters(1.)),
    ])
    curve = transform.curve()
    assert curve['precision_achieved']
    assert curve['y'][-1] == 1.
    assert np.all(np.diff(curve['y']) >= -1e-14)


def test_histogram_panel_uses_original_domain_and_cumulative_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le PNG affiche trois axes et le min/max réel, même pour une faible dynamique."""
    from matplotlib.figure import Figure
    from lib.ghs_diagnostics import histogram_figure

    domain = (.00012345, .00012876)
    curve = CumulativeStretchTransform(domain, [
        dict(kind='ghs', parameters=curve_parameters(5.)),
    ]).curve()
    captured = []
    original = Figure.savefig

    def inspect(figure: Figure, *args: object, **kwargs: object) -> None:
        """Inspecte les données avant la libération de la figure."""
        assert len(figure.axes) == 3
        axis = figure.axes[1]
        assert axis.get_xlim() == domain
        np.testing.assert_array_equal(axis.lines[0].get_xdata(), curve['x'])
        np.testing.assert_array_equal(axis.lines[0].get_ydata(), curve['gain'])
        assert axis.get_yscale() == 'linear'
        assert not figure.axes[2].axison
        assert 'R=G=B' in axis.get_title()
        captured.append(True)
        original(figure, *args, **kwargs)

    monkeypatch.setattr(Figure, 'savefig', inspect)
    histogram_figure(dict(histograms=[dict(counts=[0, 100, 10, 1])],
                          cumulative_transfer=curve, transfer_domain=domain),
                     tmp_path / 'histogram.png')
    assert captured == [True]


def test_refinement_budget_is_reported_without_claiming_target_precision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une limite de maillage atteinte reste visible dans le bilan de précision."""
    import lib.stretch_transfer as module

    monkeypatch.setattr(module, 'INITIAL_CURVE_POINTS', 17)
    monkeypatch.setattr(module, 'MAX_CURVE_POINTS', 17)
    transform = CumulativeStretchTransform((0., 1.), [
        dict(kind='ghs', parameters=curve_parameters(5.)),
    ])
    curve = transform.curve()
    assert not curve['precision_achieved']
    assert curve['observed_error'] > curve['tolerance']
    assert len(curve['x']) == 17



def test_gain_export_preserves_zero_output_and_undefined_zero_input(tmp_path: Path) -> None:
    """Les gains nuls restent nuls ; 0/0 est indéfini, sans sentinelle négative."""
    transform = CumulativeStretchTransform((0., .01), [dict(kind='blackpoint', value=.003)])
    curve, metadata = transform.export(tmp_path / 'transform_001')
    assert np.isnan(curve['gain'][0])
    assert (curve['gain'][(curve['x'] > 0) & (curve['x'] <= .003)] == 0).all()
    np.testing.assert_allclose(curve['gain'][1:], curve['y'][1:]/curve['x'][1:])
    with np.load(metadata['transform_samples']) as samples:
        np.testing.assert_array_equal(samples['gain'], curve['gain'])
        assert 'blackpoint_lost' not in samples
    recipe = json.loads(Path(metadata['transform_recipe']).read_text())
    assert 'blackpoint_loss_display_level' not in recipe
    assert recipe['plotted_quantity'] == 'output/input'


@pytest.mark.parametrize('domain', [(0., 0.), (.001, .001), (.001, .002)])
def test_gain_panel_handles_fully_black_and_constant_sources(
    tmp_path: Path, domain: tuple[float, float], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Les gains entièrement nuls ou indéfinis se tracent sans faux plancher positif."""
    from matplotlib.figure import Figure
    from lib.ghs_diagnostics import histogram_figure

    curve = CumulativeStretchTransform(domain, [dict(kind='blackpoint', value=.01)]).curve()
    original = Figure.savefig

    def inspect(figure: Figure, *args: object, **kwargs: object) -> None:
        """Vérifie les masques et l'absence de courbe rouge à −1."""
        assert len(figure.axes) == 3
        linear = figure.axes[1]
        assert linear.get_yscale() == 'linear'
        assert len(linear.lines) == 2
        values = linear.lines[0].get_ydata().compressed()
        assert (values == 0).all()
        assert linear.get_ylim()[0] == 0
        original(figure, *args, **kwargs)

    monkeypatch.setattr(Figure, 'savefig', inspect)
    histogram_figure(dict(histograms=[dict(counts=[100, 1, 1, 1])],
                          cumulative_transfer=curve, transfer_domain=domain),
                     tmp_path / 'gain.png')


def test_linear_gain_has_the_expected_constant_multiplier() -> None:
    """Le facteur affiché est bien le rapport total, avec identité égale à un."""
    curve = CumulativeStretchTransform((.001, .01), [dict(kind='gain', value=8.)]).curve()
    np.testing.assert_allclose(curve['gain'], 8., rtol=1e-15)


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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
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
    from matplotlib.figure import Figure
    from lib.ghs_diagnostics import histogram_figure

    original_save = Figure.savefig
    captured = []

    def inspect(figure: Figure, *args: object, **kwargs: object) -> None:
        """L'enveloppe spatiale doit être annoncée dans la figure réellement produite."""
        axis = figure.axes[1]
        assert 'Enveloppe' in axis.get_title()
        assert 'Poids spatial fixé à 1' in axis.texts[0].get_text()
        captured.append(True)
        original_save(figure, *args, **kwargs)

    monkeypatch.setattr(Figure, 'savefig', inspect)
    histogram_figure(dict(histograms=[dict(counts=[0, 10, 2, 0])],
                          cumulative_transfer=transform.curve(), transfer_domain=(.001, 1)),
                     tmp_path / 'envelope.png')
    assert captured == [True]
