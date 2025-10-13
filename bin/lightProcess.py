#!/bin/env python3
"""
Script pour traiter automatiquement les images light avec option de mosaïque.

Ce script :
1. Analyse un ou plusieurs répertoires contenant des sous-répertoires 'light' et éventuellement 'flat'
2. Détecte automatiquement les caractéristiques des images light
3. Trouve le master dark correspondant dans la librairie
4. Effectue le prétraitement (soustraction du dark) et le stacking
5. Optionnellement, assemble les sessions en mosaïque

Usage:
    python lightProcessor.py <répertoire_session> [options]
    python lightProcessor.py <répertoire1> <répertoire2> [répertoire3...] [options]
    
Exemples:
    # Traitement d'une seule session
    python lightProcessor.py /path/to/session_M31 --dark-lib /path/to/dark_library
    
    # Traitement de plusieurs sessions en séquence
    python lightProcessor.py /path/to/session_M31 /path/to/session_M42 /path/to/session_NGC7000
    
    # Traitement avec création de mosaïque automatique
    python lightProcessor.py /path/to/session_M31_nord /path/to/session_M31_sud --mosaic
    
    # Mosaïque avec nom personnalisé
    python lightProcessor.py session1 session2 session3 --mosaic --mosaic-name "M31_complete"
"""

import os
import sys
import argparse
import logging
from pathlib import Path

# Add the parent directory to the path to import the lib module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.lightprocessor import LightProcessor
from lib.siril_utils import Siril
from lib.mosaic import Mosaic, calculate_common_basename
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
        "session_dirs",
        nargs='+',
        help="Un ou plusieurs répertoires de session contenant les sous-répertoires 'light' (et éventuellement 'flat')"
    )
    
    # Arguments optionnels pour les chemins
    parser.add_argument(
        '-d', '--dark-lib',
        dest="dark_library_path",
        default=config.get("dark_library_path"),
        help=f"Répertoire où sont stockés les master darks. (Défaut: '{config.get('dark_library_path')}')"
    )
    
    parser.add_argument(
        '--output',
        dest="output_dir",
        default=config.get("output_dir"),
        help=f"Répertoire de sortie pour les résultats. (défaut: '{config.get('output_dir')}')"
    )
    
    parser.add_argument(
        '-w', '--work-dir',
        dest='work_dir',
        type=str,
        default=config.get("work_dir"),
        help=f"Répertoire de travail temporaire. (Défaut: '{config.get('work_dir')}')"
    )
    
    # Arguments pour le traitement
    parser.add_argument(
        '-t', '--temperature-precision',
        dest='temperature_precision',
        type=float,
        default=config.get("temperature_precision"),
        help=f"Précision d'arrondi pour la température en degrés Celsius. (Défaut: {config.get('temperature_precision')}°C)"
    )
    
    parser.add_argument(
        '-f', '--force',
        dest='force_reprocess',
        action="store_true",
        help="Force le retraitement même si le fichier de sortie existent"
    )
    
    # Arguments pour Siril
    parser.add_argument(
        '-s', '--siril-path',
        dest='siril_path',
        default=config.get("siril_path"),
        help=f"Chemin vers l'exécutable Siril. (Défaut: '{config.get('siril_path')}')"
    )
    
    parser.add_argument(
        '-m', '--siril-mode',
        dest='siril_mode',
        choices=["native", "flatpak", "appimage"],
        default=config.get("siril_mode"),
        help=f"Mode d'exécution de Siril. (Défaut: '{config.get('siril_mode')}')"
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
    
    # Arguments pour la mosaïque
    parser.add_argument(
        '--mosaic',
        dest='create_mosaic',
        action='store_true',
        help="Créer une mosaïque après traitement de toutes les sessions"
    )
    
    parser.add_argument(
        '--mosaic-name',
        dest='mosaic_name',
        type=str,
        help="Nom personnalisé pour la mosaïque (obligatoire si le nom automatique fait moins de 3 caractères)"
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
        help=f"Niveau de journalisation. (Défaut: 'WARNING')"
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
    
    # Validation des répertoires de session
    session_dirs = []
    for session_path in args.session_dirs:
        session_dir = Path(session_path)
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
        
        session_dirs.append(session_dir)
    
    logging.info(f"Validation réussie pour {len(session_dirs)} répertoires de session")
    
    # Validation spécifique pour la mosaïque
    if args.create_mosaic:
        if len(session_dirs) < 2:
            logging.error("La mosaïque nécessite au moins 2 sessions de traitement")
            return 1
        
        # Vérifier le nom de la mosaïque si pas fourni explicitement
        if not args.mosaic_name:
            auto_name = calculate_common_basename(session_dirs)
            if len(auto_name) < 3:
                logging.error(f"Le nom automatique '{auto_name}' est trop court (< 3 caractères)")
                logging.error("Veuillez spécifier un nom explicite avec --mosaic-name")
                return 1
        
        logging.info(f"Mosaïque activée: {len(session_dirs)} sessions")
    
    # Définition des répertoires par défaut
    if not args.output_dir:
        args.output_dir = config.get("output_dir")
    
    if not args.work_dir:
        args.work_dir = config.get("work_dir")
    
    # Configuration globale de Siril
    try:
        Siril.configure_defaults(siril_path=args.siril_path, siril_mode=args.siril_mode)
        logging.info(f"Configuration Siril validée: path={args.siril_path}, mode={args.siril_mode}")
    except ValueError as e:
        logging.error(f"Erreur de configuration Siril: {e}")
        logging.error("Vérifiez que Siril est installé et accessible avec les paramètres spécifiés")
        return 1
    
    # Configuration des paramètres de stacking
    stack_params = {
        "method": args.stack_method,
        "rejection": args.rejection_method,
        "rejection_low": args.rejection_param1,
        "rejection_high": args.rejection_param2
    }
    
    # Traitement des images pour chaque répertoire de session
    total_sessions = len(session_dirs)
    successful_sessions = 0
    failed_sessions = []
    successful_processors = []  # Garder les références des processors réussis
    
    for i, session_dir in enumerate(session_dirs, 1):
        logging.info(f"{'='*60}")
        logging.info(f"Traitement de la session {i}/{total_sessions}: {session_dir}")
        logging.info(f"{'='*60}")
        
        try:
            # Création du processeur pour cette session
            processor = LightProcessor(
                session_dir=session_dir,
                dark_library_path=config.get("dark_library_path"),
                output_dir=Path(args.output_dir),
                work_dir=Path(args.work_dir),
                temp_precision=args.temperature_precision,
                force_reprocess=args.force_reprocess,
                dry_run=args.dry_run
            )
        except Exception as e:
            logging.error(f"Erreur lors de l'initialisation du processeur pour {session_dir}: {e}")
            failed_sessions.append(session_dir)
            continue
        
        try:
            logging.info(f"Début du traitement de la session: {session_dir}")
            
            # Vérifier le répertoire light pour cette session
            light_dir = None
            for light_name in ["light", "Light"]:
                potential_light_dir = session_dir / light_name
                if potential_light_dir.exists():
                    light_dir = potential_light_dir
                    break
            
            logging.info(f"Répertoire light: {light_dir}")
            
            flat_dir = session_dir / "flat"
            if flat_dir.exists():
                logging.info(f"Répertoire flat détecté: {flat_dir} (sera ignoré pour l'instant)")
            
            success = processor.process_session(stack_params)
            
            if success:
                logging.info(f"✅ Session {session_dir} traitée avec succès")
                successful_sessions += 1
                successful_processors.append(processor)  # Sauvegarder le processor réussi
            else:
                logging.error(f"❌ Échec du traitement de la session {session_dir}")
                failed_sessions.append(session_dir)
                
        except KeyboardInterrupt:
            logging.warning("Traitement interrompu par l'utilisateur")
            failed_sessions.append(session_dir)
            break
        except Exception as e:
            logging.error(f"Erreur durant le traitement de {session_dir}: {e}")
            if args.log_level == "DEBUG":
                import traceback
                traceback.print_exc()
            failed_sessions.append(session_dir)
            continue
    
    # Résumé final
    logging.info(f"{'='*60}")
    logging.info(f"RÉSUMÉ DU TRAITEMENT")
    logging.info(f"{'='*60}")
    logging.info(f"Sessions traitées avec succès: {successful_sessions}/{total_sessions}")
    
    if failed_sessions:
        logging.error(f"Sessions échouées ({len(failed_sessions)}):")
        for failed_session in failed_sessions:
            logging.error(f"  - {failed_session}")
    
    # Traitement de la mosaïque si demandé et si suffisamment de sessions ont réussi
    if args.create_mosaic and successful_sessions >= 2:
        logging.info(f"{'='*60}")
        logging.info(f"CRÉATION DE LA MOSAÏQUE")
        logging.info(f"{'='*60}")
        
        try:
            # Filtrer les sessions qui ont réussi
            successful_session_dirs = [session_dirs[i] for i, session_dir in enumerate(session_dirs) 
                                     if session_dir not in failed_sessions]
            
            # Collecter tous les fichiers de sortie des processors réussis
            all_output_files = []
            for processor in successful_processors:
                output_files = processor.get_output_files()
                all_output_files.extend(output_files)
                logging.info(f"Fichiers récupérés du processor {processor.session_dir.name}: {len(output_files)} fichiers")
            
            logging.info(f"Total des fichiers pour la mosaïque: {len(all_output_files)}")
            for file in all_output_files:
                logging.info(f"  - {file}")
            
            # Calculer le nom de la mosaïque
            if args.mosaic_name:
                mosaic_name = args.mosaic_name
            else:
                auto_name = calculate_common_basename(successful_session_dirs)
                if len(auto_name) < 3:
                    logging.error(f"Le nom automatique '{auto_name}' est trop court (< 3 caractères)")
                    logging.error("Veuillez spécifier un nom explicite avec --mosaic-name")
                    return 1
                mosaic_name = auto_name
            
            # Créer l'instance de mosaïque avec les fichiers explicites
            mosaic = Mosaic(
                output_dir=Path(args.output_dir),
                work_dir=Path(args.work_dir),
                mosaic_name=mosaic_name,
                input_files=all_output_files
            )
            
            # Créer la mosaïque
            if not args.dry_run:
                mosaic_result = mosaic.create_mosaic()
                if mosaic_result:
                    logging.info(f"🌟 Mosaïque créée avec succès: {mosaic_result}")
                else:
                    logging.error("❌ Échec de la création de la mosaïque")
                    
                # Nettoyer les fichiers temporaires
                mosaic.cleanup()
            else:
                logging.info("🔍 Mode simulation: mosaïque non créée")
                
        except Exception as e:
            logging.error(f"Erreur lors de la création de la mosaïque: {e}")
            if args.log_level == "DEBUG":
                import traceback
                traceback.print_exc()
    
    elif args.create_mosaic and successful_sessions < 2:
        logging.warning("Mosaïque demandée mais moins de 2 sessions traitées avec succès")
    
    # Retour final
    if successful_sessions == total_sessions:
        if args.create_mosaic:
            logging.info("🎉 Toutes les sessions traitées et mosaïque créée avec succès")
        else:
            logging.info("🎉 Toutes les sessions ont été traitées avec succès")
        return 0
    elif successful_sessions > 0:
        logging.warning(f"⚠️  Traitement partiel: {successful_sessions}/{total_sessions} sessions réussies")
        return 1
    else:
        logging.error("💥 Aucune session n'a pu être traitée")
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