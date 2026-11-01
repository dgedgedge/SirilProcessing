"""Sélection du venv et lancement sans installation ni accès réseau implicite."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'bin/venv_helpers.sh'
LAUNCHERS = ['postProcess', 'lightProcess', 'darkLibUpdate', 'kstarsAnalyze',
             'guideLogAnalyze', 'solarEclipseGif']


def isolated_environment(tmp_path: Path) -> dict[str, str]:
    """Interdire les installateurs et les outils réseau accessibles par PATH."""
    traps = tmp_path / 'traps'
    traps.mkdir()
    marker = tmp_path / 'forbidden-call'
    for name in ['python3', 'pip', 'pip3', 'curl', 'wget']:
        script = traps / name
        script.write_text('#!/bin/bash\nprintf forbidden > "$FORBIDDEN_CALL"\nexit 97\n')
        script.chmod(0o755)
    env = {**os.environ, 'PATH': f'{traps}:{os.environ["PATH"]}',
           'PROJECT_ROOT': str(tmp_path), 'FORBIDDEN_CALL': str(marker),
           'PIP_NO_INDEX': '1'}
    env.pop('VENV_DIR', None)
    return env


@pytest.mark.parametrize('selection', ['default', 'custom', 'legacy', 'active'])
def test_select_existing_venv(tmp_path: Path, selection: str) -> None:
    """Réutiliser le venv choisi sans dépendre du Python du terminal."""
    target = tmp_path / ('custom env' if selection == 'custom' else
                         'venv' if selection == 'legacy' else '.venv')
    subprocess.run([sys._base_executable, '-m', 'venv', '--without-pip',
                    str(target)], check=True)
    env = isolated_environment(tmp_path)
    if selection == 'custom':
        env['VENV_DIR'] = str(target)
        (tmp_path / '.venv').mkdir()
    if selection == 'default':
        (tmp_path / 'venv').mkdir()
    if selection == 'active':
        env['VIRTUAL_ENV'] = str(tmp_path / 'unrelated-active-venv')
    marker = target / 'preserve-me'
    marker.write_text('unchanged')
    result = subprocess.run(
        ['bash', '-ec', 'source "$1"; select_project_venv; '
         '"${SELECTED_VENV}/bin/python" -c "import sys; print(sys.prefix)"',
         'test', str(HELPER)], env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(target)
    assert marker.read_text() == 'unchanged'
    assert not (tmp_path / 'forbidden-call').exists()


@pytest.mark.parametrize('broken', [False, True])
def test_missing_venv_stops_without_creating_it(tmp_path: Path, broken: bool) -> None:
    """Expliquer comment installer et arrêter avant l'exécution du traitement."""
    env = isolated_environment(tmp_path)
    target = tmp_path / '.venv'
    if broken:
        target.mkdir()
    result = subprocess.run(
        ['bash', '-ec', 'source "$1"; select_project_venv; touch "$2"',
         'test', str(HELPER), str(tmp_path / 'executed')],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert 'install_dependances.sh' in result.stderr
    assert str(target) in result.stderr
    assert target.exists() is broken
    assert not (tmp_path / 'executed').exists()
    assert not (tmp_path / 'forbidden-call').exists()


@pytest.mark.parametrize('launcher', LAUNCHERS)
def test_launcher_only_executes_selected_python(tmp_path: Path, launcher: str) -> None:
    """Transmettre l'aide et les chemins intacts sans pip ni installation Chromium."""
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    shutil.copyfile(ROOT / 'bin' / f'{launcher}.sh', bindir / f'{launcher}.sh')
    shutil.copyfile(HELPER, bindir / HELPER.name)
    target = tmp_path / 'custom env'
    (target / 'bin').mkdir(parents=True)
    python = target / 'bin/python'
    python.write_text(
        f'#!{sys.executable}\nimport json, os, sys\n'
        'with open(os.environ["CALL_LOG"], "a") as stream:\n'
        '    stream.write(json.dumps(sys.argv[1:]) + "\\n")\n'
    )
    python.chmod(0o755)
    log = tmp_path / 'calls.jsonl'
    env = {**isolated_environment(tmp_path), 'VENV_DIR': str(target),
           'CALL_LOG': str(log)}
    options = ['--help', 'image space.fit', '--cosmic-model-dir=cache space']
    result = subprocess.run(['bash', str(bindir / f'{launcher}.sh'), *options],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    expected = [str(bindir / f'{launcher}.py')]
    if launcher == 'kstarsAnalyze':
        expected += ['--gui']
    assert calls == [[*expected, *options]]
    assert not (tmp_path / 'forbidden-call').exists()


@pytest.mark.parametrize('failure', ['', 'pip', 'playwright'])
def test_documentation_prepares_environment(tmp_path: Path, failure: str) -> None:
    """Préparer la documentation séparément et arrêter sur échec d'installation."""
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    shutil.copyfile(ROOT / 'bin/build_docs.sh', bindir / 'build_docs.sh')
    shutil.copyfile(HELPER, bindir / HELPER.name)
    target = tmp_path / 'docs env'
    system_python = bindir / 'python3'
    system_python.write_text(
        f'#!{sys.executable}\n'
        'import json, os, pathlib, shutil, sys\n'
        'if sys.argv[1:2] == ["-c"]:\n'
        '    print(os.environ["DOCS_STUB_PYTHON"])\n'
        '    sys.exit(0)\n'
        'with open(os.environ["CALL_LOG"], "a") as stream:\n'
        '    stream.write(json.dumps(sys.argv[1:]) + "\\n")\n'
        'if sys.argv[1:3] == ["-m", "venv"]:\n'
        '    python = pathlib.Path(sys.argv[-1]) / "bin/python"\n'
        '    python.parent.mkdir(parents=True)\n'
        '    shutil.copyfile(sys.argv[0], python)\n'
        '    python.chmod(0o755)\n'
        'if sys.argv[1:3] == ["-m", os.environ["INSTALL_FAILURE"]]:\n'
        '    sys.exit(9)\n'
    )
    system_python.chmod(0o755)
    log = tmp_path / 'calls.jsonl'
    env = {**os.environ, 'PATH': f'{bindir}:{os.environ["PATH"]}',
           'VENV_DIR': str(target), 'DOCS_STUB_PYTHON': str(system_python),
           'CALL_LOG': str(log), 'INSTALL_FAILURE': failure}
    result = subprocess.run(['bash', str(bindir / 'build_docs.sh'), '--no-pdf'],
                            env=env, capture_output=True, text=True)
    assert result.returncode == (9 if failure else 0), result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[0] == ['-m', 'venv', '--without-pip', str(target)]
    assert calls[1] == [
        '-m', 'pip', '--python', str(target / 'bin/python'), 'install',
        '--upgrade', '--quiet', '--disable-pip-version-check',
        '-r', str(tmp_path / 'requirements-docs.txt'),
    ]
    if failure == 'pip':
        assert len(calls) == 2
    else:
        assert calls[2] == ['-m', 'playwright', 'install', 'chromium']
        if failure == 'playwright':
            assert len(calls) == 3
        else:
            assert calls[3] == [str(bindir / 'build_documentation.py'),
                                '--source-root', str(tmp_path),
                                '--output-dir', str(tmp_path / 'out'), '--no-pdf']
