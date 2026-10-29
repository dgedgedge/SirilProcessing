"""Vibrance liée, couleurs cohérentes et reprise sur les mêmes couches GHS."""

import argparse
import json
from pathlib import Path

from astropy.io import fits
import numpy as np
import pytest

from lib.auto_ghs import ComputeBackend, luminance
from lib.config import Config
from lib.final_color import FinalColorProcessor
from lib.postprocessing import PostProcessingProcessor
from lib.postprocess import _PostProcessorSequence
from lib.postprocess_resume import PostProcessResume
from lib.type_defs import JSONReport


def color_field(color: float = .025) -> np.ndarray:
    """Produit un objet étendu coloré et un ciel bruité sans étoiles."""
    yy, xx = np.mgrid[-1:1:128j, -1:1:160j]
    signal = .3 * np.exp(-(xx**2 + yy**2) * 5)
    sky = .04 + np.random.default_rng(43).normal(0, .0005, xx.shape)
    data = np.array([sky + signal * (1 + color), sky + signal,
                     sky + signal * (1 - color)], dtype='f4')
    return data


def test_render_preserves_luminance_and_color_direction() -> None:
    """Le gain augmente la couleur fiable sans toucher Y, le cœur ni le gris."""
    data = color_field()
    original = data.copy()
    output, report = FinalColorProcessor.render(data, 1)
    y = luminance(data)
    np.testing.assert_allclose(luminance(output), y, atol=1e-7)
    np.testing.assert_array_equal(original, data)
    assert report['status'] == 'applied'
    assert 1 < report['maximum_gain'] <= 2
    assert report['sky_median_gain'] == 1
    sky = y <= np.percentile(y[y > 1e-6], 25)
    np.testing.assert_array_equal(output[:, sky], data[:, sky])
    assert report['diffuse_median_gain'] > 1
    core = y >= report['core_threshold']
    np.testing.assert_array_equal(output[:, core], data[:, core])
    before, after = data - y, output - y
    # Le gain commun garde les vecteurs colinéaires, même avec une luminosité pondérée.
    cross = np.cross(np.moveaxis(before, 0, -1), np.moveaxis(after, 0, -1))
    assert abs(cross).max() < 1e-8
    gray = np.repeat(data[1:2], 3, axis=0)
    result, _ = FinalColorProcessor.render(gray, 1)
    np.testing.assert_array_equal(result, gray)


def test_stronger_colors_receive_less_gain() -> None:
    """La même règle traite moins fortement un objet déjà coloré."""
    _, pale = FinalColorProcessor.render(color_field(.025), 1)
    _, colorful = FinalColorProcessor.render(color_field(.35), 1)
    assert pale['diffuse_median_gain'] > colorful['diffuse_median_gain'] >= 1


@pytest.mark.parametrize('strength', [-1, 3.1, float('nan')])
def test_invalid_strength_is_refused(strength: float) -> None:
    """Les limites sont vérifiées avant de traiter les pixels."""
    with pytest.raises(ValueError, match='final-color-strength'):
        FinalColorProcessor.render(color_field(), strength)


def test_zero_and_empty_support_preserve_input() -> None:
    """La force nulle et les distributions sans signal n'inventent aucune couleur."""
    for data, strength in ((color_field(), 0), (np.zeros((3, 32, 32), 'f4'), 1)):
        output, _ = FinalColorProcessor.render(data, strength)
        np.testing.assert_array_equal(data, output)


def prepared_layers(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Écrit un historique minimal avec couches GHS rendues et provenance initiale."""
    source, background, stars, final = [tmp_path / name for name in
                                       ('input.fits', 'ghs.fits', 'stars.fits', 'ghs_final.fits')]
    data = color_field()
    fits.writeto(source, data * .02)
    prepared = tmp_path / 'prepared_linear.fits'
    prepared.write_bytes(source.read_bytes())
    fits.writeto(background, data, fits.Header({'SPNLIN': True, 'SPROLE': 'STARLESS'}))
    stellar = np.zeros_like(data)
    stellar[:, 35:38, 60:63] = .15
    fits.writeto(stars, stellar, fits.Header({'SPNLIN': True, 'SPROLE': 'STARS'}))
    fits.writeto(final, .95 * data + stellar * (1 - .95 * data),
                 fits.Header({'SPNLIN': True}))
    report = tmp_path / 'run.json'
    previous = {'gradient': {'status': 'SUCCESS'}, 'autoghs': {
        'status': 'SUCCESS', 'image_path': str(prepared), 'output_image': str(final),
        'strategies': {'range': {'starless_image': str(background)}},
        'stars': {'output_image': str(stars)}, 'parameters': {'ghs_headroom': .95},
    }}
    report.write_text(json.dumps(previous))
    PostProcessResume.save(source, report, previous)
    return source, background, stars, report


def test_color_only_skips_all_upstream_and_does_not_accumulate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deux reprises repartent du même GHS, même si l'entrée linéaire préparée manque."""
    source, background, stars, report = prepared_layers(tmp_path)
    from lib.render_exports import write_render_jpeg

    ghs = tmp_path / 'ghs_final.fits'
    ghs_before = ghs.read_bytes()
    ghs_jpeg = ghs.with_suffix('.jpg')
    write_render_jpeg(ghs, ghs_jpeg)
    previous = json.loads(report.read_text())
    previous['autoghs']['output_jpeg'] = str(ghs_jpeg)
    report.write_text(json.dumps(previous))
    (tmp_path / 'prepared_linear.fits').unlink()
    originals = [path.read_bytes() for path in (source, background, stars)]

    def forbidden(*args: object, **kwargs: object) -> None:
        """Interdit tout lancement de StarNet, GHS ou traitement linéaire."""
        raise AssertionError('Une étape amont a été relancée')

    monkeypatch.setattr(_PostProcessorSequence, 'post_process', forbidden)
    args = argparse.Namespace(color_only=True, compute_backend='cpu', final_annotation=True)
    pipeline = PostProcessingProcessor()
    first = pipeline.post_process(source, report, args)
    assert Path(first['output_image']).name == 'run_saturated.fits'
    assert Path(first['output_jpeg']).name == 'run_saturated.jpg'
    assert Path(first['output_annotated_image']).name == 'run_saturated_annotated.png'
    assert Path(first['output_annotated_image']).is_file()
    assert Path(first['output_ghs_image']).name == 'run_ghs.fits'
    assert Path(first['output_ghs_image']).read_bytes() == ghs_before
    assert Path(first['output_ghs_jpeg']).read_bytes() == ghs_jpeg.read_bytes()
    before = fits.getdata(first['output_image']).copy()
    second = pipeline.post_process(source, report, args)
    np.testing.assert_array_equal(before, fits.getdata(second['output_image']))
    assert Path(second['output_ghs_image']).read_bytes() == ghs_before
    assert [path.read_bytes() for path in (source, background, stars)] == originals
    assert 'gradient' in second['treatments'] and 'final_color' in second['treatments']
    assert 'output_jpeg' in second
    args.final_color_strength = 0
    restored = pipeline.post_process(source, report, args)
    b, s = fits.getdata(background), fits.getdata(stars)
    np.testing.assert_allclose(fits.getdata(restored['output_image']),
                               .95 * b + s * (1 - .95 * b), atol=1e-7)
    fits.setval(background, 'HISTORY', value='modified after color')
    with pytest.raises(ValueError, match='couche GHS a changé'):
        PostProcessResume.load_color(source, report)


@pytest.mark.parametrize('enabled', [True, False])
def test_full_run_places_optional_color_after_ghs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool,
) -> None:
    """L'activation remplace l'ancienne chroma avant GHS ; désactivée, aucun appel couleur."""
    from lib.quality_preview import QualityMaskPreview

    source, _, _, report = prepared_layers(tmp_path)
    previous = json.loads(report.read_text())
    calls = []

    def sequence(self: _PostProcessorSequence, input_path: Path, output_path: Path,
                 args: argparse.Namespace | None = None) -> JSONReport:
        """Simule les moteurs amont et contrôle le réglage transmis au GHS."""
        assert args.ghs_chroma == ('off' if enabled else 'enhanced')
        calls.append('ghs')
        return previous

    real_color = FinalColorProcessor.post_process

    def color(self: FinalColorProcessor, *args: object, **kwargs: object) -> JSONReport:
        """Compte le rendu couleur effectif, sans remplacer son calcul."""
        calls.append('color')
        return real_color(self, *args, **kwargs)

    monkeypatch.setattr(_PostProcessorSequence, 'post_process', sequence)
    monkeypatch.setattr(FinalColorProcessor, 'post_process', color)
    monkeypatch.setattr(QualityMaskPreview, 'ensure_for_run', lambda *args: None)
    monkeypatch.setattr(QualityMaskPreview, 'write_gradient_points', lambda *args: None)
    args = argparse.Namespace(enable_final_color=enabled, compute_backend='cpu',
                              final_annotation=False, ghs_chroma='enhanced')
    result = PostProcessingProcessor().post_process(source, report, args)
    assert calls == (['ghs', 'color'] if enabled else ['ghs'])
    assert ('final_color' in result['treatments']) == enabled
    assert Path(result['output_image']).name == ('run_saturated.fits' if enabled else 'run.fits')
    assert ('output_ghs_image' in result) == enabled
    assert args.ghs_chroma == 'enhanced'


@pytest.mark.parametrize('disabled', [False, True])
def test_autoghs_resume_runs_following_color_unless_explicitly_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, disabled: bool,
) -> None:
    """La reprise GHS active la suite malgré le défaut ou le JSON désactivé."""
    source, _, _, report = prepared_layers(tmp_path)
    previous = json.loads(report.read_text())
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'enable_final_color': False}))
    config = Config(settings)
    parser = argparse.ArgumentParser()
    config.register(PostProcessingProcessor, parser)
    flags = ['--autoghs-only', '--compute-backend', 'cpu']
    if disabled:
        flags.append('--disable-final-color')
    args = config.parse_args(parser, flags)
    args.final_annotation = False
    config.bind_parameters(args)

    def sequence(self: _PostProcessorSequence, input_path: Path, output_path: Path,
                 args: argparse.Namespace | None = None) -> JSONReport:
        """Simule GHS seul ; la vraie passe couleur doit ensuite être exécutée."""
        assert args.autoghs_only and args.enable_autoghs
        assert args.enable_final_color is not disabled
        return previous

    monkeypatch.setattr(_PostProcessorSequence, 'post_process', sequence)
    result = PostProcessingProcessor().post_process(source, report)
    assert ('final_color' in result['treatments']) is not disabled
    assert args.enable_final_color is False
    assert json.loads(settings.read_text())['enable_final_color'] is False


@pytest.mark.parametrize('suffix', ['_ghs', '_saturated'])
def test_named_exports_cannot_replace_source(tmp_path: Path, suffix: str) -> None:
    """Les nouveaux noms réservés sont refusés avant tout calcul ou écrasement."""
    source = tmp_path / f'run{suffix}.fits'
    fits.writeto(source, color_field())
    before = source.read_bytes()
    with pytest.raises(ValueError, match='remplacer la source'):
        PostProcessingProcessor().post_process(
            source, tmp_path / 'run.json', argparse.Namespace(enable_final_color=True),
        )
    assert source.read_bytes() == before


def test_source_change_and_missing_ghs_are_rejected(tmp_path: Path) -> None:
    """La reprise refuse une entrée modifiée ou un historique sans couches rendues."""
    source, _, _, report = prepared_layers(tmp_path)
    fits.setval(source, 'HISTORY', value='changed')
    with pytest.raises(ValueError, match='entrée initiale a changé'):
        PostProcessResume.load_color(source, report)
    PostProcessResume.manifest_path(report).unlink()
    report.write_text(json.dumps({'gradient': {}}))
    with pytest.raises(ValueError, match='aucun rendu AutoGHS'):
        PostProcessResume.load_color(source, report)


def test_gamut_and_incompatible_layers(tmp_path: Path) -> None:
    """Le gamut est borné sans clip et les couches incompatibles restent refusées."""
    data = color_field(.7)
    data[:, 20, 25] = (1, .5, 0)
    output, _ = FinalColorProcessor.render(data, 3)
    assert output.min() >= 0 and output.max() <= 1 and np.isfinite(output).all()
    source, background, stars, report = prepared_layers(tmp_path)
    with fits.open(stars, mode='update') as hdul:
        hdul[0].data = hdul[0].data[:, :-1, :]
    with pytest.raises(ValueError, match='Géométrie ou WCS'):
        FinalColorProcessor().post_process(background, tmp_path / 'color.json',
                                           argparse.Namespace(compute_backend='cpu'),
                                           stars_path=stars)


def test_color_options_are_persistent_but_resume_is_transient(tmp_path: Path) -> None:
    """CLI > JSON > défaut ; la relance seule ne devient pas le mode suivant."""
    path = tmp_path / 'settings.json'
    config = Config(path)
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(PostProcessingProcessor, parser)
    assert parser.parse_args([]).final_color_strength == 3.0
    assert FinalColorProcessor.options(argparse.Namespace()).final_color_strength == 3.0
    args = config.parse_args(parser, ['--color-only', '--final-color-strength', '1.5',
                                      '--star-max-width-ratio', '1.8', '-S'])
    config.bind_parameters(args)
    assert FinalColorProcessor()._get_args().star_max_width_ratio == 1.8
    assert config.save_requested(args)
    saved = json.loads(path.read_text())
    assert 'color_only' not in saved
    assert saved['final_color_strength'] == 1.5
    parser = argparse.ArgumentParser()
    Config(path).register(PostProcessingProcessor, parser)
    assert parser.parse_args([]).final_color_strength == 1.5
    assert parser.parse_args(['--final-color-strength', '.5']).final_color_strength == .5
    with pytest.raises(SystemExit):
        parser.parse_args(['--autoghs-only', '--color-only'])


def test_color_only_save_does_not_discover_starnet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Même avec -S, une reprise couleur n'exige pas un exécutable StarNet installé."""
    from lib.star_separation import StarNetSeparator

    def forbidden(*args: object) -> None:
        """Interdit une dépendance aux moteurs amont pour la sauvegarde couleur."""
        raise AssertionError('StarNet recherché pour la couleur seule')

    monkeypatch.setattr(StarNetSeparator, 'resolve_executable', forbidden)
    PostProcessingProcessor.prepare_config_save(argparse.Namespace(
        color_only=True, save_config=True, enable_autoghs=True,
    ))


def test_fusion_guard_reduces_strength(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Une dérive stellaire rejetée réduit la force au lieu de publier le candidat."""
    import lib.final_color as module

    _, background, stars, _ = prepared_layers(tmp_path)
    original = background.read_bytes()
    actual = module.recompose
    calls = []

    def guarded(*args: object, **kwargs: object) -> tuple[np.ndarray, JSONReport]:
        """Simule une première mesure rejetée et conserve ensuite le moteur réel."""
        data, audit = actual(*args, **kwargs)
        if not calls:
            audit['star_color_after_fusion']['chromaticity_mean_abs'] = .5
        calls.append(True)
        return data, audit

    monkeypatch.setattr(module, 'recompose', guarded)
    result = FinalColorProcessor().post_process(
        background, tmp_path / 'color.json', argparse.Namespace(compute_backend='cpu'),
        stars_path=stars,
    )
    assert len(result['attempts']) == 2
    assert result['attempts'][0]['rejections'] == ['star color after fusion']
    assert result['metrics']['requested_strength'] == 1.5
    assert background.read_bytes() == original


def test_cuda_matches_cpu_when_available() -> None:
    """Le backend sélectionné donne les mêmes couleurs sans changer de moteur."""
    try:
        backend = ComputeBackend('cuda')
    except RuntimeError as error:
        pytest.skip(str(error))
    data = color_field()
    cpu, _ = FinalColorProcessor.render(data, 1)
    gpu, _ = FinalColorProcessor.render(data, 1, backend=backend)
    np.testing.assert_allclose(gpu, cpu, atol=3e-6)
