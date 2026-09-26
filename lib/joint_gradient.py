"""Panel sky correction constrained by source-free WCS overlaps."""
from contextlib import ExitStack
from pathlib import Path
import json

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from scipy.ndimage import map_coordinates, gaussian_filter, binary_dilation
from scipy.spatial.distance import cdist
from lib.background_samples import select_background, allowed_positions, require_coverage, write_preview


def _normalized_wcs_header(header):
    """Modernise le nom du référentiel sur une copie, sans masquer les erreurs WCS."""
    normalized = header.copy()
    if 'RADECSYS' in normalized:
        legacy = str(normalized['RADECSYS']).strip()
        if 'RADESYS' in normalized:
            if str(normalized['RADESYS']).strip().upper() != legacy.upper():
                raise ValueError('Référentiels WCS contradictoires : RADECSYS et RADESYS')
        else:
            normalized['RADESYS'] = (legacy, normalized.comments['RADECSYS'])
        del normalized['RADECSYS']
    return normalized


def _basis(points, method, order, knots, quadratic=False):
    x, y = points.T
    if method == 'polynomial':
        return np.column_stack([x**i*y**j for i in range(order+1) for j in range(order+1-i)])
    r = cdist(points, knots)
    radial = r*r*np.log(np.maximum(r, 1e-30))
    drift = [np.ones(len(x)), x, y]
    if quadratic:
        drift.extend([x*x, x*y, y*y])
    return np.column_stack([*drift, radial])


def match_backgrounds(processor, paths, directory, args=None):
    """Fit panel backgrounds and overlap differences together.

    Background mode anchors each panel on selected sky patches. Relative mode
    preserves the historical reference panel and only matches overlap differences.
    """
    paths = [Path(p).resolve() for p in paths]
    directory = Path(directory).resolve()
    mode = getattr(args, 'mosaic_gradient_mode', 'background')
    border = getattr(args, 'mosaic_gradient_border', .02)
    growth = getattr(args, 'mosaic_gradient_mask_growth', 1)
    tolerance = getattr(args, 'gradient_grid_tolerance', processor.grid_tolerance)
    mask_paths = getattr(args, 'mosaic_gradient_masks', None)
    if mode not in ('background', 'relative'):
        raise ValueError('Mode de gradient mosaïque invalide')
    if not np.isfinite(border) or not 0 <= border <= .2 or not isinstance(growth, int) or not 0 <= growth <= 5:
        raise ValueError('Marge ou dilatation du masque de gradient invalide')
    if not np.isfinite(tolerance) or tolerance <= 0:
        raise ValueError('Tolérance de sélection du fond invalide')
    if mask_paths is not None and len(mask_paths) != len(paths):
        raise ValueError('Fournir un masque FITS par panneau, dans le même ordre')
    if mode == 'relative' and mask_paths:
        raise ValueError('Les masques de fond nécessitent --mosaic-gradient-mode background')
    method = getattr(args, 'gradient_method', processor.method)
    smoothing = getattr(args, 'gradient_smoothing', processor.smoothing)
    density = getattr(args, 'gradient_samples_per_line', processor.samples_per_line)
    order = getattr(args, 'gradient_min_order', processor.min_polynomial_order)
    if method not in ('rbf', 'polynomial') or not np.isfinite(smoothing) or not 0 <= smoothing <= 1:
        raise ValueError('Paramètres du gradient conjoint invalides')
    if not isinstance(density, int) or density < 2 or not isinstance(order, int) or not 1 <= order <= 5:
        raise ValueError('Grille ou ordre polynomial invalide')
    if len(paths) < 2:
        raise ValueError('Le gradient conjoint nécessite au moins deux panneaux')
    with ExitStack() as stack:
        hdus = [stack.enter_context(fits.open(p, memmap=True)) for p in paths]
        arrays, wcs = [], []
        for p, h in zip(paths, hdus):
            a = h[0].data
            if a.ndim == 2:
                a = a[None]
            if a.ndim != 3 or a.shape[0] not in (1, 3):
                raise ValueError(f'FITS monochrome ou RGB attendu : {p}')
            w = WCS(_normalized_wcs_header(h[0].header), naxis=2).celestial
            if not w.has_celestial:
                raise ValueError(f'WCS céleste nécessaire au gradient conjoint : {p}')
            arrays.append(a)
            wcs.append(w)
        channels = arrays[0].shape[0]
        if any(a.shape[0] != channels for a in arrays):
            raise ValueError('Les panneaux doivent avoir le même nombre de canaux')
        background_samples = []
        external_exclusions = []
        if mode == 'background':
            for index, array in enumerate(arrays):
                exclusion = None
                if mask_paths is not None:
                    mask = fits.getdata(mask_paths[index])
                    if mask.shape != array.shape[-2:]:
                        raise ValueError('Le masque doit être 2D et avoir les dimensions du panneau')
                    exclusion = ~np.isfinite(mask) | (mask != 0)
                external_exclusions.append(binary_dilation(exclusion, iterations=8) if exclusion is not None else None)
                try:
                    background_samples.append(select_background(
                        array, density, tolerance, border, growth, exclusion,
                        getattr(args, 'gradient_keep_all_samples', processor.keep_all_samples)))
                except ValueError as error:
                    raise ValueError(f'{paths[index].name}: {error}') from error
        # All bases share the first panel tangent projection, avoiding RA wrap.
        corners = []
        for a, w in zip(arrays, wcs):
            height, width = a.shape[-2:]
            x = np.array([0, width-1, width-1, 0])
            y = np.array([0, 0, height-1, height-1])
            u, v = wcs[0].world_to_pixel_values(*w.pixel_to_world_values(x, y))
            corners.extend(zip(u, v))
        corners = np.asarray(corners)
        if not np.all(np.isfinite(corners)):
            raise ValueError('Projection WCS commune impossible')
        center = (corners.min(axis=0)+corners.max(axis=0))/2
        scale = np.maximum(np.ptp(corners, axis=0)/2, 1)
        knots = np.array([(x, y) for y in np.linspace(-1, 1, 4) for x in np.linspace(-1, 1, 4)])
        pairs = []
        degree = np.zeros(len(paths), dtype=int)
        pair_pixels = []
        for i in range(len(paths)):
            height, width = arrays[i].shape[-2:]
            # Oversample the grid so narrow overlaps still provide constraints.
            nx = min(width, max(40, density*4))
            ny = min(height, max(20, round(nx*height/width)))
            xx, yy = np.meshgrid(np.linspace(4, width-5, nx), np.linspace(4, height-5, ny))
            xx, yy = xx.ravel(), yy.ravel()
            world = wcs[i].pixel_to_world_values(xx, yy)
            for j in range(i+1, len(paths)):
                xj, yj = wcs[j].world_to_pixel_values(*world)
                hj, wj = arrays[j].shape[-2:]
                good = np.isfinite(xj) & np.isfinite(yj) & (xj >= 4) & (xj < wj-5) & (yj >= 4) & (yj < hj-5)
                if mode == 'background':
                    finite = np.flatnonzero(good)
                    good[finite] &= (allowed_positions(background_samples[i], xx[finite], yy[finite])
                                     & allowed_positions(background_samples[j], xj[finite], yj[finite]))
                    for panel, x, y in ((i, xx, yy), (j, xj, yj)):
                        if external_exclusions[panel] is not None:
                            good[finite] &= ~external_exclusions[panel][np.rint(y[finite]).astype(int),
                                                                       np.rint(x[finite]).astype(int)]
                if good.sum() < 20:
                    continue
                xi, yi, xj, yj = xx[good], yy[good], xj[good], yj[good]
                differences = []
                valid = np.ones(len(xi), dtype=bool)
                for c in range(channels):
                    # Matched small patches reduce sensitivity to PSF and subpixel shifts.
                    samples = []
                    for a, x, y in [(arrays[i][c], xi, yi), (arrays[j][c], xj, yj)]:
                        finite = np.isfinite(a) & (a != 0)
                        weight = gaussian_filter(finite.astype(np.float32), 2)
                        smooth = gaussian_filter(np.where(finite, a, 0).astype(np.float32), 2)
                        smooth /= np.maximum(weight, 1e-10)
                        values = map_coordinates(smooth, [y, x], order=1, mode='constant', cval=np.nan)
                        support = map_coordinates(weight, [y, x], order=1, mode='constant', cval=0)
                        valid &= np.isfinite(values) & (support > .99)
                        samples.append(values)
                    differences.append(samples[0]-samples[1])
                if valid.sum() < 20:
                    continue
                u, v = wcs[0].world_to_pixel_values(*wcs[i].pixel_to_world_values(xi[valid], yi[valid]))
                points = (np.column_stack([u, v])-center)/scale
                basis = _basis(points, method, order, knots, quadratic=mode == 'background')
                pairs.append((i, j, basis, np.asarray(differences)[:, valid]))
                degree[i] += valid.sum()
                degree[j] += valid.sum()
                pair_pixels.append((np.column_stack([xi[valid], yi[valid]]),
                                    np.column_stack([xj[valid], yj[valid]])))
        reference = int(np.argmax(degree))
        connected = {reference}
        for _ in paths:
            for i, j, _, _ in pairs:
                if i in connected or j in connected:
                    connected.update([i, j])
        if len(connected) != len(paths):
            raise ValueError('Recouvrements WCS insuffisants : tous les panneaux doivent être reliés ; aucune correction indépendante appliquée')
        nbase = pairs[0][2].shape[1]
        free = list(range(len(paths))) if mode == 'background' else [i for i in range(len(paths)) if i != reference]
        slots = {i: slice(k*nbase, (k+1)*nbase) for k, i in enumerate(free)}
        matrices, targets, weights, groups = [], [], [], []
        background_rows = []
        row_start = 0
        sky_level = None
        if mode == 'background':
            sample = background_samples[reference]
            sky_level = np.median(sample['values'][:, sample['keep']], axis=1)
            for i, sample in enumerate(background_samples):
                indices = np.flatnonzero(sample['keep'])
                x, y = sample['pixels'][indices].T
                u, v = wcs[0].world_to_pixel_values(*wcs[i].pixel_to_world_values(x, y))
                basis = _basis((np.column_stack([u, v])-center)/scale, method, order, knots, quadratic=mode == 'background')
                row = np.zeros((len(basis), nbase*len(free)))
                row[:, slots[i]] = basis
                matrices.append(row)
                targets.append(sample['values'][:, indices]-sky_level[:, None])
                weights.append(np.full(len(row), 1/np.sqrt(len(row))))
                section = slice(row_start, row_start+len(row))
                groups.append(section)
                background_rows.append((section, indices))
                row_start += len(row)
        pair_rows = []
        for i, j, b, d in pairs:
            row = np.zeros((len(b), nbase*len(free)))
            if i in slots:
                row[:, slots[i]] = b
            if j in slots:
                row[:, slots[j]] = -b
            matrices.append(row)
            targets.append(d)
            weights.append(np.full(len(row), 1/np.sqrt(len(row))) if mode == 'background' else np.ones(len(row)))
            section = slice(row_start, row_start+len(row))
            groups.append(section)
            pair_rows.append(section)
            row_start += len(row)
        design = np.vstack(matrices)
        target = np.concatenate(targets, axis=1)
        weights = np.concatenate(weights)
        # Penalize spline residuals, leaving the low-order instrumental trend
        # free (quadratic in background mode, affine in historical relative mode).
        penalty = np.zeros(nbase)
        if method == 'rbf':
            penalty[6 if mode == 'background' else 3:] = max(smoothing, 1e-4)*float(weights@weights)
        else:
            powers = [i+j for i in range(order+1) for j in range(order+1-i)]
            penalty = np.array([.01*float(weights@weights) if p > (2 if mode == 'background' else 1) else 0 for p in powers])
        ridge = np.diag(np.sqrt(np.tile(penalty, len(free))))
        coefficients = np.zeros((len(paths), channels, nbase))
        residuals = []
        background_metrics = [[] for _ in paths]
        retained = []
        for c in range(channels):
            mask = np.ones(len(design), dtype=bool)
            for _ in range(6):
                matrix = np.vstack([design[mask]*weights[mask, None], ridge])
                solution, _, rank, _ = np.linalg.lstsq(matrix, np.r_[target[c, mask]*weights[mask], np.zeros(len(ridge))], rcond=None)
                if rank < matrix.shape[1]:
                    raise ValueError('Recouvrements insuffisants pour contraindre le modèle de gradient')
                residual = target[c] - design @ solution
                newmask = mask.copy()
                for section in groups:
                    local_mask = mask[section]
                    if local_mask.sum() < 4:
                        continue
                    local = residual[section]
                    median = np.median(local[local_mask])
                    sigma = 1.4826*np.median(np.abs(local[local_mask]-median))
                    newmask[section] = local_mask & (np.abs(local-median) <= max(4*sigma, 1e-8))
                if np.array_equal(newmask, mask):
                    break
                if newmask.sum() < matrix.shape[1]:
                    break
                mask = newmask
            # Resolve once on the final retained sample set.
            matrix = np.vstack([design[mask]*weights[mask, None], ridge])
            solution, _, rank, _ = np.linalg.lstsq(matrix, np.r_[target[c, mask]*weights[mask], np.zeros(len(ridge))], rcond=None)
            if rank < matrix.shape[1]:
                raise ValueError('Recouvrements insuffisants après rejet des valeurs aberrantes')
            residual = target[c] - design @ solution
            for i, slot in slots.items():
                coefficients[i, c] = solution[slot]
            if mode == 'background':
                for panel, (sample, (section, indices)) in enumerate(zip(background_samples, background_rows)):
                    require_coverage(sample['normalized'][indices[mask[section]]])
                    before = sample['values'][c, indices[mask[section]]]
                    after = residual[section][mask[section]]
                    background_metrics[panel].append(dict(
                        used_samples=len(before),
                        before_rms=float(np.std(before)), after_rms=float(np.std(after))))
            # Every channel must still have reliable junction constraints.
            connected = {reference}
            for _ in paths:
                for (i, j, _, _), section in zip(pairs, pair_rows):
                    if mask[section].sum() >= 20 and (i in connected or j in connected):
                        connected.update([i, j])
            if len(connected) != len(paths):
                raise ValueError('Recouvrements insuffisants après rejet des échantillons')
            retained.append(mask.copy())
            overlap_mask = mask.copy()
            if background_rows:
                overlap_mask[:background_rows[-1][0].stop] = False
            residuals.append({'before_rms': float(np.sqrt(np.mean(target[c, overlap_mask]**2))),
                              'after_rms': float(np.sqrt(np.mean(residual[overlap_mask]**2))),
                              'used_samples': int(overlap_mask.sum()),
                              'background_samples': int(mask.sum()-overlap_mask.sum())})
        directory.mkdir(parents=True, exist_ok=True)
        report = {'method': method, 'mode': 'panel_background_and_overlap' if mode == 'background' else 'joint_overlap', 'reference_image': str(paths[reference]),
                  'smoothing': smoothing, 'samples_per_line': density, 'polynomial_order': order if method == 'polynomial' else None,
                  'channels': residuals,
                  'common_gradient_removed': mode == 'background',
                  'reference_role': 'sky_level_only' if mode == 'background' else 'unchanged_panel',
                  'sky_level': sky_level.tolist() if sky_level is not None else None,
                  'grid_tolerance': tolerance, 'border_fraction': border, 'mask_growth_cells': growth}
        retained = np.asarray(retained)
        sample_points = [[] for _ in paths]
        for (i, j, _, _), (pi, pj), section in zip(pairs, pair_pixels, pair_rows):
            used = np.any(retained[:, section], axis=0)
            sample_points[i].extend(pi[used].tolist())
            sample_points[j].extend(pj[used].tolist())
        report['overlaps'] = [dict(panels=[i+1, j+1], candidates=len(b), samples=len(b),
                                   used_per_channel=retained[:, section].sum(axis=1).tolist())
                              for (i, j, b, _), section in zip(pairs, pair_rows)]
        results = []
        for i, (path, h) in enumerate(zip(paths, hdus)):
            output = directory/f'{i+1:02d}_gradient_{path.stem}_gradient_corrected.fits'
            if output in paths:
                raise ValueError('Une sortie de gradient ne peut pas remplacer un panneau source')
            corrected = arrays[i].astype(np.float32, copy=True)
            height, width = corrected.shape[-2:]
            for start in range(0, height*width, 8192):
                index = np.arange(start, min(start+8192, height*width))
                y, x = np.divmod(index, width)
                u, v = wcs[0].world_to_pixel_values(*wcs[i].pixel_to_world_values(x, y))
                b = _basis((np.column_stack([u,v])-center)/scale, method, order, knots, quadratic=mode == 'background')
                for c in range(channels):
                    values = corrected[c].flat[start:start+len(index)]
                    valid = np.isfinite(values) & (values != 0)
                    correction = (b@coefficients[i,c]).astype(np.float32)
                    if not np.all(np.isfinite(correction[valid])):
                        raise ValueError('Correction non finie dans le domaine du panneau')
                    values[valid] -= correction[valid]
                    corrected[c].flat[start:start+len(index)] = values
            saved = fits.HDUList([item.copy() for item in h])
            saved[0].header = _normalized_wcs_header(saved[0].header)
            saved[0].data = corrected[0] if h[0].data.ndim == 2 else corrected
            saved[0].header.add_history('Source-masked panel backgrounds and WCS overlaps' if mode == 'background'
                                        else 'Joint background matching from WCS overlaps; reference panel retained')
            saved.writeto(output, overwrite=True, checksum=True)
            saved.close()
            result = {'image_path': str(path), 'output_image': str(output), 'method': method,
                      'correction': report, 'coefficients': coefficients[i].tolist()}
            selected = None
            if mode == 'background':
                sample = background_samples[i]
                section, indices = background_rows[i]
                selected = np.zeros((channels, len(sample['pixels'])), dtype=bool)
                selected[:, indices] = retained[:, section]
                result['background_selection'] = dict(
                    candidates=sample['pixels'].tolist(), patch_radius=sample['radius'],
                    channels=background_metrics[i],
                    source_excluded=sample['source'].tolist(), valid=sample['valid'].tolist(),
                    used_per_channel=selected.tolist(),
                    used_counts=selected.sum(axis=1).tolist(),
                    overlap_used_points=sample_points[i],
                    exclusion_mask_path=str(Path(mask_paths[i]).resolve()) if mask_paths else None)
            if getattr(args, 'gradient_measurement_image', processor.create_measurement_image):
                png = directory/f'{i+1:02d}_gradient_{path.stem}_measurement_points.png'
                if mode == 'background':
                    write_preview(arrays[i], sample, selected, sample_points[i], png)
                elif processor.create_measurement_points_image(path, sample_points[i], png) is None:
                    raise RuntimeError('Impossible de créer les points de mesure du gradient conjoint')
                result['measurement_image_path'] = str(png)
            individual = directory/f'{i+1:02d}_gradient.json'
            result['report_saved_to'] = str(individual)
            individual.write_text(json.dumps(result, indent=2), encoding='utf-8')
            results.append(result)
        (directory/'joint_gradient.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        return results
