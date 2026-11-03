"""Interface des traitements d’images configurables et de leurs rapports."""

from __future__ import annotations

import argparse
from abc import abstractmethod
from pathlib import Path

from lib.parameterized_component import ParameterizedComponent, ProcessorParameters
from lib.type_defs import JSONReport


class processor(ParameterizedComponent):
    """Traitement configurable produisant des sorties décrites par un rapport JSON.

    ``input_path`` désigne une image ou une séquence selon le traitement.
    ``output_path`` désigne le produit principal ou, pour les étapes qui
    l'exigent, le chemin du rapport JSON. La forme exacte des sorties est propre
    à chaque traitement ; ``output_image`` désigne l'image transmise à une étape
    suivante lorsqu'il y en a une.
    """

    enabled_by_default = True

    @abstractmethod
    def post_process(
        self,
        input_path: Path,
        output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Traite les entrées configurées ; args permet une surcharge ponctuelle."""
        raise NotImplementedError
