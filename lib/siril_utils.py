#!/bin/env python3
from datetime import time
import os
import subprocess
import logging
import shutil
from pathlib import Path
from typing import List, Optional


class Siril:
    """
    Classe pour gérer l'exécution de Siril avec validation et mémorisation des configurations.
    """
    
    # Attributs de classe pour la configuration globale par défaut
    _default_siril_path = "siril"
    _default_siril_mode = "flatpak"
    
    def __init__(self, siril_path: str = None, siril_mode: str = None):
        """
        Initialise une instance Siril.
        
        Args:
            siril_path: Chemin vers l'exécutable Siril (utilise la config de classe si None)
            siril_mode: Mode d'exécution ('native', 'flatpak', ou 'appimage') (utilise la config de classe si None)
            
        Raises:
            ValueError: Si la configuration n'est pas valide
        """
        self._siril_path = siril_path if siril_path is not None else self._default_siril_path
        self._siril_mode = siril_mode if siril_mode is not None else self._default_siril_mode
        self._validated = False
        
        # Validation lors de l'initialisation
        if not self._validate_configuration():
            raise ValueError(f"Configuration Siril invalide: path='{self._siril_path}', mode='{self._siril_mode}'")
    
    @classmethod
    def configure_defaults(cls, siril_path: str = None, siril_mode: str = None):
        """
        Configure les valeurs par défaut pour toutes les instances Siril futures.
        Valide immédiatement la configuration.
        
        Args:
            siril_path: Chemin par défaut vers l'exécutable Siril
            siril_mode: Mode d'exécution par défaut ('native', 'flatpak', ou 'appimage')
            
        Raises:
            ValueError: Si la configuration n'est pas valide
        """
        if siril_path is not None:
            cls._default_siril_path = siril_path
        if siril_mode is not None:
            cls._default_siril_mode = siril_mode
        
        # Validation immédiate de la nouvelle configuration
        temp_instance = cls()
        if not temp_instance._validated:
            # Restaurer les anciennes valeurs en cas d'échec
            if siril_path is not None:
                cls._default_siril_path = "siril"
            if siril_mode is not None:
                cls._default_siril_mode = "flatpak"
            raise ValueError(f"Configuration Siril invalide: path='{cls._default_siril_path}', mode='{cls._default_siril_mode}'")
        
        logging.info(f"Configuration Siril globale mise à jour et validée: path={cls._default_siril_path}, mode={cls._default_siril_mode}")
    
    @classmethod
    def get_default_config(cls) -> tuple[str, str]:
        """
        Retourne la configuration par défaut actuelle.
        
        Returns:
            Tuple (siril_path, siril_mode)
        """
        return cls._default_siril_path, cls._default_siril_mode
    
    @classmethod
    def create_with_defaults(cls):
        """
        Crée une nouvelle instance Siril avec la configuration par défaut.
        
        Returns:
            Instance Siril configurée avec les valeurs par défaut
        """
        return cls()
    
    @property
    def siril_path(self) -> str:
        """Retourne le chemin vers Siril."""
        return self._siril_path
    
    @siril_path.setter
    def siril_path(self, path: str):
        """Définit le chemin vers Siril et re-valide la configuration."""
        old_path = self._siril_path
        self._siril_path = path
        self._validated = False
        if not self._validate_configuration():
            # Restaurer l'ancienne valeur en cas d'échec
            self._siril_path = old_path
            self._validated = True  # L'ancienne configuration était valide
            raise ValueError(f"Chemin Siril invalide: '{path}'")
    
    @property
    def siril_mode(self) -> str:
        """Retourne le mode d'exécution de Siril."""
        return self._siril_mode
    
    @siril_mode.setter
    def siril_mode(self, mode: str):
        """Définit le mode d'exécution de Siril et re-valide la configuration."""
        old_mode = self._siril_mode
        self._siril_mode = mode
        self._validated = False
        if not self._validate_configuration():
            # Restaurer l'ancienne valeur en cas d'échec
            self._siril_mode = old_mode
            self._validated = True  # L'ancienne configuration était valide
            raise ValueError(f"Mode Siril invalide: '{mode}'")
    
    @property
    def is_validated(self) -> bool:
        """Retourne True si la configuration a été validée avec succès."""
        return self._validated
    
    def _validate_configuration(self) -> bool:
        """
        Valide la configuration Siril actuelle.
        
        Returns:
            True si la configuration est valide, False sinon
        """
        try:
            # Validation du mode
            valid_modes = ["native", "flatpak", "appimage"]
            if self._siril_mode not in valid_modes:
                logging.error(f"Mode Siril invalide: {self._siril_mode}. Modes valides: {valid_modes}")
                self._validated = False
                return False
            
            # Validation selon le mode
            if self._siril_mode == "flatpak":
                # Vérifier si flatpak est disponible
                result = subprocess.run(["flatpak", "--version"], 
                                      capture_output=True, text=True, check=False)
                if result.returncode != 0:
                    logging.error("Flatpak n'est pas disponible sur ce système")
                    self._validated = False
                    return False
                
                # Vérifier si Siril est installé via flatpak
                result = subprocess.run(["flatpak", "list", "--app"], 
                                      capture_output=True, text=True, check=False)
                if result.returncode != 0 or "org.siril.Siril" not in result.stdout:
                    logging.error("Siril n'est pas installé via Flatpak")
                    self._validated = False
                    return False
                    
            elif self._siril_mode in ["native", "appimage"]:
                # Vérifier si l'exécutable existe et est accessible
                if not shutil.which(self._siril_path) and not os.path.isfile(self._siril_path):
                    logging.error(f"Exécutable Siril introuvable: {self._siril_path}")
                    self._validated = False
                    return False
                
                # Vérifier si le fichier est exécutable
                if os.path.isfile(self._siril_path) and not os.access(self._siril_path, os.X_OK):
                    logging.error(f"Le fichier Siril n'est pas exécutable: {self._siril_path}")
                    self._validated = False
                    return False
            
            logging.info(f"Configuration Siril validée: mode={self._siril_mode}, path={self._siril_path}")
            self._validated = True
            return True
            
        except Exception as e:
            logging.error(f"Erreur lors de la validation de la configuration Siril: {e}")
            self._validated = False
            return False
    
    def run_siril_script(self, siril_script_content: str, working_dir: str) -> bool:
        """
        Exécute un script Siril temporaire en utilisant la configuration de l'instance.
        
        Args:
            siril_script_content: Contenu du script Siril à exécuter
            working_dir: Répertoire de travail pour l'exécution du script
        
        Returns:
            True si l'exécution a réussi, False sinon
        """
        # Vérifier que la configuration est valide
        if not self._validated:
            logging.error("Configuration Siril non valide. Impossible d'exécuter le script.")
            return False
        
        script_path = os.path.join(working_dir, "siril_script.sps")
        try:
            with open(script_path, "w") as f:
                f.write(siril_script_content)

            logging.info(f"Exécution du script Siril {script_path} dans {working_dir}:\n{siril_script_content}")

            # Construction de la commande selon le mode
            if self._siril_mode == "native":
                cmd = [self._siril_path, "-s", script_path]
            elif self._siril_mode == "flatpak":
                cmd = ["flatpak", "run", "org.siril.Siril", "-s", script_path]
            elif self._siril_mode == "appimage":
                cmd = [self._siril_path, "-s", script_path]
            else:
                logging.error(f"Mode Siril inconnu: {self._siril_mode}")
                return False

            result = subprocess.run(
                cmd,
                cwd=working_dir,
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode != 0:
                logging.error(f"Le script Siril a échoué avec le code d'erreur {result.returncode}.")
                logging.error(f"Stdout Siril:\n{result.stdout}")
                logging.error(f"Stderr Siril:\n{result.stderr}")
                return False
            else:
                logging.info("Script Siril exécuté avec succès.")
                logging.debug(f"Stdout Siril:\n{result.stdout}")
                return True
        except FileNotFoundError:
            logging.error(f"Exécutable Siril introuvable à '{self._siril_path}'. Veuillez vérifier le chemin.")
            return False
        except Exception as e:
            logging.error(f"Erreur lors de l'exécution du script Siril: {e}")
            return False
        finally:
            if os.path.exists(script_path):
                os.remove(script_path)  # Nettoyage du script temporaire


# Fonction de compatibilité pour maintenir l'ancienne interface
def run_siril_script(siril_script_content: str, working_dir: str, siril_path: str = "siril", siril_mode: str = "flatpak") -> bool:
    """
    Fonction de compatibilité pour exécuter un script Siril temporaire.
    
    DEPRECATED: Utilisez la classe Siril à la place pour une meilleure gestion des configurations.
    
    Args:
        siril_script_content: Contenu du script Siril à exécuter
        working_dir: Répertoire de travail pour l'exécution du script
        siril_path: Chemin vers l'exécutable Siril
        siril_mode: Mode d'exécution de Siril ('native', 'flatpak', ou 'appimage')
    
    Returns:
        True si l'exécution a réussi, False sinon
    """
    siril_instance = Siril(siril_path=siril_path, siril_mode=siril_mode)
    return siril_instance.run_siril_script(siril_script_content, working_dir)



