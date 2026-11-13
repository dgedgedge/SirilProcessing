"""Traitements configurables des séquences light et des masters flats.

``SequenceCalibrationProcessor`` calibre un groupe et délègue la création
facultative de son master flat à ``MasterFlatProcessor``. Les deux étapes
utilisent l'interface commune ``lib.processor.processor`` et reçoivent leurs
paramètres d'exécution explicitement. Les règles des commandes Siril et des
formats de sortie sont décrites dans
``docs/scripts/lightProcess/treatments/CALIBRATION.md``.
"""

from __future__ import annotations


import argparse
import json
import logging
import os
import re
import shutil
from pathlib import Path

from astropy.io import fits

from lib.processor import processor
from lib.siril import SirilSequence
from lib.siril import Siril
from lib.type_defs import ConfigValue, JSONReport
from lib.siril_api import SirilCommand, command


class CalibrationProfile:
    """Construit les commandes de calibration CFA selon les masters disponibles."""

    def __init__(
        self,
        use_dark: bool = True,
        use_flat: bool = True,
        equalize_cfa: bool = True,
        debayer: bool = True,
    ) -> None:
        """Mémorise les choix de calibration utilisés pour bâtir les commandes."""
        self.use_dark = use_dark
        self.use_flat = use_flat
        self.equalize_cfa = equalize_cfa
        self.debayer = debayer

    def build_command(
        self,
        target: str,
        sequence_name: str,
        dark_path: str | None = None,
        flat_path: str | None = None,
    ) -> str:
        """Construit la commande ``calibrate`` correspondante.

        ``target`` doit valoir ``flat`` ou ``light``. La commande flat ne
        soustrait qu'un dark ; les commandes light appliquent les masters
        disponibles puis choisissent le dématriçage selon le profil.
        """
        target_name = target.lower()
        if target_name not in {"flat", "light"}:
            raise ValueError(f"Cible de calibration inconnue: {target}")
        arguments = [sequence_name]
        if target_name == 'flat':
            if not self.use_dark:
                return ''
            if dark_path is None:
                raise ValueError('dark_path est requis pour la calibration des flats')
            arguments += [f'-dark={dark_path}', '-cc=dark', '-cfa']
        else:
            has_dark = self.use_dark and dark_path is not None
            has_flat = self.use_flat and bool(flat_path)
            if has_dark:
                arguments.append(f'-dark={dark_path}')
            if has_flat:
                arguments.append(f'-flat={flat_path}')
            if has_dark:
                arguments.append('-cc=dark')
            arguments.append('-cfa')
            if has_flat:
                arguments.append('-equalize_cfa')
            arguments.append('-debayer')
        return command('calibrate', *arguments).render()


    def build_flat_calibration(
        self, sequence_name: str, dark_path: str | None = None
    ) -> tuple[str, str]:
        """Retourne le titre de journal et la commande de calibration des flats."""
        if not self.use_dark:
            return "Mode sans dark: flats non calibrés par dark", ""
        if dark_path is None:
            raise ValueError("dark_path est requis quand use_dark est activé")
        command = self.build_command("flat", sequence_name, dark_path=dark_path)
        return f"cmd:========> {command}", command

    def build_light_preprocess(
        self,
        sequence_name: str,
        dark_path: str | None = None,
        flat_path: str | None = None,
    ) -> tuple[str, str, str]:
        """Retourne le titre, le détail et la commande pour calibrer les lights."""
        if self.use_dark and dark_path is None:
            raise ValueError("dark_path est requis quand use_dark est activé")
        if self.use_dark and dark_path and self.use_flat and flat_path:
            title = "Pre-process Light Frames (dark + flat calibration)"
        elif self.use_dark and dark_path:
            title = "Pre-process Light Frames (calibration with dark subtraction)"
            flat_path = None
        elif self.use_flat and flat_path:
            title = "Pre-process Light Frames (flat calibration only)"
            dark_path = None
        else:
            title = "Pre-process Light Frames (calibration without dark subtraction)"
            dark_path = None
            flat_path = None
        command = self.build_command(
            "light", sequence_name, dark_path=dark_path, flat_path=flat_path
        )
        return title, f"cmd:========> {command}", command


class MasterFlatProcessor(processor):
    """Construit un master flat Siril appelé par le traitement d'une séquence."""

    def __init__(
        self,
        siril: Siril,
        work_dir: Path,
        calibration: CalibrationProfile,
        keep_intermediate: bool = False,
    ) -> None:
        """Configure les dépendances d'exécution sans partager l'état entre groupes."""
        self.siril = siril
        self.work_dir = Path(work_dir)
        self.calibration = calibration
        self.keep_intermediate = keep_intermediate

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Ne déclare pas d'option propre ; reçoit le profil du processeur parent.

        L'utilisation des darks est un choix commun aux calibrations des flats et
        des lights, porté par ``SequenceCalibrationProcessor``. Les chemins et
        noms de séquence sont fournis au moment de l'exécution.
        """
        del parser

    @classmethod
    def parameter_children(cls) -> tuple[type[processor], ...]:
        """Déclare qu'un master flat n'a pas de traitement enfant."""
        return ()

    def _generate_commands(
        self,
        sequence_name: str,
        output_base: Path,
        dark_path: str | None,
    ) -> list[SirilCommand]:
        """Construit les appels API qui calibrent les flats puis les empilent."""
        flat_process_dir = self.work_dir / "flat_process"
        sequence_dir = self.work_dir / sequence_name
        calibration_message, calibration_command = self.calibration.build_flat_calibration(sequence_name, dark_path)
        logging.info('%s', calibration_message)
        stack_input = f'pp_{sequence_name}' if self.calibration.use_dark else sequence_name
        operations = [command('cd', sequence_dir),
                      command('convert', sequence_name, f'-out={flat_process_dir}'),
                      command('cd', flat_process_dir)]
        if calibration_command.strip():
            operations.append(SirilCommand.from_text(calibration_command))
        operations += [command('stack', stack_input, 'median', '-norm=mul', f'-out={output_base}'),
                       command('cd', self.work_dir), command('close')]
        return operations


    def post_process(
        self,
        input_path: Path,
        output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Crée et valide un master flat à partir des flats sélectionnés.

        Parameters
        ----------
        input_path : Path
            Premier flat sélectionné, utilisé comme entrée descriptive du rapport.
        output_path : Path
            Chemin de base du master flat, sans extension.
        args : argparse.Namespace | None
            Doit contenir ``sequence_name``, ``flat_files``, ``flat_dark_path``,
            ``group_key`` et ``dry_run``.

        Returns
        -------
        JSONReport
            État de l'étape et chemin du master flat créé ou attendu.

        Raises
        ------
        RuntimeError
            Si Siril échoue ou ne produit pas le master flat demandé.
        """
        options = self._get_args(args)
        SequenceCalibrationProcessor._require_runtime_arguments(
            options,
            (
                "sequence_name",
                "flat_files",
                "flat_dark_path",
                "group_key",
                "dry_run",
            ),
        )
        sequence_name = options.sequence_name
        flat_files = [Path(path) for path in options.flat_files]
        dark_path = options.flat_dark_path
        group_key = options.group_key
        dry_run = options.dry_run
        if not flat_files:
            raise ValueError("La séquence flat ne contient aucune image")
        expected = Path(f"{output_path}.fits")
        if dry_run:
            return {
                "status": "dry_run",
                "input_image": str(input_path),
                "output_image": str(expected),
            }
        if self.calibration.use_dark and not dark_path:
            raise ValueError("flat_dark_path est requis quand use_dark est activé")

        try:
            SequenceCalibrationProcessor._prepare_sequence(
                self.work_dir, sequence_name, flat_files
            )
            flat_process_dir = self.work_dir / "flat_process"
            if flat_process_dir.exists():
                try:
                    shutil.rmtree(flat_process_dir)
                except OSError as error:
                    logging.warning(
                        "Impossible de nettoyer %s: %s", flat_process_dir, error
                    )
            flat_process_dir.mkdir(parents=True, exist_ok=True)
            operations = self._generate_commands(sequence_name, output_path, dark_path)
            if not self.siril.run_commands(
                operations,
                str(self.work_dir),
                name=f"master_flat_{group_key}",
            ):
                raise RuntimeError("Échec de la création du master flat")
            candidates = [expected, Path(f"{output_path}.fit")]
            master_flat = next((path for path in candidates if path.exists()), None)
            if master_flat is None:
                raise RuntimeError(
                    f"Master flat non trouvé: {candidates[0]} ou {candidates[1]}"
                )
            logging.info("Master flat créé : %s", master_flat)
            return {
                "status": "ok",
                "input_image": str(input_path),
                "output_image": str(master_flat),
            }
        finally:
            if self.keep_intermediate:
                logging.debug(
                    "Mode conservation activé: séquence flat conservée pour inspection (%s)",
                    self.work_dir / sequence_name,
                )
            else:
                SequenceCalibrationProcessor._cleanup_sequence(
                    self.work_dir, sequence_name
                )


class SequenceCalibrationProcessor(processor):
    """Calibre un groupe de lights et construit au besoin son master flat."""

    config_path_parameters = {"dark_library_path"}
    CONFIG_DEFAULTS = {
        "dark_library_path": os.path.abspath(os.path.expanduser("~/darkLib")),
        "temperature_precision": 0.5,
    }

    def __init__(
        self,
        siril: Siril,
        work_dir: Path,
        keep_intermediate: bool = False,
        args: argparse.Namespace | None = None,
        dark_library_path: str | Path | None = None,
    ) -> None:
        """Configure la calibration depuis les options propres à cette classe.

        ``args`` permet une surcharge isolée, notamment pour les fabriques qui
        construisent le traitement hors du parseur CLI. L'option ``--dark-lib``
        fournit la bibliothèque recherchée ; ``--no-dark`` détermine l'usage des
        masters darks pour les lights et les flats. ``--no-flat`` ignore les
        flats et leur master ; ``--flat`` les réactive (défaut).
        ``dark_library_path`` permet de surcharger directement le chemin résolu.
        ``--temperature-precision`` détermine le pas d'arrondi utilisé pour
        grouper les images et rechercher un master dark compatible.
        """
        if args is not None:
            self.set_from_args(args)
        options = self._get_args()
        self.use_dark = not getattr(options, "no_dark", False)
        self.use_flat = getattr(options, "use_flat", True)
        self.temp_precision = getattr(
            options,
            "temperature_precision",
            self.CONFIG_DEFAULTS["temperature_precision"],
        )
        configured_dark_library = getattr(
            options, "dark_library_path", self.CONFIG_DEFAULTS["dark_library_path"]
        )
        selected_dark_library = (
            configured_dark_library if dark_library_path is None else dark_library_path
        )
        self.dark_library_path = str(selected_dark_library) if selected_dark_library else ""
        self.siril = siril
        self.work_dir = Path(work_dir)
        self.calibration = CalibrationProfile(
            use_dark=self.use_dark, use_flat=self.use_flat
        )
        self.keep_intermediate = keep_intermediate
        self.master_flat_processor = MasterFlatProcessor(
            siril, self.work_dir, self.calibration, keep_intermediate
        )

    @staticmethod
    def _require_runtime_arguments(
        args: argparse.Namespace, required: tuple[str, ...]
    ) -> None:
        """Vérifie les champs du contexte requis par une étape de calibration."""
        missing = [name for name in required if not hasattr(args, name)]
        if missing:
            raise ValueError(
                "Paramètres d'exécution de calibration manquants: "
                + ", ".join(missing)
            )

    @staticmethod
    def _prepare_sequence(
        work_dir: Path, sequence_name: str, source_files: list[Path]
    ) -> None:
        """Prépare les liens .fits d’une séquence, sans modifier les sources .fit/.fits."""
        sequence_dir = work_dir / sequence_name
        sequence_dir.mkdir(parents=True, exist_ok=True)
        for index, file_path in enumerate(source_files):
            source_path = file_path.resolve()
            if not source_path.is_file():
                raise FileNotFoundError(f"Fichier source inexistant: {source_path}")
            link_path = sequence_dir / f"{sequence_name}_{index:04d}.fits"
            link_path.unlink(missing_ok=True)
            link_path.symlink_to(source_path)
            logging.debug("Lien créé: %s -> %s", link_path, source_path)
        logging.debug(
            "Séquence préparée: '%s' (%d fichiers)", sequence_name, len(source_files)
        )

    @staticmethod
    def _cleanup_sequence(work_dir: Path, sequence_name: str) -> None:
        """Nettoie un répertoire temporaire de séquence lorsqu'il existe."""
        sequence_dir = work_dir / sequence_name
        if sequence_dir.exists():
            try:
                shutil.rmtree(sequence_dir)
                logging.debug("Répertoire de séquence nettoyé: %s", sequence_dir)
            except OSError as error:
                logging.warning(
                    "Erreur lors du nettoyage de %s: %s", sequence_dir, error
                )

    def export_calibrated_outputs(
        self, sequence_name: str, calibrated_output_dir: Path
    ) -> list[Path]:
        """Exporte les FITS calibrés et préserve la séquence Siril associée.

        Les sorties sont copiées vers des fichiers temporaires avant remplacement
        pour éviter de laisser des FITS incomplets en cas d'interruption.
        """
        process_dir = self.work_dir / "process"
        if not process_dir.exists():
            logging.warning(
                "Export calibré impossible: répertoire process introuvable: %s",
                process_dir,
            )
            return []

        sources = sorted(
            list(process_dir.glob(f"pp_{sequence_name}_*.fits"))
            + list(process_dir.glob(f"pp_{sequence_name}_*.fit"))
        )
        if not sources:
            logging.warning(
                "Aucun fichier calibré pp_%s trouvé dans %s", sequence_name, process_dir
            )
            return []

        calibrated_output_dir.mkdir(parents=True, exist_ok=True)
        for stale_file in self.list_calibrated_outputs(calibrated_output_dir):
            stale_file.unlink()
        for name in (f"pp_{sequence_name}_.seq", f"pp_{sequence_name}.seq"):
            (calibrated_output_dir / name).unlink(missing_ok=True)

        for source_file in sources:
            destination = calibrated_output_dir / source_file.name
            temp_copy = destination.with_name(f"{destination.name}.tmp_export")
            if temp_copy.exists():
                temp_copy.unlink()
            shutil.copy2(source_file, temp_copy)
            temp_copy.rename(destination)

        self.save_calibrated_sequence(
            sequence_name, calibrated_output_dir, process_dir
        )
        logging.debug(
            "Sorties calibrées exportées depuis process/: %d fichier(s) vers %s",
            len(sources),
            calibrated_output_dir,
        )
        return self.list_calibrated_outputs(calibrated_output_dir)

    @staticmethod
    def list_calibrated_outputs(calibrated_output_dir: Path) -> list[Path]:
        """Liste les FITS calibrés avec le préfixe de sortie Siril attendu."""
        return sorted(
            list(calibrated_output_dir.glob("pp_*.fits"))
            + list(calibrated_output_dir.glob("pp_*.fit"))
        )

    @staticmethod
    def save_calibrated_sequence(
        sequence_name: str,
        output_dir: Path | str,
        source_dir: Path | str | None = None,
    ) -> Path:
        """Conserve une séquence Siril ou la reconstruit à partir des FITS exportés.

        Parameters
        ----------
        sequence_name : str
            Nom de la séquence Siril sans préfixe ``pp_``.
        output_dir : Path | str
            Répertoire des images calibrées exportées.
        source_dir : Path | str | None
            Répertoire Siril d'origine, si une séquence native y est disponible.

        Returns
        -------
        Path
            Chemin de la séquence conservée.

        Raises
        ------
        ValueError
            Si aucune image de la séquence n'est disponible ou si sa numérotation
            est ambiguë.
        """
        destination_dir = Path(output_dir)
        names = [f"pp_{sequence_name}_.seq", f"pp_{sequence_name}.seq"]
        if source_dir is not None:
            for name in names:
                source = Path(source_dir) / name
                if source.is_file():
                    destination = destination_dir / name
                    SirilSequence.read(source).write(destination)
                    logging.debug("Séquence calibrée conservée : %s", destination)
                    return destination

        prefix = f"pp_{sequence_name}_"
        pattern = re.compile(re.escape(prefix) + r"(\d+)\.(?:fit|fits)$")
        frames = []
        for path in destination_dir.iterdir():
            match = pattern.fullmatch(path.name)
            if match:
                frames.append((int(match[1]), len(match[1]), path))
        frames.sort()
        if not frames:
            raise ValueError(f"Aucun FITS calibré pour la séquence {sequence_name}")

        indices = [frame[0] for frame in frames]
        widths = {frame[1] for frame in frames}
        if len(set(indices)) != len(indices) or len(widths) != 1:
            raise ValueError("Numérotation ambiguë des FITS calibrés")

        layers = int(fits.getheader(frames[0][2]).get("NAXIS3", 1))
        destination = destination_dir / names[0]
        SirilSequence.from_files(
            destination, prefix, [frame[2] for frame in frames], layers=layers
        ).write()
        logging.debug("Séquence calibrée conservée : %s", destination)
        return destination

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare la recherche des darks et l'activation des darks et flats."""
        parser.add_argument(
            "-d",
            "--dark-lib",
            dest="dark_library_path",
            default=cls.CONFIG_DEFAULTS["dark_library_path"],
            help=(
                "Répertoire où sont stockés les master darks. "
                f"(Défaut: '{cls.CONFIG_DEFAULTS['dark_library_path']}')"
            ),
        )
        parser.add_argument(
            "--no-dark",
            dest="no_dark",
            action="store_true",
            default=False,
            help=(
                "Désactive l'utilisation des master darks "
                "(calibration sans soustraction de dark)"
            ),
        )
        parser.add_argument(
            "--flat",
            dest="use_flat",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Utiliser les flats compatibles ; --no-flat désactive leur calibration et application",
        )
        parser.add_argument(
            "-t",
            "--temperature-precision",
            dest="temperature_precision",
            type=float,
            default=cls.CONFIG_DEFAULTS["temperature_precision"],
            help=(
                "Pas d'arrondi pour le regroupement des températures et la "
                "recherche des master darks, en °C. "
                f"(Défaut : {cls.CONFIG_DEFAULTS['temperature_precision']} °C)"
            ),
        )

    @classmethod
    def parameter_children(cls) -> tuple[type[processor], ...]:
        """Déclare la création récursive du master flat optionnel."""
        return (MasterFlatProcessor,)

    def flat_cache_matches(self, calibrated_dir: Path) -> bool:
        """Vérifie que les sorties ont été calculées avec le même mode de flats.

        Les sorties historiques sans marqueur restent utilisables en mode
        normal. Désactiver les flats impose leur recalcul. Un marqueur illisible
        ou un changement de mode impose également une nouvelle calibration.
        """
        marker = calibrated_dir / "calibration_settings.json"
        try:
            settings = json.loads(marker.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self.use_flat
        except (OSError, ValueError):
            logging.warning("Réglages de calibration illisibles : %s", marker)
            return False
        return isinstance(settings, dict) and settings.get("use_flat") is self.use_flat

    def _generate_commands(
        self,
        sequence_name: str,
        dark_path: str | None,
        flat_path: str | None,
        stack_params: dict[str, ConfigValue],
    ) -> list[SirilCommand]:
        """Construit les appels API de conversion et calibration de la séquence."""
        process_dir = self.work_dir / "process"
        sequence_dir = self.work_dir / sequence_name
        title, message, calibration_command = self.calibration.build_light_preprocess(
            sequence_name, dark_path=dark_path, flat_path=flat_path)
        logging.info('%s — %s', title, message)
        calibration_call = SirilCommand.from_text(calibration_command)
        if stack_params.get('drizzle', 'off') != 'off':
            calibration_call = command(calibration_call.name,
                                       *(argument for argument in calibration_call.arguments if argument != '-debayer'))
        return [command('cd', sequence_dir),
                command('convert', sequence_name, f'-out={process_dir}'),
                command('cd', process_dir), calibration_call,
                command('cd', self.work_dir), command('close')]


    def post_process(
        self,
        input_path: Path,
        output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Calibre une séquence, en construisant d'abord son master flat si requis.

        Parameters
        ----------
        input_path : Path
            Première image light du groupe.
        output_path : Path
            Répertoire de destination des FITS calibrés et de leur séquence.
        args : argparse.Namespace | None
            Paramètres d'exécution du groupe : noms de séquences, liste des lights,
            masters dark, flats, durée de Drizzle, options Siril et mode simulation.

        Returns
        -------
        JSONReport
            État de l'étape, chemin du master flat éventuel et FITS exportés.

        Raises
        ------
        RuntimeError
            Si une séquence ne peut être calibrée ou si ses sorties sont absentes.
        """
        options = self._get_args(args)
        self._require_runtime_arguments(
            options,
            (
                "sequence_name",
                "group_key",
                "light_files",
                "master_dark_path",
                "flat_files",
                "flat_dark_path",
                "flat_sequence_name",
                "master_flat_output_base",
                "calibration_stage",
                "stack_params",
                "dry_run",
            ),
        )
        sequence_name = options.sequence_name
        group_key = options.group_key
        light_files = [Path(path) for path in options.light_files]
        dark_path = options.master_dark_path
        flat_files = (
            [Path(path) for path in options.flat_files] if self.use_flat else []
        )
        flat_dark_path = options.flat_dark_path
        flat_sequence_name = options.flat_sequence_name
        master_flat_base = Path(options.master_flat_output_base)
        dry_run = options.dry_run
        calibration_stage = options.calibration_stage
        stack_params = options.stack_params
        if not light_files:
            raise ValueError("La séquence de calibration ne contient aucun light")

        flat_path: Path | None = None
        if dry_run:
            if flat_files:
                logging.info(
                    "[DRY-RUN] === Création du master flat : %d flats — groupe %s ===",
                    len(flat_files), group_key,
                )
                if self.calibration.use_dark:
                    logging.info("[DRY-RUN] Dark pour calibrer les flats: %s", flat_dark_path)
                logging.info(
                    "[DRY-RUN] Master flat attendu: %s.fits (ou .fit)",
                    master_flat_base,
                )
                flat_path = Path(f"{master_flat_base}.fits")
            else:
                logging.info("[DRY-RUN] Aucun flat utilisé pour ce groupe")
            logging.info("[DRY-RUN] %s", calibration_stage)
            if dark_path:
                logging.info("[DRY-RUN] Master dark des lights : %s", dark_path)
            logging.info(
                "[DRY-RUN] Dossier de sortie calibrée attendu: %s "
                "(contiendra des fichiers .fit/.fits)",
                output_path,
            )
            operations = self._generate_commands(
                sequence_name, str(dark_path) if dark_path else None,
                str(flat_path) if flat_path else None, stack_params,
            )
            logging.debug("[DRY-RUN] Appels sirilpy prévus:")
            for index, operation in enumerate(operations, 1):
                logging.debug("[DRY-RUN]   %2d: %s", index, operation.render())
            return {
                "status": "dry_run",
                "input_image": str(input_path),
                "group_key": group_key,
                "outputs": [],
            }

        if self.calibration.use_dark and not dark_path:
            raise ValueError("dark_path est requis quand use_dark est activé")

        try:
            self._prepare_sequence(self.work_dir, sequence_name, light_files)
            if flat_files:
                logging.info(
                    "=== Création du master flat : %d flats — groupe %s ===",
                    len(flat_files), group_key,
                )
                if flat_dark_path:
                    logging.info("Master dark des flats : %s", flat_dark_path)
                flat_report = self.master_flat_processor.post_process(
                    flat_files[0],
                    master_flat_base,
                    args=argparse.Namespace(
                        sequence_name=flat_sequence_name,
                        flat_files=flat_files,
                        flat_dark_path=str(flat_dark_path) if flat_dark_path else None,
                        group_key=group_key,
                        dry_run=False,
                    ),
                )
                flat_path = Path(str(flat_report["output_image"]))
            else:
                logging.info("Master flat non utilisé : aucun flat compatible")

            process_dir = self.work_dir / "process"
            if process_dir.exists():
                try:
                    shutil.rmtree(process_dir)
                except OSError as error:
                    logging.warning(
                        "Impossible de nettoyer le répertoire de traitement %s: %s",
                        process_dir,
                        error,
                    )
            process_dir.mkdir(parents=True, exist_ok=True)
            operations = self._generate_commands(
                sequence_name,
                str(dark_path) if dark_path else None,
                str(flat_path) if flat_path else None,
                stack_params,
            )
            logging.info("%s", calibration_stage)
            if dark_path:
                logging.info("Master dark des lights : %s", dark_path)
            if flat_path:
                logging.info("Master flat des lights : %s", flat_path)
            if not self.siril.run_commands(
                operations,
                str(self.work_dir),
                name=f"calibrate_light_{group_key}",
            ):
                raise RuntimeError(
                    f"Échec du traitement complet de la séquence {sequence_name}"
                )
            outputs = self.export_calibrated_outputs(sequence_name, output_path)
            if not outputs:
                raise RuntimeError(
                    f"Aucune sortie calibrée trouvée dans le dossier: {output_path}"
                )
            (output_path / "calibration_settings.json").write_text(
                json.dumps({"use_flat": self.use_flat}), encoding="utf-8"
            )
            logging.info(
                "Calibration terminée : %d FITS calibrés dans %s",
                len(outputs), output_path,
            )
            return {
                "status": "ok",
                "input_image": str(input_path),
                "group_key": group_key,
                "master_flat": str(flat_path) if flat_path else None,
                "outputs": [str(path) for path in outputs],
            }
        finally:
            if self.keep_intermediate:
                logging.debug(
                    "Mode conservation activé: répertoires temporaires conservés "
                    "pour inspection (%s, %s)",
                    self.work_dir / sequence_name,
                    self.work_dir / flat_sequence_name,
                )
            else:
                self._cleanup_sequence(self.work_dir, sequence_name)
