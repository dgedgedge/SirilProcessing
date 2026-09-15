from pathlib import Path

from lib.mosaic import Mosaic


def test_integrated_script():
    """Teste la génération du script Siril intégré."""
    mosaic = Mosaic(
        output_dir=Path("/tmp/mosaic_output"),
        work_dir=Path("/tmp/mosaic_work"),
        mosaic_name="M31_test",
        input_files=[
            Path("panel_01_session_M31_nord.fit"),
            Path("panel_02_session_M31_sud.fit"),
        ],
    )

    script_content = mosaic._generate_mosaic_script(mosaic.input_files)

    assert "requires 1.2.0" in script_content
    assert "convert mosaic_" in script_content
    assert "seqplatesolve mosaic_ -force" in script_content
    assert "seqapplyreg mosaic_ -framing=max" in script_content
    assert "stack r_mosaic_" in script_content
    assert "M31_test_mosaic" in script_content
    assert "close" in script_content
