"""Renforcement local à gain constant, avec raccord hors des ailes stellaires.

Les profils sont estimés uniquement sur la couche stellaire. Les sources
superposées reçoivent un gain réduit sous garde de largeur ; les profils
tronqués restent à la base. Aucune séparation PSF n'est faite.
Voir docs/scripts/postProcess/stellar-selection.md.
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import distance_transform_edt, map_coordinates
from scipy.spatial import cKDTree

from lib.type_defs import JSONReport

PROFILE_RAYS = 16
PROFILE_LIMIT_PX = 256  # Au-delà, conserver la base plutôt que tronquer un halo.
PROFILE_STEP_PX = 0.5
NEIGHBOUR_RADIUS_FRACTION = .49  # Supports disjoints, même pour deux pics proches.
MIN_TAPER_RATIO = 1.1  # Réserver au moins 10 % au raccord radial.
CROWDED_CORE_FRACTION = .55  # Raccord plus large si les ailes se confondent.
SAMPLED_PROFILE_RATIO = .92  # Marge de 0,02 pour l'interpolation des rayons.
MIN_PROFILE_RATIO = .9  # Rétrécissement maximal du profil normalisé : 10 %.
MAX_PROFILE_REDUCTIONS = 24  # Divisions par deux du supplément individuel.
BRIGHT_PEAK = 0.15  # Maximum RGB de la base ; protéger les étoiles déjà brillantes.
INTERPOLATION_MARGIN_PX = 1.5  # Support des voisins de l’interpolation bilinéaire.
WING_LEVEL = 0.01  # Étendue du profil, relative au sommet local.


def _radial_coordinates(y: int, x: int, radii: np.ndarray) -> np.ndarray:
    """Retourne les coordonnées (y,x) des mêmes 16 rayons pour tout échantillonnage."""
    angles = np.arange(PROFILE_RAYS) * (2 * np.pi / PROFILE_RAYS)
    yy = y + np.sin(angles)[:, None] * radii
    xx = x + np.cos(angles)[:, None] * radii
    return np.array([yy, xx])


def radial_samples(light: np.ndarray, y: int, x: int) -> tuple[np.ndarray, np.ndarray]:
    """Échantillonne 16 rayons à pas de 0,5 pixel jusqu'au bord ou à 256 pixels.

    Parameters
    ----------
    light : np.ndarray
        Maximum RGB 2D normalisé ; aucune modification en place.
    y, x : int
        Coordonnées natives du sommet.

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        Rayons en pixels et intensités (16, nombre de rayons). La limite
        est commune aux directions : un profil tronqué n'est pas extrapolé.
    """
    limit = min(PROFILE_LIMIT_PX, y, x, light.shape[0] - 1 - y,
                light.shape[1] - 1 - x)
    radii = np.arange(0, limit + PROFILE_STEP_PX / 2, PROFILE_STEP_PX)
    coordinates = _radial_coordinates(y, x, radii)
    return radii, map_coordinates(light, coordinates, order=1, mode='nearest')


def crossing_radii(
    radii: np.ndarray, samples: np.ndarray, level: float,
) -> np.ndarray | None:
    """Interpole la première traversée d'un niveau relatif au sommet sur chaque rayon.

    Renvoie None si un rayon ne traverse pas le niveau : bruit, voisinage,
    plateau et bord ne sont pas convertis en largeur nulle. Les niveaux 0,5,
    0,1 et 0,01 servent aux gardes de profil, en pixels depuis le sommet.
    """
    if radii.size < 3 or samples[0, 0] <= 0:
        return None
    below = samples <= samples[:, :1] * level
    if not below.any(axis=1).all():
        return None
    indices = below.argmax(axis=1)
    rows = np.arange(samples.shape[0])
    previous = np.maximum(indices - 1, 0)
    high, low = samples[rows, previous], samples[rows, indices]
    fraction = (high - samples[:, 0] * level) / np.maximum(high - low, 1e-30)
    return radii[previous] + fraction * (radii[indices] - radii[previous])


def profile_gain_map(selection: JSONReport, shape: tuple[int, ...]) -> np.ndarray:
    """Rejoue les gains enregistrés, sans analyser l'image ni ses voisins.

    Chaque profil [y,x,rayon_constant,rayon_externe,gain] applique un supplément
    constant dans le cœur mesuré, puis un raccord cosinus à pente nulle.
    En champ dense le raccord peut commencer dans les ailes, sous garde de profil.
    Les supports ne doivent pas se chevaucher ; leur maximum évite d'additionner
    les gains. Une géométrie ou un profil non fini/hors domaine lève ValueError.
    """
    if len(shape) != 2 or list(shape) != selection.get('shape'):
        raise ValueError('Géométrie de profils stellaires incompatible')
    result = np.ones(shape, dtype=np.float32)
    seen: set[tuple[int, int]] = set()
    for entry in selection.get('profiles', []):
        if len(entry) != 5 or not np.isfinite(entry).all():
            raise ValueError('Profil stellaire invalide')
        y, x, core, outer, gain = entry
        if (int(y) != y or int(x) != x or not 0 <= y < shape[0]
                or not 0 <= x < shape[1] or not 0 < core < outer <= PROFILE_LIMIT_PX
                or not 1 <= gain <= np.finfo(np.float32).max
                or (int(y), int(x)) in seen):
            raise ValueError('Profil stellaire hors domaine ou dupliqué')
        y, x = int(y), int(x)
        seen.add((y, x))
        radius = int(np.ceil(outer))
        y0, y1 = max(0, y - radius), min(shape[0], y + radius + 1)
        x0, x1 = max(0, x - radius), min(shape[1], x + radius + 1)
        yy, xx = np.ogrid[y0:y1, x0:x1]
        distance = np.hypot(yy - y, xx - x)
        transition = np.clip((distance - core) / (outer - core), 0, 1)
        envelope = (1 + np.cos(np.pi * transition)) / 2
        patch = (1 + (gain - 1) * envelope).astype(np.float32)
        np.maximum(result[y0:y1, x0:x1], patch, out=result[y0:y1, x0:x1])
    return result


class StellarProfileBoost:
    """Construit des gains individuels à profils conservés, sans fond ni contexte."""

    @staticmethod
    def build(
        source: np.ndarray, baseline: np.ndarray, selection: JSONReport,
        desired: np.ndarray, max_width_ratio: float,
    ) -> JSONReport:
        """Mesure les profils sélectionnés et enregistre les suppléments admissibles.

        Parameters
        ----------
        source, baseline : np.ndarray
            Maximum RGB linéaire et de la base, de même géométrie 2D.
        selection : dict
            Budget et coordonnées de la sélection, laissés intacts.
        desired : np.ndarray
            Niveaux supplémentaires visés aux seules positions sélectionnées.
        max_width_ratio : float
            Rapport maximal des rayons base/source, à 50 % et 10 % du sommet.

        Returns
        -------
        dict
            Copie de sélection en mode profiles, supports/gains et diagnostics.
            Les étoiles brillantes ou hors garde restent à la base. Pour les
            profils isolés, le gain est constant jusqu'aux ailes à 10 %. Pour
            les voisinages proches, le raccord est rapproché et le gain est
            réduit individuellement pour limiter la déformation à 50, 10 et 1 %.
        """
        from copy import deepcopy

        plan = deepcopy(selection)
        plan.update(mode='profiles', profiles=[], profile_checks=[])
        points = np.asarray(selection['selected'] + selection['others'], dtype=int)
        tree = cKDTree(points)
        protected = np.zeros(source.shape, dtype=bool)
        # Protéger aussi le halo des étoiles brillantes non sélectionnées.
        for y, x in points:
            if baseline[y, x] < BRIGHT_PEAK:
                continue
            radii, samples = radial_samples(baseline, int(y), int(x))
            crossings = crossing_radii(radii, samples, WING_LEVEL)
            extent = (float(crossings.max()) if crossings is not None
                      else float(radii[-1]))
            extent = max(extent + INTERPOLATION_MARGIN_PX, 1.)
            radius = int(np.ceil(extent))
            y0, y1 = max(0, y-radius), min(source.shape[0], y+radius+1)
            x0, x1 = max(0, x-radius), min(source.shape[1], x+radius+1)
            yy, xx = np.ogrid[y0:y1, x0:x1]
            protected[y0:y1, x0:x1] |= (yy-y)**2 + (xx-x)**2 <= extent**2
        protected_distance = (
            distance_transform_edt(~protected).astype(np.float32)
            if protected.any() else None
        )
        for (y, x), target in zip(selection['selected'], desired):
            peak = float(baseline[y, x])
            check: JSONReport = dict(y=y, x=x, accepted=False)
            plan['profile_checks'].append(check)
            if peak <= 0 or peak >= BRIGHT_PEAK or protected[y, x]:
                check['reason'] = 'bright star or protected halo'
                continue
            radii, samples = radial_samples(baseline, y, x)
            outer = crossing_radii(radii, samples, WING_LEVEL)
            core = crossing_radii(radii, samples, .1)
            if outer is None or core is None:
                check['reason'] = 'truncated or ambiguous wings'
                continue
            distance = float(tree.query([y, x], k=2)[0][1])
            support = min(float(outer.max()) + INTERPOLATION_MARGIN_PX,
                          NEIGHBOUR_RADIUS_FRACTION * distance, PROFILE_LIMIT_PX)
            if protected_distance is not None:
                support = min(support, .95 * float(protected_distance[y, x]))
            if support <= INTERPOLATION_MARGIN_PX:
                check['reason'] = 'no resolved support'
                continue
            constant = float(core.max()) + INTERPOLATION_MARGIN_PX
            crowded = support <= MIN_TAPER_RATIO * constant
            if crowded:
                constant = CROWDED_CORE_FRACTION * support
            radius = int(np.ceil(support))
            y0, y1 = max(0, y-radius), min(source.shape[0], y+radius+1)
            x0, x1 = max(0, x-radius), min(source.shape[1], x+radius+1)
            yy, xx = np.ogrid[y0:y1, x0:x1]
            mask = (yy-y)**2 + (xx-x)**2 < support**2
            _, original = radial_samples(source, y, x)
            ratios = []
            lower_ratios = []
            original_widths = []
            baseline_widths = []
            for level in (.5, .1):
                before = crossing_radii(radii, original, level)
                after = crossing_radii(radii, samples, level)
                if before is None or after is None:
                    break
                original_widths.append(before)
                baseline_widths.append(after)
                relative = after / np.maximum(before, 1e-12)
                ratios.append(float(np.max(relative)))
                lower_ratios.append(float(np.min(relative)))
            check['width_ratios'] = ratios
            check['minimum_width_ratios'] = lower_ratios
            if (len(ratios) != 2 or max(ratios) > max_width_ratio
                    or min(lower_ratios) < MIN_PROFILE_RATIO):
                check['reason'] = 'individual width limit or unmeasurable profile'
                continue
            local_peak = float(baseline[y0:y1, x0:x1][mask].max())
            # Plafond doux : préserver l'ordre des niveaux visés, sans ramener
            # tous les pics renforcés à la même valeur de 0,15.
            soft_target = BRIGHT_PEAK * float(target) / (BRIGHT_PEAK + float(target))
            gain = max(1., min(soft_target / peak, BRIGHT_PEAK / local_peak))
            if gain <= 1:
                check['reason'] = 'no additional gain needed'
                continue
            # Vérifier également le halo à 1 % sur la fenêtre adaptative.
            # Le raccord peut le rétrécir : réduire le gain de cette seule étoile.
            distance_grid = np.hypot(yy-y, xx-x)
            transition = np.clip((distance_grid-constant) / (support-constant), 0, 1)
            envelope = (1 + np.cos(np.pi * transition)) / 2
            increment = baseline[y0:y1, x0:x1] * envelope
            coordinates = _radial_coordinates(y-y0, x-x0, radii)
            sampled_increment = map_coordinates(
                increment, coordinates, order=1, mode='constant', cval=0.,
            )
            requested_gain = gain
            halo_ratio = None
            for attempt in range(MAX_PROFILE_REDUCTIONS + 1):
                trial = samples + (gain - 1) * sampled_increment
                halo = crossing_radii(radii, trial, WING_LEVEL)
                if halo is not None:
                    halo_ratio = float(np.min(halo / outer))
                    profile_ok = halo_ratio >= SAMPLED_PROFILE_RATIO
                    final_ratios = []
                    final_minimum_ratios = []
                    for level, before, base_width in zip(
                        (.5, .1), original_widths, baseline_widths,
                    ):
                        after = crossing_radii(radii, trial, level)
                        if after is None:
                            profile_ok = False
                            break
                        relative = after / np.maximum(before, 1e-12)
                        final_ratios.append(float(relative.max()))
                        final_minimum_ratios.append(float(relative.min()))
                        profile_ok &= (
                            float(np.min(after / base_width)) >= SAMPLED_PROFILE_RATIO
                            and relative.min() >= MIN_PROFILE_RATIO
                            and relative.max() <= max_width_ratio
                        )
                    if profile_ok:
                        break
                gain = 1 + (gain - 1) / 2
            else:
                check['reason'] = 'individual profile contraction limit'
                continue
            check.update(
                accepted=True,
                reason=('crowded profile with reduced gain' if crowded
                        else 'constant profile gain'),
                width_ratios=final_ratios, minimum_width_ratios=final_minimum_ratios,
                constant_radius_px=constant, outer_radius_px=support, gain=gain,
                requested_gain=requested_gain, halo_minimum_ratio=halo_ratio,
                gain_reductions=attempt,
            )
            plan['profiles'].append([y, x, constant, support, gain])
        plan['profile_method'] = (
            '16 radial rays; individual gain; guarded cosine taper; '
            'crowded fields supported'
        )
        plan['bright_peak_threshold'] = BRIGHT_PEAK
        return plan
