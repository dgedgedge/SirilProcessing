"""Découverte, groupement et orchestration de la calibration des images light."""

from __future__ import annotations

import argparse
import glob
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from astropy.io import fits

from lib.fits_info import FitsInfo
from lib.light_calibration import SequenceCalibrationProcessor
from lib.logging_utils import add_session_file_logging, log_section, remove_session_file_logging
from lib.parameterized_component import ParameterizedComponent
from lib.siril import Siril
from lib.type_defs import ConfigValue


@dataclass
class SessionProcessingResult:
    """Résultat du traitement complet d’une session et de ses sous-sessions."""

    session_dir: Path
    output_image: Path | None
    total_subsessions: int
    successful_subsessions: int
    failed_subsessions: list[Path] = field(default_factory=list)
    wcs_stats: dict[str, int] = field(default_factory=dict)


class LightProcessor(ParameterizedComponent):
    """
    Calibre les sous-sessions d’une session puis produit son image empilée.

    ``process_session`` conserve la responsabilité du traitement d’une
    sous-session. ``process_target`` orchestre leur boucle et transmet l’ensemble
    des FITS calibrés à ``SessionStackingProcessor`` pour produire une image
    unique par session fournie en CLI.
    """

    parameter_persistence = dict.fromkeys(
        (
            "session_dirs",
            "force_reprocess",
            "purge_target",
        ),
        False,
    )
    config_path_parameters = {"work_dir", "output_dir"}

    @classmethod
    def parameter_children(cls) -> tuple[type[ParameterizedComponent], ...]:
        """Déclare la calibration par groupe et l’empilement de la session."""
        from lib.light_stacking import SessionStackingProcessor

        return (SequenceCalibrationProcessor, SessionStackingProcessor)

    @staticmethod
    def discover_subsessions(session_dir: Path | str) -> list[Path]:
        """Retourne la session directe ou ses sous-répertoires d’acquisition.

        Une session contenant directement ``light`` ou ``Light`` est traitée
        comme une seule sous-session. Sinon, seuls les enfants immédiats qui
        contiennent l’un de ces répertoires sont retournés, triés par nom.
        """
        root = Path(session_dir)
        if not root.is_dir():
            return []
        if any((root / name).exists() for name in ("light", "Light")):
            return [root]
        return [
            child
            for child in sorted(root.iterdir(), key=lambda path: path.name.lower())
            if child.is_dir()
            and any((child / name).exists() for name in ("light", "Light"))
        ]

    @staticmethod
    def _directory_last_modified(path: Path) -> float:
        """Retourne la date la plus récente, hors index HTML de présentation.

        Les dates de dossiers restent significatives pour les ajouts/suppressions
        de poses ; le générateur HTML les préserve lors de sa publication.
        """
        if not path.exists():
            return 0.0
        latest = path.stat().st_mtime
        if path.is_dir():
            for child in path.rglob("*"):
                if child.name == "index.html":
                    continue
                try:
                    latest = max(latest, child.stat().st_mtime)
                except OSError:
                    continue
        return latest

    @classmethod
    def _should_rebuild_stack(
        cls, combined_output: Path, calibrated_dirs: list[Path]
    ) -> bool:
        """Indique si une calibration est plus récente que le stack existant."""
        if not combined_output.exists():
            return True
        latest_input = max(
            (cls._directory_last_modified(directory) for directory in calibrated_dirs),
            default=0.0,
        )
        return latest_input > combined_output.stat().st_mtime

    @classmethod
    def process_target(
        cls,
        session_dir: Path | str,
        output_base_dir: Path,
        work_base_dir: Path,
        args: argparse.Namespace,
        stack_params: dict[str, ConfigValue],
    ) -> SessionProcessingResult:
        """Calibre toutes les sous-sessions d’une session et empile leurs FITS.

        Les sorties de calibration sont conservées sous
        ``<sortie>/<session>/sessions/<sous-session>``. Les FITS de toutes les
        sous-sessions réussies sont empilés ensemble en un unique
        ``<session>_combined.fits``. Le CLI numéroté fournit explicitement
        calibration_output_dir/calibration_work_dir : le stack s'appelle alors
        stack_combined.fits, sous 01_calibration ou son panneau. Les acquisitions
        sont sous 01_session et les étapes 02_registration à 05_stacking sont directes. En simulation, les étapes sont annoncées
        sans exécuter le stacking.
        """
        from lib.drizzle import cache_matches
        from lib.light_stacking import SessionStackingProcessor

        root = Path(session_dir)
        subsessions = cls.discover_subsessions(root)
        result = SessionProcessingResult(
            session_dir=root,
            output_image=None,
            total_subsessions=len(subsessions),
            successful_subsessions=0,
            wcs_stats={
                "lights_total": 0,
                "wcs_present": 0,
                "wcs_missing": 0,
                "wcs_unreadable": 0,
            },
        )
        if not subsessions:
            logging.error("Aucune sous-session valide à traiter dans %s", root)
            return result

        logging.info("Session %s : %d sous-session(s)", root, len(subsessions))
        calibration_output = getattr(args, 'calibration_output_dir', None)
        target_output = Path(calibration_output) if calibration_output else output_base_dir / root.name
        calibration_work = getattr(args, 'calibration_work_dir', None)
        target_work = Path(calibration_work) if calibration_work else work_base_dir / root.name
        stack_name = 'stack' if calibration_output else root.name
        sessions_name = '01_session' if calibration_output else 'sessions'
        successful_processors: list[LightProcessor] = []
        for index, subsession in enumerate(subsessions, 1):
            logging.info("%s", "=" * 60)
            logging.info(
                "Traitement de la sous-session %d/%d : %s",
                index,
                len(subsessions),
                subsession,
            )
            child_output_dir = target_output / sessions_name / subsession.name
            child_work_dir = target_work / sessions_name / subsession.name
            if args.force_reprocess:
                for directory in (child_output_dir, child_work_dir):
                    if directory.exists():
                        try:
                            shutil.rmtree(directory)
                            logging.info("Répertoire supprimé (relance --force): %s", directory)
                        except OSError as error:
                            logging.warning("Impossible de supprimer %s: %s", directory, error)

            log_file = child_work_dir / f"{subsession.name}_lightProcess.log"
            handler = add_session_file_logging(log_file, args.log_level)
            try:
                logging.info("Début du traitement de la sous-session : %s", subsession)
                logging.info("Log de sous-session : %s", log_file)
                child = cls(
                    session_dir=subsession,
                    output_dir=child_output_dir,
                    work_dir=child_work_dir,
                    force_reprocess=args.force_reprocess,
                    dry_run=args.dry_run,
                    keep_intermediate=args.keep_intermediate,
                )
                succeeded = child.process_session(stack_params)
                stats = child.get_session_stats()
                for key in result.wcs_stats:
                    result.wcs_stats[key] += int(stats.get(key, 0) or 0)
                logging.info(
                    "Sous-session stats - total lights: %d | WCS: present=%d, missing=%d, unreadable=%d | rejets: invalides=%d, non-light=%d, no-dark=%d, erreurs=%d | conservées=%d",
                    stats.get("lights_total", 0),
                    stats.get("wcs_present", 0),
                    stats.get("wcs_missing", 0),
                    stats.get("wcs_unreadable", 0),
                    stats.get("rejected_invalid_metadata", 0),
                    stats.get("rejected_not_light_type", 0),
                    stats.get("rejected_no_matching_dark", 0),
                    stats.get("rejected_processing_failure", 0),
                    stats.get("kept_for_stacking", 0),
                )
                if succeeded:
                    result.successful_subsessions += 1
                    successful_processors.append(child)
                    logging.info("Sous-session %s traitée avec succès", subsession)
                else:
                    result.failed_subsessions.append(subsession)
                    logging.error("Échec du traitement de la sous-session %s", subsession)
            except KeyboardInterrupt:
                result.failed_subsessions.append(subsession)
                logging.warning("Traitement interrompu par l'utilisateur")
                break
            except Exception:
                result.failed_subsessions.append(subsession)
                logging.exception("Erreur durant le traitement de %s", subsession)
            finally:
                remove_session_file_logging(handler)

        if args.dry_run:
            log_section(f"[DRY-RUN] Stacking prévu : {root.name}")
            logging.info(
                "[DRY-RUN] Stacking de la session %s à partir de %d sous-session(s)",
                root,
                len(subsessions),
            )
            return result

        session_outputs = [
            output
            for child in successful_processors
            for output in child.get_output_files()
        ]
        if args.force_stacking:
            logging.info(
                "Mode --force-stacking : reconstruction des FITS calibrés depuis le disque pour %s",
                root,
            )
            session_outputs = []
            session_output_root = target_output / sessions_name
            for subsession in subsessions:
                calibrated_root = session_output_root / subsession.name
                if calibrated_root.exists():
                    for directory in sorted(calibrated_root.rglob("*_calibrated")):
                        if directory.is_dir():
                            session_outputs.extend(sorted(directory.glob("pp_*.fit")))
                            session_outputs.extend(sorted(directory.glob("pp_*.fits")))

        unique_outputs: list[Path] = []
        seen: set[Path] = set()
        for output in session_outputs:
            absolute = Path(output).absolute()
            if absolute not in seen:
                seen.add(absolute)
                unique_outputs.append(absolute)
        session_outputs = unique_outputs
        logging.info(
            "Stack final de la session %s : %d FITS calibrés",
            root.name,
            len(session_outputs),
        )
        if not session_outputs:
            logging.warning("Aucun FITS calibré disponible pour %s", root)
            return result

        stack_output_dir = target_output
        stack_output_dir.mkdir(parents=True, exist_ok=True)
        stack_work_base = target_work if calibration_output else work_base_dir
        stack_work_dir = (target_work if calibration_output
                          else stack_work_base / stack_name / "stacking")
        stack_log = stack_work_dir / "stacking.log" if calibration_output else stack_work_dir / f"{root.name}_stacking.log"
        stack_handler = add_session_file_logging(stack_log, args.log_level)
        try:
            log_section(f"Début du stacking : {root.name}")
            logging.info("Log du stacking : %s", stack_log)
            candidates = [
                stack_output_dir / f"{stack_name}_combined.fits",
                stack_output_dir / f"{stack_name}_combined.fit",
            ]
            existing = next((candidate for candidate in candidates if candidate.exists()), None)
            calibrated_dirs = [
                directory
                for subsession in subsessions
                for directory in (
                    target_output / sessions_name / subsession.name
                ).rglob("*_calibrated")
                if directory.is_dir()
            ]
            if (
                not args.force_stacking
                and existing
                and cache_matches(existing, stack_params)
                and not cls._should_rebuild_stack(existing, calibrated_dirs)
            ):
                log_section(f"Réutilisation du stack existant : {root.name}")
                logging.info("Stack final déjà à jour pour %s : %s", root.name, existing)
                result.output_image = existing
                return result

            stack_report: dict[str, object] = {}
            report = SessionStackingProcessor().post_process(
                input_path=session_outputs[0],
                output_path=stack_output_dir / f"{stack_name}_combined.fits",
                args=argparse.Namespace(
                    output_files=session_outputs,
                    work_dir=stack_work_base,
                    target_name=stack_name,
                    stack_params=stack_params,
                    force_stacking=args.force_stacking,
                    stack_report=stack_report,
                    stack_work_dir=stack_work_dir if calibration_output else None,
                ),
            )
            output_image = report.get("output_image")
            if isinstance(output_image, str) and Path(output_image).is_file():
                result.output_image = Path(output_image)
                logging.info("Sortie combinée créée pour la session %s : %s", root, output_image)
            else:
                logging.error("Échec de la sortie combinée pour la session %s", root)
            logging.info(
                "Stack stats [%s] - total=%d | rejets: layout_incompatible=%d, layout_illisible=%d, alignement_qualité=%d | conservées uniques=%d | entrées effectives=%d",
                root.name,
                stack_report.get("total_input", 0),
                stack_report.get("rejected_incompatible_layout", 0),
                stack_report.get("rejected_unreadable_layout", 0),
                stack_report.get("rejected_registration_quality", 0),
                stack_report.get("kept_unique_for_stack", 0),
                stack_report.get("kept_effective_for_stack", 0),
            )
        except Exception:
            logging.exception("Échec du stacking de la session %s", root)
        finally:
            remove_session_file_logging(stack_handler)
        return result

    @classmethod
    def from_config(
        cls, session_dir: Path | str, **overrides: ConfigValue
    ) -> LightProcessor:
        """Construit un traitement depuis Config ; les valeurs explicites priment.

        ``use_flat`` configure la calibration enfant, y compris lorsqu'elle
        est fournie dans les surcharges. La valeur par défaut est True.
        """
        from lib.config import Config

        config = Config()
        explicit_overrides = set(overrides)
        options = {
            "output_dir": config.get(
                "output_dir", cls.CONFIG_DEFAULTS["output_dir"]
            ),
            "work_dir": config.get("work_dir", cls.CONFIG_DEFAULTS["work_dir"]),
            "force_reprocess": config.get("force_reprocess", False),
            "dry_run": config.get("dry_run", False),
            "keep_intermediate": config.get(
                "keep_intermediate", cls.CONFIG_DEFAULTS["keep_intermediate"]
            ),
        }
        options.update(overrides)
        calibration_processor = options.pop("calibration_processor", None)
        if calibration_processor is None:
            dark_library_path = options.pop(
                "dark_library_path",
                config.get(
                    "dark_library_path",
                    SequenceCalibrationProcessor.CONFIG_DEFAULTS[
                        "dark_library_path"
                    ],
                ),
            )
            no_dark = options.pop("no_dark", config.get("no_dark", False))
            use_flat = options.pop("use_flat", config.get("use_flat", True))
            temperature_precision = options.pop(
                "temperature_precision",
                config.get(
                    "temperature_precision",
                    SequenceCalibrationProcessor.CONFIG_DEFAULTS[
                        "temperature_precision"
                    ],
                ),
            )
            siril = options.get("siril") or Siril.create_with_defaults()
            options["siril"] = siril
            calibration_processor = SequenceCalibrationProcessor(
                siril,
                Path(options["work_dir"]),
                keep_intermediate=bool(options["keep_intermediate"]),
                args=argparse.Namespace(
                    no_dark=no_dark,
                    use_flat=use_flat,
                    temperature_precision=temperature_precision,
                    dark_library_path=dark_library_path,
                ),
            )
        else:
            if "dark_library_path" in explicit_overrides:
                calibration_processor.dark_library_path = str(
                    options.pop("dark_library_path")
                )
            if "temperature_precision" in explicit_overrides:
                calibration_processor.temp_precision = float(
                    options.pop("temperature_precision")
                )
            if "no_dark" in explicit_overrides:
                calibration_processor.use_dark = not bool(options.pop("no_dark"))
                calibration_processor.calibration.use_dark = (
                    calibration_processor.use_dark
                )
            if "use_flat" in explicit_overrides:
                calibration_processor.use_flat = bool(options.pop("use_flat"))
                calibration_processor.calibration.use_flat = calibration_processor.use_flat
        options["calibration_processor"] = calibration_processor
        return cls(session_dir, **options)

    CONFIG_DEFAULTS = {
        "work_dir": os.path.abspath(os.path.expanduser("~/tmp/sirilWorkDir")),
        "output_dir": os.path.abspath(os.path.expanduser("~/SirilProcessed")),
        "keep_intermediate": False,
        "enable_postprocess": True,
    }

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare les paramètres du traitement avec leurs défauts locaux."""
        config = cls.CONFIG_DEFAULTS
        parser.add_argument(
            "--postprocess",
            action=argparse.BooleanOptionalAction,
            dest="enable_postprocess",
            default=config["enable_postprocess"],
            help=(
                "Enchaîner le post-traitement du stack ou de la mosaïque finale "
                "(activé par défaut)"
            ),
        )
        # Arguments positionnels
        parser.add_argument(
            "session_dirs",
            nargs="+",
            help="Un répertoire de données ; plusieurs uniquement avec --mosaic (light/ et éventuellement flat/)",
        )

        SequenceCalibrationProcessor.add_arguments(parser)

        parser.add_argument(
            "--output",
            dest="output_dir",
            default=config["output_dir"],
            help=f"Répertoire de sortie pour les résultats. (défaut: '{config['output_dir']}')",
        )

        parser.add_argument(
            "-w",
            "--work-dir",
            dest="work_dir",
            type=str,
            default=config["work_dir"],
            help=f"Répertoire de travail temporaire. (Défaut: '{config['work_dir']}')",
        )

        parser.add_argument(
            "-f",
            "--force",
            dest="force_reprocess",
            action="store_true",
            help="Force le retraitement même si le fichier de sortie existent",
        )

        from lib.light_stacking import SessionStackingProcessor

        SessionStackingProcessor.add_arguments(parser)

        parser.add_argument(
            "--purge-target",
            dest="purge_target",
            action="store_true",
            default=False,
            help="Supprime l'arborescence de sortie et de travail des cibles passées en argument avant traitement (sans toucher les autres cibles).",
        )

        parser.add_argument(
            "--keep-intermediate",
            dest="keep_intermediate",
            action="store_true",
            default=config["keep_intermediate"],
            help="Conserve les répertoires/fichiers temporaires de prétraitement pour inspection manuelle.",
        )

    def __init__(
        self,
        session_dir: Path,
        output_dir: Path,
        work_dir: Path,
        force_reprocess: bool = False,
        dry_run: bool = False,
        keep_intermediate: bool = False,
        siril: Siril | None = None,
        calibration_processor: SequenceCalibrationProcessor | None = None,
    ) -> None:
        """
        Initialise le processeur de light.

        Args:
            session_dir: Répertoire de la session contenant light/ et flat/
            output_dir: Répertoire de sortie pour les résultats
            work_dir: Répertoire de travail temporaire
            force_reprocess: Force le retraitement même si les fichiers existent
            dry_run: Simule le traitement sans l'exécuter
            keep_intermediate: Conserve les répertoires/fichiers temporaires de prétraitement
            siril: Service Siril partagé avec les traitements de calibration.
            calibration_processor: Traitement de calibration déjà configuré.
        """
        self.session_dir = Path(session_dir)
        self.output_dir = Path(output_dir)
        self.work_dir = Path(work_dir)
        self.force_reprocess = force_reprocess
        self.dry_run = dry_run
        self.keep_intermediate = keep_intermediate

        # Initialisation de l'instance Siril avec la configuration par défaut
        self.siril = siril or Siril.create_with_defaults()
        self.calibration_processor = calibration_processor or SequenceCalibrationProcessor(
            self.siril,
            self.work_dir,
            keep_intermediate=self.keep_intermediate,
        )

        # Liste des fichiers de sortie créés durant le traitement
        self.output_files = []

        # Cache des flats de session (évite de rescanner le disque pour chaque groupe)
        self._session_flats_cache: list[FitsInfo] | None = None

        # Statistiques de session (lights)
        self._init_session_stats()

        # Validation des répertoires
        self._validate_directories()

        # Création des répertoires de sortie si nécessaire
        if not self.dry_run:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.work_dir.mkdir(parents=True, exist_ok=True)

    def _init_session_stats(self) -> None:
        """Initialise les compteurs de statistiques de session."""
        self.session_stats = {
            "session": str(self.session_dir),
            "lights_total": 0,
            "wcs_present": 0,
            "wcs_missing": 0,
            "wcs_unreadable": 0,
            "rejected_invalid_metadata": 0,
            "rejected_not_light_type": 0,
            "rejected_no_matching_dark": 0,
            "rejected_processing_failure": 0,
            "kept_for_stacking": 0,
            "groups_total": 0,
            "groups_success": 0,
        }

    def _has_wcs_header(self, file_path: Path) -> bool | None:
        """Retourne True si le FITS contient des infos WCS minimales, False sinon, None si illisible."""
        try:
            with fits.open(file_path, memmap=False) as hdul:
                header = hdul[0].header

            # Présence minimale de solution WCS 2D dans le header.
            has_crval = ("CRVAL1" in header) and ("CRVAL2" in header)
            has_crpix = ("CRPIX1" in header) and ("CRPIX2" in header)
            has_projection = ("CTYPE1" in header) and ("CTYPE2" in header)
            has_transform = (
                "CD1_1" in header
                and "CD1_2" in header
                and "CD2_1" in header
                and "CD2_2" in header
            ) or ("CDELT1" in header and "CDELT2" in header)
            return has_crval and has_crpix and has_projection and has_transform
        except Exception as exc:
            logging.debug(f"Lecture WCS impossible pour {file_path}: {exc}")
            return None

    def _collect_wcs_stats(self, light_files: list[Path]) -> None:
        """Collecte les stats de présence WCS sur les fichiers lights originaux."""
        wcs_present = 0
        wcs_missing = 0
        wcs_unreadable = 0

        for light_file in light_files:
            has_wcs = self._has_wcs_header(light_file)
            if has_wcs is True:
                wcs_present += 1
            elif has_wcs is False:
                wcs_missing += 1
            else:
                wcs_unreadable += 1

        self.session_stats["wcs_present"] = wcs_present
        self.session_stats["wcs_missing"] = wcs_missing
        self.session_stats["wcs_unreadable"] = wcs_unreadable

    def _validate_directories(self) -> None:
        """Valide l'existence des répertoires requis."""
        if not self.session_dir.exists():
            raise ValueError(
                f"Le répertoire de session n'existe pas: {self.session_dir}"
            )

        # Vérifier l'existence du répertoire light (insensible à la casse)
        light_found = False
        for light_name in ["light", "Light"]:
            light_dir = self.session_dir / light_name
            if light_dir.exists():
                light_found = True
                break

        if not light_found:
            raise ValueError(
                f"Aucun répertoire 'light' ou 'Light' trouvé dans: {self.session_dir}"
            )

        if (
            self.calibration_processor.use_dark
            and self.calibration_processor.dark_library_path
            and not Path(self.calibration_processor.dark_library_path).exists()
        ):
            raise ValueError(
                "La librairie de darks n'existe pas: "
                f"{self.calibration_processor.dark_library_path}"
            )

    def find_light_files(self) -> list[Path]:
        """
        Trouve tous les fichiers FITS dans le répertoire light ou Light.

        Returns:
            Liste des chemins vers les fichiers light trouvés
        """
        # Chercher le répertoire light (insensible à la casse)
        light_dir = None
        for light_name in ["light", "Light"]:
            potential_light_dir = self.session_dir / light_name
            if potential_light_dir.exists():
                light_dir = potential_light_dir
                break

        if light_dir is None:
            logging.error(
                f"Aucun répertoire 'light' ou 'Light' trouvé dans {self.session_dir}"
            )
            return []

        # Extensions FITS supportées
        extensions = ["*.fit", "*.fits", "*.FIT", "*.FITS"]

        light_files = []
        for ext in extensions:
            pattern = str(light_dir / ext)
            light_files.extend(glob.glob(pattern))

        # Conversion en objets Path et tri
        light_files = [Path(f) for f in light_files]
        light_files.sort()

        logging.debug(f"Répertoire light trouvé: {light_dir}")
        logging.debug(f"{len(light_files)} fichiers light détectés dans {light_dir}")
        return light_files

    def find_flat_files(self) -> list[Path]:
        """
        Trouve tous les fichiers FITS dans le répertoire flat ou Flat.

        Returns:
            Liste des chemins vers les fichiers flat trouvés
        """
        flat_dir = None
        for flat_name in ["flat", "Flat"]:
            potential_flat_dir = self.session_dir / flat_name
            if potential_flat_dir.exists():
                flat_dir = potential_flat_dir
                break

        if flat_dir is None:
            return []

        extensions = ["*.fit", "*.fits", "*.FIT", "*.FITS"]
        flat_files = []
        for ext in extensions:
            pattern = str(flat_dir / ext)
            flat_files.extend(glob.glob(pattern))

        flat_files = [Path(f) for f in flat_files]
        flat_files.sort()

        logging.debug(f"Répertoire flat trouvé: {flat_dir}")
        logging.debug(f"{len(flat_files)} fichiers flat détectés dans {flat_dir}")
        return flat_files

    def _is_flat_frame(self, fits_info: FitsInfo) -> bool:
        """Retourne True si le FITS correspond à un flat d'après IMAGETYP."""
        image_type = (fits_info.imagetyp_value or "").lower()
        return "flat" in image_type

    def _load_session_flats(self) -> list[FitsInfo]:
        """
        Charge les flats valides de la session (avec mise en cache).

        Returns:
            Liste des métadonnées flats exploitables
        """
        if self._session_flats_cache is not None:
            return self._session_flats_cache

        flat_files = self.find_flat_files()
        flat_infos: list[FitsInfo] = []

        for flat_file in flat_files:
            try:
                flat_info = FitsInfo(str(flat_file))

                if not flat_info.validData():
                    logging.warning(
                        f"Fichier flat invalide (métadonnées manquantes): {flat_file}"
                    )
                    continue

                if not self._is_flat_frame(flat_info):
                    logging.warning(
                        f"Fichier ignoré (pas un flat): {flat_file} (type: {flat_info.imagetyp_value})"
                    )
                    continue

                flat_infos.append(flat_info)
            except Exception as e:
                logging.warning(f"Erreur lors de l'analyse du flat {flat_file}: {e}")

        self._session_flats_cache = flat_infos
        return flat_infos

    def _select_flats_for_light_group(self, light_info: FitsInfo) -> list[FitsInfo]:
        """
        Sélectionne les flats compatibles avec un groupe de lights.

        Le critère principal suit les contraintes instrumentales communes: caméra,
        gain et binning. La température et le temps d'exposition sont propres aux
        flats et ne sont donc pas imposés ici.
        """
        session_flats = self._load_session_flats()
        if not session_flats:
            return []

        selected = []
        for flat_info in session_flats:
            same_camera = flat_info.camera() == light_info.camera()
            same_gain = round(flat_info.gain()) == round(light_info.gain())
            same_binning = flat_info.binning_value() == light_info.binning_value()

            if same_camera and same_gain and same_binning:
                selected.append(flat_info)

        if selected:
            logging.debug(
                f"{len(selected)} flats compatibles trouvés pour le groupe light "
                f"(cam={light_info.camera()}, gain={round(light_info.gain())}, binning={light_info.binning()})"
            )
        else:
            logging.debug("Aucun flat compatible trouvé pour ce groupe de lights")

        return selected

    def analyze_light_characteristics(
        self, light_files: list[Path]
    ) -> dict[str, list[FitsInfo]]:
        """
        Analyse les caractéristiques des images light et les groupe.

        Args:
            light_files: Liste des fichiers light à analyser

        Returns:
            Dictionnaire groupant les FitsInfo par caractéristiques communes
        """
        groups = {}
        invalid_files = []

        for light_file in light_files:
            try:
                fits_info = FitsInfo(str(light_file))

                if not fits_info.validData():
                    invalid_files.append(light_file)
                    self.session_stats["rejected_invalid_metadata"] += 1
                    logging.warning(
                        f"Fichier light invalide (métadonnées manquantes): {light_file}"
                    )
                    continue

                # Vérifier que c'est bien un light (pas un dark ou bias)
                if fits_info.is_dark() or fits_info.is_bias():
                    self.session_stats["rejected_not_light_type"] += 1
                    logging.warning(
                        f"Fichier ignoré (pas un light): {light_file} (type: {fits_info.imagetyp_value})"
                    )
                    continue

                # Grouper par caractéristiques
                group_key = fits_info.group_key(
                    self.calibration_processor.temp_precision
                )
                if group_key not in groups:
                    groups[group_key] = []
                groups[group_key].append(fits_info)

            except Exception as e:
                invalid_files.append(light_file)
                logging.error(f"Erreur lors de l'analyse de {light_file}: {e}")

        if invalid_files:
            logging.warning(f"{len(invalid_files)} fichiers light invalides ignorés")

        # Log des groupes trouvés
        for group_key, fits_list in groups.items():
            logging.debug(
                f"Groupe de calibration détecté: '{group_key}' ({len(fits_list)} images)"
            )
            if fits_list:
                example = fits_list[0]
                logging.debug(
                    f"  Exemple: T={example.temperature()}°C, "
                    f"Exp={example.exptime()}s, "
                    f"Gain={example.gain()}, "
                    f"Caméra={example.camera()}, "
                    f"Binning={example.binning()}"
                )

        return groups

    def find_matching_master_dark(self, light_info: FitsInfo) -> Path | None:
        """
        Trouve le master dark correspondant aux caractéristiques du light.

        Args:
            light_info: Information du fichier light

        Returns:
            Chemin vers le master dark correspondant ou None si non trouvé
        """
        if not self.calibration_processor.use_dark:
            logging.debug("Mode sans dark: recherche de master dark ignorée")
            return None

        dark_library_path = self.calibration_processor.dark_library_path
        if not dark_library_path:
            logging.warning("Aucune librairie de darks spécifiée")
            return None

        dark_lib_path = Path(dark_library_path)
        if not dark_lib_path.exists():
            logging.error(f"Librairie de darks introuvable: {dark_lib_path}")
            return None

        # Extensions FITS supportées
        extensions = ["*.fit", "*.fits", "*.FIT", "*.FITS"]

        # Chercher tous les fichiers FITS dans la librairie
        master_dark_files = []
        for ext in extensions:
            pattern = str(dark_lib_path / "**" / ext)
            master_dark_files.extend(glob.glob(pattern, recursive=True))

        # Analyser chaque master dark pour trouver une correspondance
        for dark_file in master_dark_files:
            try:
                dark_info = FitsInfo(dark_file)

                if not dark_info.validData():
                    continue

                if not dark_info.is_dark():
                    continue

                # Vérifier la correspondance des caractéristiques
                if light_info.is_equivalent(
                    dark_info, self.calibration_processor.temp_precision
                ):
                    logging.debug(f"Master dark trouvé: {dark_file}")
                    logging.debug(
                        f"  Light: T={light_info.temperature()}°C, "
                        f"Exp={light_info.exptime()}s, "
                        f"Gain={light_info.gain()}, "
                        f"Caméra={light_info.camera()}"
                    )
                    logging.debug(
                        f"  Dark:  T={dark_info.temperature()}°C, "
                        f"Exp={dark_info.exptime()}s, "
                        f"Gain={dark_info.gain()}, "
                        f"Caméra={dark_info.camera()}"
                    )
                    return Path(dark_file)

            except Exception as e:
                logging.debug(f"Erreur lors de l'analyse du dark {dark_file}: {e}")
                continue

        logging.warning(
            f"Aucun master dark correspondant trouvé pour: "
            f"T={light_info.temperature()}°C, "
            f"Exp={light_info.exptime()}s, "
            f"Gain={light_info.gain()}, "
            f"Caméra={light_info.camera()}, "
            f"Binning={light_info.binning()}"
        )
        return None

    def process_light_group(
        self, group_key: str, light_infos: list[FitsInfo],
        stack_params: dict[str, ConfigValue],
    ) -> bool:
        """Calibre un groupe de lights ou reprend ses sorties existantes.

        Les étapes master flat et calibration sont annoncées en INFO avec
        des titres ===. La reprise annonce les lights déjà calibrées ; elle
        ne prétend pas réutiliser séparément un master flat. Les détails de
        préparation et les caractéristiques sont en DEBUG. Le mode de flats
        doit correspondre aux sorties en cache avant leur réutilisation.

        Parameters
        ----------
        group_key : str
            Identifiant du groupe de calibration dans la sous-session.
        light_infos : list[FitsInfo]
            Métadonnées des lights compatibles, sans modification des sources.
        stack_params : dict[str, ConfigValue]
            Réglages du stacking ; Drizzle détermine ici la conservation du CFA.

        Returns
        -------
        bool
            Succès du calcul ou de la reprise, sans exécuter de stack de groupe.
            En simulation, les opérations sont seulement annoncées.
        """
        if not light_infos:
            logging.warning(f"Groupe vide: {group_key}")
            return False

        logging.debug(f"Traitement du groupe '{group_key}' ({len(light_infos)} images)")

        # Prendre le premier light comme référence pour les caractéristiques
        reference_light = light_infos[0]

        master_dark_path = None
        if self.calibration_processor.use_dark:
            # Chercher le master dark correspondant
            master_dark_path = self.find_matching_master_dark(reference_light)
            if not master_dark_path:
                self.session_stats["rejected_no_matching_dark"] += len(light_infos)
                logging.error(
                    f"Impossible de traiter le groupe '{group_key}': aucun master dark correspondant"
                )
                return False

        # Préparation optionnelle des flats pour ce groupe
        selected_flats = (
            self._select_flats_for_light_group(reference_light)
            if self.calibration_processor.use_flat else []
        )
        if not self.calibration_processor.use_flat:
            logging.info("Flats désactivés : aucune recherche ni application pour ce groupe")
        if selected_flats:
            # Pour appliquer un seul dark de calibration flats, on garde un sous-ensemble
            # homogène en temps de pose (groupe majoritaire).
            flats_by_exptime: dict[float, list[FitsInfo]] = {}
            for flat_info in selected_flats:
                exp_key = round(flat_info.exptime(), 6)
                flats_by_exptime.setdefault(exp_key, []).append(flat_info)

            if len(flats_by_exptime) > 1:
                best_exp_key, best_group = max(
                    flats_by_exptime.items(), key=lambda item: len(item[1])
                )
                ignored_count = len(selected_flats) - len(best_group)
                logging.warning(
                    f"Plusieurs temps de pose de flats détectés pour le groupe '{group_key}'. "
                    f"Utilisation de EXPTIME={best_exp_key}s ({len(best_group)} flats), "
                    f"{ignored_count} flat(s) ignoré(s)."
                )
                selected_flats = best_group

        flat_sequence_name = f"flat_{group_key}"
        master_flat_output_base = str(self.work_dir / f"master_flat_{group_key}")

        # Préparer les chemins de fichiers
        light_files = [Path(info.filepath) for info in light_infos]

        # Nom de la séquence basé sur le group_key
        sequence_name = f"light_{group_key}"

        # Nom de base du fichier de sortie basé sur le nom de session
        session_basename = self.session_dir.name
        calibrated_output_dir = (
            self.output_dir / f"{session_basename}_{group_key}_calibrated"
        )

        # Les groupes calibrés sont exportés sans stack intermédiaire ;
        # le stacking final se fera sur l'ensemble des FITS calibrés dans ce dossier.
        existing_outputs = SequenceCalibrationProcessor.list_calibrated_outputs(
            calibrated_output_dir
        )
        existing_output = existing_outputs[0] if existing_outputs else None

        needs_native_cfa = False
        if existing_output and stack_params.get("drizzle", "off") != "off":
            try:
                source_header = fits.getheader(light_files[0])
                cached_header = fits.getheader(existing_output)
                needs_native_cfa = (
                    source_header.get("NAXIS") == 2
                    and source_header.get("BAYERPAT", "").strip()
                    in {"RGGB", "BGGR", "GRBG", "GBRG"}
                    and cached_header.get("NAXIS") == 3
                )
            except (OSError, ValueError):
                logging.warning(
                    "Impossible de vérifier le CFA des calibrations existantes"
                )
        if needs_native_cfa:
            logging.info(
                "Recalibration nécessaire pour préserver le CFA natif avant décision Drizzle"
            )

        flat_cache_matches = self.calibration_processor.flat_cache_matches(
            calibrated_output_dir
        )
        if existing_output and not flat_cache_matches:
            logging.info("Recalibration nécessaire : changement du mode d'utilisation des flats")
        if (
            existing_output and not self.force_reprocess and not needs_native_cfa
            and flat_cache_matches
        ):
            logging.info(
                "%s=== Réutilisation des lights déjà calibrées : groupe %s ===",
                "[DRY-RUN] " if self.dry_run else "", group_key,
            )
            if not self.dry_run and not any(
                (calibrated_output_dir / name).exists()
                for name in (f"pp_{sequence_name}_.seq", f"pp_{sequence_name}.seq")
            ):
                SequenceCalibrationProcessor.save_calibrated_sequence(
                    sequence_name, calibrated_output_dir
                )
            logging.info("Résultats calibrés disponibles : %s", calibrated_output_dir)
            # Enregistrer le fichier existant dans la liste des sorties
            self.output_files.append(existing_output)
            self.session_stats["kept_for_stacking"] += len(light_infos)
            return True

        # Créer le répertoire de sortie si nécessaire
        if not self.dry_run:
            self.output_dir.mkdir(parents=True, exist_ok=True)

        if self.force_reprocess and calibrated_output_dir.exists() and not self.dry_run:
            try:
                shutil.rmtree(calibrated_output_dir)
                logging.debug(
                    f"Répertoire calibré existant supprimé (mode --force): {calibrated_output_dir}"
                )
            except Exception as exc:
                logging.warning(
                    f"Impossible de nettoyer le répertoire calibré {calibrated_output_dir}: {exc}"
                )

        masters: list[str] = []
        if master_dark_path:
            masters.append("master dark")
        if selected_flats:
            masters.append("master flat")
        calibration_description = (
            "avec " + " + ".join(masters)
            if masters else "sans master dark ni master flat"
        )
        calibration_stage = (
            f"=== Calibration des lights {calibration_description} : "
            f"{len(light_files)} images — groupe {group_key} ==="
        )

        flat_dark_path = None
        if selected_flats and self.calibration_processor.use_dark:
            flat_dark_path = self.find_matching_master_dark(selected_flats[0])
            if not flat_dark_path and not self.dry_run:
                self.session_stats["rejected_processing_failure"] += len(light_infos)
                logging.info(
                    "=== Création du master flat : %d flats — groupe %s ===",
                    len(selected_flats), group_key,
                )
                logging.error(
                    f"Impossible de traiter le groupe '{group_key}': "
                    "aucun master dark adapté pour calibrer les flats"
                )
                return False

        calibration_processor = self.calibration_processor
        try:
            report = calibration_processor.post_process(
                light_files[0],
                calibrated_output_dir,
                args=argparse.Namespace(
                    sequence_name=sequence_name,
                    group_key=group_key,
                    light_files=light_files,
                    master_dark_path=(
                        str(master_dark_path) if master_dark_path else None
                    ),
                    flat_sequence_name=flat_sequence_name,
                    flat_files=[Path(info.filepath) for info in selected_flats],
                    flat_dark_path=(
                        str(flat_dark_path) if flat_dark_path else None
                    ),
                    master_flat_output_base=master_flat_output_base,
                    calibration_stage=calibration_stage,
                    stack_params=stack_params,
                    dry_run=self.dry_run,
                ),
            )
        except Exception as error:
            if self.dry_run:
                logging.warning(
                    "[DRY-RUN] Impossible de générer le script Siril: %s", error
                )
                return True
            self.session_stats["rejected_processing_failure"] += len(light_infos)
            logging.error("Erreur lors du traitement du groupe '%s': %s", group_key, error)
            return False

        if self.dry_run:
            return True

        output_paths = report.get("outputs")
        if not isinstance(output_paths, list) or not all(
            isinstance(path, str) for path in output_paths
        ):
            self.session_stats["rejected_processing_failure"] += len(light_infos)
            logging.error("Rapport de calibration invalide pour le groupe '%s'", group_key)
            return False
        self.output_files.extend(Path(path) for path in output_paths)
        self.session_stats["kept_for_stacking"] += len(light_infos)
        return True

    def process_session(self, stack_params: dict) -> bool:
        """
        Traite toute la session: détecte les lights, les groupe et les traite.

        Args:
            stack_params: Paramètres de stacking

        Returns:
            True si tout s'est bien passé, False sinon
        """
        logging.debug(f"Début du traitement de la session: {self.session_dir}")

        # 1. Trouver tous les fichiers light
        self._init_session_stats()
        light_files = self.find_light_files()
        self.session_stats["lights_total"] = len(light_files)
        self._collect_wcs_stats(light_files)
        if not light_files:
            logging.error("Aucun fichier light trouvé")
            return False

        # 2. Analyser et grouper les caractéristiques
        light_groups = self.analyze_light_characteristics(light_files)
        if not light_groups:
            logging.error("Aucun groupe de lights valide trouvé")
            return False

        # 3. Traiter chaque groupe
        success_count = 0
        total_groups = len(light_groups)
        self.session_stats["groups_total"] = total_groups

        for group_key, light_infos in light_groups.items():
            try:
                if self.process_light_group(group_key, light_infos, stack_params):
                    success_count += 1
                    self.session_stats["groups_success"] += 1
                    logging.debug(f"Groupe '{group_key}' traité avec succès")
                else:
                    logging.error(f"Échec du traitement du groupe '{group_key}'")
            except Exception as e:
                self.session_stats["rejected_processing_failure"] += len(light_infos)
                logging.error(f"Erreur lors du traitement du groupe '{group_key}': {e}")

        # 4. Résumé final
        logging.info(
            f"Traitement terminé: {success_count}/{total_groups} groupes traités avec succès"
        )

        if success_count == total_groups:
            return True
        elif success_count > 0:
            logging.warning(
                f"Traitement partiel: {success_count}/{total_groups} groupes réussis"
            )
            return True
        else:
            logging.error("Aucun groupe n'a pu être traité")
            return False

    def get_output_files(self) -> list[Path]:
        """
        Récupère la liste des fichiers de sortie créés pendant le traitement.

        Returns:
            Liste des chemins vers les fichiers de sortie créés ou utilisés
        """
        return self.output_files.copy()  # Copie pour éviter les modifications externes

    def _materialize_calibrated_outputs(self, calibrated_output_dir: Path) -> None:
        """Remplace les liens symboliques exportés par de vrais fichiers FITS persistants."""
        if not calibrated_output_dir.exists():
            return

        candidates = SequenceCalibrationProcessor.list_calibrated_outputs(
            calibrated_output_dir
        )
        converted_count = 0

        for output_file in candidates:
            if not output_file.is_symlink():
                continue

            try:
                target = output_file.resolve(strict=True)
                temp_copy = output_file.with_name(
                    f"{output_file.name}.tmp_materialized"
                )
                if temp_copy.exists():
                    temp_copy.unlink()

                shutil.copy2(target, temp_copy)
                output_file.unlink()
                temp_copy.rename(output_file)
                converted_count += 1
            except Exception as exc:
                logging.warning(
                    "Impossible de figer la sortie calibrée %s (lien symbolique): %s",
                    output_file,
                    exc,
                )

        if converted_count > 0:
            logging.info(
                "Sorties calibrées figées en fichiers réels: %d dans %s",
                converted_count,
                calibrated_output_dir,
            )

    def get_session_stats(self) -> dict[str, object]:
        """Retourne une copie des statistiques de session."""
        return dict(self.session_stats)
