#!/bin/env python3
import os
import sys
import logging
import argparse
import json

# Add the parent directory to the path to import the lib module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.config import Config
from lib.darkprocess import DarkLib

SIRIL_PATH = "siril"
DARK_LIBRARY_PATH = os.path.expanduser("~/darkLib")  # Par défaut : ~/darkLib
WORK_DIR = os.path.expanduser("~/tmp/sirilWorkDir")  # Ajout du workdir par défaut

# --- Siril Stacking Parameters (Default values, can be overridden by command line) ---
SIRIL_STACK_METHOD = "average"  # "average" (avec rejet) ou "median"
SIRIL_OUTPUT_NORM = "noscale"   # Changer la valeur par défaut à "noscale" pour "Aucune normalisation"
SIRIL_CFA = False # By default, images are considered monochrome
SIRIL_REJECTION_METHOD = "winsorizedsigma"
SIRIL_REJECTION_PARAM1 = 3.0
SIRIL_REJECTION_PARAM2 = 3.0
SIRIL_MODE = "flatpak" # Default mode for Siril: flatpak
MAX_AGE_DAYS = 182  # Période par défaut (6 mois)

CONFIG_FILE = os.path.expanduser("~/.siril_darklib_config.json")

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.warning(f"Could not read config file {CONFIG_FILE}: {e}")
    return {}

def save_config(config: dict) -> None:
    try:
        # Options de chemin
        if "dark_library_path" in config:
            config["dark_library_path"] = os.path.abspath(config["dark_library_path"])
        if "work_dir" in config:
            config["work_dir"] = os.path.abspath(config["work_dir"])
        if "siril_path" in config:
            config["siril_path"] = config["siril_path"]  # Peut rester relatif si souhaité
            
        # Options de stacking et de traitement
        for option in ["siril_mode", "cfa", "output_norm", "rejection_method", 
                      "rejection_param1", "rejection_param2", "max_age_days",
                      "stack_method"]:
            if option in config:
                # Enregistrer la valeur telle quelle
                pass
                
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        logging.info(f"Configuration saved to {CONFIG_FILE}")
    except Exception as e:
        logging.error(f"Could not save config file {CONFIG_FILE}: {e}")


def main() -> None:
    # Seules les variables utilisées dans run_siril_script() doivent rester globales
    global SIRIL_PATH, SIRIL_MODE
    
    config = Config()
    
    # Création du parser d'arguments
    parser = argparse.ArgumentParser(
        description="Création d'une bibliothèque de master darks pour Siril",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    # Configuration des arguments
    parser.add_argument(
        '--input-dirs',
        nargs='+',
        help="Liste des répertoires contenant les fichiers dark à traiter"
    )
    parser.add_argument(
        '--dark-library-path',
        type=str,
        default=config.get("dark_library_path"),
        help=f"Répertoire où sont stockés les master darks. (Défaut: '{config.get('dark_library_path')}')"
    )
    parser.add_argument(
        '--work-dir',
        type=str,
        default=config.get("work_dir"),
        help=f"Répertoire de travail temporaire. (Défaut: '{config.get('work_dir')}')"
    )
    parser.add_argument(
        '--siril-path',
        type=str,
        default=config.get("siril_path"),
        help=f"Chemin vers l'exécutable Siril. (Défaut: '{config.get('siril_path')}')"
    )
    parser.add_argument(
        '--siril-mode',
        choices=['native', 'flatpak', 'appimage'],
        default=config.get("siril_mode"),
        help=f"Mode d'exécution de Siril. (Défaut: '{config.get('siril_mode')}')"
    )
    parser.add_argument(
        '--save-config',
        action='store_true',
        help="Sauvegarde la configuration actuelle pour une utilisation future"
    )
    parser.add_argument(
        '--dummy',
        action='store_true',
        help="Mode test: analyse les fichiers mais n'exécute pas Siril"
    )

    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        default='INFO',
        help="Niveau de journalisation"
    )
    
    parser.add_argument(
        '--list-darks',
        action='store_true',
        help="Liste tous les master darks disponibles dans la bibliothèque avec leurs caractéristiques"
    )
    parser.add_argument(
        '--log-skipped',
        action='store_true',
        help="Log les fichiers ignorés (non-DARK ou FITS invalides)"
    )
    parser.add_argument(
        '--max-age',
        type=int,
        default=config.get("max_age_days"),
        help=f"Nombre maximum de jours d'écart entre le dark le plus récent et le plus ancien d'un groupe. (Défaut: {config.get('max_age_days')} jours)"
    )
    parser.add_argument(
        '--cfa',
        action='store_true',
        default=config.get("cfa", False),
        help="Indique que les images sont en couleur (CFA). Par défaut, les images sont considérées monochromes."
    )
    parser.add_argument(
        '--output-norm',
        choices=['addscale', 'noscale', 'rejection'],
        default=config.get("output_norm"),
        help=f"Méthode de normalisation pour Siril. (Défaut: '{config.get('output_norm')}')"
    )
    parser.add_argument(
        '--rejection-method',
        choices=['winsorizedsigma', 'sigma', 'minmax', 'percentile', 'none'],
        default=config.get("rejection_method"),
        help=f"Méthode de rejet pour Siril. (Défaut: '{config.get('rejection_method')}')"
    )
    parser.add_argument(
        '--rejection-param1',
        type=float,
        default=config.get("rejection_param1"),
        help=f"Premier paramètre de rejet pour Siril. (Défaut: {config.get('rejection_param1')})"
    )
    parser.add_argument(
        '--rejection-param2',
        type=float,
        default=config.get("rejection_param2"),
        help=f"Second paramètre de rejet pour Siril. (Défaut: {config.get('rejection_param2')})"
    )
    parser.add_argument(
        '--stack-method',
        choices=['average', 'median'],
        default=config.get("stack_method"),
        help=f"Méthode d'empilement: 'average' (Empilement par moyenne avec rejet) ou 'median' (Empilement médian). (Défaut: '{config.get('stack_method')}')"
    )


    # Code d'analyse des arguments inchangé...
    args = parser.parse_args()

    # Configuration de la journalisation
    logging.basicConfig(level=args.log_level, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    
    # Configuration de la journalisation...
    
    # Sauvegarde de la configuration si demandé
    if args.save_config:
        config.set_from_args(args)
        config.save()

    # Mise à jour des variables globales nécessaires au run_siril_script
    SIRIL_PATH = config.get("siril_path")
    SIRIL_MODE = args.siril_mode
    
    # Ces variables peuvent être locales car elles ne sont utilisées que dans main()
    dark_library_path = os.path.abspath(config.get("dark_library_path"))
    work_dir = os.path.abspath(args.work_dir)
    os.makedirs(work_dir, exist_ok=True)    

    logging.info("Starting Siril dark library creation script.")

    os.makedirs(DARK_LIBRARY_PATH, exist_ok=True)
    
    # Créer l'instance DarkLib
    darklib = DarkLib(config, SIRIL_PATH, SIRIL_MODE)
    
    # Si l'option --list-darks est spécifiée, liste les master darks et termine
    if args.list_darks:
        darklib.list_master_darks()
        return

    # Si l'option --input-dirs est présente traiter les darks
    if args.input_dirs:
        dark_groups = darklib.group_dark_files(
            args.input_dirs, 
            log_groups=True, 
            log_skipped=args.log_skipped
        )
    
        if not dark_groups:
            logging.info("No dark files found or processed. Script finished.")
            return

        logging.info(f"Found {len(dark_groups)} unique dark groups based on temperature, exposure time and gain.")

        # Arrêt anticipé si --dummy est activé
        if args.dummy:
            logging.info("Option --dummy activée : arrêt du script avant traitement Siril.")
            return

        # Traiter tous les groupes
        darklib.process_all_groups(dark_groups)
    
    logging.info("Siril dark library creation script completed.")

if __name__ == "__main__":
    main()

# End of darklib.py script