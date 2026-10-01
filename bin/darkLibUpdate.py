#!/bin/env python3
"""Point d’entrée de mise à jour et d’inspection de la bibliothèque de darks."""

from __future__ import annotations

import argparse
import logging
import os
import sys

# Add the parent directory to the path to import the lib module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.config import Config
from lib.darkprocess import DarkLib
from lib.siril_utils import Siril


def main() -> None:
    """Charge la configuration CLI puis liste ou met à jour les masters de calibration."""
    config = Config.from_command_line()

    # Création du parser d'arguments
    parser = argparse.ArgumentParser(
        description="Création d'une bibliothèque de master darks pour Siril",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    config.register(DarkLib, parser)
    config.register(Siril, parser)
    config.add_arguments(parser)

    args = config.parse_args(parser)

    # Configuration de la journalisation
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )
    logging.info(f"Log level set to {args.log_level}")

    # Configuration de la journalisation...

    # Sauvegarde de la configuration si demandé
    if args.save_config:
        if not config.save_requested(args):
            return 1

    # Configuration globale de Siril
    siril_path = config.get("siril_path")
    siril_mode = args.siril_mode
    try:
        Siril.configure_defaults(siril_path=siril_path, siril_mode=siril_mode)
        logging.info(
            f"Configuration Siril validée: path={siril_path}, mode={siril_mode}"
        )
    except ValueError as e:
        logging.error(f"Erreur de configuration Siril: {e}")
        logging.error(
            "Vérifiez que Siril est installé et accessible avec les paramètres spécifiés"
        )
        print(f"Erreur: {e}")
        return 1

    # Ces variables peuvent être locales car elles ne sont utilisées que dans main()
    dark_library_path = os.path.abspath(config.get("dark_library_path"))
    work_dir = os.path.abspath(args.work_dir)
    os.makedirs(work_dir, exist_ok=True)

    logging.info("Starting Siril dark library creation script.")

    os.makedirs(dark_library_path, exist_ok=True)

    # Créer l'instance DarkLib
    darklib = DarkLib(config, force_recalc=args.force_recalc)

    # Si l'option --list-darks est spécifiée, liste les master darks et termine
    if args.list_darks:
        darklib.list_master_darks()
    # Si l'option --input-dirs est présente traiter les darks
    elif args.input_dirs:
        dark_groups = darklib.group_dark_files(
            args.input_dirs, log_groups=True, log_skipped=args.log_skipped
        )

        if dark_groups:
            logging.info(
                f"Found {len(dark_groups)} unique dark groups based on temperature, exposure time and gain."
            )
            # Arrêt anticipé si --dummy est activé
            if args.dummy:
                logging.info(
                    "Option --dummy activée : arrêt du script avant traitement Siril."
                )
            else:
                # Traiter tous les groupes
                darklib.process_all_groups(
                    dark_groups, validate_darks=args.validate_darks
                )

                # Générer le rapport de traitement si demandé
                if args.report:
                    darklib.generate_processing_report()
        else:
            logging.warning("No dark files found or processed. Script finished.")

    logging.info("Siril dark library creation script completed.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠️  Traitement interrompu par l'utilisateur.")
        print(
            "   Les fichiers temporaires peuvent être conservés dans le répertoire de travail."
        )
        sys.exit(1)
    except Exception as e:
        logging.error(f"Erreur inattendue: {e}")
        sys.exit(1)

# End of darklib.py script
