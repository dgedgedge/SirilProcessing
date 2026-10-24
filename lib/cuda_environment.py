"""Installation explicite et contrôles du venv CUDA commun aux traitements.

Le pilote NVIDIA reste fourni par le système. Ce module d'installation utilise
uniquement la bibliothèque standard jusqu'aux contrôles dans le venv cible.
Voir docs/scripts/install_cuda/README.md pour le périmètre et les limites.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import subprocess
import sys


class CudaEnvironmentInstaller:
    """Installe les dépendances CUDA dans un venv sans modifier Python système."""

    def __init__(self, root: Path, venv: Path | None = None) -> None:
        """Résout le même venv que les lanceurs : explicite, VENV_DIR, .venv, venv."""
        self.root = root.resolve()
        selected = venv or os.environ.get('VENV_DIR')
        if selected is None:
            selected = self.root / ('venv' if not (self.root / '.venv').is_dir()
                                    and (self.root / 'venv').is_dir() else '.venv')
        self.venv = Path(selected).expanduser().resolve()
        self.python = self.venv / 'bin/python'
        self.system_python = sys._base_executable
        if self.venv in (self.root, Path('/'), Path(sys.base_prefix).resolve()):
            raise ValueError('La cible doit être un venv distinct du projet et du Python système')
        if self.venv.exists():
            if not self.venv.is_dir():
                raise ValueError('La cible du venv doit être un répertoire')
            if any(self.venv.iterdir()) and not (self.venv / 'pyvenv.cfg').is_file():
                raise ValueError('Le répertoire cible non vide ne contient pas de venv')

    @staticmethod
    def check_platform() -> None:
        """Valide la plateforme couverte par les roues binaires retenues."""
        if platform.system() != 'Linux' or platform.machine() != 'x86_64':
            raise RuntimeError('Cet installateur CUDA cible Linux x86_64')
        if not (3, 11) <= sys.version_info[:2] <= (3, 14):
            raise RuntimeError('Python 3.11 à 3.14 est requis pour cet ensemble CUDA')

    @staticmethod
    def preflight() -> None:
        """Vérifie la plateforme et l'accès au pilote avant téléchargement."""
        CudaEnvironmentInstaller.check_platform()
        try:
            subprocess.run(['nvidia-smi', '-L'], check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as error:
            raise RuntimeError(
                'Pilote NVIDIA inaccessible : vérifier nvidia-smi. '
                '--install-only installe les paquets sans certifier le GPU.'
            ) from error

    def pip(self, *arguments: str, capture: bool = False) -> subprocess.CompletedProcess:
        """Exécute le pip système avec --python visant exclusivement le venv sélectionné."""
        return subprocess.run(
            [self.system_python, '-m', 'pip', '--python', str(self.python), *arguments],
            check=True, capture_output=capture, text=True,
        )

    def install(self, install_only: bool = False) -> None:
        """Crée le venv, résout les paquets, retire les conflits et installe CUDA.

        Parameters
        ----------
        install_only : bool
            Saute le contrôle matériel ; les dépendances sont installées mais
            leur fonctionnement GPU ne sera pas certifié par cette méthode.

        Notes
        -----
        La résolution pip précède le retrait des paquets CPU/variantes CuPy.
        Une panne réseau pendant l'installation peut laisser un venv incomplet ;
        relancer le script. Aucun pilote, paquet système ni poids n'est installé.
        Les échecs de pip sont propagés avec un code de sortie non nul.
        """
        if not install_only:
            self.preflight()
        else:
            self.check_platform()
        subprocess.run([self.system_python, '-m', 'venv', '--without-pip', str(self.venv)],
                       check=True)
        options = ['--upgrade', '--only-binary=:all:',
                   '-r', str(self.root / 'requirements.txt'),
                   '-r', str(self.root / 'requirements-cuda.txt')]
        self.pip('install', '--dry-run', *options)
        inventory = subprocess.run(
            [str(self.python), '-c',
             'import importlib.metadata as m,json; '
             'print(json.dumps([d.metadata["Name"] for d in m.distributions()]))'],
            check=True, capture_output=True, text=True,
        )
        conflicts = []
        for name in json.loads(inventory.stdout):
            normalized = name.lower().replace('_', '-')
            if (normalized in ('onnxruntime', 'cupy')
                    or normalized.startswith('cupy-cuda') and normalized != 'cupy-cuda12x'):
                conflicts.append(name)
        if conflicts:
            print('Remplacement des paquets en conflit : ' + ', '.join(conflicts), flush=True)
            self.pip('uninstall', '-y', *conflicts)
        self.pip('install', *options)
        if conflicts:
            # Les distributions CPU/GPU partagent des fichiers Python : restaurer
            # les versions retenues, même si leurs métadonnées existaient déjà.
            self.pip('install', '--force-reinstall', '--no-deps',
                     'onnxruntime-gpu==1.26.0', 'cupy-cuda12x==14.2.0')
        self.pip('check')

    def verify(self) -> None:
        """Lance les trois calculs de contrôle dans un processus du venv cible."""
        self.preflight()
        if not (self.venv / 'pyvenv.cfg').is_file():
            raise ValueError(f'Venv absent : {self.venv}')
        subprocess.run([str(self.python), str(self.root / 'bin/install_cuda.py'),
                        '--verify-worker'], check=True)

    @staticmethod
    def onnx_probe() -> bytes:
        """Retourne un graphe ONNX MatMul X×X, float32 [2,2], IR8/opset13.

        Protobuf minimal sans dépendance au paquet onnx : un seul nœud MatMul,
        une entrée X et une sortie Y. Sa validité est vérifiée avec ONNX Runtime.
        """
        def field(number: int, payload: bytes) -> bytes:
            """Encode un champ protobuf length-delimited de moins de 128 octets."""
            return bytes([number * 8 + 2, len(payload)]) + payload

        tensor_type = b'\x08\x01' + field(2, field(1, b'\x08\x02') * 2)
        def value(name: bytes) -> bytes:
            """Décrit un tenseur float32 carré 2×2 avec le nom donné."""
            return field(1, name) + field(2, field(1, tensor_type))

        node = field(1, b'X') * 2 + field(2, b'Y') + field(4, b'MatMul')
        graph = field(1, node) + field(2, b'CUDA probe')
        graph += field(11, value(b'X')) + field(12, value(b'Y'))
        return b'\x08\x08' + field(7, graph) + field(8, b'\x10\x0d')

    @staticmethod
    def verify_worker() -> None:
        """Exécute PyTorch, CuPy/AutoGHS et une session ONNX CUDA sans repli CPU.

        Aucun modèle astronomique n'est téléchargé. Une réussite exige un calcul
        réel dans chaque moteur ; importer les bibliothèques ne suffit pas.
        """
        import numpy as np
        import torch
        import cupy as cp
        import onnxruntime as ort
        from lib.auto_ghs import ComputeBackend, HistogramAnalyzer, ghs_rgb

        if not torch.cuda.is_available():
            raise RuntimeError('PyTorch ne peut pas utiliser CUDA')
        tensor = torch.ones((2, 2), device='cuda')
        if not torch.allclose(tensor @ tensor, torch.full_like(tensor, 2)):
            raise RuntimeError('Calcul CUDA PyTorch incorrect')
        print(f'Cosmic Clarity : PyTorch {torch.__version__}, GPU {torch.cuda.get_device_name(0)}')
        backend = ComputeBackend('cuda')
        data = cp.full((3, 8, 8), .1, dtype=cp.float32)
        result = backend.numpy(ghs_rgb(data, .5, xp=cp))
        if not np.isfinite(result).all() or not (result > .1).all():
            raise RuntimeError('Calcul CUDA AutoGHS incorrect')
        metrics = HistogramAnalyzer(256).analyze(data, backend).metrics
        if metrics['nonfinite_count'] or not np.isfinite(metrics['median']):
            raise RuntimeError('Histogrammes CUDA AutoGHS incorrects')
        print(f'AutoGHS : CuPy {cp.__version__}, transfert et histogrammes CUDA réussis')
        ort.preload_dlls(directory='')
        options = ort.SessionOptions()
        options.add_session_config_entry('session.disable_cpu_ep_fallback', '1')
        ort.disable_telemetry_events()
        session = ort.InferenceSession(CudaEnvironmentInstaller.onnx_probe(), options,
                                       providers=['CUDAExecutionProvider'])
        session.disable_fallback()
        if 'CUDAExecutionProvider' not in session.get_providers():
            raise RuntimeError('ONNX Runtime a sélectionné le CPU')
        output = session.run(None, {'X': np.ones((2, 2), dtype=np.float32)})[0]
        np.testing.assert_allclose(output, 2)
        print(f'GraXpert : ONNX Runtime {ort.__version__}, calcul CUDA réussi')
