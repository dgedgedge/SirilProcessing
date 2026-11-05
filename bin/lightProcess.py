#!/bin/env python3
"""
Script pour traiter automatiquement les images light avec option de mosaïque.

Ce script :
1. Analyse un ou plusieurs répertoires contenant des sous-répertoires 'light' et éventuellement 'flat'
2. Détecte automatiquement les caractéristiques des images light
3. Trouve le master dark correspondant dans la librairie
4. Effectue le prétraitement (soustraction du dark) et le stacking
5. Optionnellement, assemble les sessions en mosaïque
6. Avec --postprocess, traite les stacks finaux ou la mosaïque avec la même
   classe, les mêmes paramètres et les mêmes exports que postProcess.py.

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

from __future__ import annotations

import argparse
from contextlib import nullcontext
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

# Add the parent directory to the path to import the lib module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.config import Config
from lib.power_guard import PowerGuard
from lib.logging_utils import (
    add_session_file_logging,
    log_section,
    remove_session_file_logging,
    setup_logging,
)

if TYPE_CHECKING:
    from lib.mosaic import Mosaic


class HelpFormatter(
    argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter
):
    """Formatter d'aide qui conserve les sauts de ligne et affiche les valeurs par défaut."""


def calculate_common_basename(session_dirs: list[Path]) -> str:
    """
    Calcule le nom de base commun à partir d'une liste de répertoires de session.

    Args:
        session_dirs: Liste des répertoires de session

    Returns:
        Préfixe commun nettoyé ou mot commun d’au moins trois caractères.
        Une liste vide retourne une chaîne vide ; une session conserve son nom.

    Examples:
        >>> calculate_common_basename([Path("session_M31_nord"), Path("session_M31_sud")])
        'session_M31'
        >>> calculate_common_basename([Path("NGC7000_panel1"), Path("NGC7000_panel2")])
        'NGC7000_panel'
    """
    if not session_dirs:
        return ""

    # Extraire les noms de base (basename) de chaque répertoire
    basenames = [session_dir.name for session_dir in session_dirs]

    if len(basenames) == 1:
        return basenames[0]

    # Trouver le préfixe commun
    common_prefix = ""
    min_length = min(len(name) for name in basenames)

    for i in range(min_length):
        char = basenames[0][i]
        if all(name[i] == char for name in basenames):
            common_prefix += char
        else:
            break

    # Nettoyer le préfixe commun (enlever les caractères de fin non alphanumériques)
    common_prefix = common_prefix.rstrip("_-. ")

    # Retenir directement un préfixe suffisamment long
    if len(common_prefix) >= 3:
        return common_prefix

    # Si le préfixe est trop court, essayer une approche par mots
    words_sets = [
        set(name.replace("_", " ").replace("-", " ").split()) for name in basenames
    ]
    common_words = set.intersection(*words_sets) if words_sets else set()

    if common_words:
        # Prendre le mot le plus long
        longest_word = max(common_words, key=len)
        if len(longest_word) >= 3:
            return longest_word

    return common_prefix


def discover_session_roots(root_dir: Path) -> list[Path]:
    """Délègue à LightProcessor la découverte des sous-sessions d'une session.

    Ce point d'entrée est conservé pour les appelants historiques ; les règles
    de découverte appartiennent à la classe qui orchestre la session.
    """
    from lib.lightprocessor import LightProcessor

    return LightProcessor.discover_subsessions(root_dir)


def build_session_target_map(
    input_roots: list[Path],
) -> tuple[list[Path], dict[Path, Path]]:
    """Associe chaque sous-session à la session fournie en paramètre."""
    session_dirs: list[Path] = []
    session_to_target: dict[Path, Path] = {}

    for session_path in input_roots:
        discovered = discover_session_roots(session_path)
        if not discovered:
            continue

        for child in discovered:
            session_dirs.append(child)
            session_to_target[child] = session_path

    return session_dirs, session_to_target


def target_output_root(base_output_dir: Path, target_root: Path) -> Path:
    """Construit le dossier de sortie d’une cible sous la base configurée."""
    return base_output_dir / target_root.name


def session_output_root(
    base_output_dir: Path, target_root: Path, session_dir: Path
) -> Path:
    """Construit le dossier de sortie d’une session de la cible."""
    return (
        target_output_root(base_output_dir, target_root) / "sessions" / session_dir.name
    )


def session_work_root(
    base_work_dir: Path, target_root: Path, session_dir: Path
) -> Path:
    """Construit le dossier de travail d’une session sans modifier le disque."""
    return base_work_dir / target_root.name / "sessions" / session_dir.name


def stack_output_root(base_output_dir: Path, target_root: Path) -> Path:
    """Construit le dossier de sortie du résultat empilé de la cible."""
    return target_output_root(base_output_dir, target_root)


def target_work_root(base_work_dir: Path, target_root: Path) -> Path:
    """Construit le dossier de travail de la cible sous la base configurée."""
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


def main() -> int:
    """Traite les sessions, les stacks et les étapes finales demandées.

    Retourne 0 si les étapes requises réussissent, 1 sinon. --postprocess
    exécute le processeur commun sur les produits finaux après l’assemblage
    éventuel ; --dry-run annonce ces calculs sans les lancer.
    Une protection énergétique unique couvre tous les calculs et exports,
    sans activation en simulation, et est libérée même en cas d’exception.
    """
    config = Config.from_command_line()

    siril_pipeline_epilog = """
Flux de traitement attendu:
    1) Pour chaque sous-session, on trouve les lights et les flats disponibles.
    2) Si des flats existent et sont actifs (sans --no-flat), on les calibre avec un master dark pour creer
       un master flat de cette sous-session.
    3) On calibre ensuite les lights de cette sous-session avec le master dark et le master flat.
    4) On exporte la sequence calibree sous forme de FITS, sans stack intermediaire.
    5) Une fois toutes les sous-sessions traitees, on reconstruit la sequence finale sur l'ensemble
       des FITS calibres et on effectue un stack par session fournie, meme avec une seule sous-session.
    6) Avec --mosaic, on assemble uniquement ces stacks de sessions.
    7) Avec --postprocess, on traite chaque stack final, ou uniquement la mosaique
       si elle est demandee, avec les memes options que postProcess.py.

Exemple de pipeline Siril par sous-session:
    1) convert <sequence_name> -out=<work_dir>/process
    2) (si flats disponibles) creation d'un master flat:
        convert <flat_sequence_name> -out=<work_dir>/flat_process
        calibrate <flat_sequence_name> -dark=<master_dark_pour_flat> -cc=dark -cfa
        stack pp_<flat_sequence_name> median -norm=mul -out=<master_flat>
    3) calibrate <sequence_name> -dark=<master_dark> -flat=<master_flat> -cc=dark -cfa -equalize_cfa
       Ajouter -debayer en mode drizzle=off ; conserver le CFA en auto/force.
    4) copie Python de process/pp_<sequence_name>_*.fit(s) vers
       <output_dir>/<session>/sessions/<sous-session>/<sous-session>_<group>_calibrated

Stack final sur l'ensemble des sorties calibrees d'une session:
    1) convert <target>_ -out=<work_dir>/<target>/stacking/01_registration
    2) seqfindstar <target>_
    3) seqplatesolve <target>_ -force -nocache -disto=ps_distortion si active
    4) register <target>_ -2pass -transf=<align_transform>
    5) Analyse Drizzle et selection ; seqapplyreg avec -drizzle si pertinent
       (sinon dematricage CFA et alignement standard).
    6) stack r_r_<target>_ <method/rejection> ... -output_norm -out=<target>_combined
       Repertoires fixes dans stacking : 00_inputs, 01_registration, 02_quality,
       03_capability (ou SKIPPED.txt), 04_stacking. Un repertoire par script.
       Toutes les methodes utilisent seqapplyreg -filter-included -framing=min
       pour recadrer les poses selectionnees sur leur champ commun avant le stack.

Sorties:
    - Des FITS calibres par sous-session
    - Un FITS final empile par cible
    - Un JSON <target>_combined.drizzle.json contenant analyse, reglages et decision
    - Avec --postprocess : rapports, FITS et JPEG finaux comme postProcess

Traitement Siril de mosaique (si --mosaic):
    1) convert mosaic_ -out=<mosaic_output_dir>
    2) seqplatesolve mosaic_ -force -nocache -disto=ps_distortion
    3) seqapplyreg mosaic_ -framing=max
    4) stack r_mosaic_ rej 3 3 -norm=addscale -output_norm -rgb_equal -maximize -overlap_norm -feather=5 -out=<mosaic_name>_mosaic
"""

    parser = argparse.ArgumentParser(
        description="Traitement automatique des images light avec prétraitement et stacking",
        formatter_class=HelpFormatter,
        epilog=siril_pipeline_epilog,
    )

    from lib.lightprocessor import LightProcessor
    from lib.light_stacking import SessionStackingProcessor
    from lib.postprocess import PostProcessingProcessor
    from lib.siril import Siril

    config.register(LightProcessor, parser)
    config.register(Siril, parser)

    # Arguments pour la mosaïque
    parser.add_argument(
        "--mosaic",
        dest="create_mosaic",
        action="store_true",
        help="Créer une mosaïque après traitement de toutes les sessions",
    )

    from lib.mosaic import Mosaic

    config.register(Mosaic, parser)
    config.register(PostProcessingProcessor, parser)

    config.add_arguments(parser)

    parser.add_argument(
        "-l",
        "--log-level",
        dest="log_level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Niveau de journalisation. (Défaut: 'INFO')",
    )

    parser.add_argument(
        "-D",
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Simule le traitement sans l'exécuter réellement",
    )

    args = config.parse_args(parser)

    from lib.drizzle import validate_settings

    try:
        validate_settings(
            {
                key: getattr(args, key)
                for key in SessionStackingProcessor.CONFIG_DEFAULTS
                if key.startswith("drizzle")
            }
        )
    except ValueError as exc:
        parser.error(str(exc))

    if args.no_fwhm_reject:
        args.fwhm_reject_percent = 0.0
    config.capture_args(args)
    config.bind_parameters(args)
    mosaic_processor = Mosaic()

    # Configuration du logging
    setup_logging(args.log_level)
    logging.info(f"Log level set to {args.log_level}")
    logging.info(
        "Post-traitement final : %s",
        "activé" if args.enable_postprocess else "désactivé",
    )

    # Sauvegarde de la configuration si demandé
    if args.save_config:
        if args.enable_postprocess or args.autoghs_only or getattr(args, "color_only", False):
            try:
                PostProcessingProcessor.prepare_config_save(args)
            except (OSError, ValueError, RuntimeError) as error:
                logging.error("Préparation du post-traitement impossible: %s", error)
                return 1
        if not config.save_requested(args):
            return 1
        # La découverte de StarNet peut compléter les arguments sauvegardés.
        config.bind_parameters(args)

    with (nullcontext() if args.dry_run else PowerGuard()):
        return _process_sessions(args, config, mosaic_processor)


def _process_sessions(
    args: argparse.Namespace, config: Config, mosaic_processor: Mosaic,
) -> int:
    """Exécute le parcours CLI sous la protection détenue par main.

    Reçoit les paramètres déjà résolus et liés. Couvre les sous-sessions,
    stacks, mosaïque et post-traitements jusqu’aux exports et au bilan final.
    Retourne 0 si toutes les opérations requises réussissent, 1 sinon.
    Les exceptions non traitées remontent vers main et libèrent son contexte.
    """
    from lib.drizzle import cache_matches
    from lib.lightprocessor import LightProcessor
    from lib.light_stacking import SessionStackingProcessor
    from lib.postprocess import PostProcessingProcessor
    from lib.siril import Siril

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

        discovered = [] if (args.autoghs_only or getattr(args, "color_only", False)) else discover_session_roots(session_dir)
        if not discovered and not (args.autoghs_only or getattr(args, "color_only", False)):
            logging.error(
                f"Aucun répertoire 'light' ou 'Light' trouvé dans: {session_dir}"
            )
            logging.info(
                "Structure attendue: session_dir/light/ ou session_dir/Light/ (et optionnellement session_dir/flat/)"
            )
            return 1

        input_roots.append(session_dir)

    if args.create_mosaic:
        mosaic_name = args.mosaic_name or calculate_common_basename(input_roots)
        if not mosaic_name or (not args.mosaic_name and len(mosaic_name) < 3):
            logging.error("Nom automatique trop court ; fournir --mosaic-name")
            return 1
        if mosaic_name in (".", "..") or any(c in mosaic_name for c in '/\\\n\r\x00"'):
            logging.error("Nom de mosaïque incompatible avec un nom de répertoire")
            return 1
        work_base_dir = work_base_dir / mosaic_name
        output_base_dir = work_base_dir
        args.mosaic_name = mosaic_name
        args.mosaic_directory = work_base_dir
        args.work_dir = work_base_dir
        args.output_dir = output_base_dir
        logging.info("Répertoire commun de la mosaïque : %s", work_base_dir)

    if args.autoghs_only or getattr(args, "color_only", False):
        from lib.postprocess_resume import PostProcessResume

        try:
            images = PostProcessResume.final_images(
                output_base_dir, work_base_dir, input_roots,
                args.mosaic_name if args.create_mosaic else None,
            )
            # Vérifier toutes les reprises avant de modifier un seul résultat.
            for image in images:
                if getattr(args, "color_only", False):
                    PostProcessResume.load_color(image, PostProcessingProcessor.report_path_for(image))
                else:
                    PostProcessResume.load(image, PostProcessingProcessor.report_path_for(image))
            for image in images:
                logging.info("Reprise du rendu final uniquement : %s", image)
                if not args.dry_run:
                    treatment = PostProcessingProcessor()
                    treatment.post_process(image, treatment.report_path_for(image))
            return 0
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            logging.error("Reprise du rendu impossible : %s", error)
            return 1

    session_dirs, session_to_target = build_session_target_map(input_roots)
    if not session_dirs:
        logging.error("Aucune sous-session valide à traiter")
        return 1

    for root_dir in input_roots:
        discovered = discover_session_roots(root_dir)
        logging.info("Session %s : %d sous-session(s)", root_dir, len(discovered))

    logging.info(
        f"Validation réussie pour {len(session_dirs)} répertoires de sous-session"
    )

    if args.purge_target:
        unique_targets = sorted(
            {session_to_target[session] for session in session_dirs},
            key=lambda p: str(p),
        )
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

    if args.create_mosaic:
        logging.info(
            "Mosaïque demandée à partir des stacks des %d sessions", len(input_roots)
        )

    # Configuration globale de Siril
    try:
        Siril.configure_defaults(siril_path=args.siril_path, siril_mode=args.siril_mode)
        logging.info(
            f"Configuration Siril validée: path={args.siril_path}, mode={args.siril_mode}"
        )
    except ValueError as e:
        logging.error(f"Erreur de configuration Siril: {e}")
        logging.error(
            "Vérifiez que Siril est installé et accessible avec les paramètres spécifiés"
        )
        return 1

    # Configuration des paramètres de stacking
    stack_params = SessionStackingProcessor.stack_parameters(args)

    if args.no_dark:
        logging.warning("Mode sans dark activé: la soustraction de dark sera ignorée")

    # LightProcessor traite chaque session fournie, ses sous-sessions et leur stack.
    total_sessions = len(session_dirs)
    successful_sessions = 0
    failed_sessions = []
    session_stack_outputs = {}
    target_wcs_totals = {}

    for root_dir in input_roots:
        result = LightProcessor.process_target(
            session_dir=root_dir,
            output_base_dir=output_base_dir,
            work_base_dir=work_base_dir,
            args=args,
            stack_params=stack_params,
        )
        successful_sessions += result.successful_subsessions
        failed_sessions.extend(result.failed_subsessions)
        stats = result.wcs_stats
        target_wcs_totals[root_dir] = {
            **stats,
            "sessions_total": result.total_subsessions,
            "sessions_success": result.successful_subsessions,
        }
        if result.output_image is not None:
            session_stack_outputs[root_dir] = result.output_image

    # Résumé final
    logging.info(f"{'=' * 60}")
    logging.info("RÉSUMÉ DU TRAITEMENT")
    logging.info(f"{'=' * 60}")
    logging.info(
        f"Sous-sessions calibrées avec succès: {successful_sessions}/{total_sessions}"
    )

    if target_wcs_totals:
        logging.info("RÉSUMÉ WCS PAR SESSION")
        for target_root in sorted(
            target_wcs_totals.keys(), key=lambda p: p.name.lower()
        ):
            stats = target_wcs_totals[target_root]
            lights_total = int(stats.get("lights_total", 0) or 0)
            wcs_present = int(stats.get("wcs_present", 0) or 0)
            wcs_missing = int(stats.get("wcs_missing", 0) or 0)
            wcs_unreadable = int(stats.get("wcs_unreadable", 0) or 0)
            sessions_total = int(stats.get("sessions_total", 0) or 0)
            sessions_success = int(stats.get("sessions_success", 0) or 0)

            present_pct = (
                (100.0 * wcs_present / lights_total) if lights_total > 0 else 0.0
            )
            logging.info(
                "Session %s | sous-sessions=%d/%d | lights=%d | WCS present=%d (%.1f%%), missing=%d, unreadable=%d",
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
        logging.error(f"Sous-sessions échouées ({len(failed_sessions)}):")
        for failed_session in failed_sessions:
            logging.error(f"  - {failed_session}")

    logging.info(
        "Sessions empilées avec succès: %d/%d",
        len(session_stack_outputs),
        len(input_roots),
    )
    mosaic_success = not args.create_mosaic
    mosaic_result = None
    # La mosaïque reçoit exclusivement un résultat de stacking par session.
    if args.create_mosaic and args.dry_run and len(input_roots) >= 2:
        logging.info(
            "[DRY-RUN] Mosaïque prévue à partir des stacks des %d sessions",
            len(input_roots),
        )
        mosaic_success = True
    elif args.create_mosaic and len(session_stack_outputs) >= 2:
        logging.info(f"{'=' * 60}")
        logging.info("CRÉATION DE LA MOSAÏQUE")
        logging.info(f"{'=' * 60}")

        try:
            successful_session_dirs = list(session_stack_outputs)
            all_output_files = list(session_stack_outputs.values())

            logging.info(
                f"Total des fichiers pour la mosaïque: {len(all_output_files)}"
            )
            for file in all_output_files:
                logging.info(f"  - {file}")

            mosaic_name = args.mosaic_name

            # Same processor contract as the other treatments: parsed options,
            # current image, complementary panels, and a separate JSON report.
            mosaic_args = argparse.Namespace(**vars(args))
            mosaic_args.mosaic_name = mosaic_name
            mosaic_args.mosaic_inputs = all_output_files[1:]
            mosaic_args.output_dir = output_base_dir
            mosaic_args.work_dir = work_base_dir
            mosaic_report = work_base_dir / f"{mosaic_name}_mosaic.json"
            mosaic_processor.set_from_args(mosaic_args)
            result = mosaic_processor.post_process(
                input_path=all_output_files[0],
                output_path=mosaic_report,
            )
            if result.get("error"):
                raise RuntimeError(result["error"])
            mosaic_result = result.get("output_image")
            if not mosaic_result or not Path(mosaic_result).is_file():
                raise RuntimeError(
                    "La mosaïque ne fournit pas de fichier output_image valide"
                )
            mosaic_success = True
            logging.info("🌟 Mosaïque créée avec succès: %s", mosaic_result)
            logging.info("Rapport de mosaïque: %s", mosaic_report)

        except Exception as e:
            logging.error(f"Erreur lors de la création de la mosaïque: {e}")
            if args.log_level == "DEBUG":
                import traceback

                traceback.print_exc()

    elif args.create_mosaic:
        logging.warning(
            "Mosaïque demandée mais moins de 2 résultats de stacking disponibles"
        )

    postprocess_success = True
    if args.enable_postprocess:
        if args.dry_run:
            logging.info(
                "[DRY-RUN] Post-traitement prévu sur %s",
                "la mosaïque finale" if args.create_mosaic else "chaque stack final",
            )
        else:
            # L’assemblage utilise les panneaux linéaires. Le rendu s’applique
            # seulement au produit final, après la mosaïque si elle est demandée.
            if args.create_mosaic:
                final_images = (
                    [Path(mosaic_result)] if mosaic_success and mosaic_result else []
                )
            else:
                final_images = list(session_stack_outputs.values())
            postprocess_success = bool(final_images)
            for image in final_images:
                try:
                    treatment = PostProcessingProcessor()
                    report_path = treatment.report_path_for(image)
                    result = treatment.post_process(image, report_path)
                    output_image = result.get("output_image")
                    if (
                        result.get("error") or not output_image
                        or not Path(str(output_image)).is_file()
                    ):
                        raise RuntimeError(
                            "Le post-traitement ne fournit pas de sortie valide"
                        )
                    logging.info("Post-traitement terminé: %s", output_image)
                except Exception as error:
                    postprocess_success = False
                    logging.error(
                        "Échec du post-traitement de %s: %s", image, error,
                        exc_info=args.log_level == "DEBUG",
                    )

    if (args.enable_postprocess and not args.dry_run and postprocess_success
            and successful_sessions == total_sessions and mosaic_success
            and len(session_stack_outputs) == len(input_roots)):
        from lib.postprocess_resume import PostProcessResume

        PostProcessResume.save_final_images(
            output_base_dir, input_roots, args.create_mosaic, final_images,
        )

    # Retour final
    if (
        successful_sessions == total_sessions
        and (args.dry_run or len(session_stack_outputs) == len(input_roots))
        and mosaic_success
        and postprocess_success
    ):
        if args.dry_run:
            logging.info("Simulation terminée avec succès")
        elif args.create_mosaic:
            logging.info(
                "🎉 Toutes les sessions traitées et mosaïque créée avec succès"
            )
        else:
            logging.info("🎉 Toutes les sessions ont été traitées avec succès")
        return 0
    elif successful_sessions > 0:
        logging.warning(
            f"⚠️  Traitement incomplet: {successful_sessions}/{total_sessions} sous-sessions calibrées, {len(session_stack_outputs)}/{len(input_roots)} sessions empilées"
        )
        return 1
    else:
        logging.error("💥 Aucune session n'a pu être traitée")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n⚠️  Traitement interrompu par l'utilisateur.")
        print(
            "   Les fichiers temporaires peuvent être conservés dans le répertoire de travail."
        )
        sys.exit(1)
    except Exception as e:
        logging.error(f"Erreur inattendue: {e}")
        sys.exit(1)
