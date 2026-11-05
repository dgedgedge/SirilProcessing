"""Recherche de droite semi-log et de niveau relatif à son extrémité.

L'abscisse reste l'intensité linéaire ; l'ordonnée est log10(densité).
Une pente descendante n'est pas pénalisée au profit d'un plateau. Voir
``docs/scripts/postProcess/ghs-quartiles.md`` pour les critères et limites.
"""
from __future__ import annotations

import numpy as np

from lib.ghs_log_histogram import HISTOGRAM_BINS, MIN_BIN_COUNT
from lib.type_defs import JSONReport

LINE_ENDPOINT = .875
MIN_LINE_BINS = 20
DEFAULT_LINEARITY_TOLERANCE = .05  # RMS en dex, soit environ 12 % de densité.
MAX_RESIDUAL_FACTOR = 3.0
# La régularité ne peut jamais compenser une seule classe de longueur perdue.
REGULARITY_BONUS = .25 / HISTOGRAM_BINS
INFLECTION_HALF_WIDTH = 8
MAX_INFLECTIONS = 2

# Une résolution fixe rend le repère indépendant de la taille de la fenêtre GUI.
TAIL_HISTOGRAM_BINS = 512
DEFAULT_TAIL_HEIGHT = .25
# La hauteur peut compenser au plus 0,05 d'abscisse, pas une longue droite perdue.
TAIL_HEIGHT_WEIGHT = .05
TAIL_CHANNELS = ('red', 'green', 'blue')



def _validated_counts(counts: np.ndarray) -> np.ndarray:
    """Exige 128 effectifs finis non négatifs, sans inventer de pixels."""
    values = np.asarray(counts, dtype=np.float64)
    if (values.shape != (HISTOGRAM_BINS,) or not np.isfinite(values).all()
            or np.any(values < 0)):
        raise ValueError('Effectifs invalides pour la linéarité GHS')
    return values


def _peak(counts: np.ndarray) -> int:
    """Repère le pic principal par lissage [1,2,1]/4 des effectifs."""
    return int(np.argmax(np.convolve(counts, [1/4, 1/2, 1/4], mode='same')))


def linear_histogram_profile(
    counts: np.ndarray, tolerance: float = DEFAULT_LINEARITY_TOLERANCE,
) -> JSONReport:
    """Classe la longueur continue quasi rectiligne après le pic jusqu'à 0,875.

    Chaque classe du segment doit contenir au moins trois pixels. Les moindres
    carrés non pondérés doivent avoir un RMS <= tolerance dex et aucun écart
    supérieur à trois fois cette tolérance. La pente est librement négative ou
    nulle. Une droite exige au moins 20 classes contiguës. Les sommes préfixées
    évaluent les intervalles sans ajustements itératifs ni logarithme des zéros.
    À longueur égale seulement, le RMS départage les segments.

    Le score d'un profil mesurable vaut longueur + bonus de régularité inférieur
    à un quart de classe. Sans droite mesurable, le score négatif ne décrit que
    l'amorçage du plus long support continu. loss reste None dans ce cas.
    """
    values = _validated_counts(counts)
    if not np.isfinite(tolerance) or not .005 <= tolerance <= .5:
        raise ValueError('ghs-linearity-tolerance doit être dans [0.005,0.5] dex')
    support = float(values.sum())
    centers = (np.arange(HISTOGRAM_BINS)+.5) / HISTOGRAM_BINS
    peak_index = _peak(values)
    start, stop = peak_index+1, int(LINE_ENDPOINT * HISTOGRAM_BINS)
    valid = values[start:stop] >= MIN_BIN_COUNT
    edges = np.flatnonzero(np.diff(np.r_[False, valid, False]))
    support_bins = int(np.max(edges[1::2]-edges[::2])) if edges.size else 0
    peak = float(centers[peak_index]) if support else None
    report: JSONReport = dict(
        peak=peak, bins=HISTOGRAM_BINS, support=int(support), window_end=LINE_ENDPOINT,
        slope=None, intercept=None, rms_dex=None, maximum_residual_dex=None,
        fit_start=None, fit_end=None, fit_bins=0, line_start=None, line_end=None,
        line_span=0.0, coverage=float(valid.mean()) if valid.size else 0.0,
        support_span=support_bins/HISTOGRAM_BINS, measurable=False,
        tolerance_dex=tolerance, loss=None, score=-1+support_bins/HISTOGRAM_BINS,
        objective='longest_contiguous_log_linear_span',
    )
    if support_bins < MIN_LINE_BINS:
        return report
    x = centers[start:stop]
    y = np.zeros(x.size)
    y[valid] = np.log10(values[start:stop][valid] * HISTOGRAM_BINS / support)
    left, right = np.triu_indices(x.size, k=MIN_LINE_BINS-1)
    size = right-left+1

    def sums(array: np.ndarray) -> np.ndarray:
        """Calcule les sommes de chaque intervalle avec une seule somme préfixée."""
        prefix = np.r_[0., np.cumsum(array)]
        return prefix[right+1]-prefix[left]

    sx, sy, sxx, sxy, syy = (sums(a) for a in (x, y, x*x, x*y, y*y))
    centered_xx, centered_xy = sxx-sx*sx/size, sxy-sx*sy/size
    slope = centered_xy/centered_xx
    intercept = (sy-slope*sx)/size
    rms = np.sqrt(np.maximum(0, syy-sy*sy/size-centered_xy**2/centered_xx)/size)
    eligible = (sums(valid.astype(float)) == size) & (rms <= tolerance) & (slope <= 1e-6)
    candidates = np.flatnonzero(eligible)
    candidates = candidates[np.lexsort((left[candidates], rms[candidates], -size[candidates]))]
    for index in candidates:
        region = slice(left[index], right[index]+1)
        residual = y[region]-intercept[index]-slope[index]*x[region]
        maximum = float(np.max(abs(residual)))
        if maximum > MAX_RESIDUAL_FACTOR*tolerance:
            continue
        span = float(size[index]/HISTOGRAM_BINS)
        score = span + REGULARITY_BONUS*(1-float(rms[index])/tolerance)
        report.update(
            measurable=True, slope=float(slope[index]), intercept=float(intercept[index]),
            rms_dex=float(rms[index]), maximum_residual_dex=maximum,
            fit_start=float(x[left[index]]), fit_end=float(x[right[index]]),
            fit_bins=int(size[index]), line_start=float((start+left[index])/HISTOGRAM_BINS),
            line_end=float((start+right[index]+1)/HISTOGRAM_BINS), line_span=span,
            score=score, loss=-score,
        )
        break
    return report


def symmetry_points(counts: np.ndarray) -> tuple[float, ...]:
    """Propose zéro, le pic et jusqu'à deux ruptures de pente sous 0,75.

    De part et d'autre de chaque frontière de classe, huit classes peuplées
    définissent deux droites log. Leur différence de pente est classée par la
    régularité locale ; les ruptures retenues sont espacées d'un octile. Cela
    explore notamment une inflexion centrale sans fixer SP au nom de l'objet.
    """
    values = _validated_counts(counts)
    if not values.sum():
        return (0.0,)
    peak = _peak(values)
    points = {0.0, min((peak+.5)/HISTOGRAM_BINS, .749)}
    width = INFLECTION_HALF_WIDTH
    coordinates = np.arange(width)-(width-1)/2
    norm = float(np.dot(coordinates, coordinates))
    changes = []
    for split in range(max(peak+width, width), int(.75*HISTOGRAM_BINS)-width+1):
        block = values[split-width:split+width]
        if np.any(block < MIN_BIN_COUNT):
            continue
        log_counts = np.log10(block)
        first, last = log_counts[:width], log_counts[width:]
        first_slope, last_slope = np.dot(first, coordinates)/norm, np.dot(last, coordinates)/norm
        residual = np.r_[first-first.mean()-first_slope*coordinates,
                         last-last.mean()-last_slope*coordinates]
        score = abs(last_slope-first_slope)/(float(np.sqrt(np.mean(residual**2)))+.01)
        changes.append((float(score), split/HISTOGRAM_BINS))
    chosen = []
    for score, point in sorted(changes, key=lambda item: (-item[0], item[1])):
        if score < .05:
            break
        if all(abs(point-previous) >= .125 for previous in chosen):
            chosen.append(point)
        if len(chosen) == MAX_INFLECTIONS:
            break
    return tuple(sorted(points.union(chosen)))



def log_tail_height(counts: np.ndarray, population_scale: float = 1.0) -> JSONReport:
    """Mesure la hauteur log vers 0,875, normalisée par le maximum du canal.

    Les 512 classes couvrent [0,1], zéros inclus comme dans l'affichage Siril.
    Les deux hauteurs entourant 0,875 sont interpolées linéairement. Une classe
    de zéro ou un pixel reste au bas du graphique. population_scale ramène
    les effectifs d'une grille échantillonnée à la population du champ complet ;
    cette estimation ne remplace pas la validation de tous les pixels.
    Un maximum <= un pixel ne définit aucune échelle logarithmique (height=None).
    """
    values = np.asarray(counts, dtype=np.float64)
    if (values.shape != (TAIL_HISTOGRAM_BINS,) or not np.isfinite(values).all()
            or np.any(values < 0)):
        raise ValueError('Effectifs invalides pour la hauteur logarithmique GHS')
    if not np.isfinite(population_scale) or population_scale <= 0:
        raise ValueError('Échelle de population GHS strictement positive requise')
    with np.errstate(over='ignore'):
        scaled = values * population_scale
    if not np.isfinite(scaled).all():
        raise ValueError('Effectifs redimensionnés GHS non finis')
    maximum = float(scaled.max())
    centers = (np.arange(TAIL_HISTOGRAM_BINS)+.5)/TAIL_HISTOGRAM_BINS
    levels = np.zeros_like(scaled)
    positive = scaled > 1
    levels[positive] = np.log(scaled[positive])
    height = float(np.interp(LINE_ENDPOINT, centers, levels)/np.log(maximum)) if maximum > 1 else None
    return dict(
        height=height, endpoint=LINE_ENDPOINT, bins=TAIL_HISTOGRAM_BINS,
        peak_count=maximum, endpoint_count=float(np.interp(LINE_ENDPOINT, centers, scaled)),
        sampled_pixels=float(values.sum()), population_scale=float(population_scale),
        estimated=population_scale != 1.0, measurable=height is not None,
        normalization='per_channel_log_count_over_log_peak',
    )


def add_tail_height_objective(
    line: JSONReport, tail: JSONReport, target: float, channel: str,
) -> JSONReport:
    """Combine longueur et progression bornée vers le niveau log demandé.

    Le bonus de hauteur vaut au plus 0,05 : il peut compenser quelques classes
    de longueur, jamais un raccourcissement important. Au-delà de target, aucun
    gain additionnel n'est accordé. target=0 conserve exactement le score de
    longueur antérieur. Les dictionnaires fournis ne sont jamais modifiés.
    """
    if not np.isfinite(target) or not 0 <= target <= 1:
        raise ValueError('ghs-tail-height doit être dans [0,1]')
    if channel not in TAIL_CHANNELS:
        raise ValueError('ghs-tail-channel doit être red, green ou blue')
    height = tail['height']
    if height is not None and (not np.isfinite(height) or not 0 <= height <= 1):
        raise ValueError('Hauteur logarithmique GHS invalide')
    achieved = height is not None and height >= target if target > 0 else None
    progress = min(1.0, max(0.0, height)/target) if target > 0 and height is not None else 0.0
    bonus = TAIL_HEIGHT_WEIGHT*progress if target > 0 else 0.0
    report = dict(line)
    report.update(
        line_score=line['score'], tail=dict(tail, channel=channel, target=target,
                                           achieved=achieved),
        tail_height_bonus=bonus, score=line['score']+bonus,
        objective='longest_log_linear_span_with_tail_height' if target > 0 else line['objective'],
    )
    report['loss'] = -report['score'] if report['measurable'] else None
    return report
