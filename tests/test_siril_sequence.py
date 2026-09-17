from pathlib import Path

import numpy as np
import pytest

from lib.siril_sequence import SirilSequence, SequenceImage


def make_sequence(tmp_path, version=6):
    text = (f"# unchanged comment\nS 'light_' 3 2 1 3 0 {version} 0 0 0\nL 2\n"
            "I 3 1 100,80\nI 9 0 100,80\n"
            "R0 3.1398 6.51206 .844371 0 .00544856 863 H .999968 7.49496e-06 -3.32712 -1.17012e-05 1.00001 -.489946 0 0 1\n"
            "R0 4 5 .8 0 .01 500 H 1 0 0 0 1 0 0 0 1\n"
            "R1 2 3 .9 0 .01 700 H 1 0 2 0 1 3 0 0 1\n"
            "R1 3 4 .7 0 .02 400 H 1 0 3 0 1 4 0 0 1\n"
            "M0-0 100 90 .1 .2\nM0-1 80 70 .3 .4\nD0 0\n# tail\n")
    path = tmp_path/'light_.seq'
    path.write_text(text)
    for n in (3, 9):
        (tmp_path/f'light_{n:03d}.fits').write_bytes(b'unchanged pixels')
    return path, text


@pytest.mark.parametrize('version', [4, 5, 6, 7])
def test_round_trip_preserves_measurements_and_unknown_lines(tmp_path, version):
    path, original = make_sequence(tmp_path, version)
    sequence = SirilSequence.read(path)
    assert isinstance(sequence.images[0], SequenceImage)
    image = sequence.images[0]
    assert image.filename == tmp_path/'light_003.fits'
    assert image.processing_path == image.filename
    assert image.registration().number_of_stars == 863
    assert image.registration('R1').homography[0,2] == 2
    sequence.write()
    assert path.read_text() == original


def test_change_inclusion_measurements_and_statistics(tmp_path):
    path, _ = make_sequence(tmp_path)
    sequence = SirilSequence.read(path)
    sequence.images[1].included = True
    sequence.images[1].registration().fwhm = 7.25
    sequence.images[1].registration().homography[0,2] = 12.5
    sequence.images[1].statistics['M1'] = '42 35 .5 .6'
    sequence.write()
    result = SirilSequence.read(path)
    assert result.header[4] == '2'
    assert result.images[1].included
    assert result.images[1].registration().fwhm == 7.25
    assert result.images[1].registration().homography[0,2] == 12.5
    assert result.images[1].statistics['M1'] == '42 35 .5 .6'


def test_duplicate_copies_all_channels_and_statistics_without_aliasing(tmp_path):
    path, _ = make_sequence(tmp_path)
    sequence = SirilSequence.read(path, source_files=[tmp_path/'native_a.fits', tmp_path/'native_b.fits'])
    original = sequence.images[0]
    clone = sequence.duplicate(original)
    assert clone.number == 10
    assert clone.filename.is_symlink()
    assert clone.filename.resolve() == original.filename
    assert clone.processing_path == tmp_path/'native_a.fits'
    clone.registration('R1').homography[0,2] = 77
    assert original.registration('R1').homography[0,2] == 2
    sequence.write()
    result = SirilSequence.read(path)
    assert result.header[3:5] == ['3', '2']
    assert result.images[2].statistics == original.statistics
    assert result.images[2].registration('R1').homography[0,2] == 77
    assert original.filename.read_bytes() == b'unchanged pixels'


def test_duplicate_refuses_to_overwrite_existing_file(tmp_path):
    path, _ = make_sequence(tmp_path)
    collision = tmp_path/'light_010.fits'
    collision.write_bytes(b'keep')
    sequence = SirilSequence.read(path)
    with pytest.raises(FileExistsError):
        sequence.duplicate(sequence.images[0])
    assert collision.read_bytes() == b'keep'
    assert len(sequence.images) == 2


def test_from_files_uses_real_numbers_without_inventing_registration(tmp_path):
    files = [tmp_path/f'target_{n:05d}.fits' for n in (12, 3)]
    sequence = SirilSequence.from_files(tmp_path/'target_.seq', 'target_', files, layers=3)
    assert [image.number for image in sequence.images] == [3, 12]
    sequence.write()
    result = SirilSequence.read(sequence.path)
    assert not result.registration_layers
    with pytest.raises(ValueError, match='Alignement manquant'):
        result.images[0].registration()


def test_source_mapping_requires_same_length(tmp_path):
    path, _ = make_sequence(tmp_path)
    with pytest.raises(ValueError, match='fichiers sources'):
        SirilSequence.read(path, source_files=[tmp_path/'one.fits'])


@pytest.mark.parametrize('change', [
    lambda s: s.replace('3 2 1 3', '3 3 1 3'),
    lambda s: s.replace('3 2 1 3', '3 2 2 3'),
    lambda s: s.replace('I 9 0', 'I 3 0'),
    lambda s: s.replace('R1 3 4 .7 0 .02 400 H 1 0 3 0 1 4 0 0 1\n', ''),
    lambda s: s+'TS\n',
])
def test_invalid_sequences_fail_explicitly(tmp_path, change):
    path, original = make_sequence(tmp_path)
    path.write_text(change(original))
    with pytest.raises(ValueError):
        SirilSequence.read(path)


def test_failed_atomic_replace_leaves_original_intact(tmp_path, monkeypatch):
    path, original = make_sequence(tmp_path)
    sequence = SirilSequence.read(path)
    sequence.images[1].included = True
    def fail(*args):
        raise OSError('replace failed')
    monkeypatch.setattr(Path, 'replace', fail)
    with pytest.raises(OSError):
        sequence.write()
    assert path.read_text() == original
    assert not list(tmp_path.glob('*.tmp'))


def test_invalid_matrix_edit_cannot_corrupt_sequence(tmp_path):
    path, original = make_sequence(tmp_path)
    sequence = SirilSequence.read(path)
    sequence.images[0].registration().homography = np.eye(2)
    with pytest.raises(ValueError, match='3 × 3'):
        sequence.write()
    assert path.read_text() == original
