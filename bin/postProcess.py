#!/usr/bin/env python3
"""
Script de post-traitement extensible pour une image FITS unique.

Ce script prend UNE SEULE image FITS calibrée en entrée et exécute les
traitements activés par défaut : analyse du gradient, résolution
astrométrique suivie de l'étalonnage photométrique des couleurs, puis
déconvolution avec audit de PSF et comparaison stellaire avant/après,
puis réduction du bruit contrôlée. Le mode comparatif Cosmic Clarity + Siril
est activé par défaut pour ces deux dernières étapes et peut être désactivé
avec --disable-clarity.

Le script utilise _PostProcessorSequence pour exécuter les traitements.
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
from shutil import copyfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.config import Config
from lib.logging_utils import (
    add_session_file_logging,
    remove_session_file_logging,
    setup_logging,
)
from lib.postprocess import _PostProcessorSequence
from lib.siril_utils import Siril


def _sanitize_backend_name(name: str) -> str:
    """Retourne un identifiant de backend sûr pour les noms de chemins."""
    return "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in name)


def _effective_backend_name(args: argparse.Namespace) -> str:
    """Nom du mode réellement appliqué aux étapes de restauration."""
    return "cosmic-clarity" if getattr(args, "enable_clarity", True) else "siril"


def _build_postprocess_layout(
    input_file: Path, output_arg: str | None, backend: str,
) -> tuple[Path, Path, Path, str]:
    """Construit les chemins de rapport, dossier d'étapes et résultat final.

    Les étapes intermédiaires sont rangées dans un dossier nommé avec l'image
    traitée et le backend. Le rapport JSON global et le FITS final sont écrits
    au même niveau, avec le même nom de base.
    """
    backend_name = _sanitize_backend_name(backend)
    run_name = f"{input_file.stem}_postprocess_{backend_name}"
    if output_arg:
        provided = Path(output_arg)
        base_dir = provided if provided.suffix == "" else provided.parent
    else:
        base_dir = input_file.parent
    base_dir = base_dir.resolve()
    report_path = base_dir / f"{run_name}.json"
    steps_dir = base_dir / run_name
    result_path = base_dir / f"{run_name}.fits"
    return report_path, steps_dir, result_path, run_name


def _resolve_final_image(results: dict, fallback: Path) -> Path:
    """Récupère l'image finale produite par la dernière étape active."""
    final_image = fallback
    for report in results.values():
        output_image = report.get("output_image") if isinstance(report, dict) else None
        if output_image:
            final_image = Path(output_image)
    return final_image.resolve()


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

    # Créer le processeur séquentiel
    seq_processor = _PostProcessorSequence()

    # Ajouter les arguments spécifiques
    config.register(seq_processor, parser)

    args = config.parse_args(parser)
    setup_logging(args.log_level)

    input_file = Path(args.input_file).resolve()
    backend = _effective_backend_name(args)
    output_path, steps_dir, result_path, run_name = _build_postprocess_layout(
        input_file, args.output_file, backend,
    )
    args.postprocess_steps_dir = steps_dir
    log_file = steps_dir / f"00_{input_file.stem}_postProcess.log"
    log_handler = None
    try:
        if log_file.resolve() == input_file.resolve():
            raise ValueError("Le fichier de log ne peut pas remplacer l'image d'entrée")
        if result_path.resolve() == input_file.resolve():
            raise ValueError("Le fichier de sortie final ne peut pas remplacer l'image d'entrée")
        log_handler = add_session_file_logging(log_file, args.log_level)
        logging.info("Log de post-traitement: %s", log_file)
        logging.info("Log level set to %s", args.log_level)
        if not input_file.is_file():
            logging.error("Fichier introuvable: %s", input_file)
            return 1
        logging.info("Configuration: %s", config.config_file)
        logging.info("Siril: mode=%s, path=%s", args.siril_mode, args.siril_path)
        logging.info("Backend: %s", backend)
        if args.save_config:
            if not config.save_requested(args):
                return 1
        logging.info("Analyse de: %s", input_file)
        logging.info("Rapport global: %s", output_path)
        logging.info("Dossier des étapes: %s", steps_dir)
        logging.info("Résultat final: %s", result_path)
        # Exécuter la séquence de post-processing
        seq_processor.set_from_args(args)
        results = seq_processor.post_process(
            input_path=input_file,
            output_path=output_path,
        )
        final_image = _resolve_final_image(results, input_file)
        if not final_image.is_file():
            raise FileNotFoundError(f"Image finale introuvable: {final_image}")
        result_path.parent.mkdir(parents=True, exist_ok=True)
        if final_image != result_path.resolve():
            copyfile(final_image, result_path)
        logging.info("Image finale exportée: %s", result_path)

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
