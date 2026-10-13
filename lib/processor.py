"""Interface des traitements d’images configurables et de leurs rapports."""

from __future__ import annotations

import argparse
from abc import abstractmethod
from pathlib import Path

from lib.parameterized_component import ParameterizedComponent, ProcessorParameters
from lib.type_defs import JSONReport


class processor(ParameterizedComponent):
    """Traitement configurable produisant un rapport JSON et une image facultative.

    La clé output_image désigne l’image transmise au traitement suivant.
    output_path désigne un rapport JSON, jamais un répertoire.
    """

    enabled_by_default = True

    @abstractmethod
    def post_process(
        self,
        input_path: Path,
        output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Traite l'image configurée ; args permet une surcharge pour cet appel."""
        raise NotImplementedError
