"""Régressions du moteur d'aberrations et de son intégration aux chaînes."""

import argparse
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pytest
from astropy.io import fits
from numpy.typing import NDArray

from lib.aberration import (
    AberrationEngine, AberrationRemoverProcessor, DEFAULT_MODEL_PATH,
)
from lib.deconvolution import DeconvolutionProcessor
from lib.restoration_chains import RestorationChainProcessor


class AffineSession:
    """Simule un modèle dont les statistiques doivent être restaurées."""

    def run(
        self, names: None, inputs: dict[str, NDArray[np.float32]],
    ) -> list[NDArray[np.float32]]:
        """Applique une transformation affine contrôlable."""
        return [next(iter(inputs.values())) * 2 + 0.1]

    def get_inputs(self) -> list[SimpleNamespace]:
        """Expose le nom du tenseur d'entrée."""
        return [SimpleNamespace(name='image')]

    def get_providers(self) -> list[str]:
        """Expose le fournisseur simulé."""
        return ['CPUExecutionProvider']


@pytest.mark.parametrize('shape', [(1, 1, 1), (1, 31, 27), (3, 513, 555)])
def test_statistics_overlap_and_borders(shape: tuple[int, int, int]) -> None:
    """Une sortie affine restitue les pixels, même aux jointures et bords."""
    data = np.random.default_rng(42).uniform(.1, .8, shape).astype(np.float32)
    restored = AberrationEngine.restore(data, AffineSession(), 'image')
    np.testing.assert_allclose(restored, data, atol=4e-7)


@pytest.mark.parametrize('strength', [-1, 2, np.nan])
def test_invalid_strength(strength: float) -> None:
    """Les mélanges hors contrat sont rejetés avant l'inférence."""
    with pytest.raises(ValueError, match='strength'):
        AberrationEngine.restore(np.ones((1, 4, 4)), AffineSession(), 'image', strength)


def test_invalid_images_and_predictions() -> None:
    """Les entrées non finies et sorties ONNX incompatibles sont rejetées."""
    for data in (np.zeros((2, 4, 4)), np.zeros((1, 0, 4)), np.full((1, 4, 4), np.nan)):
        with pytest.raises(ValueError):
            AberrationEngine.restore(data, AffineSession(), 'image')
    session = SimpleNamespace(run=lambda *_: [np.zeros((1, 4, 4))])
    with pytest.raises(ValueError, match='Sortie ONNX'):
        AberrationEngine.restore(np.ones((1, 4, 4)), session, 'image')
    session = SimpleNamespace(run=lambda *_: [np.full((1, 1, 512, 512), np.nan)])
    with pytest.raises(ValueError, match='non finie'):
        AberrationEngine.restore(np.ones((1, 4, 4)), session, 'image')


@pytest.mark.parametrize('shape', [(1, 23, 27), (3, 101, 103)])
def test_protections_and_zero_strength(shape: tuple[int, int, int]) -> None:
    """Les protections conservent une image constante, zéro conserve l'entrée."""
    data = np.full(shape, .2, dtype=np.float32)
    np.testing.assert_allclose(AberrationEngine.restore(
        data, AffineSession(), 'image', dark_ringing=True, protect_background=True,
    ), data, atol=1e-7)
    np.testing.assert_array_equal(AberrationEngine.restore(data, None, 'image', 0), data)


@pytest.mark.parametrize('backend', ['cpu', 'auto'])
def test_fits_metadata_and_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str,
) -> None:
    """Le traitement conserve unités uint16, WCS et source sur disque."""
    model = tmp_path / 'model.onnx'
    model.touch()
    source, candidate = tmp_path / 'input.fits', tmp_path / 'output.fits'
    original = np.arange(32 * 33, dtype=np.uint16).reshape(32, 33)
    fits.writeto(source, original, header=fits.Header({'CRPIX1': 12.5}))
    contents = source.read_bytes()
    events = []

    def session(*args: object, **kwargs: object) -> AffineSession:
        """Vérifie le préchargement avant toute création de session CUDA."""
        assert events == (['preload'] if backend == 'auto' else [])
        return AffineSession()

    monkeypatch.setitem(sys.modules, 'onnxruntime', SimpleNamespace(
        InferenceSession=session,
        preload_dlls=lambda **kwargs: events.append('preload'),
        get_available_providers=lambda: ['CUDAExecutionProvider', 'CPUExecutionProvider'],
    ))
    engine = AberrationEngine()
    engine.set_from_args(argparse.Namespace(aberration_model=str(model), compute_backend=backend))
    report = engine.process(source, candidate)
    np.testing.assert_allclose(fits.getdata(candidate), original, atol=.02)
    assert fits.getheader(candidate)['CRPIX1'] == 12.5
    assert contents == source.read_bytes()
    assert report['providers'] == ['CPUExecutionProvider']
    with pytest.raises(ValueError, match='préservée'):
        engine.process(source, source)
    model.unlink()
    with pytest.raises(FileNotFoundError):
        engine.process(source, candidate)


def test_options_and_chains() -> None:
    """Le modèle reste configurable sans ajouter AR aux chaînes de restauration."""
    parser = argparse.ArgumentParser()
    RestorationChainProcessor.add_arguments(parser)
    args = parser.parse_args(['--aberration-model', '/tmp/model.onnx',
                              '--aberration-strength', '.5'])
    engines = dict(DeconvolutionProcessor.engines(args))
    assert 'aberration_remover' not in engines
    assert args.aberration_strength == .5
    assert args.aberration_protect_background is True
    assert parser.parse_args(['--no-aberration-protect-background']).aberration_protect_background is False
    assert not any(any(name == 'aberration_remover' for _, name, _ in chain)
               for chain in RestorationChainProcessor.variants(args))
    assert 'aberration_remover' not in dict(DeconvolutionProcessor.engines(parser.parse_args([])))
    disabled = parser.parse_args(['--aberration-model', ''])
    assert 'aberration_remover' not in dict(DeconvolutionProcessor.engines(disabled))


def test_strength_and_background_mask() -> None:
    """Le mélange suit l'intensité et la protection restaure les pixels du fond."""
    data = np.random.default_rng(4).uniform(.1, .8, (1, 512, 512)).astype(np.float32)
    session = SimpleNamespace(run=lambda _, inputs: [np.flip(
        next(iter(inputs.values())), axis=-1,
    ).copy()])
    full = AberrationEngine.restore(data, session, 'image', protect_background=False)
    half = AberrationEngine.restore(data, session, 'image', .5, protect_background=False)
    np.testing.assert_allclose(half, (full + data) / 2, atol=2e-7)
    protected = AberrationEngine.restore(data, session, 'image', protect_background=True)
    np.testing.assert_array_equal(AberrationEngine.restore(data, session, 'image'), protected)
    sample = data[0, ::4, ::4]
    threshold = np.median(sample) + 1.4826 * np.median(np.abs(sample - np.median(sample)))
    mask = data < threshold
    np.testing.assert_array_equal(protected[mask], data[mask])
    assert np.any(np.abs(full[mask] - data[mask]) > .01)


@pytest.mark.parametrize('accepted', [True, False])
def test_processor_quality_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, accepted: bool,
) -> None:
    """Le candidat passe par les contrôles communs avant publication."""
    from lib.cosmic_clarity import processors

    source, report = tmp_path / 'source.fits', tmp_path / 'report.json'
    fits.writeto(source, np.full((32, 32), .2, dtype=np.float32))
    original = source.read_bytes()
    monkeypatch.setattr(AberrationRemoverProcessor, '_infer',
                        lambda self, source, candidate, args: (
                            fits.writeto(candidate, fits.getdata(source)) or {'test': True}))
    monkeypatch.setattr('lib.quality_star_processors.SirilStarDetectionProcessor.post_process',
                        lambda *a, **k: {})
    monkeypatch.setattr(processors, 'compare_star_catalogs', lambda *a, **k: {
        'status': 'validated', 'pairs': [],
    })
    monkeypatch.setattr(processors, 'artifact_metrics', lambda *a, **k: {})
    monkeypatch.setattr(processors, 'assess_quality', lambda *a, **k: {
        'accepted': accepted, 'reasons': [] if accepted else ['noise_amplification'],
    })
    result = AberrationRemoverProcessor().post_process(source, report, argparse.Namespace(
        deconvolution_auto_matching=False,
    ))
    assert result['engine'] == 'aberration_remover'
    assert result['status'] == ('accepted' if accepted else 'rejected')
    assert Path(result['output_image']).is_file()
    assert (result['output_image'] == str(source)) is (not accepted)
    assert source.read_bytes() == original


def test_configuration_priority(tmp_path: Path) -> None:
    """La configuration lie le moteur en respectant CLI > JSON > défauts."""
    from lib.config import Config

    path = tmp_path / 'config.json'
    path.write_text('{"aberration_strength": 0.3}')
    config = Config(path)
    parser = argparse.ArgumentParser()
    config.register(RestorationChainProcessor, parser)
    args = config.parse_args(parser, ['--aberration-strength', '.7'])
    assert args.aberration_strength == .7
    assert AberrationEngine()._get_args().aberration_strength == .7
    args = config.parse_args(parser, [])
    assert args.aberration_strength == .3
    assert args.aberration_dark_ringing is False


def test_annotation_identifies_selected_engine() -> None:
    """L'encart identifie le moteur effectivement retenu dans la chaîne."""
    from lib.final_annotation import FinalImageAnnotator

    assert FinalImageAnnotator.restoration_line({'restoration': {
        'status': 'accepted', 'selected_chain': ['sharpen:aberration_remover'],
    }}) == 'Restauration : Déconvolution (Aberration Remover)'


def test_default_model_from_another_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le modèle livré est sélectionné indépendamment du dossier de lancement."""
    monkeypatch.chdir(tmp_path)
    parser = argparse.ArgumentParser()
    RestorationChainProcessor.add_arguments(parser)
    args = parser.parse_args([])
    assert Path(args.aberration_model) == DEFAULT_MODEL_PATH
    assert DEFAULT_MODEL_PATH.is_absolute()
    assert DEFAULT_MODEL_PATH.is_file()
    assert DEFAULT_MODEL_PATH.name == 'model_v2_5_0.onnx'
    variants = RestorationChainProcessor.variants(args)
    assert not any(chain == (('sharpen', 'aberration_remover', AberrationRemoverProcessor),)
               for chain in variants)


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('denoise', [False, True])
@pytest.mark.parametrize('sharpen', [False, True])
def test_chains_never_repeat_treatment_type(
    reverse: bool, denoise: bool, sharpen: bool,
) -> None:
    """Aucune option ne permet trois étapes ou deux moteurs du même type."""
    parser = argparse.ArgumentParser()
    RestorationChainProcessor.add_arguments(parser)
    args = parser.parse_args(['--no-enable-graxpert'])
    args.restoration_reverse_order = reverse
    args.enable_denoise = denoise
    args.enable_deconvolution = sharpen
    chains = RestorationChainProcessor.variants(args)
    assert chains and chains[0] == ()
    for chain in chains:
        kinds = [operation for operation, _, _ in chain]
        assert len(chain) <= 2
        assert len(kinds) == len(set(kinds))
        if any(name == 'aberration_remover' for _, name, _ in chain):
            assert chain[0][1] == 'aberration_remover'
            assert len(chain) == 1 or chain[1][0] == 'denoise'
        assert denoise or 'denoise' not in kinds
        assert sharpen or 'sharpen' not in kinds
    if denoise and sharpen:
        assert not any(len(chain) == 2 and chain[0][1] == 'aberration_remover'
                   for chain in chains)
