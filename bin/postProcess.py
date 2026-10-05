#!/usr/bin/env python3
"""
Script de post-traitement extensible pour une image FITS unique.

Ce script prend UNE SEULE image FITS calibrée en entrée et exécute les
traitements activés par défaut : analyse du gradient, résolution
astrométrique suivie de l'étalonnage photométrique des couleurs, puis
déconvolution avec audit de PSF et comparaison stellaire avant/après,
puis réduction du bruit contrôlée. --postprocess-backend cosmic-clarity
remplace ces deux dernières étapes par des réseaux neuronaux optionnels.
Il produit un rapport JSON et les fichiers propres à chaque traitement.

Le script utilise _PostProcessorSequence pour exécuter les traitements.
Les chemins relatifs sont résolus depuis le dossier de lancement.
Le fichier de sortie pour les résultats est celui fourni en argument.

Usage:
    python postProcess.py <input_file> [output_file] [options]

Exemples:
    python postProcess.py /path/to/pp_light_0001.fit
    python postProcess.py /path/to/image.fit /tmp/output/result.json
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.config import Config
from lib.logging_utils import (
    add_session_file_logging,
    remove_session_file_logging,
    setup_logging,
)
from lib.postprocess import _PostProcessorSequence
from lib.siril_utils import Siril


def main() -> None:
    """Exécute les étapes actives sur un FITS et écrit rapports et journal de session."""
    config = Config.from_command_line()
    parser = argparse.ArgumentParser(
        description="Séquence de post-traitements sur une image FITS calibrée",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "input_file", type=str, help="Chemin vers l'image FITS calibrée"
    )
    parser.add_argument(
        "output_file",
        type=str,
        nargs="?",
        default=None,
        help="Chemin vers le fichier de sortie JSON (défaut: <input_file>_postProcess.json)",
    )
    parser.add_argument(
        "-l",
        "--log-level",
        dest="log_level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
    )

    config.register(Siril, parser, photometry_aliases=True)
    config.add_arguments(parser)

    # Créer le processeur séquentiel
    seq_processor = _PostProcessorSequence()

    # Ajouter les arguments spécifiques
    config.register(seq_processor, parser)

    args = config.parse_args(parser)
    setup_logging(args.log_level)

    input_file = Path(args.input_file)
    # Déterminer le fichier de sortie JSON
    if args.output_file:
        output_path = Path(args.output_file)
    else:
        # Par défaut: <input_file>_postProcess.json dans le même répertoire
        output_path = input_file.parent / f"{input_file.stem}_postProcess.json"

    log_file = (
        output_path.parent
        / f"{output_path.stem}_steps"
        / f"00_{input_file.stem}_postProcess.log"
    )
    log_handler = None
    try:
        if log_file.resolve() == input_file.resolve():
            raise ValueError("Le fichier de log ne peut pas remplacer l'image d'entrée")
        log_handler = add_session_file_logging(log_file, args.log_level)
        logging.info("Log de post-traitement: %s", log_file)
        logging.info("Log level set to %s", args.log_level)
        if not input_file.is_file():
            logging.error("Fichier introuvable: %s", input_file)
            return 1
        logging.info("Configuration: %s", config.config_file)
        logging.info("Siril: mode=%s, path=%s", args.siril_mode, args.siril_path)
        if args.save_config:
            if not config.save_requested(args):
                return 1
        logging.info("Analyse de: %s", input_file)
        logging.info("Sortie vers: %s", output_path)
        # Exécuter la séquence de post-processing
        seq_processor.set_from_args(args)
        results = seq_processor.post_process(
            input_path=input_file,
            output_path=output_path,
        )

        logging.info(f"\n{'=' * 60}")
        logging.info("RÉSULTATS")
        logging.info(f"{'=' * 60}")

        for processor_name in results:
            logging.info("Traitement terminé: %s", processor_name)

        logging.info(f"\nRapport sauvegardé: {output_path}")
        logging.info("Post-traitements terminés")
        return 0

    except KeyboardInterrupt:
        logging.warning("Traitement interrompu par l'utilisateur")
        return 130
    except Exception as e:
        logging.error("Erreur: %s", e, exc_info=args.log_level == "DEBUG")
        return 1
    finally:
        if log_handler is not None:
            remove_session_file_logging(log_handler)


if __name__ == "__main__":
    sys.exit(main())
