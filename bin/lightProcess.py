#!/bin/env python3
"""
Script pour traiter automatiquement les images light.

Ce script :
1. Analyse un répertoire contenant des sous-répertoires 'light' et éventuellement 'flat'
2. Détecte automatiquement les caractéristiques des images light
3. Trouve le master dark correspondant dans la librairie
4. Effectue le prétraitement (soustraction du dark) et le stacking

Usage:
    python lightProcessor.py <répertoire_session> [options]
    
Exemple:
    python lightProcessor.py /path/to/session_M31 --dark-lib /path/to/dark_library
"""

import os
import sys
import argparse
import logging
from pathlib import Path

# Add the parent directory to the path to import the lib module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.lightprocessor import LightProcessor
from lib.config import Config


def setup_logging(log_level: str) -> None:
    """Configure le système de logging."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        force=True
    )


def main():
    config = Config()
    
    parser = argparse.ArgumentParser(
        description="Traitement automatique des images light avec prétraitement et stacking",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    # Arguments positionnels
    parser.add_argument(
        "session_dir",
        help="Répertoire de session contenant les sous-répertoires 'light' (et éventuellement 'flat')"
    )
    
    # Arguments optionnels pour les chemins
    parser.add_argument(
        '-d', '--dark-lib',
        dest="dark_library_path",
        default=config.get("dark_library_path"),
        help="Chemin vers la librairie de master darks"
    )
    
    parser.add_argument(
        '--output',
        dest="output_dir",
        help="Répertoire de sortie pour les résultats (défaut: session_dir/processed)"
    )
    
    parser.add_argument(
        '-w', '--work-dir',
        dest="work_dir",
        default=config.get("work_dir"),
        help="Répertoire de travail temporaire"
    )
    
    # Arguments pour le traitement
    parser.add_argument(
        '-t', '--temperature-precision',
        dest='temperature_precision',
        type=float,
        default=config.get("temperature_precision"),
        help="Précision de correspondance des températures en °C"
    )
    
    parser.add_argument(
        '-f', '--force',
        dest='force_reprocess',
        action="store_true",
        help="Force le retraitement même si les fichiers de sortie existent"
    )
    
    # Arguments pour Siril
    parser.add_argument(
        '-s', '--siril-path',
        dest='siril_path',
        default=config.get("siril_path"),
        help="Chemin vers l'exécutable Siril"
    )
    
    parser.add_argument(
        '-m', '--siril-mode',
        dest='siril_mode',
        choices=["native", "flatpak", "appimage"],
        default=config.get("siril_mode"),
        help="Mode d'exécution de Siril"
    )
    
    # Arguments pour le stacking
    parser.add_argument(
        '--stack-method',
        dest='stack_method',
        choices=["average", "median", "sum"],
        default=config.get("stack_method"),
        help="Méthode de stacking"
    )
    
    parser.add_argument(
        '-r', '--rejection-method',
        dest='rejection_method',
        choices=["none", "sigma", "linear", "winsor", "percentile"],
        default=config.get("rejection_method"),
        help="Méthode de rejet d'outliers"
    )
    
    parser.add_argument(
        '--rejection-param1',
        dest='rejection_param1',
        type=float,
        default=config.get("rejection_param1"),
        help="Premier paramètre de rejet"
    )
    
    parser.add_argument(
        '--rejection-param2',
        dest='rejection_param2',
        type=float,
        default=config.get("rejection_param2"),
        help="Second paramètre de rejet"
    )
    
    # Arguments de configuration
    parser.add_argument(
        '-S', '--save-config',
        dest='save_config',
        action='store_true',
        help="Sauvegarde la configuration actuelle pour une utilisation future"
    )
    
    parser.add_argument(
        '-l', '--log-level',
        dest='log_level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        default='WARNING',
        help="Niveau de journalisation"
    )
    
    parser.add_argument(
        '-D', '--dry-run',
        dest='dry_run',
        action="store_true",
        help="Simule le traitement sans l'exécuter réellement"
    )
    
    args = parser.parse_args()
    
    # Configuration du logging
    setup_logging(args.log_level)
    logging.info(f"Log level set to {args.log_level}")
    
    # Sauvegarde de la configuration si demandé
    if args.save_config:
        config.set_from_args(args)
        config.save()
    
    # Validation du répertoire de session
    session_dir = Path(args.session_dir)
    if not session_dir.exists():
        logging.error(f"Le répertoire de session n'existe pas: {session_dir}")
        return 1
    
    if not session_dir.is_dir():
        logging.error(f"Le chemin spécifié n'est pas un répertoire: {session_dir}")
        return 1
    
    # Vérification de la présence du répertoire 'light' ou 'Light'
    light_dir = None
    for light_name in ["light", "Light"]:
        potential_light_dir = session_dir / light_name
        if potential_light_dir.exists():
            light_dir = potential_light_dir
            break
    
    if light_dir is None:
        logging.error(f"Aucun répertoire 'light' ou 'Light' trouvé dans: {session_dir}")
        logging.info("Structure attendue: session_dir/light/ ou session_dir/Light/ (et optionnellement session_dir/flat/)")
        return 1
    
    # Définition des répertoires par défaut
    if not args.output_dir:
        args.output_dir = session_dir / "processed"
    
    if not args.work_dir:
        args.work_dir = config.get("work_dir")
    
    # Création du processeur
    try:
        processor = LightProcessor(
            session_dir=session_dir,
            dark_library_path=config.get("dark_library_path"),
            output_dir=Path(args.output_dir),
            work_dir=Path(args.work_dir),
            temp_precision=args.temperature_precision,
            siril_path=args.siril_path,
            siril_mode=args.siril_mode,
            force_reprocess=args.force_reprocess,
            dry_run=args.dry_run
        )
    except Exception as e:
        logging.error(f"Erreur lors de l'initialisation du processeur: {e}")
        return 1
    
    # Configuration des paramètres de stacking
    stack_params = {
        "method": args.stack_method,
        "rejection": args.rejection_method,
        "rejection_low": args.rejection_param1,
        "rejection_high": args.rejection_param2
    }
    
    # Traitement des images
    try:
        logging.info(f"Début du traitement de la session: {session_dir}")
        logging.info(f"Répertoire light: {light_dir}")
        
        flat_dir = session_dir / "flat"
        if flat_dir.exists():
            logging.info(f"Répertoire flat détecté: {flat_dir} (sera ignoré pour l'instant)")
        
        success = processor.process_session(stack_params)
        
        if success:
            logging.info("Traitement terminé avec succès")
            return 0
        else:
            logging.error("Le traitement a échoué")
            return 1
            
    except KeyboardInterrupt:
        logging.warning("Traitement interrompu par l'utilisateur")
        return 1
    except Exception as e:
        logging.error(f"Erreur durant le traitement: {e}")
        if args.log_level == "DEBUG":
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n⚠️  Traitement interrompu par l'utilisateur.")
        print("   Les fichiers temporaires peuvent être conservés dans le répertoire de travail.")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Erreur inattendue: {e}")
        sys.exit(1)