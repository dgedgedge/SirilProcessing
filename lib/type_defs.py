"""Contrats de données partagés, sans dépendance à l'exécution de Siril.

JSONValue décrit les rapports sérialisables et ConfigValue leur extension aux
chemins de configuration. Les schémas stables des données numériques utilisent
TypedDict ; les rapports extensibles restent des dictionnaires JSON.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Protocol, TypeAlias, TypedDict

import numpy as np

JSONValue: TypeAlias = (
    str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
)
JSONReport: TypeAlias = dict[str, JSONValue]
ConfigValue: TypeAlias = (
    str
    | int
    | float
    | bool
    | Path
    | None
    | list["ConfigValue"]
    | tuple["ConfigValue", ...]
    | dict[str, "ConfigValue"]
)
ConfigMap: TypeAlias = dict[str, ConfigValue]


class ArgumentProvider(Protocol):
    """Traitement capable de déclarer ses paramètres dans le parseur partagé."""

    def add_arguments(
        self, parser: argparse.ArgumentParser, **kwargs: ConfigValue
    ) -> None:
        """Ajoute les options du traitement sans parser ni persister les valeurs."""
        ...


class BackgroundSamples(TypedDict):
    """Grille de fond : coordonnées en pixels, plans par canal et masques par cellule."""

    pixels: np.ndarray
    normalized: np.ndarray
    values: np.ndarray
    valid: np.ndarray
    source: np.ndarray
    keep: np.ndarray
    shape: tuple[int, int]
    xs: np.ndarray
    ys: np.ndarray
    radius: int
    margin: int


class StarMeasurement(TypedDict):
    """Mesure Siril d'une étoile, positions et FWHM exprimées en pixels."""

    id: int
    layer: int
    x: float
    y: float
    fwhm_x: float
    fwhm_y: float
    fwhm: float
    amplitude: float
    beta: float
