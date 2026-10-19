"""Contrôle des vues stellaires directes et des deux histogrammes logarithmiques."""

from pathlib import Path

import numpy as np
import pytest
from matplotlib.figure import Figure
from PIL import Image

from lib.star_diagnostics import write_star_diagnostic
from lib.star_rendering import ConservativeStarRenderer


@pytest.mark.parametrize("row_order", ["BOTTOM-UP", "TOP-DOWN"])
def test_stellar_figure_scales_and_orientation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, row_order: str,
) -> None:
    """Les titres, niveaux directs, orientation FITS et quatre axes sont vérifiés."""
    light = np.linspace(0, 0.1, 120, dtype=np.float32).reshape(10, 12)
    before = np.array([light, 0.6 * light, 0.3 * light])
    after, report = ConservativeStarRenderer.render(before)
    save = Figure.savefig

    def inspect(figure: Figure, *args: object, **kwargs: object) -> None:
        """Vérifie les objets réellement transmis au moteur PNG."""
        axes = figure.axes
        assert len(axes) == 4
        expected = before[:, ::-1] if row_order == "BOTTOM-UP" else before
        np.testing.assert_array_equal(
            axes[0].images[0].get_array(), expected.transpose(1, 2, 0),
        )
        assert "Avant" in axes[0].get_title()
        assert "Après" in axes[1].get_title()
        assert all(axis.get_xscale() == axis.get_yscale() == "log" for axis in axes[2:])
        assert "Pics candidats" in axes[3].get_title()
        assert any("OBJECTIF" in text.get_text() and "essais" in text.get_text()
                   for text in figure.texts)
        save(figure, *args, **kwargs)

    monkeypatch.setattr(Figure, "savefig", inspect)
    destination = tmp_path / "stars.png"
    write_star_diagnostic(before, after, report, destination, row_order=row_order)
    with Image.open(destination) as image:
        assert image.format == "PNG"
        assert image.size == (1680, 1320)


def test_empty_stars_have_valid_log_diagnostic(
    tmp_path: Path, recwarn: pytest.WarningsRecorder,
) -> None:
    """Le log d'un champ nul est vide, sans avertissement ni valeurs artificielles."""
    before = np.zeros((3, 8, 8), dtype=np.float32)
    after, report = ConservativeStarRenderer.render(before)
    write_star_diagnostic(before, after, report, tmp_path / "empty.png")
    assert not recwarn.list
    assert report["visibility"]["candidate_count"] == 0


@pytest.mark.parametrize(
    "row_order,shape", [("unknown", (3, 8, 8)), ("TOP-DOWN", (3, 8, 9))],
)
def test_incompatible_diagnostic_is_rejected(
    tmp_path: Path, row_order: str, shape: tuple[int, int, int],
) -> None:
    """Une orientation ou géométrie différente ne produit pas de faux comparatif."""
    before = np.zeros((3, 8, 8), dtype=np.float32)
    with pytest.raises(ValueError):
        write_star_diagnostic(
            before, np.zeros(shape), {}, tmp_path / "bad.png", row_order=row_order,
        )
    assert not (tmp_path / "bad.png").exists()
