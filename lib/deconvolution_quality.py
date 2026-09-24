"""Sélection explicite des étoiles PSF et contrôles appariés de déconvolution.

Les seuils d'anneaux et de bruit sont des heuristiques d'audit, pas une preuve
que tous les artefacts visuels sont absents.
"""
from pathlib import Path
import json
import numpy as np
from astropy.io import fits
from scipy.ndimage import median_filter, gaussian_filter, maximum_filter, label
from scipy.spatial import cKDTree


def _normalized_data(data):
    """Échelle Siril [0, 1] pour les FITS entiers, floats laissés inchangés."""
    scale = float(np.iinfo(data.dtype).max) if np.issubdtype(data.dtype, np.integer) else 1.0
    return np.asarray(data, dtype=np.float32)/scale



def inspect_psf_stamp(patch, fwhm, *, min_roundness=.8):
    """Mesure les pixels, indépendamment du profil annoncé par le catalogue.

    Composantes et maxima détectent les compagnons ; les moments à plusieurs
    isophotes vérifient le coeur et les ailes. Les seuils restent heuristiques.
    """
    patch = np.asarray(patch, dtype=float)
    if not np.isfinite(patch).all():
        return dict(accepted=False, reasons=['nonfinite_pixels'])
    yy, xx = np.indices(patch.shape)
    cy, cx = (np.array(patch.shape)-1)/2
    rr = np.hypot(xx-cx, yy-cy)
    border = rr >= .8*min(cx, cy)
    sky = float(np.median(patch[border]))
    noise = float(1.4826*np.median(abs(patch[border]-sky)))
    signal = gaussian_filter(patch-sky, .65)
    central = rr <= max(2, fwhm/2)
    peak = float(signal[central].max())
    reasons, shapes = [], []
    if peak <= max(8*noise, 1e-8):
        return dict(accepted=False, reasons=['low_signal'], sky=sky, noise=noise)
    peak_y, peak_x = np.unravel_index(np.where(central, signal, -np.inf).argmax(), patch.shape)
    if np.hypot(peak_x-cx, peak_y-cy) > 1.5:
        reasons.append('off_center')
    threshold = max(.03*peak, 5*noise)
    maxima = (signal == maximum_filter(signal, size=3)) & (signal > threshold)
    separated = maxima & (np.hypot(xx-peak_x, yy-peak_y) > max(2, .7*fwhm))
    if separated.any():
        reasons.append('secondary_peak')
    for fraction in (.15, .35, .6):
        level = max(fraction*peak, 5*noise)
        components, _ = label(signal > level)
        central_id = components[peak_y, peak_x]
        mask = (components == central_id) & (components > 0)
        other = (components > 0) & ~mask
        if np.count_nonzero(other) >= 3:
            reasons.append('disconnected_source')
        if mask.sum() < 4:
            reasons.append('undersampled_profile')
            continue
        weights = np.maximum(signal[mask]-level, 0)
        weights /= weights.sum()
        x, y = xx[mask], yy[mask]
        mx, my = np.sum(weights*x), np.sum(weights*y)
        dx, dy = x-mx, y-my
        covariance = [[np.sum(weights*dx*dx), np.sum(weights*dx*dy)],
                      [np.sum(weights*dx*dy), np.sum(weights*dy*dy)]]
        eigen = np.linalg.eigvalsh(covariance)
        ratio = float(np.sqrt(max(0, eigen[0])/max(eigen[1], 1e-12)))
        shapes.append(dict(level_fraction=fraction, roundness=ratio,
                           centroid_shift_px=float(np.hypot(mx-cx, my-cy))))
        if ratio < min_roundness:
            reasons.append('pixel_elongation')
        if np.hypot(mx-cx, my-cy) > 1.5:
            reasons.append('asymmetric_profile')
    # Une tache diffuse peut ne pas former de pic net : compare chaque pixel
    # au profil radial médian, sans imposer un modèle gaussien aux ailes Moffat.
    radial = np.hypot(xx-peak_x, yy-peak_y).astype(int)
    model = np.zeros_like(signal)
    for radius in np.unique(radial):
        mask = radial == radius
        model[mask] = np.median(signal[mask])
    excess = (signal-model > max(.025*peak, 5*noise)) & (radial > 1.5*fwhm)
    blobs, count = label(excess)
    largest = max((int(np.count_nonzero(blobs == i)) for i in range(1, count+1)), default=0)
    if largest >= 4:
        reasons.append('diffuse_contamination')
    return dict(accepted=not reasons, reasons=sorted(set(reasons)), sky=sky, noise=noise,
                peak=peak, shapes=shapes, secondary_peaks=int(separated.sum()),
                largest_excess_region_px=largest)


def select_psf_stars(stars, image_path, sheet_path, *, quantile=.35, roundness=.8,
                     min_amplitude=.01, max_amplitude=.7, min_stars=3, max_stars=60, layer=0):
    """Construit une planche ne contenant que les découpes des étoiles retenues."""
    with fits.open(image_path, memmap=False) as hdul:
        data = _normalized_data(hdul[0].data)
        header = hdul[0].header
    if data.ndim == 3:
        data = data[layer]
    # Siril exprime X/Y à partir du centre (.5), Y depuis le bas de l'image.
    height, width = data.shape
    candidates = [s for s in stars if s['layer'] == layer and s['fwhm'] >= 1.5
                  and min(s['fwhm_x'], s['fwhm_y'])/max(s['fwhm_x'], s['fwhm_y']) >= roundness
                  and min_amplitude <= s.get('amplitude', -1) <= max_amplitude]
    if not candidates:
        raise ValueError('Aucune étoile PSF dans les critères amplitude/rondeur/taille')
    limit = float(np.quantile([s['fwhm'] for s in candidates], quantile))
    fine = sorted((s for s in candidates if s['fwhm'] <= limit), key=lambda s: (s['fwhm'], s['id']))
    tree = cKDTree([[s['x'], s['y']] for s in stars])
    radius = max(8, int(np.ceil(4*limit+3)))
    patches, selected, inspections = [], [], []
    for star in fine:
        entry = dict(id=star['id'], accepted=False, reasons=[])
        inspections.append(entry)
        if len(tree.query_ball_point([star['x'], star['y']], 2*radius)) != 1:
            entry['reasons'] = ['catalog_neighbor']
            continue
        x = int(round(star['x']-.5))
        y = int(round(height-star['y']-.5))
        if x-radius < 0 or y-radius < 0 or x+radius >= width or y+radius >= height:
            entry['reasons'] = ['image_boundary']
            continue
        patch = data[y-radius:y+radius+1, x-radius:x+radius+1]
        entry.update(inspect_psf_stamp(patch, star['fwhm'], min_roundness=roundness))
        if not entry['accepted']:
            continue
        # Vérifie qu'on prélève bien une étoile au centre des coordonnées exportées.
        sky = float(np.median(np.concatenate((patch[0], patch[-1], patch[:, 0], patch[:, -1]))))
        center_peak = float(patch[radius-2:radius+3, radius-2:radius+3].max()-sky)
        if center_peak < .2*star['amplitude'] or patch.max()-sky > 1.5*center_peak:
            entry.update(accepted=False, reasons=['catalog_peak_mismatch'])
            continue
        # Normalisation photométrique seulement : les profils gardent leur largeur.
        # makepsf stars impose sa propre plage d’amplitude : on place chaque étoile à 0.3.
        patches.append((patch-sky)*(0.3/center_peak)+.001)
        selected.append(star)
        if len(selected) >= max_stars:
            break
    audit_path = Path(sheet_path).with_suffix('.json')
    audit = dict(selected_count=len(selected), inspected_count=len(inspections),
                 inspections=inspections, min_pixel_roundness=roundness)
    audit_path.write_text(json.dumps(audit, indent=2), encoding='utf-8')
    if len(selected) < min_stars:
        raise ValueError(f'PSF : seulement {len(selected)} étoiles fines et isolées ; minimum {min_stars}')
    tile = 2*radius+1+10
    columns = int(np.ceil(np.sqrt(len(selected))))
    sheet = np.full((columns*tile, columns*tile), .001, dtype=np.float32)
    for i, patch in enumerate(patches):
        y, x = (i//columns)*tile+5, (i%columns)*tile+5
        sheet[y:y+patch.shape[0], x:x+patch.shape[1]] = patch
    sheet_header = fits.Header()
    if 'ROWORDER' in header:
        sheet_header['ROWORDER'] = header['ROWORDER']
    fits.writeto(sheet_path, sheet, sheet_header, overwrite=True)
    return dict(selected_count=len(selected), candidate_count=len(candidates), fwhm_limit_px=limit,
                min_amplitude=min_amplitude, max_amplitude=max_amplitude, min_roundness=roundness,
                fine_quantile=quantile, sheet_path=str(sheet_path), stars=selected,
                stamp_audit_path=str(audit_path), stamp_inspections=inspections)


def artifact_metrics(before_path, after_path, pairs):
    """Bruit sur les mêmes pixels de ciel, anneaux sur les mêmes étoiles et tous canaux."""
    original = _normalized_data(fits.getdata(before_path))
    changed = _normalized_data(fits.getdata(after_path))
    if original.shape != changed.shape or not np.isfinite(changed).all():
        raise ValueError('Image candidate de dimensions différentes ou non finie')
    if original.ndim == 2:
        original, changed = original[None], changed[None]
    channels = []
    for before, after in zip(original, changed):
        finite = np.isfinite(before)
        if not finite.any():
            raise ValueError('Image originale sans pixels finis')
        # Masque fixe : pixels faibles, déterminés exclusivement sur l'original.
        mask = finite & (before <= np.percentile(before[finite], 40))
        mask[:2] = mask[-2:] = False
        mask[:, :2] = mask[:, -2:] = False
        def noise(plane):
            high = plane-median_filter(plane, size=3)
            values = high[mask]
            return float(1.4826*np.median(abs(values-np.median(values))))
        nb, na = noise(before), noise(after)
        ratio = na/max(nb, np.finfo(np.float32).eps)
        scores = []
        for pair in pairs:
            radius = max(8, int(np.ceil(3*pair['fwhm_before_px'])))
            x = int(round(pair['x_before']-.5))
            y = int(round(before.shape[0]-pair['y_before']-.5))
            if min(x, y) < radius or x+radius >= before.shape[1] or y+radius >= before.shape[0]:
                continue
            b = before[y-radius:y+radius+1, x-radius:x+radius+1]
            a = after[y-radius:y+radius+1, x-radius:x+radius+1]
            yy, xx = np.indices(b.shape)
            rr = np.hypot(xx-radius, yy-radius)
            outer = rr >= 2.5*pair['fwhm_before_px']
            bins = np.arange(max(1, .8*pair['fwhm_before_px']), radius-1, 1)
            def rings(stamp):
                bg = float(np.median(stamp[outer]))
                peak = max(float(stamp[radius-1:radius+2, radius-1:radius+2].max()-bg), 1e-8)
                profile = np.array([np.median(stamp[(rr >= r) & (rr < r+1)])-bg for r in bins])/peak
                dark = max(0., -float(profile.min()))
                bump = max(0., float(np.max(profile-np.minimum.accumulate(profile))))
                return max(dark, bump)
            if np.isfinite(b).all() and outer.sum() >= 12 and len(bins) >= 3:
                rb, ra = rings(b), rings(a)
                scores.append(dict(reference_id=pair['reference_id'], before=rb, after=ra, added=max(0, ra-rb)))
        if not scores or mask.sum() < 100:
            raise ValueError('Pas assez de ciel ou d’étoiles intérieures pour contrôler les artefacts')
        channels.append(dict(noise_before=nb, noise_after=na, noise_ratio=ratio,
                             background_pixels=int(mask.sum()), rings=scores,
                             ring_fraction=float(np.mean([s['added'] > .02 for s in scores]))))
    return dict(channels=channels, max_noise_ratio=max(c['noise_ratio'] for c in channels),
                max_ring_fraction=max(c['ring_fraction'] for c in channels))


def assess_quality(comparison, artifacts, *, min_gain=.02, max_noise=1.15,
                   max_rings=.1, roundness_tolerance=.01):
    """Sépare efficacité et artefacts pour piloter l'arrêt de la recherche de pas."""
    if comparison['status'] != 'validated':
        return dict(accepted=False, artifact_failure=False, reasons=['insufficient_matches'])
    reasons = []
    before, after = comparison['before_fwhm_px'], comparison['after_fwhm_px']
    if after['mean'] > before['mean']*(1-min_gain) or comparison['paired_ratio']['median'] > 1-min_gain:
        reasons.append('insufficient_sharpening')
    rb = comparison['before_roundness']['mean']
    ra = comparison['after_roundness']['mean']
    if ra < rb-roundness_tolerance:
        reasons.append('roundness_degraded')
    if artifacts['max_noise_ratio'] > max_noise:
        reasons.append('noise_amplification')
    if artifacts['max_ring_fraction'] > max_rings:
        reasons.append('stellar_rings')
    artifact_failure = any(r in reasons for r in ('roundness_degraded', 'noise_amplification', 'stellar_rings'))
    return dict(accepted=not reasons, artifact_failure=artifact_failure, reasons=reasons,
                thresholds=dict(min_gain=min_gain, max_noise_ratio=max_noise,
                                max_ring_fraction=max_rings, roundness_tolerance=roundness_tolerance))
