"""Essai réel optionnel : registration et empilement API de FITS synthétiques.

Activer explicitement SIRIL_API_INTEGRATION=1. Les sources utilisateur et les
préférences Siril ne sont pas modifiées ; tous les produits sont sous tmp/.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

import numpy as np
from astropy.io import fits
import pytest

from lib.light_stacking import SessionStackingProcessor
from lib.siril import Siril


@pytest.mark.skipif(os.getenv('SIRIL_API_INTEGRATION') != '1', reason='Moteur Siril réel non demandé')
@pytest.mark.parametrize('unregistrable', [False, True])
def test_real_api_registration_quality_and_stacking(unregistrable: bool) -> None:
    """Quatre poses étoilées décalées traversent le vrai pipeline sans scripts."""
    root = Path(__file__).resolve().parents[1]
    (root / 'tmp').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='siril-api-pipeline-', dir=root/'tmp') as temporary:
        work = Path(temporary)
        generator = np.random.default_rng(42)
        yy, xx = np.mgrid[:512, :512]
        positions = []
        while len(positions) < 49:
            x, y = generator.uniform(40, 470, 2)
            if all((x-other_x)**2 + (y-other_y)**2 > 24**2 for other_x, other_y in positions):
                positions.append((x, y))
        paths = []
        for index, (dx, dy) in enumerate([(0, 0), (3, 2), (-2, 4), (1, -3)]):
            image = np.full((512, 512), .02, dtype='float64')
            for number, (x, y) in enumerate(positions):
                image += (.2 + .005 * number) * np.exp(-((xx-x-dx)**2 + (yy-y-dy)**2)/(2*1.7**2))
            image += generator.normal(0, .0001, image.shape)
            source = work / f'source image {index}.fits'
            fits.writeto(source, image.astype('float32'), fits.Header({'DATE-OBS': f'2026-01-01T00:00:0{index}'}))
            paths.append(source)
        if unregistrable:
            source = work / 'pose sans etoiles.fits'
            fits.writeto(source, np.full((512, 512), .02, dtype='float32'),
                         fits.Header({'DATE-OBS': '2026-01-01T00:00:04'}))
            paths.append(source)
        original = [path.read_bytes() for path in paths]
        service = Siril(siril_mode=os.getenv('SIRIL_API_MODE', 'flatpak'),
                        siril_path=os.getenv('SIRIL_API_PATH'))
        processor = SessionStackingProcessor(siril=service)
        configuration = dict(drizzle='off', robust_realign=False, enable_stack_platesolve=False,
                             stellar_profile_filter=False, fwhm_filter='none', roundness_filter='none',
                             nbstars_filter='none', rejection='none', method='average')
        result = processor.stack_outputs(paths, work/'out', work/'work', 'api', configuration)
        assert result is not None and Path(result).is_file()
        report = json.loads((work/'out/api_combined.drizzle.json').read_text())
        assert report['status'] == 'completed'
        assert report['final_selection']['independent_retained'] == 4
        assert len(report['selection_decisions']) == len(paths)
        assert report['selection_flow'][0]['remaining_count'] == 4
        registration_dir = work/'work/api/stacking/02_registration'
        initial = json.loads((registration_dir/'registration.json').read_text())
        event = initial['siril_api']['events'][0]
        assert event['complete'] and event['input_count'] == len(paths)
        assert event['included_count'] == 4 and event['excluded_count'] == int(unregistrable)
        assert sum(frame['included'] for frame in event['frames']) == 4
        assert any(frame['measurements'].get('nbstars', 0) > 0 for frame in event['frames'])
        assert event['data_source'] == 'siril_sequence'
        assert event['api_data_available'] is False
        assert all(frame['measurements']['pair_matched'] is None for frame in event['frames'])
        if unregistrable:
            excluded = [frame for frame in event['frames'] if not frame['included']]
            assert len(excluded) == 1 and excluded[0]['source'].endswith('pose sans etoiles.fits')
            assert excluded[0]['rejection_reason'] is None
        from lib.processing_html import ProcessingHtmlReport
        ProcessingHtmlReport(work).generate()
        page = (registration_dir/'index.html').read_text()
        assert 'Données détaillées Sirilpy non enregistrées' not in page
        assert 'pair_matched</a>' in page and 'inliers</a>' in page
        if unregistrable:
            assert 'EXCLUE' in page and 'Motif détaillé non exposé' in page
        assert not list(work.rglob('*.sps')) and not list(work.rglob('*.ssf'))
        assert list(work.rglob('*.commands.json'))
        pixels = fits.getdata(result)
        assert np.isfinite(pixels).all() and pixels.max() > .1
        assert min(pixels.shape[-2:]) >= 500
        assert [path.read_bytes() for path in paths] == original
