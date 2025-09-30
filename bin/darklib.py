#!/bin/env python3
import os
import subprocess
import datetime
import shutil
import tempfile
from astropy.io import fits
from astropy.time import Time
import logging
import argparse # Import argparse module
import json
import unicodedata
import re
import copy

# --- Configuration (Default values, can be overridden by command line) ---
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

# --- Script Functions ---
class FitsInfo:
    """
    Objet pour lire et accéder facilement aux champs d'un fichier FITS dark.
    """

    def __init__(self, filepath: str, log_level: int = logging.WARNING):
        self.filepath:str = filepath
        self.header = None
        self.valid:bool = False
        self.log_level = log_level
        self.fields = {}
        # Attributs pour accès direct
        self.date_obs_value:float = None
        self.rawdate_obs_value = None
        self.exptime_value:float= None
        self.temperature_value:float = None
        self.gain_value:float = None
        self.imagetyp_value:str = None
        self.camera_value:str = None
        self.xbinning_value = None
        self.ybinning_value = None
        self.ndarks_value = None
        self.history_values = []
        self.stack_command_value = None
        # Lecture des champs FITS
        self._read_header()

    def _log(self, msg: str, level: int = logging.INFO) -> None:
        if level >= self.log_level:
            logging.log(level, msg)

    def _read_header(self) -> None:
        try:
            with fits.open(self.filepath) as hdul:
                self.header = hdul[0].header
            
            # Auto-détection du mot-clé de température
            temp_value = None
            for keyword in ['CCD-TEMP', 'CCDTEMP', 'SET-TEMP', 'CCD_TEMP', 'SENSOR-TEMP', 'TEMP']:
                if keyword in self.header:
                    temp_value = self.header.get(keyword)
                    break
                    
            camera_value = (
                self.header.get('INSTRUME') or
                self.header.get('INSTRUMENT') or
                self.header.get('CAMERA', 'unknown')
            )

            # Attributs pour accès direct
            self.rawdate_obs_value = self.header.get('DATE-OBS')
            self.date_obs_value = self._parse_date(self.rawdate_obs_value)
            self.exptime_value = float(self.header.get('EXPTIME')) if self.header.get('EXPTIME') is not None else None
            self.temperature_value = float(temp_value) if temp_value is not None else None
            self.gain_value = float(self.header.get('GAIN')) if self.header.get('GAIN') is not None else None
            self.imagetyp_value = (self.header.get('IMAGETYP') or '').strip().lower() if self.header.get('IMAGETYP') else ''
            self.camera_value = self._normalize_camera_name(camera_value)
            
            # Lecture des champs NDARKS et HISTORY
            self.ndarks_value = self.header.get('NDARKS')
            
            # Pour HISTORY qui peut avoir plusieurs lignes, on les récupère toutes
            self.history_values = []
            if 'HISTORY' in self.header:
                if isinstance(self.header['HISTORY'], str):
                    self.history_values = [self.header['HISTORY']]
                else:
                    # Si multiple entrées HISTORY
                    self.history_values = self.header['HISTORY']

            # Lecture du binning (XBINNING/YBINNING ou BINNING)
            self.xbinning_value = int(self.header.get('XBINNING', 1))
            self.ybinning_value = int(self.header.get('YBINNING', 1))
            
            # Si XBINNING n'est pas disponible, essayez BINNING
            if 'XBINNING' not in self.header and 'BINNING' in self.header:
                binning = self.header.get('BINNING', '1x1')
                if isinstance(binning, str) and 'x' in binning:
                    parts = binning.split('x')
                    if len(parts) == 2:
                        self.xbinning_value = int(parts[0])
                        self.ybinning_value = int(parts[1])

            # Lecture du champ STACKCMD qui contient la commande de stacking
            self.stack_command_value = self.header.get('STACKCMD')

            # Validation stricte : tous les champs doivent être valides
            self.valid = (
                self.date_obs_value is not None and
                self.exptime_value is not None and
                self.temperature_value is not None and
                self.gain_value is not None and
                self.imagetyp_value != '' and
                self.camera_value not in (None, '', 'unknown') and
                self.xbinning_value is not None and
                self.ybinning_value is not None
            )

        except Exception as e:
            self._log(f"Erreur lecture FITS {self.filepath}: {e}", logging.WARNING)
            self.header = None
            self.valid = False
            self.date_obs_value = None
            self.exptime_value = None
            self.temperature_value = None
            self.gain_value = None
            self.imagetyp_value = None
            self.camera_value = None
            self.xbinning_value = None
            self.ybinning_value = None
            self.stack_command_value = None

    def _parse_date(self, date_obs_str: str) -> datetime.datetime | None:
        if not date_obs_str:
            return None
        try:
            return Time(date_obs_str, format='isot', scale='utc').to_datetime()
        except Exception:
            try:
                return Time(date_obs_str, format='fits', scale='utc').to_datetime()
            except Exception:
                self._log(f"Cannot parse DATE-OBS '{date_obs_str}' in {self.filepath}", logging.WARNING)
                return None

    def is_dark(self) -> bool:
        return "dark" in (self.imagetyp_value or '')

    def rawdate_obs(self) -> str | None:
        return self.rawdate_obs_value
    
    def date_obs(self) -> datetime.datetime | None:
        return self.date_obs_value

    def exptime(self) -> float | None:
        return self.exptime_value

    def temperature(self) -> float | None:
        return self.temperature_value

    def gain(self) -> float | None:
        return self.gain_value

    def camera(self) -> str:
        return self.camera_value

    def validData(self) -> bool:
        """
        Retourne True si tous les champs requis sont présents.
        """
        return self.valid
    
    def _normalize_camera_name(self, name: str) -> str:
        # Supprime les accents
        name = unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode()
        # Remplace tout caractère non alphanumérique par '_'
        name = re.sub(r'[^A-Za-z0-9]', '_', name)
        # Supprime les '_' à la fin
        name = name.rstrip('_')
        return name
    
    def binning(self) -> str:
        """
        Retourne le binning sous forme de chaîne "XxY".
        """
        return f"{self.xbinning_value}x{self.ybinning_value}"
    
    def binning_value(self) -> tuple:
        """
        Retourne le tuple (xbinning, ybinning).
        """
        return (self.xbinning_value, self.ybinning_value)

    def group_key(self) -> str:
        """
        Retourne la clé de groupement pour cet objet FitsInfo sous forme de chaîne.
        Format: "TEMP_EXPTIME_GAIN_CAMERA_BINNING"
        """
        if self.validData():
            rounded_temp = round(self.temperature())
            rounded_gain = round(self.gain())
            formatted_temp = str(rounded_temp)
            formatted_exp = str(int(self.exptime()))
            formatted_gain = str(rounded_gain)
            formatted_camera = self.camera()
            formatted_binning = self.binning()
            
            return f"{formatted_camera}_T{formatted_temp}_E{formatted_exp}_G{formatted_gain}_B{formatted_binning}"
        else:
            return None

    def is_equivalent(self, other: "FitsInfo") -> bool:
        """
        Compare tous les attributs (sauf le nom de fichier) avec un autre FitsInfo.
        Retourne True si tous les champs sont égaux, y compris la commande de stacking si disponible.
        """
        if not isinstance(other, FitsInfo):
            return False
        
        # Vérification de base par group_key
        if self.group_key() != other.group_key():
            return False
            
        # Vérification supplémentaire de la commande de stacking si disponible
        if (self.stack_command_value is not None and 
            other.stack_command_value is not None and
            self.stack_command_value != other.stack_command_value):
            return False
            
        return True


    def create_symlink(self, link_dir: str, index: int = None):
        """
        Crée un lien symbolique vers le fichier FITS dans link_dir.
        Si index est fourni, le nom du lien sera dark_{index:04d}.fit, sinon le nom d'origine.
        """
        if not os.path.exists(link_dir):
            os.makedirs(link_dir, exist_ok=True)
        if index is not None:
            link_name = f"dark_{index:04d}.fit"
        else:
            link_name = os.path.basename(self.filepath)
        link_path = os.path.join(link_dir, link_name)
        try:
            if os.path.exists(link_path):
                os.remove(link_path)
            os.symlink(os.path.abspath(self.filepath), link_path)
            return self.copy_with_filepath(link_path)
        except Exception as e:
            logging.warning(f"Impossible de créer le lien symbolique {link_path} -> {self.filepath}: {e}")
            return None

    def copy_with_filepath(self, new_filepath: str):
        """
        Retourne une copie de l'objet FitsInfo avec le filepath remplacé par new_filepath.
        Les autres attributs sont copiés sans relecture du FITS.
        """
        new_info = copy.copy(self)
        new_info.filepath = new_filepath
        return new_info

    def update_header(self, source_info: "FitsInfo" = None) -> None:
        """
        Met à jour l'entête FITS du fichier associé à cette instance (self.filepath)
        avec les données d'un autre FitsInfo (source_info).
        Si source_info est None, utilise les données de l'instance courante.
        Si ndarks_value est défini, ajoute cette information à l'en-tête.
        Log les différences et met à jour l'en-tête si nécessaire.
        En cas d'erreur, log l'erreur et relance l'exception.
        """
        # Si aucune source fournie, utiliser self comme source
        if source_info is None:
            source_info = self
            
        try:
            with fits.open(self.filepath, mode='update') as hdul:
                header = hdul[0].header
                updates = {
                    'DATE-OBS': source_info.rawdate_obs(),
                    'EXPTIME': source_info.exptime(),
                    'CCD-TEMP': source_info.temperature(),
                    'GAIN': source_info.gain(),
                    'CAMERA': source_info.camera(),
                    'XBINNING': source_info.xbinning_value,
                    'YBINNING': source_info.ybinning_value,
                    'BINNING': source_info.binning()
                }
                
                # Ajouter le nombre de darks utilisés si défini
                if self.ndarks_value is not None:
                    updates['NDARKS'] = self.ndarks_value
                    updates['HISTORY'] = f"Master dark created from {self.ndarks_value} frames"
                
                # Ajouter la commande de stacking si définie
                if self.stack_command_value is not None:
                    updates['STACKCMD'] = self.stack_command_value
                
                for key, value in updates.items():
                    old_value = header.get(key)
                    if old_value != value:
                        logging.info(f"Updating {key}: {old_value} -> {value} in {self.filepath}")
                        header[key] = value
                hdul.flush()
        except Exception as e:
            logging.error(f"Failed to update FITS header for {self.filepath}: {e}")
            raise

    def set_date_obs(self, value: str | datetime.datetime) -> None:
        self.rawdate_obs_value = value if isinstance(value, str) else value.isoformat()
        self.date_obs_value = self._parse_date(self.rawdate_obs_value)

    def set_exptime(self, value: float) -> None:
        self.exptime_value = float(value)

    def set_temperature(self, value: float) -> None:
        self.temperature_value = float(value)

    def set_gain(self, value: float) -> None:
        self.gain_value = float(value)

    def set_camera(self, value: str) -> None:
        self.camera_value = self._normalize_camera_name(value)

    def set_ndarks(self, value: int) -> None:
        """
        Définit le nombre de darks utilisés pour créer ce master dark.
        """
        self.ndarks_value = value

    def ndarks(self) -> int | None:
        """
        Retourne le nombre de darks utilisés pour créer ce master dark.
        """
        return self.ndarks_value

    def history(self) -> list[str]:
        """
        Retourne l'historique du fichier FITS.
        """
        return self.history_values

    def stack_command(self) -> str | None:
        """
        Retourne la commande de stacking utilisée pour créer ce master dark.
        """
        return self.stack_command_value
    
    def set_stack_command(self, value: str) -> None:
        """
        Définit la commande de stacking utilisée pour créer ce master dark.
        """
        self.stack_command_value = value

class Config:
    """
    Classe pour charger, sauvegarder et accéder à la configuration du script.
    Gère la persistance des paramètres dans un fichier JSON.
    """
    # Valeurs par défaut pour chaque paramètre de configuration
    DEFAULTS = {
        "siril_path": "siril",
        "dark_library_path": os.path.expanduser("~/darkLib"),
        "work_dir": os.path.expanduser("~/tmp/sirilWorkDir"),
        "siril_mode": "flatpak",
        "cfa": False,
        "output_norm": "noscale",
        "rejection_method": "winsorizedsigma",
        "rejection_param1": 3.0,
        "rejection_param2": 3.0,
        "max_age_days": 182,
        "stack_method": "average"
    }
    
    def __init__(self, config_file=None):
        """
        Initialise la configuration à partir d'un fichier.
        Si le fichier n'est pas spécifié, utilise le chemin par défaut ~/.siril_darklib_config.json
        """
        self.config_file = config_file or os.path.expanduser("~/.siril_darklib_config.json")
        self._config = {}
        self.load()
    
    def load(self):
        """
        Charge la configuration depuis le fichier.
        Si le fichier n'existe pas ou est invalide, utilise les valeurs par défaut.
        """
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r") as f:
                    self._config = json.load(f)
                logging.info(f"Configuration chargée depuis {self.config_file}")
            except Exception as e:
                logging.warning(f"Erreur lors du chargement de la configuration: {e}")
                self._config = {}
        else:
            logging.info(f"Fichier de configuration {self.config_file} inexistant, utilisation des valeurs par défaut")
            self._config = {}
    
    def save(self):
        """
        Sauvegarde la configuration dans le fichier.
        Normalise les chemins avant la sauvegarde.
        """
        try:
            # Normaliser les chemins
            if "dark_library_path" in self._config:
                self._config["dark_library_path"] = os.path.abspath(self._config["dark_library_path"])
            if "work_dir" in self._config:
                self._config["work_dir"] = os.path.abspath(self._config["work_dir"])
            
            with open(self.config_file, "w") as f:
                json.dump(self._config, f, indent=2)
            logging.info(f"Configuration sauvegardée dans {self.config_file}")
            return True
        except Exception as e:
            logging.error(f"Erreur lors de la sauvegarde de la configuration: {e}")
            return False
    
    def get(self, key, default=None):
        """
        Récupère une valeur de configuration.
        Si la clé n'existe pas, renvoie la valeur par défaut spécifiée ou celle définie dans DEFAULTS.
        """
        if default is None and key in self.DEFAULTS:
            default = self.DEFAULTS[key]
        return self._config.get(key, default)
    
    def set(self, key, value):
        """
        Définit une valeur de configuration.
        """
        self._config[key] = value
    
    def update(self, **kwargs):
        """
        Met à jour plusieurs valeurs de configuration en une seule fois.
        """
        self._config.update(kwargs)
    
    def to_dict(self):
        """
        Retourne la configuration sous forme de dictionnaire.
        """
        return dict(self._config)
    
    def set_from_args(self, args):
        """
        Met à jour la configuration à partir des arguments de la ligne de commande.
        """
        # Mise à jour des valeurs à partir des arguments
        updates = {
            "siril_path": args.siril_path,
            "dark_library_path": args.dark_library_path,
            "work_dir": args.work_dir,
            "siril_mode": args.siril_mode,
            "cfa": args.cfa,
            "output_norm": args.output_norm,
            "rejection_method": args.rejection_method,
            "rejection_param1": args.rejection_param1,
            "rejection_param2": args.rejection_param2,
            "max_age_days": args.max_age,
            "stack_method": args.stack_method
        }
        self.update(**updates)

# Standalone group_dark_files function has been removed.
# Use DarkLib.group_dark_files() method instead.

def run_siril_script(siril_script_content: str, working_dir: str) -> bool:
    """
    Executes a temporary Siril script.
    Uses the global variable SIRIL_PATH and SIRIL_MODE.
    """
    global SIRIL_PATH, SIRIL_MODE
    script_path = os.path.join(working_dir, "siril_script.sps")
    try:
        with open(script_path, "w") as f:
            f.write(siril_script_content)

        logging.info(f"Executing Siril script {script_path} in {working_dir}:\n{siril_script_content}")

        # --- Build command based on SIRIL_MODE ---
        if SIRIL_MODE == "native":
            cmd = [SIRIL_PATH, "-s", script_path]
        elif SIRIL_MODE == "flatpak":
            cmd = ["flatpak", "run", "org.siril.Siril", "-s", script_path]
        elif SIRIL_MODE == "appimage":
            cmd = [SIRIL_PATH, "-s", script_path]
        else:
            logging.error(f"Unknown SIRIL_MODE: {SIRIL_MODE}")
            return False

        result = subprocess.run(
            cmd,
            cwd=working_dir,
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode != 0:
            logging.error(f"Siril script failed with error code {result.returncode}.")
            logging.error(f"Siril stdout:\n{result.stdout}")
            logging.error(f"Siril stderr:\n{result.stderr}")
            return False
        else:
            logging.info("Siril script executed successfully.")
            logging.debug(f"Siril stdout:\n{result.stdout}")
            return True
    except FileNotFoundError:
        logging.error(f"Siril executable not found at '{SIRIL_PATH}'. Please check the path.")
        return False
    except Exception as e:
        logging.error(f"Error executing Siril script: {e}")
        return False
    finally:
        if os.path.exists(script_path):
            os.remove(script_path) # Clean up temporary script

# Standalone stack_and_save_master_dark function has been removed.
# Use DarkLib.stack_and_save_master_dark() method instead.

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

# Import DarkLib class from darklib_obj module
from darklib_obj import DarkLib

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
        '--create-dummy-data',
        action='store_true',
        help="Crée des fichiers FITS factices pour tester le script"
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
    
    input_dirs_to_use = args.input_dirs

    # Handle dummy data creation
    if args.create_dummy_data:
        logging.info("Creating dummy FITS files for demonstration...")

        # Nettoyer les anciens fichiers dans le répertoire de travail
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir)
        os.makedirs(work_dir, exist_ok=True)

        # Créer deux sous-répertoires dans le répertoire de travail pour simuler deux sessions
        session1 = os.path.join(work_dir, "darks_session1")
        session2 = os.path.join(work_dir, "darks_session2")
        os.makedirs(session1, exist_ok=True)
        os.makedirs(session2, exist_ok=True)

        # Fonction pour créer un dummy FITS
        def create_dummy_fits(path, date_obs, exptime, ccd_temp, imagetyp='DARK'):
            hdu = fits.PrimaryHDU()
            hdu.header['DATE-OBS'] = date_obs.isoformat(timespec='seconds')
            hdu.header['EXPTIME'] = exptime
            hdu.header['CCD-TEMP'] = ccd_temp
            hdu.header['IMAGETYP'] = imagetyp
            hdu.writeto(path, overwrite=True)

        # Création des fichiers dummy dans le répertoire de travail
        create_dummy_fits(os.path.join(session1, "dark_27_1.fit"), datetime.datetime(2023, 10, 27, 20, 0, 0), 300, -15)
        create_dummy_fits(os.path.join(session1, "dark_27_2.fit"), datetime.datetime(2023, 10, 27, 20, 5, 0), 300, -15)
        create_dummy_fits(os.path.join(session1, "dark_27_3.fit"), datetime.datetime(2023, 10, 27, 20, 10, 0), 300, -15)
        create_dummy_fits(os.path.join(session1, "dark_27_4.fit"), datetime.datetime(2023, 10, 27, 21, 0, 0), 600, -15)
        create_dummy_fits(os.path.join(session1, "dark_27_5.fit"), datetime.datetime(2023, 10, 27, 21, 10, 0), 600, -15)
        create_dummy_fits(os.path.join(session2, "dark_28_1.fit"), datetime.datetime(2023, 10, 28, 19, 0, 0), 300, -15)
        create_dummy_fits(os.path.join(session2, "dark_28_2.fit"), datetime.datetime(2023, 10, 28, 19, 5, 0), 300, -15)
        create_dummy_fits(os.path.join(session2, "dark_28_3.fit"), datetime.datetime(2023, 10, 28, 20, 0, 0), 300, -10)
        create_dummy_fits(os.path.join(session2, "dark_28_4.fit"), datetime.datetime(2023, 10, 28, 20, 5, 0), 300, -10)
        create_dummy_fits(os.path.join(session1, "dark_single.fit"), datetime.datetime(2023, 10, 29, 20, 0, 0), 300, -15)
        create_dummy_fits(os.path.join(session1, "light_frame.fit"), datetime.datetime(2023, 10, 29, 21, 0, 0), 120, -15, imagetyp='LIGHT')
        create_dummy_fits(os.path.join(session1, "dark_27_6.fit"), datetime.datetime(2023, 10, 27, 20, 15, 0), 300, -15)
        create_dummy_fits(os.path.join(session1, "dark_27_7.fit"), datetime.datetime(2023, 10, 27, 20, 20, 0), 300, -15)
        create_dummy_fits(os.path.join(session1, "dark_27_8.fit"), datetime.datetime(2023, 10, 27, 20, 25, 0), 300, -15)
        logging.info("Dummy FITS files created.")

        # Si --create-dummy-data est utilisé sans --input-dirs, utiliser les sous-répertoires du workdir comme entrées
        if not input_dirs_to_use:
            logging.info("Using dummy data directories as inputs.")
            input_dirs_to_use = [session1, session2]



    logging.info("Starting Siril dark library creation script.")

    os.makedirs(DARK_LIBRARY_PATH, exist_ok=True)
    
    # Créer l'instance DarkLib
    darklib = DarkLib(config)
    
    # Si l'option --list-darks est spécifiée, liste les master darks et termine
    if args.list_darks:
        darklib.list_master_darks()
        return
    
    # Regrouper les darks
    dark_groups = darklib.group_dark_files(
        input_dirs_to_use, 
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