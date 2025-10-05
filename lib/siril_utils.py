#!/bin/env python3
from datetime import time
import os
import subprocess
import logging
import shutil
from pathlib import Path
from typing import List, Optional


def run_siril_script(siril_script_content: str, working_dir: str, siril_path: str = "siril", siril_mode: str = "flatpak") -> bool:
    """
    Executes a temporary Siril script.
    
    Args:
        siril_script_content: Content of the Siril script to execute
        working_dir: Working directory for the script execution
        siril_path: Path to the Siril executable
        siril_mode: Mode for running Siril ('native', 'flatpak', or 'appimage')
    
    Returns:
        True if execution was successful, False otherwise
    """
    script_path = os.path.join(working_dir, "siril_script.sps")
    try:
        with open(script_path, "w") as f:
            f.write(siril_script_content)

        logging.info(f"Executing Siril script {script_path} in {working_dir}:\n{siril_script_content}")

        # --- Build command based on siril_mode ---
        if siril_mode == "native":
            cmd = [siril_path, "-s", script_path]
        elif siril_mode == "flatpak":
            cmd = ["flatpak", "run", "org.siril.Siril", "-s", script_path]
        elif siril_mode == "appimage":
            cmd = [siril_path, "-s", script_path]
        else:
            logging.error(f"Unknown siril_mode: {siril_mode}")
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
        logging.error(f"Siril executable not found at '{siril_path}'. Please check the path.")
        return False
    except Exception as e:
        logging.error(f"Error executing Siril script: {e}")
        return False
    finally:
        if os.path.exists(script_path):
            os.remove(script_path)  # Clean up temporary script


class Sequence:
    """
    Gère une séquence Siril avec création automatique de liens symboliques.
    
    Cette classe facilite la gestion des séquences Siril en créant automatiquement
    les liens symboliques nécessaires et en gérant le nettoyage.
    """
    
    def __init__(self, name: str, file_paths: List[str], output_dir: str, session_dir: str = None):
        """
        Initialise une nouvelle séquence.
        
        Args:
            name: Nom de la séquence
            file_paths: Liste des chemins vers les fichiers à inclure
            output_dir: Répertoire de sortie pour les liens et résultats
            session_dir: Répertoire de session (pour nommer le fichier de sauvegarde)
        """
        self.name = name
        self.file_paths = file_paths
        self.output_dir = Path(output_dir)
        self.session_dir_path = Path(session_dir) if session_dir else None
        self.sequence_dir = self.output_dir / name
        self.links_created = False
    
    def __enter__(self):
        """Support du context manager."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Nettoyage automatique en sortie du context manager."""
        self.cleanup()
    
    def prepare(self) -> bool:
        """
        Prépare la séquence en créant les liens symboliques.
        
        Returns:
            True si la préparation a réussi, False sinon
        """
        try:
            # Créer le répertoire de la séquence
            self.sequence_dir.mkdir(parents=True, exist_ok=True)
            
            # Créer les liens symboliques avec la convention Siril
            for i, file_path in enumerate(self.file_paths):
                source_path = Path(file_path).resolve()
                if not source_path.exists():
                    logging.error(f"Fichier source inexistant: {source_path}")
                    return False
                
                # Nom du lien selon la convention Siril
                link_name = f"{self.name}_{i:04d}.fit"
                link_path = self.sequence_dir / link_name
                
                # Supprimer le lien existant s'il y en a un
                if link_path.exists():
                    link_path.unlink()
                
                # Créer le lien symbolique
                link_path.symlink_to(source_path)
                logging.debug(f"Lien créé: {link_path} -> {source_path}")
            
            self.links_created = True
            logging.info(f"Séquence '{self.name}' préparée avec {len(self.file_paths)} fichiers")
            return True
            
        except Exception as e:
            logging.error(f"Erreur lors de la préparation de la séquence {self.name}: {e}")
            return False
    
    def convert(self, siril_path: str = "siril", siril_mode: str = "flatpak") -> bool:
        """
        Convertit la séquence en utilisant Siril.
        
        Args:
            siril_path: Chemin vers l'exécutable Siril
            siril_mode: Mode d'exécution de Siril
            
        Returns:
            True si la conversion a réussi, False sinon
        """
        if not self.links_created:
            logging.error(f"Sequence {self.name} not prepared. Call prepare() first.")
            return False
        
        # Script Siril pour la conversion
        script_content = f"""cd {self.output_dir}
convert {self.name} -out={self.output_dir}
close"""
        
        success = run_siril_script(script_content, str(self.output_dir), siril_path, siril_mode)
        
        if success:
            # Vérifier la présence du fichier de séquence Siril
            expected_seq_file = os.path.join(self.output_dir, f"{self.name}.seq")
            if os.path.exists(expected_seq_file):
                logging.debug(f"Sequence file created successfully: {expected_seq_file}")
                return True
            else:
                logging.error(f"Siril convert succeeded but sequence file not found: {expected_seq_file}")
                
                # Lister le contenu du répertoire de travail pour diagnostic
                logging.error(f"Contents of output directory '{self.output_dir}':")
                try:
                    if os.path.exists(self.output_dir):
                        items = sorted(os.listdir(self.output_dir))
                        if items:
                            for item in items:
                                item_path = os.path.join(self.output_dir, item)
                                if os.path.isdir(item_path):
                                    logging.error(f"  [DIR]  {item}/")
                                else:
                                    file_size = os.path.getsize(item_path)
                                    logging.error(f"  [FILE] {item} ({file_size} bytes)")
                        else:
                            logging.error("  (directory is empty)")
                    else:
                        logging.error("  (output directory does not exist)")
                except Exception as e:
                    logging.error(f"  Error listing directory contents: {e}")
                
                # Lister aussi le contenu du répertoire de la séquence
                logging.error(f"Contents of sequence directory '{self.sequence_dir}':")
                try:
                    if os.path.exists(self.sequence_dir):
                        items = sorted(os.listdir(self.sequence_dir))
                        if items:
                            for item in items:
                                item_path = os.path.join(self.sequence_dir, item)
                                if os.path.isdir(item_path):
                                    logging.error(f"  [DIR]  {item}/")
                                else:
                                    file_size = os.path.getsize(item_path)
                                    logging.error(f"  [FILE] {item} ({file_size} bytes)")
                        else:
                            logging.error("  (directory is empty)")
                    else:
                        logging.error("  (sequence directory does not exist)")
                except Exception as e:
                    logging.error(f"  Error listing sequence directory contents: {e}")
                
                return False
        else:
            logging.error(f"Siril convert failed for sequence {self.name}")
            return False
    
    def _generate_siril_script(self, dark_path: str, stack_params: dict = None) -> str:
        """
        Génère le script Siril pour le traitement complet.
        
        Args:
            dark_path: Chemin vers le fichier master dark
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
        
        # Adaptation des noms de méthodes pour Siril
        siril_method = stack_params.get("method", "average")
        if siril_method == "average":
            siril_method = "mean"  # Siril utilise "mean" au lieu de "average"
        
        rejection = stack_params.get("rejection", "sigma")
        rejection_low = stack_params.get("rejection_low", 3.0)
        rejection_high = stack_params.get("rejection_high", 3.0)
        
        # Construire la commande stack selon la méthode et le type de rejet
        if siril_method == "mean":
            # Pour la moyenne simple sans rejet
            stack_command = f"stack r_pp_{self.name} mean -output_norm -out={self.output_dir}/{self.name}_stacked"
        elif siril_method == "median":
            # Pour la médiane
            stack_command = f"stack r_pp_{self.name} rej median {rejection_low} {rejection_high} -output_norm -out={self.output_dir}/{self.name}_stacked"
        else:
            # Pour l'empilement avec rejet (moyenne avec rejet)
            stack_command = f"stack r_pp_{self.name} rej {rejection} {rejection_low} {rejection_high} -output_norm -out={self.output_dir}/{self.name}_stacked"
        
        # Créer le répertoire de traitement
        process_dir = self.output_dir / "process"
        
        # Obtenir le nom du répertoire de session pour le fichier de sauvegarde
        if self.session_dir_path:
            save_filename = self.session_dir_path.name
        else:
            save_filename = self.name
        
        # Script Siril pour le traitement complet
        script_content = f"""requires 1.2
# Convert Light Frames to .fit files
cd {self.sequence_dir}
convert {self.name} -out={process_dir}
cd {process_dir}

# Pre-process Light Frames (calibration with dark subtraction)
calibrate {self.name} -dark={dark_path}

# Align lights
register pp_{self.name}

# Stack calibrated lights to result.fit
{stack_command}


cd {self.output_dir}
close"""
        
        return script_content
    
    def convert_with_dark_subtraction(self, 
                                     dark_path: str, 
                                     siril_path: str = "siril", 
                                     siril_mode: str = "flatpak",
                                     stack_params: dict = None) -> bool:
        """
        Effectue le traitement complet des lights : conversion, calibration, alignement et stacking.
        
        Args:
            dark_path: Chemin vers le fichier master dark
            siril_path: Chemin vers l'exécutable Siril
            siril_mode: Mode d'exécution de Siril
            stack_params: Paramètres de stacking (method, rejection, rejection_low, rejection_high)
            
        Returns:
            True si le traitement complet a réussi, False sinon
        """
        if not self.links_created:
            logging.error(f"Sequence {self.name} not prepared. Call prepare() first.")
            return False
        
        if not Path(dark_path).exists():
            logging.error(f"Master dark file not found: {dark_path}")
            return False
        
        # Créer le répertoire de traitement
        process_dir = self.output_dir / "process"
        process_dir.mkdir(parents=True, exist_ok=True)
        
        # Générer le script Siril
        script_content = self._generate_siril_script(dark_path, stack_params)
        
        # Paramètres par défaut si non spécifiés
        if stack_params is None:
            stack_params = {
                "method": "average",
                "rejection": "sigma", 
                "rejection_low": 3.0,
                "rejection_high": 3.0
            }
        
        # Adaptation des noms de méthodes pour Siril
        siril_method = stack_params.get("method", "average")
        if siril_method == "average":
            siril_method = "mean"  # Siril utilise "mean" au lieu de "average"
        
        rejection = stack_params.get("rejection", "sigma")
        rejection_low = stack_params.get("rejection_low", 3.0)
        rejection_high = stack_params.get("rejection_high", 3.0)
        if siril_method == "average":
            siril_method = "mean"  # Siril utilise "mean" au lieu de "average"
        
        
        logging.info(f"Executing complete light processing workflow for sequence {self.name}")
        # Extraire des infos pour le log depuis stack_params
        if stack_params:
            siril_method = stack_params.get("method", "average")
            if siril_method == "average":
                siril_method = "mean"
            rejection = stack_params.get("rejection", "sigma")
            rejection_low = stack_params.get("rejection_low", 3.0)
            rejection_high = stack_params.get("rejection_high", 3.0)
            logging.info(f"Stack parameters: method={siril_method}, rejection={rejection} {rejection_low} {rejection_high}")
        
        success = run_siril_script(script_content, str(self.output_dir), siril_path, siril_mode)

        if success:
            # Vérifier la présence du fichier final stacké
            expected_result_file = self.output_dir / f"{self.name}_stacked.fits"
            if expected_result_file.exists():
                logging.info(f"Complete light processing workflow successful: {expected_result_file}")
                return True
            else:
                logging.error(f"Light processing workflow succeeded but result file not found: {expected_result_file}")
                
                # Lister le contenu du répertoire de sortie pour diagnostic
                logging.error(f"Contents of output directory '{self.output_dir}':")
                try:
                    if os.path.exists(self.output_dir):
                        items = sorted(os.listdir(self.output_dir))
                        if items:
                            for item in items:
                                item_path = os.path.join(self.output_dir, item)
                                if os.path.isdir(item_path):
                                    logging.error(f"  [DIR]  {item}/")
                                else:
                                    file_size = os.path.getsize(item_path)
                                    logging.error(f"  [FILE] {item} ({file_size} bytes)")
                        else:
                            logging.error("  (directory is empty)")
                    else:
                        logging.error("  (output directory does not exist)")
                except Exception as e:
                    logging.error(f"  Error listing directory contents: {e}")
                
                return False
        else:
            logging.error(f"Complete light processing workflow failed for sequence {self.name}")
            return False
    
    def cleanup(self) -> None:
        """
        Nettoie les fichiers temporaires de la séquence.
        """
        if self.sequence_dir.exists():
            try:
                shutil.rmtree(self.sequence_dir)
                logging.debug(f"Répertoire de séquence nettoyé: {self.sequence_dir}")
            except Exception as e:
                logging.warning(f"Erreur lors du nettoyage de {self.sequence_dir}: {e}")
        
        self.links_created = False
