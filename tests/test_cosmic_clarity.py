"""Sélection du moteur, installation vérifiée et audit des traitements neuronaux."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from lib.cosmic_clarity import models
from lib.cosmic_clarity.processors import (
    CosmicClarityDenoiseProcessor, CosmicClaritySharpenProcessor,
)
from lib.postprocess import _PostProcessorSequence, DeconvolutionProcessor


def arguments(*options: str) -> argparse.Namespace:
    """Parse les réglages réels de la séquence avec ses valeurs par défaut."""
    parser = argparse.ArgumentParser()
    _PostProcessorSequence().add_arguments(parser)
    return parser.parse_args(options)


def test_backend_selection_and_override() -> None:
    """Le moteur optionnel remplace deux étapes, sans import de PyTorch ni cumul."""
    sequence = _PostProcessorSequence()
    selected = sequence._selected_processors(arguments())
    assert [p.get_prefix() for p in selected] == ['gradient', 'photometry', 'deconvolution', 'denoise']
    assert isinstance(selected[2], CosmicClaritySharpenProcessor)
    assert isinstance(selected[3], CosmicClarityDenoiseProcessor)
    assert selected[0] is sequence.processors[0]
    assert isinstance(sequence._selected_processors(arguments('--disable-clarity'))[2], DeconvolutionProcessor)
    custom = _PostProcessorSequence([DeconvolutionProcessor()])
    assert isinstance(custom._selected_processors(arguments('--enable-clarity'))[0], DeconvolutionProcessor)


def test_backend_configuration_persistence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sauvegarde du moteur et priorité de l'option CLI sur la configuration."""
    from lib.config import Config
    config_path = tmp_path / 'config.json'
    monkeypatch.setattr(Config, '_instance', None)
    config = Config(config_path)
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(_PostProcessorSequence(), parser)
    args = config.parse_args(parser, [
        '--disable-clarity', '--cosmic-sharpen-mode', 'separate',
        '--cosmic-denoise-mode', 'full', '--cosmic-color-denoise-amount', '.25', '-S',
    ])
    assert config.save_requested(args)
    monkeypatch.setattr(Config, '_instance', None)
    config = Config(config_path)
    parser = argparse.ArgumentParser()
    config.register(_PostProcessorSequence(), parser)
    loaded = config.parse_args(parser, [])
    assert loaded.enable_clarity is False
    assert loaded.cosmic_sharpen_mode == 'separate'
    assert loaded.cosmic_denoise_mode == 'full'
    assert loaded.cosmic_color_denoise_amount == .25
    overridden = config.parse_args(parser, ['--cosmic-denoise-mode', 'luminance'])
    assert overridden.cosmic_denoise_mode == 'luminance'
    assert config.parse_args(parser, ['--enable-clarity']).enable_clarity is True


def test_atomic_model_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Installation idempotente, détection de corruption et conservation sur échec."""
    payload = b'known model bytes'
    entry = {'url': 'https://example.invalid/model', 'size': len(payload),
             'sha256': hashlib.sha256(payload).hexdigest()}
    monkeypatch.setattr(models, 'manifest', lambda: {'test.pth': entry})
    calls = []

    def download(*args: object, **kwargs: object) -> io.BytesIO:
        """Fournit un modèle local en comptant les accès réseau simulés."""
        calls.append(args)
        return io.BytesIO(payload)

    monkeypatch.setattr(models, 'urlopen', download)
    models.install_models(tmp_path)
    models.install_models(tmp_path)
    assert len(calls) == 1
    target = tmp_path / 'test.pth'
    target.write_bytes(b'corrupt')
    monkeypatch.setattr(models, 'urlopen', lambda *a, **k: io.BytesIO(b'wrong'))
    with pytest.raises(ValueError, match='Empreinte'):
        models.install_models(tmp_path)
    assert target.read_bytes() == b'corrupt'
    assert not list(tmp_path.glob('*.part'))
    monkeypatch.setattr(models, 'urlopen', download)
    models.install_models(tmp_path)
    assert models.verify_model(tmp_path, 'test.pth') == target


@pytest.mark.parametrize('siril_better', [False, True])
@pytest.mark.parametrize('operation,noise,width,status', [
    ('denoise', .8, 1.01, 'accepted'),
    ('denoise', .8, 1.08, 'rejected'),
    ('denoise', 1.0, 1.0, 'rejected'),
    ('sharpen', 1.1, .95, 'accepted'),
    ('sharpen', 1.3, .95, 'no_safe_improvement'),
])
def test_processor_quality_and_source_preservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    operation: str, noise: float, width: float, status: str,
    caplog: pytest.LogCaptureFixture, siril_better: bool,
) -> None:
    """Vérifie les sorties, les audits et le journal des évaluations et du choix."""
    from lib.cosmic_clarity import processors
    source = tmp_path / 'source.fit'
    fits.writeto(source, np.arange(1024, dtype='float32').reshape(32, 32) / 1024)
    original = source.read_bytes()

    class FakeEngine:
        """Produit un candidat déterministe sans dépendance GPU pour tester l'audit."""

        def __init__(self, *args: object) -> None:
            """Accepte le contrat de construction du moteur."""

        def process(self, source: Path, destination: Path, *args: object, **kwargs: object) -> dict:
            """Copie le FITS de test ; les métriques sont injectées séparément."""
            assert kwargs['sharpen_mode'] == 'luminance'
            assert kwargs['denoise_mode'] == 'luminance'
            assert kwargs['color_denoise_amount'] is None
            shutil.copyfile(source, destination)
            return {'device': 'cuda', 'operation': operation}

    monkeypatch.setitem(sys.modules, 'lib.cosmic_clarity.inference', SimpleNamespace(CosmicClarityEngine=FakeEngine))
    monkeypatch.setattr(processors, 'create_siril_from_args', lambda args:
                        SimpleNamespace(run_siril_script=lambda *a, **k: True))
    monkeypatch.setattr(processors, 'compare_star_catalogs', lambda *a: {
        'status': 'validated', 'pairs': [], 'before_fwhm_px': {'mean': 4},
        'after_fwhm_px': {'mean': 4 * width}, 'paired_ratio': {'median': width},
        'before_roundness': {'mean': 1}, 'after_roundness': {'mean': 1},
    })
    monkeypatch.setattr(processors, 'artifact_metrics', lambda *a:
                        {'max_noise_ratio': noise, 'max_ring_fraction': 0})
    if operation == 'sharpen':
        if status == 'no_safe_improvement':
            monkeypatch.setattr(
                'lib.deconvolution.post_process',
                lambda *a, **k: (_ for _ in ()).throw(RuntimeError(
                    'Aucune déconvolution efficace sans dégradation mesurée'
                )),
            )
        else:
            def _accepted_siril(source: Path, report: Path, args: argparse.Namespace) -> dict:
                """Simule un candidat Siril accepté, à score égal ou supérieur."""
                siril_width = width * (.95 if siril_better else 1)
                output = report.with_suffix('.fits')
                shutil.copyfile(source, output)
                return {
                    'status': 'accepted',
                    'output_image': str(output),
                    'comparison': {
                        'status': 'validated',
                        'before_fwhm_px': {'mean': 4},
                        'after_fwhm_px': {'mean': 4 * siril_width},
                        'paired_ratio': {'median': siril_width},
                        'before_roundness': {'mean': 1},
                        'after_roundness': {'mean': 1},
                    },
                    'artifacts': {'max_noise_ratio': noise, 'max_ring_fraction': 0},
                }
            monkeypatch.setattr(
                'lib.deconvolution.post_process',
                _accepted_siril,
            )
    else:
        def _siril_denoise(source: Path, report: Path, args: argparse.Namespace) -> dict:
            """Simule le verdict Siril et une réduction de bruit contrôlée."""
            if status == 'accepted':
                output = report.with_suffix('.fits')
                shutil.copyfile(source, output)
                return {
                    'status': 'accepted',
                    'output_image': str(output),
                    'comparison': {
                        'status': 'validated',
                        'before_fwhm_px': {'mean': 4},
                        'after_fwhm_px': {'mean': 4 * width},
                        'paired_ratio': {'median': width},
                        'before_roundness': {'mean': 1},
                        'after_roundness': {'mean': 1},
                    },
                    'artifacts': {'max_noise_ratio': noise * (.9 if siril_better else 1), 'max_ring_fraction': 0},
                    'rejection_reasons': [],
                }
            return {
                'status': 'rejected',
                'output_image': str(source),
                'rejection_reasons': ['no_noise_reduction'],
            }
        monkeypatch.setattr('lib.denoising.post_process', _siril_denoise)
    cls = CosmicClaritySharpenProcessor if operation == 'sharpen' else CosmicClarityDenoiseProcessor
    report = tmp_path / 'report.json'
    stale_parent = tmp_path / 'report_work'
    stale_parent.mkdir(parents=True, exist_ok=True)
    (stale_parent / 'stale.txt').write_text('stale')
    final = report.with_suffix('.fits')
    if status == 'no_safe_improvement':
        final.write_bytes(b'previous output')
    with caplog.at_level('INFO'):
        result = cls().post_process(source, report, arguments())
    treatment = 'Déconvolution' if operation == 'sharpen' else 'Débruitage'
    assert f'{treatment} — début de l’évaluation Cosmic Clarity' in caplog.text
    assert f'{treatment} — début de l’évaluation Siril' in caplog.text
    assert f'{treatment} — évaluation Cosmic Clarity : statut=' in caplog.text
    assert f'{treatment} — évaluation Siril : statut=' in caplog.text
    if status == 'accepted':
        assert f'{treatment} — score Cosmic Clarity :' in caplog.text
        assert f'{treatment} — score Siril :' in caplog.text
        selected_label = 'Siril' if siril_better else 'Cosmic Clarity'
        assert f'{treatment} — choix final : {selected_label} ; score=' in caplog.text
        assert result['selected_engine'] == ('siril' if siril_better else 'cosmic_clarity')
        assert 'meilleur score parmi 2 candidat(s) accepté(s)' in caplog.text
        assert 'variation maximale du bruit' in caplog.text
        assert 'gain médian apparié' in caplog.text
    else:
        assert f'{treatment} — choix final : image d’entrée conservée' in caplog.text
        assert 'aucun candidat accepté' in caplog.text
        assert f'{treatment} — score ' not in caplog.text
    if operation == 'sharpen' and status == 'accepted':
        assert result['status'] == 'accepted'
        assert Path(result['output_image']) == final
    elif operation == 'sharpen' and status == 'no_safe_improvement':
        assert result['status'] == 'no_safe_improvement'
        assert result['selected_engine'] is None
        assert Path(result['output_image']) == source
    else:
        assert Path(result['output_image']) == (final if status == 'accepted' else source)
    audit = json.loads(report.read_text())
    assert audit['status'] == status
    assert Path(audit['candidate_image_path']).is_file()
    candidate = Path(audit['candidate_image_path'])
    assert candidate.parent.name == '001'
    assert candidate.parent.parent == tmp_path / 'report_work'
    assert not (tmp_path / 'report_work' / 'stale.txt').exists()
    assert source.read_bytes() == original


def test_sequence_neural_handoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """La séquence transmet la netteté au débruitage avec les indices historiques."""
    calls = []
    source = tmp_path / 'source.fit'
    source.write_bytes(b'image')

    def process(self: object, input_path: Path, output_path: Path, args: argparse.Namespace) -> dict:
        """Simule une sortie pour vérifier l'ordre et les chemins réellement transmis."""
        calls.append((input_path, output_path.name))
        destination = output_path.with_suffix('.fits')
        shutil.copyfile(input_path, destination)
        return {'output_image': str(destination)}

    monkeypatch.setattr(CosmicClaritySharpenProcessor, 'post_process', process)
    monkeypatch.setattr(CosmicClarityDenoiseProcessor, 'post_process', process)
    args = arguments('--enable-clarity', '--disable-gradient', '--disable-photometry')
    sequence = _PostProcessorSequence()
    sequence.set_from_args(args)
    result = sequence.post_process(source, tmp_path / 'global.json')
    assert list(result) == ['deconvolution', 'denoise']
    assert calls == [(source, '03_deconvolution.json'),
                     (tmp_path / 'global_steps' / '03_deconvolution.fits', '04_denoise.json')]
    calls.clear()
    args.enable_deconvolution = False
    sequence.post_process(source, tmp_path / 'disabled.json', args)
    assert calls == [(source, '04_denoise.json')]


def test_sequence_continues_when_fallback_has_no_gain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un échec de gain en déconvolution (Cosmic puis Siril) laisse passer le débruitage."""
    from lib.cosmic_clarity import processors
    source = tmp_path / 'source.fit'
    fits.writeto(source, np.arange(1024, dtype='float32').reshape(32, 32) / 1024)
    denoise_inputs: list[Path] = []

    class FakeEngine:
        def __init__(self, *args: object) -> None:
            pass

        def process(self, source: Path, destination: Path, *args: object, **kwargs: object) -> dict:
            shutil.copyfile(source, destination)
            return {'device': 'cuda'}

    def denoise(self: object, input_path: Path, output_path: Path, args: argparse.Namespace) -> dict:
        denoise_inputs.append(Path(input_path))
        destination = output_path.with_suffix('.fits')
        shutil.copyfile(input_path, destination)
        return {'output_image': str(destination)}

    monkeypatch.setitem(sys.modules, 'lib.cosmic_clarity.inference', SimpleNamespace(CosmicClarityEngine=FakeEngine))
    monkeypatch.setattr(processors, 'create_siril_from_args', lambda args:
                        SimpleNamespace(run_siril_script=lambda *a, **k: True))
    monkeypatch.setattr(processors, 'compare_star_catalogs', lambda *a: {'status': 'insufficient_matches'})
    monkeypatch.setattr('lib.deconvolution.post_process', lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError('Aucune déconvolution efficace sans dégradation mesurée'),
    ))
    monkeypatch.setattr(CosmicClarityDenoiseProcessor, 'post_process', denoise)
    args = arguments('--enable-clarity', '--disable-gradient', '--disable-photometry')
    results = _PostProcessorSequence().post_process(source, tmp_path / 'global.json', args)
    assert results['deconvolution']['status'] == 'no_safe_improvement'
    assert results['deconvolution']['selected_engine'] is None
    assert denoise_inputs == [source]


def test_sharpen_uses_auto_matching_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le backend neuronal calcule les seuils d'appariement si non forcés."""
    from lib.cosmic_clarity import processors
    source = tmp_path / 'source.fit'
    fits.writeto(source, np.arange(1024, dtype='float32').reshape(32, 32) / 1024)

    class FakeEngine:
        def __init__(self, *args: object) -> None:
            pass

        def process(self, source: Path, destination: Path, *args: object, **kwargs: object) -> dict:
            shutil.copyfile(source, destination)
            return {'device': 'cuda', 'operation': 'sharpen'}

    captured: dict[str, float | int] = {}

    def compare(
        _before: Path, _after: Path,
        radius: float, minimum: int, fraction: float, _layer: int,
    ) -> dict:
        captured.update(radius=radius, minimum=minimum, fraction=fraction)
        return {
            'status': 'validated',
            'pairs': [],
            'before_fwhm_px': {'mean': 4},
            'after_fwhm_px': {'mean': 3.6},
            'paired_ratio': {'median': 0.9},
            'before_roundness': {'mean': 1.0},
            'after_roundness': {'mean': 1.0},
        }

    monkeypatch.setitem(sys.modules, 'lib.cosmic_clarity.inference', SimpleNamespace(CosmicClarityEngine=FakeEngine))
    monkeypatch.setattr(processors, 'create_siril_from_args', lambda args:
                        SimpleNamespace(run_siril_script=lambda *a, **k: True))
    monkeypatch.setattr(processors, 'read_star_catalog', lambda path: ([
        {'id': idx, 'layer': 0, 'x': float(idx), 'y': 10.0, 'fwhm_x': 5.0,
         'fwhm_y': 5.0, 'fwhm': 5.0, 'amplitude': 1.0, 'beta': 2.0}
        for idx in range(1, 41)
    ], []))
    monkeypatch.setattr(processors, 'compare_star_catalogs', compare)
    monkeypatch.setattr(processors, 'artifact_metrics', lambda *a: {'max_noise_ratio': 1.0, 'max_ring_fraction': 0.0})
    monkeypatch.setattr(processors, 'assess_quality', lambda *a, **k: {'reasons': []})
    monkeypatch.setattr(
        'lib.deconvolution.post_process',
        lambda source, report, args: {
            'status': 'rejected',
            'output_image': str(source),
            'rejection_reasons': ['insufficient_sharpening'],
        },
    )
    result = CosmicClaritySharpenProcessor().post_process(source, tmp_path / 'report.json', arguments())
    assert captured == {'radius': 1.75, 'minimum': 12, 'fraction': 0.6}
    assert result['matching']['mode'] == 'auto'
    assert result['matching']['valid_stars_before'] == 40


def test_sharpen_forced_manual_matching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Les seuils saisis sont appliqués uniquement avec l'option de forçage."""
    from lib.cosmic_clarity import processors
    source = tmp_path / 'source.fit'
    fits.writeto(source, np.arange(1024, dtype='float32').reshape(32, 32) / 1024)

    class FakeEngine:
        def __init__(self, *args: object) -> None:
            pass

        def process(self, source: Path, destination: Path, *args: object, **kwargs: object) -> dict:
            shutil.copyfile(source, destination)
            return {'device': 'cuda', 'operation': 'sharpen'}

    captured: dict[str, float | int] = {}

    def compare(
        _before: Path, _after: Path,
        radius: float, minimum: int, fraction: float, _layer: int,
    ) -> dict:
        captured.update(radius=radius, minimum=minimum, fraction=fraction)
        return {
            'status': 'validated',
            'pairs': [],
            'before_fwhm_px': {'mean': 4},
            'after_fwhm_px': {'mean': 3.6},
            'paired_ratio': {'median': 0.9},
            'before_roundness': {'mean': 1.0},
            'after_roundness': {'mean': 1.0},
        }

    monkeypatch.setitem(sys.modules, 'lib.cosmic_clarity.inference', SimpleNamespace(CosmicClarityEngine=FakeEngine))
    monkeypatch.setattr(processors, 'create_siril_from_args', lambda args:
                        SimpleNamespace(run_siril_script=lambda *a, **k: True))
    monkeypatch.setattr(processors, 'read_star_catalog', lambda path: ([
        {'id': idx, 'layer': 0, 'x': float(idx), 'y': 10.0, 'fwhm_x': 5.0,
         'fwhm_y': 5.0, 'fwhm': 5.0, 'amplitude': 1.0, 'beta': 2.0}
        for idx in range(1, 41)
    ], []))
    monkeypatch.setattr(processors, 'compare_star_catalogs', compare)
    monkeypatch.setattr(processors, 'artifact_metrics', lambda *a: {'max_noise_ratio': 1.0, 'max_ring_fraction': 0.0})
    monkeypatch.setattr(processors, 'assess_quality', lambda *a, **k: {'reasons': []})
    monkeypatch.setattr(
        'lib.deconvolution.post_process',
        lambda source, report, args: {
            'status': 'rejected',
            'output_image': str(source),
            'rejection_reasons': ['insufficient_sharpening'],
        },
    )
    args = arguments(
        '--deconvolution-match-radius', '1.2',
        '--deconvolution-min-stars', '9',
        '--deconvolution-min-match-fraction', '0.42',
        '--deconvolution-force-manual-matching',
    )
    result = CosmicClaritySharpenProcessor().post_process(source, tmp_path / 'report.json', args)
    assert captured == {'radius': 1.2, 'minimum': 9, 'fraction': 0.42}
    assert result['matching']['mode'] == 'forced-manual'


@pytest.mark.parametrize('install,run,fail', [(True, False, False), (True, True, False),
                                           (False, True, False), (True, True, True)])
def test_shell_install_flag(
    tmp_path: Path, install: bool, run: bool, fail: bool,
) -> None:
    """Le .sh consomme l'installation, cible le venv et transmet les arguments intacts."""
    root = Path(__file__).resolve().parents[1]
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    shutil.copyfile(root / 'bin/postProcess.sh', bindir / 'postProcess.sh')
    (bindir / 'venv_helpers.sh').write_text('prepare_project_venv() { SELECTED_VENV="$VENV_DIR"; }\n')
    venv = tmp_path / 'venv space'
    (venv / 'bin').mkdir(parents=True)
    stub = f'''#!{sys.executable}
import json, os, sys
if sys.argv[1:2] == ['-c']:
    print(os.environ['STUB_SYSTEM_PYTHON'])
else:
    with open(os.environ['STUB_LOG'], 'a') as stream:
        stream.write(json.dumps(sys.argv) + '\\n')
    if sys.argv[1:3] == ['-m', 'pip'] and os.environ['FAIL_PIP'] == '1':
        sys.exit(9)
'''
    for path in (bindir / 'python3', venv / 'bin/python'):
        path.write_text(stub)
        path.chmod(0o755)
    log = tmp_path / 'calls.jsonl'
    env = {**os.environ, 'PATH': f'{bindir}:{os.environ["PATH"]}',
           'VENV_DIR': str(venv), 'STUB_SYSTEM_PYTHON': str(bindir / 'python3'),
           'STUB_LOG': str(log), 'FAIL_PIP': '1' if fail else '0'}
    options = ['--cosmic-model-dir', str(tmp_path / 'models space')]
    if install:
        options.append('--install-cosmic-clarity')
    if run:
        options += ['image space.fit', '--enable-clarity']
    result = subprocess.run(['bash', str(bindir / 'postProcess.sh'), *options], env=env, capture_output=True, text=True)
    assert result.returncode == (9 if fail else 0), result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert all('--install-cosmic-clarity' not in call for call in calls)
    if install:
        assert calls[0][1:8] == [
            '-m', 'pip', '--python', str(venv / 'bin/python'),
            'install', '--upgrade', '--quiet',
        ]
        assert '--disable-pip-version-check' in calls[0]
    if fail:
        assert len(calls) == 1
    elif run:
        assert calls[-1][1:] == [str(bindir / 'postProcess.py'), '--cosmic-model-dir',
                                str(tmp_path / 'models space'), 'image space.fit',
                                '--enable-clarity']
    else:
        assert calls[-1][1:] == [str(tmp_path / 'lib/cosmic_clarity/models.py'),
                                '--directory', str(tmp_path / 'models space')]


def test_classic_backend_does_not_require_torch() -> None:
    """La construction et l'aide de la séquence restent utilisables sans PyTorch."""
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, '-c', 'import sys, argparse; sys.modules["torch"] = None; '
         'from lib.postprocess import _PostProcessorSequence; '
         'p = argparse.ArgumentParser(); _PostProcessorSequence().add_arguments(p); '
         'assert p.parse_args([]).enable_clarity is True'],
        cwd=root, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
