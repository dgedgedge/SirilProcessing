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
@pytest.mark.parametrize('bad_alignment', [False, True])
def test_pipeline_native_cfa_and_report(tmp_path, mode, bad_alignment, simulated_registered_sequence):
    files=[]
    for i in range(4):
        f=tmp_path/f'input{i}.fits'
        h=fits.Header({'BAYERPAT':'RGGB','FOCALLEN':550,'XPIXSZ':3.76,'DATE-OBS':f'2026-09-15T00:00:0{i}'})
        fits.writeto(f,np.ones((10,10)),h);files.append(f)
        (tmp_path/f'light_{i+1:03d}.fits').symlink_to(f)
    seq=tmp_path/'light_.seq'
    seq.write_text("S 'light_' 1 4 4 3 0 6 0 0 0\n"+''.join(f'I {i+1} 1\n' for i in range(4))+''.join(f'R0 1.7 1.8 .9 1 0 30 H 1 0 {i} 0 1 {i} 0 0 1\n' for i in range(4)))
    class FakeSiril:
        scripts=[]
        def run_siril_script(self, script, *args, **kwargs):
            self.scripts.append(script)
            directory = Path(args[0])
            if kwargs.get('script_name') == '04_debayer_registration.sps':
                overrides = {2: [[.951232,.0476758,114.759],[-.698402,-.0725698,2288.6],[0,0,1]]} if bad_alignment else None
                simulated_registered_sequence(directory, 'light_', 'debayer_light_', overrides)
            if kwargs.get('script_name') == '04_realign.sps':
                from lib.siril_sequence import SirilSequence
                checked = SirilSequence.read(directory/'debayer_light_.seq')
                assert checked.images[1].included == (not bad_alignment)
                assert '-filter-included' in script
                simulated_registered_sequence(directory, 'debayer_light_', 'r_debayer_light_')
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
        assert any('calibrate light_ -debayer -prefix=debayer_' in part for part in siril.scripts)
        assert report['final_selection']['independent_retained'] == (3 if bad_alignment else 4)
        assert any(check['rejected'] for check in report['geometry_checks']) == bad_alignment
        assert '-drizzle' not in script


def test_invalid_debayer_alignment_stops_before_any_resampling(tmp_path, simulated_registered_sequence):
    from lib.siril_sequence import SirilSequence, RegistrationData
    files = [tmp_path/f'light_{i:03d}.fits' for i in range(1,4)]
    for path in files:
        fits.writeto(path, np.ones((20,20)), fits.Header({'BAYERPAT':'RGGB'}))
    sequence = SirilSequence.from_files(tmp_path/'light_.seq', 'light_', files)
    for image in sequence.images:
        image.registrations['R0'] = RegistrationData(2,2,.9,0,.01,200,np.eye(3))
    sequence.write()
    scripts = []
    class FakeSiril:
        def run_siril_script(self, script, working_dir, script_name=None):
            scripts.append(script)
            if script_name == '04_debayer_registration.sps':
                mirrored = [[-1,0,19],[0,1,0],[0,0,1]]
                simulated_registered_sequence(working_dir, 'light_', 'debayer_light_',
                                              {i: mirrored for i in range(1,4)})
            return True
    assert not run_stack(FakeSiril(), files, {'drizzle':'off'}, tmp_path, tmp_path, 'light_',
                         tmp_path/'final', 'requires 1.2', 'stack r_light_ rej 3 3 -out=final', 'min')
    assert not any('seqapplyreg' in script or '\nstack ' in script for script in scripts)
    assert json.loads((tmp_path/'final.drizzle.json').read_text())['status'] == 'failed'


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
    report.write_text(json.dumps({'quality_pipeline_version':10,'status':'completed','settings':{'drizzle':'auto'}}))
    assert cache_matches(output, {'drizzle':'auto'})
    assert not cache_matches(output, {'drizzle':'off'})
    report.write_text(json.dumps({'status':'failed','settings':{'drizzle':'auto'}}))
    assert not cache_matches(output, {'drizzle':'auto'})



def test_session_boundaries_are_not_dithers():
    data=[]
    for i in range(5):
        data += [dict(dx=i*20, dy=i*10, fwhm=1.7, source=f'/night{i}/frame{j}.fits') for j in range(20)]
    assert not analyse(data, {}, metadata(100), {'sufficient':True})['dithering_detected']


@pytest.mark.parametrize('mode', ['off', 'auto', 'force'])
@pytest.mark.parametrize('weighted', [False, True])
@pytest.mark.parametrize('abnormal_stars,raw_fwhm', [(2, 1.7), (60, 1.7), (30, 6.40424)])
def test_all_quality_controls_precede_drizzle(tmp_path, mode, weighted, abnormal_stars, raw_fwhm):
    files=[]
    for i in range(6):
        f=tmp_path/f'light_{i+1:03d}.fits'
        fits.writeto(f,np.ones((10,10), dtype=np.float32))
        files.append(f)
    # One elongated exposure, one count anomaly or blurred exposure, four good ones.
    rounds=[.3,.8,.6,.7,.8,.9]
    stars=[30,abnormal_stars,30,30,30,30]
    seq=tmp_path/'light_.seq'
    seq.write_text("S 'light_' 1 6 6 3 2 6 0 0 0\nL 1\n"+
                   ''.join(f'I {i+1} 1\n' for i in range(6))+
                   ''.join(f'R0 {raw_fwhm if i == 1 else 1.7} 1.8 {r} 1 0 {n} H 1 0 {i*.2} 0 1 0 0 0 1\n' for i,(r,n) in enumerate(zip(rounds,stars))))
    original_seq = seq.read_text()
    calls = []
    cfg={'drizzle':mode,'roundness_filter':'.5','nbstars_filter':'10', 'max_fwhm': 6,
         'roundness_weighted':weighted,'robust_realign':False}
    class FakeSiril:
        def run_siril_script(self, script, *args, **kwargs):
            calls.append(Path(args[0]))
            if '\nstack ' in script:
                assert seq.read_text() == original_seq
                _, images, reg = read_registration(Path(args[0])/seq.name)
                assert images[0][2] == images[1][2] == '0'
                assert len(images)==len(reg)==(8 if weighted else 6)
                assert '-filter-included' in script
                report=json.loads((tmp_path/'final.drizzle.json').read_text())
                assert report['analysis']['images_retained']==4
                assert report['quality_selection']['effective_stack_entries']==(6 if weighted else 4)
                assert len(report['frames'])==4
                assert all(r['fwhm']<=6 and r['roundness']>=.5 and abs(r['nbstars']-30)<=10 for r in report['frames'])
                (tmp_path/'final.fit').touch()
            return True
    assert run_stack(FakeSiril(), files, cfg, tmp_path,tmp_path,'light_',tmp_path/'final',
                     'requires 1.2', 'stack r_light_ rej 3 3 -out=final', 'min')
    assert len(calls) == len(set(calls))
    assert calls[-1] == tmp_path/'04_stacking'
    assert (tmp_path/'02_quality'/seq.name).read_bytes() == (tmp_path/'04_stacking'/seq.name).read_bytes()
    assert not list(tmp_path.glob('run_*'))
    assert not list(tmp_path.glob('03_stacking_*'))
    if mode != 'force':
        assert 'non exécutée' in (tmp_path/'03_capability'/'SKIPPED.txt').read_text()
    else:
        assert tmp_path/'03_capability' in calls
    assert seq.read_text() == original_seq
    assert not (tmp_path/'light_007.fits').exists()


def test_old_quality_cache_is_invalidated(tmp_path):
    from lib.drizzle import cache_matches
    (tmp_path/'result.drizzle.json').write_text(json.dumps({'status':'completed','settings':{}}))
    assert not cache_matches(tmp_path/'result.fits', {})
    (tmp_path/'result.drizzle.json').write_text(json.dumps({'status':'completed','settings':{},'quality_pipeline_version':9}))
    assert not cache_matches(tmp_path/'result.fits', {})


def test_precise_reasons_distinguish_missing_and_exceeded():
    result=analyse(records(np.arange(12).reshape(6,2), 3.2),
                   {'drizzle_fwhm_limit':2.7,'drizzle_max_drift':4}, metadata(10), {'sufficient':True})
    messages={d['criterion']:d['message'] for d in result['reason_details']}
    assert '3.200 px' in messages['sampling'] and '2.700 px' in messages['sampling']
    assert '6/10' in messages['alignment'] and '9 poses' in messages['alignment']
    assert '14.142 px' in messages['drift'] and '4.000 px' in messages['drift']
    missing=analyse([], {}, metadata(10), {'sufficient':True})
    messages={d['criterion']:d['message'] for d in missing['reason_details']}
    assert 'indisponible' in messages['sampling'] and '0 poses' in messages['sampling']
    assert 'non calculée' in messages['drift']


def test_resource_reasons_report_only_failing_resources():
    resources={'sufficient':False, 'estimated_memory_bytes':2**31, 'available_memory_bytes':2**30,
               'estimated_disk_bytes':100, 'free_disk_bytes':200,
               'estimated_output_bytes':300, 'free_output_disk_bytes':250}
    result=analyse([], {}, metadata(1), resources)
    messages=[d['message'] for d in result['reason_details'] if d['criterion']=='resources']
    assert len(messages)==2
    assert 'Mémoire RAM' in messages[0] and '2.000 Gio' in messages[0] and '1.000 Gio' in messages[0]
    assert 'Disque de sortie' in messages[1] and '300 octets' in messages[1] and '250 octets' in messages[1]
    assert not any('Disque de travail' in m for m in messages)


def test_transform_reason_identifies_pose_coefficient_and_tolerance():
    frames=records(np.zeros((4,2)))
    for i,frame in enumerate(frames):
        frame.update(source=f'pose{i}.fits',homography=np.eye(3).tolist())
    frames[2]['homography'][0][1]=.02
    result=analyse(frames, {}, metadata(4), {'sufficient':True})
    message=next(d['message'] for d in result['reason_details'] if d['criterion']=='translation_model')
    assert '1/4 poses' in message and 'pose2.fits' in message
    assert 'H[0,1]=0.02' in message and 'tolérance=0.01' in message


def test_stage_transfer_isolates_metadata_and_references_fits(tmp_path):
    from lib.drizzle import copy_stage_inputs
    source=tmp_path/'registration';source.mkdir()
    dest=tmp_path/'stacking';dest.mkdir()
    (source/'light_.seq').write_text('original')
    fits.writeto(source/'light_001.fits',np.ones((4,4)))
    (source/'registration.sps').write_text('close')
    (source/'distortion').mkdir()
    (source/'distortion'/'map.txt').write_text('calibration')
    copy_stage_inputs(source,dest)
    assert (dest/'light_001.fits').is_symlink()
    assert (dest/'light_001.fits').resolve()==source/'light_001.fits'
    (dest/'light_.seq').write_text('selection changed')
    assert (source/'light_.seq').read_text()=='original'
    assert not (dest/'registration.sps').exists()
    assert (dest/'distortion'/'map.txt').read_text()=='calibration'


def test_reset_numbered_stage_cleans_previous_outputs_and_unlinks_alias(tmp_path):
    from lib.drizzle import reset_stage
    stage=tmp_path/'04_stacking'
    reset_stage(stage)
    (stage/'stale.fits').write_text('old')
    assert reset_stage(stage)==stage
    assert list(stage.iterdir())==[]
    elsewhere=tmp_path/'saved';elsewhere.mkdir()
    (elsewhere/'keep').write_text('preserve')
    alias=tmp_path/'03_capability';alias.symlink_to(elsewhere,target_is_directory=True)
    reset_stage(alias)
    assert not alias.is_symlink()
    assert (elsewhere/'keep').read_text()=='preserve'


def test_memory_budget_counts_reclaimable_ram_not_swap(tmp_path):
    from lib.drizzle import memory_resources
    meminfo=tmp_path/'meminfo'
    meminfo.write_text('MemTotal: 64000 kB\nMemFree: 1000 kB\nMemAvailable: 50000 kB\nSwapTotal: 8000 kB\nSwapFree: 6000 kB\n')
    result=memory_resources(meminfo)
    assert result['available_memory_bytes']==50000*1024
    assert result['free_memory_bytes']==1000*1024
    assert result['free_swap_bytes']==6000*1024
    assert not result['swap_included_in_memory_budget']
    assert result['memory_available_source']=='/proc/meminfo:MemAvailable'
    meminfo.write_text('MemAvailable: 0 kB\n')
    assert memory_resources(meminfo)['available_memory_bytes']==0


def test_memory_budget_fallback(tmp_path, monkeypatch):
    from lib.drizzle import memory_resources
    monkeypatch.setattr('lib.drizzle.os.sysconf', lambda key: {'SC_AVPHYS_PAGES':10, 'SC_PAGE_SIZE':4096}[key])
    result=memory_resources(tmp_path/'missing')
    assert result['available_memory_bytes']==40960
    assert result['free_swap_bytes'] is None
    assert result['memory_available_source'].startswith('sysconf:')
