from lib.mosaic import Mosaic


def test_simplified_mosaic(tmp_path):
    """Test l'API simplifiée de Mosaic."""
    input_files = []
    for index in range(3):
        test_file = tmp_path / f"session_M31_panel_{index}_stacked.fits"
        test_file.write_text(f"FAKE FITS DATA FOR SESSION {index}")
        input_files.append(test_file)

    mosaic = Mosaic(
        output_dir=tmp_path / "output",
        work_dir=tmp_path / "work",
        mosaic_name="M31_complete",
        input_files=input_files,
    )

    prepared_files = mosaic.prepare_input_files()
    script_content = mosaic._generate_mosaic_script(prepared_files)

    assert len(prepared_files) == 3
    assert all(prepared_file.exists() for prepared_file in prepared_files)
    assert "M31_complete" in script_content
