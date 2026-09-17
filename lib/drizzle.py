"""Conservative Drizzle diagnostics from Siril v4–v7 registration records."""
import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from astropy.io import fits
from lib.siril_sequence import SirilSequence
from lib.quality_filter import quality_mask, apply_quality_weights, filter_registration_geometry, filter_stellar_profiles


def memory_resources(meminfo_path=Path('/proc/meminfo')):
    """Use Linux's reclaim-aware RAM estimate; report swap separately."""
    values = {}
    try:
        for line in Path(meminfo_path).read_text().splitlines():
            key, _, raw = line.partition(':')
            if key in ('MemTotal', 'MemFree', 'MemAvailable', 'SwapTotal', 'SwapFree'):
                fields = raw.split()
                if len(fields) == 2 and fields[1] == 'kB':
                    value = int(fields[0]) * 1024
                    if value >= 0:
                        values[key] = value
    except (OSError, ValueError):
        values = {}
    available = values.get('MemAvailable')
    source = '/proc/meminfo:MemAvailable'
    if available is None:
        available = os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
        source = 'sysconf:SC_AVPHYS_PAGES (RAM libre, repli conservateur)'
    return dict(available_memory_bytes=available, memory_available_source=source,
                total_memory_bytes=values.get('MemTotal'), free_memory_bytes=values.get('MemFree'),
                total_swap_bytes=values.get('SwapTotal'), free_swap_bytes=values.get('SwapFree'),
                swap_included_in_memory_budget=False)


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
        return report.get('quality_pipeline_version') == 10 and report['status'] == 'completed' and report['settings'] == cfg
    except (OSError, ValueError, KeyError):
        return False


def read_registration(path):
    """Compatibility view; all sequence parsing belongs to SirilSequence."""
    sequence = SirilSequence.read(path)
    return (sequence.to_lines(), [image.to_line().split() for image in sequence.images],
            [(image.registration().values, image.registration().homography) for image in sequence.images])


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


def explain_reasons(result, cfg, metadata, resources, records):
    """Describe only failed checks, using the same thresholds as the decision."""
    details = []
    def add(code, message):
        details.append({'criterion': code, 'message': message})

    n = result['images_retained']
    for code in result['reasons']:
        if code == 'sampling':
            fwhm = result['fwhm_pixels']
            if fwhm is None:
                cause = f'{n} poses retenues ; analyse nécessitant au moins 3 poses' if n < 3 else 'aucune FWHM positive disponible sur les poses retenues'
                add(code, f'FWHM médiane indisponible : {cause}.')
            else:
                add(code, f"FWHM médiane = {fwhm:.3f} px ; activation exigeant une valeur strictement inférieure à {float(cfg.get('drizzle_fwhm_limit', 2.5)):.3f} px.")
        elif code == 'alignment':
            total = metadata['unique_inputs']
            add(code, f'Sélection qualité/alignement : {n}/{total} poses indépendantes retenues ({100*n/total:.2f} %) ; minimum requis = 90 % ({int(np.ceil(.9*total))} poses). {total-n} poses écartées au total ; ce critère ne distingue pas rejet qualité et échec d’alignement.')
        elif code == 'drift':
            drift = result['drift_px']
            if drift is None:
                add(code, f'Dérive non calculée : {n} poses retenues ; au moins 3 poses nécessaires.')
            else:
                add(code, f"Dérive lente estimée = {np.linalg.norm(drift):.3f} px (X={drift[0]:+.3f}, Y={drift[1]:+.3f}) ; norme requise strictement inférieure à {float(cfg.get('drizzle_max_drift', 10)):.3f} px.")
        elif code == 'resources':
            checks = [('Mémoire RAM', 'estimated_memory_bytes', 'available_memory_bytes'),
                      ('Disque de travail', 'estimated_disk_bytes', 'free_disk_bytes'),
                      ('Disque de sortie', 'estimated_output_bytes', 'free_output_disk_bytes')]
            for label, need_key, free_key in checks:
                need, free = resources.get(need_key), resources.get(free_key)
                if need is None or free is None:
                    missing = ', '.join(key for key in (need_key, free_key) if resources.get(key) is None)
                    add(code, f'{label} : vérification impossible ; données absentes : {missing}.')
                elif need >= free:
                    add(code, f'{label} : besoin estimé {need/2**30:.3f} Gio ({need} octets), disponible {free/2**30:.3f} Gio ({free} octets) ; le besoin doit être strictement inférieur au disponible.')
        elif code == 'translation_model':
            bad = []
            for record in records:
                if 'homography' not in record:
                    continue
                h = np.array(record['homography'])
                for row, col in [(0,0),(0,1),(1,0),(1,1),(2,0),(2,1)]:
                    target = 1. if row == col else 0.
                    limit = (1e-7 if row == 2 else .01) + 1e-5*abs(target)
                    difference = abs(h[row,col]-target)
                    if difference > limit:
                        bad.append((difference/limit, record, row, col, h[row,col], target, limit))
            if bad:
                _, record, row, col, value, target, limit = max(bad, key=lambda item: item[0])
                count = len({id(item[1]) for item in bad})
                source = record.get('source', f"pose index {record.get('sequence_index', '?')}")
                add(code, f'Modèle de translation : {count}/{n} poses dépassent la tolérance des matrices. Dépassement relatif maximal sur {source} : H[{row},{col}]={value:.9g}, valeur attendue={target:g}, écart={abs(value-target):.9g} > tolérance={limit:.9g}. Ce test ne permet pas à lui seul d’attribuer l’écart à une rotation ou à une déformation.')
        elif code == 'image_count':
            add(code, f"Nombre de poses indépendantes : {n} ; minimum requis = {int(cfg.get('drizzle_min_frames', 30))}.")
        elif code == 'dithering':
            if n < 3:
                add(code, f'Dithering non analysé : {n} poses retenues ; au moins 3 nécessaires.')
            else:
                if result['dither_count'] < 3:
                    add(code, f"Dithering : {result['dither_count']} sauts significatifs détectés ; minimum requis = 3 (seuil de saut = {result['jump_threshold_px']:.3f} px après retrait de la dérive).")
                if result.get('direction_count', 0) < 2:
                    add(code, f"Directions des sauts : {result.get('direction_count', 0)} secteurs angulaires occupés ; minimum requis = 2 secteurs de 45°.")
        elif code in ('coverage', 'cfa'):
            if code == 'cfa' and n < 60:
                add(code, f'CFA : {n} poses indépendantes ; minimum requis = 60.')
            coverages = (result['cfa_coverage'] or {}) if code == 'cfa' else {'sub-pixel': result['coverage']}
            if not coverages:
                add(code, 'Couverture CFA non calculée : moins de 3 poses retenues.')
            for label, cov in coverages.items():
                if cov is None:
                    add(code, f'Couverture {label} non calculée : {n} poses ; au moins 3 nécessaires.')
                    continue
                minimum = .75 if code == 'cfa' else float(cfg.get('drizzle_min_coverage', .75))
                for metric, title, threshold in [('occupied', 'fraction de cellules occupées', minimum),
                                                  ('uniformity', 'uniformité', .6),
                                                  ('independent_phases', 'phases indépendantes', 12)]:
                    if cov[metric] < threshold:
                        add(code, f'Couverture {label} : {title} = {cov[metric]:.4g} ; minimum requis = {threshold:.4g}.')
        else:
            add(code, str(code))
    return details


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
                      dither_count=len(indices), direction_count=len(directions), drift_px=(drift*(n-1)).tolist(),
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
    result['reason_details'] = explain_reasons(result, cfg, metadata, resources, records)
    return result


STACK_STAGES = ('00_inputs', '01_registration', '02_quality', '03_capability', '04_stacking')


def reset_stage(path):
    """Recreate a named stage, unlinking directory aliases rather than following them."""
    path = Path(path)
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def copy_stage_inputs(source, destination):
    """Copy mutable metadata; reference immutable FITS without duplicating pixels."""
    def transfer(src, dst):
        src, dst = Path(src), Path(dst)
        if src.name.lower().endswith(('.fit', '.fits', '.fts', '.fit.fz', '.fits.fz')):
            dst.symlink_to(src.resolve())
        else:
            shutil.copy2(src, dst)
        return str(dst)

    for entry in Path(source).iterdir():
        if entry.resolve() == destination.resolve() or entry.name in STACK_STAGES:
            continue
        if entry.suffix in ('.sps', '.log'):
            continue
        target = destination / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target, copy_function=transfer)
        else:
            transfer(entry, target)


def run_stack(siril, files, cfg, input_dir, work_dir, sequence, output_path, prepare, stack_line, framing, stack_report=None):
    """Register untouched inputs, diagnose, then apply exactly one resampling."""
    if not siril.run_siril_script(prepare+'\nclose', str(input_dir), script_name='01_registration.sps'):
        return False
    registration_dir = Path(input_dir)
    quality_dir = reset_stage(Path(work_dir) / '02_quality')
    stacking_dir = reset_stage(Path(work_dir) / '04_stacking')
    capability_path = reset_stage(Path(work_dir) / '03_capability')
    copy_stage_inputs(registration_dir, quality_dir)
    input_dir = quality_dir
    try:
        image_sequence = SirilSequence.read(Path(input_dir)/f'{sequence}.seq', source_files=files)
    except (OSError, ValueError) as exc:
        logging.error('Séquence Siril illisible : %s', exc)
        return False
    images = image_sequence.images
    headers = [fits.getheader(image.processing_path) for image in images]
    first = headers[0]
    native = first.get('NAXIS', 0) == 2 and first.get('BAYERPAT', '').strip() in ('RGGB','BGGR','GRBG','GBRG')
    focal, pixel = first.get('FOCALLEN'), first.get('XPIXSZ')
    sampling = 206.265*float(pixel)*float(first.get('XBINNING', 1))/float(focal) if focal and pixel and float(focal)>0 else None
    metadata = dict(native_cfa=bool(native), bayer_pattern=first.get('BAYERPAT'),
                    focal_length_mm=focal, pixel_size_um=pixel, binning=[first.get('XBINNING',1),first.get('YBINNING',1)],
                    dimensions=[first.get('NAXIS1'),first.get('NAXIS2')], sampling_arcsec_px=sampling,
                    unique_inputs=len({str(Path(p).resolve()) for p in files}), effective_inputs=len(files))
    records, error, effective_entries = [], None, 0
    geometry_checks = []
    stellar_report = None
    try:
        geometry_checks.append(filter_registration_geometry(image_sequence))
        # Compute quality thresholds on independent exposures, before weighting.
        unique_by_source = {}
        for i, image in enumerate(images):
            unique_by_source.setdefault(str(image.processing_path.resolve()), i)
        unique_indices = list(unique_by_source.values())
        independent_images = [images[i] for i in unique_indices]
        valid_registration = [image.included and image.registration().valid for image in independent_images]
        unique_selection = quality_mask(independent_images, cfg, valid_registration)
        stellar_report = filter_stellar_profiles(independent_images, unique_selection, cfg)
        (quality_dir / 'stellar_profiles.json').write_text(
            json.dumps(stellar_report, indent=2, ensure_ascii=False, allow_nan=False)+'\n')
        selected_sources = {str(image.processing_path.resolve()) for image, keep in zip(independent_images, unique_selection) if keep}
        seen = set()
        for i, image in enumerate(images):
            source = str(image.processing_path.resolve())
            registration = image.registration()
            if source in seen or source not in selected_sources or not image.included or not registration.valid:
                continue
            seen.add(source)
            h = registration.homography
            records.append(dict(source=source, sequence_index=i, date_obs=headers[i].get('DATE-OBS'),
                                dx=float(h[0,2]/h[2,2]), dy=float(h[1,2]/h[2,2]), fwhm=registration.fwhm,
                                roundness=registration.roundness, nbstars=registration.number_of_stars, homography=h.tolist(),
                                dithering_headers={key: headers[i][key] for key in headers[i] if 'DITH' in key.upper()}))
        records.sort(key=lambda r: (r['date_obs'] or '', r['source']))
        effective_entries = apply_quality_weights(image_sequence, records, cfg)
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
    memory = memory_resources()
    available = memory['available_memory_bytes']
    logging.info('RAM disponible pour Drizzle : %.2f Gio (source : %s) ; swap libre : %s, non ajouté au budget RAM.',
                 available / 2**30, memory['memory_available_source'],
                 f"{memory['free_swap_bytes'] / 2**30:.2f} Gio" if memory['free_swap_bytes'] is not None else 'inconnu')
    resources = dict(pixel_multiplier=scale**2, estimated_disk_bytes=frame_bytes*(max(len(files), effective_entries)+2)*2,
                     estimated_memory_bytes=frame_bytes*8, **memory,
                     free_disk_bytes=shutil.disk_usage(input_dir).free)
    resources['estimated_output_bytes'] = frame_bytes*2
    resources['free_output_disk_bytes'] = shutil.disk_usage(Path(output_path).parent).free
    resources['sufficient'] = frame_bytes*2 < resources['free_output_disk_bytes'] and resources['estimated_disk_bytes'] < resources['free_disk_bytes'] and resources['estimated_memory_bytes'] < available
    decision = analyse(records, cfg, metadata, resources)
    supported, capability_dir = None, None
    if decision['enabled']:
        capability_dir = capability_path
        supported = siril.run_siril_script(f'requires 1.4\ncd {capability_dir}\nclose', str(capability_dir), script_name='03_capability.sps')
    if capability_dir is None:
        (capability_path / 'SKIPPED.txt').write_text('Étape non exécutée : Drizzle non sélectionné après analyse qualité.\n')
    if supported is False:
        decision['reasons'].append('Siril 1.4 requis')
        decision['reason_details'].append({'criterion': 'siril_version', 'message': 'Vérification Siril échouée : le script requires 1.4 a échoué. Consulter la sortie de 03_capability.sps pour distinguer version incompatible et erreur d’exécution.'})
        decision['enabled'] = False
        decision['decision'] = 'Drizzle désactivé'
        decision['sampling_out'] = sampling
    decision['siril_drizzle_supported'] = supported
    report = dict(schema_version=1, quality_pipeline_version=10, geometry_checks=geometry_checks,
                  stellar_profiles=dict(report=str(quality_dir / 'stellar_profiles.json'),
                                        **{k:v for k,v in stellar_report.items() if k not in ('frames', 'references')}) if stellar_report is not None else None,
                  stages=dict(registration=str(registration_dir), quality=str(quality_dir), stacking=str(stacking_dir),
                              capability=str(capability_path), capability_executed=capability_dir is not None),
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
    if error:
        decision['reason_details'].insert(0, {'criterion': 'analysis_error', 'message': f'Analyse interrompue : {error}.'})
    save()
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
        '\n  - ' + '\n  - '.join(item['message'] for item in decision['reason_details']) if decision['reason_details'] else 'tous les critères sont satisfaits')
    if cfg.get('drizzle') == 'off':
        logging.info('Drizzle désactivé explicitement (mode off).')
    elif not decision['enabled']:
        logging.info('Drizzle non activé automatiquement : voir les critères détaillés ci-dessus.')
    else:
        if decision['reasons']:
            logging.warning('Drizzle forcé malgré ces critères non satisfaits : %s', '; '.join(item['message'] for item in decision['reason_details']))
        logging.info('Drizzle ×%s : pixels et stockage approximatif ×%s', decision['scale'], scale**2)
    if error or not records:
        report['status'] = 'failed'
        save()
        logging.error('Sélection qualité impossible, empilement interrompu : %s', error or 'aucune pose retenue')
        return False
    logging.info(
        'Sélection AVANT Drizzle\n'
        'Images distinctes retenues      : %d\n'
        'Entrées après pondération       : %d\n'
        'Dont répétitions pour pondération : %d',
        len(records), effective_entries, effective_entries - len(records),
    )
    copy_stage_inputs(quality_dir, stacking_dir)
    input_dir = stacking_dir
    prefix = sequence
    required = 'requires 1.4' if decision['enabled'] else 'requires 1.2'
    def execute(lines, script_name):
        return siril.run_siril_script('\n'.join([required, f'cd {stacking_dir}', *lines, 'close']),
                                      str(stacking_dir), script_name=script_name)

    allowed = {image.number for image in image_sequence.images if image.included}
    sources_by_number = {image.number: str(image.processing_path.resolve()) for image in image_sequence.images}
    def validate_new_registration(new_prefix):
        nonlocal allowed
        try:
            aligned = SirilSequence.read(stacking_dir/f'{new_prefix}.seq')
            check = filter_registration_geometry(aligned, allowed_numbers=allowed)
            geometry_checks.append(check)
            allowed = {image.number for image in aligned.images if image.included}
            save()
            if not allowed:
                raise ValueError('aucune image après contrôle géométrique')
            return True
        except (OSError, ValueError, IndexError) as exc:
            logging.error('Alignement inutilisable avant rééchantillonnage : %s', exc)
            report['status'] = 'failed'
            report['alignment_error'] = str(exc)
            save()
            return False

    def failed_stage():
        report['status'] = 'failed'
        save()
        return False

    if supported is False and cfg.get('drizzle') == 'force':
        logging.error('Drizzle forcé impossible : Siril 1.4 requis')
        return failed_stage()
    if decision['enabled']:
        options = f"-drizzle -scale={decision['scale']} -pixfrac={decision['pixfrac']} -kernel={decision['kernel']}"
    else:
        options = ''
        if native:
            if not execute([f'calibrate {sequence} -debayer -prefix=debayer_',
                            f'register debayer_{sequence} -2pass -transf={cfg.get("align_transform", "affine")}'],
                           '04_debayer_registration.sps'):
                return failed_stage()
            prefix = f'debayer_{sequence}'
            if not validate_new_registration(prefix):
                return False
    if not decision['enabled'] and cfg.get('robust_realign', True):
        if not execute([f'seqapplyreg {prefix} -filter-included -framing={framing}',
                        f'register r_{prefix} -2pass -transf={cfg.get("align_transform", "affine")}'],
                       '04_realign.sps'):
            return failed_stage()
        prefix = f'r_{prefix}'
        if not validate_new_registration(prefix):
            return False
    final_sources = {sources_by_number[number] for number in allowed}
    report['final_selection'] = dict(independent_retained=len(final_sources), effective_stack_entries=len(allowed),
                                    sources=sorted(final_sources))
    if stack_report is not None:
        stack_report.update(kept_unique_for_stack=len(final_sources), kept_effective_for_stack=len(allowed),
                            rejected_registration_quality=metadata['unique_inputs']-len(final_sources),
                            weighted_extra_entries=len(allowed)-len(final_sources))
    save()
    final_stack = re.sub(r'^stack \S+', f'stack r_{prefix}', stack_line)
    success = execute([f'seqapplyreg {prefix} -filter-included -framing={framing} {options}', final_stack],
                      '04_stacking.sps')
    success = success and any(Path(str(output_path)+ext).exists() for ext in ('.fit', '.fits'))
    report['status'] = 'completed' if success else 'failed'
    if success:
        report['output'] = str(next(Path(str(output_path)+ext) for ext in ('.fit', '.fits') if Path(str(output_path)+ext).exists()))
    save()
    return success
