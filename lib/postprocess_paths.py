"""Nommage commun des fichiers des étapes de post-traitement."""

from __future__ import annotations

import re
import shutil
from pathlib import Path


def treatment_file_prefix(report_path: Path | str) -> str:
    """Conserve les appels autonomes ; préfixe les artefacts des étapes indexées."""
    stem = Path(report_path).stem
    return f"{stem}_" if re.match(r"^\d{2,}_", stem) else ""


def reset_ordered_work_dir(report_path: Path | str, order: int = 1) -> Path:
    """Réinitialise le dossier de travail d'une étape et crée un sous-dossier ordonné.

    Le parent `<étape>_work/` est supprimé puis recréé au démarrage de l'étape
    afin de ne conserver que les artefacts de l'exécution courante.
    """
    report = Path(report_path).resolve()
    if order < 1:
        raise ValueError("order doit être >= 1")
    parent = report.parent / f"{report.stem}_work"
    if parent.exists():
        shutil.rmtree(parent)
    work = parent / f"{order:03d}"
    work.mkdir(parents=True, exist_ok=False)
    return work
