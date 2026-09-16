"""Sélection et pondération des poses à partir des mesures de qualité Siril.

Les critères sont indépendants de la décision d’utiliser le Drizzle.
Voir docs/IMAGE_SELECTION.md pour les seuils et leurs valeurs par défaut.
"""
import logging
import shlex
from pathlib import Path

import numpy as np


def quality_mask(rows, cfg):
    """Explicit selection shared by diagnostics and seqapplyreg -filter-included."""
    if not rows:
        raise ValueError('Aucune transformation disponible')
    values = np.array([r[0] for r in rows])
    mask = np.isfinite(values).all(axis=1) & (values[:, 0] > 0)
    for key, column, lower in [('fwhm_filter', 1, False), ('roundness_filter', 2, True), ('nbstars_filter', 5, True)]:
        raw = str(cfg.get(key, 'none')).strip().lower()
        if raw in ('none', 'off', 'false', '0', ''):
            continue
        sample = values[mask, column]
        if not len(sample):
            break
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
            logging.info('Sélection étoiles : médiane=%.3f, tolérance=%.3f, plage=[%.3f, %.3f], retenues=%d/%d',
                         median, tolerance, median-tolerance, median+tolerance, int(mask.sum()), len(sample))
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
    return mask


def apply_roundness_weights(lines, images, rows, records, files, seqpath, cfg):
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
    accepted = {r['source']: r for r in records}
    for record in records:
        quality = (record['roundness'] - low) / (high-low) if high > low else 0
        record['roundness_multiplicity'] = 1 + int(round(quality*extra_max)) if enabled else 1
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
            extra_indices.extend([i] * (record['roundness_multiplicity'] - 1))
            record['effective_stack_entries'] += record['roundness_multiplicity']
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
    return sum(r['effective_stack_entries'] for r in records)


