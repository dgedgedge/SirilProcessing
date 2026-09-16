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

    # Traitement sans utiliser de master dark
    python lightProcessor.py /path/to/session_M31 --no-dark
    
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
import shutil
from pathlib import Path

# Add the parent directory to the path to import the lib module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.config import Config


class HelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    """Formatter d'aide qui conserve les sauts de ligne et affiche les valeurs par défaut."""


def setup_logging(log_level: str) -> None:
    """Configure le système de logging."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        force=True
    )


def add_session_file_logging(log_file: Path, log_level: str) -> logging.Handler:
    """Ajoute un fichier de log pour la sous-session courante."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, log_level.upper(), logging.INFO)
    handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logging.getLogger().addHandler(handler)
    return handler


def remove_session_file_logging(handler: logging.Handler) -> None:
    """Retire et ferme un handler de log de sous-session."""
    logging.getLogger().removeHandler(handler)
    handler.close()


def build_session_target_map(input_roots: list[Path]) -> tuple[list[Path], dict[Path, Path]]:
    """Construit la liste des sous-sessions et leur cible parente."""
    from lib.lightprocessor import discover_session_roots

    session_dirs: list[Path] = []
    session_to_target: dict[Path, Path] = {}

    for session_path in input_roots:
        discovered = discover_session_roots(session_path)
        if not discovered:
            continue

        for child in discovered:
            session_dirs.append(child)
            if len(discovered) > 1:
                session_to_target[child] = session_path
            else:
                session_to_target[child] = child

    return session_dirs, session_to_target


def target_output_root(base_output_dir: Path, target_root: Path) -> Path:
    return base_output_dir / target_root.name


def session_output_root(base_output_dir: Path, target_root: Path, session_dir: Path) -> Path:
    return target_output_root(base_output_dir, target_root) / "sessions" / session_dir.name


def session_work_root(base_work_dir: Path, target_root: Path, session_dir: Path) -> Path:
    return base_work_dir / target_root.name / "sessions" / session_dir.name


def stack_output_root(base_output_dir: Path, target_root: Path) -> Path:
    return target_output_root(base_output_dir, target_root) / "stack"


def target_work_root(base_work_dir: Path, target_root: Path) -> Path:
    return base_work_dir / target_root.name


def normalize_work_base(work_dir: Path) -> Path:
    """Homogénéise la racine de travail en supprimant un suffixe 'process'."""
    work_dir = Path(work_dir)
    if work_dir.name == "process":
        return work_dir.parent
    return work_dir


def _dir_last_modified(path: Path) -> float:
    """Retourne la date de dernière modification max d'un dossier (récursif)."""
    if not path.exists():
        return 0.0

    latest = path.stat().st_mtime
    if path.is_dir():
        for child in path.rglob("*"):
            try:
                child_mtime = child.stat().st_mtime
            except OSError:
                continue
            if child_mtime > latest:
                latest = child_mtime
    return latest


def should_rebuild_stack(combined_output: Path, calibrated_dirs: list[Path]) -> bool:
    """Décide si le stack final doit être reconstruit selon les dates de modif."""
    if not combined_output.exists():
        return True

    combined_mtime = combined_output.stat().st_mtime
    latest_input = 0.0
    for directory in calibrated_dirs:
        latest_input = max(latest_input, _dir_last_modified(directory))

    return latest_input > combined_mtime


def main():
    config = Config()

    siril_pipeline_epilog = """
Flux de traitement attendu:
    1) Pour chaque sous-session, on trouve les lights et les flats disponibles.
    2) Si des flats existent, on les calibres avec un master dark de la bibliotheque pour creer
       un master flat de cette sous-session.
    3) On calibre ensuite les lights de cette sous-session avec le master dark et le master flat.
    4) On exporte la sequence calibree sous forme de FITS, sans stack intermediaire.
    5) Une fois toutes les sous-sessions traitees, on reconstruit la sequence finale sur l'ensemble
       des FITS calibres et on effectue un seul stack final de la cible.

Exemple de pipeline Siril par sous-session:
    1) convert <sequence_name> -out=<work_dir>/process
    2) (si flats disponibles) creation d'un master flat:
        convert <flat_sequence_name> -out=<work_dir>/flat_process
        calibrate <flat_sequence_name> -dark=<master_dark_pour_flat> -cc=dark -cfa
        stack pp_<flat_sequence_name> median -norm=mul -out=<master_flat>
    3) calibrate <sequence_name> -dark=<master_dark> -flat=<master_flat> -cc=dark -cfa -equalize_cfa
       Ajouter -debayer en mode drizzle=off ; conserver le CFA en auto/force.
    4) copie Python de process/pp_<sequence_name>_*.fit(s) vers
       <output_dir>/<target>/sessions/<session>/<session>_<group>_calibrated

Stack final sur l'ensemble des sorties calibrees d'une cible:
    1) convert <target>_ -out=<work_dir>/<target>/stacking/01_registration
    2) seqfindstar <target>_
    3) seqplatesolve <target>_ -force -nocache -disto=ps_distortion si active
    4) register <target>_ -2pass -transf=<align_transform>
    5) Analyse Drizzle et selection ; seqapplyreg avec -drizzle si pertinent
       (sinon dematricage CFA et alignement standard).
    6) stack r_r_<target>_ <method/rejection> ... -output_norm -out=<target>_combined
       Repertoires fixes dans stacking : 00_inputs, 01_registration, 02_quality,
       03_capability (ou SKIPPED.txt), 04_stacking. Un repertoire par script.
       Le stack median utilise -framing=min pour eviter les images alignees
       de tailles differentes.

Sorties:
    - Des FITS calibres par sous-session
    - Un FITS final empile par cible
    - Un JSON <target>_combined.drizzle.json contenant analyse, reglages et decision
    - Un JPG de previsualisation genere automatiquement a partir du FITS final

Traitement Siril de mosaique (si --mosaic):
    1) convert mosaic_ -out=<mosaic_output_dir>
    2) seqplatesolve mosaic_ -force -nocache -disto=ps_distortion
    3) seqapplyreg mosaic_ -framing=max
    4) stack r_mosaic_ rej 3 3 -norm=addscale -output_norm -rgb_equal -maximize -overlap_norm -feather=5 -out=<mosaic_name>_mosaic
"""
    
    parser = argparse.ArgumentParser(
        description="Traitement automatique des images light avec prétraitement et stacking",
        formatter_class=HelpFormatter,
        epilog=siril_pipeline_epilog
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
        '--no-dark',
        dest='no_dark',
        action='store_true',
        help="Désactive l'utilisation des master darks (calibration sans soustraction de dark)"
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
    
    parser.add_argument("--drizzle", choices=["off", "auto", "force"], default=config.get("drizzle"))
    for option in ("scale", "pixfrac"):
        parser.add_argument(f"--drizzle-{option}", default=config.get(f"drizzle_{option}"), help="Valeur numérique ou auto")
    parser.add_argument("--drizzle-kernel", choices=["auto", "square", "gaussian", "turbo", "point"], default=config.get("drizzle_kernel"))
    for option, kind in [("min_frames", int), ("min_coverage", float), ("fwhm_limit", float), ("max_drift", float)]:
        parser.add_argument("--drizzle-" + option.replace("_", "-"), type=kind, default=config.get("drizzle_" + option))

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

    parser.add_argument(
        '--roundness-filter',
        dest='roundness_filter',
        type=str,
        default=config.get("roundness_filter"),
        help=f"Filtre de rondeur des étoiles appliqué aux mesures natives avant analyse et application du Drizzle (ex: '2k'). Utiliser 'none' pour désactiver. (Défaut: '{config.get('roundness_filter')}')"
    )

    parser.add_argument(
        '--fwhm-filter',
        dest='fwhm_filter',
        type=str,
        default=config.get("fwhm_filter"),
        help=f"Filtre de FWHM appliqué avant le stack final via seqapplyreg -filter-wfwhm (ex: '1.8k'). Utiliser 'none' pour désactiver. (Défaut: '{config.get('fwhm_filter')}')"
    )

    parser.add_argument(
        '--fwhm-reject-percent',
        dest='fwhm_reject_percent',
        type=float,
        default=config.get("fwhm_reject_percent", 10.0),
        help="Pourcentage des images les moins bonnes en FWHM à exclure avant le stack final (0 à 95). Défaut safe: 10%%."
    )

    parser.add_argument(
        '--no-fwhm-reject',
        dest='no_fwhm_reject',
        action='store_true',
        default=False,
        help="Désactive totalement le rejet proportionnel FWHM (force à 0%%)."
    )

    parser.add_argument('--nbstars-filter', default=config.get('nbstars_filter'),
                        help="Minimum d'étoiles avant Drizzle : seuil, pourcentage ou k MAD ; none désactive")
    parser.add_argument('--no-roundness-weighted', dest='roundness_weighted', action='store_false',
                        default=config.get('roundness_weighted'), help="Désactive la pondération de rondeur avant Drizzle")
    parser.add_argument('--roundness-weight-max-extra', type=int, choices=range(0, 9),
                        default=config.get('roundness_weight_max_extra'), help="Répétitions supplémentaires maximales selon la rondeur")

    parser.add_argument(
        '--no-fwhm-weighted',
        dest='fwhm_weighted',
        action='store_false',
        default=config.get("fwhm_weighted", True),
        help="Désactive la pondération FWHM des meilleures images (safe actif par défaut)."
    )

    parser.add_argument(
        '--fwhm-weight-max-extra',
        dest='fwhm_weight_max_extra',
        type=int,
        default=config.get("fwhm_weight_max_extra", 1),
        help="Nombre max de répétitions supplémentaires pour les meilleures images quand la pondération FWHM est active. Défaut safe: 1."
    )

    parser.add_argument(
        '--align-transform',
        dest='align_transform',
        choices=["shift", "similarity", "affine", "homography"],
        default=config.get("align_transform"),
        help=f"Transformation d'alignement pour register au stack final. (Défaut: '{config.get('align_transform')}')"
    )

    parser.add_argument(
        '--stack-platesolve',
        dest='enable_stack_platesolve',
        action='store_true',
        default=config.get("enable_stack_platesolve", True),
        help="Active seqplatesolve avant register dans le stack final multi-sessions (utile en cas d'alignement difficile). Défaut: activé."
    )

    parser.add_argument(
        '--force-stacking',
        dest='force_stacking',
        action='store_true',
        default=config.get("force_stacking", False),
        help="Force uniquement la reconstruction du stack final multi-sessions (supprime les artefacts de stack existants sans forcer la recalibration)"
    )

    parser.add_argument(
        '--purge-target',
        dest='purge_target',
        action='store_true',
        default=False,
        help="Supprime l'arborescence de sortie et de travail des cibles passées en argument avant traitement (sans toucher les autres cibles)."
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
        default='INFO',
        help=f"Niveau de journalisation. (Défaut: 'INFO')"
    )
    
    parser.add_argument(
        '-D', '--dry-run',
        dest='dry_run',
        action="store_true",
        help="Simule le traitement sans l'exécuter réellement"
    )

    parser.add_argument(
        '--keep-intermediate',
        dest='keep_intermediate',
        action='store_true',
        default=config.get("keep_intermediate", False),
        help="Conserve les répertoires/fichiers temporaires de prétraitement pour inspection manuelle."
    )
    
    args = parser.parse_args()

    from lib.drizzle import validate_settings, cache_matches
    try:
        validate_settings({key: getattr(args, key) for key in Config.DEFAULTS if key.startswith("drizzle")})
    except ValueError as exc:
        parser.error(str(exc))

    if args.no_fwhm_reject:
        args.fwhm_reject_percent = 0.0

    # Imports différés pour permettre l'affichage de l'aide sans dépendances complètes.
    from lib.lightprocessor import LightProcessor, discover_session_roots, stack_session_outputs
    from lib.siril_utils import Siril
    from lib.mosaic import Mosaic, calculate_common_basename
    
    # Configuration du logging
    setup_logging(args.log_level)
    logging.info(f"Log level set to {args.log_level}")
    
    # Sauvegarde de la configuration si demandé
    if args.save_config:
        config.set_from_args(args)
        config.save()
    
    # Définition des répertoires par défaut
    if not args.output_dir:
        args.output_dir = config.get("output_dir")

    if not args.work_dir:
        args.work_dir = config.get("work_dir")

    output_base_dir = Path(args.output_dir)
    work_base_dir = normalize_work_base(Path(args.work_dir))
    if work_base_dir != Path(args.work_dir):
        logging.info(
            f"Répertoire de travail normalisé: {args.work_dir} -> {work_base_dir}"
        )

    # Validation des répertoires de session
    input_roots: list[Path] = []
    for session_path in args.session_dirs:
        session_dir = Path(session_path)
        if not session_dir.exists():
            logging.error(f"Le répertoire de session n'existe pas: {session_dir}")
            return 1

        if not session_dir.is_dir():
            logging.error(f"Le chemin spécifié n'est pas un répertoire: {session_dir}")
            return 1

        discovered = discover_session_roots(session_dir)
        if not discovered:
            logging.error(f"Aucun répertoire 'light' ou 'Light' trouvé dans: {session_dir}")
            logging.info("Structure attendue: session_dir/light/ ou session_dir/Light/ (et optionnellement session_dir/flat/)")
            return 1

        input_roots.append(session_dir)

    session_dirs, session_to_target = build_session_target_map(input_roots)
    if not session_dirs:
        logging.error("Aucune sous-session valide à traiter")
        return 1

    for root_dir in input_roots:
        discovered = discover_session_roots(root_dir)
        if len(discovered) > 1:
            logging.info(f"Répertoire cible détecté avec {len(discovered)} sous-sessions: {root_dir}")

    logging.info(f"Validation réussie pour {len(session_dirs)} répertoires de session")

    if args.purge_target:
        unique_targets = sorted({session_to_target[session] for session in session_dirs}, key=lambda p: str(p))
        for target_root in unique_targets:
            out_tree = target_output_root(output_base_dir, target_root)
            work_tree = target_work_root(work_base_dir, target_root)

            for tree in [out_tree, work_tree]:
                if tree.exists():
                    try:
                        shutil.rmtree(tree)
                        logging.info(f"Arborescence cible purgée: {tree}")
                    except Exception as exc:
                        logging.warning(f"Impossible de purger {tree}: {exc}")
    
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
        **{key: getattr(args, key) for key in Config.DEFAULTS if key.startswith("drizzle")},
        "method": args.stack_method,
        "rejection": args.rejection_method,
        "rejection_low": args.rejection_param1,
        "rejection_high": args.rejection_param2,
        "roundness_filter": args.roundness_filter,
        "nbstars_filter": args.nbstars_filter,
        "roundness_weighted": args.roundness_weighted,
        "roundness_weight_max_extra": args.roundness_weight_max_extra,
        "fwhm_filter": args.fwhm_filter,
        "fwhm_reject_percent": args.fwhm_reject_percent,
        "fwhm_weighted": args.fwhm_weighted,
        "fwhm_weight_max_extra": args.fwhm_weight_max_extra,
        "align_transform": args.align_transform,
        "enable_stack_platesolve": args.enable_stack_platesolve,
        "force_stacking": args.force_stacking,
    }

    if args.no_dark:
        logging.warning("Mode sans dark activé: la soustraction de dark sera ignorée")
    
    # Traitement des images pour chaque répertoire de session
    total_sessions = len(session_dirs)
    successful_sessions = 0
    failed_sessions = []
    successful_processors = []  # Garder les références des processors réussis
    parent_target_outputs = {}  # parent root -> fichiers combinés
    target_wcs_totals = {}  # target root -> aggregated WCS stats

    for i, session_dir in enumerate(session_dirs, 1):
        logging.info(f"{'='*60}")
        logging.info(f"Traitement de la session {i}/{total_sessions}: {session_dir}")
        logging.info(f"{'='*60}")

        target_root = session_to_target.get(session_dir, session_dir)
        processor_output_dir = session_output_root(output_base_dir, target_root, session_dir)
        processor_work_dir = session_work_root(work_base_dir, target_root, session_dir)

        if args.force_reprocess:
            for directory in [processor_output_dir, processor_work_dir]:
                if directory.exists():
                    try:
                        shutil.rmtree(directory)
                        logging.info(f"Répertoire supprimé (relance --force): {directory}")
                    except Exception as exc:
                        logging.warning(f"Impossible de supprimer {directory}: {exc}")

        session_log_file = processor_work_dir / f"{session_dir.name}_lightProcess.log"
        session_log_handler = add_session_file_logging(session_log_file, args.log_level)
        
        try:
            # Création du processeur pour cette session
            logging.info("Log de sous-session: %s", session_log_file)
            processor = LightProcessor(
                session_dir=session_dir,
                dark_library_path=args.dark_library_path,
                output_dir=processor_output_dir,
                work_dir=processor_work_dir,
                temp_precision=args.temperature_precision,
                force_reprocess=args.force_reprocess,
                dry_run=args.dry_run,
                use_dark=not args.no_dark,
                keep_intermediate=args.keep_intermediate,
            )
        except Exception as e:
            logging.error(f"Erreur lors de l'initialisation du processeur pour {session_dir}: {e}")
            failed_sessions.append(session_dir)
            remove_session_file_logging(session_log_handler)
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
            
            flat_dir = None
            for flat_name in ["flat", "Flat"]:
                potential_flat_dir = session_dir / flat_name
                if potential_flat_dir.exists():
                    flat_dir = potential_flat_dir
                    break
            if flat_dir is not None:
                logging.info(f"Répertoire flat détecté: {flat_dir} (sera utilisé pour le prétraitement)")
            
            success = processor.process_session(stack_params)
            session_stats = processor.get_session_stats()

            target_stats = target_wcs_totals.setdefault(
                target_root,
                {
                    "lights_total": 0,
                    "wcs_present": 0,
                    "wcs_missing": 0,
                    "wcs_unreadable": 0,
                    "sessions_total": 0,
                    "sessions_success": 0,
                },
            )
            target_stats["sessions_total"] += 1
            target_stats["lights_total"] += int(session_stats.get("lights_total", 0) or 0)
            target_stats["wcs_present"] += int(session_stats.get("wcs_present", 0) or 0)
            target_stats["wcs_missing"] += int(session_stats.get("wcs_missing", 0) or 0)
            target_stats["wcs_unreadable"] += int(session_stats.get("wcs_unreadable", 0) or 0)

            logging.info(
                "Session stats - total lights: %d | WCS: present=%d, missing=%d, unreadable=%d | rejets: invalides=%d, non-light=%d, no-dark=%d, erreurs=%d | conservées=%d",
                session_stats.get("lights_total", 0),
                session_stats.get("wcs_present", 0),
                session_stats.get("wcs_missing", 0),
                session_stats.get("wcs_unreadable", 0),
                session_stats.get("rejected_invalid_metadata", 0),
                session_stats.get("rejected_not_light_type", 0),
                session_stats.get("rejected_no_matching_dark", 0),
                session_stats.get("rejected_processing_failure", 0),
                session_stats.get("kept_for_stacking", 0),
            )
            
            if success:
                logging.info(f"✅ Session {session_dir} traitée avec succès")
                successful_sessions += 1
                target_stats["sessions_success"] += 1
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
        finally:
            remove_session_file_logging(session_log_handler)
    
    # Montage des sorties d’une même cible à partir des sous-sessions
    # Si un répertoire cible contient plusieurs sous-sessions, on les empile ensuite en un seul résultat.
    for root_dir in input_roots:
        discovered = discover_session_roots(root_dir)
        if len(discovered) <= 1:
            continue

        session_outputs = []
        for processor in successful_processors:
            if processor.session_dir in discovered:
                session_outputs.extend(processor.get_output_files())

        if args.force_stacking:
            logging.info(
                f"Mode --force-stacking: reconstruction des FITS calibrés depuis le disque pour {root_dir}"
            )
            output_root = target_output_root(output_base_dir, root_dir) / "sessions"
            disk_outputs = []
            per_session_counts = {}
            for session_dir in discovered:
                session_count = 0
                session_root = output_root / session_dir.name
                if session_root.exists():
                    for calibrated_dir in sorted(session_root.rglob("*_calibrated")):
                        if not calibrated_dir.is_dir():
                            continue

                        fit_files = sorted(calibrated_dir.glob("pp_*.fit"))
                        fits_files = sorted(calibrated_dir.glob("pp_*.fits"))
                        session_count += len(fit_files) + len(fits_files)
                        disk_outputs.extend(fit_files)
                        disk_outputs.extend(fits_files)

                per_session_counts[session_dir.name] = session_count

            for session_name, count in sorted(per_session_counts.items()):
                logging.info(
                    "[force-stacking] Sous-session %s: %d FITS calibrés retrouvés sur disque",
                    session_name,
                    count,
                )

            logging.info(
                "[force-stacking] Total FITS calibrés retrouvés sur disque pour %s: %d",
                root_dir.name,
                len(disk_outputs),
            )

            session_outputs = disk_outputs

        if session_outputs:
            # Déduplique les chemins pour éviter de doubler les entrées lors du fallback disque.
            deduped_outputs = []
            seen = set()
            for output_file in session_outputs:
                absolute_path = Path(output_file).absolute()
                if absolute_path in seen:
                    continue
                seen.add(absolute_path)
                deduped_outputs.append(absolute_path)
            session_outputs = deduped_outputs

            logging.info(
                f"Stack final multi-sessions pour {root_dir.name}: {len(session_outputs)} FITS calibrés"
            )

        if session_outputs:
            target_stack_dir = stack_output_root(output_base_dir, root_dir)
            target_stack_dir.mkdir(parents=True, exist_ok=True)
            target_stack_work_dir = target_work_root(work_base_dir, root_dir) / "stacking"
            stack_log_files = [target_stack_work_dir / f"{root_dir.name}_stacking.log"]
            for session_dir in discovered:
                stack_log_files.append(
                    session_work_root(work_base_dir, root_dir, session_dir) / f"{session_dir.name}_stacking.log"
                )
            stack_log_handlers = [
                add_session_file_logging(stack_log_file, args.log_level)
                for stack_log_file in stack_log_files
            ]

            try:
                logging.info(
                    "Logs de stack: %s",
                    ", ".join(str(stack_log_file) for stack_log_file in stack_log_files),
                )
                stack_candidates = [
                    target_stack_dir / f"{root_dir.name}_combined.fit",
                    target_stack_dir / f"{root_dir.name}_combined.fits",
                ]
                existing_combined = next((c for c in stack_candidates if c.exists()), None)
                calibrated_dirs = []
                session_outputs_root = target_output_root(output_base_dir, root_dir) / "sessions"
                for session_dir in discovered:
                    session_root = session_outputs_root / session_dir.name
                    if session_root.exists():
                        calibrated_dirs.extend([d for d in session_root.rglob("*_calibrated") if d.is_dir()])

                if not args.force_stacking and existing_combined and cache_matches(existing_combined, stack_params) and not should_rebuild_stack(existing_combined, calibrated_dirs):
                    logging.info(
                        "Stack final déjà à jour pour %s (dates répertoires calibrés inchangées): %s",
                        root_dir.name,
                        existing_combined,
                    )
                    parent_target_outputs[root_dir] = [existing_combined]
                    continue

                stack_report = {}
                combined_output = stack_session_outputs(
                    output_files=session_outputs,
                    output_dir=target_stack_dir,
                    work_dir=work_base_dir,
                    target_name=root_dir.name,
                    stack_params=stack_params,
                    force_stacking=args.force_stacking,
                    stack_report=stack_report,
                )
                logging.info(
                    "Stack stats [%s] - total=%d | rejets: layout_incompatible=%d, layout_illisible=%d, fwhm_proportion=%d, fwhm_non_mesurable=%d | conservées uniques=%d | entrées effectives=%d",
                    root_dir.name,
                    stack_report.get("total_input", 0),
                    stack_report.get("rejected_incompatible_layout", 0),
                    stack_report.get("rejected_unreadable_layout", 0),
                    stack_report.get("rejected_fwhm_proportion", 0),
                    stack_report.get("rejected_fwhm_unmeasurable", 0),
                    stack_report.get("kept_unique_for_stack", 0),
                    stack_report.get("kept_effective_for_stack", 0),
                )
                if combined_output:
                    parent_target_outputs[root_dir] = [combined_output]
                    logging.info(f"✅ Sortie combinée créée pour la cible {root_dir}: {combined_output}")
                else:
                    logging.error(f"❌ Échec de la sortie combinée pour la cible {root_dir}")
            finally:
                for stack_log_handler in stack_log_handlers:
                    remove_session_file_logging(stack_log_handler)
        else:
            logging.warning(f"Aucun FITS calibré trouvé pour le stack final de la cible {root_dir}")

    # Résumé final
    logging.info(f"{'='*60}")
    logging.info(f"RÉSUMÉ DU TRAITEMENT")
    logging.info(f"{'='*60}")
    logging.info(f"Sessions traitées avec succès: {successful_sessions}/{total_sessions}")

    if target_wcs_totals:
        logging.info("RÉSUMÉ WCS PAR CIBLE")
        for target_root in sorted(target_wcs_totals.keys(), key=lambda p: p.name.lower()):
            stats = target_wcs_totals[target_root]
            lights_total = int(stats.get("lights_total", 0) or 0)
            wcs_present = int(stats.get("wcs_present", 0) or 0)
            wcs_missing = int(stats.get("wcs_missing", 0) or 0)
            wcs_unreadable = int(stats.get("wcs_unreadable", 0) or 0)
            sessions_total = int(stats.get("sessions_total", 0) or 0)
            sessions_success = int(stats.get("sessions_success", 0) or 0)

            present_pct = (100.0 * wcs_present / lights_total) if lights_total > 0 else 0.0
            logging.info(
                "Cible %s | sessions=%d/%d | lights=%d | WCS present=%d (%.1f%%), missing=%d, unreadable=%d",
                target_root.name,
                sessions_success,
                sessions_total,
                lights_total,
                wcs_present,
                present_pct,
                wcs_missing,
                wcs_unreadable,
            )
    
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

            for target_root, combined in parent_target_outputs.items():
                all_output_files.extend(combined)
                logging.info(f"Fichiers combinés ajoutés pour la cible {target_root}: {len(combined)} fichiers")

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
                output_dir=output_base_dir,
                work_dir=work_base_dir,
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
