"""Conservative sky samples for mosaic backgrounds, independently of overlaps."""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_dilation
from scipy.optimize import linprog
from scipy.sparse import csr_matrix, eye, hstack
from scipy.spatial import ConvexHull


def _lower_plane(points, values):
    """Fit the 20th percentile plane; extended positive sources have little weight."""
    design = np.column_stack([np.ones(len(points)), points])
    location = np.median(values)
    scale = max(float(np.ptp(values)), abs(float(location))*1e-7, 1e-12)
    n = len(values)
    constraints = hstack([csr_matrix(design), eye(n), -eye(n)], format='csr')
    result = linprog(np.r_[np.zeros(3), np.full(n, .2), np.full(n, .8)],
                     A_eq=constraints, b_eq=(values-location)/scale,
                     bounds=[(None, None)]*3+[(0, None)]*(2*n), method='highs')
    if not result.success:
        raise ValueError('Impossible de sélectionner le fond : ajustement quantile échoué')
    coefficients = result.x[:3]*scale
    coefficients[0] += location
    return design @ coefficients


def require_coverage(points):
    """Refuse a correction determined by a small patch or a single edge."""
    if len(points) < 12 or np.any(np.ptp(points, axis=0) < 1.2):
        raise ValueError('Fond insuffisant ou mal réparti ; fournir un masque adapté ou utiliser --mosaic-gradient-mode relative')
    if np.linalg.matrix_rank(np.column_stack([np.ones(len(points)), points])) < 3:
        raise ValueError('Les échantillons de fond sont alignés')
    if ConvexHull(points).volume < 1.0:
        raise ValueError('Les échantillons de fond couvrent trop peu du panneau')


def select_background(array, density, tolerance=2., border=.02, growth=1,
                      exclusion=None, keep_all=False):
    """Measure clipped patch medians and mask extended positive structures.

    Coordinates are pixel centres. RGB uses the union of source masks: a source
    detected in a single channel is excluded from the background in all channels.
    Masking cannot distinguish arbitrary diffuse emission from instrumental sky;
    callers can supply an explicit exclusion mask in the panel's pixel grid.
    """
    channels, height, width = array.shape
    nx = min(density, max(2, width//5))
    ny = min(max(2, round(density*height/width)), max(2, height//5))
    radius = max(1, min(width//nx, height//ny)//4)
    margin = max(radius+2, int(np.ceil(min(height, width)*border)))
    if 2*margin >= min(height, width):
        raise ValueError('Panneau trop petit pour échantillonner le fond')
    xs = np.linspace(margin, width-1-margin, nx)
    ys = np.linspace(margin, height-1-margin, ny)
    xx, yy = np.meshgrid(xs, ys)
    pixels = np.rint(np.column_stack([xx.ravel(), yy.ravel()]))
    points = (pixels-np.array([width-1, height-1])/2)/(np.array([width-1, height-1])/2)
    values = np.full((channels, len(pixels)), np.nan)
    errors = np.full_like(values, np.nan)
    valid = np.ones(len(pixels), dtype=bool)
    for index, (x, y) in enumerate(np.rint(pixels).astype(int)):
        rows, columns = slice(y-radius, y+radius+1), slice(x-radius, x+radius+1)
        if exclusion is not None and exclusion[rows, columns].any():
            valid[index] = False
            continue
        for channel in range(channels):
            patch = array[channel, rows, columns].ravel()
            finite = patch[np.isfinite(patch) & (patch != 0)]
            if len(finite) < .95*len(patch):
                valid[index] = False
                continue
            median = np.median(finite)
            sigma = 1.4826*np.median(np.abs(finite-median))
            floor = max(abs(float(median))*1e-7, 1e-12)
            clipped = finite[np.abs(finite-median) <= max(3*sigma, floor)]
            values[channel, index] = np.median(clipped)
            errors[channel, index] = max(1.253*sigma/np.sqrt(len(clipped)), floor)
    valid &= np.all(np.isfinite(values), axis=0)
    require_coverage(points[valid])
    source = np.zeros(len(pixels), dtype=bool)
    if not keep_all:
        for channel in range(channels):
            model = _lower_plane(points[valid], values[channel, valid])
            residual = values[channel, valid]-model
            # Estimate scatter from the lower half, avoiding the bright tail.
            median = np.median(residual)
            lower_sigma = max((median-np.percentile(residual, 16)),
                              float(np.median(errors[channel, valid])))
            clean = residual <= median+tolerance*lower_sigma
            # Refine on sky candidates only; never bring rejected sources back.
            design = np.column_stack([np.ones(valid.sum()), points[valid]])
            for _ in range(8):
                if clean.sum() < 12:
                    break
                fit = np.linalg.lstsq(design[clean], values[channel, valid][clean], rcond=None)[0]
                residual = values[channel, valid]-design@fit
                center = np.median(residual[clean])
                sigma = max(1.4826*np.median(np.abs(residual[clean]-center)),
                            float(np.median(errors[channel, valid][clean])))
                newclean = clean & (residual <= center+tolerance*sigma)
                if np.array_equal(clean, newclean):
                    break
                clean = newclean
            source[np.flatnonzero(valid)] |= ~clean
        if growth:
            source = binary_dilation(source.reshape(ny, nx), iterations=growth).ravel()
    keep = valid & ~source
    require_coverage(points[keep])
    return dict(pixels=pixels, normalized=points, values=values, valid=valid,
                source=source, keep=keep, shape=(ny, nx), xs=xs, ys=ys,
                radius=radius, margin=margin)


def allowed_positions(samples, x, y):
    """Exclude overlap points in any rejected sky cell, including masked padding."""
    ix = np.abs(x[:, None]-samples['xs']).argmin(axis=1)
    iy = np.abs(y[:, None]-samples['ys']).argmin(axis=1)
    keep = samples['keep'].reshape(samples['shape'])[iy, ix]
    return (keep & (x >= samples['xs'][0]) & (x <= samples['xs'][-1])
            & (y >= samples['ys'][0]) & (y <= samples['ys'][-1]))


def write_preview(array, samples, retained, overlaps, path):
    """Show fit points with distinct, outlined symbols at preview resolution."""
    height, width = array.shape[-2:]
    stride = max(1, int(np.ceil(max(height, width)/1500)))
    # Reduce before combining channels to avoid a full-size temporary RGB copy.
    reduced = array[:, ::stride, ::stride]
    finite_pixels = np.isfinite(reduced)
    image = np.where(finite_pixels, reduced, 0).sum(axis=0)/np.maximum(finite_pixels.sum(axis=0), 1)
    finite = image[np.isfinite(image) & (image != 0)]
    low, high = np.percentile(finite, [1, 99]) if len(finite) else (0, 1)
    gray = (np.clip(np.nan_to_num((image-low)/max(high-low, 1e-12)), 0, 1)*255).astype(np.uint8)
    picture = Image.fromarray(gray).convert('RGB')
    draw = ImageDraw.Draw(picture)
    # Draw junctions first so a sky circle or rejection cross stays legible
    # when its centre coincides with an overlap sample.
    for x, y in np.asarray(overlaps).reshape(-1, 2)/stride:
        draw.ellipse((x-3, y-3, x+3, y+3), fill='black')
        draw.ellipse((x-2, y-2, x+2, y+2), fill='#22ddeb')
    used = np.any(retained, axis=0)
    for index, (x, y) in enumerate(samples['pixels']/stride):
        color = '#48ed65' if used[index] else '#f5a442' if samples['source'][index] else '#b8b8b8'
        if used[index]:
            draw.ellipse((x-7, y-7, x+7, y+7), outline='black', width=4)
            draw.ellipse((x-6, y-6, x+6, y+6), outline=color, width=2)
        else:
            for line in ((x-5, y-5, x+5, y+5), (x-5, y+5, x+5, y-5)):
                draw.line(line, fill='black', width=4)
                draw.line(line, fill=color, width=2)
    canvas = Image.new('RGB', (max(picture.width, 680), picture.height+40), '#161616')
    canvas.paste(picture, (0, 40))
    legend = ImageDraw.Draw(canvas)
    legend.text((8, 4), 'Cercle vert: fond retenu | Point cyan: raccord | Croix orange: objet exclu', fill='white')
    legend.text((8, 21), 'Croix grise: invalide, masque ou rejet final | Retenus dans au moins un canal RGB', fill='white')
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path)
