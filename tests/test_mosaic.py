from pathlib import Path

import pytest

from lib.mosaic import Mosaic, calculate_common_basename


@pytest.mark.parametrize("session_names, expected", [
    (["session_M31_nord", "session_M31_sud"], "session_M31"),
    (["NGC7000_panel1", "NGC7000_panel2", "NGC7000_panel3"], "NGC7000_panel"),
    (["M42_rouge", "M42_vert", "M42_bleu"], "M42"),
    (["IC1396_A", "IC1396_B"], "IC1396"),
    (["session1", "session2"], "session"),
    (["A", "B"], ""),
    (["completely_different", "names_here"], ""),
    (["single_session"], "single_session"),
    (["M31_mosaic_part_1", "M31_mosaic_part_2"], "M31_mosaic_part"),
    (["2023_10_15_M31", "2023_10_15_M42"], "2023_10_15_M"),
    ([], ""),
    (["north_M31", "south_M31"], "M31"),
])
def test_calculate_common_basename(session_names, expected):
    session_dirs = [Path(name) for name in session_names]
    assert calculate_common_basename(session_dirs) == expected


def test_mosaic_accepts_explicit_input_files(tmp_path):
    input_files = []
    for index in range(2):
        input_file = tmp_path / f"session_M31_panel_{index}.fits"
        input_file.write_text(f"fake fits panel {index}")
        input_files.append(input_file)

    mosaic = Mosaic(
        output_dir=tmp_path / "output",
        work_dir=tmp_path / "work",
        mosaic_name="M31_complete",
        input_files=input_files,
    )

    assert mosaic.mosaic_name == "M31_complete"
    assert mosaic.input_files == input_files
    assert mosaic.mosaic_work_dir == tmp_path / "work" / "mosaic_M31_complete"


def test_mosaic_prepare_input_files(tmp_path):
    input_files = []
    for index in range(2):
        input_file = tmp_path / f"session_M31_panel_{index}.fits"
        input_file.write_text(f"fake fits panel {index}")
        input_files.append(input_file)

    mosaic = Mosaic(
        output_dir=tmp_path / "output",
        work_dir=tmp_path / "work",
        mosaic_name="M31_complete",
        input_files=input_files,
    )

    prepared_files = mosaic.prepare_input_files()

    assert len(prepared_files) == 2
    assert all(prepared_file.exists() for prepared_file in prepared_files)
    assert all(prepared_file.name.startswith("panel_") for prepared_file in prepared_files)
    assert [path.read_bytes() for path in prepared_files] == [path.read_bytes() for path in input_files]
