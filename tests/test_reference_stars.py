"""Régression des mesures locales : signal fiable, positions et détection indépendante."""
import csv
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from lib.deconvolution import compare_star_catalogs
from lib.reference_stars import FWHM_TO_SIGMA, measure_reference_catalog
from lib.type_defs import StarMeasurement


def write_image(path: Path, sigma: float, amplitude: float, dx: float = 0.0) -> Path:
    """Produit trois étoiles éloignées avec bruit reproductible et orientation explicite."""
    yy, xx = np.mgrid[:128, :128]
    image = np.random.default_rng(123).normal(.1, .001, (128, 128))
    for x, y in [(25., 25.), (85., 25.), (60., 85.)]:
        image += amplitude*np.exp(-((xx-x-dx)**2+(yy-y)**2)/(2*sigma*sigma))
    fits.writeto(path, image.astype('float32'), fits.Header({'ROWORDER': 'TOP-DOWN'}))
    return path


def reference_stars() -> list[StarMeasurement]:
    """Déclare les positions Siril de trois gaussiennes synthétiques."""
    return [dict(id=i, layer=0, x=x+.5, y=128-y-.5,
                 fwhm_x=2*FWHM_TO_SIGMA, fwhm_y=2*FWHM_TO_SIGMA,
                 fwhm=2*FWHM_TO_SIGMA, amplitude=.05, beta=2.5)
            for i, (x, y) in enumerate([(25., 25.), (85., 25.), (60., 85.)], 1)]


def write_catalog(path: Path, stars: list[StarMeasurement]) -> None:
    """Écrit un export Gaussian Siril avec amplitude et coordonnées en pixels."""
    with path.open('w') as stream:
        writer = csv.writer(stream, delimiter='\t')
        writer.writerow(['# star#', 'layer', 'X', 'Y', 'FWHMx [px]', 'FWHMy [px]', 'Sat', 'Profile', 'A'])
        for star in stars:
            writer.writerow([star['id'], 0, star['x'], star['y'], star['fwhm_x'],
                             star['fwhm_y'], 0, 'Gaussian', star['amplitude']])


def test_measures_star_missing_from_independent_catalog(tmp_path: Path) -> None:
    """Un catalogue candidat vide ne bloque pas la mesure aux positions initiales."""
    source = write_image(tmp_path/'source.fits', 2., .05)
    candidate = write_image(tmp_path/'candidate.fits', 1.6, .06, .3)
    before, after = tmp_path/'before.tsv', tmp_path/'after.tsv'
    write_catalog(before, reference_stars())
    write_catalog(after, [])
    result = compare_star_catalogs(before, after, 1., 3, .7, 0,
                                  reference_images=(source, candidate))
    assert result['status'] == 'validated'
    assert result['matched_count'] == 3
    assert result['independent_after_count'] == 0
    assert result['paired_ratio']['median'] == pytest.approx(.8, abs=.04)
    assert all(pair['distance_px'] == pytest.approx(.3, abs=.1) for pair in result['pairs'])


def test_faded_star_remains_in_reference_denominator(tmp_path: Path) -> None:
    """Une mesure candidate devenue trop faible reste un échec, pas une référence exclue."""
    source = write_image(tmp_path/'source.fits', 2., .05)
    candidate = write_image(tmp_path/'candidate.fits', 2., 0.)
    before, after, audit = measure_reference_catalog(source, candidate, reference_stars(), 0, 1.)
    assert len(before) == 3
    assert len(after) == 0
    assert audit['reliable_reference_count'] == 3
    assert any(item['stage'] == 'after' and item['reason'] != 'measured'
               for item in audit['measurements'])


def test_low_snr_and_edge_references_are_audited(tmp_path: Path) -> None:
    """Les références peu fiables sont exclues avant la comparaison et leur motif reste visible."""
    source = write_image(tmp_path/'source.fits', 2., .0001)
    stars = reference_stars()
    stars.append(dict(stars[0], id=4, x=1., y=1.))
    before, _, audit = measure_reference_catalog(source, source, stars, 0, 1.)
    assert not before
    reasons = {item['reason'] for item in audit['measurements']}
    assert 'edge_window' in reasons
    assert reasons & {'low_reference_snr', 'excessive_displacement', 'fit_failed'}


def test_mismatched_shapes_are_rejected(tmp_path: Path) -> None:
    """Une géométrie incompatible ne peut pas produire une comparaison valide."""
    source = write_image(tmp_path/'source.fits', 2., .05)
    candidate = tmp_path/'other.fits'
    fits.writeto(candidate, np.zeros((20, 20)))
    with pytest.raises(ValueError, match='Dimensions différentes'):
        measure_reference_catalog(source, candidate, reference_stars(), 0, 1.)


def test_bottom_up_orientation_matches_top_down(tmp_path: Path) -> None:
    """Les deux conventions FITS donnent les mêmes mesures aux positions Siril."""
    top = write_image(tmp_path/'top.fits', 2., .05)
    bottom = tmp_path/'bottom.fits'
    fits.writeto(bottom, fits.getdata(top)[::-1], fits.Header({'ROWORDER': 'BOTTOM-UP'}))
    before, after, _ = measure_reference_catalog(top, bottom, reference_stars(), 0, 1.)
    assert len(before) == len(after) == 3
    _, expected_after, _ = measure_reference_catalog(top, top, reference_stars(), 0, 1.)
    for first, second in zip(expected_after, after):
        assert second['fwhm'] == pytest.approx(first['fwhm'], abs=1e-5)
        assert second['x'] == pytest.approx(first['x'], abs=1e-5)
        assert second['y'] == pytest.approx(first['y'], abs=1e-5)
