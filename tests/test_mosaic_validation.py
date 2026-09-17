import pytest

from lib.mosaic import Mosaic


def test_mosaic_name_is_explicit_in_current_api(tmp_path):
    input_file = tmp_path / "panel.fit"
    input_file.write_text("fake fits")

    mosaic = Mosaic(
        output_dir=tmp_path / "output",
        work_dir=tmp_path / "work",
        mosaic_name="test_explicite",
        input_files=[input_file],
    )

    assert mosaic.mosaic_name == "test_explicite"
    assert mosaic.mosaic_input_dir == tmp_path / "work" / "mosaic_test_explicite" / "input"


def test_mosaic_prepare_rejects_missing_inputs(tmp_path):
    mosaic = Mosaic(
        output_dir=tmp_path / "output",
        work_dir=tmp_path / "work",
        mosaic_name="missing_inputs",
        input_files=[tmp_path / "missing.fit"],
    )

    with pytest.raises(ValueError, match="Aucun fichier d'entrée"):
        mosaic.prepare_input_files()
