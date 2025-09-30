#!/bin/env python3
import os
import sys
import datetime
import shutil
import logging
import argparse
from astropy.io import fits

# Add the parent directory to the path to import the lib module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.fits_info import FitsInfo
from lib.config import Config
from lib.siril_utils import run_siril_script

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
# --- Ajout du niveau USERINFO ---
USERINFO_LEVEL = 25
logging.addLevelName(USERINFO_LEVEL, "USERINFO")

def userinfo(self, message, *args, **kws):
    if self.isEnabledFor(USERINFO_LEVEL):
        self._log(USERINFO_LEVEL, message, args, **kws)
logging.Logger.userinfo = userinfo

class DarkLib:
    """
    Classe pour gérer une bibliothèque de master darks.
    Fournit des méthodes pour grouper, empiler et maintenir des master darks.
    """
    def __init__(self, config, siril_path="siril", siril_mode="flatpak"):
        """
        Initialise la bibliothèque de master darks avec les paramètres de configuration.
        """
        # Configuration
        self.dark_library_path = config.get("dark_library_path")
        self.work_dir = config.get("work_dir")
        self.siril_path = siril_path
        self.siril_mode = siril_mode
        self.siril_cfa = config.get("cfa", False)
        self.siril_output_norm = config.get("output_norm", "noscale")
        self.siril_rejection_method = config.get("rejection_method", "winsorizedsigma")
        self.siril_rejection_param1 = config.get("rejection_param1", 3.0)
        self.siril_rejection_param2 = config.get("rejection_param2", 3.0)
        self.siril_stack_method = config.get("stack_method", "average")
        self.max_age_days = config.get("max_age_days", 182)

        # Créer les répertoires nécessaires
        os.makedirs(self.dark_library_path, exist_ok=True)
        os.makedirs(self.work_dir, exist_ok=True)

    def group_dark_files(self, input_dirs: list[str], log_groups: bool = True, log_skipped: bool = False) -> dict[str, list[FitsInfo]]:
        """
        Groupe les fichiers dark par température, temps d'exposition, gain et nom de caméra.
        Ne conserve que les fichiers FITS les plus récents dans l'intervalle de temps spécifié.
        """
        dark_groups = {}
        skipped_files = []
        filtered_by_date = []  # Liste des fichiers filtrés par la date

        for input_dir in input_dirs:
            if not os.path.isdir(input_dir):
                logging.warning(f"Input directory not found: {input_dir}. Ignored.")
                continue

            logging.info(f"Scanning directory: {input_dir}")
            for root, _, files in os.walk(input_dir):
                for filename in files:
                    if filename.lower().endswith(('.fit', '.fits')):
                        filepath = os.path.join(root, filename)
                        info = FitsInfo(filepath)
                        group_key = None
                        if info.validData():
                            group_key = info.group_key()
                        if group_key and info.is_dark():
                            dark_groups.setdefault(group_key, []).append(info)
                        else:
                            skipped_files.append(filepath)

        # Tri des groupes par date décroissante et filtrage par intervalle de temps
        for key in list(dark_groups.keys()):
            infos = dark_groups[key]
            infos.sort(key=lambda x: x.date_obs(), reverse=True)
            if infos:
                latest_date = infos[0].date_obs()
                max_age = datetime.timedelta(days=self.max_age_days)
                filtered = [info for info in infos if (latest_date - info.date_obs()) <= max_age]
                removed = [info for info in infos if (latest_date - info.date_obs()) > max_age]
                dark_groups[key] = filtered
                if removed:
                    filtered_by_date.extend(removed)
                    logging.info(f"Fichiers filtrés par la date (>{self.max_age_days} jours du plus récent) pour le groupe {key}:")
                    for info in removed:
                        logging.info(f"  FILTERED: {info.filepath} | DATE-OBS={info.date_obs()}")

        # Affichage des groupes et fichiers
        if log_groups:
            for group_key, infos in dark_groups.items():
                logging.getLogger().userinfo(
                    f"GROUP: {group_key}"
                )
                for info in infos:
                    logging.getLogger().userinfo(
                        f"  FILE: {info.filepath} | DATE-OBS={info.date_obs()} | BINNING={info.binning()}"
                    )
        if log_skipped and skipped_files:
            logging.info("Fichiers ignorés (non conformes ou non DARK) :")
            for f in skipped_files:
                logging.info(f"  SKIPPED: {f}")

        return dark_groups

    def stack_and_save_master_dark(self, group_key: str, fitsinfo_list: list[FitsInfo], process_dir: str, link_dir: str) -> None:
        """
        Empile les darks d'un groupe en utilisant Siril et enregistre le master dark
        dans la bibliothèque, en gérant les remplacements selon la date.
        """
        # Récupérer l'objet FitsInfo avec la date la plus récente
        latest_infoFile: FitsInfo | None = None
        for info in fitsinfo_list:
            if info.validData():
                if latest_infoFile is None:
                    latest_infoFile = info
                else:
                    if latest_infoFile.is_equivalent(info):
                        if info.date_obs() > latest_infoFile.date_obs():
                            latest_infoFile = info
                    else:
                        logging.error(f"Inconsistent in group {group_key}. File {info.filepath} has GAIN={info.gain()}, CAMERA={info.camera()}. Skipping group.")
                        return
            else:
                logging.warning(f"Invalid FITS data in {info.filepath}, skipping for date comparison.")
        if latest_infoFile is None:
            logging.warning("No valid DATE-OBS found in group, skipping stacking.")
            return

        # Utilise directement group_key pour le nom du fichier
        os.makedirs(self.dark_library_path, exist_ok=True)
        master_dark_filename = f"{group_key}.fit"
        master_dark_path = os.path.join(self.dark_library_path, master_dark_filename)

        # Déplacer la génération de stack_line avant la vérification du master existant
        cfa_param = "-cfa" if self.siril_cfa else ""
        siril_output_name = "master_dark_temp.fit"

        # Mapping Python -> Siril
        SIRIL_REJECTION_METHOD_MAP = {
            "winsorizedsigma": "w",   # Siril attend "w" ou "winsorized"
            "sigma": "s",
            "minmax": "minmax",
            "percentile": "p",
            "none": "n"
        }
        siril_rejection_method = SIRIL_REJECTION_METHOD_MAP.get(self.siril_rejection_method, self.siril_rejection_method)

        if self.siril_stack_method == "average":
            if self.siril_rejection_method != "none":
                # Empilement par moyenne avec rejet
                stack_line = (
                    f"stack dark rej {siril_rejection_method} {self.siril_rejection_param1} {self.siril_rejection_param2} "
                    f"-norm={self.siril_output_norm} {cfa_param} -out={siril_output_name}"
                )
            else:
                # Empilement par moyenne sans rejet
                stack_line = (
                    f"stack dark rej n -norm={self.siril_output_norm} {cfa_param} -out={siril_output_name}"
                )
        else:
            # Empilement médian (ne prend pas de paramètres de rejet)
            stack_line = (
                f"stack dark median -norm={self.siril_output_norm} {cfa_param} -out={siril_output_name}"
            )

        # Mémoriser la ligne de commande pour la stocker dans l'en-tête
        stack_command = stack_line
        
        # --- Check for existing master dark for overwrite logic ---
        existing_master = None
        if os.path.exists(master_dark_path):
            info = FitsInfo(master_dark_path)
            if info.validData():
                existing_master = info
            else:
                logging.warning(f"Cannot read metadata from existing master dark {master_dark_path}. Will be treated as non-existent for comparison.")

        if existing_master:
            # Vérification de la commande de stacking si elle est disponible
            different_stack_cmd = (existing_master.stack_command() is None or 
                                stack_command != existing_master.stack_command())
            
            # Si la commande de stacking est différente, toujours remplacer
            if different_stack_cmd:
                logging.getLogger().userinfo(
                    f"Existing master dark for {group_key} has different stacking command. Replacing."
                )
                # Pas de 'return' ici pour permettre le remplacement
            # Si même commande mais date plus récente ou identique, ignorer
            elif latest_infoFile.date_obs() <= existing_master.date_obs():
                logging.getLogger().userinfo(
                    f"Master dark already exists and is newer or same date ({existing_master.date_obs().date()}). Update ignored."
                )
                return
            # Même commande mais plus ancien, on remplace
            else:
                logging.getLogger().userinfo(
                    f"Existing master dark for {group_key} is older ({existing_master.date_obs().date()}). Overwriting with newer darks from {latest_infoFile.date_obs().date()}."
                )
        else:
            logging.info(
                f"No master dark found for {group_key} or unreadable date. Creating new one."
            )

        # Les fichiers dark_files sont déjà des liens dans process_dir, nommés dark_XXXX.fit
        siril_file_list = [os.path.basename(info.filepath) for info in fitsinfo_list if os.path.exists(info.filepath)]

        if not siril_file_list:
            logging.warning(f"No dark files to stack for group {group_key}. Ignored.")
            return

        siril_script_content = f"""requires 1.2
# Siril script generated by Python to stack darks
cd "{link_dir}"
convert dark -out={process_dir}
cd {process_dir}
{stack_line}
"""
        if not run_siril_script(siril_script_content, process_dir, self.siril_path, self.siril_mode):
            logging.error(f"Erreur critique : l'exécution du script Siril a échoué pour le groupe {group_key}. Le répertoire de travail est conservé pour inspection : {process_dir}")
            exit(1)

        temp_master_dark_path = os.path.join(process_dir, siril_output_name)
        if os.path.exists(temp_master_dark_path):
            shutil.move(temp_master_dark_path, master_dark_path)
            logging.info(f"Master dark successfully created/updated: {master_dark_path}")
            masterDark=FitsInfo(master_dark_path)
            try:
                # Met à jour le header du master dark
                masterDark.set_ndarks(len(fitsinfo_list))
                masterDark.set_stack_command(stack_command)
                masterDark.update_header(latest_infoFile)
                logging.info(f"Header of {master_dark_path} updated with group metadata, stack command, and number of frames ({len(fitsinfo_list)}).")
            except Exception as e:
                logging.error(f"Failed to update FITS header for {master_dark_path}: {e}")
        else:
            logging.error(f"Siril script executed, but master dark '{siril_output_name}' not found in {process_dir}.")

CONFIG_FILE = os.path.expanduser("~/.siril_darklib_config.json")

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.warning(f"Could not read config file {CONFIG_FILE}: {e}")
    return {}

def save_config(config: dict) -> None:
    try:
        # Options de chemin
        if "dark_library_path" in config:
            config["dark_library_path"] = os.path.abspath(config["dark_library_path"])
        if "work_dir" in config:
            config["work_dir"] = os.path.abspath(config["work_dir"])
        if "siril_path" in config:
            config["siril_path"] = config["siril_path"]  # Peut rester relatif si souhaité
            
        # Options de stacking et de traitement
        for option in ["siril_mode", "cfa", "output_norm", "rejection_method", 
                      "rejection_param1", "rejection_param2", "max_age_days",
                      "stack_method"]:
            if option in config:
                # Enregistrer la valeur telle quelle
                pass
                
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        logging.info(f"Configuration saved to {CONFIG_FILE}")
    except Exception as e:
        logging.error(f"Could not save config file {CONFIG_FILE}: {e}")

class DarkLib:
    """
    Classe pour gérer une bibliothèque de master darks.
    Fournit des méthodes pour grouper, empiler et maintenir des master darks.
    """
    def __init__(self, config, siril_path="siril", siril_mode="flatpak"):
        """
        Initialise la bibliothèque de master darks avec les paramètres de configuration.
        """
        # Configuration
        self.dark_library_path = config.get("dark_library_path")
        self.work_dir = config.get("work_dir")
        self.siril_path = siril_path
        self.siril_mode = siril_mode
        self.siril_cfa = config.get("cfa", False)
        self.siril_output_norm = config.get("output_norm", "noscale")
        self.siril_rejection_method = config.get("rejection_method", "winsorizedsigma")
        self.siril_rejection_param1 = config.get("rejection_param1", 3.0)
        self.siril_rejection_param2 = config.get("rejection_param2", 3.0)
        self.siril_stack_method = config.get("stack_method", "average")
        self.max_age_days = config.get("max_age_days", 182)

        # Créer les répertoires nécessaires
        os.makedirs(self.dark_library_path, exist_ok=True)
        os.makedirs(self.work_dir, exist_ok=True)

    def group_dark_files(self, input_dirs: list[str], log_groups: bool = True, log_skipped: bool = False) -> dict[str, list[FitsInfo]]:
        """
        Groupe les fichiers dark par température, temps d'exposition, gain et nom de caméra.
        Ne conserve que les fichiers FITS les plus récents dans l'intervalle de temps spécifié.
        """
        dark_groups = {}
        skipped_files = []
        filtered_by_date = []  # Liste des fichiers filtrés par la date

        for input_dir in input_dirs:
            if not os.path.isdir(input_dir):
                logging.warning(f"Input directory not found: {input_dir}. Ignored.")
                continue

            logging.info(f"Scanning directory: {input_dir}")
            for root, _, files in os.walk(input_dir):
                for filename in files:
                    if filename.lower().endswith(('.fit', '.fits')):
                        filepath = os.path.join(root, filename)
                        info = FitsInfo(filepath)
                        group_key = None
                        if info.validData():
                            group_key = info.group_key()
                        if group_key and info.is_dark():
                            dark_groups.setdefault(group_key, []).append(info)
                        else:
                            skipped_files.append(filepath)

        # Tri des groupes par date décroissante et filtrage par intervalle de temps
        for key in list(dark_groups.keys()):
            infos = dark_groups[key]
            infos.sort(key=lambda x: x.date_obs(), reverse=True)
            if infos:
                latest_date = infos[0].date_obs()
                max_age = datetime.timedelta(days=self.max_age_days)
                filtered = [info for info in infos if (latest_date - info.date_obs()) <= max_age]
                removed = [info for info in infos if (latest_date - info.date_obs()) > max_age]
                dark_groups[key] = filtered
                if removed:
                    filtered_by_date.extend(removed)
                    logging.info(f"Fichiers filtrés par la date (>{self.max_age_days} jours du plus récent) pour le groupe {key}:")
                    for info in removed:
                        logging.info(f"  FILTERED: {info.filepath} | DATE-OBS={info.date_obs()}")

        # Affichage des groupes et fichiers
        if log_groups:
            for group_key, infos in dark_groups.items():
                logging.getLogger().userinfo(
                    f"GROUP: {group_key}"
                )
                for info in infos:
                    logging.getLogger().userinfo(
                        f"  FILE: {info.filepath} | DATE-OBS={info.date_obs()} | BINNING={info.binning()}"
                    )
        if log_skipped and skipped_files:
            logging.info("Fichiers ignorés (non conformes ou non DARK) :")
            for f in skipped_files:
                logging.info(f"  SKIPPED: {f}")

        return dark_groups

    def stack_and_save_master_dark(self, group_key: str, fitsinfo_list: list[FitsInfo], process_dir: str, link_dir: str) -> None:
        """
        Empile les darks d'un groupe en utilisant Siril et enregistre le master dark
        dans la bibliothèque, en gérant les remplacements selon la date.
        """
        # Récupérer l'objet FitsInfo avec la date la plus récente
        latest_infoFile: FitsInfo | None = None
        for info in fitsinfo_list:
            if info.validData():
                if latest_infoFile is None:
                    latest_infoFile = info
                else:
                    if latest_infoFile.is_equivalent(info):
                        if info.date_obs() > latest_infoFile.date_obs():
                            latest_infoFile = info
                    else:
                        logging.error(f"Inconsistent in group {group_key}. File {info.filepath} has GAIN={info.gain()}, CAMERA={info.camera()}. Skipping group.")
                        return
            else:
                logging.warning(f"Invalid FITS data in {info.filepath}, skipping for date comparison.")
        if latest_infoFile is None:
            logging.warning("No valid DATE-OBS found in group, skipping stacking.")
            return

        # Utilise directement group_key pour le nom du fichier
        os.makedirs(self.dark_library_path, exist_ok=True)
        master_dark_filename = f"{group_key}.fit"
        master_dark_path = os.path.join(self.dark_library_path, master_dark_filename)

        # Déplacer la génération de stack_line avant la vérification du master existant
        cfa_param = "-cfa" if self.siril_cfa else ""
        siril_output_name = "master_dark_temp.fit"

        # Mapping Python -> Siril
        SIRIL_REJECTION_METHOD_MAP = {
            "winsorizedsigma": "w",   # Siril attend "w" ou "winsorized"
            "sigma": "s",
            "minmax": "minmax",
            "percentile": "p",
            "none": "n"
        }
        siril_rejection_method = SIRIL_REJECTION_METHOD_MAP.get(self.siril_rejection_method, self.siril_rejection_method)

        if self.siril_stack_method == "average":
            if self.siril_rejection_method != "none":
                # Empilement par moyenne avec rejet
                stack_line = (
                    f"stack dark rej {siril_rejection_method} {self.siril_rejection_param1} {self.siril_rejection_param2} "
                    f"-norm={self.siril_output_norm} {cfa_param} -out={siril_output_name}"
                )
            else:
                # Empilement par moyenne sans rejet
                stack_line = (
                    f"stack dark rej n -norm={self.siril_output_norm} {cfa_param} -out={siril_output_name}"
                )
        else:
            # Empilement médian (ne prend pas de paramètres de rejet)
            stack_line = (
                f"stack dark median -norm={self.siril_output_norm} {cfa_param} -out={siril_output_name}"
            )

        # Mémoriser la ligne de commande pour la stocker dans l'en-tête
        stack_command = stack_line
        
        # --- Check for existing master dark for overwrite logic ---
        existing_master = None
        if os.path.exists(master_dark_path):
            info = FitsInfo(master_dark_path)
            if info.validData():
                existing_master = info
            else:
                logging.warning(f"Cannot read metadata from existing master dark {master_dark_path}. Will be treated as non-existent for comparison.")

        if existing_master:
            # Vérification de la commande de stacking si elle est disponible
            different_stack_cmd = (existing_master.stack_command() is None or 
                                stack_command != existing_master.stack_command())
            
            # Si la commande de stacking est différente, toujours remplacer
            if different_stack_cmd:
                logging.getLogger().userinfo(
                    f"Existing master dark for {group_key} has different stacking command. Replacing."
                )
                # Pas de 'return' ici pour permettre le remplacement
            # Si même commande mais date plus récente ou identique, ignorer
            elif latest_infoFile.date_obs() <= existing_master.date_obs():
                logging.getLogger().userinfo(
                    f"Master dark already exists and is newer or same date ({existing_master.date_obs().date()}). Update ignored."
                )
                return
            # Même commande mais plus ancien, on remplace
            else:
                logging.getLogger().userinfo(
                    f"Existing master dark for {group_key} is older ({existing_master.date_obs().date()}). Overwriting with newer darks from {latest_infoFile.date_obs().date()}."
                )
        else:
            logging.info(
                f"No master dark found for {group_key} or unreadable date. Creating new one."
            )

        # Les fichiers dark_files sont déjà des liens dans process_dir, nommés dark_XXXX.fit
        siril_file_list = [os.path.basename(info.filepath) for info in fitsinfo_list if os.path.exists(info.filepath)]

        if not siril_file_list:
            logging.warning(f"No dark files to stack for group {group_key}. Ignored.")
            return

        siril_script_content = f"""requires 1.2
# Siril script generated by Python to stack darks
cd "{link_dir}"
convert dark -out={process_dir}
cd {process_dir}
{stack_line}
"""
        if not run_siril_script(siril_script_content, process_dir, self.siril_path, self.siril_mode):
            logging.error(f"Erreur critique : l'exécution du script Siril a échoué pour le groupe {group_key}. Le répertoire de travail est conservé pour inspection : {process_dir}")
            exit(1)

        temp_master_dark_path = os.path.join(process_dir, siril_output_name)
        if os.path.exists(temp_master_dark_path):
            shutil.move(temp_master_dark_path, master_dark_path)
            logging.info(f"Master dark successfully created/updated: {master_dark_path}")
            masterDark=FitsInfo(master_dark_path)
            try:
                # Met à jour le header du master dark
                masterDark.set_ndarks(len(fitsinfo_list))
                masterDark.set_stack_command(stack_command)
                masterDark.update_header(latest_infoFile)
                logging.info(f"Header of {master_dark_path} updated with group metadata, stack command, and number of frames ({len(fitsinfo_list)}).")
            except Exception as e:
                logging.error(f"Failed to update FITS header for {master_dark_path}: {e}")
        else:
            logging.error(f"Siril script executed, but master dark '{siril_output_name}' not found in {process_dir}.")

    def read_existing_master_darks(self) -> list[FitsInfo]:
        """
        Parcourt le répertoire de la dark library et lit les entêtes FITS de chaque master dark.
        Retourne une liste d'objets FitsInfo représentant les master darks existants.
        """
        existing_darks = []
        if not os.path.isdir(self.dark_library_path):
            return existing_darks

        for fname in os.listdir(self.dark_library_path):
            if fname.lower().endswith(('.fit', '.fits')):
                fpath = os.path.join(self.dark_library_path, fname)
                try:
                    info = FitsInfo(fpath)
                    if info.validData() and info.is_dark():
                        existing_darks.append(info)
                except Exception as e:
                    logging.warning(f"Impossible de lire l'entête FITS de {fpath}: {e}")
        return existing_darks

    def list_master_darks(self) -> None:
        """
        Liste tous les master darks de la bibliothèque avec leurs caractéristiques
        lues directement depuis les en-têtes FITS.
        """
        logging.info(f"Lecture des master darks dans : {self.dark_library_path}")
        existing_darks = self.read_existing_master_darks()
        
        if not existing_darks:
            logging.info("Aucun master dark trouvé dans la bibliothèque.")
            return
        
        # Calculate base length (sum of fixed width columns and spaces)
        base_length = 25 + 1 + 10 + 1 + 10 + 1 + 8 + 1 + 10 + 1 + 20 + 1 + 8 + 1
        
        # Calculate the maximum length needed for the variable part (command and filename)
        max_variable_length = len("Commande/Fichier")  # Start with header length
        for dark in existing_darks:
            filename = os.path.basename(dark.filepath)
            stack_cmd = dark.stack_command() if hasattr(dark, 'stack_command_value') and dark.stack_command() else "N/A"
            
            # Update max_variable_length if either stack_cmd or filename is longer
            max_variable_length = max(max_variable_length, len(stack_cmd), len(filename) + 2)  # +2 for "→ "
        
        # Calculate total max line length
        max_line_length = base_length + max_variable_length
        
        # Create the separator based on the maximum line length
        separator = "-" * max_line_length
        
        # Affiche un en-tête pour le tableau avec colonne combinée
        header = "{:<25} {:<10} {:<10} {:<8} {:<10} {:<20} {:<8} {:<}".format(
            "Caméra", "Temp (°C)", "Exp (s)", "Gain", "Binning", "Date d'observation", "N darks", "Commande/Fichier"
        )
        
        print(f"\nListe des {len(existing_darks)} master darks disponibles :")
        print(separator)
        print(header)
        print(separator)
        
        for dark in sorted(existing_darks, key=lambda x: (x.camera(), x.temperature(), x.exptime())):
            # Format des valeurs pour l'affichage
            filename = os.path.basename(dark.filepath)
            date_str = dark.date_obs().strftime("%Y-%m-%d %H:%M:%S") if dark.date_obs() else "N/A"
            n_darks = dark.ndarks() if hasattr(dark, 'ndarks_value') and dark.ndarks() is not None else "N/A"
            
            # Récupérer la commande de stacking (ou N/A)
            stack_cmd = dark.stack_command() if hasattr(dark, 'stack_command_value') and dark.stack_command() else "N/A"
            
            # Ligne principale avec les infos et la commande de stacking
            main_row = "{:<25} {:<10.1f} {:<10.1f} {:<8.1f} {:<10} {:<20} {:<8} {:<}".format(
                dark.camera()[:24], 
                dark.temperature() if dark.temperature() is not None else float('nan'),
                dark.exptime() if dark.exptime() is not None else float('nan'),
                dark.gain() if dark.gain() is not None else float('nan'),
                dark.binning(),
                date_str,
                n_darks,
                stack_cmd
            )
            print(main_row)
            
            # Ligne secondaire avec le nom du fichier (avec indentation)
            file_row = "{:<25} {:<10} {:<10} {:<8} {:<10} {:<20} {:<8} → {}".format(
                "", "", "", "", "", "", "", filename
            )
            print(file_row)
        
        print(separator)
        print()  # Ligne vide à la fin pour améliorer la lisibilité

    def process_all_groups(self, dark_groups):
        """
        Traite tous les groupes de darks pour créer des master darks.
        """
        for group_key, files in dark_groups.items():
            logging.info(f"Processing group: {group_key}")
            if len(files) < 2:
                logging.warning(f"Group {group_key} contains only {len(files)} file(s). Stacking ignored (Siril requires at least 2).")
                continue

            # Utiliser un sous-répertoire 'process' dans WORK_DIR pour le traitement Siril
            process_dir = os.path.join(self.work_dir, "processs")
            link_dir = os.path.join(process_dir, "link")
            if os.path.exists(process_dir):
                shutil.rmtree(process_dir)
            os.makedirs(link_dir, exist_ok=True)

            # Lier les fichiers originaux dans le sous-répertoire 'link'
            linked_infos = []
            for i, info in enumerate(files):
                newInfo = info.create_symlink(link_dir, index=i)
                if newInfo:
                    linked_infos.append(newInfo)

            # Passer la liste des liens au stacking
            self.stack_and_save_master_dark(group_key, linked_infos, process_dir, link_dir)

            # Nettoyer le répertoire process après traitement
            shutil.rmtree(process_dir, ignore_errors=True)

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
        '--input-dirs',
        nargs='+',
        help="Liste des répertoires contenant les fichiers dark à traiter"
    )
    parser.add_argument(
        '--dark-library-path',
        type=str,
        default=config.get("dark_library_path"),
        help=f"Répertoire où sont stockés les master darks. (Défaut: '{config.get('dark_library_path')}')"
    )
    parser.add_argument(
        '--work-dir',
        type=str,
        default=config.get("work_dir"),
        help=f"Répertoire de travail temporaire. (Défaut: '{config.get('work_dir')}')"
    )
    parser.add_argument(
        '--siril-path',
        type=str,
        default=config.get("siril_path"),
        help=f"Chemin vers l'exécutable Siril. (Défaut: '{config.get('siril_path')}')"
    )
    parser.add_argument(
        '--siril-mode',
        choices=['native', 'flatpak', 'appimage'],
        default=config.get("siril_mode"),
        help=f"Mode d'exécution de Siril. (Défaut: '{config.get('siril_mode')}')"
    )
    parser.add_argument(
        '--save-config',
        action='store_true',
        help="Sauvegarde la configuration actuelle pour une utilisation future"
    )
    parser.add_argument(
        '--dummy',
        action='store_true',
        help="Mode test: analyse les fichiers mais n'exécute pas Siril"
    )

    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL', 'USERINFO'],
        default='INFO',
        help="Niveau de journalisation"
    )
    
    parser.add_argument(
        '--list-darks',
        action='store_true',
        help="Liste tous les master darks disponibles dans la bibliothèque avec leurs caractéristiques"
    )
    parser.add_argument(
        '--log-skipped',
        action='store_true',
        help="Log les fichiers ignorés (non-DARK ou FITS invalides)"
    )
    parser.add_argument(
        '--max-age',
        type=int,
        default=config.get("max_age_days"),
        help=f"Nombre maximum de jours d'écart entre le dark le plus récent et le plus ancien d'un groupe. (Défaut: {config.get('max_age_days')} jours)"
    )
    parser.add_argument(
        '--cfa',
        action='store_true',
        default=config.get("cfa", False),
        help="Indique que les images sont en couleur (CFA). Par défaut, les images sont considérées monochromes."
    )
    parser.add_argument(
        '--output-norm',
        choices=['addscale', 'noscale', 'rejection'],
        default=config.get("output_norm"),
        help=f"Méthode de normalisation pour Siril. (Défaut: '{config.get('output_norm')}')"
    )
    parser.add_argument(
        '--rejection-method',
        choices=['winsorizedsigma', 'sigma', 'minmax', 'percentile', 'none'],
        default=config.get("rejection_method"),
        help=f"Méthode de rejet pour Siril. (Défaut: '{config.get('rejection_method')}')"
    )
    parser.add_argument(
        '--rejection-param1',
        type=float,
        default=config.get("rejection_param1"),
        help=f"Premier paramètre de rejet pour Siril. (Défaut: {config.get('rejection_param1')})"
    )
    parser.add_argument(
        '--rejection-param2',
        type=float,
        default=config.get("rejection_param2"),
        help=f"Second paramètre de rejet pour Siril. (Défaut: {config.get('rejection_param2')})"
    )
    parser.add_argument(
        '--stack-method',
        choices=['average', 'median'],
        default=config.get("stack_method"),
        help=f"Méthode d'empilement: 'average' (Empilement par moyenne avec rejet) ou 'median' (Empilement médian). (Défaut: '{config.get('stack_method')}')"
    )


    # Code d'analyse des arguments inchangé...
    args = parser.parse_args()

    # Configuration de la journalisation
    log_level = getattr(logging, args.log_level) if args.log_level != 'USERINFO' else USERINFO_LEVEL
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
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
    darklib = DarkLib(config, SIRIL_PATH, SIRIL_MODE)
    
    # Si l'option --list-darks est spécifiée, liste les master darks et termine
    if args.list_darks:
        darklib.list_master_darks()
        return

    # Si l'option --input-dirs est présente traiter les darks
    if args.input_dirs:
        dark_groups = darklib.group_dark_files(
            args.input_dirs, 
            log_groups=True, 
            log_skipped=args.log_skipped
        )
    
        if not dark_groups:
            logging.info("No dark files found or processed. Script finished.")
            return

        logging.info(f"Found {len(dark_groups)} unique dark groups based on temperature, exposure time and gain.")

        # Arrêt anticipé si --dummy est activé
        if args.dummy:
            logging.info("Option --dummy activée : arrêt du script avant traitement Siril.")
            return

        # Traiter tous les groupes
        darklib.process_all_groups(dark_groups)
    
    logging.info("Siril dark library creation script completed.")

if __name__ == "__main__":
    main()

# End of darklib.py script