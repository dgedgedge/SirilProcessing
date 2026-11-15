"""Composition reproductible des étirements et courbe cumulée de gris neutres.

Une recette agit sur des triplets RGB ; sa courbe scalaire représente seulement
la restriction aux gris R=G=B, ou leur enveloppe pour une sélection spatiale. Elle n'est jamais présentée comme une LUT exacte
pour tous les pixels colorés. Voir docs/scripts/postProcess/stretch-transfer.md.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import numpy as np

from lib.type_defs import JSONReport
from lib.stellar_selection import stellar_selection_weights

CURVE_TOLERANCE = 1e-7  # Erreur absolue observée aux sondes d'interpolation.
INITIAL_CURVE_POINTS = 2049
MAX_CURVE_POINTS = 262145
MAX_CURVE_REFINEMENTS = 24  # Évite de subdiviser au-delà de la résolution float64.
GHS_KEYS = ('D', 'B', 'SP', 'LP', 'HP')


def stellar_noise_gate(light: np.ndarray, threshold: float) -> np.ndarray:
    """Retourne une porte douce monotone, nulle en zéro et égale à 1 en un.

    Parameters
    ----------
    light : np.ndarray
        Intensités flottantes normalisées, finies dans [0,1], validées par
        l'appelant. Aucune modification en place.
    threshold : float
        Seuil normalisé fini dans [0,1] ; zéro désactive la porte.

    Returns
    -------
    np.ndarray
        Facteur multiplicatif de même forme et dtype que light.

    Raises
    ------
    ValueError
        Seuil non fini ou hors domaine.

    Notes
    -----
    Un rapport borné calculé en float64 évite les quotients 0/0
    pour les seuils très petits et les débordements des puissances.
    La porte ne distingue pas une étoile du bruit de même intensité. Voir
    docs/scripts/postProcess/star-rendering.md pour la formule et les limites.

    Examples
    --------
    >>> stellar_noise_gate(np.array([0., 1.]), 0.001)
    array([0., 1.])
    """
    if not np.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError('Seuil de bruit stellaire hors de [0,1]')
    if threshold == 0:
        return np.ones_like(light)
    values = np.asarray(light, dtype=np.float64)
    ratio = np.minimum(values, threshold) / np.maximum(values, threshold)
    power = ratio ** 4
    gate = np.where(values >= threshold, 1 / (1 + power), power / (1 + power))
    gate *= 1 + threshold ** 4
    return gate.astype(light.dtype)


def stellar_operation(parameters: JSONReport, balance: JSONReport | None = None) -> JSONReport:
    """Décrit une courbe stellaire, ses quantiles et son seuil de bruit retenu.

    parameters et balance proviennent du rapport du moteur ; le seuil est nul
    pour les recettes historiques. Les tables sont copiées, sans redétection
    de pics ni nouvelle estimation du bruit lors de la réapplication. La sélection
    spatiale et ses coordonnées sont également copiées.
    """
    balance = balance or {}
    return dict(kind='stars', strength=float(parameters['strength']),
                ceiling=float(parameters['ceiling']), faint_boost=float(parameters['faint_boost']),
                noise_threshold=float(
                    parameters.get('noise_suppression', {}).get('threshold', 0.)),
                histogram_strength=float(parameters.get('histogram_strength',
                    balance.get('accepted_strength', 0.))),
                knots=deepcopy(balance.get('input_knots', [])),
                levels=deepcopy(balance.get('target_knots', [])),
                boost_selection=deepcopy(parameters.get('boost_selection', {'mode': 'all'})))


class CumulativeStretchTransform:
    """Rejoue une composition RGB et échantillonne sa courbe neutre ou son enveloppe."""

    def __init__(self, domain: tuple[float, float], operations: list[JSONReport]) -> None:
        """Capture des intensités normalisées min/max et une recette indépendante.

        Le domaine doit être fini, ordonné et inclus dans [0,1]. Les opérations
        sont copiées afin qu'un autre essai ne puisse modifier cette recette.
        """
        low, high = domain
        if not np.isfinite(domain).all() or not 0 <= low <= high <= 1:
            raise ValueError('Domaine initial invalide pour la transformation cumulée')
        self.domain = float(low), float(high)
        self.operations = deepcopy(operations)
        spatial = any(op.get('kind') == 'stars' and op.get('histogram_strength', 0) > 0
                      and op.get('boost_selection', {}).get('mode') == 'spatial'
                      for op in self.operations)
        self.scalar_role = ('selected_star_neutral_envelope' if spatial
                            else 'neutral_rgb_restriction')

    def apply_rgb(
        self, data: np.ndarray, *, ideal: bool = False, stellar_envelope: bool = False,
    ) -> np.ndarray:
        """Applique directement la recette à un RGB normalisé, sans optimisation.

        data est de forme (3, ...). ideal=True conserve les intermédiaires en
        float64 pour la courbe continue. Sinon les quantifications float32
        des étapes du moteur sont reproduites ; l'arrondi CUDA peut différer.
        Aucun histogramme, quantile ou point noir n'est réestimé. La sélection
        stellaire est figée par coordonnées et impose sa géométrie native.
        stellar_envelope=True réserve le poids 1 aux seules sondes de courbe :
        il représente l'enveloppe neutre des pics sélectionnés, pas une LUT RGB.
        En mode idéal, les intermédiaires neutres sont bornés à [0,1] :
        leur gamut peut différer de celui des pixels colorés de référence.
        """
        from lib.auto_ghs import ghs_rgb, linear_blackpoint, luminance, render_chroma

        output = np.asarray(data, dtype=np.float64 if ideal else np.float32).copy()
        if output.ndim < 2 or output.shape[0] != 3 or not output.size:
            raise ValueError('La transformation cumulée nécessite un tableau RGB non vide')
        if not np.isfinite(output).all() or output.min() < 0 or output.max() > 1:
            raise ValueError('Intensités RGB hors de [0,1]')
        for operation in self.operations:
            kind = operation['kind']
            if kind == 'blackpoint':
                output = linear_blackpoint(output, operation['value'])
            elif kind == 'ghs':
                work = output.astype(np.float64) if ideal or operation.get('float64') else output
                output = ghs_rgb(work, **operation['parameters'],
                                 color_mode=operation.get('color_mode', 'human'))
            elif kind == 'gain':
                output = output*operation['value']
            elif kind == 'chroma':
                output = render_chroma(output, operation['policy'])
            elif kind == 'cdf':
                light = luminance(output, operation.get('color_mode', 'human'))
                table = np.asarray(operation['table'], dtype=np.float64)
                if table.ndim != 1 or len(table) < 2 or not np.isfinite(table).all():
                    raise ValueError('Table CDF invalide')
                mapped = np.interp(light, np.linspace(0, 1, len(table)), table).astype(output.dtype)
                strength = operation['strength']
                target = (1-strength)*light+strength*mapped
                gain = np.minimum(target/np.maximum(light, 1e-30),
                                  1/np.maximum(output.max(axis=0), 1e-30))
                output = output*gain[None]
            elif kind == 'stars':
                light = output.max(axis=0)
                ceiling, strength = operation['ceiling'], operation['strength']
                boost = operation['faint_boost']
                target = ceiling*np.arcsinh(strength*light)/np.arcsinh(strength)
                target = boost*target/(1+(boost-1)*target/ceiling)
                fraction = operation.get('histogram_strength', 0.)
                knots = np.asarray(operation.get('knots', []), dtype=np.float64)
                levels = np.asarray(operation.get('levels', []), dtype=np.float64)
                if fraction and knots.size:
                    balanced = np.exp(np.interp(
                        np.log(np.maximum(light, np.finfo(np.float32).tiny)),
                        np.log(knots), np.log(levels),
                    ))
                    below = light < knots[0]
                    balanced[below] = light[below]*levels[0]/knots[0]
                    balanced = np.maximum(target, balanced).astype(output.dtype)
                    selection = operation.get('boost_selection', {'mode': 'all'})
                    weights = (1. if stellar_envelope and selection.get('mode') == 'spatial'
                               else stellar_selection_weights(selection, light.shape))
                    target = target+fraction*weights*(balanced-target)
                target = target * stellar_noise_gate(light, operation.get('noise_threshold', 0.))
                gain = np.divide(target, light, out=np.zeros_like(light), where=light > 0)
                output = output*gain[None]
            else:
                raise ValueError(f'Opération cumulée inconnue : {kind}')
            if ideal:
                # La rampe neutre peut dépasser le gamut des pixels colorés
                # ayant déterminé le gain ; borner avant le GHS suivant.
                output = np.clip(output, 0., 1.)
            else:
                output = output.astype(np.float32)
        return output

    def evaluate(self, levels: np.ndarray) -> np.ndarray:
        """Évalue la courbe neutre ; une sélection spatiale fournit son enveloppe à poids 1."""
        values = np.asarray(levels, dtype=np.float64)
        ramp = np.repeat(values.reshape(1, 1, -1), 3, axis=0)
        return self.apply_rgb(ramp, ideal=True, stellar_envelope=True)[0, 0].reshape(values.shape)

    @staticmethod
    def multiplicative_gain(levels: np.ndarray, output: np.ndarray) -> np.ndarray:
        """Calcule F(x)/x en float64 ; à x=0, le rapport indéfini est NaN.

        Les zéros de sortie pour x>0 restent des gains nuls. Aucun epsilon
        ni facteur arbitraire n'est substitué aux intensités initiales nulles.
        """
        return np.divide(output, levels, out=np.full_like(output, np.nan, dtype=np.float64),
                         where=np.asarray(levels) > 0)

    def curve(self) -> JSONReport:
        """Affine la courbe au seuil 1e-7, jusqu'à 262145 points au maximum.

        Les sondes aux quarts et au milieu contrôlent l'erreur d'interpolation
        observée, pas une borne analytique globale. Les limites atteintes sont
        signalées explicitement, avec 24 raffinements maximum. L'abscisse
        reste exactement le min/max source. Retourne aussi gain=F(x)/x,
        indéfini (NaN) à x=0 ; le seuil de précision porte sur F(x).
        scalar_role distingue la courbe neutre de l’enveloppe stellaire à poids 1.
        """
        low, high = self.domain
        if low == high:
            values = np.array([low])
            output = self.evaluate(values)
            return dict(x=values, y=output, gain=self.multiplicative_gain(values, output),
                        observed_error=0.,
                        precision_achieved=True, tolerance=CURVE_TOLERANCE,
                        scalar_role=self.scalar_role)
        x = np.linspace(low, high, INITIAL_CURVE_POINTS)
        y = self.evaluate(x)
        for refinement in range(MAX_CURVE_REFINEMENTS + 1):
            probes = x[:-1, None]+np.diff(x)[:, None]*np.array([.25, .5, .75])
            actual = self.evaluate(probes)
            predicted = y[:-1, None]+np.diff(y)[:, None]*np.array([.25, .5, .75])
            errors = np.max(abs(actual-predicted), axis=1)
            maximum = float(errors.max(initial=0.))
            failed = errors > CURVE_TOLERANCE
            if (not failed.any() or len(x)+3*int(failed.sum()) > MAX_CURVE_POINTS
                    or refinement == MAX_CURVE_REFINEMENTS):
                return dict(x=x, y=y, gain=self.multiplicative_gain(x, y), observed_error=maximum,
                            precision_achieved=not bool(failed.any()), tolerance=CURVE_TOLERANCE,
                            scalar_role=self.scalar_role)
            refined = np.unique(np.r_[x, probes[failed].ravel()])
            if len(refined) == len(x):
                return dict(x=x, y=y, gain=self.multiplicative_gain(x, y), observed_error=maximum,
                            precision_achieved=False, tolerance=CURVE_TOLERANCE,
                            scalar_role=self.scalar_role)
            x = refined
            y = self.evaluate(x)

    def export(self, destination: Path) -> tuple[JSONReport, JSONReport]:
        """Écrit la recette JSON et les échantillons float64 NPZ ; retourne courbe et liens."""
        curve = self.curve()
        recipe = destination.with_suffix('.json')
        samples = destination.with_suffix('.npz')
        summary = dict(domain=list(self.domain), scalar_role=curve['scalar_role'],
                       operations=self.operations, input_dtype='float32',
                       curve_dtype='float64', neutral_intermediate_clip=True,
                       tolerance=curve['tolerance'],
                       observed_interpolation_error=curve['observed_error'],
                       precision_achieved=curve['precision_achieved'], points=len(curve['x']),
                       plotted_quantity='output/input', gain_at_zero='undefined')
        recipe.write_text(json.dumps(summary, ensure_ascii=False, allow_nan=False, indent=2),
                          encoding='utf-8')
        np.savez_compressed(samples, input=curve['x'], output=curve['y'],
                            gain=curve['gain'])
        return curve, dict(transform_recipe=str(recipe), transform_samples=str(samples),
                           transform_curve_role=curve['scalar_role'],
                           transform_domain=list(self.domain), transform_points=len(curve['x']),
                           transform_observed_error=curve['observed_error'],
                           transform_precision_achieved=curve['precision_achieved'])
