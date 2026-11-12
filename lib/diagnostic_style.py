"""Présentation sombre commune aux graphiques AutoGHS et stellaires.

Le style est appliqué à une figure seule, sans changer les paramètres globaux
Matplotlib ni les pixels des images affichées. Voir les diagnostics dans
docs/scripts/postProcess/ghs-histogram-shape.md et star-rendering.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from matplotlib.figure import Figure

DIAGNOSTIC_COLORS = ('#66b3ff', '#ffbd66', '#70d68b', '#ff7b7b')


def apply_dark_diagnostic_style(figure: Figure) -> None:
    """Applique un fond noir et des textes clairs à une figure déjà construite.

    Parameters
    ----------
    figure : matplotlib.figure.Figure
        Figure contenant les axes, légendes et encarts à rendre lisibles.

    Notes
    -----
    Modifie uniquement les artistes de cette figure. Les données, échelles,
    images RGB et couleurs des courbes sont conservées. Les encarts et légendes
    reçoivent un fond gris très sombre pour rester distincts du fond noir.
    """
    from matplotlib.text import Text

    figure.set_facecolor('black')
    figure.set_edgecolor('black')
    legends = list(figure.legends)
    for axis in figure.axes:
        axis.set_facecolor('black')
        axis.tick_params(which='both', colors='#dedede')
        for spine in axis.spines.values():
            spine.set_edgecolor('#999999')
        for line in axis.get_xgridlines()+axis.get_ygridlines():
            line.set_color('#777777')
        if axis.get_legend() is not None:
            legends.append(axis.get_legend())
    for text in figure.findobj(match=Text):
        text.set_color('#dedede')
        patch = text.get_bbox_patch()
        if patch is not None:
            patch.set_facecolor('#101010')
            patch.set_edgecolor('#666666')
    for legend in legends:
        legend.get_frame().set_facecolor('#101010')
        legend.get_frame().set_edgecolor('#666666')
        legend.get_frame().set_alpha(1.)
