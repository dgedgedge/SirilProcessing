"""Régression robuste de la queue d'histogramme pour le classement GHS.

Les intensités restent linéaires dans [0,1] ; seule la densité est logarithmique.
La pente est une heuristique de présentation, sans valeur cible universelle.
Voir docs/scripts/postProcess/ghs-highlight-protection.md pour ses limites.
"""

from __future__ import annotations

import numpy as np

from lib.type_defs import JSONReport

# Une résolution fixe rend les essais comparables malgré le déplacement du pic.
HISTOGRAM_BINS = 128
TAIL_ENDPOINT = 0.875
FALLBACK_TAIL_ENDPOINT = 0.75
# Au moins la moitié des 16 classes du dernier intervalle doit être exploitable.
MIN_EXTENSION_BINS = 8
MIN_BIN_COUNT = 3
MIN_FIT_BINS = 12
MIN_FIT_SPAN = 0.15
HUBER_ITERATIONS = 8
HUBER_TUNING = 1.5
HISTOGRAM_SLOPE_WEIGHT = 0.15
DEFAULT_LOG_RMS_WEIGHT = 2.0
# 0,01 dex correspond à environ 2,3 % de densité : éviter de survaloriser
# une différence infinitésimale entre deux distributions presque rectilignes.
RMS_REGULARIZER_DEX = 0.01


def fit_log_tail(light: np.ndarray) -> JSONReport:
    """Ajuste log10(densité) = a + bY après le pic, jusqu'à 0,875 ou 0,75.

    Parameters
    ----------
    light : numpy.ndarray
        Échantillon de luminosité fini dans [0,1]. Les zéros de bordure sont
        exclus ; l'appelant garantit le domaine et la finitude des pixels.

    Returns
    -------
    JSONReport
        Pic, fenêtre, pente en dex par unité Y, résidu RMS en dex et R² pondéré.
        Les mesures de régression sont nulles lorsque le support est insuffisant.
        Les classes vides ne reçoivent ni pseudo-effectif ni logarithme artificiel.

    Notes
    -----
    Le pic vient des effectifs lissés [1,2,1]/4. La régression utilise les
    effectifs bruts normalisés par le support et la largeur des classes. Des
    poids de Huber limitent l'influence des bosses, sans imposer une droite.
    Le bruit, la segmentation de champ et le choix de fenêtre restent des biais.
    """
    active = np.asarray(light)[np.asarray(light) > 1e-6]
    counts = np.histogram(active, bins=HISTOGRAM_BINS, range=(0, 1))[0]
    return fit_log_tail_counts(counts)


def fit_log_tail_counts(
    counts: np.ndarray, window_end: float | None = None,
) -> JSONReport:
    """Ajuste la queue depuis 128 effectifs, transférables seuls depuis CUDA.

    Parameters
    ----------
    counts : numpy.ndarray
        Effectifs finis, positifs ou nuls, de 128 classes uniformes sur [0,1].
        Les pixels de bordure nuls doivent être exclus par l'appelant.
    window_end : float, optional
        Borne commune imposée, 0,75 ou 0,875. Sinon, 0,875 est retenu lorsque
        huit classes de ]0,75;0,875] contiennent chacune au moins trois pixels ;
        à défaut, 0,75. L'appelant fixe cette borne avant de comparer les essais.

    Returns
    -------
    JSONReport
        Même audit que ``fit_log_tail``, sans transfert des pixels RGB sur CPU.

    Raises
    ------
    ValueError
        Dimensions, valeurs non finies, effectifs négatifs ou borne invalide.
    """
    counts = np.asarray(counts, dtype=np.float64)
    if (counts.shape != (HISTOGRAM_BINS,) or not np.isfinite(counts).all()
            or np.any(counts < 0)):
        raise ValueError('Effectifs invalides pour la régression GHS')
    support = float(counts.sum())
    centers = (np.arange(HISTOGRAM_BINS) + 0.5) / HISTOGRAM_BINS
    if window_end is None:
        extension = ((centers > FALLBACK_TAIL_ENDPOINT) & (centers <= TAIL_ENDPOINT)
                     & (counts >= MIN_BIN_COUNT))
        window_end = (TAIL_ENDPOINT if np.count_nonzero(extension) >= MIN_EXTENSION_BINS
                      else FALLBACK_TAIL_ENDPOINT)
    if window_end not in (FALLBACK_TAIL_ENDPOINT, TAIL_ENDPOINT):
        raise ValueError('La borne de régression GHS doit valoir 0,75 ou 0,875')
    smooth = np.convolve(counts, np.array([1, 2, 1]) / 4, mode='same')
    peak_index = int(np.argmax(smooth))
    peak = float(centers[peak_index]) if support else None
    report: JSONReport = {
        'peak': peak, 'window_end': window_end, 'bins': HISTOGRAM_BINS,
        'support': int(support), 'slope': None, 'intercept': None,
        'rms_dex': None, 'r_squared': None, 'fit_bins': 0,
        'fit_start': None, 'fit_end': None,
    }
    if peak is None:
        return report
    selected = ((centers > peak) & (centers <= window_end)
                & (counts >= MIN_BIN_COUNT))
    x = centers[selected]
    report['fit_bins'] = int(x.size)
    if x.size < MIN_FIT_BINS or np.ptp(x) < MIN_FIT_SPAN:
        return report
    y = np.log10(counts[selected] * HISTOGRAM_BINS / support)
    design = np.column_stack([np.ones_like(x), x])
    weights = np.ones_like(x)
    for _ in range(HUBER_ITERATIONS):
        root = np.sqrt(weights)
        intercept, slope = np.linalg.lstsq(
            design * root[:, None], y * root, rcond=None,
        )[0]
        residual = y - intercept - slope * x
        scale = max(float(1.4826 * np.median(abs(residual - np.median(residual)))), 1e-6)
        weights = np.minimum(1, HUBER_TUNING * scale / np.maximum(abs(residual), 1e-12))
    # Recalculer avec les derniers poids pour que coefficients et audit concordent.
    root = np.sqrt(weights)
    intercept, slope = np.linalg.lstsq(design * root[:, None], y * root, rcond=None)[0]
    residual = y - intercept - slope * x
    error = float(np.average(residual ** 2, weights=weights))
    variance = float(np.average((y - np.average(y, weights=weights)) ** 2, weights=weights))
    report.update(
        slope=float(slope), intercept=float(intercept), rms_dex=float(np.sqrt(error)),
        r_squared=1 - error / variance if variance > 1e-12 else None,
        fit_start=float(x[0]), fit_end=float(x[-1]),
    )
    return report


def log_tail_score(
    before: JSONReport, after: JSONReport, rms_weight: float = DEFAULT_LOG_RMS_WEIGHT,
) -> float:
    """Favorise la réduction relative du RMS, puis une décroissance moins raide.

    Les pentes doivent être négatives et mesurables des deux côtés. Des fenêtres
    sans recouvrement suffisant ou sans décroissance ne donnent aucun bonus.
    Les bornes hautes doivent être identiques. Le terme de pente est
    log(|b_avant|/|b_après|), borné à ±1 et pondéré par 0,15. Celui de régularité
    vaut log((RMS_avant+0,01)/(RMS_après+0,01)), borné à ±1 et pondéré par
    rms_weight (défaut 2, dans [0,10]). Le RMS est en dex ; son régularisateur
    évite une amplification arbitraire près de zéro. Le poids nul désactive
    sa contribution. Les gardes de l'orchestrateur restent obligatoires.

    Raises
    ------
    ValueError
        Poids du RMS non fini ou hors de [0,10].
    """
    if not np.isfinite(rms_weight) or not 0 <= rms_weight <= 10:
        raise ValueError('ghs-log-rms-weight doit être dans [0,10]')
    first, last = before['slope'], after['slope']
    if first is None or last is None or first >= -1e-6 or last >= -1e-6:
        return 0.0
    if before['window_end'] != after['window_end']:
        return 0.0
    # Une queue révélée par l'étirement ne doit pas être comparée à une poignée
    # de classes centrales : exiger une fenêtre commune réellement peuplée.
    overlap = min(before['fit_end'], after['fit_end']) - max(
        before['fit_start'], after['fit_start'],
    )
    if overlap < MIN_FIT_SPAN:
        return 0.0
    flatter = float(np.clip(np.log(abs(first) / abs(last)), -1, 1))
    smoother = float(np.clip(np.log(
        (before['rms_dex'] + RMS_REGULARIZER_DEX)
        / (after['rms_dex'] + RMS_REGULARIZER_DEX),
    ), -1, 1))
    return HISTOGRAM_SLOPE_WEIGHT * flatter + rms_weight * smoother
