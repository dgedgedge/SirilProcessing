"""Relative background matching on WCS overlaps, without modelling the sky signal."""
from contextlib import ExitStack
from pathlib import Path
import json

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from scipy.ndimage import map_coordinates, gaussian_filter
from scipy.spatial.distance import cdist


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


def _basis(points, method, order, knots):
    x, y = points.T
    if method == 'polynomial':
        return np.column_stack([x**i*y**j for i in range(order+1) for j in range(order+1-i)])
    r = cdist(points, knots)
    radial = r*r*np.log(np.maximum(r, 1e-30))
    return np.column_stack([np.ones(len(x)), x, y, radial])


def match_backgrounds(processor, paths, directory, args=None):
    """Fit all panel corrections simultaneously; keep the best-connected panel fixed.

    Only differences at matching sky positions constrain the model. An absolute
    gradient common to every panel is unobservable and deliberately retained.
    """
    paths = [Path(p).resolve() for p in paths]
    directory = Path(directory).resolve()
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
        sample_points = [[] for _ in paths]
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
                basis = _basis(points, method, order, knots)
                pairs.append((i, j, basis, np.asarray(differences)[:, valid]))
                degree[i] += valid.sum()
                degree[j] += valid.sum()
                sample_points[i].extend(zip(xi[valid].astype(int).tolist(), yi[valid].astype(int).tolist()))
                sample_points[j].extend(zip(xj[valid].astype(int).tolist(), yj[valid].astype(int).tolist()))
        reference = int(np.argmax(degree))
        connected = {reference}
        for _ in paths:
            for i, j, _, _ in pairs:
                if i in connected or j in connected:
                    connected.update([i, j])
        if len(connected) != len(paths):
            raise ValueError('Recouvrements WCS insuffisants : tous les panneaux doivent être reliés ; aucune correction indépendante appliquée')
        nbase = pairs[0][2].shape[1]
        free = [i for i in range(len(paths)) if i != reference]
        slots = {i: slice(k*nbase, (k+1)*nbase) for k, i in enumerate(free)}
        matrices, targets = [], []
        for i, j, b, d in pairs:
            row = np.zeros((len(b), nbase*len(free)))
            if i in slots:
                row[:, slots[i]] = b
            if j in slots:
                row[:, slots[j]] = -b
            matrices.append(row)
            targets.append(d)
        design = np.vstack(matrices)
        target = np.concatenate(targets, axis=1)
        # Penalize curvature, not panel offsets or linear ramps. This stabilizes
        # extrapolation beyond overlaps without fitting astronomical structures.
        penalty = np.zeros(nbase)
        if method == 'rbf':
            penalty[3:] = max(smoothing, 1e-4)*len(design)
        else:
            powers = [i+j for i in range(order+1) for j in range(order+1-i)]
            penalty = np.array([.01*len(design) if p > 1 else 0 for p in powers])
        ridge = np.diag(np.sqrt(np.tile(penalty, len(free))))
        coefficients = np.zeros((len(paths), channels, nbase))
        residuals = []
        for c in range(channels):
            mask = np.ones(len(design), dtype=bool)
            for _ in range(6):
                matrix = np.vstack([design[mask], ridge])
                solution, _, rank, _ = np.linalg.lstsq(matrix, np.r_[target[c, mask], np.zeros(len(ridge))], rcond=None)
                if rank < matrix.shape[1]:
                    raise ValueError('Recouvrements insuffisants pour contraindre le modèle de gradient')
                residual = target[c] - design @ solution
                median = np.median(residual[mask])
                sigma = 1.4826*np.median(np.abs(residual[mask]-median))
                newmask = np.abs(residual-median) <= max(4*sigma, 1e-8)
                if np.array_equal(newmask, mask):
                    break
                if newmask.sum() < matrix.shape[1]:
                    break
                mask = newmask
            # Resolve once on the final retained sample set.
            matrix = np.vstack([design[mask], ridge])
            solution, _, rank, _ = np.linalg.lstsq(matrix, np.r_[target[c, mask], np.zeros(len(ridge))], rcond=None)
            if rank < matrix.shape[1]:
                raise ValueError('Recouvrements insuffisants après rejet des valeurs aberrantes')
            residual = target[c] - design @ solution
            for i, slot in slots.items():
                coefficients[i, c] = solution[slot]
            residuals.append({'before_rms': float(np.sqrt(np.mean(target[c, mask]**2))),
                              'after_rms': float(np.sqrt(np.mean(residual[mask]**2))),
                              'used_samples': int(mask.sum())})
        directory.mkdir(parents=True, exist_ok=True)
        report = {'method': method, 'mode': 'joint_overlap', 'reference_image': str(paths[reference]),
                  'smoothing': smoothing, 'samples_per_line': density, 'polynomial_order': order if method == 'polynomial' else None,
                  'channels': residuals, 'overlaps': [{'panels': [i+1, j+1], 'samples': len(b)} for i,j,b,_ in pairs],
                  'common_gradient_removed': False}
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
                b = _basis((np.column_stack([u,v])-center)/scale, method, order, knots)
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
            saved[0].header.add_history('Joint background matching from WCS overlaps; reference panel retained')
            saved.writeto(output, overwrite=True, checksum=True)
            saved.close()
            result = {'image_path': str(path), 'output_image': str(output), 'method': method,
                      'correction': report, 'coefficients': coefficients[i].tolist()}
            if getattr(args, 'gradient_measurement_image', processor.create_measurement_image):
                png = directory/f'{i+1:02d}_gradient_{path.stem}_measurement_points.png'
                if processor.create_measurement_points_image(path, sample_points[i], png) is None:
                    raise RuntimeError('Impossible de créer les points de mesure du gradient conjoint')
                result['measurement_image_path'] = str(png)
            individual = directory/f'{i+1:02d}_gradient.json'
            result['report_saved_to'] = str(individual)
            individual.write_text(json.dumps(result, indent=2), encoding='utf-8')
            results.append(result)
        (directory/'joint_gradient.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        return results
