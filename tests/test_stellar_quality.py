from copy import deepcopy
import json

import numpy as np
import pytest
from astropy.io import fits

from lib.quality_filter import classify_stellar_profiles, filter_stellar_profiles
from lib.siril_sequence import RegistrationData, SequenceImage, SirilSequence
from lib.stellar_quality import measure_stellar_profiles, measure_stamp


def write_field(path, kind='good', cfa=False):
    rng = np.random.default_rng(42)
    data = rng.normal(100, 2, (640, 640))
    yy, xx = np.mgrid[-35:36, -35:36]
    for n, (y, x) in enumerate((y, x) for y in range(65, 630, 100) for x in range(65, 630, 100)):
        dx, dy = rng.uniform(-.4, .4, 2)
        sx, sy = (4, 4) if kind == 'blur' else (2, 2)
        if kind == 'trail':
            sx = 4
        star = np.exp(-((xx-dx)**2/sx**2 + (yy-dy)**2/sy**2)/2)
        if kind == 'double' or (kind == 'binary' and n == 10):
            star = .65*star + .35*np.exp(-((xx-dx-7)**2 + (yy-dy-2)**2)/8)
        data[y-35:y+36, x-35:x+36] += 1000*star
    if cfa:
        data[::2, ::2] *= 1.4
        data[1::2, 1::2] *= .6
    fits.writeto(path, data.astype('float32'), fits.Header({'BAYERPAT': 'RGGB'} if cfa else {}))
    image = SequenceImage(1, True, path)
    image.registrations['R0'] = RegistrationData(5, 5, .9, 0, .01, 200, np.eye(3))
    return image


@pytest.mark.parametrize('cfa', [False, True])
@pytest.mark.parametrize('kind,expected', [('good', False), ('binary', False), ('blur', True),
                                           ('trail', True), ('double', True)])
def test_profiles_detect_global_defects_without_star_count_gate(tmp_path, cfa, kind, expected):
    good = measure_stellar_profiles(write_field(tmp_path/'good.fits', cfa=cfa), 5)
    candidate = measure_stellar_profiles(write_field(tmp_path/'candidate.fits', kind, cfa), 5)
    assert good['stars_measured'] >= 30
    assert candidate['status'] == 'measured'
    frames = [deepcopy(good) for _ in range(10)] + [candidate]
    classify_stellar_profiles(frames, {})
    assert all(frame['status'] == 'accepted' for frame in frames[:-1])
    assert (candidate['status'] == 'rejected') == expected
    if kind in ('double', 'trail'):
        assert 'elongation' in candidate['reasons']
    if kind == 'blur':
        assert candidate['reasons'] == ['r80']


def test_r80_matches_gaussian_and_ignores_sky_gradient():
    yy, xx = np.mgrid[-21:22, -21:22]
    star = 1000*np.exp(-(xx**2+yy**2)/8)
    base = measure_stamp(star+100, 5)
    slope = measure_stamp(star+100+xx*.5-yy*.8, 5)
    assert base['r80'] == pytest.approx(2*np.sqrt(-2*np.log(.2)), abs=.08)
    assert slope['r80'] == pytest.approx(base['r80'], abs=.01)
    assert slope['elongation'] == pytest.approx(1, abs=.005)


def test_low_signal_and_saturation_are_inconclusive(tmp_path):
    for name, data in [('noise', np.random.default_rng(1).normal(100, 2, (200, 200))),
                       ('saturated', np.ones((200, 200))), ('invalid', np.full((200, 200), np.nan))]:
        path = tmp_path/f'{name}.fits'
        fits.writeto(path, data.astype('float32'))
        report = measure_stellar_profiles(SequenceImage(1, True, path), 5)
        assert report['status'] == 'insufficient_stars'
        assert report['stars_measured'] == 0


def measured_frame(r80=4, elongation=1.1, sectors=3):
    return dict(status='measured', group=[1000, 1000, None, None, 1, 1, 3.76, 555., None],
                r80=r80, elongation=elongation, e1=.5, e2=0,
                stars=[dict(r80=r80, elongation=elongation, e1=.5, e2=0, sector=n%sectors)
                       for n in range(30)])


def test_spatial_consensus_and_direction_prevent_isolated_rejections():
    baseline = [measured_frame() for _ in range(10)]
    localized = measured_frame(r80=10, sectors=1)
    directions = measured_frame(elongation=2)
    for n, star in enumerate(directions['stars']):
        star['e1'], star['e2'] = np.cos(n*2*np.pi/30), np.sin(n*2*np.pi/30)
    classify_stellar_profiles(baseline+[localized, directions], {})
    assert localized['status'] == directions['status'] == 'accepted'


def test_reference_accepts_plate_solved_focal_variations_but_separates_sampling():
    frames = [measured_frame() for _ in range(10)]
    for n, frame in enumerate(frames):
        frame['group'][7] += n*.02
    other = measured_frame(10)
    other['group'][7] = 1100
    references = classify_stellar_profiles(frames+[other], {})
    assert len(references) == 1 and references[0]['frames'] == 10
    assert other['status'] == 'insufficient_reference'


@pytest.mark.parametrize('sigma', [0, -1, float('nan'), float('inf')])
def test_invalid_profile_sigma_is_rejected(sigma):
    with pytest.raises(ValueError, match='stellar_profile_sigma'):
        classify_stellar_profiles([], {'stellar_profile_sigma': sigma})


def test_disabled_profiles_do_not_read_pixels(tmp_path, monkeypatch):
    def fail(*args):
        raise AssertionError('No pixel reading when disabled')
    monkeypatch.setattr('lib.stellar_quality.measure_stellar_profiles', fail)
    mask = np.array([True])
    report = filter_stellar_profiles([SequenceImage(1, True, tmp_path/'missing.fits')], mask,
                                    {'stellar_profile_filter': False})
    assert mask[0] and not report['enabled']


def test_profiles_read_bound_native_file_and_skip_previous_rejections(tmp_path):
    native = write_field(tmp_path/'native.fits')
    converted = tmp_path/'converted.fits'
    fits.writeto(converted, np.ones((10, 10), dtype='float32'))
    image = SequenceImage(1, True, converted, source_path=native.filename,
                          registrations=native.registrations)
    missing = SequenceImage(2, False, tmp_path/'missing.fits')
    mask = np.array([True, False])
    report = filter_stellar_profiles([image, missing], mask, {})
    assert len(report['frames']) == 1
    frame = report['frames'][0]
    assert frame['stars_measured'] >= 30
    assert frame['source'] == str(native.filename)
    assert frame['status'] == 'insufficient_reference'
    assert mask.tolist() == [True, False]


def test_stellar_filter_defaults_are_enabled():
    from lib.config import Config
    assert Config.DEFAULTS['stellar_profile_filter'] is True
    assert Config.DEFAULTS['stellar_profile_sigma'] == 3


def test_profile_read_errors_preserve_selection_and_are_reported(tmp_path, caplog):
    image = SequenceImage(1, True, tmp_path/'missing.fits')
    image.registrations['R0'] = RegistrationData(5, 5, .9, 0, .01, 200, np.eye(3))
    mask = np.array([True])
    report = filter_stellar_profiles([image], mask, {})
    assert mask[0] and report['inconclusive'] == 1
    assert report['frames'][0]['status'] == 'error'
    assert 'non mesurés' in caplog.text


@pytest.mark.parametrize('mode', ['off', 'auto', 'force'])
def test_pixel_filter_excludes_defects_before_stacking(tmp_path, mode, caplog):
    from lib.drizzle import run_stack

    files = []
    for n in range(12):
        path = tmp_path/f'light_{n+1:03d}.fits'
        write_field(path, ['blur', 'double'][n] if n < 2 else 'good')
        files.append(path)
    seq = SirilSequence.from_files(tmp_path/'light_.seq', 'light_', files)
    for image in seq.images:
        image.registrations['R0'] = RegistrationData(5, 5, .9, 0, .01, 200, np.eye(3))
    seq.write()
    original = seq.path.read_bytes()

    class FakeSiril:
        def run_siril_script(self, script, directory, **kwargs):
            if '\nstack ' in script:
                selected = SirilSequence.read(tmp_path/'04_stacking/light_.seq')
                assert [i.included for i in selected.images] == [False, False]+[True]*10
                (tmp_path/'final.fit').touch()
            return True

    with caplog.at_level('INFO'):
        assert run_stack(FakeSiril(), files, {'drizzle': mode, 'robust_realign': False},
                         tmp_path, tmp_path, 'light_', tmp_path/'final', 'requires 1.2',
                         'stack r_light_ rej 3 3 -out=final', 'min')
    assert seq.path.read_bytes() == original
    report = json.loads((tmp_path/'02_quality/stellar_profiles.json').read_text())
    assert report['rejected'] == 2 and report['remaining'] == 10
    assert report['frames'][1]['reasons'] == ['r80', 'elongation']
    assert 'étalement R80 : 2 image(s) retirée(s)' in caplog.text
    assert 'allongement cohérent : 0 image(s) retirée(s)' in caplog.text
    final = json.loads((tmp_path/'final.drizzle.json').read_text())
    assert final['stellar_profiles']['rejected'] == 2
    assert final['quality_selection']['independent_retained'] == 10
