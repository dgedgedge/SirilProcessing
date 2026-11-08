#!/usr/bin/env python3
"""Régénère les index HTML hors ligne d’une arborescence de résultats existante."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.processing_html import ProcessingHtmlReport


def main() -> int:
    """Indexe les résultats sans calcul d’image ; affiche le chemin d’accueil."""
    parser = argparse.ArgumentParser(description=__doc__)
    ProcessingHtmlReport.add_arguments(parser)
    args = parser.parse_args()
    try:
        print(ProcessingHtmlReport(args.directory).generate())
    except (OSError, ValueError) as error:
        parser.exit(1, f'Indexation impossible : {error}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
