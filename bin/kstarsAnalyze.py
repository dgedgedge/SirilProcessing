#!/usr/bin/env python3
"""Visualiseur graphique et HTML hors connexion des journaux .analyze de KStars/Ekos."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plotly.graph_objects import Figure


import argparse
import json
import math
import sys
import unicodedata
import webbrowser
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from glob import glob
from html import escape
from pathlib import Path

# Ligne, début/fin en secondes, état et détails de l'intervalle.
TimelineInterval = tuple[str, float, float, str, str]


@dataclass
class Event:
    """Événement horodaté en secondes depuis le début local de la session."""

    kind: str
    seconds: float
    fields: list[str]
    line: int


@dataclass
class Session:
    """Journal .analyze et ses avertissements, sans conversion implicite de fuseau."""

    path: Path
    start: datetime
    timezone: str
    events: list[Event]
    warnings: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        """Retourne le dernier horodatage enregistré en secondes, ou zéro si vide."""
        return max((e.seconds for e in self.events), default=0)


STATE_ROWS = {
    "MountState": "Monture",
    "GuideState": "Guidage",
    "AlignState": "Alignement",
    "MeridianFlipState": "Retournement",
}
ROWS = [
    "Scheduler",
    "Monture",
    "Retournement",
    "Guidage",
    "Alignement",
    "Mise au point",
    "Acquisition",
]
# Indices dans les champs après le temps ; unités issues du lecteur officiel KStars.
METRICS = {
    "GuideStats": [
        (0, "Erreur AD", "arcsec"),
        (1, "Erreur DEC", "arcsec"),
        (2, "Impulsion AD", "ms"),
        (3, "Impulsion DEC", "ms"),
        (4, "SNR", "rapport"),
        (5, "Fond du ciel", "ADU"),
        (6, "Étoiles guidage", "nombre"),
    ],
    "CaptureComplete": [
        (2, "HFR", "px"),
        (4, "Étoiles image", "nombre"),
        (5, "Médiane image", "ADU"),
        (6, "Excentricité", "rapport"),
    ],
    "Temperature": [(0, "Température", "°C")],
    "MountCoords": [
        (0, "AD monture", "°"),
        (1, "DEC monture", "°"),
        (2, "Azimut", "°"),
        (3, "Altitude", "°"),
    ],
    "TargetDistance": [(0, "Distance cible", "arcsec")],
}


MOUNT_STYLES = {
    "idle": ("À l’arrêt", "#9ca3af"),
    "tracking": ("Suivi", "#34d399"),
    "moving": ("En mouvement", "#fbbf24"),
    "slewing": ("Pointage", "#60a5fa"),
    "parking": ("Parcage", "#c084fc"),
    "parked": ("Parquée", "#64748b"),
    "error": ("Erreur", "#f87171"),
    "unknown": ("État inconnu", "#d1d5db"),
}


def mount_style(state: str) -> tuple[str, str]:
    """Normalise l’état de monture et retourne son libellé français et sa couleur."""
    text = (
        "".join(
            c
            for c in unicodedata.normalize("NFKD", state.casefold())
            if not unicodedata.combining(c)
        )
        .replace("’", "'")
        .strip()
    )
    aliases = {
        "a l'arret": "idle",
        "idle": "idle",
        "stopped": "idle",
        "en cours de suivi": "tracking",
        "suivi": "tracking",
        "tracking": "tracking",
        "en mouvement": "moving",
        "moving": "moving",
        "pointage": "slewing",
        "slewing": "slewing",
        "parcage": "parking",
        "parking": "parking",
        "parquee": "parked",
        "parque": "parked",
        "parked": "parked",
        "erreur": "error",
        "error": "error",
    }
    return MOUNT_STYLES[aliases.get(text.removeprefix("mount_"), "unknown")]


def timeline_color(row: str, state: str) -> str:
    """Choisit la couleur d’un intervalle selon son équipement et son état."""
    if row == "Monture":
        return mount_style(state)[1]
    if any(
        w in state.casefold()
        for w in ("abandon", "erreur", "error", "abort", "fail", "interromp", "échec")
    ):
        return "#f87171"
    return [
        "#60a5fa",
        "#34d399",
        "#fbbf24",
        "#c084fc",
        "#fb7185",
        "#22d3ee",
        "#fb923c",
    ][ROWS.index(row)]


def read_session(path: Path | str) -> Session:
    """Conserve les événements inconnus ; signale les lignes illisibles."""
    path = Path(path)
    events, warnings = [], []
    start, zone = None, ""
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        # KStars écrit du texte séparé par des virgules, sans échappement CSV.
        parts = line.split(",")
        try:
            if parts[0] == "AnalyzeStartTime":
                if start is not None:
                    raise ValueError("plusieurs en-têtes de session")
                start = datetime.fromisoformat(parts[1])
                zone = parts[2] if len(parts) > 2 else ""
                continue
            seconds = float(parts[1])
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError("temps invalide")
            events.append(Event(parts[0], seconds, parts[2:], number))
        except (ValueError, IndexError) as exc:
            warnings.append(f"Ligne {number} ignorée : {exc}")
    if start is None:
        raise ValueError(f"{path} : en-tête AnalyzeStartTime absent ou invalide")
    return Session(path, start, zone, sorted(events, key=lambda e: e.seconds), warnings)


def intervals(session: Session) -> list[TimelineInterval]:
    """Retourne (ligne, début, fin, état, détails), sans relier les sessions."""
    pending, result = {}, []

    def close(
        key: tuple[str, str], end: float, status: str | None = None, details: str = ""
    ) -> None:
        """Ferme un intervalle actif avec sa fin observée et ses détails accumulés."""
        if key in pending:
            begin, old_status, old_details = pending.pop(key)
            result.append(
                (
                    key[0],
                    begin,
                    end,
                    status or old_status,
                    old_details + (" · " + details if details else ""),
                )
            )

    for e in session.events:
        f, k, t = e.fields, e.kind, e.seconds
        detail = ", ".join(f)
        if k in STATE_ROWS and f:
            key = (STATE_ROWS[k], "")
            if k == "AlignState":
                # Terminal states are markers, not ongoing alignment activity.
                # Success may be followed by syncing/slewing and a new solve.
                terminal = {
                    "succès",
                    "terminé",
                    "échec",
                    "interrompu",
                    "suspendu",
                    "à l'arrêt",
                    "success",
                    "successful",
                    "complete",
                    "completed",
                    "failed",
                    "aborted",
                    "suspended",
                    "idle",
                }
                close(key, t, details=f"État suivant : {detail}")
                if f[0].strip().casefold().replace("’", "'") in terminal:
                    continue
            else:
                close(key, t)
            pending[key] = (t, f[0], detail)
        elif k == "SchedulerJobStart" and f:
            key = ("Scheduler", f[0])
            close(key, t, "Interrompue")
            pending[key] = (t, f[0], f"Tâche : {f[0]}")
        elif k == "SchedulerJobEnd" and f:
            close(
                ("Scheduler", f[0]),
                t,
                details=f"Fin : {f[1]}" if len(f) > 1 and f[1] else "Tâche terminée",
            )
        elif k == "CaptureStarting" and len(f) >= 2:
            key = ("Acquisition", f[2] if len(f) > 2 else "")
            close(key, t, "Interrompue")
            pending[key] = (
                t,
                "Acquisition",
                f"Pose {f[0]} s · Filtre {f[1] or '—'} · {detail}",
            )
        elif k in ("CaptureComplete", "CaptureAborted"):
            index = 7 if k == "CaptureComplete" else 1
            key = ("Acquisition", f[index] if len(f) > index else "")
            state = "Terminée" if k == "CaptureComplete" else "Abandonnée"
            if k == "CaptureComplete" and len(f) > 3:
                kind = capture_type(f[3])
                if kind != "Autre":
                    state += f" · {kind}"
            close(key, t, state, detail)
        elif k == "AutofocusStarting" and len(f) >= 2:
            key = ("Mise au point", f[4] if len(f) > 4 else "")
            close(key, t, "Interrompue")
            pending[key] = (t, "Mise au point", detail)
        elif k in ("AutofocusComplete", "AutofocusAborted"):
            key = ("Mise au point", f[8] if len(f) > 8 else "")
            close(
                key, t, "Terminée" if k == "AutofocusComplete" else "Abandonnée", detail
            )
    for key in list(pending):
        close(key, session.duration, details="Fin du journal ; fin réelle inconnue")
    return result


CAPTURE_PALETTES = {
    "Light": ("#34d399", "#059669"),
    "Flat": ("#fde047", "#f59e0b"),
    "Dark": ("#c084fc", "#7c3aed"),
    "Autre": ("#22d3ee", "#fb923c"),
}


def capture_type(filename: str) -> str:
    """Match complete directory names, case-insensitively (POSIX or Windows)."""
    directories = filename.replace(chr(92), "/").casefold().split("/")[:-1]
    for directory in reversed(directories):
        if directory in ("light", "flat", "dark"):
            return directory.title()
    return "Autre"


def capture_palette_key(state: str) -> str:
    """Extrait le type Light/Flat/Dark d’un état, sinon utilise la palette générique."""
    kind = state.rsplit(" · ", 1)[-1]
    return kind if kind in CAPTURE_PALETTES else "Autre"


def colored_intervals(
    spans: Sequence[TimelineInterval],
) -> Iterator[tuple[TimelineInterval, str]]:
    """Stable alternating acquisition colors, ordered by start within a session."""
    acquisition_indices = Counter()
    for span in sorted(spans, key=lambda item: item[1]):
        row, _, _, state, _ = span
        color = timeline_color(row, state)
        if row == "Acquisition":
            kind = capture_palette_key(state)
            if color != "#f87171":
                color = CAPTURE_PALETTES[kind][acquisition_indices[kind] % 2]
            acquisition_indices[kind] += 1
        yield (*span, color)


def measurements(session: Session) -> dict[tuple[str, str], list[tuple[float, float]]]:
    """Regroupe les mesures finies par nom et unité, en gardant leurs temps relatifs.

    Les sentinelles négatives de HFR et d’excentricité sont omises. Les autres
    valeurs restent dans les unités du journal."""
    series = defaultdict(list)
    for e in session.events:
        for index, name, unit in METRICS.get(e.kind, []):
            if index >= len(e.fields):
                continue  # Champs optionnels des anciennes versions.
            try:
                value = float(e.fields[index])
                if not math.isfinite(value):
                    raise ValueError("valeur non finie")
                if value < 0 and name in ("HFR", "Excentricité", "Étoiles image"):
                    continue  # -1 indique une mesure indisponible.
                series[name, unit].append((e.seconds, value))
            except ValueError:
                session.warnings.append(f"Ligne {e.line} : mesure {name} invalide")
    rms = guiding_rms(session)
    if rms:
        series["RMS", "arcsec"] = rms
    return series


def guiding_rms(session: Session) -> list[tuple[float, float]]:
    """Reproduit RmsFilter de KStars : dispersion AD/DEC sur 40 points.

    Le filtre utilise les variances corrigées (N-1), pas la moyenne des
    carrés par rapport à zéro. Une interruption > 30 s réinitialise le filtre.
    """
    window = deque(maxlen=40)
    result = []
    previous = None
    for event in session.events:
        if event.kind != "GuideStats":
            continue
        try:
            ra, dec = map(float, event.fields[:2])
            if not math.isfinite(ra) or not math.isfinite(dec):
                continue
        except ValueError:
            continue
        if previous is not None and event.seconds - previous > 30:
            result.append((previous + 0.0001, math.nan))
            window.clear()
        window.append((ra, dec))
        n = len(window)
        if n < 2:
            rms = 0.0
        else:
            mean_ra = math.fsum(x for x, _ in window) / n
            mean_dec = math.fsum(y for _, y in window) / n
            rms = math.sqrt(
                math.fsum((x - mean_ra) ** 2 + (y - mean_dec) ** 2 for x, y in window)
                / (n - 1)
            )
        result.append((event.seconds, rms))
        previous = event.seconds
    return result


def build_figure(sessions: Sequence[Session], relative: bool = False) -> Figure:
    """Construit la chronologie et les courbes Plotly des sessions indépendantes."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    fig = make_subplots(
        rows=6,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.26, 0.18, 0.14, 0.14, 0.14, 0.14],
        vertical_spacing=0.045,
        subplot_titles=[
            "Chronologie",
            "Guidage et qualité des étoiles",
            "RMS du guidage — secondes d’arc (40 échantillons)",
            "Signal et étoiles",
            "Température",
            "Position de la monture",
        ],
    )
    ranges, trace_sessions = [], []
    for sid, session in enumerate(sessions):

        def x(seconds: float) -> float | str:
            """Convertit le temps relatif en date locale ISO lorsque ce mode est demandé."""
            return (
                seconds
                if relative
                else (session.start + timedelta(seconds=seconds)).isoformat()
            )

        begin = len(fig.data)
        grouped = defaultdict(list)
        for row, t0, t1, state, detail, color in colored_intervals(intervals(session)):
            grouped[row, state, color].append((t0, t1, detail))
        for (row, state, color), spans in grouped.items():
            durations = [(b - a) * (1 if relative else 1000) for a, b, _ in spans]
            fig.add_trace(
                go.Bar(
                    y=[row] * len(spans),
                    x=durations,
                    base=[x(a) for a, _, _ in spans],
                    orientation="h",
                    width=0.65,
                    marker_color=color,
                    showlegend=False,
                    name=state,
                    customdata=[
                        [
                            escape(session.path.name),
                            escape(state),
                            escape(d),
                            round(b - a, 3),
                        ]
                        for a, b, d in spans
                    ],
                    hovertemplate="%{customdata[0]}<br>%{y} : %{customdata[1]}<br>"
                    "%{customdata[2]}<br>Durée : %{customdata[3]} s<extra></extra>",
                ),
                row=1,
                col=1,
            )
        for (name, unit), values in measurements(session).items():
            row = (
                2 if name in ("Erreur AD", "Erreur DEC", "HFR", "Distance cible") else 4
            )
            if name == "RMS":
                row = 3
            if unit == "°C":
                row = 5
            if unit == "°":
                row = 6
            default = name in (
                "Erreur AD",
                "Erreur DEC",
                "HFR",
                "RMS",
                "Température",
                "Altitude",
            )
            fig.add_trace(
                go.Scattergl(
                    x=[x(t) for t, _ in values],
                    y=[v for _, v in values],
                    mode="markers"
                    if name in ("HFR", "Excentricité", "Étoiles image", "Médiane image")
                    else "lines",
                    name=f"{sid + 1} · {name} ({unit})",
                    visible=True if default else "legendonly",
                    connectgaps=False,
                    line={"width": 1.3},
                    hovertemplate=f"{escape(name)} : %{{y:.3f}} {unit}<br>%{{x}}<extra>{escape(session.path.name)}</extra>",
                ),
                row=row,
                col=1,
            )
        trace_sessions.extend([sid] * (len(fig.data) - begin))
        ranges.append([x(0), x(max(session.duration, 1))])
    all_range = [min(r[0] for r in ranges), max(r[1] for r in ranges)]
    initial = [t.visible if t.visible is not None else True for t in fig.data]
    buttons = [
        dict(
            label="Toutes les sessions",
            method="update",
            args=[
                {"visible": initial},
                {f"xaxis{i if i > 1 else ''}.range": all_range for i in range(1, 7)},
            ],
        )
    ]
    for sid, session in enumerate(sessions):
        buttons.append(
            dict(
                label=f"{sid + 1} · {session.path.name}",
                method="update",
                args=[
                    {
                        "visible": [
                            v if owner == sid else False
                            for v, owner in zip(initial, trace_sessions)
                        ]
                    },
                    {
                        f"xaxis{i if i > 1 else ''}.range": ranges[sid]
                        for i in range(1, 7)
                    },
                ],
            )
        )
    fig.update_layout(
        template="plotly_dark",
        height=1300,
        barmode="overlay",
        title="KStars / Ekos — Analyse des sessions",
        margin=dict(l=115, r=30, t=125, b=60),
        legend=dict(groupclick="toggleitem"),
        updatemenus=[dict(buttons=buttons, x=0, y=1.075, xanchor="left")],
    )
    fig.update_yaxes(
        categoryorder="array", categoryarray=ROWS, autorange="reversed", row=1, col=1
    )
    fig.update_yaxes(title_text="arcsec / HFR (px)", row=2, col=1)
    fig.update_yaxes(title_text="RMS (″)", tickformat=".3~f", row=3, col=1)
    fig.update_yaxes(title_text="Unités de la légende", row=4, col=1)
    fig.update_yaxes(title_text="°C", row=5, col=1)
    fig.update_yaxes(title_text="°", row=6, col=1)
    fig.update_xaxes(
        type="linear" if relative else "date", range=all_range, showgrid=True
    )
    fig.update_xaxes(
        title_text="Temps depuis le début (s)"
        if relative
        else "Heure locale inscrite dans les journaux",
        row=6,
        col=1,
    )
    return fig


def write_report(
    sessions: Sequence[Session], output: Path | str, relative: bool = False
) -> None:
    """Écrit un HTML autonome avec graphiques, récapitulatif et sélection de sessions."""
    fig = build_figure(sessions, relative)
    rows = []
    for i, s in enumerate(sessions, 1):
        counts = Counter(e.kind for e in s.events)
        squared_errors = []
        for event in s.events:
            if event.kind == "GuideStats" and len(event.fields) >= 2:
                try:
                    ra, dec = map(float, event.fields[:2])
                    if math.isfinite(ra) and math.isfinite(dec):
                        squared_errors.append(ra * ra + dec * dec)
                except ValueError:
                    pass
        rms = (
            math.sqrt(sum(squared_errors) / len(squared_errors))
            if squared_errors
            else None
        )
        rows.append(
            f"<tr><td>{i}</td><td>{escape(s.path.name)}</td><td>{s.start} {escape(s.timezone)}</td>"
            f"<td>{s.duration / 3600:.2f} h</td><td>{counts['CaptureComplete']}</td>"
            f"<td>{counts['CaptureAborted']}</td><td>{f'{rms:.3f}' if rms is not None else '—'}</td></tr>"
        )
    warnings = "".join(
        f"<li>{escape(s.path.name)} : {escape(w)}</li>"
        for s in sessions
        for w in sorted(set(s.warnings))
    )
    html = """<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Analyse KStars / Ekos</title><style>body{background:#111827;color:#e5e7eb;font:15px system-ui;margin:24px}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:8px;border-bottom:1px solid #374151}summary{cursor:pointer}a{color:#93c5fd}</style>
<h1>Analyse KStars / Ekos</h1><p>Sélectionnez une session dans le menu. Glissez pour zoomer ; double-cliquez pour réinitialiser.
Cliquez sur les légendes pour afficher ou masquer une mesure. La barre d’outils permet l’export PNG.</p>
<p>Les panneaux partagent le même axe temporel. Les unités sont indiquées dans la légende ; masquez les séries d’échelles différentes pour les examiner.
Les intervalles sans événement de fin s’arrêtent à la dernière donnée du journal.</p>"""
    filenames = [s.path.name for s in sessions]
    html += (
        '<p><label for="session-filenames">Fichier(s) affiché(s) — cliquez pour '
        "sélectionner, puis Ctrl+C pour copier :</label></p>"
        '<textarea id="session-filenames" readonly spellcheck="false" '
        'style="box-sizing:border-box;width:100%;padding:10px;background:#1f2937;'
        'color:#e5e7eb;border:1px solid #6b7280;border-radius:4px;font:inherit" '
        f'rows="{min(len(filenames), 4)}">{escape(chr(10).join(filenames))}</textarea>'
    )
    # Escape HTML delimiters so filenames cannot terminate the embedded script.
    filenames_json = json.dumps(filenames, ensure_ascii=True).replace("<", r"\u003c")
    post_script = """
    const filenames = __FILENAMES__;
    const field = document.getElementById('session-filenames');
    field.addEventListener('click', () => field.select());
    document.getElementById('{plot_id}').on('plotly_buttonclicked', (event) => {
        const selected = event.active === 0 ? filenames : [filenames[event.active - 1]];
        field.value = selected.join('\\n');
        field.rows = Math.min(selected.length, 4);
    });
    """.replace("__FILENAMES__", filenames_json)
    html += fig.to_html(
        full_html=False,
        include_plotlyjs=True,
        post_script=post_script,
        config={"scrollZoom": True, "displaylogo": False},
    )
    html += (
        "<p>Monture : "
        + " · ".join(
            f'<span style="color:{color}">■ {label}</span>'
            for label, color in MOUNT_STYLES.values()
        )
        + "</p>"
    )
    html += (
        "<p>Acquisitions : "
        + " · ".join(
            f'<span style="color:{pair[0]}">■</span><span style="color:{pair[1]}">■</span> {kind}'
            for kind, pair in CAPTURE_PALETTES.items()
        )
        + ' · <span style="color:#f87171">■</span> Abandon / interruption</p>'
    )
    html += (
        "<h2>Sessions</h2><table><tr><th>#</th><th>Fichier</th><th>Début</th><th>Durée</th><th>Images terminées</th><th>Abandons</th><th>RMS guidage (″)</th></tr>"
        + "".join(rows)
        + "</table>"
    )
    html += "<p>RMS global = √moyenne(AD² + DEC²), calculé sur les échantillons disponibles, y compris les phases perturbées. Les HFR ou excentricités à −1 sont indisponibles et masqués. Les heures locales ne sont pas converties entre fuseaux.</p>"
    html += (
        "<details><summary>Avertissements de lecture</summary><ul>"
        + warnings
        + "</ul></details>"
    )
    html += '<p>Référence : <a href="https://kstars-docs.kde.org/en/user_manual/ekos-analyze.html">Module Analyze de KStars</a>.</p></html>'
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Résout les fichiers CLI et lance l’interface native ou le rapport HTML."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "inputs", nargs="*", help="Fichiers .analyze, dossiers ou motifs glob"
    )
    parser.add_argument(
        "-o",
        "--output",
        default="out/kstars-analyze.html",
        help="Rapport HTML de sortie",
    )
    parser.add_argument(
        "--relative",
        action="store_true",
        help="Comparer les sessions depuis leur début (secondes)",
    )
    parser.add_argument(
        "--with-captures",
        action="store_true",
        help="Conserver uniquement les sessions avec acquisition démarrée, terminée ou abandonnée",
    )
    parser.add_argument(
        "--open", action="store_true", help="Ouvrir le rapport dans le navigateur"
    )
    parser.add_argument(
        "--gui", action="store_true", help="Ouvrir la fenêtre Python native"
    )
    parser.add_argument(
        "--html",
        action="store_false",
        dest="gui",
        help="Générer uniquement le rapport HTML",
    )
    parser.set_defaults(gui=False)
    args = parser.parse_args(argv)
    paths = set()
    for arg in args.inputs:
        matches = glob(str(Path(arg).expanduser()))
        if not matches:
            parser.error(f"Aucun fichier trouvé : {arg}")
        for match in matches:
            path = Path(match)
            paths.update(
                p.resolve()
                for p in (path.glob("*.analyze") if path.is_dir() else [path])
                if p.is_file()
            )
    if not paths and (args.inputs or not args.gui):
        parser.error("Aucun fichier .analyze dans les dossiers indiqués")
    output = Path(args.output).expanduser().resolve()
    if output in paths:
        parser.error("La sortie ne peut pas écraser un journal source")
    try:
        sessions = sorted(
            (read_session(p) for p in sorted(paths)), key=lambda s: s.start
        )
        if args.gui:
            try:
                from kstarsAnalyzeGui import launch

                launch(sessions, args.relative, args.with_captures)
            except ImportError as exc:
                parser.exit(
                    1,
                    f"Dépendance graphique manquante : {exc}. Installez requirements-analyze.txt et python3-tk.\n",
                )
            except Exception as exc:
                import tkinter

                if isinstance(exc, tkinter.TclError):
                    parser.exit(
                        1,
                        f"Impossible d’ouvrir la fenêtre : {exc}. Un affichage graphique est nécessaire ; utilisez --html pour un rapport.\n",
                    )
                raise
            return 0
        if args.with_captures:
            total = len(sessions)
            capture_events = {"CaptureStarting", "CaptureComplete", "CaptureAborted"}
            sessions = [
                s for s in sessions if any(e.kind in capture_events for e in s.events)
            ]
            if not sessions:
                parser.error(
                    "Aucun fichier ne contient d’acquisition ; aucun rapport généré"
                )
            print(
                f"Filtre acquisitions : {len(sessions)}/{total} fichier(s) conservé(s)"
            )
        write_report(sessions, output, args.relative)
    except ImportError:
        parser.exit(1, "Plotly manque : installez requirements-analyze.txt avec pip.\n")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Erreur : {exc}\n")
    for s in sessions:
        for warning in sorted(set(s.warnings)):
            print(f"{s.path.name} : {warning}", file=sys.stderr)
    print(f"{len(sessions)} session(s) — rapport : {output}")
    if args.open:
        webbrowser.open(output.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
