import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from lib.mosaic_crop import crop_mosaic, largest_covered_rectangle


def test_rectangle_matches_exhaustive_search():
    rng = np.random.default_rng(18)
    for _ in range(100):
        mask = rng.random((5, 7)) > .25
        mask[2, 3] = True
        x, y, width, height = largest_covered_rectangle(mask)
        assert mask[y:y+height, x:x+width].all()
        expected = max((right-left)*(bottom-top)
                       for top in range(5) for bottom in range(top+1, 6)
                       for left in range(7) for right in range(left+1, 8)
                       if mask[top:bottom, left:right].all())
        assert width*height == expected


@pytest.mark.parametrize('rgb', [False, True])
def test_crop_preserves_original_values_metadata_and_wcs(tmp_path, rgb):
    data = np.pad(-np.ones((6, 8), dtype=np.float32), ((2, 3), (4, 1)))
    if rgb:
        data = np.stack([data, np.zeros_like(data), data])
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [7, 5]
    wcs.wcs.cdelt = [-.001, .001]
    wcs.wcs.crval = [10, 41]
    wcs.wcs.ctype = ['RA---TAN', 'DEC--TAN']
    header = wcs.to_header()
    header['CRPIX1A'], header['CRPIX2A'] = 8, 6
    header['OBJECT'] = 'M31'
    original, cropped = tmp_path/'full.fits', tmp_path/'crop.fits'
    fits.HDUList([fits.PrimaryHDU(data, header),
                  fits.ImageHDU(np.ones((11, 13)), name='WEIGHT'),
                  fits.BinTableHDU.from_columns([fits.Column(name='ID', format='J', array=[42])])]).writeto(original)
    before = original.read_bytes()
    result = crop_mosaic(original, cropped)
    assert original.read_bytes() == before
    assert result['bounds'] == dict(x=4, y=2, width=8, height=6)
    with fits.open(cropped) as hdul:
        np.testing.assert_array_equal(hdul[0].data, data[..., 2:8, 4:12])
        assert hdul[0].header['OBJECT'] == 'M31'
        assert hdul[0].header['CRPIX1A'] == 4
        assert hdul[0].header['CRPIX2A'] == 4
        np.testing.assert_allclose(WCS(hdul[0].header).celestial.all_pix2world([[0, 0], [7, 5]], 0),
                                   wcs.all_pix2world([[4, 2], [11, 7]], 0))
        assert hdul[1].data.shape == (6, 8)
        assert hdul[2].data['ID'][0] == 42


def test_nonfinite_and_internal_holes_are_excluded(tmp_path):
    data = np.ones((9, 12))
    data[2, 3] = np.nan
    data[5, 8] = 0
    source, target = tmp_path/'full.fits', tmp_path/'crop.fits'
    fits.writeto(source, data)
    crop_mosaic(source, target)
    result = fits.getdata(target)
    assert np.isfinite(result).all() and (result != 0).all()


@pytest.mark.parametrize('dtype', [np.uint16, np.int16, np.float32])
def test_fully_covered_image_keeps_values(tmp_path, dtype):
    data = np.full((4, 7), 123, dtype=dtype)
    source, target = tmp_path/'full.fits', tmp_path/'crop.fits'
    fits.writeto(source, data)
    result = crop_mosaic(source, target)
    assert not result['applied']
    np.testing.assert_array_equal(fits.getdata(target), data)


def test_failure_preserves_files(tmp_path, monkeypatch):
    source, target = tmp_path/'full.fits', tmp_path/'crop.fits'
    fits.writeto(source, np.ones((4, 7)))
    original = source.read_bytes()
    target.write_bytes(b'previous result')
    def fail(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(fits.HDUList, 'writeto', fail)
    with pytest.raises(OSError, match='disk full'):
        crop_mosaic(source, target)
    assert source.read_bytes() == original
    assert target.read_bytes() == b'previous result'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['crop.fits', 'full.fits']


def test_empty_coverage_and_same_file_rejected(tmp_path):
    source, target = tmp_path/'full.fits', tmp_path/'crop.fits'
    fits.writeto(source, np.zeros((4, 7)))
    with pytest.raises(ValueError, match='distinct'):
        crop_mosaic(source, source)
    with pytest.raises(ValueError, match='Aucune zone'):
        crop_mosaic(source, target)
    assert source.exists() and not target.exists()


def test_sip_distortion_coordinates_survive_crop(tmp_path):
    from astropy.wcs import Sip
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [8, 7]
    wcs.wcs.cdelt = [-.001, .001]
    wcs.wcs.crval = [10, 41]
    wcs.wcs.ctype = ['RA---TAN-SIP', 'DEC--TAN-SIP']
    a, b = np.zeros((3, 3)), np.zeros((3, 3))
    a[2, 0], b[0, 2] = 1e-4, -2e-4
    wcs.sip = Sip(a, b, None, None, wcs.wcs.crpix)
    source, target = tmp_path/'full.fits', tmp_path/'crop.fits'
    fits.writeto(source, np.pad(np.ones((10, 12)), ((2, 3), (4, 1))), wcs.to_header(relax=True))
    crop_mosaic(source, target)
    after = WCS(fits.getheader(target))
    np.testing.assert_allclose(after.all_pix2world([[0, 0], [11, 9]], 0),
                               wcs.all_pix2world([[4, 2], [15, 11]], 0), atol=1e-10)


@pytest.mark.parametrize('removed_columns, rejected', [(4, False), (5, False), (6, True)])
def test_panel_loss_threshold_is_strict_and_per_panel(tmp_path, removed_columns, rejected):
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ['RA---TAN', 'DEC--TAN']
    wcs.wcs.cdelt = [-.001, .001]
    wcs.wcs.crval = [10, 41]
    header = wcs.to_header()
    source, target = tmp_path/'full.fits', tmp_path/'crop.fits'
    # Proposed rectangle x=10..29. One panel straddles its left boundary.
    mosaic = np.zeros((10, 30))
    mosaic[:, 10:] = 1
    fits.writeto(source, mosaic, header)
    panels = []
    for index, offset in enumerate([10-removed_columns, 20]):
        panel_header = header.copy()
        panel_header['CRPIX1'] -= offset
        path = tmp_path/f'panel{index}.fits'
        fits.writeto(path, np.ones((10, 10)), panel_header)
        panels.append(path)
    original = source.read_bytes()
    result = crop_mosaic(source, target, panel_paths=panels)
    assert source.read_bytes() == original
    assert result['panel_retention'][0]['removed_pixels'] == removed_columns*10
    assert result['panel_retention'][1]['removed_pixels'] == 0
    assert result['applied'] is not rejected
    assert result['output_image'] == str(source if rejected else target)
    assert target.exists() is not rejected
    if rejected:
        assert result['reason'] == 'panel_loss_exceeds_half'
        assert result['cropped_shape'] == [10, 30]


def test_missing_panel_coordinates_returns_original(tmp_path):
    source, target, panel = tmp_path/'full.fits', tmp_path/'crop.fits', tmp_path/'panel.fits'
    fits.writeto(source, np.pad(np.ones((5, 7)), 2))
    fits.writeto(panel, np.ones((5, 7)))
    result = crop_mosaic(source, target, panel_paths=[panel])
    assert result['reason'] == 'panel_coverage_unavailable'
    assert result['output_image'] == str(source)
    assert not target.exists()
