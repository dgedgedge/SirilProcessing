"""Mesures fixes, préférence de ciel sobre et repli sans segmentation fiable."""
from copy import deepcopy

import numpy as np
import pytest

from lib.ghs_weak_structure import WeakStructureQuality as Weak
from lib.ghs_weak_structure import WeakStructureEvaluator
from lib.ghs_final_stretch import ProtectedGHSFinalizer
from lib.ghs_quartiles import QuartileGHSProcessor
from lib.type_defs import JSONReport


def scene() -> np.ndarray:
    """Construit une émission étendue faible sur ciel bruité avec un gradient doux."""
    y, x = np.indices((160, 160))
    noise = np.random.default_rng(73).normal(0, .001, x.shape)
    return (.02+.001*x/160+.035*np.exp(-((x-80)**2+(y-80)**2)/600)+noise)


def test_evaluator_segments_once_and_returns_independent_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plusieurs candidats partagent une seule segmentation et des statistiques initiales fixes."""
    source = scene()
    expected = Weak.measure(source, np.sqrt(source))
    classify = Weak.classify_grid
    calls = []

    def counted(
        cls: type[Weak], reference: np.ndarray, *, weak_sigma: float,
    ) -> tuple[JSONReport, dict[str, np.ndarray]]:
        """Compte les constructions réelles des masques, indépendamment des évaluations."""
        calls.append(reference.shape)
        return classify(reference, weak_sigma=weak_sigma)

    monkeypatch.setattr(Weak, 'classify_grid', classmethod(counted))
    model = WeakStructureEvaluator(source)
    initial = model.evaluate(source)
    stretched = model.evaluate(np.sqrt(source))
    assert stretched == expected
    assert initial['reference'] == stretched['reference']
    assert initial['candidate'] != stretched['candidate']
    initial['reference']['faint_median'] = -1
    initial['sigma_thresholds']['weak'] = -1
    assert model.evaluate(source)['reference'] == stretched['reference']
    assert model.evaluate(source)['sigma_thresholds']['weak'] == 1.1
    assert len(calls) == 1


def test_evaluator_owns_its_reference_and_rejects_mismatched_reuse() -> None:
    """Une modification externe ou une autre image/grille ne remplace pas les zones conservées."""
    source = scene()
    candidate = source.copy()
    model = WeakStructureEvaluator(source)
    before = model.evaluate(candidate)
    source[:] = .5
    assert model.evaluate(candidate) == before
    with pytest.raises(ValueError, match='Référence'):
        Weak.measure(source, candidate, evaluator=model)
    with pytest.raises(ValueError, match='Référence'):
        Weak.measure(candidate, candidate, evaluator=model, weak_sigma=2.)
    with pytest.raises(ValueError, match='Géométrie'):
        model.evaluate(candidate[:20])
    other = WeakStructureEvaluator(source)
    assert not other.evaluate(source)['available'] and before['available']


def test_unavailable_evaluator_is_also_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une référence non mesurable conserve son indisponibilité sans relancer la détection."""
    source = np.full((64, 64), .02)
    model = WeakStructureEvaluator(source)

    def unexpected(*args: object, **kwargs: object) -> None:
        """Signale une reconstruction interdite après la préparation de l'instance."""
        pytest.fail('Les masques ont été recalculés')

    monkeypatch.setattr(Weak, 'classify_grid', unexpected)
    assert not model.evaluate(source)['available']
    assert not model.evaluate(source*2)['available']


def test_diagnostic_band_has_strict_bounds_without_connectivity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La bande 1<S<seuil exclut les bornes et reste visible sans aucune graine forte."""
    reference = .02 + np.random.default_rng(16).normal(0, .001, (64, 64))
    difference = np.diff(reference, axis=1)
    sigma = 1.4826*np.median(abs(difference-np.median(difference)))/np.sqrt(2)
    significance = np.full(reference.shape, 1.)
    significance[:, 16:32] = 1.5
    significance[:, 32:48] = 2.
    significance[:, 48:] = 2.5

    def filtered(image: np.ndarray, scale: float, *, mode: str) -> np.ndarray:
        """Fournit un excès connu à l'échelle 1, nul aux autres échelles et sans fond."""
        return (significance*(sigma/np.sqrt(4*np.pi)) if scale == 1.
                else np.zeros_like(image))

    monkeypatch.setattr('lib.ghs_weak_structure.gaussian_filter', filtered)
    audit, masks = Weak.classify_grid(reference, weak_sigma=2.)
    assert not audit['classification_available'] and not audit['available']
    assert masks['subthreshold'][:, 16:32].all()
    assert masks['subthreshold'].sum() == 64*16
    assert audit['subthreshold_count'] == 64*16
    _, no_band = Weak.classify_grid(reference, weak_sigma=1.)
    assert not no_band['subthreshold'].any()
    assert not no_band['objects'].any()
    assert np.all(no_band['below_one'] | no_band['above_threshold'] | no_band['on_threshold'])
    assert no_band['on_threshold'][:, :16].all()
    assert not no_band['below_one'][:, :16].any()
    assert not no_band['above_threshold'][:, :16].any()
    assert masks['on_threshold'][:, 32:48].all()
    assert not masks['above_threshold'][:, 32:48].any()


def test_hysteresis_keeps_weak_extensions_but_not_isolated_weak_regions() -> None:
    """Le même signal faible est retenu seulement s'il rejoint une graine fiable."""
    signal = np.zeros((80, 80))
    signal[5:24, 5:30] = 2.
    signal[8:18, 8:18] = 4.
    signal[10:14, 10:14] = 8.
    signal[45:60, 45:60] = 2.
    signal[55:65, 5:15] = 2.
    signal[60, 10] = 8.  # Un pic isolé ne forme pas une graine de quatre cellules.
    masks = Weak.connected_regions(signal, np.ones(signal.shape, dtype=bool))
    assert masks['seeds'][11, 11]
    assert masks['middle'][9, 9]
    assert masks['faint'][6, 6]
    assert not masks['objects'][50, 50]
    assert not masks['objects'][60, 10]
    assert np.all(masks['faint'] <= masks['objects'])


def test_margin_starts_propagation_without_repeated_dilation() -> None:
    """La marge amorce la composante entière puis le faible, sans atteindre un îlot séparé."""
    from scipy.ndimage import binary_dilation

    signal = np.zeros((48, 48))
    signal[8:12, 8:12] = 8.
    signal[8:12, 13:20] = 2.  # Un trou sous seuil sépare le fragment des graines.
    signal[8:12, 21:24] = 2.  # Îlot derrière un nouveau trou, sans amorce initiale.
    signal[13, 9] = 3.5
    signal[13, 10] = Weak.WEAK_SIGMA  # Faible connecté à l'ajout orange.
    signal[13, 8] = 2.
    active = np.ones(signal.shape, dtype=bool)
    active[13, 8] = False
    initial = Weak.connected_regions(signal, active)
    snapshot = {name: mask.copy() for name, mask in initial.items()}
    result = Weak.admit_margin_regions(signal, active, initial)
    expected = binary_dilation(snapshot['objects'], iterations=2)
    np.testing.assert_array_equal(result['exclusion'], expected)
    assert result['faint'][8:12, 13].all()
    assert result['objects'][8:12, 14:20].all()
    assert not result['objects'][8:12, 21:24].any()
    assert not result['objects'][8:12, 12].any()
    assert result['middle'][13, 9] and not result['faint'][13, 9]
    assert result['faint'][13, 10] and not result['admitted'][13, 8]
    assert not (result['admission_starts'] & ~expected).any()
    assert (result['objects'] & ~expected).any()
    np.testing.assert_array_equal(result['seeds'], snapshot['seeds'])
    for name in initial:
        np.testing.assert_array_equal(initial[name], snapshot[name])
    with pytest.raises(ValueError, match='déjà préparée'):
        Weak.admit_margin_regions(signal, active, result)


def test_margin_admission_needs_an_initial_object() -> None:
    """Une vaste zone significative sans graine ne crée pas sa propre marge d'admission."""
    signal = np.full((48, 48), 2.)
    active = np.ones(signal.shape, dtype=bool)
    initial = Weak.connected_regions(signal, active)
    result = Weak.admit_margin_regions(signal, active, initial)
    assert not result['objects'].any()
    assert not result['exclusion'].any()
    assert not result['admitted'].any()


def test_admission_propagation_stops_at_frozen_sky() -> None:
    """Même significatif à grande échelle, le ciel préalablement défini reste une barrière."""
    from scipy.ndimage import binary_dilation

    signal = np.zeros((48, 48))
    signal[8:12, 8:12] = 8.
    signal[8:12, 13:23] = 2.
    active = np.ones(signal.shape, dtype=bool)
    initial = Weak.connected_regions(signal, active)
    candidates = np.zeros_like(active)
    candidates[:, 17] = True
    result = Weak.admit_margin_regions(signal, active, initial, sky_candidates=candidates)
    expected_sky = candidates & ~binary_dilation(initial['objects'], iterations=2)
    np.testing.assert_array_equal(result['sky'], expected_sky)
    assert result['objects'][8:12, 13:17].all()
    assert not result['objects'][8:12, 17:23].any()
    assert not (result['objects'] & result['sky']).any()


def test_hysteresis_does_not_jump_over_a_gap_below_weak_threshold() -> None:
    """La connexité n'invente pas de pont de signal à travers le fond."""
    signal = np.zeros((48, 48))
    signal[8:15, 8:15] = 4.
    signal[10:12, 10:12] = 8.
    signal[20:30, 8:15] = 2.
    masks = Weak.connected_regions(signal, np.ones(signal.shape, dtype=bool))
    assert masks['objects'][10, 10]
    assert not masks['faint'].any()
    assert not masks['objects'][25, 10]


def test_weak_threshold_extends_connected_emission_down_to_1_2_sigma() -> None:
    """Le seuil inclusif 1,2 étend l'objet, sans retenir les îlots ou franchir 1,19."""
    signal = np.zeros((48, 48))
    signal[8:12, 8:12] = 8.
    signal[12:16, 8:12] = 1.2
    signal[16, 8:12] = 1.19
    signal[17:22, 8:12] = 1.3
    signal[30:34, 30:34] = 1.2
    masks = Weak.connected_regions(signal, np.ones(signal.shape, dtype=bool), weak_sigma=1.2)
    assert masks['faint'][12:16, 8:12].all()
    assert not masks['objects'][16:22, 8:12].any()
    assert not masks['objects'][30:34, 30:34].any()


def test_connectivity_uses_eight_neighbors_and_keeps_brightness_fluctuations() -> None:
    """Une liaison diagonale avec un renforcement local reste une extension connectée."""
    signal = np.zeros((48, 48))
    signal[8:12, 8:12] = 8.
    for i in range(12, 30):
        signal[i, i] = 2. if i % 2 else 4.
    masks = Weak.connected_regions(signal, np.ones(signal.shape, dtype=bool))
    assert masks['objects'][29, 29]
    assert masks['faint'][29, 29]
    assert masks['middle'][28, 28]


def test_bright_seed_support_is_required_but_not_imposed_on_extensions() -> None:
    """Une zone significative hors P95 n'est pas une graine ; les extensions restent admises."""
    signal = np.zeros((64, 64))
    signal[5:20, 5:20] = 2.
    signal[10:14, 10:14] = 8.
    signal[40:55, 40:55] = 8.
    allowed = np.zeros(signal.shape, dtype=bool)
    allowed[10:14, 10:14] = True
    masks = Weak.connected_regions(signal, np.ones(signal.shape, dtype=bool), seed_support=allowed)
    assert masks['faint'][7, 7]
    assert not masks['objects'][45, 45]


def test_insufficient_weak_extension_uses_the_lowest_retained_percentile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une petite couronne déclenche le repli sans modifier les objets ni le ciel."""
    monkeypatch.setattr(Weak, 'MIN_PIXELS', 64)
    y, x = np.indices((160, 160))
    image = (.02+.001*x/160+.035*np.exp(-((x-80)**2+(y-80)**2)/250)
             + np.random.default_rng(73).normal(0, .001, x.shape))
    result, masks = Weak.classify_grid(image)
    assert result['available'] and result['classification_available']
    assert result['faint_selection']['mode'] == 'lowest_percentile'
    assert result['faint_count'] < Weak.MIN_PIXELS
    assert masks['objects'].any()
    measured = Weak.measure(image, image)
    assert measured['available'] and 'candidate' in measured
    expected = int(np.ceil(masks['objects'].sum()*.01))
    assert masks['faint'].sum() == expected
    assert not (masks['faint'] & ~masks['objects']).any()


def test_faint_centile_selection_is_exact_stable_and_fixed() -> None:
    """Le repli prend exactement le centile inférieur, y compris en cas d'égalité."""
    light = np.ones((20, 20))
    objects = np.ones(light.shape, dtype=bool)
    detected = np.zeros_like(objects)
    light[10, 10] = .1
    selected, audit = Weak.faint_reference_regions(light, objects, detected)
    assert audit['mode'] == 'lowest_percentile'
    assert selected.sum() == 4
    assert selected[10, 10]
    assert selected.ravel()[:3].all()
    assert not detected.any()
    no_objects, _ = Weak.faint_reference_regions(light, ~objects, detected)
    assert not no_objects.any()
    tiny = np.zeros_like(objects)
    tiny[10, 10] = True
    one, _ = Weak.faint_reference_regions(light, tiny, detected)
    assert one.sum() == 1 and one[10, 10]
    detected[:2] = True
    kept, audit = Weak.faint_reference_regions(light, objects, detected)
    np.testing.assert_array_equal(kept, detected)
    assert audit['mode'] == 'detected_weak'


def test_each_initial_object_contains_a_bright_seed() -> None:
    """Les objets initiaux possèdent une graine ; les admissions restent dans leur marge figée."""
    from scipy.ndimage import label

    source = scene()
    audit, masks = Weak.classify_grid(source)
    assert audit['available'] and audit['seed_quantile'] == .95
    labels, count = label(masks['initial_objects'], structure=np.ones((3, 3)))
    assert count > 0
    for identifier in range(1, count+1):
        assert masks['seeds'][labels == identifier].any()
    assert np.all(source[masks['seeds']] > audit['seed_luminance_threshold'])
    assert not (masks['admission_starts'] & ~masks['exclusion']).any()
    assert not (masks['sky'] & masks['exclusion']).any()
    assert audit['admitted_count'] == int(masks['admitted'].sum())


def test_old_and_connected_segmentations_are_not_compared() -> None:
    """Une signature d'image identique ne suffit pas si la définition des zones a changé."""
    measured = Weak.measure(scene(), scene())
    legacy = deepcopy(measured)
    legacy.pop('segmentation_version')
    comparison = Weak.compare({'weak_structure': legacy}, {'weak_structure': measured})
    assert not comparison['available'] and comparison['score_adjustment'] == 0


@pytest.mark.parametrize('threshold', [1., 1.1, 1.2, 1.5, 2., 3.])
def test_configurable_threshold_changes_only_connected_extensions(threshold: float) -> None:
    """Les bornes sont inclusives, les graines fixes et les zones faibles décroissent avec le seuil."""
    signal = np.zeros((48, 48))
    signal[8:12, 8:12] = 8.
    signal[12:16, 8:12] = 2.
    signal[16:20, 8:12] = 1.2
    signal[20:24, 8:12] = 1.
    active = np.ones(signal.shape, dtype=bool)
    masks = Weak.connected_regions(signal, active, weak_sigma=threshold)
    assert masks['seeds'].sum() == 16
    assert masks['faint'][12:16, 8:12].all() == (threshold <= 2.)
    assert masks['faint'][16:20, 8:12].all() == (threshold <= 1.2)
    assert masks['faint'][20:24, 8:12].all() == (threshold <= 1.)


def test_different_sigma_thresholds_cannot_be_compared() -> None:
    """Même à masques identiques, les audits de seuils différents sont incompatibles."""
    measured = Weak.measure(scene(), scene(), weak_sigma=1.)
    other = deepcopy(measured)
    other['sigma_thresholds']['weak'] = 2.
    comparison = Weak.compare({'weak_structure': measured}, {'weak_structure': other})
    assert not comparison['available']


def measurements(sky: float, faint: float, contrast: float, cnr: float) -> JSONReport:
    """Fabrique une mesure appariée dont la provenance est explicite et fixe."""
    return dict(weak_structure=dict(
        available=True, reference_sha256='fixed', grid_shape=[100, 100],
        object_count=200, faint_count=50, sky_count=2000, faint_reference_range=[.1, .2],
        candidate=dict(sky_upper_median=sky, sky_median=sky, faint_median=faint,
                       contrast=contrast, cnr=cnr)))


def test_fixed_masks_are_independent_of_candidate_and_source_unchanged() -> None:
    """Deux rendus ont exactement les mêmes populations de mesure et signature."""
    source = scene()
    saved = source.copy()
    first = Weak.measure(source, source)
    after = Weak.measure(source, np.sqrt(source))
    assert first['available'] and after['available']
    for key in ('reference_sha256', 'faint_count', 'object_count', 'sky_count',
                'faint_reference_range'):
        assert first[key] == after[key]
    assert first['reference'] == after['reference']
    np.testing.assert_array_equal(source, saved)


def test_lower_sky_wins_at_preserved_visibility() -> None:
    """Le ciel plus sombre est préféré si le signal faible demeure lisible."""
    before = measurements(.1, .2, .3, 4)
    dark = Weak.compare(before, measurements(.06, .14, .4, 4.1))
    bright = Weak.compare(before, measurements(.2, .3, .2, 4.1))
    assert dark['lower_background_preferred']
    assert dark['score_adjustment'] > bright['score_adjustment']
    assert not bright['sufficient']


@pytest.mark.parametrize('contrast,cnr,faint', [(.1, 4, .15), (.3, 1, .15), (.3, 4, .001)])
def test_darkening_cannot_hide_signal_loss(contrast: float, cnr: float, faint: float) -> None:
    """Une perte de contraste, de CNR ou de visibilité ne reçoit aucun bonus de ciel."""
    result = Weak.compare(measurements(.1, .2, .3, 4),
                          measurements(.01, faint, contrast, cnr))
    assert not result['sufficient']
    assert not result['lower_background_preferred']


def test_linear_input_gets_an_initial_visibility_budget() -> None:
    """Un objet initialement sombre peut être révélé sans pénaliser l'ouverture utile."""
    before = measurements(.001, .002, .02, 2)
    after = measurements(.025, .08, .3, 2)
    result = Weak.compare(before, after)
    assert result['visibility_gain_budget'] == 40
    assert result['sky_penalty'] == 0
    assert result['score_adjustment'] > 0


def test_missing_or_different_reference_does_not_change_legacy_score() -> None:
    """Un champ uniforme ou une référence incompatible n'invente aucun signal."""
    uniform = np.full((100, 100), .1)
    assert not Weak.measure(uniform, uniform)['available']
    assert Weak.compare({}, {})['score_adjustment'] == 0
    before = measurements(.1, .2, .3, 4)
    other = deepcopy(before)
    other['weak_structure']['reference_sha256'] = 'different'
    assert not Weak.compare(before, other)['available']


def test_legacy_diffuse_guard_only_relaxes_when_requested() -> None:
    """Le mode historique garde sa règle ; le piecewise peut préférer un ciel sobre."""
    before = measurements(.1, .2, .3, 4)
    after = measurements(.06, .14, .4, 4.1)
    before.update(diffuse_median=.2, sky_median=.1)
    after.update(diffuse_median=.14, sky_median=.06)
    assert QuartileGHSProcessor.diffuse_rejections(before, after)
    assert not QuartileGHSProcessor.diffuse_rejections(
        before, after, allow_weak_preservation=True)


def test_curvature_softening_does_not_apply_to_rising_slopes() -> None:
    """La pénalité ne se réduit que pour un profil toujours descendant et préservé."""
    weak = dict(available=True, sufficient=True)
    audit = dict(penalty=8., candidate_geometry=dict(slopes=[-3., -2., -4.]))
    assert Weak.shape_penalty(audit, weak) == .25
    audit['candidate_geometry']['slopes'][1] = .2
    assert Weak.shape_penalty(audit, weak) == 8
    assert Weak.shape_penalty(audit, {}) == 8


def test_bad_geometry_or_nonfinite_light_is_refused() -> None:
    """Les entrées incohérentes ne deviennent pas une qualité apparente valide."""
    source = scene()
    with pytest.raises(ValueError):
        Weak.measure(source, source[:5])
    candidate = source.copy()
    candidate[0, 0] = np.nan
    with pytest.raises(ValueError):
        Weak.measure(source, candidate)


def test_new_modes_remain_blocking_even_with_weak_structure_bonus() -> None:
    """Un bonus de ciel ne peut jamais rendre admissible une nouvelle bosse sombre."""
    from lib.ghs_histogram_shape import histogram_selection_key
    from lib.ghs_render_quality import HistogramRenderQuality
    before = measurements(.1, .2, .3, 4)
    after = measurements(.06, .14, .4, 4.1)
    for measured in (before, after):
        measured.update(sky_median=.05, diffuse_median=.15, noise=.01)
    profile = dict(measurable=True, peak=.1, shape_preservation=dict(
        measurable=True, achieved=True, rejections=[], penalty=0.,
        candidate_geometry=dict(slopes=[-3., -2., -4.])),
        low_light_modes=dict(measurable=True, prominence_fraction=.8))
    reference = dict(low_light_modes=dict(measurable=True, prominence_fraction=0.))
    result = HistogramRenderQuality.attach(profile, before, after, reference,
                                           bright_retention=.9)
    assert result['render_quality']['weak_structure']['lower_background_preferred']
    assert not histogram_selection_key(result)[0]
    assert 'new low-light histogram mode' in result['low_light_mode_guard']['rejections']


def test_audit_annotations_expose_measured_quality() -> None:
    """L'encart montre les mesures et contribution du nouveau contrôle."""
    from lib.ghs_diagnostics import _weak_structure_annotation
    comparison = Weak.compare(measurements(.1, .2, .3, 4), measurements(.06, .14, .4, 4.1))
    text = '\n'.join(_weak_structure_annotation(comparison))
    assert 'Ciel régional Q90 : 0.0600' in text
    assert 'Contraste faible : 0.400' in text
    assert 'Contribution au score' in text
    assert 'politique antérieure' in '\n'.join(_weak_structure_annotation({'available': False}))


def test_histogram_bonus_cannot_reward_wasted_sky_lift() -> None:
    """Un objet déjà visible ne justifie pas un ciel doublé à CNR inchangé."""
    before = measurements(.05, .15, .3, 4)
    after = measurements(.1, .25, .35, 4)
    result = Weak.compare(before, after)
    assert result['unnecessary_sky_lift']
    assert result['unnecessary_sky_penalty'] == pytest.approx(2.)
    assert result['score_adjustment'] < -2
