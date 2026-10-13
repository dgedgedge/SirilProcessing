"""Orchestration de moteurs processor indépendants et sélection des candidats."""
import argparse
from pathlib import Path

import pytest

from lib.denoising import NoiseReductionProcessor, SirilNoiseReductionProcessor
from lib.cosmic_clarity.processors import CosmicClarityDenoiseProcessor
from lib.processor import processor
from lib.type_defs import JSONReport


def test_noise_orchestrates_two_independent_processors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Les enfants reçoivent la même image et seul le meilleur accepté est publié."""
    source = tmp_path / 'source.fits'
    source.write_bytes(b'original')
    calls: list[tuple[str, Path]] = []

    def candidate(
        name: str, input_path: Path, noise: float,
    ) -> JSONReport:
        """Produit un candidat contrôlé dont le score dépend du bruit."""
        calls.append((name, input_path))
        path = tmp_path / f'{name}.fits'
        path.write_bytes(name.encode())
        return dict(status='accepted', output_image=str(path), comparison=dict(
            status='validated', before_fwhm_px={'mean': 4.0},
            after_fwhm_px={'mean': 4.0}, paired_ratio={'median': 1.0}),
            artifacts=dict(max_noise_ratio=noise, max_ring_fraction=0.0))

    def cosmic(self: processor, input_path: Path, output_path: Path, args: argparse.Namespace) -> JSONReport:
        """Simule uniquement le moteur neuronal."""
        return candidate('cosmic', input_path, .8)

    def siril(self: processor, input_path: Path, output_path: Path, args: argparse.Namespace) -> JSONReport:
        """Simule uniquement le moteur Siril."""
        return candidate('siril', input_path, .4)

    monkeypatch.setattr(CosmicClarityDenoiseProcessor, 'post_process', cosmic)
    monkeypatch.setattr(SirilNoiseReductionProcessor, 'post_process', siril)
    result = NoiseReductionProcessor().post_process(
        source, tmp_path / 'report.json', argparse.Namespace(enable_clarity=True),
    )
    assert calls == [('cosmic', source), ('siril', source)]
    assert result['selected_engine'] == 'siril'
    assert (tmp_path / 'report.fits').read_bytes() == b'siril'
    assert source.read_bytes() == b'original'
    assert all(issubclass(cls, processor) for cls in NoiseReductionProcessor.parameter_children())
    assert NoiseReductionProcessor.__module__ == 'lib.denoising'


def test_noise_disabled_clarity_runs_only_siril(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le mode classique n’instancie ni n’exécute le moteur neuronal."""
    def forbidden(*args: object, **kwargs: object) -> None:
        """Refuse toute création du moteur Clarity dans ce scénario."""
        raise AssertionError('Clarity ne doit pas être instancié')

    def siril(self: processor, input_path: Path, output_path: Path, args: argparse.Namespace) -> JSONReport:
        """Retourne un refus de qualité classique sans calcul d’image."""
        return dict(status='rejected', output_image=str(input_path))

    monkeypatch.setattr(CosmicClarityDenoiseProcessor, '__init__', forbidden)
    monkeypatch.setattr(SirilNoiseReductionProcessor, 'post_process', siril)
    source = tmp_path / 'source.fits'
    result = NoiseReductionProcessor().post_process(
        source, tmp_path / 'report.json', argparse.Namespace(enable_clarity=False),
    )
    assert result['status'] == 'rejected'
    assert result['output_image'] == str(source)


def test_neural_failure_preserves_child_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Une erreur technique conserve le diagnostic du moteur et reste bloquante."""
    import json

    def cosmic(self: processor, input_path: Path, output_path: Path, args: argparse.Namespace) -> JSONReport:
        """Écrit un audit technique puis simule l’échec du calcul neuronal."""
        output_path.write_text(json.dumps(dict(engine='cosmic-clarity', status='failed', detail='checkpoint')))
        raise RuntimeError('checkpoint incompatible')

    def forbidden(*args: object, **kwargs: object) -> None:
        """Le traitement Siril ne doit pas masquer une erreur technique."""
        raise AssertionError('Siril ne doit pas être exécuté après cette erreur')

    monkeypatch.setattr(CosmicClarityDenoiseProcessor, 'post_process', cosmic)
    monkeypatch.setattr(SirilNoiseReductionProcessor, 'post_process', forbidden)
    report = tmp_path / 'report.json'
    with pytest.raises(RuntimeError, match='checkpoint incompatible'):
        NoiseReductionProcessor().post_process(
            tmp_path / 'source.fits', report, argparse.Namespace(enable_clarity=True),
        )
    audit = json.loads(report.read_text())
    assert audit['status'] == 'failed'
    assert audit['evaluations']['cosmic_clarity']['detail'] == 'checkpoint'
