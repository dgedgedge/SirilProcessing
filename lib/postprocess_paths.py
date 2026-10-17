"""Nommage commun des fichiers des étapes de post-traitement."""

from __future__ import annotations

import re
import shutil
from pathlib import Path


def treatment_file_prefix(report_path: Path | str) -> str:
    """Conserve les appels autonomes ; préfixe les artefacts des étapes indexées."""
    stem = Path(report_path).stem
    return f"{stem}_" if re.match(r"^\d{2,}_", stem) else ""


def reset_work_dir(report_path: Path | str) -> Path:
    """Réinitialise et retourne le dossier visible `<rapport>_work/`.

    Les anciens produits, y compris un éventuel sous-dossier historique 001,
    sont supprimés avant de recréer le dossier. L'appelant doit vérifier que
    son entrée n'est pas située dans ce dossier avant de demander sa remise à
    zéro. Les erreurs du système de fichiers sont propagées.
    """
    report = Path(report_path).resolve()
    work = report.parent / f"{report.stem}_work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=False)
    return work


def reset_ordered_work_dir(report_path: Path | str, order: int = 1) -> Path:
    """Réinitialise le dossier d'une étape puis crée un sous-dossier ordonné.

    Réserve le niveau `<ordre>` aux traitements qui rangent leurs produits dans
    une sous-étape explicite. L'ordre doit être positif ; une valeur invalide est
    refusée avant toute suppression. L'appelant protège son entrée comme pour
    `reset_work_dir`.
    """
    if order < 1:
        raise ValueError("order doit être >= 1")
    work = reset_work_dir(report_path) / f"{order:03d}"
    work.mkdir(exist_ok=False)
    return work
