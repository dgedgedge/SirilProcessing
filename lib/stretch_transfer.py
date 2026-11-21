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
from lib.stellar_profiles import profile_gain_map, apply_contrast_profiles

CURVE_TOLERANCE = 1e-7  # Erreur absolue observée aux sondes d'interpolation.
INITIAL_CURVE_POINTS = 2049
MAX_CURVE_POINTS = 262145
MAX_CURVE_REFINEMENTS = 24  # Évite de subdiviser au-delà de la résolution float64.
GHS_KEYS = ('D', 'B', 'SP', 'LP', 'HP')
MONOTONE_BRIGHT_PROTECTION = 'monotone_cap_v1'


def asinh_rgb(
    data: np.ndarray, strength: float, ceiling: float, faint_boost: float,
    color_mode: str = 'human',
) -> np.ndarray:
    """Applique un asinh lié à la luminance avec RGBBlend sous le plafond.

    ``human`` emploie le proxy Rec.709 déjà utilisé par GHS ; ``even`` emploie
    la moyenne RGB. Le gain lié préserve les rapports des canaux tant qu'aucun
    ne dépasse ``ceiling``. Dans ce cas seulement, RGBBlend mélange avec les
    trois courbes indépendantes, selon la même construction que ``ghs_rgb``.
    La balance SPCC/PCC doit donc être appliquée aux pixels avant cet appel.

    Parameters
    ----------
    data : np.ndarray
        RGB non négatif de forme ``(3, ...)``, normalement borné dans [0, 1].
    strength : float
        Force asinh strictement positive.
    ceiling : float
        Plafond RGB strictement compris entre zéro et un.
    faint_boost : float
        Renforcement rationnel des faibles intensités, au moins égal à un.
    color_mode : str, optional
        ``human`` ou ``even``.

    Returns
    -------
    np.ndarray
        RGB de même dtype, fini et borné dans ``[0, ceiling]``.
    """
    values = np.asarray(data)
    light = stellar_luminance(values, color_mode)

    def transfer(levels: np.ndarray) -> np.ndarray:
        """Évalue la courbe scalaire avec le dtype du tableau appelant."""
        target = ceiling * np.arcsinh(strength * levels) / np.arcsinh(strength)
        return faint_boost * target / (
            1 + (faint_boost - 1) * target / ceiling
        )

    linked_target = transfer(light)
    linked_gain = np.divide(
        linked_target, light, out=np.zeros_like(light), where=light > 0,
    )
    linked = values * linked_gain[None]
    independent = transfer(values)
    difference = linked - independent
    ratios = np.where(
        linked > ceiling,
        (ceiling - independent) / np.maximum(difference, 1e-30),
        1,
    )
    weight = np.clip(ratios.min(axis=0), 0, 1)
    result = weight[None] * linked + (1 - weight[None]) * independent
    result = np.where((values > 0) & (result == 0), values, result)
    return np.clip(result, 0, ceiling).astype(values.dtype, copy=False)


def stellar_luminance(data: np.ndarray, color_mode: str = 'human') -> np.ndarray:
    """Calcule la luminosité ``human`` ou ``even`` partagée avec le GHS.

    Les coefficients ``human`` sont le proxy Rec.709 appliqué aux pixels déjà
    équilibrés par SPCC/PCC. Ils ne remplacent pas une matrice colorimétrique.
    """
    if color_mode == 'human':
        return 0.2126 * data[0] + 0.7152 * data[1] + 0.0722 * data[2]
    if color_mode == 'even':
        return data.mean(axis=0)
    raise ValueError('Mode couleur stellaire inconnu')


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


def protected_stellar_target(
    light: np.ndarray, baseline: np.ndarray, knots: np.ndarray,
    levels: np.ndarray, bright_protection: float,
) -> np.ndarray:
    """Calibre les étoiles faibles sans inversion au raccord des noyaux protégés.

    Parameters
    ----------
    light, baseline : np.ndarray
        Intensités d'entrée et cibles de base, mêmes formes, finies dans [0,1].
    knots, levels : np.ndarray
        Points positifs et strictement croissants de la courbe log-log.
    bright_protection : float
        Niveau de présentation dans ]0,1] au-delà duquel la base reste intacte.

    Returns
    -------
    np.ndarray
        ``max(baseline, min(courbe_quantile, bright_protection))``, avec la
        base conservée sous le premier point. Le dtype est celui de baseline.
        Le plafond du supplément évite la chute créée par un remplacement
        brutal par la base lorsque celle-ci atteint le seuil de protection.
        Le maximum de deux courbes monotones conserve leur monotonie.

    Raises
    ------
    ValueError
        Formes, valeurs ou points de contrôle invalides. Aucune entrée n'est
        modifiée en place ; les points sont ceux de la recette, sans recalage.
    """
    if (light.shape != baseline.shape or light.size == 0
            or not np.isfinite(light).all() or not np.isfinite(baseline).all()
            or light.min() < 0 or light.max() > 1
            or baseline.min() < 0 or baseline.max() > 1
            or not np.isfinite(bright_protection) or not 0 < bright_protection <= 1):
        raise ValueError('Intensités ou protection stellaire invalides')
    if (knots.ndim != 1 or levels.shape != knots.shape or knots.size < 2
            or not np.isfinite(knots).all() or not np.isfinite(levels).all()
            or np.any(np.diff(knots) <= 0) or np.any(np.diff(levels) <= 0)
            or knots[0] <= 0 or knots[-1] > 1 or levels[0] <= 0 or levels[-1] > 1):
        raise ValueError('Points de la courbe stellaire invalides')
    mapped = np.exp(np.interp(
        np.log(np.maximum(light, np.finfo(np.float32).tiny)),
        np.log(knots), np.log(levels),
    ))
    mapped = np.maximum(baseline, np.minimum(mapped, bright_protection))
    return np.where(light <= knots[0], baseline, mapped).astype(baseline.dtype)


def stellar_operation(parameters: JSONReport, balance: JSONReport | None = None) -> JSONReport:
    """Décrit une courbe stellaire, ses quantiles et son seuil de bruit retenu.

    parameters et balance proviennent du rapport du moteur ; le seuil est nul
    pour les recettes historiques. Les tables sont copiées, sans redétection
    de pics ni nouvelle estimation du bruit lors de la réapplication. La sélection
    spatiale et ses coordonnées sont également copiées.
    """
    balance = balance or {}
    selection = deepcopy(parameters.get('boost_selection', {'mode': 'all'}))
    if selection.get('mode') == 'contrast_profiles':
        selection.pop('profile_checks', None)  # Audit sans effet sur le rejeu.
    return dict(kind='stars', strength=float(parameters['strength']),
                ceiling=float(parameters['ceiling']), faint_boost=float(parameters['faint_boost']),
                noise_threshold=float(
                    parameters.get('noise_suppression', {}).get('threshold', 0.)),
                histogram_strength=float(parameters.get('histogram_strength',
                    balance.get('accepted_strength', 0.))),
                color_mode=parameters.get('color_mode'),
                knots=deepcopy(balance.get('input_knots', [])),
                levels=deepcopy(balance.get('target_knots', [])),
                boost_selection=selection)


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
                      and op.get('boost_selection', {}).get('mode')
                      in ('spatial', 'profiles', 'contrast_profiles')
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
        stellar_envelope=True réserve aux sondes de courbe l'enveloppe neutre :
        poids 1 pour les anciennes sélections spatiales, plus grand supplément
        enregistré pour les profils individuels. Ce n'est pas une LUT RGB.
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
                color_mode = operation.get('color_mode')
                legacy = color_mode is None
                if legacy:
                    target = ceiling*np.arcsinh(strength*light)/np.arcsinh(strength)
                    target = boost*target/(1+(boost-1)*target/ceiling)
                    base = None
                else:
                    base = asinh_rgb(output, strength, ceiling, boost, color_mode)
                    target = base.max(axis=0)
                fraction = operation.get('histogram_strength', 0.)
                knots = np.asarray(operation.get('knots', []), dtype=np.float64)
                levels = np.asarray(operation.get('levels', []), dtype=np.float64)
                selection = operation.get('boost_selection', {'mode': 'all'})
                profile_mode = selection.get('mode') in ('profiles', 'contrast_profiles')
                contrast_profiles = selection.get('mode') == 'contrast_profiles'
                quantile_mode = selection.get('mode') == 'histogram_quantile'
                if quantile_mode and not legacy:
                    noise_light = output.max(axis=0)
                    base = base * stellar_noise_gate(
                        noise_light, operation.get('noise_threshold', 0.),
                    )[None]
                    target = base.max(axis=0)
                if fraction and knots.size and not profile_mode:
                    policy = selection.get('bright_protection_policy') if quantile_mode else None
                    if quantile_mode and policy == MONOTONE_BRIGHT_PROTECTION:
                        balanced = protected_stellar_target(
                            light, target, knots, levels,
                            selection.get('bright_protection', 1.),
                        )
                    else:
                        if quantile_mode and policy is not None:
                            raise ValueError('Politique de protection stellaire inconnue')
                        balanced = np.exp(np.interp(
                            np.log(np.maximum(light, np.finfo(np.float32).tiny)),
                            np.log(knots), np.log(levels),
                        ))
                        below = light < knots[0]
                        balanced[below] = light[below]*levels[0]/knots[0]
                        balanced = np.maximum(target, balanced).astype(output.dtype)
                        if quantile_mode:
                            # Les anciennes recettes conservent leur raccord
                            # historique pour reproduire les exports enregistrés.
                            balanced[light <= knots[0]] = target[light <= knots[0]]
                            protected = target >= selection.get('bright_protection', 1.)
                            balanced[protected] = target[protected]
                    if quantile_mode:
                        weights = 1.
                    else:
                        weights = (1. if stellar_envelope and selection.get('mode') == 'spatial'
                                   else stellar_selection_weights(selection, light.shape))
                    target = target+fraction*weights*(balanced-target)
                    if not legacy:
                        base_light = base.max(axis=0)
                        gain = np.divide(
                            target, base_light, out=np.zeros_like(target),
                            where=base_light > 0,
                        )
                        base = base * gain[None]
                if legacy:
                    target = target * stellar_noise_gate(
                        light, operation.get('noise_threshold', 0.),
                    )
                    if fraction and profile_mode:
                        if stellar_envelope:
                            extra = max(
                                (p[4] for p in selection.get('profiles', [])),
                                default=1.,
                            )
                            target = np.minimum(
                                target * (1 + fraction * (extra - 1)), ceiling,
                            )
                        else:
                            gains = profile_gain_map(selection, light.shape)
                            target = target * (1 + fraction * (gains - 1))
                    gain = np.divide(
                        target, light, out=np.zeros_like(light), where=light > 0,
                    )
                    output = output * gain[None]
                else:
                    # La recette reproduit la porte du moteur, calée sur le
                    # maximum RGB afin de ne pas sous-estimer un résidu isolé
                    # dans un seul canal.
                    if not quantile_mode:
                        noise_light = output.max(axis=0)
                        base = base * stellar_noise_gate(
                            noise_light, operation.get('noise_threshold', 0.),
                        )[None]
                    if fraction and profile_mode:
                        if stellar_envelope:
                            extra = max(
                                (p[4] for p in selection.get('profiles', [])),
                                default=1.,
                            )
                            if contrast_profiles:
                                floor = min(
                                    (p[5] for p in selection.get('profiles', [])), default=0.,
                                )
                                level = base.max(axis=0)
                                target = np.minimum(
                                    level + fraction*(extra-1)*np.maximum(level-floor, 0), ceiling,
                                )
                                factor = np.divide(
                                    target, level, out=np.ones_like(level), where=level > 0,
                                )
                                base = base*factor[None]
                            else:
                                base = np.minimum(
                                    base * (1 + fraction * (extra - 1)), ceiling,
                                )
                        elif contrast_profiles:
                            base = apply_contrast_profiles(base, selection, fraction)
                        else:
                            gains = profile_gain_map(selection, light.shape)
                            base = base * (1 + fraction * (gains - 1))[None]
                    output = base
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
        """Évalue la courbe neutre ou l’enveloppe maximale des suppléments stellaires."""
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
        scalar_role distingue la courbe neutre de l’enveloppe maximale stellaire.
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


class TrialTransformResolver:
    """Reconstruit chaque chemin depuis l'entrée de branche, y compris ses BP."""

    def __init__(self, report: JSONReport) -> None:
        """Indexe les étapes retenues et les parents des essais de la recherche."""
        self.report = report
        self.mode = report.get('color_mode', 'human')
        self.range_stages: dict[str, list[JSONReport]] = {}
        operations: list[JSONReport] = []
        for step in report.get('iterations', []):
            operations = self.blackpoint(operations, step.get('BP', 0.))
            accepted = step.get('accepted')
            if accepted is not None:
                operations = self.ghs_step(operations, accepted, float64=False, pre=False)
                self.range_stages[f"RANGE — passe {step['iteration']}"] = operations
        self.balance_entry = operations
        balance = report.get('histogram_balance', {})
        if balance.get('status') == 'applied':
            operations = self.cdf_step(balance)
        self.balance_output = operations
        for step in report.get('dynamic_range', {}).get('iterations', []):
            operations = self.blackpoint(operations, step.get('BP', 0.))
            operations = [*operations, dict(kind='gain', value=step['gain'])]
        self.dynamic_output = operations
        chroma = report.get('chroma', 'off')
        if chroma != 'off':
            operations = [*operations, dict(kind='chroma', policy=chroma)]
        self.presentation_entry = operations
        presentation = report.get('presentation_stretch', {})
        if presentation.get('status') == 'applied':
            operations = self.ghs_step(operations, presentation, float64=False, pre=False)
        self.search_entry = operations
        self.search_reports = {name: item.get('report', {}) for name, item in
                               report.get('quartile_stretch', {}).get('searches', {}).items()}
        self.parents: dict[str, dict[str, JSONReport]] = {}
        for name, search in self.search_reports.items():
            self.parents[name] = {item['branch_id']: item for iteration in search.get('iterations', [])
                                  for item in iteration.get('verified_candidates', [])
                                  if item.get('branch_id')}

    @staticmethod
    def blackpoint(operations: list[JSONReport], value: float) -> list[JSONReport]:
        """Ajoute un recalage non nul sans modifier le chemin parent."""
        return [*operations, dict(kind='blackpoint', value=float(value))] if value else operations

    def ghs_step(
        self, operations: list[JSONReport], step: JSONReport, *, float64: bool, pre: bool = True,
    ) -> list[JSONReport]:
        """Compose BP initial, GHS et BP final dans l'ordre réellement appliqué."""
        parameters = step.get('parameters', step)
        if pre:
            operations = self.blackpoint(operations, step.get('BP', 0.))
        operations = [*operations, dict(kind='ghs', parameters={key: parameters[key] for key in GHS_KEYS},
                                        color_mode=self.mode, float64=float64)]
        return self.blackpoint(operations, step.get('post_BP', 0.))

    def cdf_step(self, step: JSONReport) -> list[JSONReport]:
        """Compose une CDF fixe depuis son entrée, pas depuis l'essai précédent."""
        balance = self.report.get('histogram_balance', {})
        table = balance.get('transfer_cdf')
        if table is None:
            raise ValueError('Table CDF manquante pour la transformation cumulée')
        strength = step.get('parameters', {}).get('strength', step.get('strength'))
        operations = [*self.balance_entry, dict(kind='cdf', table=table,
                                               strength=strength, color_mode=self.mode)]
        return self.blackpoint(operations, step.get('post_BP', 0.))

    def branch(self, name: str, identifier: str) -> list[JSONReport]:
        """Remonte les parents d'une branche ; un parent inconnu est une erreur."""
        steps = []
        seen = set()
        while identifier != 'root':
            if identifier in seen:
                raise ValueError('Cycle dans le chemin de transformation')
            seen.add(identifier)
            item = self.parents[name][identifier]
            steps.append(item)
            identifier = item.get('parent_branch_id', 'root')
        operations = self.search_entry
        for item in reversed(steps):
            operations = self.ghs_step(operations, item, float64=True)
        return operations

    def final(self) -> list[JSONReport]:
        """Retourne le chemin choisi, ou l'entrée de recherche si elle est conservée."""
        quartiles = self.report.get('quartile_stretch', {})
        name = quartiles.get('selected_search')
        search = self.search_reports.get(name, quartiles)
        operations = self.search_entry
        if name == 'unchanged' or search.get('status') != 'applied':
            return operations
        steps = search.get('selected_path')
        if steps is None:
            steps = [item['accepted'] for item in search.get('iterations', []) if item.get('accepted')]
        for step in steps:
            operations = self.ghs_step(operations, step, float64=True)
        return operations

    def for_trial(self, trial: JSONReport) -> list[JSONReport]:
        """Résout l'identité, une étape, un finaliste ou un enfant admissible/rejeté."""
        if self.report.get('diagnostic_subject') == 'stars':
            if trial['stage'] == 'Étoiles retenues':
                return self.report.get('histogram_stages', [])[-1]['transform_operations']
            return trial['transform_operations']
        stage = trial['stage']
        if stage == 'Entrée linéaire':
            return []
        if stage in self.range_stages:
            return self.range_stages[stage]
        if stage.startswith('Égalisation — essai'):
            return self.cdf_step(trial)
        if stage == 'Égalisation partielle':
            return self.balance_output
        if stage == 'Expansion de dynamique':
            return self.dynamic_output
        if stage.startswith('Chroma '):
            return self.presentation_entry
        if stage.startswith('Présentation — finaliste'):
            return self.ghs_step(self.presentation_entry, trial, float64=False, pre=False)
        if stage == 'Présentation protégée':
            return self.search_entry
        if stage == 'Fond retenu':
            return self.final()
        name = trial.get('search_name')
        if name:
            parent = trial.get('parent_branch_id')
            if parent is not None:
                operations = self.branch(name, parent)
            else:
                operations = self.search_entry
                for item in self.search_reports[name].get('iterations', []):
                    if item['iteration'] >= trial['iteration']:
                        break
                    if item.get('accepted'):
                        operations = self.ghs_step(operations, item['accepted'], float64=True)
            return self.ghs_step(operations, trial, float64=True)
        raise ValueError(f'Étape sans transformation cumulée : {stage}')
