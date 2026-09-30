"""Interface commune aux traitements d’images et à leurs rapports."""

import argparse
from copy import deepcopy
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict


class processor(ABC):
    """Interface commune : options propres, rapport JSON et image facultative.

    post_process retourne un dictionnaire sérialisable en JSON. La clé optionnelle
    output_image désigne un fichier image à transmettre au processeur suivant.
    output_path désigne toujours un fichier de rapport JSON, jamais un répertoire.
    """

    enabled_by_default = True
    _args = None
    parameter_persistence = {}

    def set_from_args(self, args: argparse.Namespace) -> None:
        """Configure les prochains traitements à partir des arguments parsés.

        Une copie indépendante évite qu'une modification du Namespace appelant
        change ensuite la configuration du processeur.
        """
        self._args = deepcopy(args)

    def _get_args(self, args=None):
        """Une surcharge ponctuelle ne modifie pas la configuration mémorisée."""
        if args is not None:
            return args
        if self._args is not None:
            return self._args
        from lib.config import Config
        return Config().arguments()

    def get_prefix(self) -> str:
        return self.__class__.__name__.lower()

    @abstractmethod
    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Déclare les options, préfixées pour éviter les collisions."""
        raise NotImplementedError

    @abstractmethod
    def post_process(self, input_path: Path, output_path: Path, args=None) -> Dict:
        """Traite l'image configurée ; args permet une surcharge pour cet appel."""
        raise NotImplementedError
