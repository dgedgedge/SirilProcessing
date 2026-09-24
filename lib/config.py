#!/bin/env python3
import os
import argparse
from pathlib import Path
import json
import logging
from threading import Lock
from copy import deepcopy


class _LegacyDefaults:
    """Vue de compatibilité des défauts appartenant aux traitements."""

    def __get__(self, instance, owner):
        from lib.darkprocess import DarkLib
        from lib.lightprocessor import LightProcessor
        from lib.siril_utils import Siril
        return {**DarkLib.CONFIG_DEFAULTS, **LightProcessor.CONFIG_DEFAULTS,
                **Siril.CONFIG_DEFAULTS}


class Config:
    """
    Classe pour charger, sauvegarder et accéder à la configuration du script.
    Gère la persistance des paramètres dans un fichier JSON.
    Une seule instance et un seul fichier sont utilisés par processus.
    """
    _instance = None
    _instance_lock = Lock()

    # Vue de compatibilité ; les déclarations restent dans les traitements.
    DEFAULTS = _LegacyDefaults()
    
    def __new__(cls, config_file=None):
        """
        Charge le fichier au premier appel, puis retourne l'instance partagée.
        Un autre fichier explicite est refusé ; load() permet un rechargement.
        """
        with cls._instance_lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance.config_file = str(Path(config_file or "~/.siril_darklib_config.json").expanduser().resolve())
                instance._config = {}
                instance._runtime = {}
                instance._parameters = {}
                instance._path_parameters = set()
                instance.load()
                cls._instance = instance
            elif config_file is not None:
                requested = str(Path(config_file).expanduser().resolve())
                if requested != cls._instance.config_file:
                    raise ValueError(
                        f"Config utilise déjà {cls._instance.config_file}, "
                        f"impossible de sélectionner {requested}"
                    )
            return cls._instance
    
    @classmethod
    def from_command_line(cls, argv=None):
        """Charge --config avant de définir les valeurs par défaut du parseur complet."""
        parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        parser.add_argument("--config")
        args, _ = parser.parse_known_args(argv)
        return cls(args.config) if args.config else cls()

    def add_arguments(self, parser):
        """Options communes de sélection et de sauvegarde de la configuration."""
        parser.add_argument("--config", default=str(self.config_file),
                            help="Fichier JSON de configuration partagé")
        parser.add_argument("-S", "--save-config", action="store_true",
                            help="Sauvegarder les paramètres dans le fichier de configuration")

    def register(self, provider, parser, **kwargs):
        """Interroge un traitement sans changer son API argparse historique.

        parameter_persistence indique, par destination, les exceptions à la
        rémanence des options. Les arguments positionnels sont toujours locaux
        à l'exécution. config_keys permet de conserver les anciennes clés JSON.
        """
        previous = set(parser._actions)
        provider.add_arguments(parser, **kwargs)
        persistence = getattr(provider, 'parameter_persistence', {})
        aliases = getattr(provider, 'config_keys', {})
        self._path_parameters.update(getattr(provider, 'config_path_parameters', ()))
        for action in parser._actions:
            if action in previous or action.dest == argparse.SUPPRESS:
                continue
            dest = action.dest
            key = aliases.get(dest, dest)
            persistent = bool(action.option_strings) and persistence.get(dest, True)
            metadata = (key, persistent, action.default)
            if dest in self._parameters:
                old = self._parameters[dest]
                if old[:2] != metadata[:2]:
                    raise ValueError(f"Déclarations incompatibles pour {dest}")
            else:
                self._parameters[dest] = metadata
            if persistent and key in self._config:
                value = deepcopy(self._config[key])
                if value is not None and action.type is not None:
                    try:
                        value = ([action.type(item) for item in value]
                                 if isinstance(value, list) else action.type(value))
                    except (TypeError, ValueError) as exc:
                        parser.error(f"Configuration invalide pour {dest}: {exc}")
                action.default = value
        return provider

    def parse_args(self, parser, argv=None):
        """Résout CLI > fichier > défaut local, sans écriture implicite."""
        args = parser.parse_args(argv)
        self.capture_args(args)
        return args

    def capture_args(self, args):
        """Actualise les valeurs accessibles aux traitements pour cet appel."""
        self._runtime = deepcopy(vars(args))
        for dest, (key, _, _) in self._parameters.items():
            if dest in self._runtime:
                self._runtime[key] = deepcopy(self._runtime[dest])

    def arguments(self):
        """Copie des paramètres résolus, utilisable par les anciennes API."""
        return argparse.Namespace(**deepcopy(self._runtime))

    def save_requested(self, args):
        """Sauvegarde seulement les paramètres déclarés rémanents avec -S."""
        self.capture_args(args)
        if not getattr(args, 'save_config', False):
            return True
        return self.save()

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
            for dest, (key, persistent, _) in self._parameters.items():
                if persistent and dest in self._runtime:
                    self._config[key] = deepcopy(self._runtime[dest])
            paths = self._path_parameters
            if not self._parameters:
                from lib.darkprocess import DarkLib
                from lib.lightprocessor import LightProcessor
                paths = DarkLib.config_path_parameters | LightProcessor.config_path_parameters
            for key in paths:
                value = self._config.get(key)
                if value is not None:
                    self._config[key] = ([os.path.abspath(item) for item in value]
                                         if isinstance(value, list) else os.path.abspath(value))
            
            Path(self.config_file).parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, "w") as f:
                transient = {key for key, persistent, _ in self._parameters.values() if not persistent}
                json.dump({key: value for key, value in self._config.items() if key not in transient},
                          f, indent=2, default=self._json_value)
            logging.info(f"Configuration sauvegardée dans {self.config_file}")
            return True
        except Exception as e:
            logging.error(f"Erreur lors de la sauvegarde de la configuration: {e}")
            return False

    @staticmethod
    def _json_value(value):
        if isinstance(value, Path):
            return str(value)
        raise TypeError(f"Valeur non sérialisable: {type(value).__name__}")
    
    def get(self, key, default=None):
        """
        Récupère une valeur de configuration.
        Si la clé n'existe pas, renvoie la valeur par défaut spécifiée ou celle définie dans DEFAULTS.
        """
        if key in self._runtime:
            return self._runtime[key]
        for dest, (config_key, persistent, declared_default) in self._parameters.items():
            if key in (dest, config_key):
                return self._config.get(config_key, declared_default) if persistent else declared_default
        if default is None and key in self.DEFAULTS:
            default = self.DEFAULTS[key]
        return self._config.get(key, default)
    
    def set(self, key, value):
        """
        Définit une valeur de configuration.
        """
        self._config[key] = value
        if key in self._runtime:
            self._runtime[key] = value
    
    def update(self, **kwargs):
        """
        Met à jour plusieurs valeurs de configuration en une seule fois.
        """
        for key, value in kwargs.items():
            self.set(key, value)
    
    def to_dict(self):
        """
        Retourne la configuration sous forme de dictionnaire.
        """
        return dict(self._config)
    
    def set_from_args(self, args):
        """Compatibilité : copie les paramètres rémanents sans écrire le fichier.

        Les applications enregistrées utilisent leurs déclarations locales.
        Les anciens appelants conservent les clés et alias historiques.
        """
        if self._parameters:
            self.capture_args(args)
            for dest, (key, persistent, _) in self._parameters.items():
                if persistent and hasattr(args, dest):
                    self.set(key, deepcopy(getattr(args, dest)))
            return
        from lib.darkprocess import DarkLib
        defaults = self.DEFAULTS
        aliases = DarkLib.config_keys
        for dest, value in vars(args).items():
            key = aliases.get(dest, dest)
            if key in defaults or key in DarkLib.legacy_parameters:
                if value is not None:
                    self.set(key, deepcopy(value))
