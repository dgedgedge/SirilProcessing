#!/usr/bin/env python3
"""Produit un comparatif HTML/CSV des chaînes et contrôles de restauration.

Utilisation : python bin/compareRestoration.py DOSSIER [DOSSIER ...] -o PREFIXE.
Analyse uniquement les rapports existants, sans Siril et sans modifier les FITS.
Voir docs/scripts/compareRestoration/README.md pour les unités et les limites.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.restoration_comparison import discover, export, read_report


def main(argv: list[str] | None = None) -> int:
    """Analyse les chemins CLI ; retourne 1 si un rapport ne peut pas être lu.

    Les rapports invalides sont signalés sur stderr, les rapports valides sont
    exportés. Une erreur partielle reste un échec dans le code de retour.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', nargs='+', type=Path, help='Rapports ou répertoires traités')
    parser.add_argument('-o', '--output', type=Path, default=Path('restoration_comparison'),
                        help='Préfixe des sorties .html et .csv (défaut : %(default)s)')
    parser.add_argument('--recursive', action='store_true',
                        help='Recherche récursive de restoration.json')
    args = parser.parse_args(argv)
    rows = []
    errors = False
    paths: list[Path] = []
    for source in args.inputs:
        try:
            paths.extend(discover([source], args.recursive))
        except (OSError, ValueError) as error:
            print(f'Erreur : {error}', file=sys.stderr)
            errors = True
    for path in dict.fromkeys(paths):
        print(f'Lecture : {path}', file=sys.stderr)
        try:
            rows.extend(read_report(path))
        except (OSError, ValueError) as error:
            print(f'Erreur : {path} : {error}', file=sys.stderr)
            errors = True
    if not rows:
        return 1
    try:
        for path in export(rows, args.output):
            print(path.resolve())
    except (OSError, ValueError) as error:
        print(f'Erreur export : {error}', file=sys.stderr)
        return 1
    return int(errors)


if __name__ == '__main__':
    sys.exit(main())
