"""Conservative Drizzle diagnostics from Siril v4–v7 registration records."""
import json
import logging
import os
import shlex
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from astropy.io import fits


def validate_settings(cfg):
    if cfg.get('drizzle_kernel', 'auto') not in ('auto', 'square', 'gaussian', 'turbo', 'point'):
        raise ValueError('Kernel Drizzle invalide')
    if cfg.get('drizzle', 'auto') not in ('off', 'auto', 'force'):
        raise ValueError('Mode Drizzle invalide')
    for name, low, high in [('scale', 1, 3), ('pixfrac', .01, 1)]:
        value = cfg.get('drizzle_' + name, 'auto')
        if value != 'auto' and not low <= float(value) <= high:
            raise ValueError(f'drizzle_{name} doit être entre {low} et {high}, ou auto')
    for key, low, high in [('min_frames', 3, 1000000), ('min_coverage', .01, 1),
                           ('fwhm_limit', .1, 100), ('max_drift', .01, 1000000)]:
        if 'drizzle_' + key in cfg and not low <= float(cfg['drizzle_' + key]) <= high:
            raise ValueError(f'drizzle_{key} invalide')


def cache_matches(output, cfg):
    try:
        report = json.loads(Path(output).with_suffix('.drizzle.json').read_text())
        return report.get('quality_pipeline_version') == 2 and report['status'] == 'completed' and report['settings'] == cfg
    except (OSError, ValueError, KeyError):
        return False


def read_registration(path):
    lines = Path(path).read_text().splitlines()
    header = next(shlex.split(s) for s in lines if s.startswith('S '))
    if int(header[7]) not in (4, 5, 6, 7):
        raise ValueError('Version de séquence non prise en charge')
    images = [s.split() for s in lines if s.startswith('I ')]
    layers = {}
    for line in lines:
        if line.startswith('R'):
            v = line.split()
            if len(v) != 17 or v[7] != 'H':
                raise ValueError('Transformation Siril invalide')
            h = np.array(v[8:], dtype=float).reshape(3, 3)
            layers.setdefault(v[0], []).append((list(map(float, v[1:7])), h))
    rows = next(iter(layers.values()), [])
    if len(rows) != len(images):
        raise ValueError('Alignements manquants')
    return lines, images, rows


def quality_mask(rows, cfg):
    """Explicit selection shared by diagnostics and seqapplyreg -filter-included."""
    if not rows:
        raise ValueError('Aucune transformation disponible')
    values = np.array([r[0] for r in rows])
    mask = np.isfinite(values).all(axis=1) & (values[:, 0] > 0)
    for key, column, lower in [('fwhm_filter', 1, False), ('roundness_filter', 2, True), ('nbstars_filter', 5, True)]:
        raw = str(cfg.get(key, 'none')).lower()
        if raw in ('none', 'off', 'false', '0', ''):
            continue
        sample = values[mask, column]
        if not len(sample):
            break
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


def coverage(points, period=1.0, bins=4):
    phases = np.mod(points, period) / period
    # Torus distance: phases near 0 and 1 are neighbours, not independent.
    unique = []
    for p in phases:
        if not any(np.linalg.norm(np.minimum(abs(p-q), 1-abs(p-q))) < .05 for q in unique):
            unique.append(p)
    hist, _, _ = np.histogram2d(phases[:, 0], phases[:, 1], bins=bins, range=[[0, 1], [0, 1]])
    occupied = np.count_nonzero(hist) / hist.size
    probability = hist[hist > 0] / max(1, hist.sum())
    uniformity = float(np.exp(-np.sum(probability * np.log(probability))) / hist.size)
    return dict(occupied=float(occupied), uniformity=uniformity,
                independent_phases=len(unique), histogram=hist.astype(int).tolist())


def analyse(records, cfg, metadata, resources):
    n = len(records)
    validate_settings(cfg)
    points = np.array([[r['dx'], r['dy']] for r in records], dtype=float).reshape(-1, 2)
    result = dict(images_retained=n, dithering_detected=False, dither_count=0,
                  frequency_frames=None, amplitude_median_px=None, amplitude_min_px=None,
                  amplitude_max_px=None, amplitude_xy_px=None, bidirectional=False,
                  drift_px=None, coverage=None, cfa_coverage=None, fwhm_pixels=None)
    if n >= 3:
        delta = np.diff(points, axis=0)
        contiguous = np.array([Path(a.get('source', '.')).parent == Path(b.get('source', '.')).parent
                               for a,b in zip(records, records[1:])])
        drift = np.median(delta[contiguous], axis=0) if contiguous.any() else np.zeros(2)
        residual = np.linalg.norm(delta-drift, axis=1)
        local = residual[contiguous]
        quiet = local[local <= np.median(local)] if len(local) else np.zeros(1)
        noise = 1.4826 * np.median(abs(quiet - np.median(quiet)))
        threshold = max(.15, float(np.median(quiet) + 6*noise))
        jumps = (residual > threshold) & contiguous
        # Reject isolated excursions followed immediately by a return.
        for i in range(len(delta)-1):
            if jumps[i] and jumps[i+1] and np.linalg.norm(delta[i]+delta[i+1]-2*drift) < threshold:
                jumps[i:i+2] = False
        indices = np.flatnonzero(jumps)
        vectors = delta[jumps]-drift
        amplitudes = np.linalg.norm(vectors, axis=1)
        directions = np.unique(np.floor((np.arctan2(vectors[:, 1], vectors[:, 0])+np.pi)/(np.pi/4)).astype(int)) if len(vectors) else []
        result.update(dithering_detected=bool(len(indices) >= 3 and len(directions) >= 2),
                      dither_count=len(indices), drift_px=(drift*(n-1)).tolist(),
                      noise_px=float(noise), jump_threshold_px=threshold,
                      dither_indices=indices.tolist(), coverage=coverage(points))
        if len(indices):
            result.update(frequency_frames=float(np.mean(np.diff(indices))) if len(indices)>1 else None,
                          amplitude_median_px=float(np.median(amplitudes)), amplitude_min_px=float(min(amplitudes)),
                          amplitude_max_px=float(max(amplitudes)), amplitude_xy_px=np.median(abs(vectors), axis=0).tolist(),
                          bidirectional=bool(np.all(np.ptp(vectors, axis=0) > threshold)))
        if metadata['native_cfa']:
            # Each Bayer site repeats every two physical pixels (including G1/G2).
            pattern = metadata.get('bayer_pattern') or 'RGGB'
            sites, green = {}, 0
            for index, color in enumerate(pattern):
                if color == 'G':
                    green += 1
                    color = f'G{green}'
                sites[color] = coverage(points + (index % 2, index // 2), 2, 4)
            result['cfa_coverage'] = sites
        fwhm = [r['fwhm'] for r in records if r['fwhm'] > 0]
        result['fwhm_pixels'] = float(np.median(fwhm)) if fwhm else None
    cov = result['coverage'] or {}
    gates = dict(dithering=result['dithering_detected'],
                 image_count=n >= int(cfg.get('drizzle_min_frames', 30)),
                 coverage=cov.get('occupied', 0) >= float(cfg.get('drizzle_min_coverage', .75)) and cov.get('independent_phases', 0) >= 12 and cov.get('uniformity', 0) >= .6,
                 sampling=result['fwhm_pixels'] is not None and result['fwhm_pixels'] < float(cfg.get('drizzle_fwhm_limit', 2.5)),
                 alignment=n >= .9 * metadata['unique_inputs'],
                 drift=result['drift_px'] is not None and np.linalg.norm(result['drift_px']) < float(cfg.get('drizzle_max_drift', 10)),
                 resources=resources['sufficient'],
                 cfa=not metadata['native_cfa'] or (n >= 60 and all(c['occupied'] >= .75 and c['uniformity'] >= .6 and c['independent_phases'] >= 12 for c in (result['cfa_coverage'] or {}).values()) and result['cfa_coverage'] is not None))
    gates['translation_model'] = all(
        np.allclose(np.array(r['homography'])[:2,:2], np.eye(2), atol=.01)
        and np.allclose(np.array(r['homography'])[2,:2], 0, atol=1e-7)
        for r in records if 'homography' in r)
    mode = cfg.get('drizzle', 'auto')
    enabled = mode == 'force' or (mode == 'auto' and all(gates.values()))
    pixfrac = cfg.get('drizzle_pixfrac', 'auto')
    pixfrac = (.8 if cov.get('uniformity', 0) >= .85 and n >= 100 else 1.) if pixfrac == 'auto' else float(pixfrac)
    scale = cfg.get('drizzle_scale', 'auto')
    scale = 2. if scale == 'auto' else float(scale)
    kernel = cfg.get('drizzle_kernel', 'auto')
    kernel = 'square' if kernel == 'auto' else kernel
    if not 0 < pixfrac <= 1 or not 1 <= scale <= 3 or kernel not in ('square', 'gaussian', 'turbo', 'point'):
        raise ValueError('Paramètres Drizzle invalides')
    result['images_analysed'] = metadata['unique_inputs']
    result.update(enabled=enabled, mode=mode, scale=scale, pixfrac=pixfrac, kernel=kernel,
                  gates={k: bool(v) for k,v in gates.items()}, drizzle_score=sum(gates.values())/len(gates),
                  reasons=[k for k,v in gates.items() if not v],
                  decision=('CFA Drizzle' if metadata['native_cfa'] else 'Drizzle') if enabled else 'Drizzle désactivé',
                  sampling_out=metadata['sampling_arcsec_px'] / scale if enabled and metadata['sampling_arcsec_px'] else metadata['sampling_arcsec_px'])
    return result


def run_stack(siril, files, cfg, input_dir, work_dir, sequence, output_path, prepare, stack_line, framing, stack_report=None):
    """Register untouched inputs, diagnose, then apply exactly one resampling."""
    if not siril.run_siril_script(prepare+'\nclose', str(work_dir), script_name='drizzle_registration.sps'):
        return False
    headers = [fits.getheader(p) for p in files]
    first = headers[0]
    native = first.get('NAXIS', 0) == 2 and first.get('BAYERPAT', '').strip() in ('RGGB','BGGR','GRBG','GBRG')
    focal, pixel = first.get('FOCALLEN'), first.get('XPIXSZ')
    sampling = 206.265*float(pixel)*float(first.get('XBINNING', 1))/float(focal) if focal and pixel and float(focal)>0 else None
    metadata = dict(native_cfa=bool(native), bayer_pattern=first.get('BAYERPAT'),
                    focal_length_mm=focal, pixel_size_um=pixel, binning=[first.get('XBINNING',1),first.get('YBINNING',1)],
                    dimensions=[first.get('NAXIS1'),first.get('NAXIS2')], sampling_arcsec_px=sampling,
                    unique_inputs=len({str(Path(p).resolve()) for p in files}), effective_inputs=len(files))
    seqpath = Path(input_dir)/f'{sequence}.seq'
    records, error, effective_entries = [], None, 0
    try:
        lines, images, rows = read_registration(seqpath)
        if len(rows) != len(files):
            raise ValueError('Nombre de transformations différent du nombre des entrées')
        # Compute quality thresholds on independent exposures, before weighting.
        unique_by_source = {}
        for i, path in enumerate(files):
            unique_by_source.setdefault(str(Path(path).resolve()), i)
        unique_indices = list(unique_by_source.values())
        unique_selection = quality_mask([rows[i] for i in unique_indices], cfg)
        selected_sources = {str(Path(files[i]).resolve()) for i,keep in zip(unique_indices, unique_selection) if keep}
        selected = [str(Path(p).resolve()) in selected_sources for p in files]
        seen = set()
        for i, (values, h) in enumerate(rows):
            source = str(Path(files[i]).resolve())
            if source in seen or not selected[i] or images[i][2] != '1' or not np.isfinite(h).all() or abs(np.linalg.det(h)) < 1e-8 or abs(h[2,2]) < 1e-8 or values[0] <= 0:
                continue
            seen.add(source)
            records.append(dict(source=source, sequence_index=i, date_obs=headers[i].get('DATE-OBS'),
                                dx=float(h[0,2]/h[2,2]), dy=float(h[1,2]/h[2,2]), fwhm=values[0], roundness=values[2], nbstars=int(values[5]), homography=h.tolist(),
                                dithering_headers={key: headers[i][key] for key in headers[i] if 'DITH' in key.upper()}))
        records.sort(key=lambda r: (r['date_obs'] or '', r['source']))
        effective_entries = apply_roundness_weights(lines, images, rows, records, files, seqpath, cfg)
    except (OSError, ValueError, IndexError, StopIteration) as exc:
        error = str(exc)
        records = []
    if stack_report is not None:
        stack_report.update(kept_unique_for_stack=len(records), kept_effective_for_stack=effective_entries,
                            rejected_registration_quality=metadata['unique_inputs']-len(records),
                            weighted_extra_entries=max(0, effective_entries-len(records)))
    scale = cfg.get('drizzle_scale', 'auto')
    scale = 2 if scale == 'auto' else float(scale)
    frame_bytes = int(first['NAXIS1']*first['NAXIS2']* (3 if native else first.get('NAXIS3',1))*4*scale**2)
    available = os.sysconf('SC_AVPHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')
    resources = dict(pixel_multiplier=scale**2, estimated_disk_bytes=frame_bytes*(max(len(files), effective_entries)+2)*2,
                     estimated_memory_bytes=frame_bytes*8, available_memory_bytes=available,
                     free_disk_bytes=shutil.disk_usage(input_dir).free)
    resources['free_output_disk_bytes'] = shutil.disk_usage(Path(output_path).parent).free
    resources['sufficient'] = frame_bytes*2 < resources['free_output_disk_bytes'] and resources['estimated_disk_bytes'] < resources['free_disk_bytes'] and resources['estimated_memory_bytes'] < available
    decision = analyse(records, cfg, metadata, resources)
    supported = siril.run_siril_script('requires 1.4\nclose', str(work_dir), script_name='drizzle_capability.sps') if decision['enabled'] else None
    if supported is False:
        decision['reasons'].append('Siril 1.4 requis')
        decision['enabled'] = False
        decision['decision'] = 'Drizzle désactivé'
        decision['sampling_out'] = sampling
    decision['siril_drizzle_supported'] = supported
    report = dict(schema_version=1, quality_pipeline_version=2,
                  quality_selection=dict(independent_retained=len(records), effective_stack_entries=effective_entries,
                                         rejected=metadata['unique_inputs']-len(records),
                                         before_drizzle=True), created_utc=datetime.now(timezone.utc).isoformat(), metadata=metadata,
                  resources=resources, analysis=decision, frames=records, analysis_error=error,
                  settings=cfg, status='pending', output=str(output_path))
    report_path = Path(str(output_path)+'.drizzle.json')
    def save():
        temporary = report_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)+'\n')
        temporary.replace(report_path)
    report['per_session'] = {}
    for session in sorted({str(Path(r['source']).parent) for r in records}):
        session_records = [r for r in records if str(Path(r['source']).parent) == session]
        session_metadata = dict(metadata, unique_inputs=len(session_records))
        report['per_session'][session] = analyse(session_records, cfg, session_metadata, resources)
    for i, frame in enumerate(records):
        frame['fx'] = frame['dx'] % 1
        frame['fy'] = frame['dy'] % 1
        same_session = i > 0 and Path(frame['source']).parent == Path(records[i-1]['source']).parent
        frame['ddx'] = frame['dx'] - records[i-1]['dx'] if same_session else None
        frame['ddy'] = frame['dy'] - records[i-1]['dy'] if same_session else None
        frame['displacement_px'] = float(np.hypot(frame['ddx'], frame['ddy'])) if same_session else None
    save()
    reasons_fr = {
        'dithering': 'dithering non démontré', 'image_count': 'nombre de poses insuffisant',
        'coverage': 'couverture sub-pixel insuffisante ou irrégulière',
        'sampling': 'FWHM absente ou bénéfice en résolution incertain',
        'alignment': 'trop de poses rejetées ou mal alignées', 'drift': 'dérive excessive ou inconnue',
        'resources': 'mémoire ou espace disque insuffisant', 'cfa': 'couverture Bayer insuffisante',
        'translation_model': 'rotation ou déformation incompatible avec le diagnostic par translation',
    }
    logging.info(
        'Analyse Drizzle\nImages analysées : %s ; retenues : %s\n'
        'Dithering détecté : %s ; dithers : %s ; fréquence : %s poses\n'
        'Amplitude médiane : %s px ; couverture : %s\n'
        'CFA : %s ; échantillonnage : %s arcsec/px ; FWHM : %s px\n'
        'Score : %.2f ; décision : %s ; pixfrac : %s ; kernel : %s\nRaisons : %s',
        metadata['unique_inputs'], decision['images_retained'], decision['dithering_detected'],
        decision['dither_count'], decision['frequency_frames'], decision['amplitude_median_px'],
        decision['coverage'], metadata['bayer_pattern'], sampling, decision['fwhm_pixels'],
        decision['drizzle_score'], decision['decision'], decision['pixfrac'], decision['kernel'],
        '; '.join(reasons_fr.get(reason, reason) for reason in decision['reasons']) or 'critères satisfaits')
    if cfg.get('drizzle') == 'off':
        logging.info('Drizzle désactivé explicitement (mode off).')
    elif not decision['enabled']:
        logging.info('Drizzle non activé automatiquement : données insuffisantes ou bénéfice incertain.')
    else:
        if decision['reasons']:
            logging.warning('Drizzle forcé malgré ces critères non satisfaits : %s', ', '.join(decision['reasons']))
        logging.info('Drizzle ×%s : pixels et stockage approximatif ×%s', decision['scale'], scale**2)
    if error or not records:
        report['status'] = 'failed'
        save()
        logging.error('Sélection qualité impossible, empilement interrompu : %s', error or 'aucune pose retenue')
        return False
    logging.info('Sélection AVANT Drizzle : %d poses indépendantes, %d entrées pondérées', len(records), effective_entries)
    filters = '-filter-included'
    prefix = sequence
    commands = ['requires 1.4' if decision['enabled'] else 'requires 1.2', f'cd {input_dir}']
    if decision['enabled']:
        options = f"-drizzle -scale={decision['scale']} -pixfrac={decision['pixfrac']} -kernel={decision['kernel']}"
    else:
        options = ''
        if native:
            commands += [f'calibrate {sequence} -debayer -prefix=debayer_', f'register debayer_{sequence} -2pass -transf={cfg.get("align_transform", "affine")}']
            prefix = f'debayer_{sequence}'
    final_stack = re.sub(r'^stack \S+', f'stack r_{prefix}', stack_line)
    commands += [f'seqapplyreg {prefix} {filters} -framing={framing} {options}']
    if not decision['enabled'] and cfg.get('robust_realign', True):
        commands += [f'register r_{prefix} -2pass -transf={cfg.get("align_transform", "affine")}',
                     f'seqapplyreg r_{prefix} -framing={framing}']
        final_stack = re.sub(r'^stack \S+', f'stack r_r_{prefix}', stack_line)
    commands += [final_stack, 'close']
    if supported is False and cfg.get('drizzle') == 'force':
        report['status'] = 'failed'
        save()
        logging.error('Drizzle forcé impossible : Siril 1.4 requis')
        return False
    success = siril.run_siril_script('\n'.join(commands), str(work_dir), script_name='drizzle_stack.sps')
    success = success and any(Path(str(output_path)+ext).exists() for ext in ('.fit', '.fits'))
    report['status'] = 'completed' if success else 'failed'
    if success:
        report['output'] = str(next(Path(str(output_path)+ext) for ext in ('.fit', '.fits') if Path(str(output_path)+ext).exists()))
    save()
    return success
