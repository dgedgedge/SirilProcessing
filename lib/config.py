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
        "dark_library_path": os.path.abspath(os.path.expanduser("~/darkLib")),
        "bias_library_path": os.path.abspath(os.path.expanduser("~/biasLib")),
        "work_dir": os.path.abspath(os.path.expanduser("~/tmp/sirilWorkDir")),
        "output_dir": os.path.abspath(os.path.expanduser("~/SirilProcessed")),
        "siril_mode": "flatpak",
        "cfa": False,
        "output_norm": "noscale",
        "rejection_method": "winsorizedsigma",
        "rejection_param1": 3.0,
        "rejection_param2": 3.0,
        "max_age_days": 182,
        "stack_method": "average",
        "temperature_precision": 0.2,
        "min_darks_threshold": 0,
        "validate_darks": False,
        "report": False,
        "input_dirs": None
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
            if "output_dir" in self._config:
                self._config["output_dir"] = os.path.abspath(self._config["output_dir"])
            
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
        Convertit automatiquement tous les chemins de répertoires en chemins absolus.
        """
        # Mise à jour des valeurs à partir des arguments
        updates = {
            "siril_path": args.siril_path,
            "siril_mode": args.siril_mode,
        }
        
        # Add work_dir if available
        if hasattr(args, 'work_dir') and args.work_dir:
            updates["work_dir"] = os.path.abspath(args.work_dir)
        
        # Add parameters for darkLibUpdate.py
        if hasattr(args, 'cfa'):
            updates["cfa"] = args.cfa
        if hasattr(args, 'output_norm'):
            updates["output_norm"] = args.output_norm
        if hasattr(args, 'max_age'):
            updates["max_age_days"] = args.max_age
        
        # Add rejection parameters
        if hasattr(args, 'rejection_method'):
            updates["rejection_method"] = args.rejection_method
        if hasattr(args, 'rejection_param1'):
            updates["rejection_param1"] = args.rejection_param1
        if hasattr(args, 'rejection_param2'):
            updates["rejection_param2"] = args.rejection_param2
        
        # Add stacking method
        if hasattr(args, 'stack_method'):
            updates["stack_method"] = args.stack_method
        
        # Add temperature precision if available
        if hasattr(args, 'temperature_precision'):
            updates["temperature_precision"] = args.temperature_precision
        
        # Add min darks threshold if available
        if hasattr(args, 'min_darks_threshold'):
            updates["min_darks_threshold"] = args.min_darks_threshold
        
        # Add validation options if available
        if hasattr(args, 'validate_darks'):
            updates["validate_darks"] = args.validate_darks
        if hasattr(args, 'report'):
            updates["report"] = args.report
        if hasattr(args, 'input_dirs') and args.input_dirs is not None:
            # Convertir tous les répertoires d'entrée en chemins absolus
            updates["input_dirs"] = [os.path.abspath(d) for d in args.input_dirs]
        
        # Add library paths based on which script is being used
        if hasattr(args, 'dark_library_path') and args.dark_library_path:
            updates["dark_library_path"] = os.path.abspath(args.dark_library_path)
        if hasattr(args, 'bias_library_path') and args.bias_library_path:
            updates["bias_library_path"] = os.path.abspath(args.bias_library_path)
        if hasattr(args, 'output_dir') and args.output_dir:
            updates["output_dir"] = os.path.abspath(args.output_dir)
            
        self.update(**updates)
