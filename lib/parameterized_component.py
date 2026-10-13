"""Paramètres communs aux services et traitements du programme."""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from typing import ClassVar

from lib.type_defs import ConfigValue


@dataclass(frozen=True)
class ProcessorParameters:
    """Instantané des paramètres déclarés d’un composant configurable.

    Les valeurs sont copiées à la capture et à la lecture pour isoler listes et
    chemins des anciens Namespace. Aucune référence au parser n’est conservée.
    """

    _values: tuple[tuple[str, ConfigValue], ...]

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> ProcessorParameters:
        """Capture les valeurs résolues sans partager les conteneurs mutables."""
        return cls(tuple(deepcopy(vars(args)).items()))

    def arguments(self) -> argparse.Namespace:
        """Produit une copie compatible avec les API historiques des traitements."""
        return argparse.Namespace(**deepcopy(dict(self._values)))


class ParameterizedComponent(ABC):
    """Composant configurable sans contrat de traitement d’image."""

    _args: argparse.Namespace | None = None
    _class_parameters: ClassVar[ProcessorParameters | None] = None

    def __new__(cls, *args: object, **kwargs: object) -> ParameterizedComponent:
        """Capture les réglages de la classe avant son constructeur concret."""
        instance = super().__new__(cls)
        parameters = cls.__dict__.get("_class_parameters")
        instance._args = parameters.arguments() if parameters is not None else None
        return instance

    @classmethod
    def bind_parameters(cls, args: argparse.Namespace) -> None:
        """Mémorise les paramètres pour les futures instances de cette classe."""
        cls._class_parameters = ProcessorParameters.from_args(args)

    @classmethod
    def parameter_children(cls) -> tuple[type[ParameterizedComponent], ...]:
        """Déclare les enfants potentiels sans créer leurs instances."""
        return ()

    parameter_persistence: ClassVar[dict[str, bool]] = {}

    def set_from_args(self, args: argparse.Namespace) -> None:
        """Remplace les paramètres de cette instance par une copie des arguments.

        Une copie indépendante évite qu'une modification du Namespace appelant
        change ensuite la configuration du composant.
        """
        self._args = deepcopy(args)

    def _get_args(self, args: argparse.Namespace | None = None) -> argparse.Namespace:
        """Résout appel explicite > instance/instantané > configuration historique.

        Une surcharge ponctuelle ne modifie pas les paramètres mémorisés.
        Les instances créées après bind_parameters ne relisent pas Config.
        """
        if args is not None:
            return args
        if self._args is not None:
            return self._args
        from lib.config import Config

        return Config().arguments()

    @classmethod
    def get_prefix(cls) -> str:
        """Retourne le préfixe par défaut des options du composant."""
        return cls.__name__.lower()

    @classmethod
    @abstractmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare les options, préfixées pour éviter les collisions."""
        raise NotImplementedError
