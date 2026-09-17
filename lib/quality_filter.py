"""Sélection et pondération des poses à partir des mesures de qualité Siril.

Les critères sont indépendants de la décision d’utiliser le Drizzle.
Voir docs/scripts/lightProcess/filter/stacking/IMAGE_SELECTION.md pour les seuils et leurs valeurs par défaut.
"""
import logging

import numpy as np
from astropy.io import fits


def filter_registration_geometry(sequence, allowed_numbers=None, min_scale=0.5, max_scale=2.0):
    """Reject gross registration failures before resampling a same-scale sequence.

    Local singular values allow genuine rotations (including 180 degrees), but
    reject mirrored, collapsed or excessively stretched transforms. Check both
    center and corners for projective transforms.
    """
    reference = sequence.images[int(sequence.header[6])]
    reference_header = fits.getheader(reference.filename)
    reference_width, reference_height = reference_header['NAXIS1'], reference_header['NAXIS2']
    before = sum(image.included for image in sequence.images)
    rejected = []
    for index, image in enumerate(sequence.images):
        if not image.included:
            continue
        reason = None
        if allowed_numbers is not None and image.number not in allowed_numbers:
            reason = 'image exclue par la sélection précédente'
        registration = image.registration()
        if reason is None and not registration.valid:
            reason = 'mesures ou transformation invalides'
        if reason is None:
            header = fits.getheader(image.filename)
            w, h = header['NAXIS1'], header['NAXIS2']
            points = np.array([[0,0], [w-1,0], [w-1,h-1], [0,h-1], [(w-1)/2,(h-1)/2]])
            matrix = registration.homography / registration.homography[2,2]
            projected = (matrix @ np.column_stack((points, np.ones(5))).T).T
            denominator = projected[:,2]
            if np.min(denominator) <= 1e-8:
                reason = 'projection singulière dans le champ'
            else:
                mapped = projected[:,:2]/denominator[:,None]
                for position, d in zip(mapped, denominator):
                    jacobian = (matrix[:2,:2] - np.outer(position, matrix[2,:2]))/d
                    scales = np.linalg.svd(jacobian, compute_uv=False)
                    if np.linalg.det(jacobian) <= 0:
                        reason = 'inversion d’orientation'
                        break
                    if scales[-1] < min_scale or scales[0] > max_scale:
                        reason = f'échelles locales hors [{min_scale}, {max_scale}] : {scales.tolist()}'
                        break
                if reason is None and (mapped[:4,0].max() < 0 or mapped[:4,0].min() > reference_width-1
                                       or mapped[:4,1].max() < 0 or mapped[:4,1].min() > reference_height-1):
                    reason = 'champ transformé entièrement hors du cadre de référence'
        if reason is not None:
            image.included = False
            rejected.append(dict(index=index, number=image.number, filename=str(image.filename), reason=reason))
            logging.info('Rejet alignement : %s ; indice %d, fichier %d ; %s',
                         image.filename, index, image.number, reason)
    remaining = sum(image.included for image in sequence.images)
    logging.info('Contrôle géométrique — %s : %d entrée(s) retirée(s), %d/%d restante(s)',
                 sequence.path.name, len(rejected), remaining, before)
    sequence.write()
    return dict(sequence=str(sequence.path), rejected=rejected, remaining=remaining,
                min_scale=min_scale, max_scale=max_scale)


def star_count_bounds(sample, raw):
    """Return median and symmetric tolerance for finite star counts."""
    median = float(np.median(sample))
    coefficient = float(raw[:-1] if raw.endswith(('k', '%')) else raw)
    if not np.isfinite(coefficient) or coefficient < 0:
        raise ValueError('Tolérance du nombre d’étoiles invalide : valeur finie positive ou nulle requise')
    if raw.endswith('k'):
        tolerance = coefficient * 1.4826 * float(np.median(abs(sample - median)))
    elif raw.endswith('%'):
        tolerance = coefficient * median / 100
    else:
        tolerance = coefficient
    return median, tolerance


def quality_mask(rows, cfg, included=None):
    """Explicit selection shared by diagnostics and seqapplyreg -filter-included."""
    if not rows:
        raise ValueError('Aucune transformation disponible')
    values = np.array([r.registration().values if hasattr(r, "registration") else r[0] for r in rows])
    mask = np.isfinite(values).all(axis=1) & (values[:, 0] > 0)
    if included is not None:
        mask &= np.asarray(included, dtype=bool)
    logging.info('Sélection qualité — mesures valides : %d image(s) retirée(s), %d/%d restante(s)',
                 len(rows)-int(mask.sum()), int(mask.sum()), len(rows))
    max_fwhm = float(cfg.get('max_fwhm', 0) or 0)
    if not np.isfinite(max_fwhm) or max_fwhm < 0:
        raise ValueError('Plafond FWHM invalide : valeur finie positive ou nulle requise')
    before = int(mask.sum())
    if max_fwhm > 0:
        mask &= values[:, 0] <= max_fwhm
        logging.info('Sélection qualité — FWHM non pondérée : %d image(s) retirée(s), %d/%d restante(s) ; seuil <= %.3f pixels',
                     before-int(mask.sum()), int(mask.sum()), before, max_fwhm)
    else:
        logging.info('Sélection qualité — FWHM non pondérée : filtre désactivé, 0 image(s) retirée(s), %d/%d restante(s)', before, before)
    labels = {'fwhm_filter': 'FWHM pondérée', 'fwhm_reject_percent': 'FWHM proportionnelle', 'roundness_filter': 'rondeur',
              'nbstars_filter': 'nombre d’étoiles'}
    for key, column, lower in [('fwhm_filter', 1, False), ('fwhm_reject_percent', 1, False), ('roundness_filter', 2, True), ('nbstars_filter', 5, True)]:
        raw = str(cfg.get(key, 'none')).strip().lower()
        before = int(mask.sum())
        if key == 'fwhm_reject_percent':
            percent = float(cfg.get(key, 0) or 0)
            if not np.isfinite(percent) or not 0 <= percent <= 95:
                raise ValueError('Pourcentage de rejet FWHM invalide')
            if percent == 0:
                logging.info('Sélection qualité — FWHM proportionnelle : filtre désactivé, 0 image(s) retirée(s), %d/%d restante(s)', before, before)
            else:
                indices = np.flatnonzero(mask)
                keep = min(before, max(2, int(np.ceil(before * (1-percent/100)))))
                rejected = indices[np.argsort(values[indices, column], kind='stable')[keep:]]
                mask[rejected] = False
                logging.info('Sélection qualité — FWHM proportionnelle : %d image(s) retirée(s), %d/%d restante(s) ; rejet %.3f%%',
                             before-keep, keep, before, percent)
            continue
        if raw in ('none', 'off', 'false', '0', ''):
            logging.info('Sélection qualité — %s : filtre désactivé, 0 image(s) retirée(s), %d/%d restante(s)',
                         labels[key], before, before)
            continue
        sample = values[mask, column]
        if not len(sample):
            logging.info('Sélection qualité — %s : aucune image à évaluer, 0 image(s) retirée(s), 0/0 restante(s)', labels[key])
            continue
        if key == 'nbstars_filter':
            median, tolerance = star_count_bounds(sample, raw)
            mask &= abs(values[:, column] - median) <= tolerance
            logging.info('Sélection qualité — %s : %d image(s) retirée(s), %d/%d restante(s) ; médiane=%.3f, tolérance=%.3f, plage=[%.3f, %.3f]',
                         labels[key], before-int(mask.sum()), int(mask.sum()), before,
                         median, tolerance, median-tolerance, median+tolerance)
            continue
        if raw.endswith('k'):
            median = np.median(sample)
            sigma = 1.4826 * np.median(abs(sample-median))
            limit = median + (-1 if lower else 1)*float(raw[:-1])*sigma
        elif raw.endswith('%'):
            percent = float(raw[:-1])
            if not 0 < percent <= 100:
                raise ValueError('Pourcentage de sélection invalide')
            limit = np.percentile(sample, 100-percent if lower else percent)
        else:
            limit = float(raw)
        mask &= values[:, column] >= limit if lower else values[:, column] <= limit
        logging.info('Sélection qualité — %s : %d image(s) retirée(s), %d/%d restante(s) ; seuil %s %.3f',
                     labels[key], before-int(mask.sum()), int(mask.sum()), before,
                     '>=' if lower else '<=', limit)
    return mask


def classify_stellar_profiles(measurements, cfg):
    """Set profile decisions using comparable frames and spatial consensus.

    Thresholds are computed once, before either profile criterion rejects.
    The median/MAD assumption is that defective exposures are a minority.
    """
    coefficient = float(cfg.get('stellar_profile_sigma', 3))
    if not np.isfinite(coefficient) or coefficient <= 0:
        raise ValueError('stellar_profile_sigma doit être fini et strictement positif')
    groups = {}
    for frame in measurements:
        frame['reasons'] = []
        if frame['status'] == 'measured':
            key = tuple(frame['group'])
            # Plate solving refines FOCALLEN per image: exact equality would
            # create a separate reference group for every exposure.
            for group in groups:
                focal, other = key[7], group[7]
                same_focal = focal == other or (focal is not None and other is not None
                                                and np.isclose(float(focal), float(other), rtol=.02, atol=0))
                if key[:7] == group[:7] and key[8:] == group[8:] and same_focal:
                    key = group
                    break
            groups.setdefault(key, []).append(frame)
    references = []
    for group, frames in groups.items():
        if len(frames) < 8:
            for frame in frames:
                frame['status'] = 'insufficient_reference'
            continue
        reference = dict(group=list(group), frames=len(frames))
        for key in ('r80', 'elongation'):
            values = np.array([frame[key] for frame in frames])
            median = float(np.median(values))
            dispersion = float(1.4826*np.median(abs(values-median)))
            floor = 1.25*median if key == 'r80' else 1.5
            reference[key] = dict(median=median, dispersion=dispersion,
                                  limit=max(median+coefficient*dispersion, floor))
        references.append(reference)
        for frame in frames:
            limits = {key: reference[key]['limit'] for key in ('r80', 'elongation')}
            frame['limits'] = limits
            stars = frame['stars']
            broad = [star for star in stars if star['r80'] > limits['r80']]
            direction = np.array([frame['e1'], frame['e2']])
            direction /= max(float(np.linalg.norm(direction)), 1e-12)
            elongated = []
            for star in stars:
                e = np.array([star['e1'], star['e2']])
                # Ellipticity angle is twice the physical position angle.
                aligned = float(e @ direction) >= .5 * float(np.linalg.norm(e))
                if star['elongation'] > limits['elongation'] and aligned:
                    elongated.append(star)
            for reason, agreeing in [('r80', broad), ('elongation', elongated)]:
                frame[reason+'_fraction'] = len(agreeing)/len(stars)
                if len(agreeing) >= .6*len(stars) and len({s['sector'] for s in agreeing}) >= 3:
                    frame['reasons'].append(reason)
            frame['status'] = 'rejected' if frame['reasons'] else 'accepted'
    return references


def filter_stellar_profiles(images, mask, cfg):
    """Inspect only independent survivors, without any star-count targeting."""
    from lib.stellar_quality import measure_stellar_profiles

    before = int(mask.sum())
    report = dict(enabled=bool(cfg.get('stellar_profile_filter', True)), frames=[], references=[])
    if not report['enabled']:
        logging.info('Sélection qualité — profils stellaires : filtre désactivé, 0 image(s) retirée(s), %d/%d restante(s)', before, before)
        return report
    # Validate even when no exposure is available.
    classify_stellar_profiles([], cfg)
    indices = np.flatnonzero(mask)
    # A common broad window keeps moments comparable across the selection.
    widths = [images[i].registration().fwhm for i in indices]
    window = max(3., float(np.median(widths))) if widths else 3.
    for position, index in enumerate(indices):
        image = images[index]
        logging.debug('Profils stellaires %d/%d : %s', position+1, before, image.processing_path)
        try:
            frame = measure_stellar_profiles(image, window)
        except (OSError, ValueError, np.linalg.LinAlgError) as exc:
            frame = dict(number=image.number, source=str(image.processing_path.resolve()),
                         status='error', error=str(exc), stars=[])
            logging.warning('Profils stellaires non mesurés : %s ; %s', image.processing_path, exc)
        frame['sequence_index'] = int(index)
        report['frames'].append(frame)
    report['references'] = classify_stellar_profiles(report['frames'], cfg)
    for reason, label in [('r80', 'étalement R80'), ('elongation', 'allongement cohérent')]:
        previous = int(mask.sum())
        for frame in report['frames']:
            if reason in frame['reasons'] and mask[frame['sequence_index']]:
                mask[frame['sequence_index']] = False
                logging.info('Rejet profils stellaires — %s : %s ; mesure=%.3f, seuil=%.3f, fraction=%.3f',
                             label, frame['source'], frame[reason], frame['limits'][reason], frame[reason+'_fraction'])
        logging.info('Sélection qualité — %s : %d image(s) retirée(s), %d/%d restante(s)',
                     label, previous-int(mask.sum()), int(mask.sum()), previous)
    report.update(rejected=before-int(mask.sum()), remaining=int(mask.sum()),
                  inconclusive=sum(f['status'] not in ('accepted', 'rejected') for f in report['frames']))
    logging.info('Sélection qualité — profils stellaires : %d image(s) retirée(s), %d/%d restante(s), %d non concluante(s)',
                 report['rejected'], report['remaining'], before, report['inconclusive'])
    return report


def apply_quality_weights(sequence, records, cfg):
    """Expand selected sequence entries, preserving original registration matrices.

    Integer multiplicities match the existing FWHM weighting convention. No
    interpolated image is measured and no duplicate contributes to diagnostics.
    """
    extra_max = int(cfg.get('roundness_weight_max_extra', 1))
    if not 0 <= extra_max <= 8:
        raise ValueError('roundness_weight_max_extra doit être entre 0 et 8')
    enabled = cfg.get('roundness_weighted', False)
    roundness = [r['roundness'] for r in records]
    low, high = (min(roundness), max(roundness)) if roundness else (0, 0)
    fwhm_extra = int(cfg.get('fwhm_weight_max_extra', 1))
    if not 0 <= fwhm_extra <= 8:
        raise ValueError('fwhm_weight_max_extra doit être entre 0 et 8')
    fwhms = [r['fwhm'] for r in records]
    best, worst = (min(fwhms), max(fwhms)) if fwhms else (0, 0)
    accepted = {r['source']: r for r in records}
    for record in records:
        quality = (record['roundness'] - low) / (high-low) if high > low else 0
        record['roundness_multiplicity'] = 1 + int(round(quality*extra_max)) if enabled else 1
        fwhm_quality = (worst-record['fwhm'])/(worst-best) if worst > best else 0
        record['fwhm_multiplicity'] = 1 + int(round(fwhm_quality*fwhm_extra)) if cfg.get('fwhm_weighted', False) and len(records) > 2 else 1
        record['quality_multiplicity'] = record['roundness_multiplicity'] * record['fwhm_multiplicity']
        record['effective_stack_entries'] = 0
    for image in list(sequence.images):
        record = accepted.get(str(image.processing_path.resolve()))
        image.included = bool(record is not None and image.included and image.registration().valid)
        if image.included:
            for _ in range(record['quality_multiplicity'] - 1):
                sequence.duplicate(image)
            record['effective_stack_entries'] += record['quality_multiplicity']
    sequence.write()
    effective = sum(r['effective_stack_entries'] for r in records)
    logging.info('Pondération qualité après sélection : %d poses retenues -> %d entrées effectives', len(records), effective)
    return effective
