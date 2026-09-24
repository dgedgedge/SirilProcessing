"""Nommage commun des fichiers des étapes de post-traitement."""
from pathlib import Path
import re


def treatment_file_prefix(report_path):
    """Conserve les appels autonomes ; préfixe les artefacts des étapes indexées."""
    stem = Path(report_path).stem
    return f'{stem}_' if re.match(r'^\d{2,}_', stem) else ''
