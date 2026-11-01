"""Installation Apple Silicon et contrôles MPS/CoreML, sans dépendance CUDA.

Voir docs/scripts/install_macos/README.md. Les imports scientifiques sont
différés au processus de contrôle dans le venv cible.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

from lib.gpu_environment import GpuEnvironmentInstaller


class MacOSEnvironmentInstaller(GpuEnvironmentInstaller):
    """Installe PyTorch MPS et ONNX CoreML dans le venv commun Apple Silicon."""

    @staticmethod
    def check_platform() -> None:
        """Exige macOS 14+, Python arm64 3.11–3.14, sans émulation Rosetta."""
        if platform.system() != 'Darwin' or platform.machine() != 'arm64':
            raise RuntimeError('Utiliser macOS Apple Silicon et un Python arm64 natif, sans Rosetta')
        if not (3, 11) <= sys.version_info[:2] <= (3, 14):
            raise RuntimeError('Python 3.11 à 3.14 requis pour les versions macOS épinglées')
        version = platform.mac_ver()[0].split('.')
        if not version[0].isdigit() or int(version[0]) < 14:
            raise RuntimeError('macOS 14 ou ultérieur requis pour PyTorch MPS')

    def install(self, install_only: bool = False) -> None:
        """Résout sur PyPI avant retrait ONNX GPU et installation dans le venv.

        install_only est accepté pour le contrat commun ; le contrôle matériel
        est lancé séparément par verify. Un échec de résolution laisse les
        paquets en place ; les échecs d'installation sont propagés.
        Aucun pilote, Siril, StarNet ou poids de modèle n'est téléchargé.
        """
        self.check_platform()
        subprocess.run([self.system_python, '-m', 'venv', '--without-pip', str(self.venv)],
                       check=True)
        options = ['--upgrade', '--only-binary=:all:',
                   '-r', str(self.root / 'requirements.txt'),
                   '-r', str(self.root / 'requirements-macos.txt')]
        self.pip('install', '--dry-run', *options)
        inventory = subprocess.run(
            [str(self.python), '-c', 'import importlib.metadata as m,json; '
             'print(json.dumps([d.metadata["Name"] for d in m.distributions()]))'],
            check=True, capture_output=True, text=True,
        )
        conflicts = [name for name in json.loads(inventory.stdout)
                     if name.lower().replace('_', '-') in ('onnxruntime-gpu', 'onnxruntime-directml')]
        if conflicts:
            self.pip('uninstall', '-y', *conflicts)
        self.pip('install', *options)
        if conflicts:
            self.pip('install', '--force-reinstall', '--no-deps',
                     '--index-url', 'https://pypi.org/simple', 'onnxruntime==1.26.0')
        self.pip('check')

    def verify(self) -> None:
        """Exécute les contrôles dans le venv sélectionné, sans installer de paquet."""
        self.check_platform()
        if not (self.venv / 'pyvenv.cfg').is_file():
            raise ValueError(f'Venv absent : {self.venv}')
        subprocess.run([str(self.python), str(self.root / 'bin/install_dependances.py'),
                        '--venv', str(self.venv), '--verify-worker'], check=True)

    @staticmethod
    def verify_worker() -> None:
        """Compare un réseau MPS au CPU et exige un nœud ONNX exécuté par CoreML.

        Le réseau Cosmic Clarity utilise des poids aléatoires : aucun modèle
        astronomique n'est téléchargé. Le profil ONNX atteste la prise en charge
        par CoreML, sans garantir sa répartition interne CPU/GPU/Neural Engine.
        Les traces sont temporaires ; un échec de calcul n'est pas masqué.
        """
        import numpy as np
        import torch
        import onnxruntime as ort
        from lib.cosmic_clarity.inference import select_device
        from lib.cosmic_clarity.network import RestorationCNN

        MacOSEnvironmentInstaller.check_platform()
        device = select_device('mps')
        model = RestorationCNN().eval()
        tensor = torch.full((1, 3, 16, 16), .1)
        with torch.inference_mode():
            expected = model(tensor).numpy()
            actual = model.to(device)(tensor.to(device)).cpu().numpy()
        np.testing.assert_allclose(actual, expected, rtol=1e-3, atol=1e-4)
        print(f'Cosmic Clarity : PyTorch {torch.__version__}, réseau MPS validé contre CPU')
        if 'CoreMLExecutionProvider' not in ort.get_available_providers():
            raise RuntimeError('Le paquet ONNX Runtime installé ne fournit pas CoreML')
        ort.disable_telemetry_events()
        with tempfile.TemporaryDirectory(prefix='siril-coreml-') as directory:
            options = ort.SessionOptions()
            options.enable_profiling = True
            options.profile_file_prefix = str(Path(directory) / 'profile')
            session = ort.InferenceSession(
                MacOSEnvironmentInstaller.onnx_probe('Add'), sess_options=options,
                providers=[('CoreMLExecutionProvider', {
                    'ModelFormat': 'MLProgram', 'MLComputeUnits': 'ALL',
                }), 'CPUExecutionProvider'],
            )
            session.disable_fallback()
            output = session.run(None, {'X': np.ones((2, 2), dtype=np.float32)})[0]
            np.testing.assert_allclose(output, 2)
            trace = json.loads(Path(session.end_profiling()).read_text())
            if not any(event.get('args', {}).get('provider') == 'CoreMLExecutionProvider'
                       for event in trace):
                raise RuntimeError('Le graphe de contrôle ONNX a été exécuté sans CoreML')
        print(f'GraXpert : ONNX Runtime {ort.__version__}, calcul CoreML confirmé')
        print('AutoGHS et couleur finale : CPU sur macOS (CuPy/CUDA non applicable)')
