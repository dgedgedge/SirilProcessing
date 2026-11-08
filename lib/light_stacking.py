"""Empilement des images light calibrées d'une session."""

from __future__ import annotations


import argparse
import logging
import shutil
from pathlib import Path

import numpy as np
from astropy.io import fits

from lib.logging_utils import log_section
from lib.processor import processor
from lib.siril import Siril, SirilSequence
from lib.type_defs import ConfigValue, JSONReport
from lib.siril_api import SirilCommand, command


class SessionStackingProcessor(processor):
    """Orchestre l'alignement, la sélection et l'empilement d'une session."""

    parameter_persistence = {
        "force_stacking": False,
        "no_fwhm_reject": False,
    }
    CONFIG_DEFAULTS = {
        "rejection_method": "winsorizedsigma",
        "rejection_param1": 3.0,
        "rejection_param2": 3.0,
        "roundness_filter": "1.8k",
        "nbstars_filter": "1.8k",
        "roundness_weighted": True,
        "roundness_weight_max_extra": 1,
        "fwhm_filter": "1.8k",
        "max_fwhm": 0.0,
        "stellar_profile_filter": True,
        "stellar_profile_sigma": 3.0,
        "fwhm_reject_percent": 0.0,
        "fwhm_weighted": True,
        "fwhm_weight_max_extra": 1,
        "align_transform": "affine",
        "enable_stack_platesolve": True,
        "force_stacking": False,
        "stack_method": "average",
        "drizzle": "auto",
        "drizzle_scale": "auto",
        "drizzle_pixfrac": "auto",
        "drizzle_kernel": "auto",
        "drizzle_min_frames": 30,
        "drizzle_min_coverage": 0.75,
        "drizzle_fwhm_limit": 2.5,
        "drizzle_max_drift": 10.0,
    }

    def __init__(self, siril: Siril | None = None) -> None:
        """Configure le service Siril utilisé pour exécuter les scripts de stack."""
        self.siril = siril or Siril.create_with_defaults()

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare les options de sélection, de Drizzle et d'empilement."""
        config = cls.CONFIG_DEFAULTS
        parser.add_argument(
            "--drizzle", choices=["off", "auto", "force"], default=config["drizzle"]
        )
        for option in ("scale", "pixfrac"):
            parser.add_argument(
                f"--drizzle-{option}",
                default=config[f"drizzle_{option}"],
                help="Valeur numérique ou auto",
            )
        parser.add_argument(
            "--drizzle-kernel",
            choices=["auto", "square", "gaussian", "turbo", "point"],
            default=config["drizzle_kernel"],
        )
        for option, kind in (
            ("min_frames", int),
            ("min_coverage", float),
            ("fwhm_limit", float),
            ("max_drift", float),
        ):
            parser.add_argument(
                "--drizzle-" + option.replace("_", "-"),
                type=kind,
                default=config[f"drizzle_{option}"],
            )

        parser.add_argument(
            "--stack-method",
            dest="stack_method",
            choices=["average", "median", "sum"],
            default=config["stack_method"],
            help="Méthode de stacking",
        )
        parser.add_argument(
            "-r",
            "--rejection-method",
            dest="rejection_method",
            choices=[
                "none",
                "sigma",
                "linear",
                "winsor",
                "winsorizedsigma",
                "percentile",
            ],
            default=config["rejection_method"],
            help=(
                "Méthode de rejet pour Siril. "
                f"(Défaut: '{config['rejection_method']}')"
            ),
        )
        parser.add_argument(
            "--rejection-param1",
            dest="rejection_param1",
            type=float,
            default=config["rejection_param1"],
            help=f"Premier paramètre de rejet pour Siril. (Défaut: {config['rejection_param1']})",
        )
        parser.add_argument(
            "--rejection-param2",
            dest="rejection_param2",
            type=float,
            default=config["rejection_param2"],
            help=f"Second paramètre de rejet pour Siril. (Défaut: {config['rejection_param2']})",
        )
        parser.add_argument(
            "--roundness-filter",
            type=str,
            default=config["roundness_filter"],
            help=(
                "Filtre de rondeur des étoiles appliqué aux mesures natives avant "
                "analyse et application du Drizzle (ex: '2k'). Utiliser 'none' "
                f"pour désactiver. (Défaut: '{config['roundness_filter']}')"
            ),
        )
        parser.add_argument(
            "--fwhm-filter",
            type=str,
            default=config["fwhm_filter"],
            help=(
                "Filtre par seuil sur la FWHM pondérée Siril, après alignement "
                "(ex: '1.8k'). Utiliser 'none' pour désactiver. "
                f"(Défaut: '{config['fwhm_filter']}')"
            ),
        )
        parser.add_argument(
            "--max-fwhm",
            type=float,
            default=config["max_fwhm"],
            help=(
                "Plafond de FWHM non pondérée Siril en pixels natifs, avant les "
                "filtres statistiques. Indépendant du nombre d'étoiles ; "
                "0 désactive (défaut)."
            ),
        )
        parser.add_argument(
            "--stellar-profile-filter",
            dest="stellar_profile_filter",
            action="store_true",
            default=config["stellar_profile_filter"],
            help=(
                "Filtre des profils stellaires : étalement R80 et allongement "
                "cohérent, sur toutes les poses encore retenues "
                "(actif par défaut)."
            ),
        )
        parser.add_argument(
            "--no-stellar-profile-filter",
            dest="stellar_profile_filter",
            action="store_false",
            help="Désactive la mesure et le filtrage des profils stellaires.",
        )
        parser.add_argument(
            "--stellar-profile-sigma",
            type=float,
            default=config["stellar_profile_sigma"],
            help="Coefficient de dispersion robuste pour les profils stellaires (défaut : 3).",
        )
        parser.add_argument(
            "--fwhm-reject-percent",
            type=float,
            default=config["fwhm_reject_percent"],
            help=(
                "Rejet proportionnel FWHM après alignement (0 à 95), cumulable "
                "avec --fwhm-filter et appliqué sur ses survivantes. Défaut : 0%%."
            ),
        )
        parser.add_argument(
            "--no-fwhm-reject",
            action="store_true",
            default=False,
            help="Désactive totalement le rejet proportionnel FWHM (force à 0%%).",
        )
        parser.add_argument(
            "--nbstars-filter",
            default=config["nbstars_filter"],
            help=(
                "Tolérance bilatérale autour du nombre médian d'étoiles : écart "
                "absolu (30), relatif (20%%), ou MAD (1.8k par défaut) ; none désactive"
            ),
        )
        parser.add_argument(
            "--no-roundness-weighted",
            dest="roundness_weighted",
            action="store_false",
            default=config["roundness_weighted"],
            help="Désactive la pondération de rondeur avant Drizzle",
        )
        parser.add_argument(
            "--roundness-weight-max-extra",
            type=int,
            choices=range(0, 9),
            default=config["roundness_weight_max_extra"],
            help="Répétitions supplémentaires maximales selon la rondeur",
        )
        parser.add_argument(
            "--no-fwhm-weighted",
            dest="fwhm_weighted",
            action="store_false",
            default=config["fwhm_weighted"],
            help="Désactive la pondération FWHM des meilleures images (safe actif par défaut).",
        )
        parser.add_argument(
            "--fwhm-weight-max-extra",
            type=int,
            default=config["fwhm_weight_max_extra"],
            help=(
                "Nombre max de répétitions supplémentaires pour les meilleures "
                "images quand la pondération FWHM est active. Défaut safe: 1."
            ),
        )
        parser.add_argument(
            "--align-transform",
            choices=["shift", "similarity", "affine", "homography"],
            default=config["align_transform"],
            help=(
                "Transformation d'alignement pour register au stack final. "
                f"(Défaut: '{config['align_transform']}')"
            ),
        )
        parser.add_argument(
            "--stack-platesolve",
            dest="enable_stack_platesolve",
            action="store_true",
            default=config["enable_stack_platesolve"],
            help=(
                "Active seqplatesolve avant register dans le stack final de "
                "chaque session (utile en cas d'alignement difficile). Défaut: activé."
            ),
        )
        parser.add_argument(
            "--force-stacking",
            action="store_true",
            default=config["force_stacking"],
            help=(
                "Force uniquement la reconstruction du stack final de chaque "
                "session (supprime les artefacts de stack existants sans forcer "
                "la recalibration)"
            ),
        )

    @classmethod
    def stack_parameters(cls, args: argparse.Namespace) -> dict[str, ConfigValue]:
        """Construit les réglages de traitement depuis les options résolues du CLI."""
        parameters: dict[str, ConfigValue] = {
            key: getattr(args, key)
            for key in cls.CONFIG_DEFAULTS
            if key.startswith("drizzle")
        }
        parameters.update(
            {
                "method": args.stack_method,
                "rejection": args.rejection_method,
                "rejection_low": args.rejection_param1,
                "rejection_high": args.rejection_param2,
                "roundness_filter": args.roundness_filter,
                "nbstars_filter": args.nbstars_filter,
                "roundness_weighted": args.roundness_weighted,
                "roundness_weight_max_extra": args.roundness_weight_max_extra,
                "fwhm_filter": args.fwhm_filter,
                "max_fwhm": args.max_fwhm,
                "stellar_profile_filter": args.stellar_profile_filter,
                "stellar_profile_sigma": args.stellar_profile_sigma,
                "fwhm_reject_percent": args.fwhm_reject_percent,
                "fwhm_weighted": args.fwhm_weighted,
                "fwhm_weight_max_extra": args.fwhm_weight_max_extra,
                "align_transform": args.align_transform,
                "enable_stack_platesolve": args.enable_stack_platesolve,
                "force_stacking": args.force_stacking,
            }
        )
        return parameters

    @classmethod
    def parameter_children(cls) -> tuple[type[processor], ...]:
        """Déclare qu'un stack de session n'a pas de traitement enfant."""
        return ()

    def post_process(
        self,
        input_path: Path,
        output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Empile les FITS calibrés et retourne la sortie et les statistiques.

        Parameters
        ----------
        input_path : Path
            Première image calibrée, utilisée comme entrée descriptive du rapport.
        output_path : Path
            Chemin attendu du FITS combiné ; son répertoire est le répertoire cible.
        args : argparse.Namespace | None
            Doit fournir ``output_files``, ``work_dir``, ``target_name``,
            ``stack_params``, ``force_stacking`` et le dictionnaire mutable
            ``stack_report``.

        Returns
        -------
        JSONReport
            État de l'étape, chemin du stack créé ou réutilisé et statistiques.

        Raises
        ------
        ValueError
            Si le contexte d'exécution ne contient pas les paramètres requis.
        """
        options = self._get_args(args)
        required = (
            "output_files",
            "work_dir",
            "target_name",
            "stack_params",
            "force_stacking",
            "stack_report",
        )
        missing = [name for name in required if not hasattr(options, name)]
        if missing:
            raise ValueError(
                "Paramètres d'exécution du stacking manquants: "
                + ", ".join(missing)
            )
        if not isinstance(options.output_files, list):
            raise ValueError("output_files doit être une liste de chemins")
        if not isinstance(options.stack_params, dict):
            raise ValueError("stack_params doit être un dictionnaire")
        if not isinstance(options.stack_report, dict):
            raise ValueError("stack_report doit être un dictionnaire")

        output = self.stack_outputs(
            [Path(path) for path in options.output_files],
            Path(output_path).parent,
            Path(options.work_dir),
            str(options.target_name),
            options.stack_params,
            bool(options.force_stacking),
            options.stack_report,
            stack_work_dir=getattr(options, "stack_work_dir", None),
        )
        return {
            "status": "ok" if output is not None else "failed",
            "input_image": str(input_path),
            "output_image": str(output) if output is not None else None,
        }

    def stack_outputs(
        self,
        output_files: list[Path],
        output_dir: Path,
        work_dir: Path,
        target_name: str,
        stack_params: dict[str, ConfigValue] | None = None,
        force_stacking: bool = False,
        stack_report: dict[str, object] | None = None,
        stack_work_dir: Path | None = None,
    ) -> Path | None:
        """Empile les images calibrées dans ``<target>_combined.fits``.

        Les entrées incompatibles en structure FITS sont exclues avant les
        traitements Siril. Une entrée unique est renvoyée sans nouvel
        empilement. Les étapes de préparation et d'empilement consignent leurs
        bandeaux de progression dans les journaux. La séquence est écrite
        directement à côté des liens FITS, sans conversion ni modification
        des sources ; le nombre de couches vient du premier FITS compatible.
        Le générateur commun Siril fixe l’extension à fits pour que la résolution astrométrique
        remplace les liens locaux sans créer un doublon .fit ambigu.
        ``stack_work_dir`` fournit
        une racine explicite pour les étapes 02 à 05 ; sinon le répertoire
        historique <work_dir>/<target_name>/stacking est utilisé.
        """
        valid_files = [
            Path(file).absolute() for file in output_files if Path(file).exists()
        ]
        if not valid_files:
            return None
        if len(valid_files) == 1:
            return valid_files[0]

        log_section("Stacking — Préparation des images calibrées")
        resolved_targets = set()
        for file_path in valid_files:
            try:
                resolved_targets.add(file_path.resolve(strict=True))
            except Exception:
                continue

        if len(resolved_targets) < len(valid_files):
            logging.warning(
                "Attention: %d chemins calibrés pointent vers %d fichiers réels seulement. "
                "Des sorties calibrées en liens symboliques peuvent avoir été écrasées; "
                "relancer une recalibration complète pour fiabiliser le stack.",
                len(valid_files),
                len(resolved_targets),
            )

        target_name = str(target_name).strip() or "combined_session"
        target_name = "".join(
            ch if ch.isalnum() or ch in "_-" else "_" for ch in target_name
        )

        session_stack_dir = (Path(stack_work_dir) if stack_work_dir is not None
                             else Path(work_dir) / target_name / "stacking")
        if force_stacking and stack_work_dir is None and session_stack_dir.exists():
            shutil.rmtree(session_stack_dir)

        output_path = Path(output_dir) / f"{target_name}_combined"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if force_stacking:
            for candidate in (
                output_path.with_suffix(".fit"),
                output_path.with_suffix(".fits"),
            ):
                if candidate.exists():
                    candidate.unlink()

        # Les anciens input/output appartiennent uniquement à l’API historique.
        if stack_work_dir is None:
            for transient_dir in (session_stack_dir / "input", session_stack_dir / "output"):
                if transient_dir.exists():
                    shutil.rmtree(transient_dir)

        stack_cfg = dict(stack_params or {})
        stack_cfg.setdefault("fwhm_filter", "1.8k")
        stack_cfg.setdefault("roundness_filter", "1.8k")
        from lib.drizzle import STACK_STAGES, reset_stage, run_stack

        run_dir = session_stack_dir
        for stage in STACK_STAGES:
            reset_stage(run_dir / stage)
        output_stack_dir = run_dir / "02_registration"

        local_report: dict[str, object] = {
            "target_name": target_name,
            "total_input": len(valid_files),
            "rejected_incompatible_layout": 0,
            "rejected_unreadable_layout": 0,
            "rejected_registration_quality": 0,
            "kept_unique_for_stack": len(valid_files),
            "kept_effective_for_stack": len(valid_files),
            "weighted_extra_entries": 0,
        }

        filtered_files, layout_stats = self._filter_compatible_stack_inputs(
            valid_files
        )
        local_report["rejected_incompatible_layout"] = layout_stats.get(
            "rejected_incompatible_layout", 0
        )
        local_report["rejected_unreadable_layout"] = layout_stats.get(
            "rejected_unreadable_layout", 0
        )

        if (
            local_report["rejected_incompatible_layout"] > 0
            or local_report["rejected_unreadable_layout"] > 0
        ):
            logging.warning(
                "Nettoyage des entrées de stack: %d rejetée(s) "
                "(layout incompatible), %d rejetée(s) (layout illisible).",
                local_report["rejected_incompatible_layout"],
                local_report["rejected_unreadable_layout"],
            )

        valid_files = filtered_files
        local_report["kept_unique_for_stack"] = len(valid_files)
        local_report["kept_effective_for_stack"] = len(valid_files)

        if not valid_files:
            logging.error("Aucune image compatible restante pour le stack final")
            if stack_report is not None:
                stack_report.clear()
                stack_report.update(local_report)
            return None

        if len(valid_files) == 1:
            if stack_report is not None:
                stack_report.clear()
                stack_report.update(local_report)
            return valid_files[0]

        files_for_stack = valid_files
        if stack_report is not None:
            stack_report.clear()
            stack_report.update(local_report)

        sequence_prefix = f"{target_name}_"
        sequence_files: list[Path] = []
        for index, source_file in enumerate(files_for_stack, start=1):
            destination = output_stack_dir / f"{sequence_prefix}{index:03d}.fits"
            if destination.exists():
                destination.unlink()
            try:
                destination.symlink_to(source_file)
            except OSError:
                shutil.copy2(source_file, destination)
            sequence_files.append(destination)

        layers = int(fits.getheader(files_for_stack[0]).get("NAXIS3", 1))
        SirilSequence.from_files(
            output_stack_dir / f"{sequence_prefix}.seq",
            sequence_prefix, sequence_files, layers=layers,
        ).write()

        method = str(stack_cfg.get("method", "average")).lower()
        rejection = str(stack_cfg.get("rejection", "sigma")).lower()
        rejection_low = stack_cfg.get("rejection_low", 3.0)
        rejection_high = stack_cfg.get("rejection_high", 3.0)
        align_transform = str(stack_cfg.get("align_transform", "affine")).lower()
        enable_stack_platesolve = bool(
            stack_cfg.get("enable_stack_platesolve", True)
        )
        framing = "min"
        stack_sequence_prefix = f"r_{sequence_prefix}"

        if method in {'median', 'med'}:
            stack_line = command('stack', stack_sequence_prefix, 'median', '-output_norm', f'-out={output_path}.fits')
        elif rejection == 'none':
            stack_line = command('stack', stack_sequence_prefix, 'mean', 'none', '-output_norm', f'-out={output_path}.fits')
        else:
            stack_line = command('stack', stack_sequence_prefix, 'rej', rejection_low, rejection_high,
                                 '-output_norm', f'-out={output_path}.fits')

        platesolve_lines = ""
        if enable_stack_platesolve:
            platesolve_lines = (
                f"seqplatesolve {sequence_prefix} -force -nocache "
                "-disto=ps_distortion\n"
            )

        prepare = [command('cd', output_stack_dir), command('seqfindstar', sequence_prefix)]
        if platesolve_lines:
            prepare.append(SirilCommand.from_text(platesolve_lines.strip()))
        prepare.append(command('register', sequence_prefix, '-2pass', f'-transf={align_transform}'))


        success = run_stack(
            self.siril,
            files_for_stack,
            stack_cfg,
            output_stack_dir,
            run_dir,
            sequence_prefix,
            output_path,
            prepare,
            stack_line,
            framing,
            stack_report=stack_report,
        )
        if not success:
            return None

        for candidate in (
            output_path.with_suffix(".fits"),
            output_path.with_suffix(".fit"),
        ):
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _read_fits_layout(file_path: Path) -> tuple[int, int, int, int] | None:
        """Lit le layout FITS comme (canaux, largeur, hauteur, BITPIX)."""
        try:
            with fits.open(file_path, memmap=False) as hdul:
                header = hdul[0].header
                data = hdul[0].data

            bitpix = int(header.get("BITPIX", 0) or 0)
            if data is None:
                return None

            array = np.asarray(data)
            if array.ndim == 2:
                height, width = array.shape
                return (1, int(width), int(height), bitpix)
            if array.ndim == 3:
                channels, height, width = array.shape
                return (int(channels), int(width), int(height), bitpix)
            return None
        except Exception:
            return None

    @classmethod
    def _filter_compatible_stack_inputs(
        cls, files: list[Path]
    ) -> tuple[list[Path], dict[str, int]]:
        """Conserve les FITS lisibles du layout majoritaire."""
        by_layout: dict[tuple[int, int, int, int], list[Path]] = {}
        unreadable: list[Path] = []
        for file_path in files:
            layout = cls._read_fits_layout(file_path)
            if layout is None:
                unreadable.append(file_path)
                continue
            by_layout.setdefault(layout, []).append(file_path)

        stats = {
            "rejected_incompatible_layout": 0,
            "rejected_unreadable_layout": 0,
        }
        if not by_layout:
            return files, stats

        dominant_layout, dominant_files = max(
            by_layout.items(), key=lambda item: len(item[1])
        )
        incompatible_files = [
            path
            for layout, layout_files in by_layout.items()
            if layout != dominant_layout
            for path in layout_files
        ]
        stats["rejected_incompatible_layout"] = len(incompatible_files)
        stats["rejected_unreadable_layout"] = len(unreadable)

        if incompatible_files:
            logging.warning(
                "Layouts FITS mélangés détectés pour le stack final. "
                "Layout conservé: %s (%d fichiers).",
                dominant_layout,
                len(dominant_files),
            )
            logging.warning(
                "Répertoires du layout conservé: %s",
                cls._summarize_parent_directories(dominant_files),
            )
            logging.warning(
                "Répertoires des fichiers incompatibles: %s",
                cls._summarize_parent_directories(incompatible_files),
            )

        if unreadable:
            logging.warning(
                "%d fichier(s) ignoré(s) pour le stack final: "
                "layout FITS non lisible.",
                len(unreadable),
            )
        return list(dominant_files), stats

    @staticmethod
    def _summarize_parent_directories(
        files: list[Path], max_dirs: int = 8
    ) -> str:
        """Résume les répertoires parents des fichiers avec leurs effectifs."""
        if not files:
            return "(aucun)"
        by_dir: dict[str, int] = {}
        for file_path in files:
            parent = str(file_path.parent)
            by_dir[parent] = by_dir.get(parent, 0) + 1

        ranked = sorted(by_dir.items(), key=lambda item: item[1], reverse=True)
        parts = [
            f"{directory} ({count})" for directory, count in ranked[:max_dirs]
        ]
        remaining = len(ranked) - max_dirs
        if remaining > 0:
            parts.append(f"... +{remaining} autre(s) répertoire(s)")
        return "; ".join(parts)
