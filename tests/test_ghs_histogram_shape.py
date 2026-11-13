"""Mesures par morceaux, bruit de queue et protection de forme AutoGHS."""

import argparse
import json

import numpy as np
import pytest

from lib.ghs_histogram_shape import (
    piecewise_histogram_profile, compare_histogram_shapes, protect_histogram_shape,
    histogram_selection_key,
)
from lib.ghs_linear_histogram import add_regression_floor_objective
from lib.auto_ghs import ComputeBackend, GHSOptimizer, HistogramAnalyzer
from lib.ghs_log_render import LogHistogramRenderer, ShapeGHSProcessor
from lib.type_defs import JSONReport
from lib.ghs_quartiles import QuartileGHSProcessor, PEAK_POSITION_STEP


def curved_counts(scale: float = 1.0) -> np.ndarray:
    """Définit trois pentes connues : forte, douce, puis forte décroissance."""
    x = (np.arange(128)+.5)/128
    z = -6*x+5*np.maximum(x-.3, 0)-6*np.maximum(x-.6, 0)
    counts = 10**(5+scale*z)
    counts[4] = 1e9
    return counts


def test_curved_tail_is_regular_by_pieces_without_a_single_global_line() -> None:
    """Les trois régions réelles sont reconnues sans aplatir leur courbure."""
    profile = piecewise_histogram_profile(curved_counts())
    assert profile['measurable']
    assert 2 <= len(profile['pieces']) <= 6
    assert profile['shape']['slope_sequence'] == [1, -1]
    assert profile['line_span'] > profile['legacy_line']['line_span']+.3
    assert profile['reference_covered']
    assert all(piece['rms_dex'] <= .05 for piece in profile['pieces'])
    for first, last in zip(profile['pieces'], profile['pieces'][1:]):
        assert first['end'] == last['start']
    json.dumps(profile, allow_nan=False)


def test_same_slope_sequence_cannot_hide_global_flattening() -> None:
    """Une copie cinq fois plus plate conserve l'ordre mais doit être rejetée."""
    reference = piecewise_histogram_profile(curved_counts())
    flatter = piecewise_histogram_profile(curved_counts(.2))
    audit = compare_histogram_shapes(reference, flatter)
    assert audit['sequence_before'] == audit['sequence_after']
    assert audit['drop_retention'] == pytest.approx(.2, abs=.01)
    assert not audit['achieved']
    assert 'global histogram flattening' in audit['rejections']
    assert compare_histogram_shapes(reference, reference)['achieved']


def test_middle_plateau_is_rejected_despite_same_sequence_and_total_drop() -> None:
    """Une queue conservée ne compense pas la disparition de la pente centrale."""
    reference = piecewise_histogram_profile(curved_counts())
    x = (np.arange(128)+.5)/128
    # La seule pente intermédiaire devient horizontale (0 au lieu
    # de -1), alors que les deux autres restent -6 et -7 dex/intensité.
    z = -6*x+6*np.maximum(x-.3, 0)-7*np.maximum(x-.6, 0)
    counts = 10**(5+z)
    counts[4] = 1e9
    candidate = piecewise_histogram_profile(counts)
    audit = compare_histogram_shapes(reference, candidate)
    assert audit['sequence_before'] == audit['sequence_after']
    assert audit['drop_retention'] > .9
    assert audit['distance'] < .03
    assert not audit['achieved']
    assert 'local histogram flattening' in audit['rejections']
    assert audit['local_slope_retention'] < .1


def test_common_slope_scaling_remains_homogeneous() -> None:
    """Une modification commune de 20 % ne crée ni coude ni plateau local."""
    reference = piecewise_histogram_profile(curved_counts())
    candidate = piecewise_histogram_profile(curved_counts(.8))
    audit = compare_histogram_shapes(reference, candidate)
    assert audit['achieved']
    assert audit['local_slope_retention'] > .7
    assert audit['slope_deformation'] < .1
    assert audit['slope_stiffness'] < .1


@pytest.mark.parametrize('stiffness', [0., -1., float('nan'), float('inf'), 4.1, True, False])
def test_invalid_stiffness_is_rejected(stiffness: float) -> None:
    """La comparaison et la résolution des options refusent un seuil invalide."""
    profile = piecewise_histogram_profile(curved_counts())
    with pytest.raises(ValueError, match='stiffness'):
        compare_histogram_shapes(profile, profile, max_stiffness=stiffness)
    with pytest.raises(ValueError, match='stiffness'):
        GHSOptimizer.options(argparse.Namespace(ghs_shape_stiffness=stiffness))


def test_shape_reference_is_neither_mutated_nor_replaced_by_the_previous_pass() -> None:
    """La protection reste cumulative, même si chaque petite perte serait tolérée."""
    original = piecewise_histogram_profile(curved_counts())
    saved = json.dumps(original, sort_keys=True)
    half = piecewise_histogram_profile(curved_counts(.5))
    step = piecewise_histogram_profile(curved_counts(.75))
    assert compare_histogram_shapes(original, step)['achieved']
    assert compare_histogram_shapes(step, half)['achieved']
    assert not protect_histogram_shape(half, original)['shape_preservation']['achieved']
    assert json.dumps(original, sort_keys=True) == saved


def test_noisy_tail_keeps_the_significant_slope_sequence_after_smoothing() -> None:
    """Le bruit de comptage ne doit pas créer de nouveaux morceaux structurants."""
    expected = piecewise_histogram_profile(curved_counts())
    noisy = piecewise_histogram_profile(np.random.default_rng(31).poisson(curved_counts()))
    audit = compare_histogram_shapes(expected, noisy)
    assert audit['achieved']
    assert audit['distance'] < .05
    assert noisy['shape']['slope_sequence'] == expected['shape']['slope_sequence']


def test_same_tail_cannot_hide_an_excessively_widened_peak_shoulder() -> None:
    """La conservation porte aussi sur la géométrie du pic, pas seulement la queue."""
    reference = piecewise_histogram_profile(curved_counts())
    widened = dict(reference, line_start=.4)
    audit = compare_histogram_shapes(reference, widened)
    assert audit['distance'] == 0
    assert not audit['achieved']
    assert 'histogram peak shoulder distortion' in audit['rejections']


def test_shape_preservation_also_bounds_peak_displacement() -> None:
    """Une forme identique déplacée de 0,1 ne doit pas éclaircir autant le ciel."""
    reference = piecewise_histogram_profile(curved_counts())
    shifted = dict(reference, peak=reference['peak']+.1,
                   line_start=reference['line_start']+.1,
                   line_end=reference['line_end']+.1)
    audit = compare_histogram_shapes(reference, shifted, max_peak_shift=.03)
    assert audit['distance'] == 0
    assert audit['peak_shift'] == pytest.approx(.1)
    assert 'histogram peak displacement' in audit['rejections']


def test_selector_cannot_certify_a_flat_image_without_measurable_quality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sans contraste/bruit mesurable, atteindre 0,8 ne certifie pas un rendu."""
    source = np.full((3, 32, 32), .1, dtype='f4')
    flat = np.full_like(source, .2)
    reference = piecewise_histogram_profile(curved_counts())
    flattened = piecewise_histogram_profile(curved_counts(.2))

    def candidate(self: ShapeGHSProcessor, *args: object) -> tuple[np.ndarray, JSONReport]:
        """Isole la sortie d'un moteur ; le sélecteur doit encore la contrôler."""
        return flat, dict(status='applied', iterations=[])

    def evaluate(image: np.ndarray, *args: object) -> JSONReport:
        """Fournit des niveaux issus de vrais profils analytiques, sans images énormes."""
        p = reference if image is source else flattened
        enriched = add_regression_floor_objective(dict(p, tail={'achieved': True}), 4096)
        met = enriched['regression_floor']['achieved']
        return dict(profile=enriched, longest_line=p, targets_achieved=met,
                    met_count=4 if met else 3, unmet_targets=[] if met else ['regression_floor'])

    monkeypatch.setattr(ShapeGHSProcessor, 'search', candidate)
    monkeypatch.setattr(LogHistogramRenderer, 'assess', staticmethod(evaluate))
    options = GHSOptimizer.options(argparse.Namespace(ghs_histogram_model='piecewise'))
    output, report = LogHistogramRenderer().search(
        source, HistogramAnalyzer(), ComputeBackend('cpu'), options,
    )
    np.testing.assert_array_equal(output, source)
    assert report['selected_search'] == 'unchanged'
    assert not report['targets_achieved']
    assessment = report['searches']['shape']['assessment']['profile']
    assert 'global histogram flattening' in assessment['shape_preservation']['rejections']
    assert 'unmeasurable render quality' in report['searches']['shape']['output_rejections']


def test_no_extrapolation_or_pseudocount_can_validate_an_empty_reference_zone() -> None:
    """Une queue absente reste inconnue malgré la spline et le seuil à 0,8."""
    counts = curved_counts()
    counts[85:] = 0
    profile = piecewise_histogram_profile(counts)
    assert profile['measurable']
    assert profile['line_end'] < .8
    measured = add_regression_floor_objective(profile, 4096)
    assert measured['regression_floor']['predicted_log10_count'] is None
    assert not measured['regression_floor']['achieved']
    assert not measured['regression_floor']['extrapolated']


def test_selector_keeps_the_pre_equalization_reference_across_phases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L’audit cumulé conserve la référence avant égalisation entre les phases."""
    source = np.full((3, 32, 32), .1, dtype='f4')
    candidate_image = np.full_like(source, .2)
    reference = piecewise_histogram_profile(curved_counts())
    local = piecewise_histogram_profile(curved_counts(.75))
    candidate_profile = piecewise_histogram_profile(curved_counts(.5))
    assert compare_histogram_shapes(local, candidate_profile)['achieved']

    def search(
        self: ShapeGHSProcessor, *args: object, reference_shape: JSONReport | None = None,
    ) -> tuple[np.ndarray, JSONReport]:
        """Vérifie la transmission effective de la référence au moteur enfant."""
        assert reference_shape is reference
        return candidate_image, dict(status='applied', iterations=[])

    def evaluate(image: np.ndarray, *args: object) -> JSONReport:
        """Isole la vérification de forme des autres objectifs de luminosité."""
        profile = local if image is source else candidate_profile
        enriched = dict(profile, regression_floor={'achieved': True}, tail={'achieved': True})
        return dict(profile=enriched, longest_line=profile, targets_achieved=True,
                    met_count=4, unmet_targets=[])

    monkeypatch.setattr(ShapeGHSProcessor, 'search', search)
    monkeypatch.setattr(LogHistogramRenderer, 'assess', staticmethod(evaluate))
    options = GHSOptimizer.options(argparse.Namespace(ghs_histogram_model='piecewise'))
    output, report = LogHistogramRenderer().search(
        source, HistogramAnalyzer(), ComputeBackend('cpu'), options,
        reference_shape=reference,
    )
    np.testing.assert_array_equal(output, source)
    assert report['shape_reference'] is reference
    assessment = report['searches']['shape']['assessment']['profile']
    assert 'global histogram flattening' in assessment['shape_preservation']['rejections']
    assert assessment['shape_preservation']['drop_retention'] == pytest.approx(.5, rel=.02)


def test_supported_envelope_can_progress_towards_reference_without_extrapolating() -> None:
    """Une queue plus étendue guide l'amorçage même avant de couvrir 0,8."""
    first, last = curved_counts(), curved_counts()
    first[75:] = 0
    last[95:] = 0
    a = add_regression_floor_objective(piecewise_histogram_profile(first), 4096)
    b = add_regression_floor_objective(piecewise_histogram_profile(last), 4096)
    assert a['measurable'] and b['measurable']
    assert not a['regression_floor']['reference_covered']
    assert not b['regression_floor']['reference_covered']
    assert b['regression_floor']['predicted_log10_count'] is None
    assert b['score'] > a['score']


def test_spline_level_has_the_same_pixel_units_as_the_histogram() -> None:
    """Une densité exponentielle donne le niveau analytique attendu à 0,8."""
    x = (np.arange(128)+.5)/128
    counts = 1e6*10**(-3*x)
    counts[4] = 1e9
    p = piecewise_histogram_profile(counts)
    floor = add_regression_floor_objective(p, 4096)['regression_floor']
    assert floor['method'] == 'smoothing_spline'
    assert floor['predicted_log10_count'] == pytest.approx(6-3*.8-np.log10(4096/128), abs=.005)
    assert floor['achieved'] and floor['reference_covered']


@pytest.mark.parametrize('counts', [np.zeros(128), np.ones(128), np.r_[1000, np.zeros(127)]])
def test_insufficient_support_never_invents_a_shape(counts: np.ndarray) -> None:
    """Un histogramme vide ou trop rare ne fournit aucune spline fictive."""
    profile = piecewise_histogram_profile(counts)
    assert not profile['measurable']
    assert compare_histogram_shapes(profile, profile)['achieved'] is None
    json.dumps(profile, allow_nan=False)


@pytest.mark.parametrize('smoothing', [0., -.1, float('nan'), 1.])
def test_invalid_smoothing_is_rejected(smoothing: float) -> None:
    """Les paramètres de régularisation sont validés avant le calcul SciPy."""
    with pytest.raises(ValueError, match='smoothing'):
        piecewise_histogram_profile(curved_counts(), smoothing=smoothing)


def test_terminal_steepening_can_be_retained_without_a_middle_plateau() -> None:
    """La queue peut ajouter un raccord descendant, sans changer l'ordre précédent."""
    x = (np.arange(128)+.5)/128
    reference = 10**(5-6*x+5*np.maximum(x-.3, 0))
    candidate = reference*10**(-1.5*np.maximum(x-.6, 0))
    reference[4] = candidate[4] = 1e9
    first = piecewise_histogram_profile(reference)
    last = piecewise_histogram_profile(candidate)
    audit = compare_histogram_shapes(first, last)
    assert audit['sequence_before'] == [1]
    assert audit['sequence_after'] == [1, -1]
    assert audit['terminal_steepening'] and audit['achieved']


def test_unstructured_reference_does_not_require_an_artificially_straight_output() -> None:
    """Une référence sans changement significatif admet une nouvelle courbure décroissante."""
    x = (np.arange(128)+.5)/128
    counts = 1e6*10**(-4*x)
    counts[4] = 1e9
    reference = piecewise_histogram_profile(counts)
    candidate = piecewise_histogram_profile(counts*10**(-.5*np.maximum(x-.5, 0)))
    audit = compare_histogram_shapes(reference, candidate)
    assert not audit['sequence_informative']
    assert audit['achieved']
    json.dumps(audit, allow_nan=False)


def decision_profile(
    shift: float | None, *, floor: bool = True, score: float = .1,
    structural: bool = True,
) -> JSONReport:
    """Fournit des mesures de décision : pic bas, queue atteinte et niveau configurable."""
    return dict(model='piecewise', measurable=True, peak=.1, score=score,
                shape_preservation=dict(achieved=structural, peak_shift=shift),
                regression_floor=dict(minimum_count=10., achieved=floor),
                tail=dict(target=.25, achieved=True))


def test_legacy_profiles_keep_the_smallest_peak_displacement_priority() -> None:
    """Sans render_quality dans un ancien rapport, le classement historique reste lisible."""
    near = decision_profile(.0546875, score=.01)
    farther = decision_profile(.140625, score=.3)
    assert histogram_selection_key(near) > histogram_selection_key(farther)
    assert LogHistogramRenderer.ranking({'profile': near}) > LogHistogramRenderer.ranking({'profile': farther})


def test_legacy_profiles_keep_the_target_priority() -> None:
    """Le rendu pâle sous dix pixels/classe ne prime pas pour sa proximité seule."""
    pale = decision_profile(.0234375, floor=False, score=.9)
    useful = decision_profile(.0546875, score=.01)
    assert histogram_selection_key(useful) > histogram_selection_key(pale)


def test_legacy_incomplete_profiles_keep_the_progression_order() -> None:
    """Le déplacement n'empêche pas une étape utile tant que le niveau est incomplet."""
    first = decision_profile(.01, floor=False, score=-1.8)
    better = decision_profile(.04, floor=False, score=-1.4)
    assert histogram_selection_key(better) > histogram_selection_key(first)


def test_legacy_shape_failure_and_unknown_shift_do_not_receive_a_zero_distance() -> None:
    """Une forme rejetée ou un déplacement inconnu ne contourne pas le classement."""
    known = decision_profile(.06)
    rejected = decision_profile(0., structural=False, score=10.)
    unknown = decision_profile(None, score=10.)
    assert histogram_selection_key(known) > histogram_selection_key(rejected)
    assert histogram_selection_key(known) > histogram_selection_key(unknown)


def test_default_peak_margin_accepts_a_candidate_just_above_the_old_cutoff() -> None:
    """Un déplacement de 0,0547 peut être accepté ; le seuil explicite reste respecté."""
    reference = piecewise_histogram_profile(curved_counts())
    delta = .0546875
    shifted = dict(reference, peak=reference['peak']+delta,
                   line_start=reference['line_start']+delta,
                   line_end=reference['line_end']+delta)
    assert compare_histogram_shapes(reference, shifted)['achieved']
    strict = compare_histogram_shapes(reference, shifted, max_peak_shift=.05)
    assert strict['rejections'] == ['histogram peak displacement']


def test_full_search_uses_render_quality_after_all_validations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La qualité complète prime sur l'ancien score et le déplacement isolé du pic."""
    from copy import deepcopy
    import lib.ghs_quartiles as module

    light = np.random.default_rng(2).uniform(.05, .4, (32, 32)).astype('f4')
    image = np.repeat(light[None], 3, axis=0)
    mean = float(light.mean())
    base = piecewise_histogram_profile(curved_counts())

    def profiles(counts: np.ndarray, *args: object) -> JSONReport:
        """Isole les mesures de forme pour contrôler les trois décisions attendues."""
        center = float(np.dot(counts, (np.arange(128)+.5)/128)/counts.sum())
        stage = 0 if center < mean+.01 else 1 if center < mean+.03 else 2
        profile = deepcopy(base)
        shift = [0., .06, .14][stage]
        profile.update(peak=base['peak']+shift, line_start=base['line_start']+shift,
                       line_end=base['line_end']+shift, score=[.01, .05, .5][stage],
                       reference_log_density=-8. if stage == 0 else .5)
        return profile

    def parameters(*args: object) -> list[dict[str, float]]:
        """Deux familles produisent des candidats conformes aux scores inversés."""
        return [dict(D=d, B=5., SP=0., LP=0., HP=1.) for d in (1., 2.)]

    def transform(
        source: np.ndarray, curve: dict[str, float], *args: object, **kwargs: object,
    ) -> np.ndarray:
        """Éclaire les mêmes régions sans modifier bruit, contraste ni couleur."""
        return source+np.float32(.02*curve['D'])

    monkeypatch.setattr(module, 'piecewise_histogram_profile', profiles)
    monkeypatch.setattr(ShapeGHSProcessor, 'candidate_parameters', staticmethod(parameters))
    monkeypatch.setattr(ShapeGHSProcessor, 'transform_candidate', staticmethod(transform))
    monkeypatch.setattr(ShapeGHSProcessor, 'peak_gap_parameters', staticmethod(lambda records: []))
    monkeypatch.setattr(ShapeGHSProcessor, 'population_sampling', False)
    args = GHSOptimizer.options(argparse.Namespace(
        ghs_post_blackpoint='off', ghs_quartile_iterations=1,
        ghs_quartile_validations=2, ghs_tail_height=0.,
    ))
    output, report = ShapeGHSProcessor().search(image, HistogramAnalyzer(), ComputeBackend('cpu'), args)
    iteration = report['iterations'][0]
    assert len(iteration['validations']) == 2
    assert all(not item['rejections'] for item in iteration['validations'])
    from lib.ghs_histogram_shape import histogram_selection_key

    selected = iteration['accepted']
    assert all(histogram_selection_key(selected['full_profile']) >=
               histogram_selection_key(item['full_profile']) for item in iteration['validations'])
    np.testing.assert_array_equal(output, image+np.float32(.02*selected['parameters']['D']))


def test_shape_validation_order_covers_intermediate_and_extreme_peak_positions() -> None:
    """Les variantes d'un bon pic gauche ne masquent ni le milieu ni la droite."""
    records = []
    for peak, strength, b in [(.02, 1., 5.), (.025, 1.1, 5.), (.18, 2., 15.), (.10, .5, 5.)]:
        profile = decision_profile(peak)
        profile['peak'] = peak
        records.append(dict(profile=profile, parameters=dict(D=strength, B=b, SP=0., HP=1.)))
    ordered = QuartileGHSProcessor.shape_candidate_order(records)
    assert [item['profile']['peak'] for item in ordered[:3]] == [.02, .18, .10]
    assert len(ordered) == len(records)
    assert records[1]['profile']['peak'] == .025
    assert QuartileGHSProcessor.shape_candidate_order([]) == []


def test_peak_gap_refinement_bisects_strength_without_changing_curve_family() -> None:
    """Un saut gauche/droite entraîne de nouveaux D intermédiaires après BP."""
    records = [dict(BP=.001, parameters=dict(D=d, B=5., SP=.02, LP=0., HP=.5),
                    profile={'peak': peak}) for d, peak in [(0., .02), (1., .18)]]
    original = json.dumps(records)
    first = QuartileGHSProcessor.peak_gap_parameters(records)
    assert first == [dict(BP=.001, parameters=dict(D=.5, B=5., SP=.02, LP=0., HP=.5))]
    records.append(dict(first[0], profile={'peak': .1}))
    second = QuartileGHSProcessor.peak_gap_parameters(records)
    assert {item['parameters']['D'] for item in second} == {.25, .75}
    assert json.dumps(records[:2]) == original
    records[1]['profile']['peak'] = records[0]['profile']['peak'] + PEAK_POSITION_STEP
    assert QuartileGHSProcessor.peak_gap_parameters(records[:2]) == []
    assert QuartileGHSProcessor.peak_gap_parameters([]) == []


def test_peak_gap_refinement_also_fills_symmetry_point_intervals() -> None:
    """Deux SP éloignés ne laissent pas le choix aux seules positions extrêmes."""
    records = [dict(BP=0., parameters=dict(D=1., B=5., SP=sp, LP=0., HP=.5),
                    profile={'peak': peak}) for sp, peak in [(0., .18), (.16, .02)]]
    assert QuartileGHSProcessor.peak_gap_parameters(records) == [
        dict(BP=0., parameters=dict(D=1., B=5., SP=.08, LP=0., HP=.5)),
    ]


def test_branch_search_can_continue_a_lesser_ranked_parent_towards_complete_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le chemin initialement second gagne ensuite, sans relâcher le budget cumulé."""
    from copy import deepcopy
    import lib.ghs_quartiles as module

    light = np.random.default_rng(2).uniform(.05, .4, (32, 32)).astype('f4')
    source = np.repeat(light[None], 3, axis=0)
    original = source.copy()
    mean = float(light.mean())
    base = piecewise_histogram_profile(curved_counts())

    def stage_of(image: np.ndarray) -> int:
        """Identifie quatre états séparés de 0,02 en luminosité moyenne."""
        return int(round((float(image.mean())-mean)/.02))

    def profiles(counts: np.ndarray, *args: object) -> JSONReport:
        """Définit un premier meilleur état sans issue et un chemin utile en deux coups."""
        center = float(np.dot(counts, (np.arange(128)+.5)/128)/counts.sum())
        stage = int(round((center-mean)/.02))
        profile = deepcopy(base)
        shift = [0., .04, .08, .12][stage]
        profile.update(peak=base['peak']+shift, line_start=base['line_start']+shift,
                       line_end=base['line_end']+shift, test_stage=stage)
        return profile

    def floor(profile: JSONReport, *args: object) -> JSONReport:
        """Le niveau cible n'est atteint qu'après le parent initialement second."""
        result = dict(profile)
        stage = result['test_stage']
        result.update(score=[-1., -.8, -.6, .1][stage],
                      regression_floor=dict(minimum_count=10., achieved=stage == 3,
                                            predicted_log10_count=[-8., -.6, -.3, 1.1][stage],
                                            reference_covered=True, estimated=False))
        return result

    def parameters(*args: object) -> list[dict[str, float]]:
        """Deux courbes courantes suffisent à représenter les deux branches."""
        return [dict(D=d, B=5., SP=0., LP=0., HP=1.) for d in (1., 2.)]

    def transform(
        image: np.ndarray, curve: dict[str, float], *args: object, **kwargs: object,
    ) -> np.ndarray:
        """Le meilleur premier choix est terminal ; son concurrent peut progresser."""
        stage = stage_of(image)
        delta = .02*curve['D'] if stage == 0 else .04 if stage == 1 and curve['D'] == 2 else 0.
        return image+np.float32(delta)

    monkeypatch.setattr(module, 'piecewise_histogram_profile', profiles)
    monkeypatch.setattr(module, 'add_regression_floor_objective', floor)
    monkeypatch.setattr(ShapeGHSProcessor, 'candidate_parameters', staticmethod(parameters))
    monkeypatch.setattr(ShapeGHSProcessor, 'transform_candidate', staticmethod(transform))
    monkeypatch.setattr(ShapeGHSProcessor, 'peak_gap_parameters', staticmethod(lambda records: []))
    monkeypatch.setattr(ShapeGHSProcessor, 'population_sampling', False)
    options = dict(ghs_post_blackpoint='off', ghs_quartile_iterations=3,
                   ghs_quartile_validations=4, ghs_tail_height=0.)
    backend, analyzer = ComputeBackend('cpu'), HistogramAnalyzer()
    single_args = GHSOptimizer.options(argparse.Namespace(**options, ghs_shape_branches=1))
    _, single = ShapeGHSProcessor().search(source, analyzer, backend, single_args)
    args = GHSOptimizer.options(argparse.Namespace(**options, ghs_shape_branches=2))
    output, report = ShapeGHSProcessor().search(source, analyzer, backend, args)
    assert not single['targets_achieved']
    assert report['targets_achieved']
    assert [step['parameters']['D'] for step in report['selected_path']] == [1., 2.]
    assert len(report['branch_generations'][0]['retained']) == 2
    assert all(item['validations'] <= args.ghs_quartile_validations for item in report['branch_generations'])
    for entry in report['iterations']:
        for trial in entry['verified_candidates']:
            profile = trial['full_profile']
            assert not trial['rejections']
            assert profile['shape_preservation']['peak_shift'] == pytest.approx(abs(profile['peak']-base['peak']))
    np.testing.assert_allclose(output, source+.06, atol=1e-7)
    np.testing.assert_array_equal(source, original)
    assert report['branch_storage'] == 'parameters_only'
    json.dumps(report, allow_nan=False)


def test_profile_measures_the_valley_before_a_displaced_dominant_peak() -> None:
    """L'audit conserve la trace d'un second mode à gauche, même hors de la spline."""
    counts = curved_counts()
    clean = piecewise_histogram_profile(counts)
    assert clean['prepeak_valley_dex'] < .1
    counts[20] = 2e9
    displaced = piecewise_histogram_profile(counts)
    assert displaced['peak'] > clean['peak']
    assert displaced['prepeak_valley_dex'] > clean['prepeak_valley_dex']+1


def test_local_initial_peak_detects_a_second_bump() -> None:
    """L'enveloppe locale du pic ne dépend pas du lissage de la spline du corps."""
    counts = curved_counts()
    counts[4:14] = [1e6, 8e5, 6e5, 4e5, 2e5, 3e5, 5e5, 5e5, 3e5, 1e5]
    lightly_smoothed = piecewise_histogram_profile(counts, smoothing=1e-5)
    strongly_smoothed = piecewise_histogram_profile(counts, smoothing=.01)
    peak = lightly_smoothed['initial_peak']
    assert peak['secondary_rise_fraction'] > .1
    assert peak == strongly_smoothed['initial_peak']
    assert peak['end'] < .2


def test_candidate_histograms_are_evaluated_after_the_actual_blackpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chaque évaluation estimée et complète reçoit bien les pixels après BP."""
    from copy import deepcopy
    import lib.ghs_quartiles as module
    from lib.auto_ghs import luminance
    from lib.ghs_blackpoint import HistogramBlackpointNormalizer

    source = np.repeat(np.random.default_rng(32).uniform(.01, .45, (64, 128))[None],
                       3, axis=0).astype('f4')
    base = piecewise_histogram_profile(curved_counts())
    apply = HistogramBlackpointNormalizer.apply
    pending: list[np.ndarray] = []
    evaluated = 0

    def normalize(data: np.ndarray, *args: object, **kwargs: object) -> tuple[np.ndarray, JSONReport]:
        """Mémorise l'histogramme des pixels réellement recalés, sans changer le calcul."""
        assert not pending
        output, audit = apply(data, *args, **kwargs)
        light = luminance(output, 'human')
        pending.append(np.histogram(light[light > 1e-6], bins=128, range=(0, 1))[0])
        return output, audit

    def profile(counts: np.ndarray, *args: object) -> JSONReport:
        """Vérifie l'ordre avant de fournir un profil mesurable au classement."""
        nonlocal evaluated
        if pending:
            np.testing.assert_array_equal(counts, pending.pop())
            evaluated += 1
        return deepcopy(base)

    def transform(image: np.ndarray, *args: object, **kwargs: object) -> np.ndarray:
        """Un étirement commun conserve les mesures utiles et ajoute un décalage."""
        return 2*image+.02

    monkeypatch.setattr(HistogramBlackpointNormalizer, 'apply', staticmethod(normalize))
    monkeypatch.setattr(module, 'piecewise_histogram_profile', profile)
    monkeypatch.setattr(ShapeGHSProcessor, 'transform_candidate', staticmethod(transform))
    monkeypatch.setattr(ShapeGHSProcessor, 'population_sampling', False)
    monkeypatch.setattr(ShapeGHSProcessor, 'peak_gap_parameters', staticmethod(lambda records: []))
    monkeypatch.setattr(ShapeGHSProcessor, 'candidate_parameters', staticmethod(
        lambda *args: [dict(D=1., B=5., SP=0., LP=0., HP=1.)],
    ))
    args = GHSOptimizer.options(argparse.Namespace(ghs_quartile_iterations=1,
                                                  ghs_quartile_validations=1,
                                                  ghs_shape_branches=1))
    _, report = ShapeGHSProcessor().search(source, HistogramAnalyzer(), ComputeBackend('cpu'), args)
    assert evaluated >= 2 and not pending
    assert report['iterations'][0]['validations']
    assert report['blackpoint_order'] == 'GHS, linear BP, compliance'
