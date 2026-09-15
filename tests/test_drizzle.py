import json
from pathlib import Path
import numpy as np
import pytest
from astropy.io import fits
from lib.drizzle import analyse, coverage, read_registration, run_stack, validate_settings


def metadata(n, cfa=False):
    return dict(native_cfa=cfa, unique_inputs=n, sampling_arcsec_px=1.41)


def records(points, fwhm=1.7):
    return [dict(dx=float(x), dy=float(y), fwhm=fwhm) for x,y in points]


def test_drift_and_guiding_are_not_dithering():
    rng = np.random.default_rng(1)
    points = np.arange(120)[:,None]*np.array([.01,.02])+rng.normal(0,.01,(120,2))
    result = analyse(records(points), {}, metadata(120), {'sufficient': True})
    assert not result['dithering_detected']
    assert not result['enabled']


def test_dither_and_sampling_decisions_are_distinct():
    rng = np.random.default_rng(12)
    points = np.repeat(rng.uniform(-5,5,(100,2)),3,axis=0)
    result = analyse(records(points), {}, metadata(300), {'sufficient': True})
    assert result['dithering_detected'] and result['enabled']
    assert result['frequency_frames'] == 3
    result = analyse(records(points,3.1), {}, metadata(300), {'sufficient': True})
    assert result['dithering_detected'] and not result['enabled']
    assert 'sampling' in result['reasons']


def test_repeated_and_integer_phases_not_independent():
    c = coverage(np.tile([[.999,.999],[.001,.001]],(50,1)))
    assert c['independent_phases'] == 1
    assert coverage(np.arange(100).reshape(50,2))['independent_phases'] == 1


def test_outlier_return_not_dither():
    points = np.zeros((60,2)); points[20] = [40,30]
    assert not analyse(records(points), {}, metadata(60), {'sufficient':True})['dithering_detected']


def test_force_and_off():
    assert analyse([], {'drizzle':'force'}, metadata(0), {'sufficient':False})['enabled']
    assert not analyse([], {'drizzle':'off'}, metadata(0), {'sufficient':False})['enabled']
    with pytest.raises(ValueError):
        validate_settings({'drizzle_scale':'nan'})


def test_siril_registration_parser(tmp_path):
    seq = tmp_path/'x.seq'
    seq.write_text("S 'x' 1 1 1 3 0 6 0 0 0\nI 1 1\nR0 1.7 1.8 .9 1 0 30 H 1 0 2.3 0 1 -1.2 0 0 1\n")
    _, _, rows = read_registration(seq)
    assert rows[0][1][0,2] == 2.3
    seq.write_text("S 'x' 1 1 1 3 0 99\n")
    with pytest.raises(ValueError):
        read_registration(seq)


@pytest.mark.parametrize('mode', ['force', 'auto', 'off'])
def test_pipeline_native_cfa_and_report(tmp_path, mode):
    files=[]
    for i in range(4):
        f=tmp_path/f'input{i}.fits'
        h=fits.Header({'BAYERPAT':'RGGB','FOCALLEN':550,'XPIXSZ':3.76,'DATE-OBS':f'2026-09-15T00:00:0{i}'})
        fits.writeto(f,np.ones((10,10)),h);files.append(f)
    seq=tmp_path/'light_.seq'
    seq.write_text("S 'light_' 1 4 4 3 0 6 0 0 0\n"+''.join(f'I {i+1} 1\n' for i in range(4))+''.join(f'R0 1.7 1.8 .9 1 0 30 H 1 0 {i} 0 1 {i} 0 0 1\n' for i in range(4)))
    class FakeSiril:
        scripts=[]
        def run_siril_script(self, script, *args, **kwargs):
            self.scripts.append(script)
            if '\nstack ' in script:
                (tmp_path/'final.fit').touch()
            return True
    siril=FakeSiril()
    assert run_stack(siril, files, {'drizzle':mode}, tmp_path,tmp_path,'light_',tmp_path/'final','requires 1.2','stack r_r_light_ rej 3 3 -out=final','min')
    script=siril.scripts[-1]
    report=json.loads((tmp_path/'final.drizzle.json').read_text())
    assert report['status']=='completed'
    assert len(report['frames'])==4
    if mode=='force':
        assert '-drizzle -scale=2.0' in script
        assert '-debayer' not in script
        assert '\nregister r_' not in script
    else:
        assert 'calibrate light_ -debayer -prefix=debayer_' in script
        assert '-drizzle' not in script


def test_resources_and_cfa_have_separate_gates():
    rng = np.random.default_rng(5)
    points = np.repeat(rng.uniform(-5,5,(20,2)), 2, axis=0)
    result = analyse(records(points), {}, metadata(40,True), {'sufficient':False})
    assert not result['enabled']
    assert 'resources' in result['reasons'] and 'cfa' in result['reasons']


def test_cache_requires_completed_matching_settings(tmp_path):
    from lib.drizzle import cache_matches
    output=tmp_path/'result.fits'
    report=tmp_path/'result.drizzle.json'
    assert not cache_matches(output, {'drizzle':'auto'})
    report.write_text(json.dumps({'quality_pipeline_version':2,'status':'completed','settings':{'drizzle':'auto'}}))
    assert cache_matches(output, {'drizzle':'auto'})
    assert not cache_matches(output, {'drizzle':'off'})
    report.write_text(json.dumps({'status':'failed','settings':{'drizzle':'auto'}}))
    assert not cache_matches(output, {'drizzle':'auto'})


def test_selection_rejects_bad_fwhm_and_roundness():
    from lib.drizzle import quality_mask
    rows=[([1.7, 1.8, .9, 1, 0, 30], np.eye(3)) for _ in range(10)]
    rows += [([4.,5.,.4,1,0,30],np.eye(3))]
    assert quality_mask(rows, {'fwhm_filter':'1.8k','roundness_filter':'1.8k'}).tolist() == [True]*10+[False]


def test_session_boundaries_are_not_dithers():
    data=[]
    for i in range(5):
        data += [dict(dx=i*20, dy=i*10, fwhm=1.7, source=f'/night{i}/frame{j}.fits') for j in range(20)]
    assert not analyse(data, {}, metadata(100), {'sufficient':True})['dithering_detected']


@pytest.mark.parametrize('mode', ['off', 'auto', 'force'])
@pytest.mark.parametrize('weighted', [False, True])
def test_all_quality_controls_precede_drizzle(tmp_path, mode, weighted):
    files=[]
    for i in range(6):
        f=tmp_path/f'light_{i+1:03d}.fits'
        fits.writeto(f,np.ones((10,10), dtype=np.float32))
        files.append(f)
    # One elongated exposure, one star-poor exposure, four good exposures.
    rounds=[.3,.8,.6,.7,.8,.9]
    stars=[30,2,30,30,30,30]
    seq=tmp_path/'light_.seq'
    seq.write_text("S 'light_' 1 6 6 3 2 6 0 0 0\nL 1\n"+
                   ''.join(f'I {i+1} 1\n' for i in range(6))+
                   ''.join(f'R0 1.7 1.8 {r} 1 0 {n} H 1 0 {i*.2} 0 1 0 0 0 1\n' for i,(r,n) in enumerate(zip(rounds,stars))))
    cfg={'drizzle':mode,'roundness_filter':'.5','nbstars_filter':'10',
         'roundness_weighted':weighted,'robust_realign':False}
    class FakeSiril:
        def run_siril_script(self, script, *args, **kwargs):
            if '\nstack ' in script:
                _, images, reg = read_registration(seq)
                assert images[0][2] == images[1][2] == '0'
                assert len(images)==len(reg)==(8 if weighted else 6)
                assert '-filter-included' in script
                report=json.loads((tmp_path/'final.drizzle.json').read_text())
                assert report['analysis']['images_retained']==4
                assert report['quality_selection']['effective_stack_entries']==(6 if weighted else 4)
                assert len(report['frames'])==4
                assert all(r['roundness']>=.5 and r['nbstars']>=10 for r in report['frames'])
                (tmp_path/'final.fit').touch()
            return True
    assert run_stack(FakeSiril(), files, cfg, tmp_path,tmp_path,'light_',tmp_path/'final',
                     'requires 1.2', 'stack r_light_ rej 3 3 -out=final', 'min')


def test_nbstars_filters_absolute_percent_and_mad():
    from lib.drizzle import quality_mask
    rows=[([1.7,1.8,.9,1,0,count],np.eye(3)) for count in [2,28,29,30,31,32]]
    for value in ['10','80%','1.8k']:
        selected=quality_mask(rows,{'nbstars_filter':value})
        assert not selected[0] and selected[-1]


def test_old_quality_cache_is_invalidated(tmp_path):
    from lib.drizzle import cache_matches
    (tmp_path/'result.drizzle.json').write_text(json.dumps({'status':'completed','settings':{}}))
    assert not cache_matches(tmp_path/'result.fits', {})
