import numpy as np
import pytest
from astropy.io import fits

from lib.siril_sequence import SirilSequence, RegistrationData
from lib.quality_filter import filter_registration_geometry


@pytest.mark.parametrize('matrix,keep,reason', [
    (np.eye(3), True, None),
    ([[1,0,-20],[0,1,10],[0,0,1]], True, None),
    ([[-1,0,99],[0,-1,99],[0,0,1]], True, None),
    ([[.951232,.0476758,114.759],[-.698402,-.0725698,2288.6],[0,0,1]], False, 'orientation'),
    ([[1,0,0],[0,.03,0],[0,0,1]], False, 'échelles'),
    ([[1,0,1000],[0,1,0],[0,0,1]], False, 'hors du cadre'),
    ([[1,0,0],[0,1,0],[-.02,0,1]], False, 'singulière'),
])
def test_geometric_guard_accepts_rotations_and_rejects_corruption(tmp_path, matrix, keep, reason):
    paths = [tmp_path/f'light_{i:03d}.fits' for i in (1,2)]
    for path in paths:
        fits.writeto(path, np.ones((100,100)))
    sequence = SirilSequence.from_files(tmp_path/'light_.seq', 'light_', paths)
    for image in sequence.images:
        image.registrations['R0'] = RegistrationData(2,2,.9,0,.01,200,np.eye(3))
    sequence.images[1].registration().homography = np.array(matrix, dtype=float)
    check = filter_registration_geometry(sequence)
    reread = SirilSequence.read(sequence.path)
    assert reread.images[1].included == keep
    assert check['remaining'] == (2 if keep else 1)
    if reason:
        assert reason in check['rejected'][0]['reason']


def test_new_registration_cannot_reinclude_previous_rejections(tmp_path):
    paths = [tmp_path/f'light_{i:03d}.fits' for i in (1,2)]
    for path in paths:
        fits.writeto(path, np.ones((20,20)))
    sequence = SirilSequence.from_files(tmp_path/'light_.seq','light_',paths)
    for image in sequence.images:
        image.registrations['R0'] = RegistrationData(2,2,.9,0,.01,200,np.eye(3))
    result = filter_registration_geometry(sequence, allowed_numbers={1})
    assert result['remaining'] == 1
    assert not sequence.images[1].included
