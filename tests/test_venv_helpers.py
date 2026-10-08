"""Vérifie la préparation des venv sans pip et sans installation système."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


HELPER = Path(__file__).resolve().parents[1] / 'bin' / 'venv_helpers.sh'


@pytest.mark.parametrize('selection', ['default', 'custom', 'legacy', 'active'])
def test_prepare_without_pip(tmp_path: Path, selection: str) -> None:
    """Crée le venv choisi et conserve pip uniquement dans le Python système."""
    target = tmp_path / ('custom env' if selection == 'custom' else
                         'venv' if selection == 'legacy' else '.venv')
    requirements = tmp_path / 'requirements.txt'
    requirements.write_text('', encoding='utf-8')
    env = {**os.environ, 'PIP_NO_INDEX': '1', 'PROJECT_ROOT': str(tmp_path)}
    env.pop('VENV_DIR', None)
    if selection == 'custom':
        env['VENV_DIR'] = str(target)
    if selection == 'legacy':
        target.mkdir()
    if selection == 'active':
        active = tmp_path / 'active'
        subprocess.run([sys._base_executable, '-m', 'venv', '--without-pip',
                        str(active)], check=True)
        env['PATH'] = f"{active / 'bin'}:{env['PATH']}"
        env['VIRTUAL_ENV'] = str(active)
    result = subprocess.run(
        ['bash', '-ec', 'source "$1"; prepare_project_venv "$2"; '
         '"${SELECTED_VENV}/bin/python" -c '
         '\'import sys, importlib.util; print(sys.prefix); '
         'assert importlib.util.find_spec("pip") is None\'',
         'test', str(HELPER), str(requirements)],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert str(target) in result.stdout
    assert (target / 'bin' / 'python').exists()


def test_install_failure_stops_execution(tmp_path: Path) -> None:
    """Une dépendance introuvable empêche l’exécution du traitement suivant."""
    requirements = tmp_path / 'requirements.txt'
    requirements.write_text('siril-nonexistent-test-package==0\n', encoding='utf-8')
    env = {**os.environ, 'PIP_NO_INDEX': '1', 'PROJECT_ROOT': str(tmp_path),
           'VENV_DIR': str(tmp_path / '.venv')}
    result = subprocess.run(
        ['bash', '-ec', 'source "$1"; prepare_project_venv "$2"; '
         'touch "$3"', 'test', str(HELPER), str(requirements),
         str(tmp_path / 'executed')], env=env, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert not (tmp_path / 'executed').exists()
