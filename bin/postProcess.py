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

Le script utilise _PostProcessorSequence pour exécuter les traitements.
Le rendu final AutoGHS avec séparation StarNet est activé par défaut
(--disable-autoghs le désactive) et produit des FITS et JPEG.
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
from lib.quality_preview import QualityMaskPreview
from lib.power_guard import PowerGuard
from lib.siril_utils import Siril
from lib.star_separation import StarNetSeparator


def _effective_backend_name(args: argparse.Namespace) -> str:
    """Nom du mode réellement appliqué aux étapes de restauration."""
    return "cosmic-clarity" if getattr(args, "enable_clarity", True) else "siril"


def _build_postprocess_layout(
    input_file: Path, output_arg: str | None,
) -> tuple[Path, Path, Path, str]:
    """Construit les chemins de rapport, dossier d'étapes et résultat final.

    Les étapes intermédiaires sont rangées dans un dossier nommé avec l'image
    traitée suivie de _postprocess, indépendamment des moteurs utilisés.
    Le rapport JSON global et le FITS final sont écrits
    au même niveau, avec le même nom de base.
    """
    run_name = f"{input_file.stem}_postprocess"
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


def main() -> int:
    """Exécute les étapes, diagnostics et export sous protection énergétique facultative.

    Les services D-Bus indisponibles ne bloquent pas le calcul ; leurs demandes
    sont libérées avant de fermer le journal, y compris après une interruption.
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

    # Créer le processeur séquentiel


    # Ajouter les arguments spécifiques
    config.register(_PostProcessorSequence, parser)

    args = config.parse_args(parser)
    setup_logging(args.log_level)

    input_file = Path(args.input_file).resolve()
    backend = _effective_backend_name(args)
    output_path, steps_dir, result_path, run_name = _build_postprocess_layout(
        input_file, args.output_file,
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
        logging.info("Politique de calcul commune : %s", args.compute_backend)
        if getattr(args, "restoration_compare_chains", True):
            logging.info(
                "Restauration : comparaison des chaînes complètes ; "
                "référence commune après gradient/photométrie ; ordre inverse=%s",
                getattr(args, "restoration_reverse_order", False),
            )
        else:
            logging.info(
                "Restauration : comparaison par étapes isolées ; "
                "activer --restoration-compare-chains pour comparer les chaînes complètes"
            )
        if args.save_config:
            if getattr(args, "enable_autoghs", False):
                args.starnet_path = StarNetSeparator.resolve_executable(args)
            if not config.save_requested(args):
                return 1
        logging.info("Analyse de: %s", input_file)
        logging.info("Rapport global: %s", output_path)
        logging.info("Dossier des étapes: %s", steps_dir)
        logging.info("Résultat final: %s", result_path)
        # Exécuter la séquence de post-processing
        with PowerGuard():
            config.bind_parameters(args)
            seq_processor = _PostProcessorSequence()
            results = seq_processor.post_process(
                input_path=input_file,
                output_path=output_path,
            )
            quality_preview = QualityMaskPreview.ensure_for_run(
                results, input_file, steps_dir, args
            )
            gradient_preview = QualityMaskPreview.write_gradient_points(results, steps_dir)
            if gradient_preview:
                import json

                output_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
            final_image = _resolve_final_image(results, input_file)
            if not final_image.is_file():
                raise FileNotFoundError(f"Image finale introuvable: {final_image}")
            result_path.parent.mkdir(parents=True, exist_ok=True)
            if final_image != result_path.resolve():
                copyfile(final_image, result_path)
            logging.info("Image finale exportée: %s", result_path)
            jpeg = results.get("autoghs", {}).get("output_jpeg")
            if jpeg:
                jpeg_path = Path(jpeg).resolve()
                jpeg_export = result_path.with_suffix(".jpg")
                if jpeg_path != jpeg_export.resolve():
                    copyfile(jpeg_path, jpeg_export)
                logging.info("Image finale JPEG exportée: %s", jpeg_export)

        logging.info(f"\n{'=' * 60}")
        logging.info("RÉSULTATS")
        logging.info("Image des zones de qualité : %s", quality_preview)
        if gradient_preview:
            logging.info("Points du gradient original : %s", gradient_preview)
        logging.info(f"{'=' * 60}")

        for processor_name in results:
            logging.info("Traitement terminé: %s", processor_name)
            summary_path = results[processor_name].get("summary_path")
            if summary_path:
                logging.info("Synthèse des essais %s : %s", processor_name, summary_path)

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
