#!/bin/env python3
import os
import datetime
import logging
import unicodedata
import re
import copy
import numpy as np
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

    def group_key(self, temperature_precision: float = 0.5) -> str:
        """
        Retourne la clé de groupement pour cet objet FitsInfo sous forme de chaîne.
        Format: "TEMP_EXPTIME_GAIN_CAMERA_BINNING"
        
        Args:
            temperature_precision: Précision d'arrondi pour la température (par défaut 0.2°C)
        """
        if self.validData():
            rounded_temp = round(round(self.temperature() / temperature_precision) * temperature_precision, 1)
            rounded_gain = round(self.gain())
            formatted_temp = str(rounded_temp)
            formatted_exp = str(int(self.exptime()))
            formatted_gain = str(rounded_gain)
            formatted_camera = self.camera()
            formatted_binning = self.binning()
            
            return f"{formatted_camera}_T{formatted_temp}_E{formatted_exp}_G{formatted_gain}_B{formatted_binning}"
        else:
            return None

    def is_equivalent(self, other: "FitsInfo", temperature_precision: float = 0.2) -> bool:
        """
        Compare tous les attributs (sauf le nom de fichier) avec un autre FitsInfo.
        Retourne True si tous les champs sont égaux, y compris la commande de stacking si disponible.
        
        Args:
            temperature_precision: Précision d'arrondi pour la température (par défaut 0.2°C)
        """
        if not isinstance(other, FitsInfo):
            return False
        
        # Vérification de base par group_key
        if self.group_key(temperature_precision) != other.group_key(temperature_precision):
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

    def update_header(self, source_info: "FitsInfo" = None, temperature_precision: float = 0.2) -> None:
        """
        Met à jour l'entête FITS du fichier associé à cette instance (self.filepath)
        avec les données d'un autre FitsInfo (source_info).
        Si source_info est None, utilise les données de l'instance courante.
        Si ndarks_value est défini, ajoute cette information à l'en-tête.
        Log les différences et met à jour l'en-tête si nécessaire.
        En cas d'erreur, log l'erreur et relance l'exception.
        
        Args:
            source_info: FitsInfo source pour les métadonnées (None = utiliser self)
            temperature_precision: Précision d'arrondi pour la température (par défaut 0.2°C)
        """
        # Si aucune source fournie, utiliser self comme source
        if source_info is None:
            source_info = self
            
        # Calculer la température arrondie selon la précision configurée
        rounded_temp = round(round(source_info.temperature() / temperature_precision) * temperature_precision, 1)
            
        try:
            with fits.open(self.filepath, mode='update') as hdul:
                header = hdul[0].header
                updates = {
                    'DATE-OBS': source_info.rawdate_obs(),
                    'EXPTIME': source_info.exptime(),
                    'CCD-TEMP': rounded_temp,
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

    def analyze_image_statistics(self) -> dict:
        """
        Analyse les statistiques de l'image FITS pour détecter des anomalies.
        Retourne un dictionnaire avec les statistiques clés.
        
        Returns:
            dict: Statistiques de l'image (médiane, écart-type, percentiles, etc.)
        """
        try:
            with fits.open(self.filepath) as hdul:
                data = hdul[0].data
                if data is None:
                    logging.warning(f"No image data found in {self.filepath}")
                    return None
                
                # Convertir en float pour éviter les débordements
                data = data.astype(np.float64)
                
                # Calculer les statistiques de base
                stats = {
                    'median': float(np.median(data)),
                    'mean': float(np.mean(data)),
                    'std': float(np.std(data)),
                    'min': float(np.min(data)),
                    'max': float(np.max(data)),
                    'p10': float(np.percentile(data, 10)),
                    'p25': float(np.percentile(data, 25)),
                    'p75': float(np.percentile(data, 75)),
                    'p90': float(np.percentile(data, 90)),
                    'p95': float(np.percentile(data, 95)),
                    'p99': float(np.percentile(data, 99)),
                    'pixels_total': int(data.size)
                }
                
                # Calculer la MAD (Median Absolute Deviation) - statistique robuste
                mad = float(np.median(np.abs(data - stats['median'])))
                stats['mad'] = mad
                
                # Calculer l'IQR (Interquartile Range) - autre mesure robuste de dispersion
                iqr = stats['p75'] - stats['p25']
                stats['iqr'] = float(iqr)
                
                # Calculer les ratios robustes pour validation
                if stats['median'] > 0:
                    stats['mad_ratio'] = float(mad / stats['median'])  # Bruit relatif robuste
                    stats['central_dispersion'] = float((stats['p90'] - stats['p10']) / stats['median'])  # Dispersion centrale robuste
                else:
                    stats['mad_ratio'] = 0.0
                    stats['central_dispersion'] = 0.0
                
                # Calculer le pourcentage de pixels "chauds" basé sur mean + n×std (méthode classique)
                # Seuil : mean + 3×std (détection standard des outliers en traitement d'image)
                hot_threshold_std = stats['mean'] + 3 * stats['std']
                hot_pixels_std = np.sum(data > hot_threshold_std)
                stats['hot_pixels_count_std'] = int(hot_pixels_std)
                stats['hot_pixels_percent_std'] = float(hot_pixels_std / data.size * 100)
                
                # Alternative plus stricte : mean + 4×std  
                hot_threshold_4std = stats['mean'] + 4 * stats['std']
                hot_pixels_4std = np.sum(data > hot_threshold_4std)
                stats['hot_pixels_count_4std'] = int(hot_pixels_4std)
                stats['hot_pixels_percent_4std'] = float(hot_pixels_4std / data.size * 100)
                
                # Seuil basé sur IQR pour comparaison (méthode robuste alternative)
                hot_threshold_iqr = stats['p75'] + 1.5 * iqr
                hot_pixels_iqr = np.sum(data > hot_threshold_iqr)
                stats['hot_pixels_count_iqr'] = int(hot_pixels_iqr)
                stats['hot_pixels_percent_iqr'] = float(hot_pixels_iqr / data.size * 100)
                
                # Ancien calcul basé sur median + 5×std pour compatibilité
                hot_threshold = stats['median'] + 5 * stats['std']
                hot_pixels = np.sum(data > hot_threshold)
                stats['hot_pixels_count'] = int(hot_pixels)
                stats['hot_pixels_percent'] = float(hot_pixels / data.size * 100)
                
                return stats
                
        except Exception as e:
            logging.error(f"Error analyzing image statistics for {self.filepath}: {e}")
            return None

    def calculate_plane_regression(self, data: np.ndarray, sample_fraction: float = 0.3) -> dict:
        """
        Calcule une régression plane sur l'image pour détecter des gradients d'illumination.
        
        Args:
            data: Données de l'image 2D
            sample_fraction: Fraction de pixels à échantillonner pour le calcul (défaut: 10%)
            
        Returns:
            dict: Coefficients de la régression plane et statistiques
        """
        try:
            height, width = data.shape
            
            # Échantillonnage aléatoire pour accélérer le calcul sur de grandes images
            n_samples = int(data.size * sample_fraction)
            if n_samples > 10000:  # Limite raisonnable
                n_samples = 10000
                
            # Créer les grilles de coordonnées
            y_coords, x_coords = np.mgrid[0:height, 0:width]
            
            # Échantillonnage aléatoire
            if n_samples < data.size:
                indices = np.random.choice(data.size, n_samples, replace=False)
                x_flat = x_coords.flat[indices]
                y_flat = y_coords.flat[indices]
                z_flat = data.flat[indices]
            else:
                x_flat = x_coords.flatten()
                y_flat = y_coords.flatten()
                z_flat = data.flatten()
            
            # Normaliser les coordonnées pour améliorer la stabilité numérique
            x_norm = (x_flat - width/2) / width
            y_norm = (y_flat - height/2) / height
            
            # Construire la matrice pour la régression plane: z = a*x + b*y + c
            A = np.column_stack([x_norm, y_norm, np.ones(len(x_norm))])
            
            # Résolution par moindres carrés
            coeffs, residuals, rank, s = np.linalg.lstsq(A, z_flat, rcond=None)
            
            # Calculer les statistiques
            if len(residuals) > 0:
                mse = residuals[0] / len(z_flat)
                rmse = np.sqrt(mse)
            else:
                # Calcul manuel si residuals est vide
                z_pred = A @ coeffs
                mse = np.mean((z_flat - z_pred) ** 2)
                rmse = np.sqrt(mse)
            
            # Gradients en ADU par pixel
            x_gradient = coeffs[0] * width  # Coefficient X dénormalisé
            y_gradient = coeffs[1] * height  # Coefficient Y dénormalisé
            
            return {
                'x_coefficient': float(coeffs[0]),  # Normalisé
                'y_coefficient': float(coeffs[1]),  # Normalisé
                'constant': float(coeffs[2]),
                'x_gradient_adu_per_pixel': float(x_gradient),
                'y_gradient_adu_per_pixel': float(y_gradient),
                'gradient_magnitude': float(np.sqrt(x_gradient**2 + y_gradient**2)),
                'rmse': float(rmse),
                'r_squared': float(1 - (mse / np.var(z_flat))) if np.var(z_flat) > 0 else 0.0,
                'samples_used': len(z_flat)
            }
            
        except Exception as e:
            logging.error(f"Error calculating plane regression: {e}")
            return None

    def is_valid_dark(self, 
                     max_median_adu: float = 200.0,
                     max_hot_pixels_percent: float = 0.2,
                     max_mad_factor: float = 0.15,  # MAD/median pour bruit relatif robuste
                     max_central_dispersion: float = 0.4) -> tuple[bool, str]:  # (p90-p10)/median pour dispersion centrale
        """
        Vérifie si l'image est un dark valide (capot fermé) en analysant ses statistiques.
        
        Args:
            max_median_adu: Médiane maximale acceptable (ADU)
            max_hot_pixels_percent: Pourcentage maximal de pixels chauds acceptables (mean + 3×std)
            max_mad_factor: Facteur maximal acceptable pour MAD/median (bruit relatif robuste)
            max_central_dispersion: Facteur maximal pour (p90-p10)/median (dispersion centrale robuste)
            
        Returns:
            tuple: (is_valid, reason) - True si valide, sinon False avec raison
        """
        if not self.is_dark():
            return False, "Not a dark frame"
            
        stats = self.analyze_image_statistics()
        if stats is None:
            return False, "Cannot analyze image statistics"
            
        # Test 1: Médiane trop élevée (probable lumière résiduelle)
        if stats['median'] > max_median_adu:
            return False, f"Median too high: {stats['median']:.1f} > {max_median_adu} ADU (probable light leak)"
            
        # Test 2: Trop de pixels chauds (étoiles ou lumière) - utilise mean + 3×std
        if stats['hot_pixels_percent_std'] > max_hot_pixels_percent:
            return False, f"Too many hot pixels: {stats['hot_pixels_percent_std']:.2f}% > {max_hot_pixels_percent}% (probable stars/light)"
            
        # Test 3: Bruit relatif robuste - MAD/median
        if stats['median'] > 0 and stats['mad'] > 0:
            mad_ratio = stats['mad'] / stats['median']
            if mad_ratio > max_mad_factor:
                return False, f"High relative noise: MAD/median = {mad_ratio:.3f} > {max_mad_factor} (non-uniform illumination or gradient)"
            
        # Test 4: Dispersion centrale robuste - (p90-p10)/median
        if stats['median'] > 0:
            central_dispersion = (stats['p90'] - stats['p10']) / stats['median']
            if central_dispersion > max_central_dispersion:
                return False, f"High central dispersion: (p90-p10)/median = {central_dispersion:.3f} > {max_central_dispersion} (variable illumination)"
            
        return True, "Valid dark frame"

    def get_validation_report(self) -> dict:
        """
        Génère un rapport complet de validation du dark.
        
        Returns:
            dict: Rapport contenant les statistiques et le résultat de validation
        """
        stats = self.analyze_image_statistics()
        if stats is None:
            return {
                'filepath': self.filepath,
                'is_valid': False,
                'reason': 'Cannot analyze image',
                'statistics': None
            }
            
        is_valid, reason = self.is_valid_dark()
        
        return {
            'filepath': self.filepath,
            'is_valid': is_valid,
            'reason': reason,
            'statistics': stats,
            'exposure_time': self.exptime(),
            'temperature': self.temperature(),
            'camera': self.camera()
        }
