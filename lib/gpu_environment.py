"""Sélection du venv, commandes pip et petit graphe de contrôle GPU communs.

Aucune bibliothèque GPU n'est importée. Les installateurs CUDA et macOS
valident leur plateforme avant toute création de venv ou installation.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


class GpuEnvironmentInstaller:
    """Partage les accès au venv sans installer de paquet Python système."""

    def __init__(self, root: Path, venv: Path | None = None) -> None:
        """Résout le même venv que les lanceurs : explicite, VENV_DIR, .venv, venv."""
        self.root = root.resolve()
        selected = venv or os.environ.get('VENV_DIR')
        if selected is None:
            selected = self.root / ('venv' if not (self.root / '.venv').is_dir()
                                    and (self.root / 'venv').is_dir() else '.venv')
        self.venv_path = Path(selected).expanduser().absolute()
        self.venv = self.venv_path.resolve()
        self.python = self.venv / 'bin/python'
        self.system_python = sys._base_executable
        if self.venv in (self.root, Path('/'), Path(sys.base_prefix).resolve()):
            raise ValueError('La cible doit être un venv distinct du projet et du Python système')
        if self.venv.exists():
            if not self.venv.is_dir():
                raise ValueError('La cible du venv doit être un répertoire')
            if any(self.venv.iterdir()) and not (self.venv / 'pyvenv.cfg').is_file():
                raise ValueError('Le répertoire cible non vide ne contient pas de venv')

    def pip(self, *arguments: str, capture: bool = False) -> subprocess.CompletedProcess:
        """Exécute le pip système avec --python visant exclusivement le venv sélectionné."""
        return subprocess.run(
            [self.system_python, '-m', 'pip', '--python', str(self.python), *arguments],
            check=True, capture_output=capture, text=True,
        )

    @staticmethod
    def onnx_probe(operation: str = 'MatMul') -> bytes:
        """Retourne un graphe ONNX Add X+X ou MatMul X×X, float32 [2,2], IR8/opset13.

        Protobuf minimal sans dépendance au paquet onnx : un seul nœud Add ou MatMul,
        une entrée X et une sortie Y. Sa validité est vérifiée avec ONNX Runtime.
        """
        if operation not in ('Add', 'MatMul'):
            raise ValueError('Opération de contrôle ONNX inconnue')

        def field(number: int, payload: bytes) -> bytes:
            """Encode un champ protobuf length-delimited de moins de 128 octets."""
            return bytes([number * 8 + 2, len(payload)]) + payload

        tensor_type = b'\x08\x01' + field(2, field(1, b'\x08\x02') * 2)

        def value(name: bytes) -> bytes:
            """Décrit un tenseur float32 carré 2×2 avec le nom donné."""
            return field(1, name) + field(2, field(1, tensor_type))

        node = field(1, b'X') * 2 + field(2, b'Y') + field(4, operation.encode('ascii'))
        graph = field(1, node) + field(2, b'GPU probe')
        graph += field(11, value(b'X')) + field(12, value(b'Y'))
        return b'\x08\x08' + field(7, graph) + field(8, b'\x10\x0d')
