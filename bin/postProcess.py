#!/usr/bin/env python3
"""
Script de post-traitement extensible pour une image FITS unique.

Ce script prend UNE SEULE image FITS calibrée en entrée et exécute les
traitements activés par défaut : analyse du gradient, résolution
astrométrique Gaia suivie de l'étalonnage spectrophotométrique SPCC des couleurs, puis
comparaison des chaînes complètes de débruitage et de déconvolution,
avec contrôles locaux et finaux sur une référence commune. L’option
--no-restoration-compare-chains rétablit la comparaison par étape.
Cosmic Clarity et Siril participent par défaut ; --disable-clarity écarte Clarity.

Le script utilise PostProcessingProcessor, commun avec lightProcess, pour
exécuter les traitements, diagnostics et exports.
Le rendu final AutoGHS avec séparation StarNet est activé par défaut
(--disable-autoghs le désactive) et produit des FITS et JPEG.
--autoghs-only reprend la même entrée, la même sortie et les mêmes paramètres
du traitement complet. Le FITS linéaire précédent est retrouvé dans les rapports ;
StarNet, AutoGHS, la saturation, les exports et l’annotation sont relancés.
--disable-final-color écarte explicitement la saturation de cette reprise.
--color-only reprend les couches GHS existantes pour la couleur et les exports,
sans relancer les traitements amont. --enable-final-color active cette passe
facultative dans le traitement complet ; --disable-final-color la désactive.
La dernière passe recherche D/B/HP avec protection des hautes lumières par défaut.
Les chemins relatifs sont résolus depuis le dossier de lancement.
Le second argument facultatif choisit le répertoire de sortie.

Usage:
    python postProcess.py <input_file> [output_file] [options]

Exemples:
    python postProcess.py /path/to/pp_light_0001.fit
    python postProcess.py /path/to/image.fit /tmp/output/
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.config import Config
from lib.logging_utils import setup_logging
from lib.postprocessing import PostProcessingProcessor
from lib.power_guard import PowerGuard
from lib.siril_utils import Siril


def main() -> int:
    """Exécute les étapes, diagnostics et export sous protection énergétique facultative.

    Les services D-Bus indisponibles ne bloquent pas le calcul ; leurs demandes
    sont libérées après fermeture du journal, y compris après une interruption.
    Le script détient la protection ; les processeurs ne l’activent pas.
    Retourne 0 en cas de succès, 1 pour une erreur ou 130 pour une interruption.
    """
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
        help=(
            "Répertoire de sortie des artefacts (ou ancien chemin de rapport). "
            "Défaut: dossier de l'image d'entrée"
        ),
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

    config.register(PostProcessingProcessor, parser)

    args = config.parse_args(parser)
    setup_logging(args.log_level)

    try:
        input_file = Path(args.input_file).resolve()
        if not input_file.is_file():
            raise FileNotFoundError(f"Fichier introuvable: {input_file}")
        PostProcessingProcessor.prepare_config_save(args)
        if not config.save_requested(args):
            return 1
        config.bind_parameters(args)
        treatment = PostProcessingProcessor()
        report = treatment.report_path_for(input_file, args.output_file)
        with PowerGuard():
            treatment.post_process(input_file, report)
        return 0
    except KeyboardInterrupt:
        logging.warning("Traitement interrompu par l'utilisateur")
        return 130
    except Exception as error:
        logging.error("Erreur: %s", error, exc_info=args.log_level == "DEBUG")
        return 1


if __name__ == "__main__":
    sys.exit(main())
