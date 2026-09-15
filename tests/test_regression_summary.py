import re
import subprocess
import sys


def test_full_regression_suite_summary(tmp_path, project_root, light_process_env):
    """Lance la suite pytest principale et valide le résumé global."""
    env = light_process_env.copy()
    env["SIRILPROCESSING_REGRESSION_CHILD"] = "1"

    this_file = project_root / "tests" / "test_regression_summary.py"
    test_files = sorted(
        path
        for path in project_root.rglob("test_*.py")
        if path != this_file and ".venv" not in path.parts
    )

    report_path = tmp_path / "regression_summary.txt"
    command = [sys.executable, "-m", "pytest", "-q", *[str(path) for path in test_files]]
    result = subprocess.run(
        command,
        cwd=project_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    output = result.stdout + result.stderr
    report_path.write_text(output)

    assert result.returncode == 0, output
    assert "failed" not in output.lower(), output
    assert "error" not in output.lower(), output

    match = re.search(r"(?P<passed>\d+) passed", output)
    assert match is not None, output

    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *[str(path) for path in test_files]],
        cwd=project_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    collection_output = collected.stdout + collected.stderr
    assert collected.returncode == 0, collection_output

    collected_tests = [
        line
        for line in collection_output.splitlines()
        if "::test_" in line
    ]
    passed = int(match.group("passed"))

    assert passed == len(collected_tests), (
        f"Synthèse incohérente: {passed} tests passés pour "
        f"{len(collected_tests)} tests collectés.\n{output}"
    )

    summary = (
        f"Regression summary: {passed}/{len(collected_tests)} tests passed "
        f"across {len(test_files)} files."
    )
    assert summary.startswith("Regression summary:")
