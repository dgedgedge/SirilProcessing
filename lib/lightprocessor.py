"""
Module pour le traitement automatique des images light.

Ce module contient la classe LightProcessor qui gère :
- La détection des images light dans un répertoire
- La recherche du master dark correspondant
- Le prétraitement (soustraction du dark)
- Le stacking des images prétraitées
"""

import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import glob
import shutil
import os
import math
import re
import numpy as np
from astropy.io import fits
from PIL import Image
from lib.fits_info import FitsInfo
from lib.siril_utils import Siril


def save_calibrated_sequence(sequence_name, output_dir, source_dir=None):
    """Preserve Siril's sequence, or describe the exported FITS when none exists."""
    output_dir = Path(output_dir)
    names = [f'pp_{sequence_name}_.seq', f'pp_{sequence_name}.seq']
    if source_dir is not None:
        for name in names:
            source = Path(source_dir) / name
            if source.is_file():
                destination = output_dir / name
                temporary = destination.with_suffix('.seq.tmp')
                shutil.copy2(source, temporary)
                temporary.replace(destination)
                logging.info('Séquence calibrée conservée : %s', destination)
                return destination
    prefix = f'pp_{sequence_name}_'
    pattern = re.compile(re.escape(prefix) + r'(\d+)\.(?:fit|fits)$')
    frames = []
    for path in output_dir.iterdir():
        match = pattern.fullmatch(path.name)
        if match:
            frames.append((int(match[1]), len(match[1]), path))
    frames.sort()
    if not frames:
        raise ValueError(f'Aucun FITS calibré pour la séquence {sequence_name}')
    indices = [frame[0] for frame in frames]
    widths = {frame[1] for frame in frames}
    if len(set(indices)) != len(indices) or len(widths) != 1:
        raise ValueError('Numérotation ambiguë des FITS calibrés')
    layers = int(fits.getheader(frames[0][2]).get('NAXIS3', 1))
    # Siril sequence v4: no registration or statistics are invented.
    content = ["# Séquence des FITS calibrés exportés",
               f"S '{prefix}' {indices[0]} {len(frames)} {len(frames)} {frames[0][1]} 0 4 0",
               f'L {layers}']
    content.extend(f'I {index} 1' for index in indices)
    destination = output_dir / names[0]
    temporary = destination.with_suffix('.seq.tmp')
    temporary.write_text('\n'.join(content)+'\n', encoding='utf-8')
    temporary.replace(destination)
    logging.info('Séquence calibrée conservée : %s', destination)
    return destination


def discover_session_roots(root_dir: Path) -> List[Path]:
    """Retourne les sous-répertoires de session valides pour un répertoire cible.

    Si le répertoire fourni contient directement une entrée `light` ou `Light`,
    il est traité comme une session unique. Sinon, on cherche les sous-répertoires
    immédiats qui contiennent au moins un `light` / `Light`.
    """
    root_dir = Path(root_dir)
    if not root_dir.exists() or not root_dir.is_dir():
        return []

    for light_name in ["light", "Light"]:
        if (root_dir / light_name).exists():
            return [root_dir]

    candidates: List[Path] = []
    for child in sorted(root_dir.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        for light_name in ["light", "Light"]:
            if (child / light_name).exists():
                candidates.append(child)
                break

    return candidates


class CalibrationProfile:
    """Représente le profil de calibration du pipeline Siril."""

    def __init__(
        self,
        use_dark: bool = True,
        use_flat: bool = True,
        equalize_cfa: bool = True,
        debayer: bool = True,
    ) -> None:
        self.use_dark = use_dark
        self.use_flat = use_flat
        self.equalize_cfa = equalize_cfa
        self.debayer = debayer

    def build_command(
        self,
        target: str,
        sequence_name: str,
        dark_path: Optional[str] = None,
        flat_path: Optional[str] = None,
    ) -> str:
        """Construit la commande Siril correspondante selon la cible."""
        target_name = target.lower()
        if target_name not in {"flat", "light"}:
            raise ValueError(f"Cible de calibration inconnue: {target}")

        if target_name == "flat":
            if not self.use_dark:
                return ""
            if dark_path is None:
                raise ValueError("dark_path est requis pour la calibration des flats")
            return f"calibrate {sequence_name} -dark={dark_path} -cc=dark -cfa"

        if self.use_dark and dark_path is not None and self.use_flat and flat_path is not None:
            return (
                f"calibrate {sequence_name} -dark={dark_path} -flat={flat_path} "
                "-cc=dark -cfa -equalize_cfa -debayer"
            )

        if self.use_dark and dark_path is not None:
            return f"calibrate {sequence_name} -dark={dark_path} -cc=dark -cfa -debayer"

        if self.use_flat and flat_path is not None:
            return f"calibrate {sequence_name} -flat={flat_path} -cfa -equalize_cfa -debayer"

        return f"calibrate {sequence_name} -cfa -debayer"

    def build_flat_calibration(self, sequence_name: str, dark_path: Optional[str] = None) -> Tuple[str, str]:
        """Renvoie le titre du log et la commande associated to master flat creation."""
        if not self.use_dark:
            return "Mode sans dark: flats non calibrés par dark", ""
        if dark_path is None:
            raise ValueError("dark_path est requis quand use_dark est activé")

        command = self.build_command("flat", sequence_name, dark_path=dark_path)
        return (
            f"cmd:========> {command}",
            command,
        )

    def build_light_preprocess(
        self,
        sequence_name: str,
        dark_path: Optional[str] = None,
        flat_path: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        """Renvoie le titre, le message et la commande de calibration d’un light."""
        if self.use_dark and dark_path is None:
            raise ValueError("dark_path est requis quand use_dark est activé")

        if self.use_dark and dark_path is not None and self.use_flat and flat_path is not None:
            title = "Pre-process Light Frames (dark + flat calibration)"
            command = self.build_command("light", sequence_name, dark_path=dark_path, flat_path=flat_path)
            message = f"cmd:========> {command}"
            return title, message, command

        if self.use_dark and dark_path is not None:
            title = "Pre-process Light Frames (calibration with dark subtraction)"
            command = self.build_command("light", sequence_name, dark_path=dark_path, flat_path=None)
            message = f"cmd:========> {command}"
            return title, message, command

        if self.use_flat and flat_path is not None:
            title = "Pre-process Light Frames (flat calibration only)"
            command = self.build_command("light", sequence_name, dark_path=None, flat_path=flat_path)
            message = f"cmd:========> {command}"
            return title, message, command

        title = "Pre-process Light Frames (calibration without dark subtraction)"
        command = self.build_command("light", sequence_name, dark_path=None, flat_path=None)
        message = f"cmd:========> {command}"
        return title, message, command


class LightProcessor:
    """
    Processeur automatique pour les images light.
    
    Traite un répertoire de session contenant des sous-répertoires 'light' et 
    éventuellement 'flat', trouve les master darks correspondants, et effectue
    le prétraitement et stacking automatique.
    """
    
    def __init__(self, 
                 session_dir: Path,
                 dark_library_path: str,
                 output_dir: Path,
                 work_dir: Path,
                 temp_precision: float = 0.2,
                 force_reprocess: bool = False,
                 dry_run: bool = False,
                 use_dark: bool = True,
                 keep_intermediate: bool = False):
        """
        Initialise le processeur de light.
        
        Args:
            session_dir: Répertoire de la session contenant light/ et flat/
            dark_library_path: Chemin vers la librairie de master darks
            output_dir: Répertoire de sortie pour les résultats
            work_dir: Répertoire de travail temporaire
            temp_precision: Précision de correspondance des températures
            force_reprocess: Force le retraitement même si les fichiers existent
            dry_run: Simule le traitement sans l'exécuter
            use_dark: Utilise les master darks pour la calibration si True
            keep_intermediate: Conserve les répertoires/fichiers temporaires de prétraitement
        """
        self.session_dir = Path(session_dir)
        self.dark_library_path = dark_library_path
        self.output_dir = Path(output_dir)
        self.work_dir = Path(work_dir)
        self.temp_precision = temp_precision
        self.force_reprocess = force_reprocess
        self.dry_run = dry_run
        self.use_dark = use_dark
        self.keep_intermediate = keep_intermediate
        self.calibration = CalibrationProfile(use_dark=use_dark)
        
        # Initialisation de l'instance Siril avec la configuration par défaut
        self.siril = Siril.create_with_defaults()
        
        # Liste des fichiers de sortie créés durant le traitement
        self.output_files = []

        # Cache des flats de session (évite de rescanner le disque pour chaque groupe)
        self._session_flats_cache: Optional[List[FitsInfo]] = None

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

    def _has_wcs_header(self, file_path: Path) -> Optional[bool]:
        """Retourne True si le FITS contient des infos WCS minimales, False sinon, None si illisible."""
        try:
            with fits.open(file_path, memmap=False) as hdul:
                header = hdul[0].header

            # Présence minimale de solution WCS 2D dans le header.
            has_crval = ("CRVAL1" in header) and ("CRVAL2" in header)
            has_crpix = ("CRPIX1" in header) and ("CRPIX2" in header)
            has_projection = ("CTYPE1" in header) and ("CTYPE2" in header)
            has_transform = (
                ("CD1_1" in header and "CD1_2" in header and "CD2_1" in header and "CD2_2" in header)
                or ("CDELT1" in header and "CDELT2" in header)
            )
            return has_crval and has_crpix and has_projection and has_transform
        except Exception as exc:
            logging.debug(f"Lecture WCS impossible pour {file_path}: {exc}")
            return None

    def _collect_wcs_stats(self, light_files: List[Path]) -> None:
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
            raise ValueError(f"Le répertoire de session n'existe pas: {self.session_dir}")
        
        # Vérifier l'existence du répertoire light (insensible à la casse)
        light_found = False
        for light_name in ["light", "Light"]:
            light_dir = self.session_dir / light_name
            if light_dir.exists():
                light_found = True
                break
        
        if not light_found:
            raise ValueError(f"Aucun répertoire 'light' ou 'Light' trouvé dans: {self.session_dir}")
        
        if self.use_dark and self.dark_library_path and not Path(self.dark_library_path).exists():
            raise ValueError(f"La librairie de darks n'existe pas: {self.dark_library_path}")

    def _list_calibrated_outputs(self, calibrated_output_dir: Path) -> List[Path]:
        """Liste uniquement les sorties calibrées attendues (préfixe pp_)."""
        return sorted(
            list(calibrated_output_dir.glob("pp_*.fits")) +
            list(calibrated_output_dir.glob("pp_*.fit"))
        )
    
    
    def find_light_files(self) -> List[Path]:
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
            logging.error(f"Aucun répertoire 'light' ou 'Light' trouvé dans {self.session_dir}")
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
        
        logging.info(f"Répertoire light trouvé: {light_dir}")
        logging.debug(f"{len(light_files)} fichiers light détectés dans {light_dir}")
        return light_files

    def find_flat_files(self) -> List[Path]:
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

        logging.info(f"Répertoire flat trouvé: {flat_dir}")
        logging.debug(f"{len(flat_files)} fichiers flat détectés dans {flat_dir}")
        return flat_files

    def _is_flat_frame(self, fits_info: FitsInfo) -> bool:
        """Retourne True si le FITS correspond à un flat d'après IMAGETYP."""
        image_type = (fits_info.imagetyp_value or "").lower()
        return "flat" in image_type

    def _load_session_flats(self) -> List[FitsInfo]:
        """
        Charge les flats valides de la session (avec mise en cache).

        Returns:
            Liste des métadonnées flats exploitables
        """
        if self._session_flats_cache is not None:
            return self._session_flats_cache

        flat_files = self.find_flat_files()
        flat_infos: List[FitsInfo] = []

        for flat_file in flat_files:
            try:
                flat_info = FitsInfo(str(flat_file))

                if not flat_info.validData():
                    logging.warning(f"Fichier flat invalide (métadonnées manquantes): {flat_file}")
                    continue

                if not self._is_flat_frame(flat_info):
                    logging.warning(f"Fichier ignoré (pas un flat): {flat_file} (type: {flat_info.imagetyp_value})")
                    continue

                flat_infos.append(flat_info)
            except Exception as e:
                logging.warning(f"Erreur lors de l'analyse du flat {flat_file}: {e}")

        self._session_flats_cache = flat_infos
        return flat_infos

    def _select_flats_for_light_group(self, light_info: FitsInfo) -> List[FitsInfo]:
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
            logging.info("Aucun flat compatible trouvé pour ce groupe de lights")

        return selected
    
    def analyze_light_characteristics(self, light_files: List[Path]) -> Dict[str, List[FitsInfo]]:
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
                    logging.warning(f"Fichier light invalide (métadonnées manquantes): {light_file}")
                    continue
                
                # Vérifier que c'est bien un light (pas un dark ou bias)
                if fits_info.is_dark() or fits_info.is_bias():
                    self.session_stats["rejected_not_light_type"] += 1
                    logging.warning(f"Fichier ignoré (pas un light): {light_file} (type: {fits_info.imagetyp_value})")
                    continue
                
                # Grouper par caractéristiques
                group_key = fits_info.group_key(self.temp_precision)
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
            logging.info(f"Groupe de calibration détecté: '{group_key}' ({len(fits_list)} images)")
            if fits_list:
                example = fits_list[0]
                logging.debug(f"  Exemple: T={example.temperature()}°C, "
                              f"Exp={example.exptime()}s, "
                              f"Gain={example.gain()}, "
                              f"Caméra={example.camera()}, "
                              f"Binning={example.binning()}")
        
        return groups
    
    def find_matching_master_dark(self, light_info: FitsInfo) -> Optional[Path]:
        """
        Trouve le master dark correspondant aux caractéristiques du light.
        
        Args:
            light_info: Information du fichier light
            
        Returns:
            Chemin vers le master dark correspondant ou None si non trouvé
        """
        if not self.use_dark:
            logging.info("Mode sans dark: recherche de master dark ignorée")
            return None

        if not self.dark_library_path:
            logging.warning("Aucune librairie de darks spécifiée")
            return None
        
        dark_lib_path = Path(self.dark_library_path)
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
                if light_info.is_equivalent(dark_info, self.temp_precision):
                    logging.info(f"Master dark trouvé: {dark_file}")
                    logging.info(f"  Light: T={light_info.temperature()}°C, "
                               f"Exp={light_info.exptime()}s, "
                               f"Gain={light_info.gain()}, "
                               f"Caméra={light_info.camera()}")
                    logging.info(f"  Dark:  T={dark_info.temperature()}°C, "
                               f"Exp={dark_info.exptime()}s, "
                               f"Gain={dark_info.gain()}, "
                               f"Caméra={dark_info.camera()}")
                    return Path(dark_file)
                    
            except Exception as e:
                logging.debug(f"Erreur lors de l'analyse du dark {dark_file}: {e}")
                continue
        
        logging.warning(f"Aucun master dark correspondant trouvé pour: "
                       f"T={light_info.temperature()}°C, "
                       f"Exp={light_info.exptime()}s, "
                       f"Gain={light_info.gain()}, "
                       f"Caméra={light_info.camera()}, "
                       f"Binning={light_info.binning()}")
        return None
    
    def _prepare_sequence(self, sequence_name: str, light_files: List[str]) -> bool:
        """
        Prépare une séquence en créant les liens symboliques.
        
        Args:
            sequence_name: Nom de la séquence
            light_files: Liste des chemins vers les fichiers light
            
        Returns:
            True si la préparation a réussi, False sinon
        """
        sequence_dir = self.work_dir / sequence_name
        
        try:
            # Créer le répertoire de la séquence
            sequence_dir.mkdir(parents=True, exist_ok=True)
            
            # Créer les liens symboliques avec la convention Siril
            for i, file_path in enumerate(light_files):
                source_path = Path(file_path).resolve()
                if not source_path.exists():
                    logging.error(f"Fichier source inexistant: {source_path}")
                    return False
                
                # Nom du lien selon la convention Siril
                link_name = f"{sequence_name}_{i:04d}.fit"
                link_path = sequence_dir / link_name
                
                # Supprimer le lien existant s'il y en a un
                if link_path.exists():
                    link_path.unlink()
                
                # Créer le lien symbolique
                link_path.symlink_to(source_path)
                logging.debug(f"Lien créé: {link_path} -> {source_path}")
            
            logging.info(f"Séquence préparée: '{sequence_name}' ({len(light_files)} fichiers)")
            return True
            
        except Exception as e:
            logging.error(f"Erreur lors de la préparation de la séquence {sequence_name}: {e}")
            return False
    
    def _cleanup_sequence(self, sequence_name: str) -> None:
        """
        Nettoie les fichiers temporaires de la séquence.
        
        Args:
            sequence_name: Nom de la séquence à nettoyer
        """
        sequence_dir = self.work_dir / sequence_name
        if sequence_dir.exists():
            try:
                shutil.rmtree(sequence_dir)
                logging.debug(f"Répertoire de séquence nettoyé: {sequence_dir}")
            except Exception as e:
                logging.warning(f"Erreur lors du nettoyage de {sequence_dir}: {e}")

    def _generate_flat_master_script(
        self,
        flat_sequence_name: str,
        master_flat_output_base: str,
        flat_dark_path: Optional[str]
    ) -> str:
        """
        Génère le script Siril de création du master flat.

        Args:
            flat_sequence_name: Nom de la séquence flat
            master_flat_output_base: Chemin de sortie sans extension du master flat
            flat_dark_path: Master dark utilisé pour calibrer les flats (None si --no-dark)

        Returns:
            Contenu du script Siril
        """
        flat_process_dir = self.work_dir / "flat_process"
        flat_sequence_dir = self.work_dir / flat_sequence_name

        script_dir = Path(__file__).parent.parent / "bin"
        pyecho_path = script_dir / "pyecho.py"
        pydir_path = script_dir / "pydir.py"

        if self.use_dark:
            if not flat_dark_path:
                raise ValueError("flat_dark_path est requis quand use_dark est activé")
            flat_calibration_msg, flat_calibration_cmd = self.calibration.build_flat_calibration(
                flat_sequence_name,
                dark_path=flat_dark_path,
            )
            flat_stack_input = f"pp_{flat_sequence_name}"
        else:
            flat_calibration_msg, flat_calibration_cmd = self.calibration.build_flat_calibration(
                flat_sequence_name,
                dark_path=None,
            )
            flat_stack_input = flat_sequence_name

        script_content = f"""requires 1.2
cd {flat_sequence_dir}
pyscript {pyecho_path} "====================================================================="
pyscript {pyecho_path} "Convert Flat Frames to .fit files"
pyscript {pyecho_path} "cmd:========> convert {flat_sequence_name} -out={flat_process_dir}"
convert {flat_sequence_name} -out={flat_process_dir}
cd {flat_process_dir}
pyscript {pydir_path}
pyscript {pyecho_path} "====================================================================="
pyscript {pyecho_path} "Prepare master flat"
pyscript {pyecho_path} "{flat_calibration_msg}"
{flat_calibration_cmd}
pyscript {pyecho_path} "cmd:========> stack {flat_stack_input} median -norm=mul -out={master_flat_output_base}"
stack {flat_stack_input} median -norm=mul -out={master_flat_output_base}
cd {self.work_dir}
pyscript {pydir_path}
pyscript {pyecho_path} "====================================================================="

close"""

        return script_content

    def _generate_siril_script(
        self,
        sequence_name: str,
        group_key: str,
        dark_path: Optional[str],
        flat_path: Optional[str],
        stack_params: dict = None
    ) -> str:
        """
        Génère le script Siril pour le traitement complet.
        
        Args:
            sequence_name: Nom de la séquence
            group_key: Clé du groupe pour le nom de fichier final
            dark_path: Chemin vers le fichier master dark (None en mode sans dark)
            flat_path: Chemin vers le fichier master flat (None si pas de flats)
            stack_params: Paramètres de stacking
            
        Returns:
            Contenu du script Siril
        """
        # Paramètres par défaut si non spécifiés
        if stack_params is None:
            stack_params = {
                "method": "average",
                "rejection": "sigma", 
                "rejection_low": 3.0,
                "rejection_high": 3.0
            }
        
        # Construire la commande stack avec les paramètres de rejection
        rejection_method = stack_params.get("rejection", "sigma")
        rejection_low = stack_params.get("rejection_low", 3.0)
        rejection_high = stack_params.get("rejection_high", 3.0)
        
        # Créer le répertoire de traitement
        process_dir = self.work_dir / "process"
        sequence_dir = self.work_dir / sequence_name
        
        # Chemin vers les scripts Python
        script_dir = Path(__file__).parent.parent / "bin"
        pyecho_path = script_dir / "pyecho.py"
        pydir_path = script_dir / "pydir.py"

        if self.use_dark and dark_path is None:
            raise ValueError("dark_path est requis quand use_dark est activé")

        preprocess_title, preprocess_message, preprocess_command = self.calibration.build_light_preprocess(
            sequence_name,
            dark_path=dark_path,
            flat_path=flat_path,
        )

        if stack_params.get("drizzle", "off") != "off":
            preprocess_command = preprocess_command.replace(" -debayer", "")

        script_content = f"""requires 1.2
cd {sequence_dir}
pyscript {pyecho_path} "====================================================================="
pyscript {pyecho_path} "Convert Light Frames to .fit files"
pyscript {pyecho_path} "Convert files to sequence."
pyscript {pyecho_path} "cmd:========> convert {sequence_name} -out={process_dir}"
convert {sequence_name} -out={process_dir}
cd {process_dir}
pyscript {pydir_path}
pyscript {pyecho_path} "====================================================================="
pyscript {pyecho_path} "{preprocess_title}"
pyscript {pyecho_path} "{preprocess_message}"
{preprocess_command}
cd {self.work_dir}
pyscript {pydir_path}
pyscript {pyecho_path} "====================================================================="

close"""

        return script_content
    
    def process_light_group(self, 
                           group_key: str, 
                           light_infos: List[FitsInfo],
                           stack_params: Dict) -> bool:
        """
        Traite un groupe d'images light avec les mêmes caractéristiques.
        
        Args:
            group_key: Clé identifiant le groupe
            light_infos: Liste des FitsInfo du groupe
            stack_params: Paramètres de stacking
            
        Returns:
            True si le traitement a réussi, False sinon
        """
        if not light_infos:
            logging.warning(f"Groupe vide: {group_key}")
            return False
        
        logging.info(f"Traitement du groupe '{group_key}' ({len(light_infos)} images)")
        
        # Prendre le premier light comme référence pour les caractéristiques
        reference_light = light_infos[0]
        
        master_dark_path = None
        if self.use_dark:
            # Chercher le master dark correspondant
            master_dark_path = self.find_matching_master_dark(reference_light)
            if not master_dark_path:
                self.session_stats["rejected_no_matching_dark"] += len(light_infos)
                logging.error(f"Impossible de traiter le groupe '{group_key}': aucun master dark correspondant")
                return False

        # Préparation optionnelle des flats pour ce groupe
        selected_flats = self._select_flats_for_light_group(reference_light)
        if selected_flats:
            # Pour appliquer un seul dark de calibration flats, on garde un sous-ensemble
            # homogène en temps de pose (groupe majoritaire).
            flats_by_exptime: Dict[float, List[FitsInfo]] = {}
            for flat_info in selected_flats:
                exp_key = round(flat_info.exptime(), 6)
                flats_by_exptime.setdefault(exp_key, []).append(flat_info)

            if len(flats_by_exptime) > 1:
                best_exp_key, best_group = max(flats_by_exptime.items(), key=lambda item: len(item[1]))
                ignored_count = len(selected_flats) - len(best_group)
                logging.warning(
                    f"Plusieurs temps de pose de flats détectés pour le groupe '{group_key}'. "
                    f"Utilisation de EXPTIME={best_exp_key}s ({len(best_group)} flats), "
                    f"{ignored_count} flat(s) ignoré(s)."
                )
                selected_flats = best_group

        master_flat_path: Optional[Path] = None
        flat_sequence_name = f"flat_{group_key}"
        master_flat_output_base = str(self.work_dir / f"master_flat_{group_key}")
        
        # Préparer les chemins de fichiers
        light_files = [Path(info.filepath) for info in light_infos]
        
        # Nom de la séquence basé sur le group_key
        sequence_name = f"light_{group_key}"
        
        # Nom de base du fichier de sortie basé sur le nom de session
        session_basename = self.session_dir.name
        calibrated_output_dir = self.output_dir / f"{session_basename}_{group_key}_calibrated"

        # Les groupes calibrés sont exportés sans stack intermédiaire ;
        # le stacking final se fera sur l'ensemble des FITS calibrés dans ce dossier.
        existing_outputs = self._list_calibrated_outputs(calibrated_output_dir)
        existing_output = existing_outputs[0] if existing_outputs else None

        needs_native_cfa = False
        if existing_output and stack_params.get("drizzle", "off") != "off":
            try:
                source_header = fits.getheader(light_files[0])
                cached_header = fits.getheader(existing_output)
                needs_native_cfa = (
                    source_header.get("NAXIS") == 2
                    and source_header.get("BAYERPAT", "").strip() in {"RGGB", "BGGR", "GRBG", "GBRG"}
                    and cached_header.get("NAXIS") == 3
                )
            except (OSError, ValueError):
                logging.warning("Impossible de vérifier le CFA des calibrations existantes")
        if needs_native_cfa:
            logging.info("Recalibration nécessaire pour préserver le CFA natif avant décision Drizzle")

        if existing_output and not self.force_reprocess and not needs_native_cfa:
            if not self.dry_run and not any((calibrated_output_dir / name).exists() for name in
                                           (f"pp_{sequence_name}_.seq", f"pp_{sequence_name}.seq")):
                save_calibrated_sequence(sequence_name, calibrated_output_dir)
            logging.info(f"Fichier de sortie existant, passage: {existing_output}")
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
                logging.info(f"Répertoire calibré existant supprimé (mode --force): {calibrated_output_dir}")
            except Exception as exc:
                logging.warning(
                    f"Impossible de nettoyer le répertoire calibré {calibrated_output_dir}: {exc}"
                )
        
        if self.dry_run:
            if self.use_dark:
                logging.info(f"[DRY-RUN] Traiterait {len(light_files)} lights avec dark {master_dark_path}")
            else:
                logging.info(f"[DRY-RUN] Traiterait {len(light_files)} lights sans dark")

            if selected_flats:
                logging.info(f"[DRY-RUN] Utiliserait {len(selected_flats)} flats pour créer un master flat")
                if self.use_dark:
                    flat_dark_path = self.find_matching_master_dark(selected_flats[0])
                    logging.info(f"[DRY-RUN] Dark pour calibrer les flats: {flat_dark_path}")
                logging.info(f"[DRY-RUN] Master flat attendu: {master_flat_output_base}.fit (ou .fits)")
            else:
                logging.info("[DRY-RUN] Aucun flat utilisé pour ce groupe")

            logging.info(
                f"[DRY-RUN] Dossier de sortie calibrée attendu: {calibrated_output_dir} "
                "(contiendra des fichiers .fit/.fits)"
            )
            
            # Générer et afficher le script Siril en mode dry-run
            try:
                flat_path_for_script = f"{master_flat_output_base}.fit" if selected_flats else None
                script_content = self._generate_siril_script(
                    sequence_name,
                    group_key,
                    str(master_dark_path) if master_dark_path else None,
                    flat_path_for_script,
                    stack_params
                )
                logging.info(f"[DRY-RUN] Script Siril qui serait généré:")
                for i, line in enumerate(script_content.split('\n'), 1):
                    if line.strip():
                        logging.info(f"[DRY-RUN]   {i:2d}: {line}")
            except Exception as e:
                logging.warning(f"[DRY-RUN] Impossible de générer le script Siril: {e}")
            
            return True
        
        try:
            # Préparer la séquence (créer les liens symboliques)
            if not self._prepare_sequence(sequence_name, light_files):
                logging.error(f"Échec de la préparation de la séquence {sequence_name}")
                return False
            
            try:
                # Génération du master flat si des flats sont disponibles pour ce groupe
                if selected_flats:
                    flat_dark_path = None
                    if self.use_dark:
                        # Les darkflats sont assimilés à des darks de la librairie.
                        # Le dark doit correspondre aux caractéristiques des flats.
                        flat_dark_path = self.find_matching_master_dark(selected_flats[0])
                        if not flat_dark_path:
                            self.session_stats["rejected_processing_failure"] += len(light_infos)
                            logging.error(
                                f"Impossible de traiter le groupe '{group_key}': "
                                "aucun master dark adapté pour calibrer les flats"
                            )
                            return False

                    if not self._prepare_sequence(flat_sequence_name, [Path(info.filepath) for info in selected_flats]):
                        self.session_stats["rejected_processing_failure"] += len(light_infos)
                        logging.error(f"Échec de la préparation de la séquence de flats {flat_sequence_name}")
                        return False

                    flat_process_dir = self.work_dir / "flat_process"
                    if flat_process_dir.exists():
                        try:
                            shutil.rmtree(flat_process_dir)
                            logging.debug(f"Répertoire de flat_process nettoyé: {flat_process_dir}")
                        except Exception as e:
                            logging.warning(f"Impossible de nettoyer {flat_process_dir}: {e}")

                    flat_process_dir.mkdir(parents=True, exist_ok=True)

                    flat_script_content = self._generate_flat_master_script(
                        flat_sequence_name=flat_sequence_name,
                        master_flat_output_base=master_flat_output_base,
                        flat_dark_path=str(flat_dark_path) if flat_dark_path else None
                    )

                    logging.info(f"Création du master flat pour le groupe {group_key}")
                    flat_success = self.siril.run_siril_script(
                        flat_script_content,
                        str(self.work_dir),
                        script_name=f"master_flat_{group_key}.sps",
                    )
                    if not flat_success:
                        self.session_stats["rejected_processing_failure"] += len(light_infos)
                        logging.error("Échec de la création du master flat")
                        return False

                    fit_candidate = Path(f"{master_flat_output_base}.fit")
                    fits_candidate = Path(f"{master_flat_output_base}.fits")
                    if fit_candidate.exists():
                        master_flat_path = fit_candidate
                    elif fits_candidate.exists():
                        master_flat_path = fits_candidate
                    else:
                        self.session_stats["rejected_processing_failure"] += len(light_infos)
                        logging.error(
                            f"Master flat non trouvé: {fit_candidate} ou {fits_candidate}"
                        )
                        return False

                # Nettoyer le répertoire de traitement existant pour un démarrage propre
                process_dir = self.work_dir / "process"
                if process_dir.exists():
                    try:
                        shutil.rmtree(process_dir)
                        logging.debug(f"Répertoire de traitement nettoyé: {process_dir}")
                    except Exception as e:
                        logging.warning(f"Impossible de nettoyer le répertoire de traitement {process_dir}: {e}")
                        # Continuer quand même, les fichiers seront écrasés si possible
                
                # Créer le répertoire de traitement
                process_dir.mkdir(parents=True, exist_ok=True)
                
                # Générer et exécuter le script Siril
                script_content = self._generate_siril_script(
                    sequence_name,
                    group_key,
                    str(master_dark_path) if master_dark_path else None,
                    str(master_flat_path) if master_flat_path else None,
                    stack_params
                )
                
                logging.info(f"Executing calibration workflow for sequence {sequence_name} (no per-group stack)")
                if stack_params:
                    method = stack_params.get("method", "average")
                    rejection = stack_params.get("rejection", "sigma")
                    rejection_low = stack_params.get("rejection_low", 3.0)
                    rejection_high = stack_params.get("rejection_high", 3.0)
                    logging.info(f"Calibration-only parameters: method={method}, rejection={rejection} {rejection_low} {rejection_high}")

                success = self.siril.run_siril_script(
                    script_content,
                    str(self.work_dir),
                    script_name=f"calibrate_light_{group_key}.sps",
                )
                
                if success:
                    self._export_calibrated_outputs_from_process(sequence_name, calibrated_output_dir)

                    exported_outputs = self._list_calibrated_outputs(calibrated_output_dir)

                    if exported_outputs:
                        logging.info(
                            f"Calibrated subgroup exported successfully: {len(exported_outputs)} FITS calibrés (pp_*) in "
                            f"{calibrated_output_dir}"
                        )
                        for exported_file in exported_outputs:
                            self.output_files.append(exported_file)
                        self.session_stats["kept_for_stacking"] += len(light_infos)
                        return True
                    else:
                        self.session_stats["rejected_processing_failure"] += len(light_infos)
                        logging.error(
                            f"Aucune sortie calibrée trouvée dans le dossier: {calibrated_output_dir}"
                        )
                        return False
                else:
                    self.session_stats["rejected_processing_failure"] += len(light_infos)
                    logging.error(f"Échec du traitement complet de la séquence {sequence_name}")
                    return False
            
            finally:
                # Nettoyer la séquence dans tous les cas, sauf si demandé explicitement.
                if self.keep_intermediate:
                    logging.info(
                        "Mode conservation activé: répertoires temporaires conservés pour inspection (%s, %s)",
                        self.work_dir / sequence_name,
                        self.work_dir / flat_sequence_name,
                    )
                else:
                    self._cleanup_sequence(sequence_name)
                    self._cleanup_sequence(flat_sequence_name)
        
        except Exception as e:
            logging.error(f"Erreur lors du traitement du groupe '{group_key}': {e}")
            return False
    
    def process_session(self, stack_params: Dict) -> bool:
        """
        Traite toute la session: détecte les lights, les groupe et les traite.
        
        Args:
            stack_params: Paramètres de stacking
            
        Returns:
            True si tout s'est bien passé, False sinon
        """
        logging.info(f"Début du traitement de la session: {self.session_dir}")
        
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
                    logging.info(f"Groupe '{group_key}' traité avec succès")
                else:
                    logging.error(f"Échec du traitement du groupe '{group_key}'")
            except Exception as e:
                self.session_stats["rejected_processing_failure"] += len(light_infos)
                logging.error(f"Erreur lors du traitement du groupe '{group_key}': {e}")
        
        # 4. Résumé final
        logging.info(f"Traitement terminé: {success_count}/{total_groups} groupes traités avec succès")
        
        if success_count == total_groups:
            logging.info("Tous les groupes ont été traités avec succès")
            return True
        elif success_count > 0:
            logging.warning(f"Traitement partiel: {success_count}/{total_groups} groupes réussis")
            return True
        else:
            logging.error("Aucun groupe n'a pu être traité")
            return False
    
    def get_output_files(self) -> List[Path]:
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

        candidates = self._list_calibrated_outputs(calibrated_output_dir)
        converted_count = 0

        for output_file in candidates:
            if not output_file.is_symlink():
                continue

            try:
                target = output_file.resolve(strict=True)
                temp_copy = output_file.with_name(f"{output_file.name}.tmp_materialized")
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

    def _export_calibrated_outputs_from_process(self, sequence_name: str, calibrated_output_dir: Path) -> None:
        """Copie uniquement les vrais résultats pp_<sequence> produits par Siril dans process/."""
        process_dir = self.work_dir / "process"
        if not process_dir.exists():
            logging.warning("Export calibré impossible: répertoire process introuvable: %s", process_dir)
            return

        sources = sorted(
            list(process_dir.glob(f"pp_{sequence_name}_*.fits")) +
            list(process_dir.glob(f"pp_{sequence_name}_*.fit"))
        )
        if not sources:
            logging.warning("Aucun fichier calibré pp_%s trouvé dans %s", sequence_name, process_dir)
            return

        calibrated_output_dir.mkdir(parents=True, exist_ok=True)

        for stale_file in self._list_calibrated_outputs(calibrated_output_dir):
            stale_file.unlink()

        for name in (f"pp_{sequence_name}_.seq", f"pp_{sequence_name}.seq"):
            (calibrated_output_dir / name).unlink(missing_ok=True)
        copied_count = 0
        for source_file in sources:
            if source_file.name.endswith(".seq"):
                continue

            destination = calibrated_output_dir / source_file.name
            temp_copy = destination.with_name(f"{destination.name}.tmp_export")
            if temp_copy.exists():
                temp_copy.unlink()

            shutil.copy2(source_file, temp_copy)
            temp_copy.rename(destination)
            copied_count += 1

        save_calibrated_sequence(sequence_name, calibrated_output_dir, process_dir)
        logging.info(
            "Sorties calibrées exportées depuis process/: %d fichier(s) vers %s",
            copied_count,
            calibrated_output_dir,
        )

    def get_session_stats(self) -> Dict[str, object]:
        """Retourne une copie des statistiques de session."""
        return dict(self.session_stats)


def stack_session_outputs(
    output_files: List[Path],
    output_dir: Path,
    work_dir: Path,
    target_name: str,
    stack_params: Optional[Dict] = None,
    force_stacking: bool = False,
    stack_report: Optional[Dict] = None,
) -> Optional[Path]:
    """Empile les résultats traités de plusieurs sessions de même cible."""
    valid_files = [Path(file).absolute() for file in output_files if Path(file).exists()]
    if not valid_files:
        return None
    if len(valid_files) == 1:
        return valid_files[0]

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
    target_name = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in target_name)

    session_stack_dir = Path(work_dir) / target_name / "stacking"
    input_dir = session_stack_dir / "input"
    output_stack_dir = session_stack_dir / "output"

    if force_stacking and session_stack_dir.exists():
        shutil.rmtree(session_stack_dir)

    output_path = Path(output_dir) / f"{target_name}_combined"
    if force_stacking:
        for candidate in [output_path.with_suffix(".fit"), output_path.with_suffix(".fits")]:
            if candidate.exists():
                candidate.unlink()

    for transient_dir in [input_dir, output_stack_dir]:
        if transient_dir.exists():
            shutil.rmtree(transient_dir)

    stack_cfg = stack_params or {}
    if any(key in stack_cfg for key in ("drizzle", "nbstars_filter", "roundness_weighted")):
        from lib.drizzle import STACK_STAGES, reset_stage
        run_dir = session_stack_dir
        for stage in STACK_STAGES:
            reset_stage(run_dir / stage)
        input_dir = run_dir / "00_inputs"
        output_stack_dir = run_dir / "01_registration"
    else:
        run_dir = session_stack_dir
        input_dir.mkdir(parents=True, exist_ok=True)
        output_stack_dir.mkdir(parents=True, exist_ok=True)

    local_report = {
        "target_name": target_name,
        "total_input": len(valid_files),
        "rejected_incompatible_layout": 0,
        "rejected_unreadable_layout": 0,
        "rejected_fwhm_proportion": 0,
        "rejected_fwhm_unmeasurable": 0,
        "kept_unique_for_stack": len(valid_files),
        "kept_effective_for_stack": len(valid_files),
        "weighted_extra_entries": 0,
    }

    filtered_files, layout_stats = _filter_compatible_stack_inputs(valid_files)
    local_report["rejected_incompatible_layout"] = layout_stats.get("rejected_incompatible_layout", 0)
    local_report["rejected_unreadable_layout"] = layout_stats.get("rejected_unreadable_layout", 0)

    if local_report["rejected_incompatible_layout"] > 0 or local_report["rejected_unreadable_layout"] > 0:
        logging.warning(
            "Nettoyage des entrées de stack: %d rejetée(s) (layout incompatible), %d rejetée(s) (layout illisible).",
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

    fwhm_reject_percent = float(stack_cfg.get("fwhm_reject_percent", 0.0) or 0.0)
    fwhm_weighted = bool(stack_cfg.get("fwhm_weighted", False))
    fwhm_weight_max_extra = int(stack_cfg.get("fwhm_weight_max_extra", 2) or 2)

    fwhm_reject_percent = max(0.0, min(95.0, fwhm_reject_percent))
    fwhm_weight_max_extra = max(0, min(8, fwhm_weight_max_extra))

    files_for_stack = valid_files
    if fwhm_reject_percent > 0.0 or (fwhm_weighted and len(valid_files) > 2):
        ranked_files = _rank_files_by_fwhm(valid_files)
        ranked_set = {file_path for file_path, _ in ranked_files}
        unranked_files = [file_path for file_path in valid_files if file_path not in ranked_set]
        local_report["rejected_fwhm_unmeasurable"] = len(unranked_files)

        if ranked_files:
            keep_count = max(2, int(math.ceil(len(ranked_files) * (1.0 - (fwhm_reject_percent / 100.0)))))
            keep_count = min(keep_count, len(ranked_files))
            kept_ranked = ranked_files[:keep_count]
            removed_ranked = ranked_files[keep_count:]
            local_report["rejected_fwhm_proportion"] = len(removed_ranked)
            base_kept_files = [file_path for file_path, _ in kept_ranked]

            if removed_ranked:
                logging.info(
                    "Rejet FWHM: %d image(s) exclue(s) sur %d (%.1f%%)",
                    len(removed_ranked),
                    len(ranked_files),
                    fwhm_reject_percent,
                )
                for removed_file, removed_fwhm in removed_ranked:
                    logging.debug("  Exclue (FWHM %.3f): %s", removed_fwhm, removed_file)

            files_for_stack = base_kept_files + unranked_files
            local_report["kept_unique_for_stack"] = len(files_for_stack)

            if fwhm_weighted and len(base_kept_files) > 2 and fwhm_weight_max_extra > 0:
                best_fwhm = min(fwhm for _, fwhm in kept_ranked)
                worst_fwhm = max(fwhm for _, fwhm in kept_ranked)

                if worst_fwhm > best_fwhm:
                    weighted_files: List[Path] = []
                    for file_path, fwhm_value in kept_ranked:
                        normalized_quality = (worst_fwhm - fwhm_value) / (worst_fwhm - best_fwhm)
                        extra_repeats = int(round(normalized_quality * fwhm_weight_max_extra))
                        repeats = 1 + extra_repeats
                        weighted_files.extend([file_path] * repeats)
                        logging.debug(
                            "Pondération FWHM: %s (FWHM %.3f) répété %d fois",
                            file_path,
                            fwhm_value,
                            repeats,
                        )

                    files_for_stack = weighted_files + unranked_files
                    local_report["weighted_extra_entries"] = len(weighted_files) - len(base_kept_files)
                    logging.info(
                        "Pondération FWHM activée: %d images de base -> %d entrées effectives pour le stack",
                        len(base_kept_files),
                        len(files_for_stack),
                    )
                else:
                    logging.info("Pondération FWHM ignorée: FWHM quasi identiques sur les images conservées")
        else:
            logging.warning(
                "Impossible d'estimer la FWHM des entrées: rejet/pondération FWHM ignorés pour ce stack"
            )

    local_report["kept_effective_for_stack"] = len(files_for_stack)

    if stack_report is not None:
        stack_report.clear()
        stack_report.update(local_report)

    sequence_prefix = f"{target_name}_"
    for index, source_file in enumerate(files_for_stack, start=1):
        destination = input_dir / f"{sequence_prefix}{index:03d}.fits"
        if destination.exists():
            destination.unlink()
        try:
            destination.symlink_to(source_file)
        except OSError:
            shutil.copy2(source_file, destination)

    method = str(stack_cfg.get("method", "average")).lower()
    rejection = str(stack_cfg.get("rejection", "sigma")).lower()
    rejection_low = stack_cfg.get("rejection_low", 3.0)
    rejection_high = stack_cfg.get("rejection_high", 3.0)
    roundness_filter = stack_cfg.get("roundness_filter", "1.8k")
    fwhm_filter = stack_cfg.get("fwhm_filter", "1.8k")
    robust_realign = bool(stack_cfg.get("robust_realign", True))
    align_transform = str(stack_cfg.get("align_transform", "affine")).lower()
    enable_stack_platesolve = bool(stack_cfg.get("enable_stack_platesolve", True))
    framing = "min" if method in {"median", "med"} else "max"
    if framing == "min":
        logging.info(
            "Stack médian demandé: utilisation de seqapplyreg -framing=min pour produire des images de même taille."
        )

    registered_sequence_prefix = f"r_{sequence_prefix}"

    roundness_filter_value = "" if roundness_filter is None else str(roundness_filter).strip()
    fwhm_filter_value = "" if fwhm_filter is None else str(fwhm_filter).strip()

    seqapply_filters = []
    if roundness_filter_value.lower() not in {"", "none", "off", "false", "0"}:
        seqapply_filters.append(f"-filter-round={roundness_filter_value}")
    if fwhm_filter_value.lower() not in {"", "none", "off", "false", "0"}:
        seqapply_filters.append(f"-filter-wfwhm={fwhm_filter_value}")

    if seqapply_filters:
        seqapplyreg_line = f"seqapplyreg {sequence_prefix} {' '.join(seqapply_filters)} -framing={framing}"
    else:
        seqapplyreg_line = f"seqapplyreg {sequence_prefix} -framing={framing}"

    if robust_realign:
        second_register_line = f"register {registered_sequence_prefix} -2pass -transf={align_transform}"
        second_seqapplyreg_line = f"seqapplyreg {registered_sequence_prefix} -framing={framing}"
        stack_sequence_prefix = f"r_{registered_sequence_prefix}"
    else:
        second_register_line = ""
        second_seqapplyreg_line = ""
        stack_sequence_prefix = registered_sequence_prefix

    if method in {"median", "med"}:
        stack_line = (
            f"stack {stack_sequence_prefix} median -output_norm -out={output_path}"
        )
    elif rejection == "none":
        stack_line = (
            f"stack {stack_sequence_prefix} mean -output_norm -out={output_path}"
        )
    else:
        # Siril 1.4: la forme 'rej <low> <high>' réalise une moyenne avec rejet.
        stack_line = (
            f"stack {stack_sequence_prefix} rej {rejection_low} {rejection_high} "
            f"-output_norm -out={output_path}"
        )

    robust_lines = ""
    if robust_realign:
        robust_lines = f"{second_register_line}\n{second_seqapplyreg_line}\n"

    platesolve_lines = ""
    if enable_stack_platesolve:
        platesolve_lines = (
            f"seqplatesolve {sequence_prefix} -force -nocache -disto=ps_distortion\n"
        )

    script = f'''requires 1.2
cd {input_dir}
convert {sequence_prefix} -out={output_stack_dir}
cd {output_stack_dir}
seqfindstar {sequence_prefix}
{platesolve_lines}register {sequence_prefix} -2pass -transf={align_transform}
{seqapplyreg_line}
{robust_lines}{stack_line}
close'''

    siril = Siril.create_with_defaults()
    if any(key in stack_cfg for key in ("drizzle", "nbstars_filter", "roundness_weighted")):
        from lib.drizzle import run_stack
        prepare = script.split(seqapplyreg_line)[0].rstrip()
        success = run_stack(siril, files_for_stack, stack_cfg, output_stack_dir,
                            run_dir, sequence_prefix, output_path,
                            prepare, stack_line, framing, stack_report=stack_report)
    else:
        success = siril.run_siril_script(script, str(output_stack_dir), script_name=f"stack_{target_name}.sps")
    if not success:
        return None

    combined_candidates = [
        output_path.with_suffix(".fit"),
        output_path.with_suffix(".fits"),
    ]
    for candidate in combined_candidates:
        if candidate.exists():
            return candidate
    return None


def _estimate_frame_fwhm(file_path: Path) -> Optional[float]:
    """Estime une FWHM moyenne en pixels pour une image FITS (approximation robuste)."""
    try:
        with fits.open(file_path, memmap=False) as hdul:
            data = hdul[0].data

        if data is None:
            return None

        image = np.asarray(data, dtype=np.float64)
        if image.ndim > 2:
            image = image[0]
        if image.ndim != 2 or image.size == 0:
            return None

        finite = np.isfinite(image)
        if not np.any(finite):
            return None

        valid_values = image[finite]
        background = float(np.median(valid_values))
        mad = float(np.median(np.abs(valid_values - background)))
        sigma = 1.4826 * mad if mad > 0 else float(np.std(valid_values))
        if sigma <= 0:
            return None

        threshold = background + 5.0 * sigma
        work_image = np.where(finite, image, background)

        c = work_image[1:-1, 1:-1]
        local_max_mask = (
            (c > threshold)
            & (c >= work_image[:-2, 1:-1])
            & (c >= work_image[2:, 1:-1])
            & (c >= work_image[1:-1, :-2])
            & (c >= work_image[1:-1, 2:])
            & (c >= work_image[:-2, :-2])
            & (c >= work_image[:-2, 2:])
            & (c >= work_image[2:, :-2])
            & (c >= work_image[2:, 2:])
        )

        peak_positions = np.argwhere(local_max_mask)
        if len(peak_positions) == 0:
            return None

        peak_values = c[local_max_mask]
        order = np.argsort(peak_values)[::-1]
        selected_peaks = peak_positions[order[:40]]

        fwhm_values: List[float] = []
        for peak_pos in selected_peaks:
            y = int(peak_pos[0] + 1)
            x = int(peak_pos[1] + 1)

            y0 = max(0, y - 4)
            y1 = min(work_image.shape[0], y + 5)
            x0 = max(0, x - 4)
            x1 = min(work_image.shape[1], x + 5)
            patch = work_image[y0:y1, x0:x1] - background

            if patch.shape[0] < 5 or patch.shape[1] < 5:
                continue

            patch = np.clip(patch, 0.0, None)
            flux = float(np.sum(patch))
            if flux <= 0:
                continue

            yy, xx = np.indices(patch.shape)
            cx = float(np.sum(xx * patch) / flux)
            cy = float(np.sum(yy * patch) / flux)
            var_x = float(np.sum(((xx - cx) ** 2) * patch) / flux)
            var_y = float(np.sum(((yy - cy) ** 2) * patch) / flux)
            sigma_psf = float(np.sqrt(max((var_x + var_y) / 2.0, 0.0)))

            if sigma_psf > 0:
                fwhm_values.append(2.355 * sigma_psf)

        if not fwhm_values:
            return None

        return float(np.median(fwhm_values))
    except Exception as exc:
        logging.debug("Estimation FWHM impossible pour %s: %s", file_path, exc)
        return None


def _rank_files_by_fwhm(files: List[Path]) -> List[Tuple[Path, float]]:
    """Classe les fichiers du meilleur au moins bon selon FWHM (plus petite = meilleure)."""
    ranked: List[Tuple[Path, float]] = []
    for file_path in files:
        fwhm_value = _estimate_frame_fwhm(file_path)
        if fwhm_value is None:
            continue
        ranked.append((file_path, fwhm_value))

    ranked.sort(key=lambda item: item[1])
    return ranked


def _read_fits_layout(file_path: Path) -> Optional[Tuple[int, int, int, int]]:
    """Lit la signature de layout d'un FITS: (channels, width, height, bitpix)."""
    try:
        with fits.open(file_path, memmap=False) as hdul:
            header = hdul[0].header
            data = hdul[0].data

        bitpix = int(header.get("BITPIX", 0) or 0)

        if data is None:
            return None

        arr = np.asarray(data)
        if arr.ndim == 2:
            height, width = arr.shape
            channels = 1
            return (channels, int(width), int(height), bitpix)

        if arr.ndim == 3:
            # Convention FITS habituelle: (channels, height, width)
            channels = int(arr.shape[0])
            height = int(arr.shape[1])
            width = int(arr.shape[2])
            return (channels, width, height, bitpix)

        return None
    except Exception:
        return None


def _filter_compatible_stack_inputs(files: List[Path]) -> Tuple[List[Path], Dict[str, int]]:
    """Conserve un sous-ensemble homogène en layout FITS pour éviter un échec de séquence Siril."""
    by_layout: Dict[Tuple[int, int, int, int], List[Path]] = {}
    unreadable: List[Path] = []

    for file_path in files:
        layout = _read_fits_layout(file_path)
        if layout is None:
            unreadable.append(file_path)
            continue
        by_layout.setdefault(layout, []).append(file_path)

    stats = {
        "rejected_incompatible_layout": 0,
        "rejected_unreadable_layout": 0,
    }

    # Si rien n'est lisible (cas tests avec faux FITS), ne filtre pas.
    if not by_layout:
        return files, stats

    dominant_layout, dominant_files = max(by_layout.items(), key=lambda item: len(item[1]))
    kept_files = list(dominant_files)

    rejected_incompatible = 0
    incompatible_files: List[Path] = []
    for layout, layout_files in by_layout.items():
        if layout == dominant_layout:
            continue
        rejected_incompatible += len(layout_files)
        incompatible_files.extend(layout_files)

    stats["rejected_incompatible_layout"] = rejected_incompatible
    stats["rejected_unreadable_layout"] = len(unreadable)

    if rejected_incompatible > 0:
        logging.warning(
            "Layouts FITS mélangés détectés pour le stack final. Layout conservé: %s (%d fichiers).",
            dominant_layout,
            len(dominant_files),
        )

        kept_dirs_summary = _summarize_parent_directories(dominant_files)
        incompatible_dirs_summary = _summarize_parent_directories(incompatible_files)
        logging.warning(
            "Répertoires du layout conservé: %s",
            kept_dirs_summary,
        )
        logging.warning(
            "Répertoires des fichiers incompatibles: %s",
            incompatible_dirs_summary,
        )

    if unreadable:
        logging.warning(
            "%d fichier(s) ignoré(s) pour le stack final: layout FITS non lisible.",
            len(unreadable),
        )

    return kept_files, stats


def _summarize_parent_directories(files: List[Path], max_dirs: int = 8) -> str:
    """Construit un résumé compact des répertoires parents avec effectifs."""
    if not files:
        return "(aucun)"

    by_dir: Dict[str, int] = {}
    for file_path in files:
        parent = str(file_path.parent)
        by_dir[parent] = by_dir.get(parent, 0) + 1

    ranked = sorted(by_dir.items(), key=lambda item: item[1], reverse=True)
    shown = ranked[:max_dirs]
    parts = [f"{directory} ({count})" for directory, count in shown]

    remaining = len(ranked) - len(shown)
    if remaining > 0:
        parts.append(f"... +{remaining} autre(s) répertoire(s)")

    return "; ".join(parts)
