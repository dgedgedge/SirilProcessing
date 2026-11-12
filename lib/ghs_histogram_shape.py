"""Mesure la forme semi-log lissée et sa régularité par morceaux.

Les splines servent uniquement aux mesures, jamais à transformer les pixels.
Les classes rares sont regroupées sans inventer d'effectifs ; une lacune de
quatre classes sans trois pixels termine le support. La référence reste fixe
pendant la recherche. Voir docs/scripts/postProcess/ghs-histogram-shape.md.
"""

from __future__ import annotations

import numpy as np

from lib.ghs_linear_histogram import linear_histogram_profile
from lib.ghs_log_histogram import HISTOGRAM_BINS, MIN_BIN_COUNT
from lib.type_defs import JSONReport
from lib.ghs_render_quality import HistogramRenderQuality, QUALITY_SELECTION_POLICY

DEFAULT_SMOOTHING = 1e-4
DEFAULT_SHAPE_RETENTION = .65
DEFAULT_SHAPE_DISTANCE = .15
DEFAULT_PEAK_SHIFT = .25
DEFAULT_SLOPE_STIFFNESS = 1.25
DEFAULT_LOCAL_RETENTION = .1
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
                  initial_peak=None, score=-1.0, loss=None)
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


def compare_histogram_shapes(
    reference: JSONReport, candidate: JSONReport,
    retention: float = DEFAULT_SHAPE_RETENTION,
    max_distance: float = DEFAULT_SHAPE_DISTANCE,
    max_peak_shift: float = DEFAULT_PEAK_SHIFT,
    max_stiffness: float = DEFAULT_SLOPE_STIFFNESS,
    local_retention: float = DEFAULT_LOCAL_RETENTION,
) -> JSONReport:
    """Compare à une référence fixe les pentes locales et l'amplitude globale.

    Les courbes sont échantillonnées sur une abscisse relative à leur support,
    pour autoriser un étirement horizontal. La distance moyenne entre profils
    normalisés contrôle les proportions ; la chute en dex reste protégée
    séparément, pour ne pas accepter une copie arbitrairement aplatie.
    Les pentes régionales sont aussi comparées après normalisation par cette
    chute : leur déformation relative et ses différences voisines bornent
    les plateaux locaux et les coudes intermédiaires. Un raidissement terminal
    est permis pour rejoindre le blanc. Chaque pente significative conserve
    au moins local_retention de son amplitude sur l'abscisse relative.
    Une référence non mesurable ne certifie ni conformité ni rejet de forme.

    Parameters
    ----------
    reference, candidate : JSONReport
        Profils complets issus de piecewise_histogram_profile, avec le même
        réglage de lissage et les mêmes unités de densité.
    retention : float
        Fraction minimale de décroissance initiale, dans ]0,1].
    max_distance : float
        Distance moyenne maximale des profils normalisés et variation
        maximale de largeur relative de l'épaulement, dans ]0,1].
    max_peak_shift : float
        Déplacement absolu maximal du pic, en intensité [0,1], de 0 à 0,25.
    max_stiffness : float
        Déformation relative maximale des pentes normalisées et de son
        évolution entre régions intermédiaires, dans ]0,4]. Une dilatation
        commune est permise ; un plateau sous local_retention est refusé.
    local_retention : float
        Fraction minimale conservée par chaque pente significative, dans ]0,1].
        Elle est distincte de la conservation de décroissance globale.

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
    if not np.isfinite(retention) or not 0 < retention <= 1:
        raise ValueError('ghs-shape-retention doit être dans ]0,1]')
    if not np.isfinite(max_distance) or not 0 < max_distance <= 1:
        raise ValueError('ghs-shape-distance doit être dans ]0,1]')
    if not np.isfinite(max_peak_shift) or not 0 <= max_peak_shift <= .25:
        raise ValueError('ghs-shape-peak-shift doit être dans [0,0.25]')
    if (isinstance(max_stiffness, (bool, np.bool_))
            or not np.isfinite(max_stiffness) or not 0 < max_stiffness <= 4):
        raise ValueError('ghs-shape-stiffness doit être dans ]0,4]')
    if (isinstance(local_retention, (bool, np.bool_))
            or not np.isfinite(local_retention) or not 0 < local_retention <= 1):
        raise ValueError('ghs-shape-local-retention doit être dans ]0,1]')
    before, after = reference.get('shape'), candidate.get('shape')
    audit: JSONReport = dict(measurable=False, achieved=None, rejections=[],
                             drop_retention=None, minimum_drop_retention=retention,
                             distance=None, penalty=0.0)
    if not before or before['drop_dex'] < MIN_SHAPE_DROP_DEX:
        return audit
    reasons = []
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
    if ratio < retention:
        reasons.append('global histogram flattening')
    if local_ratio is not None and local_ratio < local_retention:
        reasons.append('local histogram flattening')
    if slope_deformation > max_stiffness:
        reasons.append('histogram slope deformation')
    if stiffness > max_stiffness:
        reasons.append('histogram slope stiffness')
    sequence_before, sequence_after = before['slope_sequence'], after['slope_sequence']
    terminal_steepening = bool(sequence_after == sequence_before+[-1]
                               and slopes_after[-1] < 0)
    sequence_informative = bool(sequence_before)
    unstructured_reference = not sequence_informative and bool(np.all(slopes_after < 0))
    if sequence_before != sequence_after and not terminal_steepening and not unstructured_reference:
        reasons.append('histogram slope sequence')
    if distance > max_distance:
        reasons.append('histogram shape distortion')
    if shoulder_change > max_distance:
        reasons.append('histogram peak shoulder distortion')
    if peak_shift > max_peak_shift+1e-12:
        reasons.append('histogram peak displacement')
    audit.update(measurable=True, achieved=not reasons, rejections=reasons,
                 drop_retention=ratio, distance=distance,
                 maximum_shape_distance=max_distance,
                 shoulder_change=shoulder_change,
                 peak_shift=peak_shift,
                 maximum_peak_shift=max_peak_shift,
                 local_slope_retention=local_ratio,
                 minimum_local_retention=local_retention,
                 terminal_steepening=terminal_steepening,
                 sequence_informative=sequence_informative,
                 slope_deformation=slope_deformation, slope_stiffness=stiffness,
                 slope_relative_deformation=deformation.tolist(),
                 max_slope_stiffness=max_stiffness,
                 sequence_before=before['slope_sequence'], sequence_after=after['slope_sequence'],
                 penalty=(.35*(distance+shoulder_change)
                          +.15*abs(float(np.log(ratio)))
                          +.05*(slope_deformation+stiffness)))
    return audit


def protect_histogram_shape(
    profile: JSONReport, reference: JSONReport,
    retention: float = DEFAULT_SHAPE_RETENTION,
    max_distance: float = DEFAULT_SHAPE_DISTANCE,
    max_peak_shift: float = DEFAULT_PEAK_SHIFT,
    max_stiffness: float = DEFAULT_SLOPE_STIFFNESS,
    local_retention: float = DEFAULT_LOCAL_RETENTION,
) -> JSONReport:
    """Ajoute l'audit et la pénalité de forme sans modifier les profils reçus.

    Parameters
    ----------
    profile, reference : JSONReport
        Profil candidat et référence immuable de la phase.
    retention, max_distance, max_peak_shift, max_stiffness, local_retention : float
        Seuils et unités définis par compare_histogram_shapes.

    Returns
    -------
    JSONReport
        Copie du candidat enrichie de shape_preservation et du score pénalisé.
        Les refus restent à faire appliquer par le moteur ou le sélecteur.
    """
    result = dict(profile)
    audit = compare_histogram_shapes(reference, profile, retention, max_distance,
                                     max_peak_shift, max_stiffness, local_retention)
    result.update(shape_preservation=audit, score=profile['score']-audit['penalty'])
    return result
