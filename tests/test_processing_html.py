"""Navigation HTML hors ligne, images, audits et protection des fichiers existants."""
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from lib.processing_html import ProcessingHtmlReport


class Links(HTMLParser):
    """Collecte les URL locales pour vérifier les liens effectivement générés."""

    def __init__(self) -> None:
        """Initialise les listes de liens et sources d'images."""
        super().__init__()
        self.urls: list[str] = []
        self.images: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Capture href et src sans interpréter les fragments HTML comme du code."""
        values = dict(attrs)
        if tag == 'a' and values.get('href'):
            self.urls.append(values['href'])
        if tag == 'img' and values.get('src'):
            self.images.append(values['src'])


@pytest.fixture
def results(tmp_path: Path) -> Path:
    """Construit des résultats contenant aperçus, rapport et fichiers FITS témoins."""
    root = tmp_path / 'M31'
    step = root / '04_restoration/01_denoise_siril'
    checks = step / 'verification_finale'
    checks.mkdir(parents=True)
    for directory in (root / '01_calibration', root / '05_autoghs'):
        directory.mkdir()
    (root / 'M31.fits').write_bytes(b'FITS must not be read or changed')
    (root / 'M31.jpg').write_bytes(b'jpeg')
    (root / 'M31_annotated.png').write_bytes(b'png')
    (step / 'résultat & ciel.png').write_bytes(b'png')
    (step / 'candidate.fits').write_bytes(b'candidate')
    (step / 'report.json').write_text(json.dumps({
        'status': 'accepted', 'operation': 'denoise', 'engine': 'siril',
        'criteria': {'noise': {'status': 'PASS', 'value': .7, 'threshold': 1}},
        'parameters': {'modulation': .5}, 'comment': '<script>alert(1)</script>',
    }))
    (checks / 'verifications.txt').write_text('Bruit : PASS\nMesure 0.7 ; seuil 1\n')
    (root / '04_drizzle').mkdir()
    (root / '04_drizzle/SKIPPED.txt').write_text('Mode auto : FWHM 3 > seuil 2.5')
    return root


def test_recursive_pages_include_images_audits_and_highest_final_exports(results: Path) -> None:
    """Chaque niveau est navigable et les résultats finaux sont dans l'accueil."""
    original = {path: path.read_bytes() for path in results.rglob('*') if path.is_file()}
    home = ProcessingHtmlReport(results).generate()
    assert home == results / 'index.html'
    for directory in [results, *[p for p in results.rglob('*') if p.is_dir()]]:
        page = directory / 'index.html'
        assert page.is_file()
        parser = Links()
        parser.feed(page.read_text())
        for url in parser.urls + parser.images:
            assert not url.startswith(('http:', 'https:', 'javascript:'))
            assert (directory / unquote(urlsplit(url).path)).exists(), (page, url)
    parser = Links()
    parser.feed(home.read_text())
    assert 'M31.jpg' in parser.images
    assert 'M31_annotated.png' in parser.images
    assert 'M31.fits' in parser.urls
    step = results / '04_restoration/01_denoise_siril/index.html'
    text = step.read_text()
    assert 'PASS' in text and 'Seuil' in text and 'modulation' in text
    assert '<script>alert' not in text
    assert 'r%C3%A9sultat%20%26%20ciel.png' in text
    assert all(path.read_bytes() == content for path, content in original.items())
    ProcessingHtmlReport(results).generate()
    assert all(path.read_bytes() == content for path, content in original.items())


def test_foreign_index_is_refused_before_any_page_is_written(results: Path) -> None:
    """Une page étrangère dans un sous-dossier protège toute la publication."""
    existing = results / '04_restoration/index.html'
    existing.write_text('<h1>Manuel personnel</h1>')
    with pytest.raises(FileExistsError):
        ProcessingHtmlReport(results).generate()
    assert existing.read_text() == '<h1>Manuel personnel</h1>'
    assert not (results / 'index.html').exists()


def test_symlinked_directories_are_not_indexed_and_broken_reports_are_visible(
    results: Path, tmp_path: Path,
) -> None:
    """Ne suit pas les sources externes ; une erreur JSON reste explicitement visible."""
    outside = tmp_path / 'sources'
    outside.mkdir()
    (outside / 'private.json').write_text('{}')
    (results / 'raw_sources').symlink_to(outside, target_is_directory=True)
    (results / 'broken.json').write_text('{ incomplete')
    ProcessingHtmlReport(results).generate()
    assert not (outside / 'index.html').exists()
    assert 'lecture impossible' in (results / 'index.html').read_text()


def test_large_audits_are_bounded_and_complete_files_remain_linked(results: Path) -> None:
    """Les aperçus bornés gardent un accès au rapport complet."""
    (results / 'long.txt').write_text('x' * (ProcessingHtmlReport.TEXT_LIMIT + 20))
    (results / 'frames.json').write_text(json.dumps({'frames': list(range(1000))}))
    ProcessingHtmlReport(results).generate()
    page = (results / 'index.html').read_text()
    assert 'Texte abrégé' in page
    assert 'Données JSON détaillées' not in page
    assert 'long.txt' in page and 'href="frames.json"' not in page


def test_command_regenerates_existing_results_and_reports_missing_root(
    results: Path, tmp_path: Path,
) -> None:
    """La commande produit l'accueil et signale une racine absente par le code 1."""
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[1] / 'bin/indexProcessing.py'
    completed = subprocess.run(
        [sys.executable, str(script), str(results)],
        text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert str(results / 'index.html') in completed.stdout
    failed = subprocess.run(
        [sys.executable, str(script), str(tmp_path / 'missing')],
        text=True, capture_output=True, check=False,
    )
    assert failed.returncode == 1
    assert 'Indexation impossible' in failed.stderr


def test_hidden_directories_and_symbolic_indexes_are_protected(results: Path) -> None:
    """Les dossiers cachés sont exclus et un index symbolique n'est jamais remplacé."""
    hidden = results / '.private'
    hidden.mkdir()
    (hidden / 'data.txt').write_text('private')
    ProcessingHtmlReport(results).generate()
    assert not (hidden / 'index.html').exists()
    target = results / 'saved.html'
    target.write_text(ProcessingHtmlReport.MARKER + 'preserved')
    index = results / 'index.html'
    index.unlink()
    index.symlink_to(target)
    with pytest.raises(FileExistsError, match='symbolique'):
        ProcessingHtmlReport(results).generate()
    assert target.read_text() == ProcessingHtmlReport.MARKER + 'preserved'


def test_publication_does_not_invalidate_stack_but_source_changes_do(
    tmp_path: Path,
) -> None:
    """Les index récents ne relancent pas le stack ; les mutations de poses le font."""
    import os
    from lib.lightprocessor import LightProcessor

    root = tmp_path / 'M31'
    calibrated = root / '01_calibration/01_session/night_calibrated'
    calibrated.mkdir(parents=True)
    source = calibrated / 'pp_001.fits'
    source.write_bytes(b'calibrated')
    stack = root / 'stack_combined.fits'
    stack.write_bytes(b'stack')
    os.utime(source, (100, 100))
    os.utime(calibrated, (100, 100))
    os.utime(stack, (200, 200))
    assert not LightProcessor._should_rebuild_stack(stack, [calibrated])
    ProcessingHtmlReport(root).generate()
    assert calibrated.stat().st_mtime == 100
    assert not LightProcessor._should_rebuild_stack(stack, [calibrated])
    source.unlink()
    os.utime(calibrated, (300, 300))
    assert LightProcessor._should_rebuild_stack(stack, [calibrated])


def test_html_displays_recorded_accepted_chain_comparison(results: Path) -> None:
    """Le tableau rend les valeurs et le choix, en échappant les noms de chaînes."""
    report = results / '04_restoration/restoration.json'
    report.write_text(json.dumps({'summary': {'accepted_ranking': {
        'policy': 'Meilleur score positif', 'formula': 'formule enregistrée',
        'rows': [{'rank': 1, 'label': '<chosen>', 'decision': 'RETENUE',
                  'selection_score': .8, 'gap_to_retained_score': 0,
                  'score_details': {'fwhm_mean_ratio': .9}}],
    }}}))
    ProcessingHtmlReport(results).generate()
    page = (report.parent / 'index.html').read_text()
    assert 'Comparatif des chaînes acceptées' in page
    assert 'Ratio FWHM moyen' in page and 'Écart au retenu' in page
    assert '&lt;chosen&gt;' in page and '<chosen>' not in page
    assert 'selection-kept' in page and '0.80' in page and '0.90' in page


def test_index_extracts_visible_json_summary_parameters_and_quality(results: Path) -> None:
    """Les informations utiles sont présentées avant les détails JSON repliables."""
    report = results / '04_restoration/restoration.json'
    report.write_text(json.dumps({
        'status': 'SUCCESS', 'restoration': {
            'status': 'accepted', 'engine': 'chains', 'selected_chain': ['denoise:siril'],
            'selection_score': .45, 'description': 'Comparaison contrôlée',
            'parameters': {'denoise_modulation': .5},
            'criteria': {
                'noise': {'status': 'PASS', 'value': .7, 'threshold': 1},
                'diffuse': {'status': 'UNKNOWN', 'reason': 'NO_LOCAL_SKY'},
            },
        },
    }))
    ProcessingHtmlReport(results).generate()
    page = (report.parent / 'index.html').read_text()
    summary = page.split('Données JSON détaillées')[0]
    for item in ('Synthèse du rapport', 'Chaîne retenue', 'denoise:siril',
                 'Score de sélection', '0.45', 'denoise_modulation', '0.5',
                 'Résultats des tests qualité', 'PASS', '0.7', 'Seuil',
                 '?', 'NO_LOCAL_SKY', 'Comparaison contrôlée'):
        assert item in summary


def test_existing_json_without_prebuilt_ranking_is_summarized_without_changes(
    results: Path,
) -> None:
    """La commande extrait aussi les anciens audits, sans relancer ni réécrire les calculs."""
    report = results / '04_restoration/restoration.json'
    report.write_text(json.dumps({
        'selected_chain': ['best'], 'selection_score': .8,
        'variants': [
            {'chain': ['other'], 'status': 'accepted', 'selection_score': .3},
            {'chain': ['best'], 'status': 'accepted', 'selection_score': .8,
             'comparison': {'before_fwhm_px': {'mean': 4},
                            'after_fwhm_px': {'mean': 3.6},
                            'paired_ratio': {'median': .85}},
             'artifacts': {'noise': {'proven_gain': .2},
                           'max_ring_fraction': .01, 'max_noise_ratio': .7}},
        ],
    }))
    original = report.read_bytes()
    ProcessingHtmlReport(results).generate()
    page = (report.parent / 'index.html').read_text()
    visible = page.split('Données JSON détaillées')[0]
    assert 'Comparatif des chaînes acceptées' in visible
    assert 'selection-kept' in visible and '0.80' in visible
    assert '0.9' in visible and '0.85' in visible and '0.5' in visible
    assert report.read_bytes() == original


def test_restoration_criteria_are_repeated_for_each_combination() -> None:
    """Relie les contrôles locaux partagés et distingue les audits absents."""
    local = {'operation_id': 7, 'criteria': {
        'local_noise': {'status': 'PASS', 'value': .7, 'threshold': 1}}}
    payload = {'operations': [local], 'variants': [
        {'chain': ['denoise:<siril>'], 'status': 'accepted',
         'criteria': {'final_flux': {'status': 'FAIL', 'value': .2, 'threshold': .1}},
         'steps': [{'operation_id': 7}]},
        {'chain': ['denoise:other'], 'status': 'excluded',
         'steps': [{'operation_id': 7}, {'operation_id': 99}]},
        {'chain': [], 'status': 'reference'},
    ]}
    html = ProcessingHtmlReport._summary_view(payload)
    assert 'Combinaison 1' in html and 'Combinaison 3' in html
    assert 'denoise:&lt;siril&gt;' in html
    assert html.count('local_noise') == 2
    assert 'final_flux' in html and 'FAIL' in html and '0.1' in html
    assert 'Critères finaux non enregistrés' in html
    assert 'Audit local non enregistré' in html


def test_restoration_report_uses_json_and_only_explains_score_before_table(
    results: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le JSON seul fournit le choix, les scores et les critères, même volumineux."""
    directory = results / '04_restoration'
    monkeypatch.setattr(ProcessingHtmlReport, 'JSON_LIMIT', 10)
    payload = {'selected_chain': ['graxpert', 'siril'], 'selection_score': .48,
               'variants': [{'chain': ['graxpert', 'siril'], 'status': 'accepted',
                             'selection_score': .48,
                             'score_details': {'formula': 'gain - penalites'},
                             'criteria': {'stars': {'status': 'PASS', 'value': .99,
                                                    'threshold': .7}}}]}
    (directory / 'restoration.json').write_text(json.dumps(payload))
    (directory / 'restoration_summary.txt').write_text('CONCLUSION ERRONEE')
    ProcessingHtmlReport(results).generate()
    html = (directory / 'index.html').read_text()
    assert 'graxpert → siril' in html
    assert 'gain - penalites' in html
    assert '<td>0.70</td>' in html and 'Satisfait (PASS)' in html
    assert 'CONCLUSION ERRONEE' not in html
    assert 'href="restoration.json"' not in html
    introduction = html.split('Comparatif des chaînes acceptées</h3>', 1)[1].split('<table', 1)[0]
    assert 'gain - penalites' in introduction
    assert 'Classement d’affichage' not in introduction


def test_pages_never_offer_json_downloads(results: Path) -> None:
    """Les rapports alimentent les synthèses sans lien ni données JSON brutes."""
    ProcessingHtmlReport(results).generate()
    for page in results.rglob('index.html'):
        parser = Links()
        parser.feed(page.read_text())
        assert not any(url.endswith('.json') for url in parser.urls)
        assert 'Données JSON détaillées' not in page.read_text()




def test_quality_definitions_are_central_and_linked_only_where_used(results: Path) -> None:
    """Une page commune définit les critères ; les audits pointent vers les ancres."""
    ProcessingHtmlReport(results).generate()
    glossary = results / 'quality-criteria.html'
    assert 'id="diffuse"' in glossary.read_text()
    assert 'id="roundness"' in glossary.read_text()
    assert 'NO_LOCAL_SKY' in glossary.read_text()
    for page in results.rglob('index.html'):
        assert 'Comprendre les critères de qualité' not in page.read_text()
    calibration = (results / '01_calibration/index.html').read_text()
    assert 'quality-criteria.html#' not in calibration
    step = (results / '04_restoration/01_denoise_siril/index.html').read_text()
    assert 'href="../../quality-criteria.html#noise"' in step
    linked = ProcessingHtmlReport._link_quality_terms(
        '<p>diffuse roundness</p><a href="noise.txt">noise</a>', '../')
    assert 'href="../quality-criteria.html#diffuse"' in linked
    assert '<a href="noise.txt">noise</a>' in linked


def test_foreign_quality_guide_is_preserved_before_publication(results: Path) -> None:
    """Le fichier de définitions bénéficie de la protection des index existants."""
    guide = results / 'quality-criteria.html'
    guide.write_text('Guide personnel')
    with pytest.raises(FileExistsError):
        ProcessingHtmlReport(results).generate()
    assert guide.read_text() == 'Guide personnel'
    assert not (results / 'index.html').exists()


def test_registration_displays_recorded_selection_and_profile_decisions(results: Path) -> None:
    """Les bilans globaux et les rejets de profils restent distincts et complets."""
    calibration = results / '01_calibration'
    registration = calibration / '02_registration'
    quality = calibration / '03_quality'
    registration.mkdir()
    quality.mkdir()
    report = {'metadata': {'unique_inputs': 65},
              'quality_selection': {'independent_retained': 61, 'rejected': 4,
                                    'effective_stack_entries': 122},
              'stellar_profiles': {'enabled': True, 'rejected': 1, 'remaining': 61},
              'geometry_checks': [{'sequence': 'stack.seq', 'min_scale': .5, 'max_scale': 2, 'remaining': 63,
                                   'rejected': [{'filename': 'geometry.fits', 'reason': 'inversion'}]}],
              'settings': {'fwhm_filter': '1.8k'},
              'frames': [{'source': 'retained.fits', 'fwhm': 2.5}]}
    (calibration / 'stack.drizzle.json').write_text(json.dumps(report))
    frames = [{'source': 'bad<pose>.fits', 'status': 'rejected', 'reasons': ['r80'],
               'r80': 9, 'limits': {'r80': 8}, 'r80_fraction': .8},
              {'source': 'uncertain.fits', 'status': 'inconclusive'}]
    frames += [{'source': f'kept-{i}.fits', 'status': 'accepted'} for i in range(60)]
    (quality / 'stellar_profiles.json').write_text(json.dumps(
        {'enabled': True, 'frames': frames, 'rejected': 1, 'remaining': 61, 'inconclusive': 1}))
    ProcessingHtmlReport(results).generate()
    text = (registration / 'index.html').read_text()
    assert '<td>65</td><td>1</td><td>64</td>' in text
    assert 'Retenues : 61' not in text
    assert '1.8k' not in text
    combined = (calibration / 'index.html').read_text()
    assert 'Retenues : 61' in combined
    assert 'geometry.fits' in combined and 'inversion' in combined
    assert '../03_quality/index.html' in text
    text = (quality / 'index.html').read_text()
    assert 'Images indépendantes admissibles en entrée : 64' in text
    assert 'Images considérées en entrée du contrôle des profils : 62' in text
    assert 'bad&lt;pose&gt;.fits' in text and '✗' in text
    assert 'selection-rejected' in text
    assert 'NON CONCLUANTE' in text and '?' in text
    assert 'kept-59.fits' in text
    assert 'quality-criteria.html#r80' in text
    assert 'href="stellar_profiles.json"' not in text


def test_registration_without_audit_does_not_invent_rejections(results: Path) -> None:
    """Une registration sans JSON reste explicitement non documentée."""
    directory = results / '01_calibration/02_registration'
    directory.mkdir()
    ProcessingHtmlReport(results).generate()
    assert 'Aucun bilan JSON de sélection' in (directory / 'index.html').read_text()


def test_registration_settings_link_to_specific_definitions(results: Path) -> None:
    """Les six libellés français renvoient à leurs définitions dédiées."""
    settings = {'fwhm_reject_percent': 0, 'max_fwhm': 0,
                'stellar_profile_filter': True, 'stellar_profile_sigma': 3,
                'roundness_weighted': True, 'fwhm_weighted': True}
    registration = results / '01_calibration/02_registration'
    registration.mkdir()
    (registration.parent / '03_quality').mkdir()
    (registration.parent / 'stack.drizzle.json').write_text(json.dumps({
        'quality_selection': {}, 'settings': settings}))
    ProcessingHtmlReport(results).generate()
    page = (registration.parent / '03_quality/index.html').read_text()
    guide = (results / 'quality-criteria.html').read_text()
    for key in settings:
        assert f'href="../../quality-criteria.html#{key}"' in page
        assert f'id="{key}"' in guide
    assert 'La valeur 0 désactive ce plafond' in guide
    assert 'dispersion robuste' in guide
    assert 'ne rejette pas de pose' in guide
    assert 'multiplication' in guide


def test_html_uses_persisted_per_image_decisions(results: Path) -> None:
    """L'index distingue rejet, filtre ignoré, pondération et sélection finale."""
    report = {'quality_selection': {}, 'selection_decisions': [
        {'source': 'bad<pose>.fits', 'status': 'rejected', 'final_status': 'excluded',
         'reasons': ['max_fwhm'], 'checks': [
             {'criterion': 'max_fwhm', 'status': 'FAIL', 'value': 8,
              'threshold': 5, 'rule': 'value <= threshold'},
             {'criterion': 'roundness_filter', 'status': 'SKIPPED'}],
         'profile_status': 'SKIPPED'},
        {'source': 'good.fits', 'status': 'accepted', 'final_status': 'retained',
         'checks': [], 'reasons': [], 'profile_status': 'DISABLED',
         'weighting': {'quality_multiplicity': 4}},
    ]}
    calibration = results / '01_calibration'
    (calibration / 'stack.drizzle.json').write_text(json.dumps(report))
    ProcessingHtmlReport(results).generate()
    page = (calibration / 'index.html').read_text()
    for text in ['bad&lt;pose&gt;.fits', 'NON ÉVALUÉ : EXCLUSION ANTÉRIEURE',
                 'DÉSACTIVÉ', '<td>8</td><td><span class="selection-mark selection-rejected"', 'Seuil ou paramètres communs', 'Multiplicité totale', '<td>4</td>']:
        assert text in page.replace(' class="criteria-group-start"', '')
    assert 'quality-criteria.html#max_fwhm' in page
    assert 'href="stack.drizzle.json"' not in page


def test_shared_profile_limits_precede_frame_tables_for_new_and_old_reports() -> None:
    """Le rendu déduplique les limites anciennes et résout les nouveaux groupes."""
    references = [{'reference_id': 1, 'r80': {'limit': 8.25}, 'elongation': {'limit': 1.5}},
                  {'reference_id': 2, 'r80': {'limit': 12.75}, 'elongation': {'limit': 1.8}}]
    frames = [{'source': 'a.fits', 'reference_id': 1, 'status': 'accepted', 'r80': 4},
              {'source': 'b.fits', 'reference_id': 1, 'status': 'accepted', 'r80': 5},
              {'source': 'c.fits', 'reference_id': 2, 'status': 'accepted', 'r80': 8},
              {'source': 'unknown.fits', 'reference_id': 99, 'status': 'insufficient_reference'}]
    html = ProcessingHtmlReport._stellar_selection_view({'references': references, 'frames': frames})
    assert html.count('8.25') == html.count('12.75') == 1
    assert html.index('8.25') < html.index('a.fits')
    assert '<th>Groupe des seuils</th>' in html
    assert 'Groupe 2' in html and '?' in html
    rows = html.split('</table>', 1)[1]
    assert '8.25' not in rows and '12.75' not in rows
    old = [{'source': f'old-{i}.fits', 'limits': {'r80': 8.25, 'elongation': 1.5},
            'status': 'accepted'} for i in range(3)]
    html = ProcessingHtmlReport._stellar_selection_view({'frames': old})
    assert html.count('8.25') == 1
    assert html.index('8.25') < html.index('old-0.fits')
    decisions = [{'source': f['source'], 'status': 'accepted', 'checks': [], 'profile': f}
                 for f in frames]
    html = ProcessingHtmlReport._decision_trace_view(decisions, references)
    assert html.count('8.25') == 1
    assert '>Groupe des seuils</span></span></th>' in html
    assert '<td>1</td>' in html.replace(' class="criteria-group-start"', '')
    assert 'référence des seuils :' not in html


def test_pose_basename_links_preserve_sessions_and_rebase_moved_results(results: Path) -> None:
    """Les poses homonymes gardent leur session et les liens restent déplaçables."""
    calibration = results / '01_calibration'
    quality = calibration / '03_quality'
    registration = calibration / '02_registration'
    quality.mkdir()
    registration.mkdir()
    filename = 'pose & étoile.fits'
    frames = []
    for session in ['session-A', 'session-B']:
        source = calibration / '01_session' / session / 'calibrated' / filename
        source.parent.mkdir(parents=True)
        source.write_bytes(b'FITS')
        old = '/ancien/objet/01_calibration/01_session/' + session + '/calibrated/' + filename
        frames.append({'source': old, 'status': 'accepted'})
    (quality / 'stellar_profiles.json').write_text(json.dumps({'frames': frames}))
    (quality / 'selection_decisions.json').write_text(json.dumps({'frames': frames, 'complete': True}))
    (calibration / 'stack.drizzle.json').write_text(json.dumps({
        'quality_selection': {}, 'selection_decisions': frames, 'frames': frames}))
    ProcessingHtmlReport(results).generate()
    (registration / 'registration.json').write_text(json.dumps({'frames': frames}))
    ProcessingHtmlReport(results).generate()
    for page in [quality / 'index.html', registration / 'index.html']:
        html = page.read_text()
        assert '/ancien/objet/' not in html
        assert '>pose &amp; étoile.fits</a>' in html
        parser = Links()
        parser.feed(html)
        for session in ['session-A', 'session-B']:
            target = '../01_session/' + session + '/calibrated/pose%20%26%20%C3%A9toile.fits'
            assert target in parser.urls
            assert (page.parent / unquote(target)).is_file()
    assert ProcessingHtmlReport._source_link('/missing/path/absent.fits', quality) == 'absent.fits'


def test_registration_and_quality_have_distinct_stage_counts(results: Path) -> None:
    """La registration affiche sa sortie propre, la qualité ses retraits successifs."""
    calibration = results / '01_calibration'
    registration = calibration / '02_registration'
    quality = calibration / '03_quality'
    registration.mkdir()
    quality.mkdir()
    flow = [
        {'stage': 'registration', 'label': 'Registration Siril', 'input_count': 313,
         'rejected_count': 66, 'remaining_count': 247},
        {'stage': 'quality_filters', 'label': 'Filtres qualité', 'input_count': 247,
         'rejected_count': 75, 'remaining_count': 172,
         'children': [{'stage': 'fwhm_filter', 'label': 'Filtre FWHM', 'input_count': 247,
                       'rejected_count': 26, 'remaining_count': 221}]},
        {'stage': 'stellar_profiles', 'label': 'Profils stellaires', 'input_count': 172,
         'rejected_count': 8, 'remaining_count': 164},
    ]
    (registration / 'registration.json').write_text(json.dumps({
        'input_count': 313, 'aligned_count': 247, 'rejected_count': 66, 'frames': []}))
    (calibration / 'stack.drizzle.json').write_text(json.dumps({
        'metadata': {'unique_inputs': 313}, 'quality_selection': {'independent_retained': 164},
        'selection_flow': flow}))
    ProcessingHtmlReport(results).generate()
    reg = (registration / 'index.html').read_text()
    assert '<td>313</td><td>66</td><td>247</td>' in reg
    assert '<td>164</td>' not in reg
    text = (quality / 'index.html').read_text()
    assert 'admissibles en entrée : 247' in text
    assert '<td>247</td><td>26</td><td>221</td>' in text
    assert '<td>172</td><td>8</td><td>164</td>' in text
    assert 'Ancien rapport' not in text


def test_siril_registration_data_have_definitions_and_no_json_download(results: Path) -> None:
    """Les faits Siril sont liés aux définitions sans doublon ni cause inventée."""
    registration = results/'01_calibration/02_registration'
    stacking = results/'01_calibration/05_stacking'
    registration.mkdir()
    stacking.mkdir()
    source = results/'pose & étoile.fits'
    source.write_bytes(b'FITS')
    api = dict(events=[dict(
        complete=True, command_succeeded=True, sequence='light_', channel=0,
        parameters=dict(transformation='affine', minpairs='12', maxstars=None,
                        two_pass=True, selected_only=False),
        input_count=2, included_count=1, excluded_count=1,
        frames=[dict(source=str(source), included=True, is_reference=True,
                     registration_available=True,
                     measurements=dict(fwhm=2.5, roundness=.91, nbstars=37, pair_matched=0, inliers=0)),
                dict(source=str(source), included=False, registration_available=False,
                     measurements={})],
    )])
    (registration/'registration.json').write_text(json.dumps(dict(
        input_count=2, aligned_count=1, rejected_count=1, frames=[], siril_api=api)))
    (registration/'01_registration.registration.json').write_text(json.dumps(api))
    (stacking/'04_realign.registration.json').write_text(json.dumps(api))
    ProcessingHtmlReport(results).generate()
    for directory in (registration, stacking):
        page = (directory/'index.html').read_text()
        assert page.count('Registration : mesures et décisions par image') == 1
        assert 'INCLUSE' in page and 'EXCLUE' in page
        assert 'Motif détaillé non exposé par Sirilpy' in page
        assert '12 (argument explicite)' in page
        assert 'valeur interne Siril non exposée' in page
        assert 'Image de référence : compteurs' in page
        assert '>pose &amp; étoile.fits</a>' in page
        assert str(source) not in page
        links = Links()
        links.feed(page)
        assert not any(urlsplit(url).path.endswith('.json') for url in links.urls)
        for term in ('pair_matched', 'inliers', 'minpairs', 'maxstars',
                     'registration_transform', 'detection_sigma', 'detection_roundness'):
            assert '../../quality-criteria.html#' + term in links.urls
            guide = (results/'quality-criteria.html').read_text()
            assert 'id="' + term + '"' in guide
    assert 'Registration : mesures et décisions' not in (results/'01_calibration/index.html').read_text()


def test_siril_registration_audit_failure_is_visible_without_fabricated_counts(results: Path) -> None:
    """Une collecte indisponible ou une commande échouée ne ressemble pas à un succès."""
    registration = results/'01_calibration/02_registration'
    registration.mkdir()
    (registration/'failed.registration.json').write_text(json.dumps(dict(events=[dict(
        complete=False, command_succeeded=False, command_error='<échec explicite>',
        error='API inaccessible', parameters={}, frames=[],
    )])))
    ProcessingHtmlReport(results).generate()
    page = (registration/'index.html').read_text()
    assert 'Audit API incomplet : API inaccessible' in page
    assert 'Commande Siril échouée : &lt;échec explicite&gt;' in page
    assert 'Images dans la séquence : 0' not in page


def test_legacy_registration_uses_recorded_measurements_and_common_definitions(results: Path) -> None:
    """Les anciens JSON permettent déjà d'expliquer et d'afficher les mesures connues."""
    registration = results/'01_calibration/02_registration'
    registration.mkdir()
    (registration/'registration.json').write_text(json.dumps(dict(
        input_count=1, aligned_count=0, rejected_count=1,
        frames=[dict(source='pose.fits', status='excluded', reasons=['excluded_by_siril'],
                     measurements=dict(fwhm=2.73, roundness=.81, nbstars=42))],
    )))
    ProcessingHtmlReport(results).generate()
    page = (registration/'index.html').read_text()
    assert '<td>2.73</td><td>0.81</td><td>42</td>' in page
    assert 'Exclusion Siril : motif détaillé non fourni' in page
    assert '../../quality-criteria.html#minpairs' in page
    assert '../../quality-criteria.html#registration_roundness' in page
    assert 'Données détaillées Sirilpy non enregistrées' in page


def test_quality_columns_have_numeric_rows_and_common_thresholds() -> None:
    """Les règles ne sont pas répétées ; les seuils communs précèdent les poses."""
    import copy

    frames = []
    for index in (1, 2):
        frames.append(dict(
            source=f'pose-{index}.fits', status='accepted', final_status='retained',
            measurements={'fwhm': 2.25},
            checks=[
                dict(criterion='registration_validity', status='PASS', rule='TEXTE_COMMUN_INTERDIT',
                     inputs=dict(finite_measurements=True, positive_fwhm=True,
                                 included=True, registration_valid=True)),
                dict(criterion='fwhm_filter', status='PASS', value=index,
                     threshold=10.12345, rule='value <= threshold'),
                dict(criterion='registration_geometry', stage='debayer_', status='PASS',
                     threshold={'min_scale': .5, 'max_scale': 2}, reason='TEXTE_PAR_POSE_INTERDIT'),
                dict(criterion='registration_geometry', stage='r_debayer_', status='SKIPPED',
                     threshold={'min_scale': .5, 'max_scale': 2}),
            ],
            profile=dict(reference_id=1, status='accepted', stars_measured=72, sectors=9,
                         r80=3.25, r80_fraction=0.0, elongation=1.25, elongation_fraction=0),
            weighting=dict(roundness_multiplicity=1, fwhm_multiplicity=2,
                           quality_multiplicity=2, effective_stack_entries=2),
        ))
    original = copy.deepcopy(frames)
    html = ProcessingHtmlReport._decision_trace_view(frames, [dict(reference_id=1,
        r80={'limit': 8.5}, elongation={'limit': 1.5})])
    assert frames == original
    assert html.count('10.12') == 1 and html.index('10.12') < html.index('pose-1.fits')
    assert '10.12345' not in html
    assert '<td>0.00</td>' in html
    assert '<td>72</td>' in html
    assert 'UNKNOWN' not in html
    assert html.index('Seuils communs des profils') < html.index('class="quality-legend"')
    assert html.index('class="quality-legend"') < html.index('class="criteria-scroll"')
    legend = html.split('class="quality-legend"', 1)[1].split('</p>', 1)[0]
    for marker in ('selection-kept', 'selection-skipped', 'selection-rejected'):
        assert marker in legend
    assert html.count('8.5') == 1
    for text in ('TEXTE_COMMUN_INTERDIT', 'TEXTE_PAR_POSE_INTERDIT',
                 'value &lt;= threshold', 'Décisions et mesures', '<ul>'):
        assert text not in html
    assert 'Contrôle géométrique Python — debayer_' in html
    assert 'Contrôle géométrique Python — r_debayer_' in html
    assert '>Validité</span></span></th>' in html
    assert '>Mesures finies</span></span></th>' not in html
    assert '>Transformation valide</span></span></th>' not in html
    assert '>weighted_fwhm</span></span></th>' in html
    assert '>Fraction r80</span></span></th>' in html
    assert 'selection-kept' in html and '>✓</span>' in html
    assert '>RETENUE<' not in html

    class Rows(HTMLParser):
        """Collecte les cellules du corps pour contrôler l'alignement des poses."""

        def __init__(self) -> None:
            """Initialise le suivi du corps et des cellules."""
            super().__init__()
            self.body = False
            self.rows: list[list[str]] = []
            self.cell = False

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            """Suit uniquement le tbody du tableau de critères."""
            if tag == 'tbody':
                self.body = True
            elif tag == 'tr' and self.body:
                self.rows.append([])
            elif tag == 'td' and self.body:
                self.rows[-1].append('')
                self.cell = True

        def handle_endtag(self, tag: str) -> None:
            """Termine la collecte d'une cellule ou du corps."""
            if tag == 'tbody':
                self.body = False
            elif tag == 'td':
                self.cell = False

        def handle_data(self, data: str) -> None:
            """Conserve le texte des valeurs sans interpréter les nombres."""
            if self.body and self.cell:
                self.rows[-1][-1] += data

    table = Rows()
    table.feed(html)
    assert len(table.rows) == 2 and len(table.rows[0]) == len(table.rows[1])
    assert table.rows[0][0] == 'pose-1.fits'
    assert table.rows[0][1:3] == ['✓', '✓']
    assert '-' in table.rows[0]
    assert 'PASS' not in table.rows[0] and 'SKIPPED' not in table.rows[0]
    assert table.rows[0][-4:] == ['1', '2', '2', '2']


def test_quality_columns_keep_varying_limits_ranks_and_missing_checks() -> None:
    """Des seuils différents restent par pose ; zéro et absence restent distincts."""
    frames = [
        dict(source='a.fits', checks=[dict(criterion='fwhm_reject_percent', status='PASS',
                                         value=20, rank=0, threshold={'retained_count': 5})]),
        dict(source='b.fits', checks=[dict(criterion='fwhm_reject_percent', status='FAIL',
                                         value=30, rank=7, threshold={'retained_count': 6})]),
        dict(source='c.fits', checks=[]),
    ]
    html = ProcessingHtmlReport._decision_trace_view(frames)
    assert 'Variables selon la pose' in html
    assert '>Rang</span></span></th>' in html
    assert '<td>20</td><td>0</td><td>5</td><td><span class="selection-mark selection-kept"' in html.replace(' class="criteria-group-start"', '')
    assert '<td>30</td><td>7</td><td>6</td><td><span class="selection-mark selection-rejected"' in html.replace(' class="criteria-group-start"', '')
    third = html[html.index('c.fits'):].replace(' class="criteria-group-start"', '')
    assert '<td>?</td><td>?</td><td>?</td><td>?</td>' in third


def test_quality_headers_link_to_calculation_definitions(results: Path) -> None:
    """Les en-têtes conduisent aux calculs du guide commun, sans règles par ligne."""
    quality = results/'01_calibration/03_quality'
    quality.mkdir()
    names = ('registration_validity', 'fwhm_filter', 'roundness_filter', 'nbstars_filter')
    (quality/'selection_decisions.json').write_text(json.dumps(dict(
        complete=True, frames=[dict(source='pose.fits', checks=[
            dict(criterion=name, value=1, status='PASS', threshold=2,
                 rule='FORMULE_A_NE_PAS_REPETER') for name in names])],
    )))
    ProcessingHtmlReport(results).generate()
    page = (quality/'index.html').read_text()
    guide = (results/'quality-criteria.html').read_text()
    for name in (*names, 'weighted_fwhm'):
        assert f'quality-criteria.html#{name}' in page
        assert f'id="{name}"' in guide
    assert 'transform:rotate(-90deg)' in page
    import re
    header = page.split('<table class="criteria-table">', 1)[1].split('</thead>', 1)[0]
    assert all('criteria-vertical' in tag for tag in re.findall(r'<th\b[^>]*>', header))
    assert 'class="criteria-vertical"' in page
    assert '.criteria-table th,.criteria-table td{border:1px solid' in page
    assert 'width:10rem;min-width:10rem;max-width:10rem' in page
    assert '.selection-kept{color:#4ade80}' in page
    assert '.selection-rejected{color:#f87171}' in page
    assert '.selection-skipped{color:#fff}' in page
    assert 'class="criteria-vertical criteria-group-start"' in page
    assert '<td class="criteria-group-start">' in page
    assert 'FORMULE_A_NE_PAS_REPETER' not in page
    assert 'médiane + k' in guide and 'médiane − k' in guide


def test_registration_validity_is_one_column_using_the_recorded_verdict() -> None:
    """Une donnée absente ne devient pas OK ; aucun verdict n'est recalculé."""
    frames = [dict(source=f'pose-{index}.fits', checks=[dict(
        criterion='registration_validity', status=status, value=2,
        inputs=dict(finite_measurements=True, positive_fwhm=True,
                    included=True, registration_valid=True),
    )]) for index, status in enumerate(('PASS', 'FAIL', None, 'SKIPPED', 'DISABLED'))]
    html = ProcessingHtmlReport._decision_trace_view(frames)
    body = html.split('<tbody>', 1)[1].split('</tbody>', 1)[0].replace(' class="criteria-group-start"', '')
    import re
    rows = re.findall(r'<tr>(.*?)</tr>', body, re.S)
    validity = [re.findall(r'<td>(.*?)</td>', row, re.S)[3] for row in rows]
    assert ['✓' if '>✓</span>' in cell else '✗' if '>✗</span>' in cell
            else '-' if '>-</span>' in cell else cell for cell in validity] == ['✓', '✗', '?', '-', '-']
    assert html.count('>Validité</span></span></th>') == 1
    for title in ('Mesures finies', 'fwhm positive', 'Incluse', 'Transformation valide'):
        assert '>' + title + '</span>' not in html
    guide = ProcessingHtmlReport._quality_guide()
    for explanation in ('ni NaN ni valeur infinie', 'strictement supérieure à zéro',
                        'matrice 3 × 3', 'coche verte', 'croix rouge'):
        assert explanation in guide


def test_restoration_tables_use_vertical_groups_symbols_and_recorded_values() -> None:
    """Les chaînes acceptées non retenues et les contrôles non bloquants restent distincts."""
    import copy
    import re

    payload = dict(selected_chain=['best'], selection_score=1.67891, variants=[
        dict(chain=[], status='reference', selection_score=0.0),
        dict(chain=['other'], status='accepted', selection_score=1.23456,
             criteria={'noise': dict(status='PASS', value=.123456, threshold=1.0)}),
        dict(chain=['best'], status='accepted', selection_score=1.67891,
             criteria={'noise': dict(status='PASS', value=.123456, threshold=1.0),
                       'diffuse': dict(status='FAIL', value=.078912, threshold=.05, blocking=False)}),
        dict(chain=['bad<name>'], status='rejected', selection_score=-.123456,
             criteria={'noise': dict(status='FAIL', value=1.23456, threshold=1.0)}),
    ])
    original = copy.deepcopy(payload)
    html = ProcessingHtmlReport._restoration_view(payload)
    assert payload == original
    assert '1.68' in html and '1.67891' not in html
    assert '0.12' in html and '0.123456' not in html
    assert 'bad&lt;name&gt;' in html and 'bad<name>' not in html
    assert 'Bloquant' in html and 'UNKNOWN' not in html
    assert 'title="Chaîne admissible"' in html
    assert '>OK<' not in html and '>NOK<' not in html
    tables = html.split('<table class="criteria-table presentation-table">')[1:]
    assert len(tables) == 2
    for table in tables:
        header = table.split('</thead>', 1)[0]
        assert all('criteria-vertical' in tag for tag in re.findall(r'<th\b[^>]*>', header))
    main = tables[-1].split('<tbody>', 1)[1].split('</tbody>', 1)[0]
    rows = re.findall(r'<tr>(.*?)</tr>', main, re.S)
    cells = [[re.sub(r'<[^>]+>', '', cell) for cell in re.findall(r'<td\b[^>]*>(.*?)</td>', row, re.S)]
             for row in rows]
    assert len({len(row) for row in cells}) == 1
    assert cells[1][:4] == ['other', '✓', '-', '1.23']
    assert cells[2][:4] == ['best', '✓', '✓', '1.68']
    assert cells[3][1:4] == ['✗', '✗', '-0.12']
    assert cells[2][7:11] == ['0.08', '0.05', '0', '✗']
    assert 'class="quality-legend"' in html.split('<table', 1)[0]


def test_restoration_child_tables_share_style_without_changing_other_stages(results: Path) -> None:
    """Les pages locales sont harmonisées ; les autres étapes gardent leur disposition."""
    payload = dict(status='accepted', parameters={'gain': .123456},
                   criteria={'noise': dict(status='PASS', value=.123456, threshold=1.0),
                             'diffuse': dict(status='UNKNOWN', value=None, threshold=None)})
    stage = results/'04_restoration/02_local'
    stage.mkdir()
    child = stage/'audit.json'
    other = results/'01_calibration/audit.json'
    data = json.dumps(payload)
    child.write_text(data)
    other.write_text(data)
    ProcessingHtmlReport(results).generate()
    local = (stage/'index.html').read_text()
    calibration = (other.parent/'index.html').read_text()
    root_page = (stage.parent/'index.html').read_text()
    assert 'class="criteria-table presentation-table"' in root_page
    assert '>Dossier</span></span></th>' in root_page
    assert '>accepted<' not in root_page
    assert '>✓</span>' in root_page
    assert 'class="criteria-table presentation-table"' in local
    assert 'class="criteria-table presentation-table"' not in calibration
    assert '>0.12<' in local and '>0.123456<' not in local
    assert '>✓</span>' in local and '>UNKNOWN<' not in local
    assert 'quality-criteria.html#noise' in local
    assert 'class="quality-legend"' in local
    assert child.read_text() == other.read_text() == data
