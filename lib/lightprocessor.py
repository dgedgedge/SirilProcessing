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
from lib.fits_info import FitsInfo
from lib.siril_utils import Sequence


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
                 siril_path: str = "siril",
                 siril_mode: str = "flatpak",
                 force_reprocess: bool = False,
                 dry_run: bool = False):
        """
        Initialise le processeur de light.
        
        Args:
            session_dir: Répertoire de la session contenant light/ et flat/
            dark_library_path: Chemin vers la librairie de master darks
            output_dir: Répertoire de sortie pour les résultats
            work_dir: Répertoire de travail temporaire
            temp_precision: Précision de correspondance des températures
            siril_path: Chemin vers l'exécutable Siril
            siril_mode: Mode d'exécution de Siril
            force_reprocess: Force le retraitement même si les fichiers existent
            dry_run: Simule le traitement sans l'exécuter
        """
        self.session_dir = Path(session_dir)
        self.dark_library_path = dark_library_path
        self.output_dir = Path(output_dir)
        self.work_dir = Path(work_dir)
        self.temp_precision = temp_precision
        self.siril_path = siril_path
        self.siril_mode = siril_mode
        self.force_reprocess = force_reprocess
        self.dry_run = dry_run
        
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
        
        if self.dark_library_path and not Path(self.dark_library_path).exists():
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
        
        # Chercher le master dark correspondant
        master_dark_path = self.find_matching_master_dark(reference_light)
        if not master_dark_path:
            logging.error(f"Impossible de traiter le groupe '{group_key}': aucun master dark correspondant")
            return False
        
        # Préparer les chemins de fichiers
        light_files = [Path(info.filepath) for info in light_infos]
        
        # Nom de la séquence basé sur le group_key
        sequence_name = f"light_{group_key}"
        
        # Répertoire de sortie pour ce groupe
        group_output_dir = self.output_dir / group_key
        if not self.dry_run:
            group_output_dir.mkdir(parents=True, exist_ok=True)
        
        # Vérifier si le fichier de sortie existe déjà
        final_output = group_output_dir / f"{sequence_name}_stacked.fit"
        if final_output.exists() and not self.force_reprocess:
            logging.info(f"Fichier de sortie existant, passage: {final_output}")
            return True
        
        if self.dry_run:
            logging.info(f"[DRY-RUN] Traiterait {len(light_files)} lights avec dark {master_dark_path}")
            logging.info(f"[DRY-RUN] Sortie: {final_output}")
            
            # Générer et afficher le script Siril en mode dry-run
            try:
                with Sequence(sequence_name, light_files, self.work_dir, str(self.session_dir)) as sequence:
                    script_content = sequence._generate_siril_script(str(master_dark_path), stack_params)
                    logging.info(f"[DRY-RUN] Script Siril qui serait généré:")
                    for i, line in enumerate(script_content.split('\n'), 1):
                        if line.strip():
                            logging.info(f"[DRY-RUN]   {i:2d}: {line}")
            except Exception as e:
                logging.warning(f"[DRY-RUN] Impossible de générer le script Siril: {e}")
            
            return True
        
        try:
            # Créer la séquence pour le traitement complet
            with Sequence(sequence_name, light_files, self.work_dir, str(self.session_dir)) as sequence:
                # Préparer la séquence (créer les liens symboliques)
                if not sequence.prepare():
                    logging.error(f"Échec de la préparation de la séquence {sequence_name}")
                    return False
                
                # Effectuer le traitement complet (conversion, calibration, alignement, stacking)
                if not sequence.convert_with_dark_subtraction(
                    dark_path=str(master_dark_path),
                    siril_path=self.siril_path,
                    siril_mode=self.siril_mode,
                    stack_params=stack_params
                ):
                    logging.error(f"Échec du traitement complet de la séquence {sequence_name}")
                    return False
                
                # Vérifier que le fichier final a été créé
                expected_result = self.work_dir / f"{sequence_name}_stacked.fits"
                if expected_result.exists():
                    # Déplacer le résultat vers le répertoire de sortie final
                    if expected_result != final_output:
                        import shutil
                        shutil.move(str(expected_result), str(final_output))
                    logging.info(f"Traitement complet réussi: {final_output}")
                    return True
                else:
                    logging.error(f"Fichier de résultat non trouvé: {expected_result}")
                    return False
        
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