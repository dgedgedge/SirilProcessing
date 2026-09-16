"""Sélection et pondération des poses à partir des mesures de qualité Siril.

Les critères sont indépendants de la décision d’utiliser le Drizzle.
Voir docs/IMAGE_SELECTION.md pour les seuils et leurs valeurs par défaut.
"""
import logging
import shlex
from pathlib import Path

import numpy as np


def quality_mask(rows, cfg, included=None):
    """Explicit selection shared by diagnostics and seqapplyreg -filter-included."""
    if not rows:
        raise ValueError('Aucune transformation disponible')
    values = np.array([r[0] for r in rows])
    mask = np.isfinite(values).all(axis=1) & (values[:, 0] > 0)
    if included is not None:
        mask &= np.asarray(included, dtype=bool)
    logging.info('Sélection qualité — mesures valides : %d image(s) retirée(s), %d/%d restante(s)',
                 len(rows)-int(mask.sum()), int(mask.sum()), len(rows))
    labels = {'fwhm_filter': 'FWHM pondérée', 'roundness_filter': 'rondeur',
              'nbstars_filter': 'nombre d’étoiles'}
    for key, column, lower in [('fwhm_filter', 1, False), ('roundness_filter', 2, True), ('nbstars_filter', 5, True)]:
        raw = str(cfg.get(key, 'none')).strip().lower()
        before = int(mask.sum())
        if key == 'fwhm_filter':
            percent = float(cfg.get('fwhm_reject_percent', 0) or 0)
            if not np.isfinite(percent) or not 0 <= percent <= 95:
                raise ValueError('Pourcentage de rejet FWHM invalide')
            if percent > 0 and raw not in ('none', 'off', 'false', '0', ''):
                logging.info('Rejet FWHM proportionnel ignoré : le filtre FWHM principal est actif (aucun cumul).')
            elif percent > 0:
                indices = np.flatnonzero(mask)
                keep = min(before, max(2, int(np.ceil(before * (1-percent/100)))))
                rejected = indices[np.argsort(values[indices, column], kind='stable')[keep:]]
                mask[rejected] = False
                logging.info('Sélection qualité — FWHM pondérée : %d image(s) retirée(s), %d/%d restante(s) ; rejet proportionnel %.3f%%',
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
            median = np.median(sample)
            coefficient = float(raw[:-1] if raw.endswith(('k', '%')) else raw)
            if not np.isfinite(coefficient) or coefficient < 0:
                raise ValueError('Tolérance du nombre d’étoiles invalide : valeur finie positive ou nulle requise')
            if raw.endswith('k'):
                tolerance = coefficient * 1.4826 * np.median(abs(sample - median))
            elif raw.endswith('%'):
                tolerance = coefficient * median / 100
            else:
                tolerance = coefficient
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
    logging.info('Sélection qualité — bilan : %d image(s) retirée(s), %d/%d restante(s)',
                 len(rows)-int(mask.sum()), int(mask.sum()), len(rows))
    return mask


def apply_quality_weights(lines, images, rows, records, files, seqpath, cfg):
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
    extra_indices, image_lines = [], []
    for i, image in enumerate(images):
        record = accepted.get(str(Path(files[i]).resolve()))
        # A duplicate with a failed registration must not be re-enabled.
        valid = image[2] == '1' and np.isfinite(rows[i][0]).all() and rows[i][0][0] > 0 and np.isfinite(rows[i][1]).all() and abs(np.linalg.det(rows[i][1])) > 1e-8
        include = record is not None and valid
        tokens = list(image)
        tokens[2] = '1' if include else '0'
        image_lines.append(' '.join(tokens))
        if include:
            extra_indices.extend([i] * (record['quality_multiplicity'] - 1))
            record['effective_stack_entries'] += record['quality_multiplicity']
    header = next(shlex.split(line) for line in lines if line.startswith('S '))
    sequence, width = header[1], int(header[5])
    filenum = max(int(row[1]) for row in images)
    for index in extra_indices:
        filenum += 1
        source_number = int(images[index][1])
        candidates = [seqpath.parent / f'{sequence}{source_number:0{width}d}{ext}' for ext in ('.fit', '.fits', '.fts')]
        source = next((candidate for candidate in candidates if candidate.exists()), None)
        if source is None:
            raise ValueError('Image convertie introuvable pour la pondération de rondeur')
        destination = seqpath.parent / f'{sequence}{filenum:0{width}d}{source.suffix}'
        destination.symlink_to(source.resolve())
        tokens = list(images[index]); tokens[1:3] = [str(filenum), '1']
        image_lines.append(' '.join(tokens))
    header[3] = str(len(image_lines))
    header[4] = str(sum(line.split()[2] == '1' for line in image_lines))
    groups = {}
    for line in lines:
        if line.startswith('R'):
            groups.setdefault(line.split()[0], []).append(line)
    output, written = [], set()
    for line in lines:
        key = line.split()[0] if line.strip() else ''
        if key == 'S':
            output.append(f"S '{sequence}' " + ' '.join(header[2:]))
        elif key == 'I':
            if key not in written:
                output.extend(image_lines); written.add(key)
        elif key in groups:
            if key not in written:
                output.extend(groups[key])
                output.extend(groups[key][index] for index in extra_indices)
                written.add(key)
        else:
            output.append(line)
    seqpath.write_text('\n'.join(output)+'\n')
    effective = sum(r['effective_stack_entries'] for r in records)
    logging.info('Pondération qualité après sélection : %d poses retenues -> %d entrées effectives', len(records), effective)
    return effective
