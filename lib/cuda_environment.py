"""Installation explicite et contrôles du venv CUDA commun aux traitements.

Le pilote NVIDIA reste fourni par le système. Ce module d'installation utilise
uniquement la bibliothèque standard jusqu'aux contrôles dans le venv cible.
Voir docs/scripts/install_cuda/README.md pour le périmètre et les limites.
"""

from __future__ import annotations

import json
from pathlib import Path
import platform
import subprocess
import sys

from lib.gpu_environment import GpuEnvironmentInstaller


class CudaEnvironmentInstaller(GpuEnvironmentInstaller):
    """Installe les dépendances CUDA dans un venv sans modifier Python système."""

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

    def install(self, install_only: bool = False) -> None:
        """Crée le venv, résout les paquets, retire les conflits et installe CUDA.

        Parameters
        ----------
        install_only : bool
            Saute le contrôle matériel ; les dépendances sont installées mais
            leur fonctionnement GPU ne sera pas certifié par cette méthode.

        Notes
        -----
        PyTorch est résolu et installé depuis son index CUDA exclusif. Les
        autres dépendances sont ensuite résolues depuis PyPI en conservant son
        pin CUDA. Cette seconde résolution précède le retrait CPU/variantes
        CuPy ; son échec peut laisser PyTorch mis à jour, mais ne les retire pas.
        Une panne réseau pendant l'installation peut laisser un venv incomplet ;
        relancer le script. Aucun pilote, paquet système ni poids n'est installé.
        PowerGuard est installé sous Linux via requirements-power.txt dans une
        phase PyPI séparée autorisant les sources de dbus-python. Sa compilation
        peut nécessiter des en-têtes D-Bus/GLib et les outils de développement.
        Les échecs de pip sont propagés avec un code de sortie non nul.
        """
        if not install_only:
            self.preflight()
        else:
            self.check_platform()
        subprocess.run([self.system_python, '-m', 'venv', '--without-pip', str(self.venv)],
                       check=True)
        torch_options = ['--upgrade', '--only-binary=:all:',
                         '-r', str(self.root / 'requirements-cosmic-clarity.txt')]
        self.pip('install', '--dry-run', *torch_options)
        self.pip('install', *torch_options)
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
                     '--index-url', 'https://pypi.org/simple',
                     'onnxruntime-gpu==1.26.0', 'cupy-cuda12x==14.2.0')
        # dbus-python peut nécessiter une compilation : ne pas imposer only-binary.
        self.pip('install', '--upgrade', '--index-url', 'https://pypi.org/simple',
                 '-r', str(self.root / 'requirements-power.txt'))
        self.pip('check')

    def verify(self) -> None:
        """Lance les trois calculs de contrôle dans un processus du venv cible."""
        self.preflight()
        if not (self.venv / 'pyvenv.cfg').is_file():
            raise ValueError(f'Venv absent : {self.venv}')
        subprocess.run([str(self.python), str(self.root / 'bin/install_dependances.py'),
                        '--venv', str(self.venv), '--verify-worker'], check=True)

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
