"""Nommage commun des fichiers des étapes de post-traitement."""

from __future__ import annotations

import re
import shutil
from pathlib import Path


def treatment_file_prefix(report_path: Path | str) -> str:
    """Conserve les appels autonomes ; préfixe les artefacts des étapes indexées."""
    stem = Path(report_path).stem
    return f"{stem}_" if re.match(r"^\d{2,}_", stem) else ""


def reset_work_dir(
    report_path: Path | str, *, directory_name: str | None = None,
) -> Path:
    """Réinitialise et retourne le dossier visible `<rapport>_work/`.

    Les anciens produits, y compris un éventuel sous-dossier historique 001,
    sont supprimés avant de recréer le dossier. L'appelant doit vérifier que
    son entrée n'est pas située dans ce dossier avant de demander sa remise à
    zéro. ``directory_name`` remplace le nom du dossier, dans le même parent
    que le rapport ; il doit être un nom simple, hors « . » et « .. ». Une
    valeur invalide lève ValueError avant toute suppression. Les erreurs du
    système de fichiers sont propagées.
    """
    report = Path(report_path).resolve()
    name = directory_name if directory_name is not None else f"{report.stem}_work"
    if not name or name in (".", "..") or Path(name).name != name:
        raise ValueError("Un nom simple de répertoire de travail est requis")
    work = report.parent / name
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=False)
    return work


def reset_autoghs_work_dir(report_path: Path | str, input_path: Path | str) -> Path:
    """Prépare les cinq sous-étapes directement dans le parent du rapport.

    Nettoie uniquement les dossiers 01_separation à 05_diagnostics et
    l'ancienne enveloppe <rapport>_work. Les rapports, exports conservés et
    fichiers voisins restent intacts. Une entrée située dans l'un des dossiers
    à nettoyer est refusée avec ValueError avant toute suppression. Les liens
    symboliques sont retirés sans parcourir leur cible ; les erreurs d'accès
    sont propagées. Les appels autonomes utilisent également le parent du rapport.
    """
    report, source = Path(report_path).resolve(), Path(input_path).resolve()
    work = report.parent
    products = [work / name for name in (
        "01_separation", "02_stars", "03_ghs", "04_recomposition",
        "05_diagnostics", f"{report.stem}_work",
    )]
    if any(source.is_relative_to(path.resolve()) for path in products):
        raise ValueError("L’entrée se trouve dans le dossier de travail réinitialisé")
    work.mkdir(parents=True, exist_ok=True)
    for path in products:
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    return work


def reset_restoration_work_dir(
    report_path: Path | str, input_path: Path | str, *,
    prepared_directory: Path | str | None = None,
) -> Path:
    """Prépare un dossier direct et protège l'entrée avant toute suppression.

    Dans un objet numéroté (04_restoration et marqueur 01_calibration), les
    chaînes utilisent directement 04_restoration et les moteurs leur rapport, sans
    suffixe _work. Les appels autonomes conservent <rapport>_work, sans 001.
    ``prepared_directory`` désigne un dossier déjà nettoyé par l’orchestrateur,
    contenant les masques et le rapport : il est conservé pour éviter de les
    supprimer à l’initialisation du moteur. Il doit être le parent du rapport
    et ne pas contenir la source, sinon ValueError est levée.
    À la racine numérotée, seuls les dossiers de produits connus sont nettoyés
    (masques, essais, anciennes enveloppes) ; rapports et fichiers voisins sont
    conservés. Les opérations et leurs essais restent isolés. Une source dans le dossier
    réinitialisé est refusée avec ValueError ; les erreurs d'accès sont propagées.
    """
    report, source = Path(report_path).resolve(), Path(input_path).resolve()
    if prepared_directory is not None:
        work = Path(prepared_directory).resolve()
        if work != report.parent or source.is_relative_to(work):
            raise ValueError("Dossier préparé incompatible avec le rapport ou la source")
        work.mkdir(parents=True, exist_ok=True)
        return work
    numbered = any(
        parent.name == "04_restoration"
        and (parent.parent / "01_calibration").is_dir()
        for parent in report.parents
    )
    if numbered and report.parent.name == "04_restoration" and report.stem == "restoration":
        work = report.parent
        if source.is_relative_to(work):
            raise ValueError("L’entrée se trouve dans le dossier de travail réinitialisé")
        work.mkdir(parents=True, exist_ok=True)
        # Nettoyer seulement les produits identifiés, jamais la racine ni ses voisins.
        for entry in work.iterdir():
            owned = entry.name in {"00_mask_origine", "01_chains", "diagnostics", "001"}
            owned = owned or re.match(r"^\d{2,}_(?:denoise|sharpen)_", entry.name)
            if owned and entry.is_symlink():
                entry.unlink()
            elif owned and entry.is_dir():
                shutil.rmtree(entry)
        return work
    name = report.stem if numbered else f"{report.stem}_work"
    work = report.parent / name
    if source.is_relative_to(work.resolve()):
        raise ValueError("L’entrée se trouve dans le dossier de travail réinitialisé")
    return reset_work_dir(report, directory_name=name)
