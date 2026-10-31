"""Détection, remise à zéro ciblée et mode de test commun sans installation réseau."""

from pathlib import Path
import platform
import subprocess
import sys
from unittest.mock import Mock

import pytest

from lib.cuda_environment import CudaEnvironmentInstaller
from lib.dependency_installation import DependencyInstaller, main
from lib.macos_environment import MacOSEnvironmentInstaller


def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
            system: str = 'Linux') -> DependencyInstaller:
    """Crée un gestionnaire simulé avec un venv reconnu et un fichier témoin."""
    monkeypatch.setattr(platform, 'system', lambda: system)
    installer_class = CudaEnvironmentInstaller if system == 'Linux' else MacOSEnvironmentInstaller
    monkeypatch.setattr(installer_class, 'check_platform', lambda: None)
    target = tmp_path / 'env'
    target.mkdir()
    (target / 'pyvenv.cfg').write_text('home = /usr/bin\n')
    (target / 'old-package').write_text('old')
    selected = DependencyInstaller(tmp_path, target)
    from types import SimpleNamespace
    from lib.installation_assets import InstallationAssets
    monkeypatch.setattr(InstallationAssets, 'resolved_options', lambda root, args: SimpleNamespace(
        cosmic_model_dir=root / 'tmp/cache/models/cosmicclarity',
        graxpert_model_dir=root / 'tmp/cache/models/graxpert',
    ))
    monkeypatch.setattr(selected, 'preload_assets', Mock())
    return selected


@pytest.mark.parametrize('system, expected', [('Linux', CudaEnvironmentInstaller),
                                             ('Darwin', MacOSEnvironmentInstaller)])
def test_detects_platform_without_creating_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, system: str, expected: type,
) -> None:
    """L'OS choisit les dépendances et aucun venv n'est créé avant install."""
    monkeypatch.setattr(platform, 'system', lambda: system)
    monkeypatch.setattr(expected, 'check_platform', lambda: None)
    detected = DependencyInstaller(tmp_path)
    assert isinstance(detected.installer, expected)
    assert not detected.installer.venv.exists()


def test_unsupported_os_fails_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une détection non couverte échoue sans mutation."""
    monkeypatch.setattr(platform, 'system', lambda: 'Windows')
    with pytest.raises(RuntimeError, match='non prise en charge'):
        DependencyInstaller(tmp_path)
    assert not (tmp_path / '.venv').exists()


@pytest.mark.parametrize('reset', [False, True])
@pytest.mark.parametrize('install_only', [False, True])
@pytest.mark.parametrize('system', ['Linux', 'Darwin'])
def test_reset_removes_all_old_contents_before_reinstall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset: bool, install_only: bool,
    system: str,
) -> None:
    """Le nettoyage réel reste limité au venv ; le test suit la nouvelle installation."""
    selected = manager(tmp_path, monkeypatch, system)
    target = selected.installer.venv
    outside = tmp_path / 'image.fits'
    outside.write_bytes(b'preserved')
    events = []

    def preflight() -> None:
        """Le pilote est contrôlé avant de toucher aux fichiers existants."""
        assert (target / 'old-package').exists()
        events.append('preflight')

    def install(skip_checks: bool) -> None:
        """Simule la réinstallation après vérification du nettoyage effectif."""
        assert (target / 'old-package').exists() is not reset
        assert skip_checks == install_only
        target.mkdir(exist_ok=True)
        (target / 'pyvenv.cfg').write_text('home = /usr/bin\n')
        (target / 'new-package').write_text('new')
        events.append('install')

    monkeypatch.setattr(selected.installer, 'preflight', preflight, raising=False)
    monkeypatch.setattr(selected.installer, 'install', install)
    monkeypatch.setattr(selected.installer, 'verify', lambda: events.append('test'))
    selected.run(reset=reset, install_only=install_only)
    expected = (['preflight'] if reset and not install_only and system == 'Linux' else [])
    expected += ['install']
    if not install_only:
        expected.append('test')
    assert events == expected
    assert outside.read_bytes() == b'preserved'
    assert (target / 'new-package').read_text() == 'new'


def test_failed_preflight_preserves_existing_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un pilote indisponible ne provoque aucune suppression même avec --reset."""
    selected = manager(tmp_path, monkeypatch)
    monkeypatch.setattr(selected.installer, 'preflight', Mock(side_effect=RuntimeError('driver')))
    install = Mock()
    monkeypatch.setattr(selected.installer, 'install', install)
    with pytest.raises(RuntimeError, match='driver'):
        selected.run(reset=True)
    assert (selected.installer.venv / 'old-package').read_text() == 'old'
    install.assert_not_called()


def test_test_mode_calls_only_verification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le mode test ne crée, n'installe, ne nettoie et ne retire aucun paquet."""
    selected = manager(tmp_path, monkeypatch)
    installer = selected.installer
    install, verify, reset = Mock(), Mock(), Mock()
    monkeypatch.setattr(installer, 'install', install)
    monkeypatch.setattr(installer, 'verify', verify)
    monkeypatch.setattr(selected, 'reset_environment', reset)
    selected.run(test=True)
    verify.assert_called_once_with()
    install.assert_not_called()
    reset.assert_not_called()
    assert (installer.venv / 'old-package').read_text() == 'old'
    with pytest.raises(ValueError):
        selected.run(reset=True, test=True)


@pytest.mark.parametrize('unsafe', ['symlink', 'missing_marker', 'linked_marker', 'project', 'home', 'base'])
def test_reset_refuses_unsafe_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsafe: str,
) -> None:
    """Le nettoyage ne suit pas un lien et protège le dépôt, le domicile et les données."""
    selected = manager(tmp_path, monkeypatch)
    target = selected.installer.venv
    if unsafe == 'symlink':
        link = tmp_path / 'link'
        link.symlink_to(target, target_is_directory=True)
        selected.installer.venv_path = link
    elif unsafe == 'missing_marker':
        (target / 'pyvenv.cfg').unlink()
    elif unsafe == 'linked_marker':
        marker = tmp_path / 'marker'
        marker.write_text('home = /usr/bin\n')
        (target / 'pyvenv.cfg').unlink()
        (target / 'pyvenv.cfg').symlink_to(marker)
    elif unsafe == 'project':
        selected.installer.venv = tmp_path
    elif unsafe == 'home':
        monkeypatch.setattr(Path, 'home', lambda: target)
    else:
        selected.installer.venv = Path(sys.base_prefix).resolve()
    with pytest.raises(ValueError, match='refusée'):
        selected.reset_environment()
    assert (target / 'old-package').read_text() == 'old'


def test_reset_missing_or_empty_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Une cible vide ou absente peut être recréée sans toucher à un dossier voisin."""
    selected = manager(tmp_path, monkeypatch)
    selected.installer.venv = tmp_path / 'missing'
    selected.installer.venv_path = selected.installer.venv
    selected.reset_environment()
    selected.installer.venv.mkdir()
    selected.reset_environment()
    assert not selected.installer.venv.exists()
    assert (tmp_path / 'env/old-package').is_file()


@pytest.mark.parametrize('flags', [['--test', '--reset'], ['--check-only', '--reset'],
                                  ['--test', '--install-only'], ['--verify-worker', '--test'],
                                  ['--verify-worker', '--reset'], ['--assets-only', '--reset'],
                                  ['--test', '--with-gaia'], ['--assets-worker', '--test']])
def test_invalid_modes_are_rejected_by_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flags: list[str],
) -> None:
    """Les combinaisons invalides sont rejetées avant détection et nettoyage."""
    monkeypatch.setattr(sys, 'argv', ['install_dependances', *flags])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


def test_common_help_and_missing_environment(tmp_path: Path) -> None:
    """L'aide shell fonctionne partout ; --test sur un venv absent ne le crée pas."""
    root = Path(__file__).resolve().parents[1]
    command = [str(root / 'bin/install_dependances.sh')]
    help_result = subprocess.run([*command, '--help'], capture_output=True, text=True)
    assert help_result.returncode == 0
    assert '--reset' in help_result.stdout and '--test' in help_result.stdout
    result = subprocess.run([*command, '--test', '--venv', str(tmp_path / 'absent')],
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert not (tmp_path / 'absent').exists()


@pytest.mark.parametrize('flags', [[], ['--test'], ['--check-only'], ['--reset'],
                                  ['--install-only'], ['--verify-worker']])
def test_cli_dispatches_modes(monkeypatch: pytest.MonkeyPatch, flags: list[str]) -> None:
    """L'interface unique transmet les modes utilisateur et le contrôle interne."""
    import lib.dependency_installation as module

    instance = Mock()
    constructor = Mock(return_value=instance)
    monkeypatch.setattr(module, 'DependencyInstaller', constructor)
    monkeypatch.setattr(sys, 'argv', ['install_dependances', *flags])
    assert main() == 0
    if '--verify-worker' in flags:
        instance.installer.verify_worker.assert_called_once_with()
        instance.run.assert_not_called()
    else:
        instance.run.assert_called_once()
        kwargs = instance.run.call_args.kwargs
        assert kwargs['reset'] is ('--reset' in flags)
        assert kwargs['test'] is any(flag in flags for flag in ('--test', '--check-only'))
        assert kwargs['install_only'] is ('--install-only' in flags)
        assert kwargs['assets'].models is True


def test_cli_reports_failure_without_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une erreur du gestionnaire retourne 1 sans annoncer une vérification réussie."""
    import lib.dependency_installation as module

    constructor = Mock(side_effect=RuntimeError('unavailable'))
    monkeypatch.setattr(module, 'DependencyInstaller', constructor)
    monkeypatch.setattr(sys, 'argv', ['install_dependances', '--test'])
    assert main() == 1


def test_reset_recreates_real_venv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Recrée réellement Python dans un venv temporaire ; pip et GPU seuls sont simulés."""
    monkeypatch.setattr(platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(CudaEnvironmentInstaller, 'check_platform', lambda: None)
    target = tmp_path / 'env'
    subprocess.run([sys._base_executable, '-m', 'venv', '--without-pip', str(target)], check=True)
    (target / 'old-package').write_text('old')
    selected = DependencyInstaller(tmp_path, target)
    monkeypatch.setattr(selected.installer, 'pip', Mock())
    monkeypatch.setattr(selected, 'preload_assets', Mock())
    monkeypatch.setattr(selected.installer, 'preflight', lambda: None)
    verify = Mock()
    monkeypatch.setattr(selected.installer, 'verify', verify)
    import argparse
    selected.run(reset=True, assets=argparse.Namespace(
        config=tmp_path / 'missing-config.json', models=False, with_gaia=False,
    ))
    assert not (target / 'old-package').exists()
    assert (target / 'pyvenv.cfg').is_file()
    check = subprocess.run([str(target / 'bin/python'), '-c', 'import sys; print(sys.prefix)'],
                           capture_output=True, text=True, check=True)
    assert check.stdout.strip() == str(target)
    verify.assert_called_once_with()


def test_assets_are_preloaded_between_pip_and_gpu_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une installation complète finit ses modèles avant d'annoncer les contrôles GPU."""
    selected = manager(tmp_path, monkeypatch)
    events = []
    monkeypatch.setattr(selected.installer, 'install', lambda skip: events.append('pip'))
    monkeypatch.setattr(selected, 'preload_assets', lambda args: events.append('models'))
    monkeypatch.setattr(selected.installer, 'verify', lambda: events.append('gpu'))
    selected.run()
    assert events == ['pip', 'models', 'gpu']
    events.clear()
    selected.run(assets_only=True)
    assert events == ['models']


def test_no_models_skips_resource_worker_and_preload_uses_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le préchargement utilise le Python du venv et --no-models évite ce processus."""
    import argparse
    from lib.installation_assets import InstallationAssets

    selected = manager(tmp_path, monkeypatch)
    parser = argparse.ArgumentParser()
    InstallationAssets.add_arguments(parser)
    args = parser.parse_args(['--no-models'])
    monkeypatch.setattr(selected.installer, 'install', Mock())
    monkeypatch.setattr(selected.installer, 'verify', Mock())
    selected.run(assets=args)
    selected.preload_assets.assert_not_called()
    run = Mock()
    monkeypatch.setattr(subprocess, 'run', run)
    DependencyInstaller.preload_assets(selected, args)
    command = run.call_args.args[0]
    assert command[0] == str(selected.installer.python)
    assert '--assets-worker' in command and '--no-models' in command
