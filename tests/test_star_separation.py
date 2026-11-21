"""Contrats StarNet : une séparation linéaire, erreurs fatales et préférences isolées."""
from lib.siril_api import format_commands as _format_commands, SirilCommand as _SirilCommand

import argparse
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from lib.star_separation import StarNetSeparator, quote_siril
from lib.type_defs import JSONReport


@pytest.mark.parametrize('adaptive', [True, False])
def test_separation_signed_residual_and_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adaptive: bool,
) -> None:
    """Une sortie retournée est réalignée et son résidu signé reconstruit l'entrée."""
    source = tmp_path / "rgb.fits"
    data = np.linspace(0.01, 0.7, 3 * 8 * 9).reshape(3, 8, 9).astype("float32")
    fits.writeto(source, data, fits.Header({"ROWORDER": "TOP-DOWN", "OBJECT": "Test"}))
    executable = tmp_path / "starnet++"
    executable.write_text("test")
    executable.chmod(0o755)
    calls = []

    class FakeSiril:
        """Double externe enregistrant les préférences et la commande exécutée."""

        def __init__(self, **kwargs):
            """Accepte le service configuré."""

        def run_commands(self, script, work, **kwargs):
            """Émet un starless signé, dans l'autre orientation FITS."""
            script = _format_commands(script)
            calls.append(script)
            assert kwargs["initfile"].is_file()
            fits.writeto(
                Path(work) / "starless_linear.fits",
                (data + 0.001)[:, ::-1],
                fits.Header({"ROWORDER": "BOTTOM-UP"}),
            )
            return True

    monkeypatch.setattr("lib.star_separation.Siril", FakeSiril)
    report = StarNetSeparator().post_process(
        source,
        tmp_path / "separation.json",
        argparse.Namespace(starnet_path=executable, starnet_adaptive_stretch=adaptive,
                           starnet_target_background=None),
    )
    assert report["stretch_adaptation"]["enabled"] is adaptive
    assert len(calls) == 1
    assert "starnet -stretch" in calls[0]
    assert "-nostarmask" not in calls[0]
    starless = fits.getdata(report["starless_linear"])
    stars = fits.getdata(report["stars_linear"])
    np.testing.assert_allclose(starless + stars, data, atol=1e-7)
    assert np.all(stars < 0)
    assert report["negative_fraction"] == 1
    assert fits.getheader(report["starless_linear"])["OBJECT"] == "Test"
    np.testing.assert_array_equal(fits.getdata(source), data)


@pytest.mark.parametrize(
    "header", [{"SPNLIN": True}, {"BAYERPAT": "RGGB"}, {"FILTER": "Starless"}]
)
def test_invalid_input_rejected(tmp_path: Path, header: dict) -> None:
    """Les entrées connues non linéaires/CFA/starless sont explicitement refusées."""
    source = tmp_path / "input.fits"
    fits.writeto(source, np.ones((3, 8, 8), dtype="float32") * 0.1, fits.Header(header))
    with pytest.raises(ValueError):
        StarNetSeparator().post_process(
            source, tmp_path / "report.json", argparse.Namespace()
        )


def test_quote_rejects_injection() -> None:
    """Un chemin ne peut pas ajouter de commande Siril."""
    with pytest.raises(ValueError):
        quote_siril('test"\nexit')


def test_uint16_conversion_preserves_scale_and_extensions(tmp_path: Path) -> None:
    """Le passage FITS entier vers float32 ne conserve pas un BZERO/BSCALE obsolète."""
    from lib.restoration_quality import normalized_planes
    from lib.star_separation import write_rgb

    source = tmp_path / "integer.fits"
    data = np.linspace(0, 65535, 3 * 8 * 8).reshape(3, 8, 8).astype(np.uint16)
    fits.HDUList(
        [fits.PrimaryHDU(data), fits.ImageHDU(np.arange(5), name="AUX")]
    ).writeto(source)
    normalized = normalized_planes(source)
    output = tmp_path / "float.fits"
    write_rgb(source, output, normalized, role="STARLESS")
    np.testing.assert_allclose(fits.getdata(output), data / 65535, atol=1e-7)
    with fits.open(output) as hdul:
        assert hdul[0].header["SPROLE"] == "STARLESS"
        assert hdul["AUX"].data.tolist() == list(range(5))


def test_starnet_failure_is_fatal(tmp_path: Path, monkeypatch) -> None:
    """Un échec de binaire/modèle ne rend ni image étoilée de secours ni succès."""
    from unittest.mock import Mock

    source = tmp_path / "rgb.fits"
    fits.writeto(source, np.full((3, 8, 8), 0.01, dtype=np.float32))
    executable = tmp_path / "starnet++"
    executable.touch()
    executable.chmod(0o755)
    fake_siril = Mock()
    fake_siril.run_commands.return_value = False
    monkeypatch.setattr("lib.star_separation.Siril", Mock(return_value=fake_siril))
    with pytest.raises(RuntimeError, match="StarNet échouée"):
        StarNetSeparator().post_process(
            source,
            tmp_path / "failure.json",
            argparse.Namespace(starnet_path=executable),
        )
    assert fake_siril.run_commands.call_count == 1
    assert not (tmp_path / "failure.json").exists()


@pytest.mark.parametrize("value", [np.nan, np.inf, -0.001, 1.001])
def test_invalid_intensities_are_never_silently_clipped(value: float) -> None:
    """La frontière image refuse les valeurs non finies et hors domaine."""
    from lib.star_separation import validate_rgb

    with pytest.raises(ValueError):
        validate_rgb(np.full((3, 8, 8), value))


@pytest.mark.parametrize('peak', [1.00004351, 16777216.0])
def test_raw_starless_overshoot_is_preserved_for_compensation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, peak: float,
) -> None:
    """Conserver le léger dépassement MTF et produire un résidu additif compensable."""
    from unittest.mock import Mock
    from lib.final_render import AutoGHSFinalRenderer

    source = tmp_path / 'source.fits'
    data = np.full((3, 8, 9), .4, dtype=np.float32)
    raw = data.copy()
    raw[0, 3, 4] = peak
    fits.writeto(source, data)
    executable = tmp_path / 'starnet++'
    executable.touch()
    executable.chmod(0o755)

    def separate(script: str, work: str, **kwargs: object) -> bool:
        """Émet la sortie réseau brute, sans modifier l'entrée originale."""
        script = _format_commands(script)
        fits.writeto(Path(work) / 'starless_linear.fits', raw)
        return True

    runner = Mock()
    runner.run_commands.side_effect = separate
    monkeypatch.setattr('lib.star_separation.Siril', Mock(return_value=runner))
    if peak > 2:
        with pytest.raises(ValueError, match='somme linéaire en float32'):
            StarNetSeparator().post_process(
                source, tmp_path / 'separation.json',
                argparse.Namespace(starnet_path=executable),
            )
        return
    report = StarNetSeparator().post_process(
        source, tmp_path / 'separation.json', argparse.Namespace(starnet_path=executable),
    )
    background = fits.getdata(report['starless_linear'])
    residual = fits.getdata(report['stars_linear'])
    assert report['raw_starless_above_one_count'] == 1
    assert report['raw_starless_maximum'] == pytest.approx(1.00004351)
    np.testing.assert_array_equal(background, raw)
    corrected, stars, audit = AutoGHSFinalRenderer.compensate_residual(
        background, residual, .005,
    )
    assert corrected.max() <= 1 and stars.min() >= 0
    np.testing.assert_allclose(corrected + stars, data, atol=1e-7)
    assert audit['raw_starless_above_one_count'] == 1
    np.testing.assert_array_equal(fits.getdata(source), data)


@pytest.mark.parametrize('value', [np.nan, np.inf, -.001])
def test_raw_starless_still_rejects_nonfinite_and_negative(value: float) -> None:
    """L'exception de borne haute ne relâche pas les autres contrôles RGB."""
    from lib.star_separation import validate_rgb

    with pytest.raises(ValueError):
        validate_rgb(np.full((3, 8, 8), value), allow_upper_overshoot=True)


@pytest.mark.parametrize('target', [.05, .08, .2])
def test_controlled_stretch_reaches_target_and_inverts_unclipped_values(target: float) -> None:
    """La MTF contrôlée atteint la médiane demandée et conserve les unités linéaires."""
    rng = np.random.default_rng(19)
    data = np.clip(rng.normal(.02, .001, (3, 64, 64)), 0, 1).astype('f4')
    original = data.copy()
    stretched, parameters = StarNetSeparator.prepare_stretch(data, target)
    linear = StarNetSeparator.invert_stretch(stretched, parameters)
    for index, settings in enumerate(parameters['channels']):
        assert np.median(stretched[index]) == pytest.approx(target, abs=1e-6)
        mask = data[index] >= settings['shadows']
        np.testing.assert_allclose(linear[index][mask], data[index][mask], atol=1e-8)
        np.testing.assert_allclose(linear[index][~mask], settings['shadows'], atol=1e-8)
        assert settings['black_clipped_count'] == int(np.count_nonzero(~mask))
    np.testing.assert_array_equal(data, original)
    assert np.isfinite(stretched).all() and 0 <= stretched.min() <= stretched.max() <= 1


@pytest.mark.parametrize('target', [0, .5, 1, -.1, np.nan, np.inf, True])
def test_controlled_stretch_rejects_invalid_target(target: float) -> None:
    """La cible est une intensité finie strictement comprise entre zéro et 0,5."""
    with pytest.raises(ValueError, match='starnet-target-background'):
        StarNetSeparator.prepare_stretch(np.full((3, 8, 8), .1), target)


@pytest.mark.parametrize('level', [0., .1, 1.])
def test_controlled_stretch_handles_constant_channels(level: float) -> None:
    """Une MAD nulle n'introduit pas de division par zéro ni de fausse structure."""
    data = np.full((3, 8, 8), level, dtype='f4')
    prepared, parameters = StarNetSeparator.prepare_stretch(data, .08)
    np.testing.assert_allclose(StarNetSeparator.invert_stretch(prepared, parameters), data,
                               atol=1e-7)
    if level in (0., 1.):
        assert all(channel['fallback'] == 'degenerate median' for channel in parameters['channels'])


def test_controlled_stretch_rejects_invalid_inverse_parameters() -> None:
    """Un inverse incomplet ou non représentable n'est pas remplacé silencieusement."""
    data = np.ones((3, 8, 8), dtype='f4')*.1
    with pytest.raises(ValueError, match='incomplets'):
        StarNetSeparator.invert_stretch(data, {})
    _, parameters = StarNetSeparator.prepare_stretch(data, .08)
    parameters['channels'][0]['midtones'] = 0.
    with pytest.raises(ValueError, match='invalides'):
        StarNetSeparator.invert_stretch(data, parameters)


def test_manual_stretch_calls_starnet_without_a_second_stretch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La séparation prépare une seule MTF, l'inverse, puis calcule le résidu signé."""
    data = np.linspace(.01, .2, 3*8*9).reshape(3, 8, 9).astype('f4')
    source = tmp_path/'source.fits'
    fits.writeto(source, data, fits.Header({'ROWORDER': 'TOP-DOWN'}))
    executable = tmp_path/'starnet++'
    executable.write_text('test')
    executable.chmod(0o755)
    calls = []

    class IdentityStarNet:
        """Simule la sortie TIFF sans altération pour contrôler la composition MTF."""

        def __init__(self, **kwargs: object) -> None:
            """Accepte les paramètres du service Siril isolé."""

        def run_commands(self, operations: list[_SirilCommand], work: str, **kwargs: object) -> bool:
            """Retourne l'entrée étirée, dans l'autre convention ROWORDER."""
            calls.append(_format_commands(operations))
            prepared = fits.getdata(Path(work)/'input.fits')
            assert fits.getheader(Path(work)/'input.fits')['SPNLIN']
            fits.writeto(Path(work)/'starless_linear.fits', prepared[:, ::-1],
                         fits.Header({'ROWORDER': 'BOTTOM-UP'}))
            return True

    monkeypatch.setattr('lib.star_separation.Siril', IdentityStarNet)
    report = StarNetSeparator().post_process(
        source, tmp_path/'separation.json',
        argparse.Namespace(starnet_path=executable, starnet_target_background=.08),
    )
    assert '-stretch' not in calls[0]
    assert report['prestretch_parameters']['target_background'] == .08
    np.testing.assert_allclose(fits.getdata(report['starless_linear']), data, atol=1e-7)
    np.testing.assert_allclose(fits.getdata(report['starless_linear'])
                               + fits.getdata(report['stars_linear']), data, atol=1e-7)
    np.testing.assert_array_equal(fits.getdata(source), data)


def test_controlled_target_obeys_cli_json_and_instance_snapshots(tmp_path: Path) -> None:
    """La cible suit CLI > JSON > défaut, avec sauvegarde explicite et instances isolées."""
    import json
    from lib.config import Config

    path = tmp_path/'parameters.json'
    path.write_text(json.dumps(dict(starnet_target_background=.12)))
    config = Config(path)
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(StarNetSeparator, parser)
    args = config.parse_args(parser, [])
    config.bind_parameters(args)
    first = StarNetSeparator()
    assert first._get_args().starnet_target_background == .12
    args = config.parse_args(parser, ['--starnet-target-background', '.08', '-S'])
    config.bind_parameters(args)
    assert config.save_requested(args)
    assert json.loads(path.read_text())['starnet_target_background'] == .08
    assert first._get_args().starnet_target_background == .12


def test_controlled_target_can_return_to_auto_after_saved_override(tmp_path: Path) -> None:
    """Une option CLI explicite auto annule aussi une cible stockée dans le JSON."""
    import json
    from lib.config import Config

    path = tmp_path/'parameters.json'
    path.write_text(json.dumps(dict(starnet_target_background=.08)))
    config = Config(path)
    parser = argparse.ArgumentParser()
    config.add_arguments(parser)
    config.register(StarNetSeparator, parser)
    args = config.parse_args(parser, ['--starnet-target-background', 'auto', '-S'])
    assert args.starnet_target_background is None
    assert config.save_requested(args)
    assert json.loads(path.read_text())['starnet_target_background'] is None


@pytest.mark.parametrize('value', ['wrong', '-.1', '0', '.5', 'nan', 'inf'])
def test_invalid_target_is_rejected_by_cli(value: str) -> None:
    """La ligne de commande refuse immédiatement les cibles invalides."""
    parser = argparse.ArgumentParser()
    StarNetSeparator.add_arguments(parser)
    with pytest.raises(SystemExit):
        parser.parse_args(['--starnet-target-background', value])


def adaptive_star_field(sigma_y: float, sigma_x: float | None = None) -> np.ndarray:
    """Construit une source brillante sur un fond bruité, sans dépendre d'un objet réel."""
    y, x = np.indices((401, 401))
    sigma_x = sigma_y if sigma_x is None else sigma_x
    background = np.random.default_rng(21).normal(.02, .0005, y.shape)
    star = .98*np.exp(-.5*((y-200)**2/sigma_y**2+(x-200)**2/sigma_x**2))
    light = np.clip(background+star, 0, 1).astype('f4')
    return np.array([light, light, light])


def test_adaptive_stretch_responds_to_measured_bright_star_extent() -> None:
    """Une source trop étendue réduit la cible, sans nom de cible ni réglage par objet."""
    source = adaptive_star_field(18.)
    original = source.copy()
    prepared, parameters, audit = StarNetSeparator.adaptive_stretch(source, 96.)
    assert audit['adapted']
    assert parameters is not None
    assert .05 <= audit['selected_target'] < .25
    assert audit['initial_maximum_diameter_px'] > 96.
    assert audit['selected_maximum_diameter_px'] <= 96.
    assert audit['limit_met']
    assert len(audit['trials']) > 1
    np.testing.assert_array_equal(source, original)
    np.testing.assert_allclose(np.median(prepared, axis=(1, 2)), audit['selected_target'], atol=1e-5)


def test_adaptive_stretch_keeps_standard_siril_for_small_stars() -> None:
    """Une image déjà admissible conserve exactement la commande Siril historique."""
    source = adaptive_star_field(3.)
    prepared, parameters, audit = StarNetSeparator.adaptive_stretch(source)
    assert parameters is None
    assert prepared is source
    assert not audit['adapted']
    assert audit['limit_met']
    assert audit['initial_maximum_diameter_px'] < 96.


def test_adaptive_stretch_does_not_treat_an_elongated_bright_region_as_a_star() -> None:
    """La mesure exige un contour assez rond et ne repose pas sur la seule luminosité."""
    source = adaptive_star_field(18., 4.)
    _, parameters, audit = StarNetSeparator.adaptive_stretch(source)
    assert parameters is None
    assert not audit['adapted']
    assert audit['limit_met'] is None


def test_adaptive_stretch_no_bright_core_is_not_a_size_certificate() -> None:
    """Sans noyau brillant, aucune mesure de diamètre n'est inventée."""
    source = np.full((3, 16, 16), .01, dtype='f4')
    _, parameters, audit = StarNetSeparator.adaptive_stretch(source)
    assert parameters is None
    assert not audit['adapted'] and audit['limit_met'] is None
    assert audit['bright_source_count'] == 0


@pytest.mark.parametrize('limit', [0, -1, 192, np.inf, np.nan, True])
def test_adaptive_stretch_rejects_invalid_diameter_limit(limit: float) -> None:
    """La limite doit être finie, positive et comprise dans la fenêtre mesurée."""
    with pytest.raises(ValueError, match='starnet-max-star-diameter'):
        StarNetSeparator.adaptive_stretch(np.zeros((3, 8, 8), dtype='f4'), limit)


def test_unmeasurable_adaptive_trials_do_not_bypass_the_size_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La disparition d'un contour ne certifie pas une réduction de diamètre."""
    count = 0

    def measure(data: np.ndarray, points: list[tuple[int, int]]) -> list[JSONReport]:
        """Le premier contour est trop gros ; les suivants deviennent ambigus."""
        nonlocal count
        count += 1
        if count == 1:
            return [dict(y=200, x=200, measurable=True, roundness=1., diameter_px=120.)]
        return [dict(y=200, x=200, measurable=False)]

    monkeypatch.setattr(StarNetSeparator, '_star_footprints', staticmethod(measure))
    source = adaptive_star_field(10.)
    _, parameters, audit = StarNetSeparator.adaptive_stretch(source)
    assert parameters is None and not audit['adapted']
    assert audit['limit_met'] is False
    assert all(not trial['measurable'] for trial in audit['trials'][1:])


def test_adaptive_stretch_records_when_the_diameter_limit_cannot_be_reached() -> None:
    """Une étoile très étendue n'est pas déclarée conforme après le dernier essai."""
    _, parameters, audit = StarNetSeparator.adaptive_stretch(adaptive_star_field(24.))
    assert parameters is not None and audit['adapted']
    assert audit['selected_target'] == .05
    assert audit['limit_met'] is False
    assert audit['selected_maximum_diameter_px'] > audit['diameter_limit_px']
