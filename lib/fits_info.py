#!/bin/env python3
import os
import datetime
import logging
import unicodedata
import re
import copy
from astropy.io import fits
from astropy.time import Time


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

    def is_bias(self) -> bool:
        return "bias" in (self.imagetyp_value or '')

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
