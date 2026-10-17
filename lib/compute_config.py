"""Politique de calcul partagée, indépendante des bibliothèques GPU.

Chaque moteur vérifie sa propre disponibilité avant traitement. La politique
ne convertit pas une panne de calcul en reprise automatique sur CPU.
"""

from __future__ import annotations

import argparse

from lib.parameterized_component import ParameterizedComponent


class ComputeConfig(ParameterizedComponent):
    """Déclare le choix global et résout les surcharges propres aux moteurs."""

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare une seule option persistante, sans charger les dépendances GPU."""
        if any(action.dest == "compute_backend" for action in parser._actions):
            return
        group = (
            parser.add_argument_group("Calcul des traitements")
            if isinstance(parser, argparse.ArgumentParser)
            else parser
        )
        group.add_argument(
            "--compute-backend",
            choices=("auto", "cpu", "cuda"),
            default="auto",
            help="Politique commune : accélérateur disponible en auto, CPU sinon",
        )

    @staticmethod
    def requested(args: argparse.Namespace, override: str | None = None) -> str:
        """Résout surcharge locale non nulle > politique globale > auto.

        Les anciens réglages spécifiques sauvegardés restent des surcharges
        explicites. La disponibilité réelle dépend du moteur, pas de cette classe.
        """
        value = getattr(args, override, None) if override is not None else None
        if value is None:
            value = getattr(args, "compute_backend", "auto")
        if value not in ("auto", "cpu", "cuda"):
            raise ValueError("compute-backend doit être auto, cpu ou cuda")
        return value
