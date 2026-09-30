#!/usr/bin/env python3
"""Lecture et analyse des guide logs KStars/PHD2, sans dépendance Siril."""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import glob
from html import escape
import math
from pathlib import Path
import re
import sys

import numpy as np


@dataclass
class Section:
    path: Path
    number: int
    kind: str
    start: datetime
    end: datetime | None = None
    scale: float | None = None
    rows: list[dict] = field(default_factory=list)
    metadata: list[str] = field(default_factory=list)
    events: list[tuple[float | None, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    calibration: Section | None = field(default=None, repr=False, compare=False)

    @property
    def label(self):
        kind = 'Guidage' if self.kind == 'guide' else 'Calibration'
        return f'{self.path.name} · {self.number} · {kind} {self.start:%H:%M:%S}'


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else math.nan
    except (ValueError, TypeError):
        return math.nan


def read_log(path):
    """Conserve chaque section, y compris vide, et les lignes INFO sans date inventée."""
    path = Path(path).expanduser().resolve()
    sections, current, columns = [], None, None
    for line_no, raw in enumerate(path.read_text(encoding='utf-8-sig', errors='replace').splitlines(), 1):
        line = raw.strip()
        match = re.match(r'(Guiding|Calibration) Begins at (.+)', line)
        if match:
            try:
                start = datetime.fromisoformat(match[2])
            except ValueError as exc:
                raise ValueError(f'{path.name}:{line_no}: date invalide') from exc
            current = Section(path, len(sections) + 1, 'guide' if match[1] == 'Guiding' else 'calibration', start)
            sections.append(current)
            columns = None
        elif current is not None and line:
            if line.startswith('Guiding Ends at '):
                try:
                    current.end = datetime.fromisoformat(line.removeprefix('Guiding Ends at '))
                except ValueError:
                    current.warnings.append(f'Ligne {line_no} : date de fin invalide')
                columns = None
            elif line.startswith('Frame,') or line.startswith('Direction,Step,'):
                columns = next(csv.reader([line]))
            elif line.startswith('INFO:'):
                last = current.rows[-1].get('Time') if current.rows else None
                current.events.append((last, line))
            elif columns and (line[0].isdigit() or re.match(r'^(West|East|North|South),', line)):
                values = next(csv.reader([line]))
                if len(values) < len(columns):
                    current.warnings.append(f'Ligne {line_no} : ligne CSV incomplète ignorée')
                    continue
                row = dict(zip(columns, values))
                for key in set(row) - {'mount', 'Direction', 'RADirection', 'DECDirection', 'ErrorDescription'}:
                    row[key] = number(row[key])
                required = ('Time', 'Frame') if current.kind == 'guide' else ('Step', 'dx', 'dy')
                if any(not math.isfinite(row.get(key, math.nan)) for key in required):
                    current.warnings.append(f'Ligne {line_no} : mesure invalide ignorée')
                    continue
                if current.kind == 'guide' and (row['Time'] < 0 or (current.rows and row['Time'] < current.rows[-1]['Time'])):
                    current.warnings.append(f'Ligne {line_no} : temps décroissant ou négatif ignoré')
                    continue
                current.rows.append(row)
            else:
                current.metadata.append(line)
                scale = re.search(r'Pixel scale\s*=\s*([\d.eE+-]+)', line)
                if scale:
                    value = number(scale[1])
                    if math.isfinite(value) and value > 0:
                        current.scale = value
    if not sections:
        raise ValueError(f'{path.name} : aucune section de guidage ou calibration reconnue')
    last_calibration = None
    for section in sections:
        if section.kind == 'calibration':
            last_calibration = section
        else:
            section.calibration = last_calibration
        if section.kind == 'guide' and section.scale is None:
            section.warnings.append('Échelle absente : erreurs disponibles uniquement en pixels')
    return sections


def discover(inputs):
    paths = []
    for item in inputs:
        expanded = str(Path(item).expanduser())
        matches = sorted(glob.glob(expanded))
        if not matches:
            raise ValueError(f'Chemin introuvable : {item}')
        for match in matches:
            path = Path(match)
            paths.extend(sorted(path.glob('*.txt')) if path.is_dir() else [path])
    return list(dict.fromkeys(path.resolve() for path in paths))


def guide_data(section, unit='arcsec'):
    """Distances PHD2 en pixels ; DROP/erreurs exclus, jamais remplacés par zéro."""
    rows = section.rows
    t = np.array([r['Time'] for r in rows], dtype=float)
    factor = section.scale if unit == 'arcsec' else 1.0
    factor = factor if factor is not None else math.nan
    valid = np.array([r.get('mount', '').lower() == 'mount' and r.get('ErrorCode') == 0
                      and all(math.isfinite(r.get(k, math.nan)) for k in ('RARawDistance', 'DECRawDistance'))
                      for r in rows], dtype=bool)
    ra = np.array([r.get('RARawDistance', math.nan) for r in rows]) * factor
    dec = np.array([r.get('DECRawDistance', math.nan) for r in rows]) * factor
    ra[~valid], dec[~valid] = math.nan, math.nan
    def pulse(axis, positive, negative):
        result = []
        for row, ok in zip(rows, valid):
            duration = row.get(axis + 'Duration', math.nan)
            direction = row.get(axis + 'Direction')
            result.append((0 if duration == 0 else duration if direction == positive else
                           -duration if direction == negative else math.nan) if ok else math.nan)
        return np.array(result)
    snr = np.array([r.get('SNR', math.nan) if ok else math.nan for r, ok in zip(rows, valid)])
    return dict(time=t, ra=ra, dec=dec, ra_pulse=pulse('RA', 'E', 'W'),
                dec_pulse=pulse('DEC', 'N', 'S'), snr=snr, valid=valid)


# États observés uniquement : ne pas interpoler une panne entre deux mesures.
TRACKING_STATES = ((0.0, 'Rejet / erreur', '#dc2626'),
                   (0.5, 'Indéterminé', '#9ca3af'),
                   (1.0, 'Mesure valide', '#16a34a'))


def tracking_status(section):
    """Ne déduit aucun nombre d'étoiles de StarMass ou du SNR."""
    valid = guide_data(section, 'px')['valid']
    states = []
    for row, ok in zip(section.rows, valid):
        device = row.get('mount', '').lower()
        error = row.get('ErrorCode', math.nan)
        rejected = device == 'drop' or (device == 'mount' and math.isfinite(error) and error != 0)
        states.append(0.0 if rejected else 1.0 if ok else 0.5)
    return np.array(states)


def statistics(section, unit='arcsec', low=-math.inf, high=math.inf):
    data = guide_data(section, unit)
    window = (data['time'] >= low) & (data['time'] <= high)
    good = window & np.isfinite(data['ra']) & np.isfinite(data['dec'])
    ra, dec = data['ra'][good], data['dec'][good]
    result = dict(frames=int(window.sum()), valid=int(good.sum()),
                  rejected=int((window & ~data['valid']).sum()))
    result.update({key: math.nan for key in ('rms_ra', 'rms_dec', 'rms_total', 'bias_ra', 'bias_dec', 'p95')})
    if len(ra):
        result.update(rms_ra=float(np.sqrt(np.mean(ra ** 2))), rms_dec=float(np.sqrt(np.mean(dec ** 2))),
                      rms_total=float(np.sqrt(np.mean(ra ** 2 + dec ** 2))),
                      bias_ra=float(np.mean(ra)), bias_dec=float(np.mean(dec)),
                      p95=float(np.percentile(np.hypot(ra, dec), 95)))
    return result


def rolling_rms(data, window=40):
    """RMS autour de zéro ; remise à zéro aux rejets et aux pauses > 30 secondes."""
    result, history, previous = [], [], None
    for t, ra, dec in zip(data['time'], data['ra'], data['dec']):
        if previous is not None and t - previous > 30:
            history = []
        previous = t
        if not (math.isfinite(ra) and math.isfinite(dec)):
            history = []
            result.append(math.nan)
        else:
            history.append(ra * ra + dec * dec)
            history = history[-window:]
            result.append(math.sqrt(sum(history) / len(history)))
    return np.array(result)


def broken_line(x, y, times):
    """Ne relie pas les mesures séparées par une interruption de plus de 30 s."""
    xx, yy = [], []
    for i, (a, b) in enumerate(zip(x, y)):
        if i and times[i] - times[i - 1] > 30:
            xx.append(a)
            yy.append(math.nan)
        xx.append(a)
        yy.append(b)
    return xx, yy


def summary(section, unit='arcsec', low=-math.inf, high=math.inf):
    if section.kind != 'guide':
        return f'{len(section.rows)} pas de calibration (coordonnées en pixels)'
    s = statistics(section, unit, low, high)
    fmt = lambda value: f'{value:.3f}' if math.isfinite(value) else '—'
    return (f"{s['frames']} mesures · {s['valid']} exploitables · {s['rejected']} rejets/erreurs · "
            f"RMS AD {fmt(s['rms_ra'])} / DEC {fmt(s['rms_dec'])} / total {fmt(s['rms_total'])} {unit} · "
            f"Biais AD {fmt(s['bias_ra'])} / DEC {fmt(s['bias_dec'])} · rayon P95 {fmt(s['p95'])}")


def calibrations_for(sections):
    """Calibrations sélectionnées ou précédant les guidages, sans doublons."""
    result = {}
    for section in sections:
        calibration = section if section.kind == 'calibration' else section.calibration
        if calibration is not None:
            result[(calibration.path, calibration.number)] = calibration
    return list(result.values())


def calibration_details(section):
    if section.kind != 'guide':
        return []
    calibration = section.calibration
    if calibration is None:
        return ['Aucune calibration précédente enregistrée dans ce fichier.']
    return [f'Calibration précédente : {calibration.label}',
            summary(calibration), *calibration.metadata, *calibration.warnings]


def build_figure(sections, relative=False, unit='arcsec'):
    from plotly.subplots import make_subplots
    import plotly.graph_objects as go
    fig = make_subplots(rows=5, cols=2, specs=[[{}, {'rowspan': 2}], [{}, None], [{}, {'rowspan': 3}], [{}, None], [{}, None]],
                        subplot_titles=('Erreurs AD / DEC', 'Dispersion du guidage', 'Impulsions E+/W−, N+/S−',
                                        'RMS total glissant (40 mesures)', 'Calibration (pixels)', 'SNR', 'État du suivi'))
    trace_groups = []
    for section in sections:
        first_trace = len(fig.data)
        label = escape(section.label)
        if section.kind == 'calibration':
            trace_groups.append([])
            continue
        if not section.rows:
            trace_groups.append([])
            continue
        d = guide_data(section, unit)
        x = d['time'].tolist() if relative else [(section.start + timedelta(seconds=float(t))).isoformat() for t in d['time']]
        for key, name, row in [('ra', 'AD', 1), ('dec', 'DEC', 1), ('ra_pulse', 'Impulsion AD', 2),
                               ('dec_pulse', 'Impulsion DEC', 2), ('rms', 'RMS total', 3), ('snr', 'SNR', 4)]:
            y = rolling_rms(d) if key == 'rms' else d[key]
            xx, yy = broken_line(x, y, d['time'])
            fig.add_trace(go.Scattergl(x=xx, y=yy, mode='lines', name=f'{label} · {name}',
                                      connectgaps=False), row=row, col=1)
        states = tracking_status(section)
        for value, title, color in TRACKING_STATES:
            indices = np.flatnonzero(states == value)
            if len(indices):
                fig.add_trace(go.Scattergl(
                    x=[x[i] for i in indices], y=[value] * len(indices), mode='markers',
                    marker=dict(color=color, size=5), name=f'{label} · {title}',
                    text=[escape(f"Frame {section.rows[i]['Frame']:g} · {section.rows[i].get('mount')} · "
                                 f"ErrorCode={section.rows[i].get('ErrorCode')} · "
                                 f"{section.rows[i].get('ErrorDescription', '')}") for i in indices],
                    hovertemplate='%{x}<br>%{text}<extra></extra>'), row=5, col=1)
        rejected = np.flatnonzero(~d['valid'])
        if len(rejected):
            fig.add_trace(go.Scatter(x=[x[i] for i in rejected], y=[0] * len(rejected), mode='markers',
                                    marker=dict(color='red', symbol='x'), name=f'{label} · rejets (repère à zéro)',
                                    text=[escape(str(section.rows[i])) for i in rejected]), row=1, col=1)
        fig.add_trace(go.Scattergl(x=d['ra'], y=d['dec'], mode='markers', marker=dict(size=3, opacity=.45),
                                  name=f'{label} · dispersion'), row=1, col=2)
        trace_groups.append(list(range(first_trace, len(fig.data))))
    # Une seule trajectoire par calibration, visible avec chacun de ses guidages.
    for calibration in calibrations_for(sections):
        first_trace = len(fig.data)
        for direction in ('West', 'East', 'North', 'South'):
            rows = [r for r in calibration.rows if r.get('Direction') == direction]
            if rows:
                fig.add_trace(go.Scatter(x=[r['dx'] for r in rows], y=[r['dy'] for r in rows],
                                         mode='lines+markers',
                                         name=f'{escape(calibration.label)} · {direction}'), row=3, col=2)
        indices = list(range(first_trace, len(fig.data)))
        for section, group in zip(sections, trace_groups):
            if section is calibration or section.calibration is calibration:
                group.extend(indices)
    for row, title in [(1, unit), (2, 'ms'), (3, unit), (4, 'SNR'), (5, 'État')]:
        fig.update_yaxes(title_text=title, row=row, col=1)
        if row > 1:
            fig.update_xaxes(matches='x', row=row, col=1)
    fig.update_xaxes(title_text='Secondes depuis le début de chaque séquence' if relative else 'Heure locale du journal', row=5, col=1)
    fig.update_yaxes(tickvals=[v for v, _, _ in TRACKING_STATES],
                     ticktext=[t for _, t, _ in TRACKING_STATES], range=[-.2, 1.2], row=5, col=1)
    fig.update_xaxes(title_text=f'AD ({unit})', row=1, col=2)
    fig.update_yaxes(title_text=f'DEC ({unit})', scaleanchor='x2', scaleratio=1, row=1, col=2)
    fig.update_xaxes(title_text='dx (px)', row=3, col=2)
    fig.update_yaxes(title_text='dy (px)', scaleanchor='x5', scaleratio=1, row=3, col=2)
    buttons = [dict(label='Toutes les sections', method='update', args=[{'visible': [True] * len(fig.data)}])]
    buttons.extend(dict(label=escape(s.label), method='update',
                        args=[{'visible': [i in indices for i in range(len(fig.data))]}])
                   for s, indices in zip(sections, trace_groups))
    fig.update_layout(template='plotly_dark', height=1300, title='KStars / Ekos — Guide logs',
                      updatemenus=[dict(buttons=buttons, x=0, y=1.12, xanchor='left')],
                      legend=dict(orientation='h', y=-.12), margin=dict(b=180))
    return fig


def write_report(sections, output, relative=False, unit='arcsec'):
    output = Path(output).expanduser().resolve()
    if any(output == s.path.resolve() or (output.exists() and output.samefile(s.path)) for s in sections):
        raise ValueError('Le rapport ne peut pas remplacer un journal source')
    if output.suffix.lower() not in ('.html', '.htm'):
        raise ValueError('Le rapport doit avoir une extension .html ou .htm')
    figure = build_figure(sections, relative, unit)
    details = []
    for s in sections:
        text = [summary(s, unit), *s.metadata, *s.warnings, *calibration_details(s)]
        text.extend(f'Après la mesure t={t}s (heure exacte inconnue) : {event}' if t is not None else event
                    for t, event in s.events)
        details.append(f'<details><summary>{escape(s.label)} — {escape(summary(s, unit))}</summary><pre>'
                       + escape('\n'.join(text)) + '</pre></details>')
    html = ('<!doctype html><html lang="fr"><meta charset="utf-8"><title>Analyse des guide logs</title>'
            '<style>body{background:#111827;color:#e5e7eb;font:14px sans-serif;margin:24px}'
            'details{margin:12px 0}pre{white-space:pre-wrap}</style>'
            '<h1>Analyse des guide logs KStars / Ekos</h1>'
            '<p>Statistiques des séquences complètes, indépendantes du zoom. RMS autour de zéro ; '
            'rejets exclus, dithering conservé. Les distances utilisent l’échelle de chaque section. '
            'Une section vide ne prouve pas l’absence de guidage. Les dates restent celles du journal.</p>'
            + ''.join(details) + figure.to_html(full_html=False, include_plotlyjs=True) + '</html>')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding='utf-8')
    return output


DEFAULT_GUIDE_DIRECTORY = '~/.local/share/kstars/guidelogs'


def default_guide_directory():
    directory = Path(DEFAULT_GUIDE_DIRECTORY).expanduser()
    alternate = directory.with_name('guideLogs')
    return alternate if not directory.is_dir() and alternate.is_dir() else directory


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog='Sans entrée : charge les fichiers *.txt du répertoire KStars par défaut. '
               'La variante guideLogs est utilisée si guidelogs est absent. '
               'Si le dossier est absent ou vide, la fenêtre permet de choisir des fichiers.')
    parser.add_argument('inputs', nargs='*', help=f'Fichiers .txt, dossiers ou motifs glob (défaut : {DEFAULT_GUIDE_DIRECTORY}/)' )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--gui', action='store_true', help='Interface native (défaut)')
    mode.add_argument('--html', action='store_true', help='Rapport HTML sans interface')
    parser.add_argument('-o', '--output', default='out/guide-log-analyze.html')
    parser.add_argument('--relative', action='store_true')
    parser.add_argument('--unit', choices=['arcsec', 'px'], default='arcsec')
    args = parser.parse_args(argv)
    try:
        if args.inputs:
            paths = discover(args.inputs)
        else:
            directory = default_guide_directory()
            paths = discover([str(directory)]) if directory.is_dir() else []
            if not paths:
                print(f'Aucun guide log (*.txt) dans le répertoire par défaut : {directory}', file=sys.stderr)
        sections = [s for path in paths for s in read_log(path)]
        if args.html:
            if not sections:
                parser.error('Fournir au moins un guide log pour le rapport HTML')
            print(write_report(sections, args.output, args.relative, args.unit))
        else:
            import tkinter as tk
            from guideLogAnalyzeGui import GuideWindow
            root = tk.Tk()
            GuideWindow(root, sections, args.relative, args.unit)
            root.mainloop()
    except (OSError, ValueError, ImportError) as exc:
        parser.exit(1, f'Erreur : {exc}\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
