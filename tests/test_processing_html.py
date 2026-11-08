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
    assert 'RETENUE' in page and '0.8' in page and '0.9' in page


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
                 'UNKNOWN', 'NO_LOCAL_SKY', 'Comparaison contrôlée'):
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
    assert 'RETENUE' in visible and '0.8' in visible
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
    assert 'seuil=0.7' in html and 'PASS' in html
    assert 'CONCLUSION ERRONEE' not in html
    assert 'href="restoration.json"' not in html
    introduction = html.split('Comparatif des chaînes acceptées</h3>', 1)[1].split('<table>', 1)[0]
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
    assert 'bad&lt;pose&gt;.fits' in text and 'ÉCARTÉE' in text
    assert 'NON CONCLUANTE' in text and 'UNKNOWN' in text
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
                 'DÉSACTIVÉ', 'mesure : 8', 'seuil : 5', 'quality_multiplicity : 4']:
        assert text in page
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
    assert 'Groupe 2' in html and 'UNKNOWN' in html
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
    assert 'référence des seuils : Groupe 1' in html


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
