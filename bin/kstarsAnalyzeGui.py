"""Interface native Tkinter/Matplotlib du visualiseur KStars."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
from kstarsAnalyze import (
    CAPTURE_PALETTES,
    METRICS,
    ROWS,
    Session,
    capture_palette_key,
    colored_intervals,
    intervals,
    measurements,
    mount_style,
    read_session,
    write_report,
)
from matplotlib.axes import Axes
from matplotlib.backend_bases import PickEvent, ResizeEvent
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.dates import AutoDateLocator, ConciseDateFormatter, date2num, num2date
from matplotlib.figure import Figure
from matplotlib.patches import Patch
from matplotlib.widgets import RangeSlider


class AnalyzeWindow:
    """Fenêtre Tkinter de sélection, inspection et export des journaux de session."""

    def __init__(
        self,
        root: tk.Tk,
        sessions: Sequence[Session] = (),
        relative: bool = False,
        with_captures: bool = False,
    ) -> None:
        """Construit les widgets et charge la sélection initiale sans démarrer la boucle Tk."""
        self.root = root
        self.sessions = list(sessions)
        self.cache = {}
        self.visible_sessions = []
        self.details_by_artist = {}
        self.mount_legend_items = {}
        self.time_slider = None
        self.time_key = None
        self.time_window = None
        self.syncing_time = False
        root.title("KStars / Ekos — Analyse des sessions")
        root.geometry("1450x950")
        self.relative = tk.BooleanVar(value=relative)
        self.captures = tk.BooleanVar(value=with_captures)
        top = ttk.Frame(root, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="Ajouter des fichiers…", command=self.open_files).pack(
            side="left"
        )
        ttk.Button(top, text="Ajouter un dossier…", command=self.open_directory).pack(
            side="left", padx=6
        )
        ttk.Checkbutton(
            top,
            text="Avec acquisitions uniquement",
            variable=self.captures,
            command=self.refresh_list,
        ).pack(side="left", padx=8)
        ttk.Checkbutton(
            top, text="Temps relatif", variable=self.relative, command=self.draw
        ).pack(side="left")
        ttk.Button(top, text="Exporter HTML…", command=self.export_html).pack(
            side="right"
        )
        pane = ttk.Panedwindow(root, orient="horizontal")
        pane.pack(fill="both", expand=True)
        left = ttk.Frame(pane, padding=8)
        right = ttk.Frame(pane, padding=8)
        pane.add(left, weight=0)
        pane.add(right, weight=1)
        ttk.Label(left, text="Sessions (Ctrl/clic pour comparer)").pack(anchor="w")
        list_frame = ttk.Frame(left)
        list_frame.pack(fill="both", expand=True)
        self.session_list = tk.Listbox(
            list_frame, selectmode="extended", exportselection=False, width=37
        )
        scroll = ttk.Scrollbar(
            list_frame, orient="vertical", command=self.session_list.yview
        )
        self.session_list.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.session_list.pack(side="left", fill="both", expand=True)
        self.session_list.bind("<<ListboxSelect>>", lambda _: self.draw())
        ttk.Button(left, text="Toutes les sessions", command=self.select_all).pack(
            fill="x", pady=4
        )
        ttk.Label(left, text="Fichier(s) affiché(s) — texte copiable").pack(
            anchor="w", pady=(6, 2)
        )
        self.names = tk.Text(left, height=2, width=37, wrap="char")
        self.names.pack(fill="x")
        # Read-only without disabling text selection or Ctrl+C.
        self.names.bind(
            "<Key>",
            lambda e: (
                None if (e.state & 4 and e.keysym.lower() in ("c", "a")) else "break"
            ),
        )
        ttk.Button(left, text="Copier les noms", command=self.copy_names).pack(
            fill="x", pady=4
        )
        ttk.Label(left, text="Mesures à afficher").pack(anchor="w", pady=4)
        self.metrics = {}
        defaults = {"Erreur AD", "Erreur DEC", "HFR", "RMS", "Température", "Altitude"}
        for specs in [*METRICS.values(), [(0, "RMS", "arcsec")]]:
            for _, name, unit in specs:
                var = tk.BooleanVar(value=name in defaults)
                self.metrics[name] = var
                ttk.Checkbutton(
                    left, text=f"{name} ({unit})", variable=var, command=self.draw
                ).pack(anchor="w")
        time_controls = ttk.Frame(right)
        time_controls.pack(fill="x", pady=(4, 0))
        self.time_label = tk.StringVar(value="Aucune période sélectionnée")
        ttk.Label(time_controls, textvariable=self.time_label).pack(side="left")
        ttk.Button(
            time_controls, text="Toute la période", command=self.reset_time
        ).pack(side="right")
        self.slider_figure = Figure(figsize=(10, 0.55), dpi=100, facecolor="#111827")
        self.slider_canvas = FigureCanvasTkAgg(self.slider_figure, master=right)
        self.slider_canvas.get_tk_widget().configure(height=55)
        self.slider_canvas.get_tk_widget().pack(fill="x")
        self.figure = Figure(figsize=(10, 8), facecolor="#111827")
        self.canvas = FigureCanvasTkAgg(self.figure, master=right)
        toolbar = NavigationToolbar2Tk(self.canvas, right, pack_toolbar=False)
        toolbar.pack(fill="x")
        self.toolbar = toolbar
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas.mpl_connect("pick_event", self.show_details)
        self.canvas.mpl_connect("resize_event", self.layout_mount_legend)
        self.details = tk.Text(right, height=3, wrap="word")
        self.details.pack(fill="x")
        self.status = tk.StringVar()
        ttk.Label(root, textvariable=self.status, padding=5).pack(fill="x")
        self.refresh_list()

    def layout_mount_legend(self, event: ResizeEvent | None = None) -> None:
        """Reserve a full-width strip outside the timeline, also on resize."""
        for legend in list(self.figure.legends):
            legend.remove()
        top = 0.96
        if self.mount_legend_items:
            columns = min(
                len(self.mount_legend_items), max(1, int(self.figure.bbox.width / 145))
            )
            legend = self.figure.legend(
                handles=[
                    Patch(color=color, label=label)
                    for label, color in self.mount_legend_items.items()
                ],
                title="Chronologie — états et acquisitions",
                fontsize=8,
                title_fontsize=8,
                loc="upper left",
                bbox_to_anchor=(0.03, 0.99, 0.94, 0),
                mode="expand",
                ncol=columns,
                borderaxespad=0,
                frameon=False,
                labelcolor="#e5e7eb",
            )
            legend.get_title().set_color("#e5e7eb")
            height = legend.get_window_extent(self.canvas.get_renderer()).height
            # Include space for the timeline title and a clear gap below the legend.
            top = max(0.4, 0.99 - (height + 35) / max(self.figure.bbox.height, 1))
        self.figure.subplots_adjust(top=top)
        self.canvas.draw_idle()

    def update_time_label(self, low: float, high: float) -> None:
        """Affiche les bornes en secondes relatives ou dates locales et leur durée."""
        if self.relative.get():
            begin, end = f"{low:.1f} s", f"{high:.1f} s"
            duration = high - low
        else:
            begin = num2date(low).strftime("%d/%m/%Y %H:%M:%S")
            end = num2date(high).strftime("%d/%m/%Y %H:%M:%S")
            duration = (high - low) * 86400
        self.time_label.set(
            f"Début : {begin}    Fin : {end}    Durée : {duration:.1f} s"
        )

    def autoscale_visible_y(self, low: float, high: float) -> None:
        """Fit each numeric axis to visible curves, including clipped segments."""
        for ax in self.figure.axes:
            visible_values = []
            for line in ax.lines:
                if not line.get_visible():
                    continue
                x, y = line.get_data(orig=False)
                x, y = np.asarray(x), np.asarray(y)
                finite = np.isfinite(x) & np.isfinite(y)
                visible_values.extend(y[finite & (x >= low) & (x <= high)])
                # A line can cross the viewport with neither endpoint inside.
                # Interpolate only finite adjacent samples; never bridge NaN gaps.
                if line.get_linestyle() not in ("None", "", " "):
                    for edge in (low, high):
                        crossing = (
                            finite[:-1] & finite[1:] & (x[:-1] < edge) & (x[1:] > edge)
                        )
                        indices = np.flatnonzero(crossing)
                        for i in indices:
                            weight = (edge - x[i]) / (x[i + 1] - x[i])
                            visible_values.append(
                                (1 - weight) * y[i] + weight * y[i + 1]
                            )
            if visible_values:
                bottom, top = min(visible_values), max(visible_values)
                margin = (
                    (top - bottom) * 0.05
                    if top > bottom
                    else max(abs(top) * 0.05, 0.05)
                )
                ax.set_ylim(bottom - margin, top + margin)
            # With no visible data, retain the previous scale.

    def configure_time_slider(
        self, sessions: Sequence[Session], bounds: Sequence[float]
    ) -> None:
        """Crée le curseur temporel et reconnecte les axes sur la période disponible."""
        if self.time_slider is not None:
            self.time_slider.disconnect_events()
        self.slider_figure.clear()
        self.time_slider = None
        key = (
            self.relative.get(),
            tuple((s.path, s.start, s.duration) for s in sessions),
        )
        if not bounds:
            self.time_key = key
            self.time_window = None
            self.time_label.set("Aucune période sélectionnée")
            self.slider_canvas.draw_idle()
            return
        low, high = min(bounds), max(bounds)
        if key != self.time_key or self.time_window is None:
            self.time_window = (low, high)
        self.time_key = key
        slider_ax = self.slider_figure.add_axes([0.035, 0.30, 0.93, 0.45])
        self.time_slider = RangeSlider(
            slider_ax, "", low, high, valinit=self.time_window, color="#60a5fa"
        )
        self.time_slider.valtext.set_visible(False)
        self.time_slider.on_changed(self.change_time)
        self.figure.axes[-1].set_xlim(*self.time_window)
        # Keep the handles in sync with zoom/pan/home from the Matplotlib toolbar.
        for ax in self.figure.axes:
            ax.callbacks.connect("xlim_changed", self.sync_time_from_axes)
        self.autoscale_visible_y(*self.time_window)
        self.update_time_label(*self.time_window)
        self.slider_canvas.draw_idle()

    def change_time(self, values: Sequence[float]) -> None:
        """Applique les bornes du curseur sans créer de fenêtre de durée nulle."""
        if self.syncing_time:
            return
        low, high = map(float, values)
        if high <= low:
            # Two coincident handles must not create a singular time axis.
            self.syncing_time = True
            try:
                self.time_slider.set_val(self.time_window)
            finally:
                self.syncing_time = False
            return
        self.time_window = (low, high)
        self.syncing_time = True
        try:
            self.figure.axes[-1].set_xlim(low, high)
        finally:
            self.syncing_time = False
        self.autoscale_visible_y(low, high)
        self.update_time_label(low, high)
        self.canvas.draw_idle()

    def sync_time_from_axes(self, ax: Axes) -> None:
        """Synchronise le curseur avec le zoom ou déplacement de la barre Matplotlib."""
        if self.syncing_time or self.time_slider is None:
            return
        low, high = sorted(ax.get_xlim())
        low = max(low, self.time_slider.valmin)
        high = min(high, self.time_slider.valmax)
        if low >= high:
            low, high = self.time_slider.valmin, self.time_slider.valmax
        self.syncing_time = True
        try:
            self.time_slider.set_val((low, high))
            # Clamp toolbar panning to the available time interval as well.
            ax.set_xlim(low, high)
        finally:
            self.syncing_time = False
        self.time_window = (low, high)
        self.autoscale_visible_y(low, high)
        self.update_time_label(low, high)
        self.canvas.draw_idle()

    def reset_time(self) -> None:
        """Réinitialise les axes partagés aux bornes complètes du curseur."""
        if self.time_slider is not None:
            self.time_slider.set_val((self.time_slider.valmin, self.time_slider.valmax))

    def selected(self) -> list[Session]:
        """Retourne les sections sélectionnées dans l’ordre de la liste."""
        return [self.visible_sessions[i] for i in self.session_list.curselection()]

    def refresh_list(self) -> None:
        """Reconstruit la liste selon le filtre des acquisitions puis actualise les graphes."""
        previous = {s.path for s in self.selected()}
        capture_events = {"CaptureStarting", "CaptureComplete", "CaptureAborted"}
        self.visible_sessions = [
            s
            for s in self.sessions
            if not self.captures.get()
            or any(e.kind in capture_events for e in s.events)
        ]
        self.session_list.delete(0, "end")
        for i, s in enumerate(self.visible_sessions):
            self.session_list.insert("end", s.path.name)
            if s.path in previous:
                self.session_list.selection_set(i)
        if not self.session_list.curselection() and self.visible_sessions:
            self.session_list.selection_set(0)
        self.draw()

    def select_all(self) -> None:
        """Sélectionne toutes les entrées disponibles puis redessine les panneaux."""
        self.session_list.selection_set(0, "end")
        self.draw()

    def copy_names(self) -> None:
        """Copie dans le presse-papiers les chemins distincts des sources sélectionnées."""
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(s.path.name for s in self.selected()))

    def open_files(self) -> None:
        """Ouvre le sélecteur de fichiers et charge les chemins choisis."""
        paths = filedialog.askopenfilenames(
            filetypes=[("Analyses KStars", "*.analyze")]
        )
        self.load(paths)

    def open_directory(self) -> None:
        """Ouvre le sélecteur de dossier et charge ses journaux sans récursion."""
        directory = filedialog.askdirectory()
        if directory:
            self.load(sorted(Path(directory).glob("*.analyze")))

    def load(self, paths: Sequence[Path | str]) -> None:
        """Ajoute les sessions lisibles et signale les fichiers ignorés dans une boîte de dialogue."""
        loaded = {s.path: s for s in self.sessions}
        errors = []
        for path in paths:
            try:
                s = read_session(Path(path).resolve())
                loaded[s.path] = s
                self.cache.pop(s.path, None)
            except (OSError, ValueError) as exc:
                errors.append(str(exc))
        self.sessions = sorted(loaded.values(), key=lambda s: s.start)
        self.refresh_list()
        if errors:
            messagebox.showerror("Fichiers illisibles", "\n".join(errors))

    def show_details(self, event: PickEvent) -> None:
        """Affiche les détails de l’intervalle sélectionné dans la chronologie."""
        if event.artist in self.details_by_artist:
            self.details.delete("1.0", "end")
            self.details.insert("1.0", self.details_by_artist[event.artist])

    def export_html(self) -> None:
        """Écrit les sessions sélectionnées complètes après choix de la destination HTML."""
        sessions = self.selected()
        if not sessions:
            messagebox.showinfo("Export", "Sélectionnez au moins une session.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".html", filetypes=[("Rapport HTML", "*.html")]
        )
        if path:
            if Path(path).resolve() in {s.path.resolve() for s in self.sessions}:
                messagebox.showerror(
                    "Export", "La sortie ne peut pas écraser un journal source."
                )
                return
            try:
                write_report(sessions, path, self.relative.get())
            except (OSError, ValueError, ImportError) as exc:
                messagebox.showerror("Export", str(exc))

    def draw(self) -> None:
        """Reconstruit les panneaux selon la sélection en conservant le zoom compatible."""
        sessions = self.selected()
        self.names.delete("1.0", "end")
        self.names.insert("1.0", "\n".join(s.path.name for s in sessions))
        self.details.delete("1.0", "end")
        self.details.insert(
            "1.0",
            "Cliquez sur un intervalle pour afficher ses détails. La barre d’outils permet le zoom, le déplacement et l’export PNG.",
        )
        self.syncing_time = True
        self.figure.clear()
        self.details_by_artist.clear()
        axes = self.figure.subplots(
            6, 1, sharex=True, gridspec_kw={"height_ratios": [2, 1.4, 1.2, 1.2, 1, 1]}
        )
        mount_legend = {}
        for ax, title in zip(
            axes,
            [
                "Chronologie",
                "Guidage / HFR (arcsec / px)",
                "RMS du guidage (″) — 40 échantillons",
                "Signal (unités dans la légende)",
                "Température (°C)",
                "Monture (°)",
            ],
        ):
            ax.set_facecolor("#111827")
            ax.tick_params(colors="#e5e7eb", labelsize=8)
            ax.set_title(title, color="#e5e7eb", fontsize=10, loc="left")
            ax.grid(alpha=0.2)
            for spine in ax.spines.values():
                spine.set_color("#6b7280")
        bounds = []
        for sid, s in enumerate(sessions):
            relative = self.relative.get()

            def x(t: float) -> float:
                """Convertit les secondes en coordonnées Matplotlib selon le mode temporel."""
                return t if relative else date2num(s.start + timedelta(seconds=t))

            bounds.extend([x(0), x(max(s.duration, 1))])
            if s.path not in self.cache:
                self.cache[s.path] = intervals(s), measurements(s)
            spans, series = self.cache[s.path]
            for row, t0, t1, state, detail, bar_color in colored_intervals(spans):
                y = ROWS.index(row)
                if row == "Monture":
                    label, color = mount_style(state)
                    mount_legend[label] = color
                if row == "Acquisition":
                    if bar_color == "#f87171":
                        mount_legend["Acq. interrompue"] = bar_color
                    else:
                        kind = capture_palette_key(state)
                        for i, color in enumerate(CAPTURE_PALETTES[kind], 1):
                            mount_legend[f"{kind} {i}"] = color
                artist = axes[0].broken_barh(
                    [(x(t0), x(t1) - x(t0))],
                    (y - 0.35, 0.7),
                    facecolors=bar_color,
                    picker=True,
                )
                self.details_by_artist[artist] = (
                    f"{s.path.name}\n{row} : {state} — {t1 - t0:.3f} s\n{detail}"
                )
            for (name, unit), values in series.items():
                if not self.metrics[name].get() or not any(
                    np.isfinite(v) for _, v in values
                ):
                    continue
                row = (
                    1
                    if name in ("Erreur AD", "Erreur DEC", "HFR", "Distance cible")
                    else 3
                )
                if name == "RMS":
                    row = 2
                if unit == "°C":
                    row = 4
                if unit == "°":
                    row = 5
                marker = (
                    "."
                    if name in ("HFR", "Excentricité", "Étoiles image", "Médiane image")
                    else "-"
                )
                axes[row].plot(
                    [x(t) for t, _ in values],
                    [v for _, v in values],
                    marker,
                    linewidth=0.8,
                    markersize=3,
                    label=f"{sid + 1} · {name} ({unit})",
                )
        self.mount_legend_items = mount_legend
        axes[2].ticklabel_format(axis="y", style="plain", useOffset=False)
        axes[0].set_yticks(range(len(ROWS)), ROWS)
        axes[0].set_ylim(len(ROWS) - 0.5, -0.5)
        for ax in axes[1:]:
            if ax.lines:
                ax.legend(fontsize=7, loc="upper right", ncol=2)
        # Remove empty panels entirely so GridSpec gives their height back to
        # the selected measurements. Timeline is present only with intervals.
        weights = [2, 1.4, 1.2, 1.2, 1, 1]
        populated = [
            (ax, weights[i])
            for i, ax in enumerate(axes)
            if (ax.collections if i == 0 else ax.lines)
        ]
        for ax in axes:
            if not any(ax is item[0] for item in populated):
                self.figure.delaxes(ax)
        if populated:
            grid = self.figure.add_gridspec(
                len(populated), 1, height_ratios=[weight for _, weight in populated]
            )
            axes = [ax for ax, _ in populated]
            for i, ax in enumerate(axes):
                ax.set_subplotspec(grid[i])
                ax.tick_params(axis="x", labelbottom=(i == len(axes) - 1))
        else:
            # Keep a time axis usable by the slider, but no empty measurement panels.
            placeholder = self.figure.add_subplot(111, facecolor="#111827")
            placeholder.text(
                0.5,
                0.5,
                "Aucune mesure activée disponible",
                transform=placeholder.transAxes,
                ha="center",
                color="#e5e7eb",
            )
            placeholder.set_yticks([])
            placeholder.tick_params(colors="#e5e7eb")
            axes = [placeholder]
        if bounds:
            axes[-1].set_xlim(min(bounds), max(bounds))
        if not self.relative.get():
            locator = AutoDateLocator()
            axes[-1].xaxis.set_major_locator(locator)
            axes[-1].xaxis.set_major_formatter(ConciseDateFormatter(locator))
        axes[-1].set_xlabel(
            "Temps écoulé (s)" if self.relative.get() else "Heure locale du journal",
            color="#e5e7eb",
        )
        self.figure.subplots_adjust(
            left=0.13, right=0.98, top=0.96, bottom=0.07, hspace=0.5
        )
        self.layout_mount_legend()
        self.syncing_time = False
        self.configure_time_slider(sessions, bounds)
        self.toolbar.update()
        self.canvas.draw_idle()
        captures = sum(e.kind == "CaptureComplete" for s in sessions for e in s.events)
        warning_count = sum(len(set(s.warnings)) for s in sessions)
        self.status.set(
            f"{len(self.visible_sessions)}/{len(self.sessions)} fichiers · {len(sessions)} sélectionné(s) · {captures} images terminées · {warning_count} avertissement(s)"
        )


def launch(
    sessions: Sequence[Session] = (),
    relative: bool = False,
    with_captures: bool = False,
) -> None:
    """Crée la racine Tk et exécute la boucle d’événements du visualiseur."""
    root = tk.Tk()
    AnalyzeWindow(root, sessions, relative, with_captures)
    root.mainloop()
