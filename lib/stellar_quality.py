"""Non-parametric stellar profiles, measured on native SequenceImage pixels.

Windowed second moments follow the principle documented by SExtractor;
R80 is an aperture curve-of-growth radius. These are measurements, not a
physical diagnosis of tracking errors. Selection belongs in quality_filter.
"""
import numpy as np
from astropy.io import fits
from scipy.ndimage import gaussian_filter, maximum_filter


def _analysis_plane(image):
    with fits.open(image.processing_path, memmap=False) as hdus:
        data = hdus[0].data
        header = hdus[0].header
        if data is None or data.ndim not in (2, 3):
            raise ValueError('Image FITS 2D ou RGB requise')
        saturation = header.get('SATURATE')
        if saturation is None and np.issubdtype(data.dtype, np.integer):
            saturation = np.iinfo(data.dtype).max
        # Siril normalized floats may explicitly contain a clipped plateau at 1.
        finite = np.isfinite(data)
        if saturation is None and finite.any() and np.max(data[finite]) == 1:
            saturation = 1.0
        bad = ~finite
        if saturation is not None:
            bad |= data >= float(saturation) * .98
        data = np.asarray(data, dtype=np.float32)
        scale = 1
        if data.ndim == 3:
            if data.shape[0] != 3:
                raise ValueError('Plan couleur FITS non pris en charge')
            data, bad = data.mean(axis=0), bad.any(axis=0)
        elif header.get('BAYERPAT', '').strip() in ('RGGB', 'BGGR', 'GRBG', 'GBRG'):
            h, w = (n // 2 * 2 for n in data.shape)
            data = data[:h, :w].reshape(h//2, 2, w//2, 2).mean(axis=(1, 3))
            bad = bad[:h, :w].reshape(h//2, 2, w//2, 2).any(axis=(1, 3))
            scale = 2
        group = [header.get(k) for k in ('NAXIS1', 'NAXIS2', 'NAXIS3', 'BAYERPAT',
                                        'XBINNING', 'YBINNING', 'XPIXSZ', 'FOCALLEN', 'FILTER')]
    return data, bad, scale, group


def measure_stamp(stamp, window_sigma):
    """Measure background-subtracted flux without clipping negative noise pixels."""
    yy, xx = np.indices(stamp.shape, dtype=float)
    xx -= stamp.shape[1] // 2
    yy -= stamp.shape[0] // 2
    rr = np.hypot(xx, yy)
    aperture = 3 * window_sigma
    annulus = rr >= aperture + 2
    # Fit a local plane, with robust clipping to suppress neighbours in the sky.
    design = np.column_stack((np.ones(annulus.sum()), xx[annulus], yy[annulus]))
    sky = stamp[annulus]
    keep = np.ones(len(sky), dtype=bool)
    for _ in range(3):
        coefficients = np.linalg.lstsq(design[keep], sky[keep], rcond=None)[0]
        residual = sky - design @ coefficients
        noise = max(float(1.4826 * np.median(abs(residual - np.median(residual)))),
                    np.finfo(np.float32).eps * max(1, abs(float(np.median(sky)))))
        keep = abs(residual) < 3 * noise
        if keep.sum() < 12:
            return None
    signal = stamp - (coefficients[0] + coefficients[1] * xx + coefficients[2] * yy)
    if signal.max() < 10 * noise:
        return None
    cx = cy = 0.0
    for _ in range(12):
        radius2 = (xx-cx)**2 + (yy-cy)**2
        weighted = signal * np.exp(-radius2/(2*window_sigma**2)) * (radius2 <= aperture**2)
        flux = weighted.sum()
        if flux <= 0:
            return None
        nx, ny = float((weighted*xx).sum()/flux), float((weighted*yy).sum()/flux)
        shift = np.hypot(nx-cx, ny-cy)
        cx, cy = nx, ny
        if shift < .001:
            break
    if np.hypot(cx, cy) > window_sigma:
        return None
    dx, dy = xx-cx, yy-cy
    radius2 = dx**2 + dy**2
    weighted = signal * np.exp(-radius2/(2*window_sigma**2)) * (radius2 <= aperture**2)
    flux = weighted.sum()
    covariance = np.array([[(weighted*dx*dx).sum(), (weighted*dx*dy).sum()],
                           [(weighted*dx*dy).sum(), (weighted*dy*dy).sum()]]) / flux
    eigenvalues = np.linalg.eigvalsh(covariance)
    if eigenvalues[0] <= .15 or not np.isfinite(eigenvalues).all():
        return None
    # A 4x4 subpixel aperture integration avoids pixel-centre quantization.
    radii, fluxes = [], []
    for oy in (-.375, -.125, .125, .375):
        for ox in (-.375, -.125, .125, .375):
            r = np.hypot(dx+ox, dy+oy)
            inside = r <= aperture
            radii.append(r[inside])
            fluxes.append(signal[inside]/16)
    radii, fluxes = np.concatenate(radii), np.concatenate(fluxes)
    order = np.argsort(radii)
    growth = np.cumsum(fluxes[order])
    total = growth[-1]
    if total < 50 * noise * np.sqrt(np.pi * aperture**2):
        return None
    crossing = np.flatnonzero(growth >= .8 * total)
    if not len(crossing):
        return None
    r80 = float(radii[order[crossing[0]]])
    if r80 >= .8 * aperture:
        return None
    trace = np.trace(covariance)
    return dict(r80=r80, size=float(np.sqrt(trace)),
                elongation=float(np.sqrt(eigenvalues[1]/eigenvalues[0])),
                e1=float((covariance[0, 0]-covariance[1, 1])/trace),
                e2=float(2*covariance[0, 1]/trace), cx=cx, cy=cy)


def measure_stellar_profiles(image, window_sigma_native):
    """Measure up to eight bright unsaturated stars in each of nine sectors.

    Close peaks share an aperture: a double component is never deliberately
    fitted or deblended away. Robust frame summaries limit isolated binaries.
    """
    data, bad, scale, group = _analysis_plane(image)
    sigma = max(1.5, window_sigma_native / scale)
    radius = int(np.ceil(3*sigma + 6))
    report = dict(number=image.number, source=str(image.processing_path.resolve()),
                  status='insufficient_stars', scale=scale, group=group,
                  window_sigma_native=sigma*scale, stars=[], stars_measured=0, sectors=0)
    if min(data.shape) <= 2*radius:
        return report
    sample = data[::4, ::4]
    sample = sample[np.isfinite(sample)]
    if not len(sample):
        return report
    background = np.median(sample)
    noise = max(float(1.4826*np.median(abs(sample-background))), np.finfo(np.float32).eps)
    clean = np.where(np.isfinite(data), data, background)
    smooth = gaussian_filter(clean, .8)
    peaks = (smooth == maximum_filter(smooth, size=5)) & (smooth > background+6*noise)
    peaks[:radius] = peaks[-radius:] = False
    peaks[:, :radius] = peaks[:, -radius:] = False
    y, x = np.nonzero(peaks)
    order = np.argsort(-smooth[y, x], kind='stable')[:4000]
    centres, counts = [], np.zeros(9, dtype=int)
    for i in order:
        xi, yi = int(x[i]), int(y[i])
        sector = min(2, yi*3//data.shape[0])*3 + min(2, xi*3//data.shape[1])
        if counts[sector] >= 8:
            continue
        if any((xi-px)**2+(yi-py)**2 < (2*radius)**2 for px, py in centres):
            continue
        centres.append((xi, yi))
        cut = np.s_[yi-radius:yi+radius+1, xi-radius:xi+radius+1]
        if bad[cut].any():
            continue
        measured = measure_stamp(clean[cut].astype(float), sigma)
        if measured is None:
            continue
        counts[sector] += 1
        measured.update(x=(xi+measured.pop('cx'))*scale+(scale-1)/2,
                        y=(yi+measured.pop('cy'))*scale+(scale-1)/2, sector=int(sector))
        for key in ('r80', 'size'):
            measured[key] *= scale
        report['stars'].append(measured)
        if counts.sum() == 72:
            break
    stars = report['stars']
    report.update(stars_measured=len(stars), sectors=int((counts > 0).sum()))
    if len(stars) < 12 or report['sectors'] < 3:
        return report
    for key in ('r80', 'size', 'elongation', 'e1', 'e2'):
        report[key] = float(np.median([star[key] for star in stars]))
    report['status'] = 'measured'
    return report
