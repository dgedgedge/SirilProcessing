"""Audit PSF et comparaison appariée : aucune moyenne de populations différentes."""
import argparse
import csv
import json
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest
from astropy.io import fits

from lib.deconvolution import compare_star_catalogs, post_process, read_star_catalog
from lib.processor import processor
from lib.postprocess import DeconvolutionProcessor, PhotometricColorCalibrator, _PostProcessorSequence
from lib.siril_utils import add_siril_arguments


def catalog(path, stars, layer=0, profile="Gaussian"):
    with path.open('w', newline='') as stream:
        stream.write('# exported by Siril\n')
        writer = csv.writer(stream, delimiter='\t')
        writer.writerow(['# star#', 'layer', 'X', 'Y', 'FWHMx [px]', 'FWHMy [px]', 'Sat', 'Profile', 'A', 'beta'])
        for identifier, x, width, *saturated in stars:
            writer.writerow([identifier, layer, x, 20, width, width, saturated[0] if saturated else 0, profile, .2, 2.5])
    return path


def test_comparison_uses_exact_same_cohort_despite_reordering_and_new_stars(tmp_path):
    before = catalog(tmp_path/'before.tsv', [(1, 10, 4), (2, 30, 6), (3, 50, 8), (4, 70, 20)])
    after = catalog(tmp_path/'after.tsv', [(91, 50.2, 4), (92, 10.1, 2), (93, 30.1, 3), (94, 100, 0.5)])
    result = compare_star_catalogs(before, after, 1, 3, 0.7, 0)
    assert result['status'] == 'validated'
    assert result['matched_count'] == 3
    assert result['before_fwhm_px']['median'] == 6  # Exclut l'étoile 4 absente après.
    assert result['after_fwhm_px']['median'] == 3  # Exclut la nouvelle étoile 94.
    assert result['before_fwhm_px']['count'] == result['after_fwhm_px']['count'] == 3
    assert result['median_paired_reduction_percent'] == 50
    assert [(p['reference_id'], p['after_id']) for p in result['pairs']] == [(1, 92), (2, 93), (3, 91)]
    assert result['unmatched_before_ids'] == [4]
    assert result['unmatched_after_ids'] == [94]


@pytest.mark.parametrize('before_rows,after_rows', [
    ([(1, 10, 4)], [(2, 10.1, 2), (3, 10.2, 2)]),
    ([(1, 10, 4), (2, 10.2, 4)], [(3, 10.1, 2)]),
])
def test_ambiguous_matches_are_excluded(tmp_path, before_rows, after_rows):
    result = compare_star_catalogs(catalog(tmp_path/'b', before_rows), catalog(tmp_path/'a', after_rows), 1, 3, .7, 0)
    assert result['matched_count'] == 0
    assert result['status'] == 'insufficient_matches'
    assert 'before_fwhm_px' not in result


def test_saturation_shift_and_wrong_layer(tmp_path):
    before = catalog(tmp_path/'b', [(1, 10, 4), (2, 30, 4, 1), (3, 50, float('nan'))])
    after = catalog(tmp_path/'a', [(1, 12, 2), (2, 30, 2)])
    result = compare_star_catalogs(before, after, 1, 3, .7, 0)
    assert result['matched_count'] == 0
    assert len(result['rejected_before']) == 2
    catalog(after, [(1, 10, 2)], layer=1)
    with pytest.raises(ValueError, match='canal'):
        compare_star_catalogs(before, after, 1, 3, .7, 0)


def test_minimum_fraction_and_empty_catalog(tmp_path):
    before = catalog(tmp_path/'b', [(i, 10*i, 4) for i in range(1, 11)])
    after = catalog(tmp_path/'a', [(i, 10*i, 3) for i in range(1, 4)])
    result = compare_star_catalogs(before, after, 1, 3, .7, 0)
    assert result['matched_count'] == 3
    assert result['status'] == 'insufficient_matches'
    assert 'paired_ratio' not in result
    catalog(after, [])
    assert compare_star_catalogs(before, after, 1, 3, .7, 0)['matched_count'] == 0


def test_malformed_catalog_fails(tmp_path):
    path = tmp_path/'bad.tsv'
    path.write_text('X Y FWHM\n1 2 3\n')
    with pytest.raises(ValueError, match='En-tête'):
        read_star_catalog(path)


def arguments(*options):
    parser = argparse.ArgumentParser()
    add_siril_arguments(parser)
    _PostProcessorSequence().add_arguments(parser)
    return parser.parse_args(options)


@pytest.fixture
def simulated_siril(tmp_path, monkeypatch):
    import shlex
    image = tmp_path/'image.fit'
    fits.writeto(image, np.ones((3, 100, 100), dtype='float32'))
    runner = Mock()
    def run(script, working_dir, script_name):
        work = Path(working_dir)
        loaded = image
        profile = 'Gaussian'
        for line in script.splitlines():
            words = shlex.split(line)
            if not words:
                continue
            if words[0] == 'load':
                loaded = Path(words[1])
                if not loaded.is_absolute():
                    loaded = work/loaded
            if words[0] == 'setfindstar' and '-moffat' in words:
                profile = 'Moffat'
            if words[0] == 'makepsf' and words[1] == 'save':
                yy, xx = np.indices((25, 25))
                fits.writeto(work/words[2], np.exp(-((xx-12)**2+(yy-12)**2)/(2*1.6**2)), overwrite=True)
            if words[0] == 'save':
                fits.writeto(work/words[1], fits.getdata(loaded)*.9, overwrite=True)
            if words[0] == 'findstar':
                output = next(w.split('=', 1)[1] for w in words if w.startswith('-out='))
                layer = int(next(w.split('=')[1] for w in words if w.startswith('-layer=')))
                width = 3.5 if ('trial' in output or 'step_' in output) else 4
                catalog(work/output, [(i, 10*i, width) for i in range(1, 11)], layer=layer, profile=profile)
        return True
    runner.run_siril_script.side_effect = run
    factory = Mock(return_value=runner)
    monkeypatch.setattr('lib.siril_utils.Siril', factory)
    def select(stars, source, sheet, **kwargs):
        fits.writeto(sheet, np.ones((100, 100), dtype='float32'))
        return dict(stars=stars, selected_count=len(stars), sheet_path=str(sheet))
    monkeypatch.setattr('lib.deconvolution.select_psf_stars', select)
    monkeypatch.setattr('lib.deconvolution.artifact_metrics', lambda *a: dict(max_noise_ratio=1, max_ring_fraction=0))
    return image, runner, factory


def test_process_saves_psf_preview_audit_and_next_input(tmp_path, simulated_siril):
    image, runner, factory = simulated_siril
    source_bytes = image.read_bytes()
    class Next(processor):
        def add_arguments(self, parser):
            pass
        def post_process(self, input_path, output_path, args=None):
            return {'seen': str(input_path)}
    sequence = _PostProcessorSequence([DeconvolutionProcessor(), Next()])
    result = sequence.post_process(image, tmp_path/'report.json', arguments(
        '--enable-deconvolution', '--deconvolution-iterations', '15', '-m', 'native', '-s', '/test/siril'))
    deconv = result['deconvolution']
    factory.assert_called_once_with(siril_path='/test/siril', siril_mode='native')
    scripts = '\n'.join(call.args[0] for call in runner.run_siril_script.call_args_list)
    assert 'findstar -layer=1 -out=01_deconvolution_before_stars.tsv' in scripts
    assert 'makepsf save 01_deconvolution_blind_psf.fit' in scripts
    assert 'makepsf blind -l0 -ks=15' in scripts
    assert '-iters=15 -gdstep=0.0003000 -tv -alpha=3000' in scripts
    assert 'save 01_deconvolution_simple_trial.fit\nclose\nload 01_deconvolution_simple_trial.fit' in scripts
    for key in ('psf_path', 'psf_preview_path', 'matched_stars_path', 'output_image'):
        assert Path(deconv[key]).is_file()
    assert deconv['comparison']['matched_count'] == 10
    assert result['next']['seen'] == deconv['output_image']
    assert image.read_bytes() == source_bytes


def test_insufficient_matches_keeps_audit_without_publishing_image(tmp_path, simulated_siril):
    image, runner, _ = simulated_siril
    real_run = runner.run_siril_script.side_effect
    def fewer(*args, **kwargs):
        real_run(*args, **kwargs)
        import re
        for name in re.findall(r'-out=(\S+)', args[0]):
            if 'trial' in name or 'step_' in name:
                catalog(Path(args[1])/name, [(1, 10, 3)], layer=1)
        return True
    runner.run_siril_script.side_effect = fewer
    destination = tmp_path/'existing.fit'
    destination.write_bytes(b'old result')
    report = tmp_path/'deconvolution.json'
    with pytest.raises(RuntimeError, match='Aucune déconvolution efficace'):
        post_process(image, report, arguments('--deconvolution-output', str(destination)))
    audit = json.loads(report.read_text())
    assert audit['status'] == 'no_safe_improvement'
    assert audit['attempts'][0]['comparison']['status'] == 'insufficient_matches'
    assert Path(audit['psf_selections'][0]['psf_preview_path']).is_file()
    assert 'output_image' not in audit
    assert destination.read_bytes() == b'old result'


@pytest.mark.parametrize('options', [
    ['--deconvolution-iterations', '0'], ['--deconvolution-psf-size', '24'],
    ['--deconvolution-min-stars', '2'], ['--deconvolution-match-radius', 'nan'],
    ['--deconvolution-min-match-fraction', '1.5'],
])
def test_invalid_parameters(tmp_path, simulated_siril, options):
    image, _, factory = simulated_siril
    with pytest.raises(ValueError):
        post_process(image, tmp_path/'report.json', arguments(*options))
    factory.assert_not_called()


def test_order_and_default_activation():
    sequence = _PostProcessorSequence()
    assert isinstance(sequence.processors[-3], PhotometricColorCalibrator)
    assert isinstance(sequence.processors[-1], DeconvolutionProcessor)
    assert sequence.processors[-2].get_prefix() == 'denoise'
    assert arguments().enable_denoise is True
    assert arguments().enable_deconvolution is True
    assert arguments().enable_photometry is True
    assert all(processor.enabled_by_default for processor in sequence.processors)
    assert arguments('--disable-deconvolution').enable_deconvolution is False
    assert arguments('--disable-photometry').enable_photometry is False


def test_fallback_stops_at_artifacts_and_reuses_previous_step(tmp_path, simulated_siril, monkeypatch):
    image, runner, _ = simulated_siril
    verdicts = iter([
        dict(accepted=False, artifact_failure=False, reasons=['insufficient_sharpening']),
        dict(accepted=True, artifact_failure=False, reasons=[]),
        dict(accepted=False, artifact_failure=True, reasons=['stellar_rings']),
        dict(accepted=True, artifact_failure=False, reasons=[]),
    ])
    monkeypatch.setattr('lib.deconvolution.assess_quality', lambda *a, **k: next(verdicts))
    result = post_process(image, tmp_path/'report.json', arguments('--deconvolution-adaptive', '--deconvolution-psf-method', 'stars'))
    assert result['selected_step'] == .0004
    assert result['search_stopped_at_step'] == .0005
    attempts = result['attempts']
    assert [a['label'] for a in attempts] == ['simple_trial', 'central_step_04', 'central_step_05', 'full_trial']
    assert attempts[1]['source_path'] == attempts[2]['source_path'] == result['crop']['path']
    assert attempts[0]['source_path'] == attempts[-1]['source_path'] == result['original_image_path']
    assert fits.getdata(result['crop']['path']).shape == (3, 33, 33)
    assert attempts[1]['psf_path'] == attempts[-1]['psf_path']
    assert result['psf_selections'][1]['profile'] == 'Moffat'
    scripts = '\n'.join(call.args[0] for call in runner.run_siril_script.call_args_list)
    assert '-minA=0.1 -maxA=0.7' in scripts


def test_full_frame_must_pass_after_crop_success(tmp_path, simulated_siril, monkeypatch):
    image, _, _ = simulated_siril
    verdicts = iter([
        dict(accepted=False, artifact_failure=False, reasons=['insufficient_sharpening']),
        dict(accepted=True, artifact_failure=False, reasons=[]),
        dict(accepted=False, artifact_failure=True, reasons=['noise_amplification']),
    ])
    monkeypatch.setattr('lib.deconvolution.assess_quality', lambda *a, **k: next(verdicts))
    destination = tmp_path/'existing.fit'
    destination.write_bytes(b'previous')
    with pytest.raises(RuntimeError, match='Aucune déconvolution efficace'):
        post_process(image, tmp_path/'report.json', arguments('--deconvolution-adaptive', '--deconvolution-max-step', '.0004', '--deconvolution-output', str(destination)))
    assert destination.read_bytes() == b'previous'


def test_default_does_not_escalate_an_ineffective_trial(tmp_path, simulated_siril, monkeypatch):
    image, runner, _ = simulated_siril
    monkeypatch.setattr('lib.deconvolution.assess_quality', lambda *a, **k:
                        dict(accepted=False, artifact_failure=False, reasons=['insufficient_sharpening']))
    with pytest.raises(RuntimeError, match='Aucune déconvolution efficace'):
        post_process(image, tmp_path/'report.json', arguments())
    report = json.loads((tmp_path/'report.json').read_text())
    assert len(report['attempts']) == 1
    assert 'crop' not in report
    assert report['regularization'] == 'TV'
    assert report['psf_method'] == 'blind'
