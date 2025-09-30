#!/bin/env python3
import os
import json
import logging


class Config:
    """
    Classe pour charger, sauvegarder et accéder à la configuration du script.
    Gère la persistance des paramètres dans un fichier JSON.
    """
    # Valeurs par défaut pour chaque paramètre de configuration
    DEFAULTS = {
        "siril_path": "siril",
        "dark_library_path": os.path.expanduser("~/darkLib"),
        "bias_library_path": os.path.expanduser("~/biasLib"),
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
            if "bias_library_path" in self._config:
                self._config["bias_library_path"] = os.path.abspath(self._config["bias_library_path"])
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
        
        # Add library path based on which script is being used
        if hasattr(args, 'dark_library_path'):
            updates["dark_library_path"] = args.dark_library_path
        if hasattr(args, 'bias_library_path'):
            updates["bias_library_path"] = args.bias_library_path
            
        self.update(**updates)
