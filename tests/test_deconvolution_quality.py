"""Garde-fous mesurables sur profils synthétiques, halos et bruit."""
import numpy as np
import pytest
from astropy.io import fits

from lib.deconvolution_quality import select_psf_stars, artifact_metrics, assess_quality


def test_psf_sheet_keeps_only_fine_round_stars(tmp_path):
    yy, xx = np.indices((400, 400))
    data = np.full((400, 400), .01)
    stars = []
    for i in range(15):
        x, y = 40+(i % 5)*70, 40+(i//5)*100
        width = 3.5 if i < 5 else 7.0
        amplitude = .2 if i != 13 else .9
        fy = width if i != 14 else width/2
        data += amplitude*np.exp(-((xx-x)**2+(yy-y)**2)/(2*(width/2.355)**2))
        stars.append(dict(id=i, layer=0, x=x+.5, y=400-y-.5, fwhm_x=width,
                          fwhm_y=fy, fwhm=np.sqrt(width*fy), amplitude=amplitude))
    source, sheet = tmp_path/'image.fit', tmp_path/'sheet.fit'
    fits.writeto(source, data.astype('float32'))
    result = select_psf_stars(stars, source, sheet)
    assert {s['id'] for s in result['stars']} == set(range(5))
    assert result['selected_count'] == 5
    assert 3.5 <= result['fwhm_limit_px'] < 7.0
    assert np.max(fits.getdata(sheet)) == pytest.approx(.301, rel=.01)
    assert fits.getdata(source).shape == (400, 400)


def comparison(round_before=.9, round_after=.92, ratio=.9):
    return dict(status='validated', before_fwhm_px={'mean': 4}, after_fwhm_px={'mean': 4*ratio},
                paired_ratio={'median': ratio}, before_roundness={'mean': round_before},
                after_roundness={'mean': round_after})


@pytest.mark.parametrize('round_after,ratio,noise,rings,expected', [
    (.92, .9, 1, 0, None), (.92, .995, 1, 0, 'insufficient_sharpening'),
    (.85, .9, 1, 0, 'roundness_degraded'), (.9, .9, 1, 0, None), (.8976, .9762, 1.0591, 0, None),
    (.92, .9, 1.3, 0, 'noise_amplification'), (.92, .9, 1, .2, 'stellar_rings'),
])
def test_quality_requires_both_effectiveness_and_artifact_limits(round_after, ratio, noise, rings, expected):
    result = assess_quality(comparison(round_after=round_after, ratio=ratio),
                            dict(max_noise_ratio=noise, max_ring_fraction=rings))
    assert result['accepted'] == (expected is None)
    if expected:
        assert expected in result['reasons']


def test_already_round_stars_need_not_become_impossibly_rounder():
    result = assess_quality(comparison(round_before=.99, round_after=.99),
                            dict(max_noise_ratio=1, max_ring_fraction=0))
    assert result['accepted']


@pytest.mark.parametrize('artifact', ['none', 'noise', 'ring'])
def test_pixel_metrics_detect_noise_and_dark_rings(tmp_path, artifact):
    rng = np.random.default_rng(87)
    yy, xx = np.indices((220, 220))
    noise = rng.normal(0, .0002, yy.shape)
    before = .01+noise
    after = .01+noise.copy()
    pairs = []
    for i, (x, y) in enumerate([(40, 40), (110, 40), (180, 40), (40, 110), (110, 110), (180, 180)]):
        rr = np.hypot(xx-x, yy-y)
        before += .2*np.exp(-rr**2/8)
        after += .2*np.exp(-rr**2/(2*1.8**2))
        if artifact == 'ring':
            after -= .03*np.exp(-(rr-6)**2/(2*.8**2))
        pairs.append(dict(reference_id=i, x_before=x+.5, y_before=220-y-.5, fwhm_before_px=4.71))
    if artifact == 'noise':
        after += rng.normal(0, .001, yy.shape)
    b, a = tmp_path/'before.fit', tmp_path/'after.fit'
    fits.writeto(b, before.astype('float32'))
    fits.writeto(a, after.astype('float32'))
    result = artifact_metrics(b, a, pairs)
    if artifact == 'noise':
        assert result['max_noise_ratio'] > 1.15
    elif artifact == 'ring':
        assert result['max_ring_fraction'] > .1
    else:
        assert result['max_noise_ratio'] < 1.15
        assert result['max_ring_fraction'] == 0


def test_noise_comparison_normalizes_uint16_and_float_fits(tmp_path):
    rng = np.random.default_rng(32)
    y, x = np.indices((80, 80))
    data = (.02+rng.normal(0, .0005, (80, 80))+.2*np.exp(-((x-40)**2+(y-40)**2)/8))
    integer = (data*65535).astype('uint16')
    original, candidate = tmp_path/'uint.fit', tmp_path/'float.fit'
    fits.writeto(original, integer)
    fits.writeto(candidate, integer.astype('float32')/65535)
    pairs = [dict(reference_id=1, x_before=40.5, y_before=39.5, fwhm_before_px=4.71)]
    result = artifact_metrics(original, candidate, pairs)
    assert result['max_noise_ratio'] == pytest.approx(1)
    assert result['max_ring_fraction'] == 0


@pytest.mark.parametrize('contamination,accepted', [
    ('none', True), ('elongated', False), ('companion', False), ('diffuse', False),
    ('moffat', True), ('offset', False),
])
def test_stamp_pixels_override_catalog_roundness(contamination, accepted):
    from lib.deconvolution_quality import inspect_psf_stamp
    y, x = np.indices((39, 39))
    dx, dy = x-19.3, y-18.8
    sigma_y = 2.8 if contamination == 'elongated' else 1.5
    if contamination == 'offset':
        dx -= 2
    patch = .01+.2*np.exp(-.5*((dx/1.5)**2+(dy/sigma_y)**2))
    if contamination == 'companion':
        patch += .025*np.exp(-((x-28)**2+(y-23)**2)/4)
    if contamination == 'diffuse':
        patch += .02*np.exp(-((x-29)**2+(y-25)**2)/25)
    if contamination == 'moffat':
        patch = .01+.2*(1+(dx*dx+dy*dy)/9)**-2.5
    result = inspect_psf_stamp(patch, 3.53)
    assert result['accepted'] == accepted, result
    if contamination == 'elongated':
        assert 'pixel_elongation' in result['reasons']


def test_rejected_stamp_audit_survives_insufficient_stars(tmp_path):
    import json
    from lib.deconvolution_quality import select_psf_stars
    y, x = np.indices((80, 80))
    data = .01+.2*np.exp(-((x-40)**2/4+(y-40)**2/16))
    source, sheet = tmp_path/'source.fit', tmp_path/'sheet.fit'
    fits.writeto(source, data)
    stars = [dict(id=1, layer=0, x=40.5, y=39.5, fwhm=3.5,
                  fwhm_x=3.5, fwhm_y=3.5, amplitude=.2)]
    with pytest.raises(ValueError, match='seulement 0'):
        select_psf_stars(stars, source, sheet)
    audit = json.loads(sheet.with_suffix('.json').read_text())
    assert 'pixel_elongation' in audit['inspections'][0]['reasons']
    assert not sheet.exists()


def test_m33_conservative_trial_preserves_roundness_within_tolerance():
    result = assess_quality(
        dict(status='validated', before_fwhm_px={'mean': 3.711951173991598},
             after_fwhm_px={'mean': 3.6272584788958904},
             paired_ratio={'median': .9762317313828072},
             before_roundness={'mean': .9004455214713775},
             after_roundness={'mean': .8975936929888441}),
        dict(max_noise_ratio=1.0590809684285967, max_ring_fraction=0))
    assert result['accepted']
    assert not result['artifact_failure']
