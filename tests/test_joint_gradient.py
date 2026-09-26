import argparse
from pathlib import Path
import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS
from lib.postprocess import GradientExtractor


def panels(tmp_path, disconnected=False):
    paths=[]
    for i, shift in enumerate([0, 45, 90 if not disconnected else 500]):
        y,x=np.mgrid[:80,:100]
        sky=100 + 30*np.exp(-((x+shift-90)**2+(y-40)**2)/1000)
        bias=i*3 + i*.015*(x+shift) - i*.02*y
        w=WCS(naxis=2)
        w.wcs.ctype=['RA---TAN','DEC--TAN']
        w.wcs.crval=[10,41];w.wcs.cdelt=[-.001,.001];w.wcs.crpix=[100-shift,40]
        path=tmp_path/f'panel{i}.fit'
        fits.writeto(path,(sky+bias).astype(np.float32),w.to_header())
        paths.append(path)
    return paths


@pytest.mark.parametrize('method',['rbf','polynomial'])
def test_joint_fit_preserves_common_galaxy_and_matches_overlaps(tmp_path,method):
    paths=panels(tmp_path)
    original=[p.read_bytes() for p in paths]
    processor=GradientExtractor(method=method,create_measurement_image=False)
    result=processor.process_mosaic(paths,tmp_path/'joint', argparse.Namespace(mosaic_gradient_mode='relative'))
    reference=Path(result[0]['correction']['reference_image'])
    index=paths.index(reference)
    # Expected sky + the reference panel's background, including its linear ramp.
    for i,r in enumerate(result):
        data=fits.getdata(r['output_image'])
        y,x=np.mgrid[:80,:100];gx=x+45*i
        expected=100+30*np.exp(-((gx-90)**2+(y-40)**2)/1000)+index*3+index*.015*gx-index*.02*y
        np.testing.assert_allclose(data[8:-8,8:-8],expected[8:-8,8:-8],atol=.12)
        assert fits.getheader(r['output_image'])['CTYPE1']=='RA---TAN'
    assert original==[p.read_bytes() for p in paths]
    metrics=result[0]['correction']['channels'][0]
    assert metrics['after_rms'] < metrics['before_rms']*.03
    assert result[0]['correction']['common_gradient_removed'] is False


def test_disconnected_panels_fail_without_independent_correction(tmp_path):
    paths=panels(tmp_path,True)
    with pytest.raises(ValueError,match='Recouvrements'):
        GradientExtractor().process_mosaic(paths,tmp_path/'joint', argparse.Namespace(mosaic_gradient_mode='relative'))
    assert not list((tmp_path/'joint').glob('*.fits'))


def test_missing_wcs_fails(tmp_path):
    p=tmp_path/'bad.fit';fits.writeto(p,np.ones((30,30)))
    with pytest.raises(ValueError,match='WCS'):
        GradientExtractor().process_mosaic([p,p],tmp_path/'joint')


def test_mosaic_uses_joint_outputs(tmp_path, monkeypatch):
    from lib.mosaic import Mosaic
    paths, _ = sky_panels(tmp_path)
    mosaic=Mosaic(work_dir=tmp_path/'work',mosaic_name='joint',input_files=paths[1:])
    mosaic.set_from_args(argparse.Namespace(gradient_measurement_image=False))
    def assemble(self,args=None):
        assert all(p.parent == tmp_path/'work/mosaic_joint' for p in self.input_files)
        assert all('gradient_corrected' in p.name for p in self.input_files)
        output=tmp_path/'final.fit';output.touch();return output
    monkeypatch.setattr(Mosaic,'create_mosaic',assemble)
    report=mosaic.post_process(paths[0],tmp_path/'report.json')
    assert len(report['gradient_results'])==3
    assert all(r['correction']['mode'] == 'panel_background_and_overlap'
               for r in report['gradient_results'])
    assert (tmp_path/'work/mosaic_joint/joint_gradient.json').is_file()


def test_rgb_padding_and_reference_preserved(tmp_path):
    paths=panels(tmp_path)
    for path in paths:
        with fits.open(path) as hdul:
            header=hdul[0].header.copy()
            data=hdul[0].data.copy()
        rgb=np.stack([data,2*data,3*data])
        rgb[:,0,:]=0
        rgb[:,1,1]=np.nan
        fits.writeto(path,rgb,header,overwrite=True)
    processor=GradientExtractor(create_measurement_image=True)
    results=processor.process_mosaic(paths,tmp_path/'joint', argparse.Namespace(mosaic_gradient_mode='relative'))
    for result in results:
        data=fits.getdata(result['output_image'])
        assert np.all(data[:,0,:]==0)
        assert np.all(np.isnan(data[:,1,1]))
        assert len(result['correction']['channels'])==3
        assert Path(result['measurement_image_path']).is_file()
        if result['image_path']==result['correction']['reference_image']:
            np.testing.assert_array_equal(data,fits.getdata(result['image_path']))


def test_legacy_radecsys_normalized_without_changing_sources(tmp_path):
    import warnings
    paths = panels(tmp_path)
    for path in paths:
        with fits.open(path, mode='update') as hdul:
            hdul[0].header['RADECSYS'] = 'FK5'
            hdul[0].header.pop('RADESYS', None)
            hdul[0].header['EQUINOX'] = 2000.
    originals = [p.read_bytes() for p in paths]
    from lib.joint_gradient import _normalized_wcs_header
    header = fits.getheader(paths[0])
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        legacy_wcs = WCS(header, naxis=2)
    modern_wcs = WCS(_normalized_wcs_header(header), naxis=2)
    np.testing.assert_allclose(legacy_wcs.all_pix2world([[10, 20], [60, 50]], 0),
                               modern_wcs.all_pix2world([[10, 20], [60, 50]], 0))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        results = GradientExtractor(create_measurement_image=False).process_mosaic(paths, tmp_path/'joint', argparse.Namespace(mosaic_gradient_mode='relative'))
    assert not any('RADECSYS' in str(w.message) for w in caught)
    for result in results:
        header = fits.getheader(result['output_image'])
        assert header['RADESYS'] == 'FK5'
        assert 'RADECSYS' not in header
    assert originals == [p.read_bytes() for p in paths]


def test_conflicting_reference_frames_are_not_silently_replaced():
    from lib.joint_gradient import _normalized_wcs_header
    header = fits.Header({'RADECSYS': 'FK5', 'RADESYS': 'ICRS'})
    with pytest.raises(ValueError, match='contradictoires'):
        _normalized_wcs_header(header)


def sky_panels(tmp_path, rgb=False, curved=False):
    """A shared galaxy and stars, with visible sky and a different gradient per panel."""
    paths, signals = [], []
    rng = np.random.default_rng(548)
    for i, shift in enumerate([0, 90, 180]):
        y, x = np.mgrid[:160, :200]
        gx = x+shift
        galaxy = 80*np.exp(-((gx-190)/32)**2-((y-80)/19)**2)
        stars = sum(150*np.exp(-((gx-sx)**2+(y-sy)**2)/3)
                    for sx, sy in [(70, 30), (155, 120), (220, 35), (300, 125)])
        signal = galaxy+stars
        gradient = .045*gx-.035*y+i*(4+.014*gx-.01*y)
        if curved:
            gradient += .0003*(gx-190)**2 + .0002*(y-80)**2
        data = 100+signal+gradient+rng.normal(0,.04,x.shape)
        if rgb:
            data = np.stack([data, 1.3*data, .8*data])
        w = WCS(naxis=2)
        w.wcs.ctype = ['RA---TAN', 'DEC--TAN']
        w.wcs.crval = [10, 41]
        w.wcs.cdelt = [-.001, .001]
        w.wcs.crpix = [200-shift, 80]
        path = tmp_path/f'sky_panel{i}.fit'
        fits.writeto(path, data.astype(np.float32), w.to_header())
        paths.append(path)
        signals.append(signal)
    return paths, signals


@pytest.mark.parametrize('method', ['rbf', 'polynomial'])
def test_panel_background_removes_common_and_individual_gradients(tmp_path, method):
    paths, signals = sky_panels(tmp_path)
    originals = [p.read_bytes() for p in paths]
    results = GradientExtractor(method=method, create_measurement_image=False).process_mosaic(paths, tmp_path/'new')
    baseline = results[0]['correction']['sky_level'][0]
    for path, signal, result in zip(paths, signals, results):
        corrected = fits.getdata(result['output_image'])
        residual = corrected-signal-baseline
        assert np.sqrt(np.mean(residual[10:-10, 10:-10]**2)) < .15
        # Integrated extended source flux must survive the background estimate.
        galaxy_region = signal > 5
        assert abs(np.sum(corrected[galaxy_region]-baseline)/np.sum(signal[galaxy_region])-1) < .01
        selection = result['background_selection']
        points = np.array(selection['candidates']).astype(int)
        used = np.array(selection['used_per_channel'])[0]
        assert len(points[used]) >= 12
        assert np.max(signal[points[used, 1], points[used, 0]]) < 1
        # Sampling is not limited to the WCS overlaps.
        assert np.ptp(points[used, 0]) > .7*corrected.shape[1]
        assert fits.getheader(result['output_image'])['CTYPE1'] == 'RA---TAN'
    assert originals == [p.read_bytes() for p in paths]
    assert results[0]['correction']['common_gradient_removed'] is True
    reference = results[0]['correction']['reference_image']
    ref_result = next(r for r in results if r['image_path'] == reference)
    assert not np.array_equal(fits.getdata(reference), fits.getdata(ref_result['output_image']))


def test_background_rgb_masks_padding_and_diagnostics(tmp_path):
    from PIL import Image
    paths, _ = sky_panels(tmp_path, rgb=True)
    masks = []
    for index, path in enumerate(paths):
        with fits.open(path, mode='update') as hdul:
            hdul[0].data[:, :4, :] = 0
            hdul[0].data[:, 6, 6] = np.nan
            hdul.append(fits.ImageHDU(np.ones((2, 2)), name='EXTRA'))
        mask = np.zeros((160, 200), dtype=np.uint8)
        mask[20:45, 60:90] = 1
        name = tmp_path/f'mask{index}.fits'
        fits.writeto(name, mask)
        masks.append(name)
    result = GradientExtractor().process_mosaic(paths, tmp_path/'rgb',
                      argparse.Namespace(mosaic_gradient_masks=masks))
    for item in result:
        with fits.open(item['output_image']) as hdul:
            assert np.all(hdul[0].data[:, :4] == 0)
            assert np.all(np.isnan(hdul[0].data[:, 6, 6]))
            assert hdul['EXTRA'].data.shape == (2, 2)
        selection = item['background_selection']
        points = np.array(selection['candidates'])
        used = np.any(selection['used_per_channel'], axis=0)
        assert not np.any(used & (points[:, 0] >= 60) & (points[:, 0] < 90)
                         & (points[:, 1] >= 20) & (points[:, 1] < 45))
        assert Image.open(item['measurement_image_path']).height == 200
        assert np.asarray(selection['used_per_channel']).shape[0] == 3


def test_fully_masked_background_fails_without_writing_corrected_images(tmp_path):
    paths, _ = sky_panels(tmp_path)
    mask = tmp_path/'mask.fits'
    fits.writeto(mask, np.ones((160, 200)))
    with pytest.raises(ValueError, match='Fond insuffisant'):
        GradientExtractor().process_mosaic(paths, tmp_path/'invalid',
                        argparse.Namespace(mosaic_gradient_masks=[mask]*3))
    assert not list((tmp_path/'invalid').glob('*.fits'))


@pytest.mark.parametrize('method', ['rbf', 'polynomial'])
def test_curved_background_is_removed_without_fitting_the_galaxy(tmp_path, method):
    paths, signals = sky_panels(tmp_path, curved=True)
    results = GradientExtractor(method=method, min_polynomial_order=2,
                                create_measurement_image=False).process_mosaic(paths, tmp_path/'curved')
    baseline = results[0]['correction']['sky_level'][0]
    for result, signal in zip(results, signals):
        corrected = fits.getdata(result['output_image'])
        assert np.sqrt(np.mean((corrected-signal-baseline)[10:-10, 10:-10]**2)) < .15
        galaxy = signal > 5
        assert abs(np.sum(corrected[galaxy]-baseline)/np.sum(signal[galaxy])-1) < .01


def test_background_disconnected_panels_fail(tmp_path):
    paths, _ = sky_panels(tmp_path)
    with fits.open(paths[-1], mode='update') as hdul:
        hdul[0].header['CRVAL1'] = 50.
    with pytest.raises(ValueError, match='Recouvrements'):
        GradientExtractor().process_mosaic(paths, tmp_path/'disconnected')
    assert not list((tmp_path/'disconnected').glob('*.fits'))


@pytest.mark.parametrize('masks', ['count', 'shape'])
def test_invalid_exclusion_masks_fail(tmp_path, masks):
    paths, _ = sky_panels(tmp_path)
    mask = tmp_path/'mask.fits'
    fits.writeto(mask, np.zeros((3, 3)))
    options = argparse.Namespace(mosaic_gradient_masks=[mask] if masks == 'count' else [mask]*3)
    with pytest.raises(ValueError, match='masque'):
        GradientExtractor().process_mosaic(paths, tmp_path/'invalid', options)


def test_relative_preview_excludes_rejected_measurements(tmp_path, monkeypatch):
    paths = panels(tmp_path)
    with fits.open(paths[0], mode='update') as hdul:
        hdul[0].data[15:25, 65:80] += 100
    processor = GradientExtractor()
    displayed = {}

    def preview(path, points, output):
        displayed[Path(path)] = points
        return output

    monkeypatch.setattr(processor, 'create_measurement_points_image', preview)
    results = processor.process_mosaic(paths, tmp_path/'relative',
                        argparse.Namespace(mosaic_gradient_mode='relative'))
    overlaps = results[0]['correction']['overlaps']
    first = next(pair for pair in overlaps if pair['panels'] == [1, 2])
    assert first['used_per_channel'][0] < first['candidates']
    retained = sum(pair['used_per_channel'][0] for pair in overlaps if 1 in pair['panels'])
    assert len(displayed[paths[0]]) == retained
