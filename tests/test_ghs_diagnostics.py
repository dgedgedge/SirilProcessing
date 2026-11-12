"""Séries d'essais AutoGHS : véritables axes log, traçabilité et galerie locale."""

import json
import argparse
from pathlib import Path

import pytest
import numpy as np
from PIL import Image
from matplotlib.figure import Figure

from lib.ghs_diagnostics import HistogramDiagnostics


def test_series_exports_major_trials_with_only_logarithmic_count_axes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Les étapes et verdicts restent distincts, avec des axes Y comparables."""
    first = {'counts': [0, 100, 20, 4, 0]}
    candidate = {'counts': [0, 60, 40, 8, 2]}
    accepted = dict(parameters={'D': .25}, histogram=candidate)
    rejected = dict(parameters={'D': 4.}, histogram=first, rejections=['global histogram flattening'])
    report = dict(histograms=[first, candidate], shape_entry_histogram=first,
                  histogram_stages=[dict(stage='<script>entrée</script>', status='reference', histogram=first)],
                  quartile_stretch=dict(searches=dict(shape=dict(report=dict(iterations=[dict(
                      iteration=1, accepted=accepted, major_rejected=rejected)])))))
    source = tmp_path/'source.json'
    source.write_text(json.dumps(report))
    original = source.read_bytes()
    destination = tmp_path/'series'
    destination.mkdir()
    stale = destination/'histogram_999.png'
    stale.write_bytes(b'old generated graph')
    stale_jpeg = destination/'image_999.jpg'
    stale_jpeg.write_bytes(b'old generated preview')
    unrelated = destination/'notes.png'
    unrelated.write_bytes(b'unrelated file')
    saved, limits = [], []
    real_save = Figure.savefig

    def inspect(figure: Figure, *args: object, **kwargs: object) -> None:
        """Vérifie les axes réels, sans dépendre du contenu raster des PNG."""
        axes = [axis for axis in figure.axes if axis.axison]
        assert len(axes) == 1
        assert axes[0].get_yscale() == 'log'
        assert axes[0].get_xscale() == 'linear'
        assert axes[0].get_xlim() == (0, 1)
        limits.append(axes[0].get_ylim())
        saved.append(True)
        real_save(figure, *args, **kwargs)

    monkeypatch.setattr(Figure, 'savefig', inspect)
    output = HistogramDiagnostics().post_process(source, tmp_path/'series')
    assert len(saved) == 4
    assert all(limit == limits[0] for limit in limits)
    manifest = json.loads(Path(output['histogram_series_manifest']).read_text())
    assert [entry['status'] for entry in manifest] == ['reference', 'accepted', 'rejected', 'selected']
    assert all(Path(entry['path']).is_file() for entry in manifest)
    assert all(entry['yscale'] == 'log' for entry in manifest)
    assert manifest[2]['rejections'] == ['global histogram flattening']
    page = Path(output['histogram_gallery']).read_text()
    assert '&lt;script&gt;' in page and '<script>' not in page
    assert 'color-scheme:dark' in page
    assert 'background:#000' in page
    assert source.read_bytes() == original
    assert not stale.exists()
    assert not stale_jpeg.exists()
    assert unrelated.read_bytes() == b'unrelated file'
    from lib.processing_html import ProcessingHtmlReport
    assert ProcessingHtmlReport(tmp_path).generate().is_file()
    assert Path(output['histogram_gallery']).is_file()
    assert Path(output['histogram_gallery']).name != 'index.html'


def test_series_requires_recorded_histograms(tmp_path: Path) -> None:
    """L'export ne fabrique pas de courbe lorsque le rapport ne contient rien."""
    with pytest.raises(ValueError, match='Aucun histogramme'):
        HistogramDiagnostics.write_series({}, tmp_path/'empty')


def test_series_figures_show_full_and_estimated_peak_displacements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Les PNG affichent les audits retenus, même après rejet d'une estimation."""
    first = {'counts': [0, 100, 20, 4, 0]}
    full = {'shape_preservation': {'peak_shift': .0078125, 'maximum_peak_shift': .25}}
    estimated = {'shape_preservation': {'peak_shift': .0546875, 'maximum_peak_shift': .05}}
    report = {'histograms': [first, first], 'histogram_stages': [
        {'stage': 'Entrée linéaire', 'status': 'reference', 'histogram': first},
    ], 'quartile_stretch': {'after': full, 'searches': {'shape': {'report': {'iterations': [{
        'iteration': 1,
        'accepted': {'histogram': first, 'full_profile': full},
        'major_estimated_rejected': {
            'histogram': first, 'profile': estimated, 'estimated': True,
            'rejections': ['histogram peak displacement'],
        },
    }]}}}}}
    annotations: list[str] = []
    real_save = Figure.savefig

    def inspect(figure: Figure, *args: object, **kwargs: object) -> None:
        """Observe le texte effectivement transmis au moteur de rendu PNG."""
        annotations.append(figure.axes[1].texts[0].get_text())
        real_save(figure, *args, **kwargs)

    monkeypatch.setattr(Figure, 'savefig', inspect)
    output = HistogramDiagnostics.write_series(report, tmp_path/'series')
    assert len(annotations) == 4
    assert 'Déplacement du pic : non renseigné' in annotations[0]
    assert 'Déplacement du pic : 0.007812' in annotations[1]
    assert 'Déplacement du pic (estimé) : 0.054688' in annotations[2]
    assert 'Plafond du déplacement : 0.050000' in annotations[2]
    assert 'Déplacement du pic : 0.007812' in annotations[3]
    for record in output['histogram_series']:
        with Image.open(record['path']) as image:
            assert image.format == 'PNG'


def test_cdf_backtracking_trials_are_exported_between_range_and_balance(tmp_path: Path) -> None:
    """Les refus puis la force retenue restent visibles dans l'ordre des phases."""
    first = {'counts': [2, 100, 20, 4, 1]}
    last = {'counts': [1, 70, 35, 9, 2]}
    attempts = [dict(parameters={'strength': .7}, histogram=last,
                     rejections=['local histogram flattening']),
                dict(parameters={'strength': .35}, histogram=last, rejections=[])]
    report = dict(histograms=[first, last], histogram_stages=[
        dict(stage='Entrée linéaire', status='reference', histogram=first),
        dict(stage='RANGE — passe 1', status='accepted', histogram=first),
        dict(stage='Égalisation partielle', status='applied', histogram=last),
    ], histogram_balance=dict(full_validation=attempts, before_histogram=first))
    result = HistogramDiagnostics.write_series(report, tmp_path/'cdf')
    series = result['histogram_series']
    assert [item['stage'] for item in series] == [
        'Entrée linéaire', 'RANGE — passe 1', 'Égalisation — essai 1',
        'Égalisation — essai 2', 'Égalisation partielle', 'Fond retenu',
    ]
    assert series[2]['status'] == 'rejected'
    assert series[2]['rejections'] == ['local histogram flattening']
    assert series[3]['status'] == 'accepted'
    assert series[3]['parameters']['strength'] == .35
    assert all(Path(item['path']).is_file() for item in series)


@pytest.mark.parametrize('with_jpegs', [False, True])
def test_branch_gallery_keeps_all_verified_positions_with_their_lineage(
    tmp_path: Path, with_jpegs: bool,
) -> None:
    """Les alternatives admissibles restent visibles sans être déclarées retenues."""
    histogram = {'counts': [0, 100, 20, 4, 1]}
    trials = [dict(histogram=histogram, parameters={'D': float(index)},
                   full_profile={'shape_preservation': {'peak_shift': shift}},
                   branch_id=f'b{index}', parent_branch_id='root',
                   continued=index < 3, on_selected_path=index == 2)
              for index, shift in enumerate((.02, .1, .18), 1)]
    report = dict(histograms=[histogram, histogram], quartile_stretch=dict(searches=dict(
        shape=dict(report=dict(iterations=[dict(iteration=1, accepted=trials[1],
                                                verified_candidates=trials)])))))
    from lib.auto_ghs import ComputeBackend

    recorder = HistogramDiagnostics(ComputeBackend('cpu')) if with_jpegs else None
    try:
        if recorder is not None:
            for index, trial in enumerate(trials, 1):
                recorder.capture(trial, np.full((3, 24, 32), index/10, dtype='f4'))
            recorder.capture(report, np.full((3, 24, 32), .2, dtype='f4'))
        output = HistogramDiagnostics.write_series(report, tmp_path/'branches', recorder)
    finally:
        if recorder is not None:
            recorder.close()
    series = output['histogram_series']
    assert len(series) == 4
    assert [item['branch_id'] for item in series[:3]] == ['b1', 'b2', 'b3']
    assert [item['status'] for item in series[:3]] == ['admissible', 'accepted', 'admissible']
    assert all(item['parent_branch_id'] == 'root' for item in series[:3])
    assert series[1]['on_selected_path']
    assert not series[2]['continued']
    assert series[-1]['selected_trial_index'] == output['selected_trial_index'] == 2
    if with_jpegs:
        assert series[-1]['selected_trial_jpeg'] == series[1]['image_jpeg']
        for item, level in zip(series, (.1, .2, .3, .2)):
            with Image.open(item['image_jpeg']) as image:
                np.testing.assert_allclose(image.getpixel((16, 12)), [255*level]*3, atol=3)


def test_final_figures_identify_the_selected_branch_before_later_alternatives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La provenance suit l'identifiant retenu, même si tous les histogrammes sont égaux."""
    histogram = {'counts': [0, 100, 20, 4, 1]}
    chosen = dict(histogram=histogram, branch_id='b2', parent_branch_id='root',
                  on_selected_path=True, parameters={'D': .5})
    later = dict(histogram=histogram, branch_id='b3', parent_branch_id='b1',
                 on_selected_path=False, parameters={'D': 1.})
    report = dict(histograms=[histogram, histogram], recomposed_histogram=histogram,
                  quartile_stretch=dict(selected_search='shape', searches=dict(shape=dict(report=dict(
                      selected_branch_id='b2', iterations=[
                          dict(iteration=1, verified_candidates=[chosen], accepted=chosen),
                          dict(iteration=2, verified_candidates=[later], accepted=later),
                      ])))))
    annotations: list[str] = []
    real_save = Figure.savefig

    def inspect(figure: Figure, *args: object, **kwargs: object) -> None:
        """Vérifie les encarts réellement dessinés pour les branches et les résultats."""
        annotations.append(figure.axes[1].texts[0].get_text())
        real_save(figure, *args, **kwargs)

    monkeypatch.setattr(Figure, 'savefig', inspect)
    output = HistogramDiagnostics.write_series(report, tmp_path/'selected')
    assert output['selected_trial_index'] == 1
    assert 'SÉRIE — PROFIL PAR MORCEAUX' in annotations[0]
    assert 'IMAGE 001 / 004' in annotations[0]
    assert 'Branche parente : b1' in annotations[1]
    for index in (2, 3):
        assert 'SÉRIE — RÉSULTAT RETENU' in annotations[index]
        assert 'Essai sélectionné : 001' in annotations[index]
        assert 'Histogramme : histogram_001.png' in annotations[index]
        assert output['histogram_series'][index]['selected_trial_index'] == 1
    page = Path(output['histogram_gallery']).read_text()
    assert 'Essai sélectionné : <a href="histogram_001.png">001</a>' in page


@pytest.mark.parametrize('selected, expected', [('length', 2), ('reference_level', 3), ('unchanged', 1)])
def test_selected_legacy_search_or_unchanged_entry_has_an_exact_series_number(
    tmp_path: Path, selected: str, expected: int,
) -> None:
    """Le dernier moteur exploré ne remplace pas le moteur effectivement choisi."""
    histogram = {'counts': [0, 100, 20, 4, 1]}
    accepted = dict(histogram=histogram, parameters={'D': .5})
    searches = {name: dict(report=dict(iterations=[dict(iteration=1, accepted=accepted)]))
                for name in ('length', 'reference_level')}
    report = dict(histograms=[histogram, histogram], histogram_stages=[
        dict(stage='Présentation protégée', status='applied', histogram=histogram),
    ], quartile_stretch=dict(selected_search=selected, searches=searches))
    output = HistogramDiagnostics.write_series(report, tmp_path/'legacy')
    assert output['selected_trial_index'] == expected
    assert output['histogram_series'][-1]['selected_trial_index'] == expected


def test_missing_selection_provenance_does_not_invent_a_series_number(tmp_path: Path) -> None:
    """Un rapport ancien sans décision ne reçoit pas arbitrairement le dernier essai."""
    output = HistogramDiagnostics.write_series({'histograms': [{'counts': [1, 20, 2]}]}, tmp_path/'unknown')
    assert output['selected_trial_index'] is None
    assert output['histogram_series'][-1]['selected_trial_index'] is None


def test_trial_jpegs_match_accepted_rejected_and_recomposed_histogram_numbers(tmp_path: Path) -> None:
    """Chaque figure reçoit ses pixels, y compris un refus et la fusion finale."""
    from lib.auto_ghs import ComputeBackend

    source = np.full((3, 32, 40), .2, dtype='f4')
    stars = np.full_like(source, .1)
    first = {'counts': [0, 100, 20, 4, 1]}
    initial = dict(stage='Entrée linéaire', status='reference', histogram=first)
    accepted = dict(parameters={'D': .25}, histogram=first)
    rejected = dict(parameters={'D': 2.}, histogram=first, estimated=True,
                    rejections=['local histogram flattening'])
    report = dict(histograms=[first, first], histogram_stages=[initial],
                  recomposed_histogram=first, quartile_stretch=dict(searches=dict(
                      shape=dict(report=dict(iterations=[dict(iteration=1, accepted=accepted,
                                                             major_estimated_rejected=rejected)])))))
    recorder = HistogramDiagnostics(ComputeBackend('cpu'), stars, .95, 'TOP-DOWN')
    try:
        recorder.capture(initial, source)
        recorder.capture(accepted, source+.1)
        recorder.capture(rejected, source+.4)
        recorder.capture(report, source+.1)
        composed = {}
        recorder.capture(composed, .95*(source+.1)+stars*(1-.95*(source+.1)), with_stars=True)
        report['recomposed_trial_image_id'] = composed['trial_image_id']
        source[:] = 0  # Les captures doivent déjà être sur disque.
        result = HistogramDiagnostics.write_series(report, tmp_path/'series', recorder)
    finally:
        recorder.close()
    series = result['histogram_series']
    assert result['trial_jpegs_enabled']
    assert len(series) == 5
    for index, level in enumerate([.2, .3, .6, .3, .3565], 1):
        item = series[index-1]
        path = Path(item['image_jpeg'])
        assert path.name == f'image_{index:03d}.jpg'
        with Image.open(path) as image:
            assert image.size == (40, 32)
            np.testing.assert_allclose(image.getpixel((20, 16)), [255*level]*3, atol=3)
        assert ('with_stars_jpeg' in item) == (index < 5)
    assert series[2]['image_histogram_estimated']
    assert series[2]['status'] == 'rejected'
    with Image.open(series[1]['with_stars_jpeg']) as image:
        np.testing.assert_allclose(image.getpixel((20, 16)), [255*.3565]*3, atol=3)
    html = Path(result['histogram_gallery']).read_text()
    assert 'image_003.jpg' in html and 'image_003_with_stars.jpg' in html
    assert not recorder._images
    with pytest.raises(RuntimeError, match='fermé'):
        recorder.capture({}, source)


def test_old_histogram_report_cannot_reconstruct_a_trial_image(tmp_path: Path) -> None:
    """Les effectifs seuls ne permettent pas de restituer les pixels d'un essai."""
    with pytest.raises(ValueError, match='capturés pendant AutoGHS'):
        HistogramDiagnostics().post_process(tmp_path/'report.json', tmp_path/'out',
                                            argparse.Namespace(ghs_save_trial_jpegs=True))
