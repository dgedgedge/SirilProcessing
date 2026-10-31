"""Politique de calcul partagée, indépendante des bibliothèques GPU.

Chaque moteur vérifie sa propre disponibilité avant traitement. La politique
ne convertit pas une panne de calcul en reprise automatique sur CPU.
"""

from __future__ import annotations

import argparse
import logging
import platform

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
            type=cls.normalize_policy,
            choices=("auto", "cpu"),
            default="auto",
            help="Politique commune : accélérateur disponible en auto, CPU sinon",
        )

    @staticmethod
    def normalize_policy(value: str) -> str:
        """Normalise l'ancien choix global cuda en auto, sans imposer un fabricant.

        Utilisée par argparse et par Config lors de la lecture du JSON. Une
        migration émet un avertissement ; elle n'écrit le JSON qu'avec -S.
        Les choix forcés propres aux moteurs ne passent pas par cette méthode.
        """
        if value == 'cuda':
            logging.warning('Ancien compute-backend=cuda remplacé par auto ; '
                            'le moteur choisira l’accélérateur adapté au système')
            return 'auto'
        if value not in ('auto', 'cpu'):
            raise ValueError('compute-backend doit être auto ou cpu')
        return value

    @staticmethod
    def warn_cpu_fallback(engine: str, reason: str) -> None:
        """Avertit du choix CPU automatique avec le moteur, l'OS et sa cause."""
        system = platform.system()
        label = 'macOS (Darwin)' if system == 'Darwin' else system
        logging.warning('%s : accélération indisponible sous %s ; CPU choisi '
                        'avant calcul : %s', engine, label, reason)

    @staticmethod
    def requested(args: argparse.Namespace, override: str | None = None) -> str:
        """Résout surcharge locale non nulle > politique globale > auto.

        Les anciens réglages spécifiques sauvegardés restent des surcharges
        explicites. La disponibilité réelle dépend du moteur, pas de cette classe.
        MPS est une surcharge Cosmic Clarity, pas une politique globale.
        """
        value = getattr(args, override, None) if override is not None else None
        if value is None:
            return ComputeConfig.normalize_policy(getattr(args, "compute_backend", "auto"))
        if value not in ('auto', 'cpu', 'cuda', 'mps'):
            raise ValueError(f'{override} doit être auto, cpu, cuda ou mps')
        return value
