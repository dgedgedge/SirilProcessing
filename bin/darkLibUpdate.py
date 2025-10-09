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
        '-i', '--input-dirs',
        dest='input_dirs',
        nargs='+',
        default=config.get("input_dirs"),
        help="Liste des répertoires contenant les fichiers dark à traiter"
    )
    parser.add_argument(
        '-d', '--dark-library-path',
        dest='dark_library_path',
        type=str,
        default=config.get("dark_library_path"),
        help=f"Répertoire où sont stockés les master darks. (Défaut: '{config.get('dark_library_path')}')"
    )
    parser.add_argument(
        '-w', '--work-dir',
        dest='work_dir',
        type=str,
        default=config.get("work_dir"),
        help=f"Répertoire de travail temporaire. (Défaut: '{config.get('work_dir')}')"
    )
    parser.add_argument(
        '-s', '--siril-path',
        dest='siril_path',
        type=str,
        default=config.get("siril_path"),
        help=f"Chemin vers l'exécutable Siril. (Défaut: '{config.get('siril_path')}')"
    )
    parser.add_argument(
        '-m', '--siril-mode',
        dest='siril_mode',
        choices=['native', 'flatpak', 'appimage'],
        default=config.get("siril_mode"),
        help=f"Mode d'exécution de Siril. (Défaut: '{config.get('siril_mode')}')"
    )
    parser.add_argument(
        '-S', '--save-config',
        dest='save_config',
        action='store_true',
        help="Sauvegarde la configuration actuelle pour une utilisation future"
    )
    parser.add_argument(
        '-D', '--dummy',
        dest='dummy',
        action='store_true',
        help="Mode test: analyse les fichiers mais n'exécute pas Siril"
    )

    parser.add_argument(
        '-l', '--log-level',
        dest='log_level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        default='WARNING',
        help=f"Niveau de journalisation. (Défaut: 'WARNING')"
    )
    
    parser.add_argument(
        '-L', '--list-darks',
        dest='list_darks',
        action='store_true',
        help="Liste tous les master darks disponibles dans la bibliothèque avec leurs caractéristiques"
    )
    parser.add_argument(
        '--log-skipped',
        dest='log_skipped',
        action='store_true',
        help="Log les fichiers ignorés (non-DARK ou FITS invalides)"
    )
    parser.add_argument(
        '-a', '--max-age',
        dest='max_age',
        type=int,
        default=config.get("max_age_days"),
        help=f"Nombre maximum de jours d'écart entre le dark le plus récent et le plus ancien d'un groupe. (Défaut: {config.get('max_age_days')} jours)"
    )
    parser.add_argument(
        '-c', '--cfa',
        dest='cfa',
        action='store_true',
        default=config.get("cfa", False),
        help="Indique que les images sont en couleur (CFA). Par défaut, les images sont considérées monochromes."
    )
    parser.add_argument(
        '-o', '--output-norm',
        dest='output_norm',
        choices=['addscale', 'noscale', 'rejection'],
        default=config.get("output_norm"),
        help=f"Méthode de normalisation pour Siril. (Défaut: '{config.get('output_norm')}')"
    )
    parser.add_argument(
        '-r', '--rejection-method',
        dest='rejection_method',
        choices=['winsorizedsigma', 'sigma', 'minmax', 'percentile', 'none'],
        default=config.get("rejection_method"),
        help=f"Méthode de rejet pour Siril. (Défaut: '{config.get('rejection_method')}')"
    )
    parser.add_argument(
        '--rejection-param1',
        dest='rejection_param1',
        type=float,
        default=config.get("rejection_param1"),
        help=f"Premier paramètre de rejet pour Siril. (Défaut: {config.get('rejection_param1')})"
    )
    parser.add_argument(
        '--rejection-param2',
        dest='rejection_param2',
        type=float,
        default=config.get("rejection_param2"),
        help=f"Second paramètre de rejet pour Siril. (Défaut: {config.get('rejection_param2')})"
    )
    parser.add_argument(
        '--stack-method',
        dest='stack_method',
        choices=['average', 'median'],
        default=config.get("stack_method"),
        help=f"Méthode d'empilement: 'average' (Empilement par moyenne avec rejet) ou 'median' (Empilement médian). (Défaut: '{config.get('stack_method')}')"
    )
    parser.add_argument(
        '-t', '--temperature-precision',
        dest='temperature_precision',
        type=float,
        default=config.get("temperature_precision"),
        help=f"Précision d'arrondi pour la température en degrés Celsius. (Défaut: {config.get('temperature_precision')}°C)"
    )
    parser.add_argument(
        '-n', '--min-darks-threshold',
        dest='min_darks_threshold',
        type=int,
        default=config.get("min_darks_threshold", 10),
        help=f"Seuil minimum de darks pour mettre à jour un master dark existant. Un master dark sera remplacé si le nombre de darks disponibles dépasse ce seuil OU s'il dépasse le nombre de darks utilisés dans le master dark précédent. (Défaut: {config.get('min_darks_threshold', 0)})"
    )
    parser.add_argument(
        '-f', '--force-recalc',
        dest='force_recalc',
        action='store_true',
        help="Force le recalcul de tous les master darks existants, même s'ils sont plus récents que les fichiers sources. Utile pour tester de nouveaux paramètres de regroupement."
    )
    parser.add_argument(
        '-v', '--validate-darks',
        dest='validate_darks',
        action='store_true',
        default=config.get("validate_darks", False),
        help="Valide les fichiers darks en analysant leurs statistiques pour détecter ceux pris avec le capot ouvert (présence de lumière parasite)."
    )
    parser.add_argument(
        '--no-validate-darks',
        dest='validate_darks',
        action='store_false',
        help="Désactive la validation des fichiers darks."
    )
    parser.add_argument(
        '-R', '--report',
        dest='report',
        action='store_true',
        default=config.get("report", False),
        help="Génère un rapport détaillé du traitement et de la validation effectués."
    )
    parser.add_argument(
        '--no-report',
        dest='report',
        action='store_false',
        help="Désactive la génération du rapport de traitement."
    )


    # Code d'analyse des arguments inchangé...
    args = parser.parse_args()

    # Configuration de la journalisation
    logging.basicConfig(level=args.log_level, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s', force=True)
    logging.info(f"Log level set to {args.log_level}")

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
    darklib = DarkLib(config, SIRIL_PATH, SIRIL_MODE, force_recalc=args.force_recalc)
    

    # Si l'option --list-darks est spécifiée, liste les master darks et termine
    if args.list_darks:
        darklib.list_master_darks()
    # Si l'option --input-dirs est présente traiter les darks
    elif args.input_dirs:
        dark_groups = darklib.group_dark_files(
            args.input_dirs, 
            log_groups=True, 
            log_skipped=args.log_skipped
        )
    
        if dark_groups:
            logging.info(f"Found {len(dark_groups)} unique dark groups based on temperature, exposure time and gain.")
            # Arrêt anticipé si --dummy est activé
            if args.dummy:
                logging.info("Option --dummy activée : arrêt du script avant traitement Siril.")
            else:
                # Traiter tous les groupes
                darklib.process_all_groups(dark_groups, validate_darks=args.validate_darks)
                
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
        print("   Les fichiers temporaires peuvent être conservés dans le répertoire de travail.")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Erreur inattendue: {e}")
        sys.exit(1)

# End of darklib.py script