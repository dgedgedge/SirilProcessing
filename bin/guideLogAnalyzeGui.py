"""Interface native du visualiseur de guide logs."""

from __future__ import annotations

import math
import tkinter as tk
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
from guideLogAnalyze import (
    TRACKING_STATES,
    Section,
    broken_line,
    calibration_details,
    calibrations_for,
    discover,
    guide_data,
    read_log,
    rolling_rms,
    summary,
    tracking_status,
    write_report,
)
from matplotlib.axes import Axes
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.dates import AutoDateLocator, ConciseDateFormatter, date2num
from matplotlib.figure import Figure
from matplotlib.widgets import RangeSlider


class GuideWindow:
    """Fenêtre Tkinter de sélection, inspection et export des journaux de session."""

    def __init__(
        self,
        root: tk.Tk,
        sections: Sequence[Section] = (),
        relative: bool = False,
        unit: str = "arcsec",
    ) -> None:
        """Construit les widgets et charge la sélection initiale sans démarrer la boucle Tk."""
        self.root, self.sections = root, list(sections)
        self.slider = None
        self.syncing = False
        self.window = None
        self.view_key = None
        self.series = []
        self.time_axes = []
        root.title("KStars / Ekos — Analyse des guide logs")
        root.geometry("1500x1000")
        self.relative = tk.BooleanVar(value=relative)
        self.unit = tk.StringVar(value=unit)
        top = ttk.Frame(root, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="Ajouter des fichiers…", command=self.open_files).pack(
            side="left"
        )
        ttk.Button(top, text="Ajouter un dossier…", command=self.open_directory).pack(
            side="left", padx=8
        )
        ttk.Checkbutton(
            top, text="Temps relatif", variable=self.relative, command=self.draw
        ).pack(side="left")
        units = ttk.Combobox(
            top,
            textvariable=self.unit,
            values=("arcsec", "px"),
            state="readonly",
            width=8,
        )
        units.pack(side="left", padx=8)
        units.bind("<<ComboboxSelected>>", lambda _: self.draw())
        ttk.Button(top, text="Exporter HTML…", command=self.export).pack(side="right")
        pane = ttk.Panedwindow(root, orient="horizontal")
        pane.pack(fill="both", expand=True)
        left, right = ttk.Frame(pane, padding=8), ttk.Frame(pane, padding=8)
        pane.add(left, weight=0)
        pane.add(right, weight=1)
        ttk.Label(
            left, text="Séquences et calibrations\nCtrl/clic : sélection multiple"
        ).pack(anchor="w")
        self.listbox = tk.Listbox(
            left, width=48, selectmode="extended", exportselection=False
        )
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", lambda _: self.draw())
        ttk.Button(left, text="Tout sélectionner", command=self.select_all).pack(
            fill="x", pady=5
        )
        ttk.Button(left, text="Copier les noms", command=self.copy_names).pack(fill="x")
        self.metrics = {}
        for name in ("Erreurs", "Impulsions", "RMS", "SNR", "État du suivi"):
            value = tk.BooleanVar(value=True)
            self.metrics[name] = value
            ttk.Checkbutton(left, text=name, variable=value, command=self.draw).pack(
                anchor="w"
            )
        ttk.Label(
            left,
            text="Rouge : mesures rejetées\nE et N : impulsions positives\nW et S : impulsions négatives\n\nRMS autour de zéro.\nDithering inclus ; rejets exclus.",
            justify="left",
        ).pack(anchor="w", pady=10)
        controls = ttk.Frame(right)
        controls.pack(fill="x")
        self.period = tk.StringVar(value="Aucune mesure")
        ttk.Label(controls, textvariable=self.period).pack(side="left")
        ttk.Button(controls, text="Toute la période", command=self.reset).pack(
            side="right"
        )
        self.slider_figure = Figure(figsize=(10, 0.5))
        self.slider_canvas = FigureCanvasTkAgg(self.slider_figure, right)
        self.slider_canvas.get_tk_widget().configure(height=55)
        self.slider_canvas.get_tk_widget().pack(fill="x")
        self.figure = Figure(figsize=(11, 8), constrained_layout=True)
        self.canvas = FigureCanvasTkAgg(self.figure, right)
        self.toolbar = NavigationToolbar2Tk(self.canvas, right, pack_toolbar=False)
        self.toolbar.pack(fill="x")
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.details = tk.Text(right, height=8, wrap="word")
        self.details.pack(fill="x")
        self.status = tk.StringVar()
        ttk.Label(root, textvariable=self.status, padding=5).pack(fill="x")
        self.refresh()

    def selected(self) -> list[Section]:
        """Retourne les sections sélectionnées dans l’ordre de la liste."""
        return [self.sections[i] for i in self.listbox.curselection()]

    def refresh(self) -> None:
        """Reconstruit la liste des sections et rétablit la sélection complète."""
        self.listbox.delete(0, "end")
        for section in self.sections:
            kind = "Guidage" if section.kind == "guide" else "Calibration"
            self.listbox.insert(
                "end",
                f"{section.start:%d/%m %H:%M:%S} · {kind} #{section.number} · {len(section.rows)} mesures",
            )
        self.select_all()

    def select_all(self) -> None:
        """Sélectionne toutes les entrées disponibles puis redessine les panneaux."""
        self.listbox.selection_set(0, "end")
        self.draw()

    def copy_names(self) -> None:
        """Copie dans le presse-papiers les chemins distincts des sources sélectionnées."""
        self.root.clipboard_clear()
        self.root.clipboard_append(
            "\n".join(dict.fromkeys(str(s.path) for s in self.selected()))
        )

    def add_paths(self, paths: Sequence[str]) -> None:
        """Charge les nouveaux journaux, déduplique les sources et affiche les erreurs."""
        known = {s.path for s in self.sections}
        errors = []
        try:
            candidates = discover(paths)
        except ValueError as exc:
            messagebox.showerror("Lecture des journaux", str(exc))
            return
        for path in candidates:
            if path in known:
                continue
            try:
                self.sections.extend(read_log(path))
                known.add(path)
            except (OSError, ValueError) as exc:
                errors.append(str(exc))
        self.refresh()
        if errors:
            messagebox.showwarning("Fichiers non chargés", "\n".join(errors))

    def open_files(self) -> None:
        """Ouvre le sélecteur de fichiers et charge les chemins choisis."""
        self.add_paths(
            filedialog.askopenfilenames(
                filetypes=[("Guide logs", "*.txt"), ("Tous", "*")]
            )
        )

    def open_directory(self) -> None:
        """Ouvre le sélecteur de dossier et charge ses journaux sans récursion."""
        path = filedialog.askdirectory()
        if path:
            self.add_paths([path])

    def draw(self) -> None:
        """Reconstruit les panneaux selon la sélection en conservant le zoom compatible."""
        selected = self.selected()
        key = (tuple((s.path, s.number) for s in selected), self.relative.get())
        if key != self.view_key:
            self.window = None
        self.view_key = key
        if self.slider:
            self.slider.disconnect_events()
        self.slider = None
        self.slider_figure.clear()
        self.figure.clear()
        self.series, self.time_axes = [], []
        names = [name for name, value in self.metrics.items() if value.get()]
        grid = self.figure.add_gridspec(max(2, len(names)), 2, width_ratios=(2.5, 1))
        for i, name in enumerate(names):
            ax = self.figure.add_subplot(
                grid[i, 0], sharex=self.time_axes[0] if i else None
            )
            ax.set_title(name, fontsize=10)
            ax.set_ylabel(
                "ms"
                if name == "Impulsions"
                else "SNR"
                if name == "SNR"
                else self.unit.get()
            )
            if name == "État du suivi":
                ax.set_ylabel("")
                ax.set_yticks(
                    [v for v, _, _ in TRACKING_STATES],
                    [t for _, t, _ in TRACKING_STATES],
                    fontsize=7,
                )
                ax.set_ylim(-0.2, 1.2)
                ax.set_gid("tracking")
            ax.grid(alpha=0.25)
            self.time_axes.append(ax)
        middle = max(2, len(names)) // 2
        self.scatter = self.figure.add_subplot(grid[:middle, 1])
        calibration = self.figure.add_subplot(grid[middle:, 1])
        self.scatter.set(
            title="Dispersion · période affichée",
            xlabel=f"AD ({self.unit.get()})",
            ylabel=f"DEC ({self.unit.get()})",
        )
        calibration.set(
            title="Calibration complète", xlabel="dx (px)", ylabel="dy (px)"
        )
        for ax in (self.scatter, calibration):
            ax.set_aspect("equal", adjustable="datalim")
            ax.grid(alpha=0.25)
        all_x = []
        for source in calibrations_for(selected):
            for direction in ("West", "East", "North", "South"):
                rows = [r for r in source.rows if r.get("Direction") == direction]
                if rows:
                    calibration.plot(
                        [r["dx"] for r in rows],
                        [r["dy"] for r in rows],
                        ".-",
                        label=f"{source.start:%d/%m %H:%M:%S} #{source.number} {direction}",
                    )
        if not calibration.lines:
            calibration.text(
                0.5,
                0.5,
                "Aucune trajectoire de calibration disponible",
                ha="center",
                va="center",
                transform=calibration.transAxes,
                fontsize=8,
            )
        for index, section in enumerate(selected):
            if section.kind == "calibration":
                continue
            if not section.rows:
                continue
            data = guide_data(section, self.unit.get())
            x = (
                data["time"]
                if self.relative.get()
                else np.array(
                    [
                        date2num(section.start + timedelta(seconds=float(t)))
                        for t in data["time"]
                    ]
                )
            )
            all_x.extend(x)
            self.series.append((section, data, x))
            curves = {
                "Erreurs": [("ra", "AD"), ("dec", "DEC")],
                "Impulsions": [("ra_pulse", "AD"), ("dec_pulse", "DEC")],
                "RMS": [("rms", "Total")],
                "SNR": [("snr", "SNR")],
            }
            for name, ax in zip(names, self.time_axes):
                if name == "État du suivi":
                    states = tracking_status(section)
                    for value, title, color in TRACKING_STATES:
                        mask = states == value
                        ax.plot(x[mask], states[mask], ".", color=color, markersize=4)
                    continue
                for field, label in curves[name]:
                    y = rolling_rms(data) if field == "rms" else data[field]
                    xx, yy = broken_line(x, y, data["time"])
                    ax.plot(xx, yy, linewidth=0.8, label=f"{index + 1} {label}")
                if name == "Erreurs":
                    bad = ~data["valid"]
                    ax.plot(
                        x[bad],
                        np.zeros(bad.sum()),
                        "rx",
                        markersize=4,
                        label=f"{index + 1} Rejets",
                    )
        for ax in self.time_axes:
            if ax.lines and ax.get_gid() != "tracking":
                ax.legend(fontsize=6, loc="upper right", ncol=3)
        if calibration.lines:
            calibration.legend(fontsize=6, ncol=2)
        if self.time_axes:
            if not self.relative.get():
                locator = AutoDateLocator()
                self.time_axes[-1].xaxis.set_major_locator(locator)
                self.time_axes[-1].xaxis.set_major_formatter(
                    ConciseDateFormatter(locator)
                )
            self.time_axes[-1].set_xlabel(
                "Temps relatif (s)"
                if self.relative.get()
                else "Heure locale du journal"
            )
        if all_x and self.time_axes:
            low, high = min(all_x), max(all_x)
            if low == high:
                high += 1 if self.relative.get() else 1 / 86400
            self.window = self.window or (low, high)
            slider_ax = self.slider_figure.add_axes([0.05, 0.35, 0.90, 0.35])
            self.slider = RangeSlider(slider_ax, "", low, high, valinit=self.window)
            self.slider.valtext.set_visible(False)
            self.slider.on_changed(self.change_time)
            self.time_axes[0].set_xlim(*self.window)
            for ax in self.time_axes:
                ax.callbacks.connect("xlim_changed", self.axes_changed)
            self.update_window()
        else:
            self.window = None
            self.period.set("Aucune courbe temporelle")
            self.update_window()
        self.status.set(
            f"{len(selected)} sections sélectionnées · {sum(len(s.warnings) for s in selected)} avertissements · "
            "Les sections vides sont conservées ; aucune mesure n’est inventée."
        )
        self.toolbar.update()
        self.slider_canvas.draw_idle()
        self.canvas.draw_idle()

    def change_time(self, values: Sequence[float]) -> None:
        """Applique les bornes du curseur sans créer de fenêtre de durée nulle."""
        if self.syncing:
            return
        low, high = map(float, values)
        if low >= high:
            self.syncing = True
            try:
                self.slider.set_val(self.window)
            finally:
                self.syncing = False
            return
        self.window = low, high
        self.syncing = True
        try:
            self.time_axes[0].set_xlim(low, high)
        finally:
            self.syncing = False
        self.update_window()

    def axes_changed(self, ax: Axes) -> None:
        """Répercute le zoom Matplotlib vers le curseur en bornant la période disponible."""
        if self.syncing or self.slider is None:
            return
        low, high = sorted(ax.get_xlim())
        low, high = max(low, self.slider.valmin), min(high, self.slider.valmax)
        if low >= high:
            low, high = self.slider.valmin, self.slider.valmax
        self.slider.set_val((low, high))

    def update_window(self) -> None:
        """Recalcule dispersion, échelles et statistiques pour la fenêtre temporelle courante."""
        self.scatter.clear()
        self.scatter.set(
            title="Dispersion · période affichée",
            xlabel=f"AD ({self.unit.get()})",
            ylabel=f"DEC ({self.unit.get()})",
        )
        self.scatter.set_aspect("equal", adjustable="datalim")
        self.scatter.grid(alpha=0.25)
        for section, data, x in self.series:
            mask = (
                np.ones(len(x), dtype=bool)
                if self.window is None
                else (x >= self.window[0]) & (x <= self.window[1])
            )
            self.scatter.plot(
                data["ra"][mask], data["dec"][mask], ".", markersize=2, alpha=0.4
            )
        if self.window:
            low, high = self.window
            duration = (high - low) * (1 if self.relative.get() else 86400)
            if self.relative.get():
                label = f"{low:.1f} – {high:.1f} s"
            else:
                from matplotlib.dates import num2date

                label = (
                    f"{num2date(low):%d/%m %H:%M:%S} – {num2date(high):%d/%m %H:%M:%S}"
                )
            self.period.set(f"{label} · durée {duration:.1f} s")
            for ax in self.time_axes:
                if ax.get_gid() == "tracking":
                    continue
                values = []
                for line in ax.lines:
                    x, y = map(np.asarray, line.get_data())
                    mask = (x >= low) & (x <= high) & np.isfinite(y)
                    values.extend(y[mask])
                if values:
                    bottom, top = min(values), max(values)
                    margin = max((top - bottom) * 0.05, 0.05)
                    ax.set_ylim(bottom - margin, top + margin)
        text = []
        for index, section in enumerate(self.selected()):
            low, high = -math.inf, math.inf
            if self.window and section.kind == "guide":
                offset = 0 if self.relative.get() else date2num(section.start)
                factor = 1 if self.relative.get() else 86400
                low, high = ((value - offset) * factor for value in self.window)
                # Tolérance aux arrondis des dates Matplotlib exprimées en jours.
                low, high = low - 1e-5, high + 1e-5
            text.extend(
                [
                    f"{index + 1}. {section.label}",
                    summary(section, self.unit.get(), low, high),
                ]
            )
            text.extend(section.warnings)
            text.extend(section.metadata)
            text.extend(calibration_details(section))
            text.extend(
                f"Après t={t}s (heure exacte inconnue) : {event}"
                if t is not None
                else event
                for t, event in section.events
            )
            text.append("")
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("end", "\n".join(text))
        self.details.configure(state="disabled")
        self.canvas.draw_idle()

    def reset(self) -> None:
        """Rétablit toute la période disponible sur le curseur partagé."""
        if self.slider:
            self.slider.set_val((self.slider.valmin, self.slider.valmax))

    def export(self) -> None:
        """Exporte les sections sélectionnées complètes, indépendamment du zoom courant."""
        selected = self.selected()
        if not selected:
            messagebox.showinfo("Export HTML", "Sélectionner au moins une section.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".html", filetypes=[("HTML", "*.html")]
        )
        if path:
            try:
                write_report(selected, Path(path), self.relative.get(), self.unit.get())
                self.status.set(f"Rapport enregistré : {path} (séquences complètes)")
            except (OSError, ValueError) as exc:
                messagebox.showerror("Export HTML", str(exc))
