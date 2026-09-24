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
    result=processor.process_mosaic(paths,tmp_path/'joint')
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
        GradientExtractor().process_mosaic(paths,tmp_path/'joint')
    assert not list((tmp_path/'joint').glob('*.fits'))


def test_missing_wcs_fails(tmp_path):
    p=tmp_path/'bad.fit';fits.writeto(p,np.ones((30,30)))
    with pytest.raises(ValueError,match='WCS'):
        GradientExtractor().process_mosaic([p,p],tmp_path/'joint')


def test_mosaic_uses_joint_outputs(tmp_path, monkeypatch):
    from lib.mosaic import Mosaic
    paths=panels(tmp_path)
    mosaic=Mosaic(work_dir=tmp_path/'work',mosaic_name='joint',input_files=paths[1:])
    mosaic.set_from_args(argparse.Namespace(gradient_measurement_image=False))
    def assemble(self,args=None):
        assert all(p.parent == tmp_path/'work/mosaic_joint' for p in self.input_files)
        assert all('gradient_corrected' in p.name for p in self.input_files)
        output=tmp_path/'final.fit';output.touch();return output
    monkeypatch.setattr(Mosaic,'create_mosaic',assemble)
    report=mosaic.post_process(paths[0],tmp_path/'report.json')
    assert len(report['gradient_results'])==3
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
    results=processor.process_mosaic(paths,tmp_path/'joint')
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
        results = GradientExtractor(create_measurement_image=False).process_mosaic(paths, tmp_path/'joint')
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
