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
import numpy as np
from astropy.io import fits
from PIL import Image
from lib.fits_info import FitsInfo
from lib.siril_utils import Siril


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
                 use_dark: bool = True):
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
        """
        self.session_dir = Path(session_dir)
        self.dark_library_path = dark_library_path
        self.output_dir = Path(output_dir)
        self.work_dir = Path(work_dir)
        self.temp_precision = temp_precision
        self.force_reprocess = force_reprocess
        self.dry_run = dry_run
        self.use_dark = use_dark
        
        # Initialisation de l'instance Siril avec la configuration par défaut
        self.siril = Siril.create_with_defaults()
        
        # Liste des fichiers de sortie créés durant le traitement
        self.output_files = []
        
        # Validation des répertoires
        self._validate_directories()
        
        # Création des répertoires de sortie si nécessaire
        if not self.dry_run:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.work_dir.mkdir(parents=True, exist_ok=True)
    
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
        
        logging.info(f"Trouvé {len(light_files)} fichiers light dans {light_dir}")
        return light_files
    
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
                    logging.warning(f"Fichier light invalide (métadonnées manquantes): {light_file}")
                    continue
                
                # Vérifier que c'est bien un light (pas un dark ou bias)
                if fits_info.is_dark() or fits_info.is_bias():
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
            logging.info(f"Groupe '{group_key}': {len(fits_list)} images")
            if fits_list:
                example = fits_list[0]
                logging.info(f"  Exemple: T={example.temperature()}°C, "
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
            
            logging.info(f"Séquence '{sequence_name}' préparée avec {len(light_files)} fichiers")
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

    def _normalize_to_uint8(self, data: np.ndarray) -> np.ndarray:
        """Normalise un tableau d'image en uint8 avec un étirement robuste par percentiles."""
        finite = np.isfinite(data)
        if not finite.any():
            return np.zeros(data.shape, dtype=np.uint8)

        valid = data[finite]
        low = np.percentile(valid, 1)
        high = np.percentile(valid, 99)
        if high <= low:
            high = low + 1.0

        scaled = (data - low) / (high - low)
        scaled = np.clip(scaled, 0.0, 1.0)
        scaled[~finite] = 0.0
        return (scaled * 255.0).astype(np.uint8)

    def _export_jpg_from_fits(self, fits_path: Path) -> Optional[Path]:
        """Exporte un FITS en JPG (étirement simple) et retourne le chemin créé."""
        try:
            if not fits_path.exists():
                logging.warning(f"Conversion JPG ignorée, FITS introuvable: {fits_path}")
                return None

            with fits.open(fits_path, memmap=False) as hdul:
                image_data = None
                for hdu in hdul:
                    if hdu.data is not None:
                        image_data = np.array(hdu.data, dtype=np.float64)
                        break

            if image_data is None:
                logging.warning(f"Conversion JPG ignorée, aucune donnée image dans: {fits_path}")
                return None

            if image_data.ndim == 2:
                img_u8 = self._normalize_to_uint8(image_data)
                image = Image.fromarray(img_u8, mode="L")
            elif image_data.ndim == 3:
                if image_data.shape[0] in (3, 4) and image_data.shape[-1] not in (3, 4):
                    image_data = np.transpose(image_data, (1, 2, 0))
                if image_data.shape[-1] not in (3, 4):
                    logging.warning(f"Conversion JPG ignorée, format 3D non supporté: {image_data.shape} ({fits_path})")
                    return None

                if image_data.shape[-1] == 4:
                    image_data = image_data[..., :3]

                channels = [self._normalize_to_uint8(image_data[..., i]) for i in range(3)]
                rgb_u8 = np.stack(channels, axis=-1)
                image = Image.fromarray(rgb_u8, mode="RGB")
            else:
                logging.warning(f"Conversion JPG ignorée, dimension non supportée: {image_data.ndim} ({fits_path})")
                return None

            jpg_path = fits_path.with_suffix(".jpg")
            image.save(jpg_path, format="JPEG", quality=95)
            logging.info(f"JPG exporté: {jpg_path}")
            return jpg_path

        except Exception as e:
            logging.warning(f"Impossible d'exporter le JPG depuis {fits_path}: {e}")
            return None
    
    def _generate_siril_script(self, sequence_name: str, group_key: str, dark_path: Optional[str], stack_params: dict = None) -> str:
        """
        Génère le script Siril pour le traitement complet.
        
        Args:
            sequence_name: Nom de la séquence
            group_key: Clé du groupe pour le nom de fichier final
            dark_path: Chemin vers le fichier master dark (None en mode sans dark)
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
        
        # Nom de base du fichier de sortie basé sur le nom de session
        session_basename = self.session_dir.name
        
        # Chemin vers les scripts Python
        script_dir = Path(__file__).parent.parent / "bin"
        pyecho_path = script_dir / "pyecho.py"
        pydir_path = script_dir / "pydir.py"

        if self.use_dark:
            if not dark_path:
                raise ValueError("dark_path est requis quand use_dark est activé")
            preprocess_title = "Pre-process Light Frames (calibration with dark subtraction)"
            preprocess_message = f"cmd:========> calibrate {sequence_name} -dark={dark_path} -cc=dark -cfa -debayer"
            preprocess_command = f"calibrate {sequence_name} -dark={dark_path} -cc=dark -cfa -debayer"
        else:
            preprocess_title = "Pre-process Light Frames (calibration without dark subtraction)"
            preprocess_message = f"cmd:========> calibrate {sequence_name} -cfa -debayer"
            preprocess_command = f"calibrate {sequence_name} -cfa -debayer"
        
        # Script Siril pour le traitement complet
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
pyscript {pydir_path}

pyscript {pyecho_path} "====================================================================="
pyscript {pyecho_path} "Align lights"
pyscript {pyecho_path} "cmd:========> register pp_{sequence_name}  -2pass -transf=homography"
register pp_{sequence_name} -2pass -transf=homography
seqapplyreg pp_{sequence_name} -filter-wfwhm=2k -filter-round=2k 
pyscript {pydir_path}

pyscript {pyecho_path} "====================================================================="
pyscript {pyecho_path} "Stack calibrated lights to result.fit"
pyscript {pyecho_path} "cmd:========> stack r_pp_{sequence_name} mean {rejection_method} {rejection_low} {rejection_high} -output_norm -out={self.output_dir}/{session_basename}_{group_key}_stacked"
stack r_pp_{sequence_name} mean {rejection_method} {rejection_low} {rejection_high} -output_norm -out={self.output_dir}/{session_basename}_{group_key}_stacked
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
                logging.error(f"Impossible de traiter le groupe '{group_key}': aucun master dark correspondant")
                return False
        
        # Préparer les chemins de fichiers
        light_files = [Path(info.filepath) for info in light_infos]
        
        # Nom de la séquence basé sur le group_key
        sequence_name = f"light_{group_key}"
        
        # Nom de base du fichier de sortie basé sur le nom de session
        session_basename = self.session_dir.name
        
        # Vérifier si le fichier de sortie existe déjà
        preferred_final_output = self.output_dir / f"{session_basename}_{group_key}_stacked.fits"
        alternate_final_output = self.output_dir / f"{session_basename}_{group_key}_stacked.fit"

        existing_output = None
        if preferred_final_output.exists():
            existing_output = preferred_final_output
        elif alternate_final_output.exists():
            existing_output = alternate_final_output

        if existing_output and not self.force_reprocess:
            logging.info(f"Fichier de sortie existant, passage: {existing_output}")
            # Enregistrer le fichier existant dans la liste des sorties
            self.output_files.append(existing_output)
            if not self.dry_run:
                self._export_jpg_from_fits(existing_output)
            return True
        
        # Créer le répertoire de sortie si nécessaire
        if not self.dry_run:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        
        if self.dry_run:
            if self.use_dark:
                logging.info(f"[DRY-RUN] Traiterait {len(light_files)} lights avec dark {master_dark_path}")
            else:
                logging.info(f"[DRY-RUN] Traiterait {len(light_files)} lights sans dark")
            logging.info(f"[DRY-RUN] Sortie FITS attendue: {preferred_final_output} (ou {alternate_final_output})")
            logging.info(f"[DRY-RUN] Sortie JPG attendue: {preferred_final_output.with_suffix('.jpg')}")
            
            # Générer et afficher le script Siril en mode dry-run
            try:
                script_content = self._generate_siril_script(sequence_name, group_key, str(master_dark_path), stack_params)
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
                script_content = self._generate_siril_script(sequence_name, group_key, str(master_dark_path), stack_params)
                
                logging.info(f"Executing complete light processing workflow for sequence {sequence_name}")
                if stack_params:
                    method = stack_params.get("method", "average")
                    rejection = stack_params.get("rejection", "sigma")
                    rejection_low = stack_params.get("rejection_low", 3.0)
                    rejection_high = stack_params.get("rejection_high", 3.0)
                    logging.info(f"Stack parameters: method={method}, rejection={rejection} {rejection_low} {rejection_high}")
                
                success = self.siril.run_siril_script(script_content, str(self.work_dir))
                
                if success:
                    # Vérifier que le fichier final a été créé directement dans output_dir
                    final_output = None
                    if preferred_final_output.exists():
                        final_output = preferred_final_output
                    elif alternate_final_output.exists():
                        final_output = alternate_final_output

                    if final_output is not None:
                        logging.info(f"Traitement complet réussi: {final_output}")
                        # Enregistrer le fichier de sortie créé
                        self.output_files.append(final_output)
                        self._export_jpg_from_fits(final_output)
                        return True
                    else:
                        logging.error(
                            f"Fichier de résultat non trouvé: {preferred_final_output} ou {alternate_final_output}"
                        )
                        return False
                else:
                    logging.error(f"Échec du traitement complet de la séquence {sequence_name}")
                    return False
            
            finally:
                # Nettoyer la séquence dans tous les cas
                self._cleanup_sequence(sequence_name)
        
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
        light_files = self.find_light_files()
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
        
        for group_key, light_infos in light_groups.items():
            try:
                if self.process_light_group(group_key, light_infos, stack_params):
                    success_count += 1
                    logging.info(f"Groupe '{group_key}' traité avec succès")
                else:
                    logging.error(f"Échec du traitement du groupe '{group_key}'")
            except Exception as e:
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