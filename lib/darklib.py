#!/bin/env python3
"""
DarkLib class for managing dark frame library.
This module provides functionality to group, stack, and maintain master dark frames.
"""
import os
import datetime
import shutil
import logging

from lib.fits_info import FitsInfo
from lib.siril_utils import run_siril_script


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
                logging.warning(f"Invalid dark data in {info.filepath}. Ignored.")

        if latest_infoFile is None:
            logging.warning(f"No valid dark data to stack for group {group_key}. Ignored.")
            return

        # Créer le nom du master dark basé sur les métadonnées
        master_dark_name = latest_infoFile.master_dark_name()
        master_dark_path = os.path.join(self.dark_library_path, master_dark_name)

        # Lire les master darks existants
        existing_darks = self.read_existing_master_darks()

        # Vérifier s'il existe un master dark équivalent
        replace_existing = False
        for existing_dark in existing_darks:
            if existing_dark.is_equivalent(latest_infoFile):
                # Compare les dates d'observation
                if latest_infoFile.date_obs() > existing_dark.date_obs():
                    # Si les paramètres de stacking sont identiques et le nouveau est plus récent, remplacer
                    if self.has_same_stacking_params(existing_dark):
                        logging.info(
                            f"Master dark existant pour {group_key} trouvé (date: {existing_dark.date_obs()}). "
                            f"Les nouveaux darks sont plus récents (date: {latest_infoFile.date_obs()}). "
                            f"Master dark remplacé."
                        )
                        replace_existing = True
                        master_dark_path = existing_dark.filepath  # Utiliser le même nom de fichier
                        break
                    else:
                        # Si les paramètres de stacking sont différents, ne pas remplacer
                        logging.info(
                            f"Master dark existant pour {group_key} trouvé (date: {existing_dark.date_obs()}). "
                            f"Les paramètres de stacking sont différents. "
                            f"Nouveau master dark créé avec un nom différent."
                        )
                        # Ne pas remplacer, laisser le nouveau nom
                        break
                else:
                    logging.info(
                        f"Master dark existant pour {group_key} trouvé (date: {existing_dark.date_obs()}). "
                        f"Les nouveaux darks ne sont pas plus récents (date: {latest_infoFile.date_obs()}). "
                        f"Aucune création de master dark."
                    )
                    return

        if not replace_existing:
            if os.path.exists(master_dark_path):
                logging.warning(f"Master dark {master_dark_path} existe déjà avec le même nom mais sans match équivalent. Aucune création.")
                return

        # Si on arrive ici, soit on remplace, soit on crée un nouveau master dark
        logging.info(f"Stacking {len(fitsinfo_list)} darks for group {group_key}...")

        # Construire la commande Siril pour empiler les darks
        # La méthode pour le stacking
        if self.siril_stack_method == "median":
            stack_line = "stack dark median"
        else:  # average
            if self.siril_rejection_method == "none":
                # Pas de rejet
                stack_line = "stack dark rej 0 0 -norm=no"
            else:
                # Avec rejet
                stack_line = f"stack dark rej {self.siril_rejection_param1} {self.siril_rejection_param2} -norm=no -rej={self.siril_rejection_method}"
        
        # Ajouter la normalisation selon le paramètre
        if self.siril_output_norm == "addscale":
            stack_line += " -out=addscale"
        elif self.siril_output_norm == "rejection":
            stack_line += " -out=rejection"
        else:  # noscale
            stack_line += " -out=noscale"

        # Stocker la commande de stacking pour le header
        stack_command = stack_line

        siril_output_name = "stacked_dark_result.fit"

        # Créer les liens symboliques dans link_dir (et mettre à jour FitsInfo.filepath)
        # Les liens sont créés par create_symlink qui update aussi FitsInfo.filepath
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
            # Déplacer le master dark vers la bibliothèque
            shutil.move(temp_master_dark_path, master_dark_path)
            logging.info(f"Master dark créé : {master_dark_path}")

            # Mettre à jour le header du master dark pour y ajouter les infos du groupe
            try:
                # Créer un objet FitsInfo pour le master dark
                masterDark = FitsInfo(master_dark_path)
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

    def has_same_stacking_params(self, existing_dark: FitsInfo) -> bool:
        """
        Vérifie si les paramètres de stacking du master dark existant correspondent
        à ceux de l'instance DarkLib actuelle.
        """
        # Récupérer la commande de stacking du master dark existant
        existing_stack_command = existing_dark.stack_command() if hasattr(existing_dark, 'stack_command_value') and existing_dark.stack_command() else None
        
        # Construire la commande de stacking de l'instance actuelle
        if self.siril_stack_method == "median":
            current_stack_line = "stack dark median"
        else:  # average
            if self.siril_rejection_method == "none":
                current_stack_line = "stack dark rej 0 0 -norm=no"
            else:
                current_stack_line = f"stack dark rej {self.siril_rejection_param1} {self.siril_rejection_param2} -norm=no -rej={self.siril_rejection_method}"
        
        # Ajouter la normalisation selon le paramètre
        if self.siril_output_norm == "addscale":
            current_stack_line += " -out=addscale"
        elif self.siril_output_norm == "rejection":
            current_stack_line += " -out=rejection"
        else:  # noscale
            current_stack_line += " -out=noscale"
        
        # Comparer les commandes
        return existing_stack_command == current_stack_line

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
        
        # Calculate total length and create separator
        total_length = base_length + max_variable_length
        separator = "-" * total_length
        
        # En-tête du tableau
        logging.getLogger().userinfo(separator)
        header = f"{'DATE-OBS':<25} {'EXPOSURE':<10} {'CCD-TEMP':<10} {'GAIN':<8} {'BINNING':<10} {'CAMERA':<20} {'NDARKS':<8} {'Commande/Fichier':<{max_variable_length}}"
        logging.getLogger().userinfo(header)
        logging.getLogger().userinfo(separator)
        
        # Trier les master darks par date d'observation décroissante
        existing_darks.sort(key=lambda x: x.date_obs(), reverse=True)
        
        for dark in existing_darks:
            date_obs_str = dark.date_obs().isoformat() if dark.date_obs() else "N/A"
            exposure_str = f"{dark.exptime():.3f}" if dark.exptime() is not None else "N/A"
            temp_str = f"{dark.ccd_temp():.2f}" if dark.ccd_temp() is not None else "N/A"
            gain_str = str(dark.gain()) if dark.gain() is not None else "N/A"
            binning_str = f"{dark.binning()[0]}x{dark.binning()[1]}" if dark.binning() else "N/A"
            camera_str = dark.camera() if dark.camera() else "N/A"
            ndarks_str = str(dark.ndarks()) if hasattr(dark, 'ndarks_value') and dark.ndarks() else "N/A"
            filename = os.path.basename(dark.filepath)
            stack_cmd = dark.stack_command() if hasattr(dark, 'stack_command_value') and dark.stack_command() else "N/A"
            
            # Print row with stack command
            row = f"{date_obs_str:<25} {exposure_str:<10} {temp_str:<10} {gain_str:<8} {binning_str:<10} {camera_str:<20} {ndarks_str:<8} {stack_cmd:<{max_variable_length}}"
            logging.getLogger().userinfo(row)
            
            # Print filename row with indentation
            filename_row = f"{'':<25} {'':<10} {'':<10} {'':<8} {'':<10} {'':<20} {'':<8} {'→ ' + filename:<{max_variable_length}}"
            logging.getLogger().userinfo(filename_row)
        
        logging.getLogger().userinfo(separator)
        logging.info(f"Total: {len(existing_darks)} master darks dans la bibliothèque.")

    def process_all_groups(self, dark_groups):
        """
        Traite tous les groupes de darks pour créer des master darks.
        """
        for group_key, fitsinfo_list in dark_groups.items():
            logging.info(f"Processing group {group_key} with {len(fitsinfo_list)} files.")
            
            # Créer un répertoire temporaire pour le traitement
            process_dir = os.path.join(self.work_dir, f"process_{group_key.replace('/', '_').replace(' ', '_')}")
            os.makedirs(process_dir, exist_ok=True)
            
            # Créer un sous-répertoire pour les liens
            link_dir = os.path.join(process_dir, "link")
            os.makedirs(link_dir, exist_ok=True)

            # Créer des liens symboliques vers les fichiers dans link_dir
            linked_infos = []
            for i, info in enumerate(fitsinfo_list, start=1):
                link_name = f"dark_{i:04d}.fit"
                link_path = os.path.join(link_dir, link_name)
                linked_info = info.create_symlink(link_path)
                linked_infos.append(linked_info)

            # Passer la liste des liens au stacking
            self.stack_and_save_master_dark(group_key, linked_infos, process_dir, link_dir)

            # Nettoyer le répertoire process après traitement
            shutil.rmtree(process_dir, ignore_errors=True)
