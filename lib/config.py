#!/bin/env python3
"""Configuration JSON partagée : résolution des options et sauvegarde atomique explicite."""

from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from threading import Lock

from lib.type_defs import ArgumentProvider, ConfigValue


class _LegacyDefaults:
    """Vue de compatibilité des défauts appartenant aux traitements."""

    def __get__(
        self, instance: Config | None, owner: type[Config]
    ) -> dict[str, ConfigValue]:
        """Assemble à la demande les défauts historiques des traitements et de Siril."""
        from lib.darkprocess import DarkLib
        from lib.lightprocessor import LightProcessor
        from lib.siril_utils import Siril

        return {
            **DarkLib.CONFIG_DEFAULTS,
            **LightProcessor.CONFIG_DEFAULTS,
            **Siril.CONFIG_DEFAULTS,
        }


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

    def __new__(cls, config_file: Path | str | None = None) -> Config:
        """Retourne l’instance unique du processus sous verrou de création."""
        with cls._instance_lock:
            if cls.__dict__.get("_instance") is None:
                cls._instance = super().__new__(cls)
            return cls._instance

    def __init__(self, config_file: Path | str | None = None) -> None:
        """Initialise une fois, y compris depuis un constructeur hérité."""
        with self._instance_lock:
            if not hasattr(self, "_config"):
                self.config_file = str(
                    Path(config_file or "~/.siril_darklib_config.json")
                    .expanduser()
                    .resolve()
                )
                self._config = {}
                self._runtime = {}
                self._parameters = {}
                self._path_parameters = set()
                self.load()
            elif config_file is not None:
                requested = str(Path(config_file).expanduser().resolve())
                if requested != self.config_file:
                    raise ValueError(
                        f"Config utilise déjà {self.config_file}, "
                        f"impossible de sélectionner {requested}"
                    )

    @classmethod
    def from_command_line(cls, argv: Sequence[str] | None = None) -> Config:
        """Charge --config avant de définir les valeurs par défaut du parseur complet."""
        parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        parser.add_argument("--config")
        args, _ = parser.parse_known_args(argv)
        return cls(args.config) if args.config else cls()

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Options communes de sélection et de sauvegarde de la configuration."""
        parser.add_argument(
            "--config",
            default=str(self.config_file),
            help="Fichier JSON de configuration partagé",
        )
        parser.add_argument(
            "-S",
            "--save-config",
            action="store_true",
            help="Sauvegarder les paramètres dans le fichier de configuration",
        )

    def register(
        self,
        provider: ArgumentProvider,
        parser: argparse.ArgumentParser,
        **kwargs: ConfigValue,
    ) -> ArgumentProvider:
        """Interroge un traitement sans changer son API argparse historique.

        parameter_persistence indique, par destination, les exceptions à la
        rémanence des options. Les arguments positionnels sont toujours locaux
        à l'exécution. config_keys permet de conserver les anciennes clés JSON.
        """
        previous = set(parser._actions)
        provider.add_arguments(parser, **kwargs)
        persistence = getattr(provider, "parameter_persistence", {})
        aliases = getattr(provider, "config_keys", {})
        self._path_parameters.update(getattr(provider, "config_path_parameters", ()))
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
                        value = (
                            [action.type(item) for item in value]
                            if isinstance(value, list)
                            else action.type(value)
                        )
                    except (TypeError, ValueError) as exc:
                        parser.error(f"Configuration invalide pour {dest}: {exc}")
                action.default = value
        return provider

    def parse_args(
        self, parser: argparse.ArgumentParser, argv: Sequence[str] | None = None
    ) -> argparse.Namespace:
        """Résout CLI > fichier > défaut local, sans écriture implicite."""
        args = parser.parse_args(argv)
        self.capture_args(args)
        return args

    def capture_args(self, args: argparse.Namespace) -> None:
        """Actualise les valeurs accessibles aux traitements pour cet appel."""
        self._runtime = deepcopy(vars(args))
        for dest, (key, _, _) in self._parameters.items():
            if dest in self._runtime:
                self._runtime[key] = deepcopy(self._runtime[dest])

    def arguments(self) -> argparse.Namespace:
        """Copie des paramètres résolus, utilisable par les anciennes API."""
        return argparse.Namespace(**deepcopy(self._runtime))

    def save_requested(self, args: argparse.Namespace) -> bool:
        """Sauvegarde seulement les paramètres déclarés rémanents avec -S."""
        self.capture_args(args)
        if not getattr(args, "save_config", False):
            return True
        return self.save()

    def load(self) -> None:
        """
        Charge la configuration depuis le fichier.
        Si le fichier n'existe pas ou est invalide, utilise les valeurs par défaut.
        """
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file) as f:
                    contents = json.load(f)
                if not isinstance(contents, dict):
                    raise ValueError("La configuration JSON doit être un objet")
                self._config = contents
                logging.info(f"Configuration chargée depuis {self.config_file}")
            except Exception as e:
                logging.warning(f"Erreur lors du chargement de la configuration: {e}")
                self._config = {}
        else:
            logging.info(
                f"Fichier de configuration {self.config_file} inexistant, utilisation des valeurs par défaut"
            )
            self._config = {}

    def save(self) -> bool:
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

                paths = (
                    DarkLib.config_path_parameters
                    | LightProcessor.config_path_parameters
                )
            for key in paths:
                value = self._config.get(key)
                if value is not None:
                    self._config[key] = (
                        [os.path.abspath(item) for item in value]
                        if isinstance(value, list)
                        else os.path.abspath(value)
                    )

            transient = {
                key
                for key, persistent, _ in self._parameters.values()
                if not persistent
            }
            contents = {
                key: value
                for key, value in self._config.items()
                if key not in transient
            }
            serialized = json.dumps(contents, indent=2, default=self._json_value)
            destination = Path(self.config_file)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    dir=destination.parent,
                    prefix=f".{destination.name}.",
                    delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(serialized)
                temporary.replace(destination)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            logging.info(f"Configuration sauvegardée dans {self.config_file}")
            return True
        except Exception as e:
            logging.error(f"Erreur lors de la sauvegarde de la configuration: {e}")
            return False

    @staticmethod
    def _json_value(value: ConfigValue) -> str:
        """Convertit un chemin pour JSON et refuse les autres objets non sérialisables.

        Raises:
            TypeError: Si la valeur n’est pas un chemin."""
        if isinstance(value, Path):
            return str(value)
        raise TypeError(f"Valeur non sérialisable: {type(value).__name__}")

    def get(self, key: str, default: ConfigValue = None) -> ConfigValue:
        """
        Récupère une valeur de configuration.
        Si la clé n'existe pas, renvoie la valeur par défaut spécifiée ou celle définie dans DEFAULTS.
        """
        if key in self._runtime:
            return self._runtime[key]
        for dest, (
            config_key,
            persistent,
            declared_default,
        ) in self._parameters.items():
            if key in (dest, config_key):
                return (
                    self._config.get(config_key, declared_default)
                    if persistent
                    else declared_default
                )
        if default is None and key in self.DEFAULTS:
            default = self.DEFAULTS[key]
        return self._config.get(key, default)

    def set(self, key: str, value: ConfigValue) -> None:
        """
        Définit une valeur de configuration.
        """
        matching = [
            (dest, config_key)
            for dest, (config_key, _, _) in self._parameters.items()
            if key in (dest, config_key)
        ]
        canonical = matching[0][1] if matching else key
        self._config[canonical] = value
        for dest, config_key in matching:
            self._runtime[dest] = deepcopy(value)
            self._runtime[config_key] = deepcopy(value)
        if key in self._runtime:
            self._runtime[key] = deepcopy(value)

    def update(self, **kwargs: ConfigValue) -> None:
        """
        Met à jour plusieurs valeurs de configuration en une seule fois.
        """
        for key, value in kwargs.items():
            self.set(key, value)

    def to_dict(self) -> dict[str, ConfigValue]:
        """
        Retourne la configuration sous forme de dictionnaire.
        """
        return dict(self._config)

    def set_from_args(self, args: argparse.Namespace) -> None:
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
        from lib.lightprocessor import LightProcessor

        paths = DarkLib.config_path_parameters | LightProcessor.config_path_parameters
        defaults = self.DEFAULTS
        aliases = DarkLib.config_keys
        for dest, value in vars(args).items():
            key = aliases.get(dest, dest)
            if key in defaults or key in DarkLib.legacy_parameters:
                if value is not None:
                    if key in paths:
                        value = (
                            [os.path.abspath(item) for item in value]
                            if isinstance(value, list)
                            else os.path.abspath(value)
                        )
                    self.set(key, deepcopy(value))
