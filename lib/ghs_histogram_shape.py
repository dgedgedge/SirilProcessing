"""Mesure la forme semi-log lissée et sa régularité par morceaux.

Les splines servent uniquement aux mesures, jamais à transformer les pixels.
Les classes rares sont regroupées sans inventer d'effectifs ; une lacune de
quatre classes sans trois pixels termine le support. La référence reste fixe
pendant la recherche. Voir docs/scripts/postProcess/ghs-histogram-shape.md.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from functools import lru_cache

import numpy as np

from lib.ghs_linear_histogram import linear_histogram_profile
from lib.ghs_log_histogram import HISTOGRAM_BINS, MIN_BIN_COUNT
from lib.type_defs import JSONReport
from lib.ghs_render_quality import HistogramRenderQuality, QUALITY_SELECTION_POLICY

DEFAULT_SMOOTHING = 1e-4
DEFAULT_PEAK_SHIFT = .25
DEFAULT_RATIO_TOLERANCE = .5
DEFAULT_SHOULDER_ADVANCE = .15
MAX_SHOULDER_RELATIVE_POSITION = .6
SHOULDER_FIT_TOLERANCE = .02  # Résidu en décroissance normalisée, pas en dex.
SHOULDER_MIN_FLATTENING = .15  # Réduction relative de pente définissant un épaulement.
POST_SHOULDER_REGIONS = 5
SLOPE_RELATIVE_EPSILON = 1e-6
SLOPE_ORDER_TOLERANCE = .1  # Écart direct minimal : 10 % de la plus forte pente voisine.
RATIO_EXCESS_WEIGHT = 10.
RATIO_INVERSION_PENALTY = 8.
TERMINAL_RATIO_WEIGHT = .2  # Raidissement de queue moins influent que les raccords internes.
# Une pente faible reste comparée avec ce plancher, en chute globale/support.
SLOPE_SCALE_FLOOR = .2
GROUP_POPULATION = 32
MAX_GROUP_BINS = 4
GRID_POINTS = 61
SLOPE_REGIONS = 6
MIN_PIECE_POINTS = 5
MAX_PIECES = 6
# Variation de pente, après normalisation horizontale et verticale.
SLOPE_CHANGE_THRESHOLD = .3
MIN_SHAPE_DROP_DEX = .2
# Fenêtre sombre commune et population minimale pour une mesure de modes fiable.
LOW_LIGHT_WINDOW_END = .25
MIN_LOW_LIGHT_POPULATION = 32
# Le pic garde sa propre enveloppe locale : la spline du corps peut gommer
# un second mode. Arrêt dès que les effectifs tombent sous 10 % du maximum.
INITIAL_PEAK_FRACTION = .1
PIECEWISE_SELECTION_POLICY = QUALITY_SELECTION_POLICY


def histogram_selection_key(profile: JSONReport) -> tuple[bool, int, float, float]:
    """Classe la qualité mesurée ; les anciens profils gardent leur ordre historique.

    Parameters
    ----------
    profile : JSONReport
        Profil complet après BP, avec audits de forme, de niveau à 0,8 et de
        queue à 0,875. Les gardes spatiales restent vérifiées par le moteur.

    Returns
    -------
    tuple of bool, int, float, float
        Avec render_quality : sécurité et mesurabilité, zéro, score de qualité,
        puis déplacement du pic en dernier départage. Les objectifs lumineux
        ne priment pas sur une perte de qualité. Sans cette mesure enregistrée,
        le contrat historique reste disponible pour lire d'anciens rapports.
        Ordre historique : objectifs tous atteints, nombre atteint, priorité et départage.
        Lorsque tout est atteint, priorité au plus petit déplacement absolu
        depuis la référence fixe, puis au score. Sinon le score de progression
        guide la recherche, et le déplacement départage les scores identiques.
        Un déplacement inconnu ne reçoit pas le bénéfice d'un déplacement nul.
    """
    shape = profile.get('shape_preservation', {})
    floor, tail = profile.get('regression_floor', {}), profile.get('tail', {})
    structural = bool(profile.get('measurable') and shape.get('achieved') is True)
    peak = profile.get('peak')
    peak_met = peak is not None and peak < .25
    floor_met = floor.get('minimum_count') == 0 or floor.get('achieved') is True
    tail_met = tail.get('target') == 0 or tail.get('achieved') is True
    count = int(structural)+int(peak_met)+int(floor_met)+int(tail_met)
    shift = shape.get('peak_shift')
    distance = float(shift) if shift is not None and np.isfinite(shift) else float('inf')
    quality = profile.get('render_quality')
    if quality is not None:
        score = quality.get('score')
        safe = bool(profile.get('measurable') and shape.get('measurable') is True
                    and peak_met and not HistogramRenderQuality.hard_shape_rejections(profile)
                    and quality.get('available') and score is not None and np.isfinite(score))
        return safe, 0, float(score) if safe else -float('inf'), -distance
    score = float(profile['score'])
    return ((True, count, -distance, score) if count == 4 else
            (False, count, score, -distance))


def _pieces(x: np.ndarray, y: np.ndarray, tolerance: float) -> list[JSONReport]:
    """Couvre le profil par au plus six droites de RMS <= tolerance dex.

    Une programmation dynamique minimise d'abord le nombre de morceaux, puis
    leur erreur quadratique. Chaque morceau comporte au moins cinq points ;
    les voisins partagent leur extrémité. Aucun morceau ne saute de données.
    """
    size = len(x)
    left, right = np.triu_indices(size, k=MIN_PIECE_POINTS-1)
    length = right-left+1
    def sums(values: np.ndarray) -> np.ndarray:
        """Somme tous les intervalles par différences de préfixes."""
        prefix = np.r_[0., np.cumsum(values)]
        return prefix[right+1]-prefix[left]
    sx, sy, sxx, sxy, syy = (sums(v) for v in (x, y, x*x, x*y, y*y))
    centered = sxx-sx*sx/length
    slopes = (sxy-sx*sy/length)/centered
    intercepts = (sy-slopes*sx)/length
    errors = np.maximum(0., syy-sy*sy/length-slopes**2*centered)
    rms = np.sqrt(errors/length)
    eligible = np.flatnonzero(rms <= tolerance)
    mask = ((np.arange(size)[None] >= left[eligible, None])
            & (np.arange(size)[None] <= right[eligible, None]))
    residuals = abs(y[None]-intercepts[eligible, None]-slopes[eligible, None]*x[None])
    maximum = np.max(np.where(mask, residuals, 0), axis=1)
    eligible = eligible[maximum <= 3*tolerance]
    paths: dict[int, tuple[float, list[JSONReport]]] = {0: (0.0, [])}
    for end in range(MIN_PIECE_POINTS-1, size):
        best = None
        for index in eligible[right[eligible] == end]:
            start = int(left[index])
            if start not in paths or len(paths[start][1]) >= MAX_PIECES:
                continue
            error = paths[start][0]+float(errors[index])
            piece = dict(start=float(x[start]), end=float(x[end]),
                         slope=float(slopes[index]), intercept=float(intercepts[index]),
                         rms_dex=float(rms[index]))
            candidate = (error, paths[start][1]+[piece])
            if best is None or (len(candidate[1]), error) < (len(best[1]), best[0]):
                best = candidate
        if best is not None:
            paths[end] = best
    return paths.get(size-1, (0.0, []))[1]


def low_light_mode_profile(counts: np.ndarray) -> JSONReport:
    """Mesure le creux séparant deux modes dans le premier quart d'intensité.

    Parameters
    ----------
    counts : np.ndarray
        128 effectifs finis non négatifs, sur [0,1], réels ou pondérés.

    Returns
    -------
    JSONReport
        Profondeur maximale d'un creux bordé par deux populations, divisée
        par le sommet de la fenêtre [0,0.25[. Les effectifs sont seulement
        lissés par [1,2,1]/4 ; aucun arrêt au premier creux ni spline.
        Une fenêtre de moins de 32 pixels est non mesurable, jamais unimodale
        par convention. Les positions sont des centres de classes normalisés.

    Raises
    ------
    ValueError
        Forme, effectifs négatifs ou valeurs non finies invalides.

    Notes
    -----
    Pour chaque classe i, le creux vaut
    min(max(effectifs à gauche), max(effectifs à droite)) - effectif[i].
    La normalisation rend le test indépendant de la population et du choix
    du pic dominant. Ce diagnostic ne transforme pas les pixels. Voir
    docs/scripts/postProcess/ghs-histogram-shape.md.
    """
    values = np.asarray(counts, dtype=np.float64)
    if (values.shape != (HISTOGRAM_BINS,) or not np.isfinite(values).all()
            or np.any(values < 0)):
        raise ValueError('Effectifs invalides pour les modes sombres GHS')
    stop = int(HISTOGRAM_BINS * LOW_LIGHT_WINDOW_END)
    report: JSONReport = dict(
        method='local_smoothed_saddle', window=[0., LOW_LIGHT_WINDOW_END], measurable=False,
        prominence_fraction=None, valley=None, bins=stop,
        minimum_population=MIN_LOW_LIGHT_POPULATION,
    )
    if values[:stop].sum() < MIN_LOW_LIGHT_POPULATION:
        return report
    smooth = np.convolve(values, [.25, .5, .25], mode='same')[:stop]
    left = np.maximum.accumulate(smooth)
    right = np.maximum.accumulate(smooth[::-1])[::-1]
    depths = np.maximum(0., np.minimum(left, right)-smooth)
    valley = int(np.argmax(depths))
    report.update(
        measurable=True, prominence_fraction=float(depths[valley]/smooth.max()),
        valley=(valley+.5)/HISTOGRAM_BINS if depths[valley] > 0 else None,
    )
    return report


def piecewise_histogram_profile(
    counts: np.ndarray, tolerance: float = .05,
    smoothing: float = DEFAULT_SMOOTHING,
) -> JSONReport:
    """Lisse log10(densité) puis mesure les morceaux et changements de pente.

    Parameters
    ----------
    counts : numpy.ndarray
        128 effectifs réels ou estimés par poids de population, sur [0,1].
    tolerance : float
        RMS maximal en dex de chaque morceau sur l'enveloppe lissée.
    smoothing : float
        Régularisation de la spline cubique sur l'intensité [0,1], commune à
        tous les candidats. Les poids n/(n+16) bornent l'influence du pic.

    Returns
    -------
    JSONReport
        Profil, points lissés, morceaux, pentes normalisées, séquence et chute
        en dex. reference_log_density ne vaut un nombre que si 0,8 appartient
        au support observé des nœuds ; aucune extrapolation n'est certifiée.
        Les noms line_start/end/span sont conservés pour les lecteurs existants
        mais décrivent ici le support de l'ensemble des morceaux.
        prepeak_valley_dex mesure la baisse maximale d'effectifs lissés avant
        le pic dominant, pour détecter un nouveau mode masquant ce creux.
        initial_peak mesure la remontée locale en fraction du sommet, avant
        la première classe sous 10 % de celui-ci, indépendamment de la spline.
        low_light_modes couvre tout le premier quart, même après un creux profond.
    """
    if not np.isfinite(smoothing) or not 1e-8 <= smoothing <= .1:
        raise ValueError('ghs-shape-smoothing doit être dans [1e-8,0.1]')
    legacy = linear_histogram_profile(counts, tolerance)
    result = dict(legacy)
    result.update(model='piecewise', objective='piecewise_log_shape',
                  legacy_line=legacy, measurable=False, line_start=None,
                  line_end=None, line_span=0.0, slope=None, intercept=None,
                  pieces=[], shape=None, reference_covered=False,
                  reference_log_density=None, prepeak_valley_dex=None,
                  initial_peak=None, low_light_modes=low_light_mode_profile(counts),
                  score=-1.0, loss=None)
    if legacy['peak'] is None or not legacy['support']:
        return result
    values = np.asarray(counts, dtype=np.float64)
    support = float(values.sum())
    # Une spline admet l'épaulement courbe : commencer juste après le pic,
    # plutôt que le seuil à 2 % réservé à la comparaison de droite unique.
    start = int(round(legacy['peak']*HISTOGRAM_BINS-.5))+1
    # Un changement de pic dominant ne doit pas masquer un creux nouveau à
    # sa gauche. Les effectifs positifs lissés gardent leur échelle réelle.
    local_counts = np.convolve(values, [.25, .5, .25], mode='same')
    left = local_counts[:start]
    left = np.log10(left[left > 0])
    result['prepeak_valley_dex'] = float(np.max(np.maximum.accumulate(left)-left))
    mode = start-1
    peak_height = local_counts[mode]
    below = np.flatnonzero(local_counts[start:] < INITIAL_PEAK_FRACTION*peak_height)
    peak_end = start+int(below[0]) if below.size else HISTOGRAM_BINS
    peak_values = local_counts[mode:peak_end]
    result['initial_peak'] = dict(
        method='local_smoothed_counts', start=(mode+.5)/HISTOGRAM_BINS,
        end=peak_end/HISTOGRAM_BINS, minimum_peak_fraction=INITIAL_PEAK_FRACTION,
        secondary_rise_fraction=float(
            np.max(peak_values-np.minimum.accumulate(peak_values))/peak_height,
        ),
    )
    result.update(peak_exit=start/HISTOGRAM_BINS, peak_exit_method='after_peak')
    index, stop = start, int(.875*HISTOGRAM_BINS)
    nodes = []
    while index < stop:
        end = index+1
        while values[index:end].sum() < GROUP_POPULATION and end < min(index+MAX_GROUP_BINS, stop):
            end += 1
        population = float(values[index:end].sum())
        if population < MIN_BIN_COUNT:
            break
        density = population*HISTOGRAM_BINS/((end-index)*support)
        nodes.append(((index+end)/(2*HISTOGRAM_BINS), np.log10(density), population))
        index = end
    result.update(support_span=(index-start)/HISTOGRAM_BINS,
                  score=-1+(index-start)/HISTOGRAM_BINS)
    if len(nodes) < 8 or index-start < 20:
        return result
    x, y, populations = np.asarray(nodes).T
    from scipy.interpolate import make_smoothing_spline
    spline = make_smoothing_spline(x, y, w=populations/(populations+16), lam=smoothing)
    grid = np.linspace(x[0], x[-1], GRID_POINTS)
    smooth = spline(grid)
    pieces = _pieces(grid, smooth, tolerance)
    drop = float(np.mean(smooth[:3])-np.mean(smooth[-3:]))
    u = np.linspace(0, 1, GRID_POINTS)
    slopes = np.array([np.polyfit(u[k:k+11], smooth[k:k+11], 1)[0]
                       for k in range(0, 60, 10)])
    changes = np.diff(slopes)/max(abs(drop), MIN_SHAPE_DROP_DEX)
    sequence = []
    for change in changes:
        if abs(change) >= SLOPE_CHANGE_THRESHOLD:
            sign = int(np.sign(change))
            if not sequence or sequence[-1] != sign:
                sequence.append(sign)
    rms = float(np.mean([piece['rms_dex'] for piece in pieces])) if pieces else None
    covered = bool(x[0] <= .8 <= x[-1])
    shape = dict(x=grid.tolist(), log_density=smooth.tolist(), drop_dex=drop,
                 region_slopes=slopes.tolist(), slope_sequence=sequence,
                 smoothing=smoothing, nodes=len(nodes), reference_fixed=True)
    measurable = bool(pieces and drop >= MIN_SHAPE_DROP_DEX)
    result.update(measurable=measurable, line_start=float(x[0]),
                  line_end=float(x[-1]), line_span=float(x[-1]-x[0]),
                  fit_start=float(x[0]), fit_end=float(x[-1]), fit_bins=index-start,
                  rms_dex=rms, pieces=pieces, shape=shape,
                  reference_covered=covered,
                  reference_log_density=float(spline(.8)) if covered else None,
                  score=(.05/(1+rms/tolerance)+.02*min(.8, float(x[-1]))
                         if measurable else -1+(index-start)/HISTOGRAM_BINS),
                  loss=-rms if rms is not None else None)
    return result


def shape_comparison_options(args: argparse.Namespace) -> dict[str, float]:
    """Résout les seules options de l'épaulement et des ratios pour les moteurs."""
    return dict(
        ratio_tolerance=getattr(args, 'ghs_shape_ratio_tolerance', DEFAULT_RATIO_TOLERANCE),
        max_shoulder_advance=getattr(args, 'ghs_shape_shoulder_advance', DEFAULT_SHOULDER_ADVANCE),
        terminal_weight=getattr(args, 'ghs_shape_terminal_weight', TERMINAL_RATIO_WEIGHT),
    )


def shoulder_slope_geometry(profile: JSONReport) -> JSONReport:
    """Mesure l’épaulement initial et cinq pentes après celui-ci.

    L'épaulement termine la descente initiale vers le corps moins pentu :
    parmi les raccords descendants des premiers 60 % du support dont la pente
    se réduit d'au moins 15 %, on retient celui débouchant sur la pente la
    moins forte. La segmentation utilise la décroissance normalisée. Quatre régions égales
    décrivent le corps après l’épaulement ; le dernier
    morceau plus raide est isolé comme queue. Sans raccord terminal distinct,
    la queue occupe les derniers 20 % du support. Chaque pente utilise cinq
    points interpolés.
    Les ratios sont indépendants d'une multiplication commune des pentes.
    Une pente quasi nulle ou positive est signalée, jamais divisée par zéro.
    """
    shape = profile.get('shape') or {}
    return deepcopy(_cached_shoulder_geometry(
        tuple(shape.get('x', [])), tuple(shape.get('log_density', [])),
        float(shape.get('drop_dex', 0.)),
    ))


@lru_cache(maxsize=512)
def _cached_shoulder_geometry(
    coordinates: tuple[float, ...], levels: tuple[float, ...], drop: float,
) -> JSONReport:
    """Mémorise au plus 512 petits profils, jamais des images ni des paramètres.

    Les clés sont immuables et le résultat est copié par l'appelant : aucun
    état d'exécution ne peut se transmettre entre moteurs ou entre couches.
    """
    x = np.asarray(coordinates, dtype=float)
    y = np.asarray(levels, dtype=float)
    result: JSONReport = dict(measurable=False, shoulder_present=False,
                             shoulder_position=None, slopes=[], ratios=[])
    if (x.size < 8 or x.shape != y.shape or not np.isfinite(x).all()
            or not np.isfinite(y).all() or np.any(np.diff(x) <= 0)):
        return result
    # Une segmentation en décroissance normalisée ne disparaît pas lorsque
    # toutes les pentes sont multipliées par le même facteur.
    if drop <= 0:
        return result
    pieces = _pieces(x, (y-y[0])/drop, SHOULDER_FIT_TOLERANCE)
    shoulder = float(x[0])
    shoulders = []
    for first, second in zip(pieces, pieces[1:]):
        left, right = first['slope'], second['slope']
        if (left < 0 and right < 0 and (right-left)/abs(left) >= SHOULDER_MIN_FLATTENING
                and first['end'] <= x[0]+MAX_SHOULDER_RELATIVE_POSITION*(x[-1]-x[0])):
            shoulders.append((abs(right), float(first['end'])))
    if shoulders:
        _, shoulder = min(shoulders)
        result.update(shoulder_present=True, shoulder_position=shoulder)
    if shoulder >= x[-1]:
        return result
    terminal_start = shoulder+.8*(x[-1]-shoulder)
    if len(pieces) >= 2:
        previous, terminal = pieces[-2], pieces[-1]
        if (previous['slope'] < 0 and terminal['slope'] < previous['slope']*(1+SHOULDER_MIN_FLATTENING)
                and terminal['start'] > shoulder):
            terminal_start = float(terminal['start'])
    boundaries = np.r_[np.linspace(shoulder, terminal_start, POST_SHOULDER_REGIONS), x[-1]]
    slopes = []
    for first, last in zip(boundaries, boundaries[1:]):
        positions = np.linspace(first, last, MIN_PIECE_POINTS)
        slopes.append(float(np.polyfit(positions, np.interp(positions, x, y), 1)[0]))
    slopes = np.array(slopes)
    floor = max(float(np.max(abs(slopes)))*SLOPE_RELATIVE_EPSILON, np.finfo(float).eps)
    ratios = [float(last/first) if abs(first) > floor else None
              for first, last in zip(slopes, slopes[1:])]
    result.update(measurable=True, slopes=slopes.tolist(), ratios=ratios,
                  slope_floor=floor, boundaries=boundaries.tolist())
    return result


def compare_slope_order(reference: np.ndarray, candidate: np.ndarray) -> JSONReport:
    """Compare directement l'ordre signé des pentes voisines, sans quotient.

    Chaque différence s[i+1]-s[i] est comparée à 10 % du maximum des deux
    amplitudes voisines, indépendamment dans chaque profil. Une quasi-égalité
    ne définit pas d'ordre. Une multiplication positive commune des pentes
    ne change pas ce diagnostic. Un raidissement descendant du dernier raccord
    est diagnostiqué mais exempté comme auparavant pour la queue terminale.
    Les pentes croissantes restent contrôlées séparément par l'appelant.
    """
    first, last = np.asarray(reference, dtype=float), np.asarray(candidate, dtype=float)
    if (first.ndim != 1 or first.size < 2 or last.shape != first.shape
            or not np.isfinite(first).all() or not np.isfinite(last).all()):
        raise ValueError('Pentes finies de même dimension requises')
    rows = []
    for index in range(first.size-1):
        differences, thresholds, orders = [], [], []
        for slopes in (first, last):
            delta = float(slopes[index+1]-slopes[index])
            threshold = SLOPE_ORDER_TOLERANCE*max(abs(slopes[index]), abs(slopes[index+1]))
            order = 1 if delta > threshold else -1 if delta < -threshold else 0
            differences.append(delta)
            thresholds.append(float(threshold))
            orders.append(order)
        terminal = index == first.size-2 and last[index+1] < last[index] < 0
        reversed_order = orders[0]*orders[1] < 0
        rows.append(dict(index=index+1, reference_difference=differences[0],
                         candidate_difference=differences[1],
                         reference_tolerance=thresholds[0], candidate_tolerance=thresholds[1],
                         reference_order=orders[0], candidate_order=orders[1],
                         reversed=reversed_order, terminal_exemption=bool(terminal)))
    return dict(method='signed adjacent slope differences', tolerance=SLOPE_ORDER_TOLERANCE,
                comparisons=rows,
                inverted=any(row['reversed'] and not row['terminal_exemption'] for row in rows))


def compare_shoulder_slope_ratios(
    reference: JSONReport, candidate: JSONReport, max_advance: float,
    ratio_tolerance: float, terminal_weight: float = TERMINAL_RATIO_WEIGHT,
) -> JSONReport:
    """Compare épaulement et rapports voisins sans conserver la chute globale.

    max_advance est le déplacement positif autorisé de l'épaulement dans
    [0,1]. ratio_tolerance borne les écarts logarithmiques par log(1+t).
    L’ordre est comparé directement par les différences signées des pentes ;
    les quasi-égalités à 10 % de l’amplitude voisine sont non significatives.
    Un raidissement terminal descendant pèse 20 % et n'est pas éliminatoire ;
    les inversions de signe des pentes restent interdites à toute position.
    """
    if not np.isfinite(max_advance) or not 0 <= max_advance <= 1:
        raise ValueError('Limite d’avance de l’épaulement hors de [0,1]')
    if isinstance(ratio_tolerance, (bool, np.bool_)) or not np.isfinite(ratio_tolerance) or not 0 < ratio_tolerance <= 4:
        raise ValueError('Tolérance des ratios de pente hors de ]0,4]')
    if not np.isfinite(terminal_weight) or not 0 <= terminal_weight <= 1:
        raise ValueError('Poids terminal hors de [0,1]')
    before, after = shoulder_slope_geometry(reference), shoulder_slope_geometry(candidate)
    reasons = []
    result: JSONReport = dict(policy='shoulder_and_slope_ratios', measurable=False,
                             achieved=None, rejections=reasons, penalty=0.,
                             reference_geometry=before, candidate_geometry=after,
                             shoulder_advance=None, maximum_shoulder_advance=max_advance,
                             ratio_errors=[], ratio_break=0., ratio_tolerance=ratio_tolerance,
                             terminal_weight=terminal_weight)
    if not before['measurable']:
        return result
    if not after['measurable']:
        result.update(measurable=True, achieved=False, rejections=['unmeasurable histogram shape'])
        return result
    if before['shoulder_present']:
        if not after['shoulder_present']:
            reasons.append('histogram shoulder lost')
        else:
            advance = max(0., after['shoulder_position']-before['shoulder_position'])
            result['shoulder_advance'] = advance
            if advance > max_advance+1e-12:
                reasons.append('histogram shoulder advance')
    first = np.array(before['slopes'], dtype=float)
    last = np.array(after['slopes'], dtype=float)
    initial_floor, final_floor = before['slope_floor'], after['slope_floor']
    if np.any((first < -initial_floor) & (last >= -final_floor)):
        reasons.append('histogram slope sign inversion')
    errors, normal_errors = [], []
    order_audit = compare_slope_order(first, last)
    result['slope_order'] = order_audit
    inversion = order_audit['inverted']
    bounds_before = np.asarray(before['boundaries'])
    bounds_after = np.asarray(after['boundaries'])
    fractions_before = (bounds_before-bounds_before[0])/(bounds_before[-1]-bounds_before[0])
    fractions_after = (bounds_after-bounds_after[0])/(bounds_after[-1]-bounds_after[0])
    result['boundary_fraction_shift'] = float(np.max(abs(fractions_after-fractions_before)))
    bound = float(np.log1p(ratio_tolerance))
    for index, (old, new) in enumerate(zip(before['ratios'], after['ratios'])):
        if old is None or new is None or old <= 0 or new <= 0:
            errors.append(None)
            continue
        change = float(np.log(new/old))
        terminal_steepening = index == len(before['ratios'])-1 and new > 1 and change > 0
        weight = terminal_weight if terminal_steepening else 1.
        error = abs(change)*weight
        errors.append(error)
        if not terminal_steepening:
            normal_errors.append(change)
            if error > bound:
                reasons.append('histogram slope ratio distortion')
        result['penalty'] += RATIO_EXCESS_WEIGHT*(max(0., error-bound)/bound)**2
    if inversion:
        reasons.append('histogram slope order inversion')
        result['penalty'] += RATIO_INVERSION_PENALTY
    # Une rupture isolée ne doit pas être masquée par une moyenne des ratios.
    ratio_break = (float(np.max(abs(np.asarray(normal_errors)-np.median(normal_errors))))
                   if normal_errors else 0.)
    if ratio_break > bound:
        reasons.append('histogram slope ratio break')
    result['penalty'] += RATIO_EXCESS_WEIGHT*(max(0., ratio_break-bound)/bound)**2
    result.update(measurable=True, achieved=not reasons, rejections=list(dict.fromkeys(reasons)),
                  ratio_errors=errors, ratio_break=ratio_break)
    return result


def compare_histogram_shapes(
    reference: JSONReport, candidate: JSONReport,
    max_peak_shift: float = DEFAULT_PEAK_SHIFT,
    *, ratio_tolerance: float = DEFAULT_RATIO_TOLERANCE,
    max_shoulder_advance: float = DEFAULT_SHOULDER_ADVANCE,
    terminal_weight: float = TERMINAL_RATIO_WEIGHT,
) -> JSONReport:
    """Préserve l'épaulement et les rapports de pentes après celui-ci.

    L'aplatissement global homogène n'est plus pénalisé. Les anciennes mesures
    d'amplitude et de distance restent informatives pour lire les diagnostics.
    Le signe des pentes et les proportions de leurs rapports voisins
    sont protégés ; un raidissement terminal bénéficie d'un poids réduit.
    Une aggravation excessive des modes sombres, mesurée sans spline sur
    [0,0.25[, est éliminatoire pour toutes les phases utilisant cet audit.
    La référence reste fixe pendant toutes les phases.

    Parameters
    ----------
    reference, candidate : JSONReport
        Profils complets issus de piecewise_histogram_profile, avec le même
        réglage de lissage et les mêmes unités de densité.
    max_peak_shift : float
        Déplacement absolu maximal du pic, en intensité normalisée, de 0 à 1.
    ratio_tolerance : float
        Tolérance t dans ]0,4] sur les rapports, avec borne log(1+t).
    max_shoulder_advance : float
        Déplacement positif maximal de l’épaulement, en intensité [0,1].
    terminal_weight : float
        Poids du raidissement terminal dans [0,1], 0,2 par défaut.

    Returns
    -------
    JSONReport
        Mesurabilité, verdict, motifs et pénalité. achieved vaut None lorsque
        la référence ne permet pas de certifier la conservation de forme.

    Raises
    ------
    ValueError
        Si un seuil est non fini ou hors de son domaine.
    """
    if not np.isfinite(max_peak_shift) or not 0 <= max_peak_shift <= 1:
        raise ValueError('ghs-shape-peak-shift doit être dans [0,1]')
    before, after = reference.get('shape'), candidate.get('shape')
    mode_guard = HistogramRenderQuality.low_light_mode_guard(reference, candidate)
    audit: JSONReport = dict(
        measurable=False, achieved=False if mode_guard['rejections'] else None,
        rejections=list(mode_guard['rejections']), low_light_mode_guard=mode_guard,
        drop_retention=None, minimum_drop_retention=None, distance=None, penalty=0.0,
    )
    if not before or before['drop_dex'] < MIN_SHAPE_DROP_DEX:
        return audit
    reasons = list(audit['rejections'])
    if not after or after['drop_dex'] < MIN_SHAPE_DROP_DEX:
        reasons.append('unmeasurable histogram shape')
        audit.update(measurable=True, achieved=False, rejections=reasons)
        return audit
    first, last = np.array(before['log_density']), np.array(after['log_density'])
    first = (first[0]-first)/before['drop_dex']
    last = (last[0]-last)/after['drop_dex']
    distance = float(np.mean(abs(first-last)))
    ratio = float(after['drop_dex']/before['drop_dex'])
    slopes_before = np.asarray(before['region_slopes'])/before['drop_dex']
    slopes_after = np.asarray(after['region_slopes'])/after['drop_dex']
    slope_scale = np.maximum(abs(slopes_before), SLOPE_SCALE_FLOOR)
    deformation = (slopes_after-slopes_before)/slope_scale
    # La dernière région peut se raidir pour rejoindre le blanc. Son
    # aplatissement reste contrôlé ; les régions intermédiaires restent liées.
    slope_deformation = float(max(np.max(abs(deformation[:-1])), max(0., deformation[-1])))
    stiffness = float(max(np.max(abs(np.diff(deformation[:-1]))),
                          max(0., deformation[-1]-deformation[-2])))
    # Le signe reste significatif : une décroissance devenue croissance
    # ne doit pas être sauvée par la valeur absolue de sa pente.
    significant = abs(slopes_before) >= SLOPE_SCALE_FLOOR
    local_ratio = float(np.min(
        slopes_after[significant]/slopes_before[significant]*ratio,
    )) if np.any(significant) else None
    first_shoulder = ((reference['line_start']-reference['peak'])
                      / max(reference['line_end']-reference['peak'], .1))
    last_shoulder = ((candidate['line_start']-candidate['peak'])
                     / max(candidate['line_end']-candidate['peak'], .1))
    shoulder_change = abs(float(last_shoulder-first_shoulder))
    peak_shift = abs(float(candidate['peak']-reference['peak']))
    comparison = compare_shoulder_slope_ratios(
        reference, candidate, max_shoulder_advance, ratio_tolerance, terminal_weight,
    )
    reasons = list(dict.fromkeys([*reasons, *comparison['rejections']]))
    if not comparison['measurable']:
        reasons.append('unmeasurable histogram shape')
    if peak_shift > max_peak_shift+1e-12:
        reasons.append('histogram peak displacement')
    sequence_before, sequence_after = before['slope_sequence'], after['slope_sequence']
    terminal_steepening = bool(slopes_after[-1] < slopes_before[-1])
    sequence_informative = bool(sequence_before)
    audit.update(measurable=True, achieved=not reasons, rejections=reasons,
                 drop_retention=ratio, distance=distance,
                 maximum_shape_distance=None,
                 shoulder_change=shoulder_change,
                 peak_shift=peak_shift,
                 maximum_peak_shift=max_peak_shift,
                 local_slope_retention=local_ratio,
                 minimum_local_retention=None,
                 terminal_steepening=terminal_steepening,
                 sequence_informative=sequence_informative,
                 slope_deformation=slope_deformation, slope_stiffness=stiffness,
                 slope_relative_deformation=deformation.tolist(),
                 max_slope_stiffness=None,
                 sequence_before=before['slope_sequence'], sequence_after=after['slope_sequence'],
                 penalty=comparison['penalty'])
    audit.update({key: value for key, value in comparison.items()
                  if key not in ('measurable', 'achieved', 'rejections')})
    return audit


def protect_histogram_shape(
    profile: JSONReport, reference: JSONReport,
    max_peak_shift: float = DEFAULT_PEAK_SHIFT,
    *, ratio_tolerance: float = DEFAULT_RATIO_TOLERANCE,
    max_shoulder_advance: float = DEFAULT_SHOULDER_ADVANCE,
    terminal_weight: float = TERMINAL_RATIO_WEIGHT,
) -> JSONReport:
    """Ajoute l'audit et la pénalité de forme sans modifier les profils reçus.

    Parameters
    ----------
    profile, reference : JSONReport
        Profil candidat et référence immuable de la phase.
    max_peak_shift, ratio_tolerance, max_shoulder_advance, terminal_weight : float
        Seuils et unités définis par compare_histogram_shapes.

    Returns
    -------
    JSONReport
        Copie enrichie des audits de forme et de modes sombres, avec score
        pénalisé. Une seconde bosse excessive constitue un rejet obligatoire.
        Les refus restent à faire appliquer par le moteur ou le sélecteur.
    """
    result = dict(profile)
    audit = compare_histogram_shapes(reference, profile, max_peak_shift=max_peak_shift,
                                     ratio_tolerance=ratio_tolerance,
                                     max_shoulder_advance=max_shoulder_advance,
                                     terminal_weight=terminal_weight)
    mode_guard = audit['low_light_mode_guard']
    result.update(shape_preservation=audit, low_light_mode_guard=mode_guard,
                  score=profile['score']-audit['penalty'])
    return result
