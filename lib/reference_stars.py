"""Mesure gaussienne locale guidée par un catalogue de positions initiales.

Les positions Siril sont exprimées depuis le bas avec centres à .5 pixel. Les
FITS sont lus selon ROWORDER. Les deux images sont ajustées avec le même modèle,
sans détection globale supplémentaire. Voir le guide postProcess/reference-stars.md.
"""
from __future__ import annotations

from pathlib import Path
from collections import Counter

import numpy as np
from astropy.io import fits
from scipy.optimize import least_squares
from scipy.spatial import cKDTree

from lib.type_defs import JSONReport, StarMeasurement

FWHM_TO_SIGMA = 2 * np.sqrt(2 * np.log(2))
MIN_REFERENCE_SNR = 10.0
MAX_FIT_EVALUATIONS = 150


def image_plane(path: Path | str, layer: int) -> np.ndarray:
    """Lit un canal FITS flottant, orienté du bas vers le haut comme Siril.

    Normalise les FITS entiers pour rendre les amplitudes comparables à Siril.
    Une forme ou un canal incompatible lève ValueError.
    """
    with fits.open(path, memmap=False) as hdul:
        original = hdul[0].data
        if original is None or original.ndim not in (2, 3):
            raise ValueError('FITS sans plan image mono/RGB valide')
        if original.ndim == 3 and not 0 <= layer < original.shape[0]:
            raise ValueError('Canal incompatible avec le FITS RGB')
        data = original[layer] if original.ndim == 3 else original
        if original.ndim == 2 and layer != 0:
            raise ValueError('Canal incompatible avec un FITS monochrome')
        scale = np.iinfo(original.dtype).max if np.issubdtype(original.dtype, np.integer) else 1
        plane = np.asarray(data, dtype=float) / scale
        if hdul[0].header.get('ROWORDER', 'BOTTOM-UP') == 'TOP-DOWN':
            plane = plane[::-1]
        return plane


def measure_star(
    plane: np.ndarray, star: StarMeasurement, radius: float,
) -> tuple[StarMeasurement | None, JSONReport]:
    """Ajuste une gaussienne elliptique tournée avec fond plan près de la position.

    Le centre peut se déplacer dans le rayon d’appariement ; les largeurs sont
    bornées entre 0.5 pixel et la moitié de la fenêtre. Le bruit est un MAD
    robuste des pixels périphériques. Le SNR est celui d’une ouverture de rayon
    2.5 sigma, avec incertitude du fond ; bruit corrélé et photons ne sont pas
    modélisés. Une convergence sur une borne ou une covariance singulière est
    refusée. Les résidus sont audités sans imposer un seuil non calibré.
    """
    if not np.isfinite(radius) or radius <= 0:
        raise ValueError('Rayon de mesure invalide')
    cx, cy = star['x'] - 0.5, star['y'] - 0.5
    half = max(6, int(np.ceil(3 * star['fwhm'])))
    ix, iy = int(round(cx)), int(round(cy))
    height, width = plane.shape
    audit: JSONReport = dict(reference_id=star['id'])
    if ix - half < 0 or iy - half < 0 or ix + half >= width or iy + half >= height:
        return None, dict(**audit, reason='edge_window')
    patch = plane[iy-half:iy+half+1, ix-half:ix+half+1]
    if not np.isfinite(patch).all():
        return None, dict(**audit, reason='nonfinite_pixels')
    yy, xx = np.mgrid[-half:half+1, -half:half+1]
    border = np.hypot(xx, yy) >= 0.8 * half
    background = float(np.median(patch[border]))
    noise = float(1.4826 * np.median(abs(patch[border] - background)))
    if noise <= 0:
        return None, dict(**audit, reason='undefined_background_noise')
    peak = float(patch.max())
    if peak >= 1 and np.count_nonzero(patch >= peak - 1e-7) >= 3:
        return None, dict(**audit, reason='saturated_pixels')
    amplitude = max(float(patch.max() - background), noise)
    initial = [amplitude, cx-ix, cy-iy,
               np.clip(star['fwhm_x']/FWHM_TO_SIGMA, .6, half/2),
               np.clip(star['fwhm_y']/FWHM_TO_SIGMA, .6, half/2), 0, background, 0, 0]
    lower = [0, cx-ix-radius, cy-iy-radius, .5, .5, -np.pi, -np.inf, -np.inf, -np.inf]
    upper = [np.inf, cx-ix+radius, cy-iy+radius, half/2, half/2, np.pi, np.inf, np.inf, np.inf]

    def residual(parameters: np.ndarray) -> np.ndarray:
        """Retourne les résidus du profil et du fond plan, normalisés par le bruit."""
        a, x, y, sx, sy, angle, bg, gx, gy = parameters
        u = (xx-x)*np.cos(angle) + (yy-y)*np.sin(angle)
        v = -(xx-x)*np.sin(angle) + (yy-y)*np.cos(angle)
        model = bg + gx*xx + gy*yy + a*np.exp(-.5*((u/sx)**2+(v/sy)**2))
        return ((model-patch)/noise).ravel()

    fit = least_squares(residual, initial, bounds=(lower, upper), max_nfev=MAX_FIT_EVALUATIONS)
    if not fit.success or not np.isfinite(fit.x).all():
        return None, dict(**audit, reason='fit_failed')
    a, x, y, sx, sy, angle, bg, gx, gy = fit.x
    displacement = float(np.hypot(ix+x-cx, iy+y-cy))
    if displacement >= radius * .99:
        return None, dict(**audit, reason='excessive_displacement', displacement_px=displacement)
    if np.any(fit.active_mask[3:5]):
        return None, dict(**audit, reason='width_at_bound')
    # La rotation d’une étoile ronde est indéterminée : diagnostic conditionnel
    # des cinq paramètres principaux, angle et gradients fixés. L’incertitude
    # obtenue n’est donc pas une covariance marginale complète.
    jacobian = fit.jac[:, :5]
    if np.linalg.matrix_rank(jacobian) < 5:
        return None, dict(**audit, reason='singular_fit')
    covariance = np.linalg.pinv(jacobian.T @ jacobian)
    aperture = (xx-x)**2 + (yy-y)**2 <= (2.5*np.sqrt(sx*sy))**2
    count = int(aperture.sum())
    flux = float(np.sum((patch-bg-gx*xx-gy*yy)[aperture]))
    uncertainty = noise*np.sqrt(count + count*count/int(border.sum()))
    snr = flux/uncertainty
    measured = dict(star, x=float(ix+x+.5), y=float(iy+y+.5),
                    fwhm_x=float(sx*FWHM_TO_SIGMA), fwhm_y=float(sy*FWHM_TO_SIGMA),
                    fwhm=float(np.sqrt(sx*sy)*FWHM_TO_SIGMA), amplitude=float(a))
    audit.update(reason='measured', snr=float(snr), flux=flux,
                 background=float(bg), noise=noise, displacement_px=displacement,
                 residual_rms_sigma=float(np.sqrt(np.mean(fit.fun**2))),
                 width_uncertainty_px=float(FWHM_TO_SIGMA*np.sqrt(max(covariance[3,3],covariance[4,4]))))
    return measured, audit


def measure_reference_catalog(
    source: Path | str, candidate: Path | str, stars: list[StarMeasurement],
    layer: int, radius: float,
) -> tuple[list[StarMeasurement], list[StarMeasurement], JSONReport]:
    """Mesure les étoiles fiables avant et après, avec positions initiales communes.

    L’isolement à trois FWHM est défini sur l’entrée. Le SNR minimal de 10
    sélectionne les références et valide séparément les mesures candidates.
    Les étoiles de référence fiables restent dans le dénominateur même si
    leur mesure candidate échoue. Les deux FITS doivent avoir la même forme.
    """
    before_plane, after_plane = image_plane(source, layer), image_plane(candidate, layer)
    if before_plane.shape != after_plane.shape:
        raise ValueError('Dimensions différentes pour la mesure aux positions initiales')
    tree = cKDTree([[star['x'], star['y']] for star in stars]) if stars else None
    reliable, measured_after, audits = [], [], []
    for star in stars:
        neighbors = tree.query_ball_point([star['x'], star['y']], 3*star['fwhm'])
        if len(neighbors) > 1:
            audits.append(dict(reference_id=star['id'], stage='before', reason='neighbor_contamination'))
            continue
        measured, audit = measure_star(before_plane, star, radius)
        audit['stage'] = 'before'
        audits.append(audit)
        if measured is None:
            continue
        if audit['snr'] < MIN_REFERENCE_SNR:
            audit['reason'] = 'low_reference_snr'
            continue
        reliable.append(measured)
        after, after_audit = measure_star(after_plane, measured, radius)
        after_audit['stage'] = 'after'
        audits.append(after_audit)
        if after is not None and after_audit['snr'] >= MIN_REFERENCE_SNR:
            measured_after.append(after)
        elif after is not None:
            after_audit['reason'] = 'low_candidate_snr'
    summary = Counter(f"{entry['stage']}:{entry['reason']}" for entry in audits)
    return reliable, measured_after, dict(
        failure_summary=dict(summary),
        method='gaussian_at_reference_positions', initial_count=len(stars),
        reliable_reference_count=len(reliable), measured_after_count=len(measured_after),
        eligible_reference_ids=[star['id'] for star in reliable],
        min_reference_snr=MIN_REFERENCE_SNR, measurements=audits,
    )
