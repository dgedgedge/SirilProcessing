"""Recalage affine après GHS, avant les mesures de conformité.

Le pied de la première montée est approché par le quantile sombre autorisé
du minimum RGB par pixel. Le budget compte les pixels perdant au moins un
canal initialement positif ; les poids de population sont respectés. Voir
docs/scripts/postProcess/ghs-histogram-shape.md.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import ModuleType

import numpy as np

from lib.processor import processor
from lib.star_separation import validate_rgb
from lib.type_defs import JSONReport

BLACKPOINT_BINS = 4096
# Le tri de précision reste local et borné, même sur une grande mosaïque.
MAX_BLACKPOINT_SORT_PIXELS = 65536


class HistogramBlackpointNormalizer(processor):
    """Mesure le pied sombre puis applique max(0,(RGB-BP)/(1-BP))."""

    parameter_dependencies = {'ghs_blackpoint_loss'}

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie le recalage partagé par les moteurs GHS."""
        return 'ghs_post_blackpoint'

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Le budget est déclaré avec les autres options du moteur par morceaux."""

    @staticmethod
    def safe_threshold(limit: float, dtype: np.dtype) -> float:
        """Garde une marge numérique sous un seuil d'intensité normalisée.

        Parameters
        ----------
        limit : float
            Limite du quantile ou premier RGB positif, dans [0,1].
        dtype : numpy.dtype
            Type flottant de l'image, partagé par NumPy et CuPy.

        Returns
        -------
        float
            Seuil sous limit d'au moins un epsilon sur l'échelle [0,1],
            arrondi vers zéro. Un décalage sous cette précision reste nul.
            Cette marge évite que des recalages répétés sans perte théorique
            produisent des valeurs sous-normales ensuite arrondies à zéro.

        Raises
        ------
        ValueError
            Si la limite n'est pas finie, sort de [0,1] ou si le type n'est
            pas flottant.
        """
        if not np.isfinite(limit) or not 0 <= limit <= 1:
            raise ValueError('Limite de point noir hors de [0,1]')
        precision = np.finfo(dtype)
        point = max(0., limit-float(precision.eps))
        return float(np.nextafter(dtype.type(point), dtype.type(0)))

    @staticmethod
    def measure(
        data: np.ndarray, budget: float, xp: ModuleType = np,
        *, weights: np.ndarray | None = None, reference: np.ndarray | None = None,
    ) -> JSONReport:
        """Trouve un BP conservateur à partir du budget de pixels noirs.

        Parameters
        ----------
        data : numpy.ndarray
            Candidat RGB entier ou échantillonné, fini et dans [0,1], sur xp.
        budget : float
            Fraction maximale de pixels perdant un canal positif, dans [0,0.01].
        xp : module
            NumPy ou CuPy, identique au backend des tableaux.
        weights : numpy.ndarray or None
            Poids de population de forme (hauteur, largeur), strictement positifs.
        reference : numpy.ndarray or None
            Entrée fixe de la phase, de même forme. Les pertes déjà intervenues
            y consomment le budget ; None utilise le candidat comme référence.

        Returns
        -------
        JSONReport
            BP, méthode, population active, budget et perte cumulée estimée.
            Le quantile est affiné par ordre statistique sur un préfixe d'au
            plus 65536 valeurs positives. Au-delà, la borne gauche du bin
            reste conservatrice. Les pixels déjà perdus consomment le budget.
        """
        if not np.isfinite(budget) or not 0 <= budget <= .01:
            raise ValueError('Budget de point noir hors de [0,0.01]')
        validate_rgb(data)
        original = data if reference is None else reference
        if original.shape != data.shape:
            raise ValueError('Référence de point noir de géométrie différente')
        if reference is not None:
            validate_rgb(reference)
        if weights is not None and (
            weights.shape != data.shape[1:] or not xp.isfinite(weights).all()
            or not (weights > 0).all()
        ):
            raise ValueError('Poids de point noir invalides')
        active = xp.any(original > 0, axis=0)
        minima = xp.min(xp.where(original > 0, data, xp.inf), axis=0)
        values = minima[active]
        population_weights = weights[active] if weights is not None else None
        total = (float(population_weights.sum().item()) if weights is not None
                 else float(values.size))
        report: JSONReport = dict(BP=0., method='first_rise_with_blackpoint_budget',
                                  threshold_basis='minimum_initially_positive_rgb_channel',
                                  budget=budget, population=total,
                                  loss_fraction=0., loss_fraction_before=0.,
                                  remaining_loss_budget=budget,
                                  precision='histogram_bound', estimated=weights is not None)
        if not values.size:
            return dict(report, status='empty')
        low, high = float(values.min().item()), float(values.max().item())
        already_lost = (float(population_weights[values <= 0].sum().item())
                        if weights is not None else float(xp.count_nonzero(values <= 0).item()))
        report['loss_fraction'] = already_lost/total
        report['loss_fraction_before'] = already_lost/total
        report['remaining_loss_budget'] = max(0., budget-already_lost/total)
        if already_lost > budget*total+1e-10:
            return dict(report, status='budget exhausted')
        if high-low <= 1e-7:
            return dict(report, status='constant')
        counts, edges = xp.histogram(values, bins=BLACKPOINT_BINS, range=(0, high),
                                    weights=population_weights)
        index = int(xp.searchsorted(xp.cumsum(counts), budget*total, side='right').item())
        index = min(index, BLACKPOINT_BINS-1)
        point = float(edges[index].item())
        if point == 0 and low > 0:
            point = low
        # Un bin contenant les pertes antérieures pouvait bloquer le BP à
        # zéro malgré une marge restante. Ne trier que son préfixe positif,
        # en déduisant explicitement les pertes déjà acquises.
        prefix = (values > 0) & (values <= edges[index+1])
        prefix_size = int(xp.count_nonzero(prefix).item())
        report['precision_prefix_pixels'] = prefix_size
        if 0 < prefix_size <= MAX_BLACKPOINT_SORT_PIXELS:
            remaining = max(0., budget*total-already_lost)
            local = values[prefix]
            if population_weights is None:
                ordered = xp.sort(local)
                rank = min(int(remaining), prefix_size-1)
            else:
                order = xp.argsort(local)
                ordered = local[order]
                mass = xp.cumsum(population_weights[prefix][order])
                rank = min(int(xp.searchsorted(mass, remaining, side='right').item()),
                           prefix_size-1)
            point = float(ordered[rank].item())
            report['precision'] = 'bounded_order_statistic'
        # Conserver une marge d'intensité, au-delà du seul nextafter, évite
        # l'érosion numérique des derniers canaux positifs entre les étapes.
        report['quantile_limit'] = point
        report['numerical_margin'] = float(np.finfo(data.dtype).eps)
        point = HistogramBlackpointNormalizer.safe_threshold(point, data.dtype)
        report['BP'] = max(0., point)
        loss = values <= report['BP']
        report['loss_fraction'] = ((float(population_weights[loss].sum().item())
                                    if weights is not None else float(xp.count_nonzero(loss).item()))/total)
        if report['BP'] == 0:
            report['limit_reason'] = ('below_intensity_resolution'
                                      if 0 < report['quantile_limit'] <= report['numerical_margin'] else
                                      'below_histogram_resolution'
                                      if prefix_size > MAX_BLACKPOINT_SORT_PIXELS else
                                      'no_safe_positive_shift')
        return dict(report, status='applied' if point > 0 else 'unchanged')

    @classmethod
    def apply(
        cls, data: np.ndarray, budget: float, xp: ModuleType = np,
        *, weights: np.ndarray | None = None, reference: np.ndarray | None = None,
    ) -> tuple[np.ndarray, JSONReport]:
        """Recale le candidat sur le BP mesuré ; les contrôles suivent ce retour.

        Les arguments et unités sont ceux de measure. L'entrée reste intacte.
        Le même BP affine s'applique aux trois canaux, avec le blanc fixé à 1.
        """
        from lib.auto_ghs import linear_blackpoint

        audit = cls.measure(data, budget, xp, weights=weights, reference=reference)
        output = linear_blackpoint(data, audit['BP'], xp=xp) if audit['BP'] > 0 else data
        return output, audit

    def post_process(
        self, input_path: Path, output_path: Path, args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Écrit un fond recalé et son audit, en préservant le FITS source."""
        from astropy.io import fits
        from lib.auto_ghs import GHSOptimizer
        from lib.restoration_quality import normalized_planes
        from lib.star_separation import write_rgb

        source, report_path = Path(input_path).resolve(), Path(output_path).resolve()
        destination = report_path.with_suffix('.fits')
        if source in (report_path, destination):
            raise ValueError('Le point noir ne peut pas remplacer la source')
        options = GHSOptimizer.options(self._get_args(args))
        output, report = self.apply(normalized_planes(source), options.ghs_blackpoint_loss)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        write_rgb(source, destination, output,
                  nonlinear=bool(fits.getheader(source).get('SPNLIN', False)))
        report['output_image'] = str(destination)
        report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        return report
