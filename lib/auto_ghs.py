"""GHS Siril en NumPy/CuPy et recherche déterministe sur histogrammes.

Les courbes sont les intégrales normalisées du noyau GHS de Siril (GHSastro,
Payne), avec D interne = expm1(D CLI). Les quatre branches de B et les tangentes
LP/HP sont évaluées sans puissances hors domaine. Le rendu RGB utilise les
poids human/even et RGBBlend. Voir docs/scripts/postProcess/auto-ghs.md.
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import platform
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import numpy as np

from lib.compute_config import ComputeConfig
from lib.ghs_final_stretch import ProtectedGHSFinalizer
from lib.ghs_log_histogram import DEFAULT_LOG_RMS_WEIGHT
from lib.processor import processor
from lib.restoration_quality import normalized_planes
from lib.star_rendering import color_drift
from lib.star_separation import validate_rgb, write_rgb
from lib.type_defs import JSONReport

LOGGER = logging.getLogger(__name__)

# Maillage et gardes heuristiques décrits dans auto-ghs-algorithm.md.
CANDIDATE_D_STEP = 0.25
CANDIDATE_D_MAXIMUM = 6.0
CONCENTRATION_SCHEDULE = (15.0, 5.0, 2.0, 0.0)
MAX_BACKGROUND_MEDIAN = 0.45
HIGHLIGHT_BOUNDARY = 0.995
MIN_HIGHLIGHT_WIDTH_RATIO = 0.8
LOG_PEAK_HEIGHT_FRACTION = 0.5
LOG_PEAK_PROMINENCE_FRACTION = 0.12


class ComputeBackend:
    """Sélectionne le moteur avant calcul ; aucune reprise après son initialisation."""

    def __init__(self, requested: str = "auto", required_bytes: int = 0,
                 *, engine: str = 'AutoGHS') -> None:
        """Teste CUDA, réserve une marge VRAM et sinon choisit CPU seulement en auto.

        required_bytes estime les tableaux temporaires ; 20 % de la VRAM libre
        reste disponible. Une panne ultérieure se propage à l'appelant.
        engine nomme le traitement dans le journal. Un choix CPU automatique
        émet un warning ; sur macOS, ce moteur ne possède pas de backend Metal.
        cuda reste accepté pour les contrôles internes exigeant NVIDIA.
        """
        if requested not in ("auto", "cpu", "cuda"):
            raise ValueError("compute-backend doit être auto, cpu ou cuda")
        self.xp: ModuleType = np
        self.name = "cpu"
        self.reason = "CPU explicitly requested"
        self.device: JSONReport = {}
        if requested == "cpu":
            return
        try:
            if platform.system() == 'Darwin':
                raise RuntimeError('Ce moteur NumPy/CuPy ne possède pas de backend Metal Apple')
            cupy = importlib.import_module("cupy")
            if cupy.cuda.runtime.getDeviceCount() < 1:
                raise RuntimeError("Aucun périphérique CUDA")
            free, total = cupy.cuda.runtime.memGetInfo()
            if required_bytes > 0.8 * free:
                raise RuntimeError(f"VRAM libre insuffisante : {free} octets")
            probe = cupy.asarray([0, 1], dtype=cupy.float32)
            if float(cupy.sum(probe).item()) != 1:
                raise RuntimeError("Échec du contrôle CUDA")
            self.xp, self.name = cupy, "cuda"
            self.reason = "CUDA preflight succeeded"
            self.device = {
                "free_bytes": int(free),
                "total_bytes": int(total),
                "cupy_version": cupy.__version__,
            }
        except (ImportError, RuntimeError, OSError) as error:
            if requested == "cuda":
                raise RuntimeError(
                    f"CUDA demandé mais indisponible : {error}"
                ) from error
            self.reason = f"CPU selected before processing: {error}"
            ComputeConfig.warn_cpu_fallback(engine, str(error))
        LOGGER.info("%s : backend %s (%s)", engine, self.name, self.reason)

    def numpy(self, array: np.ndarray) -> np.ndarray:
        """Transfère un résultat vers NumPy ; les appels CUDA échoués restent fatals."""
        return np.asarray(array) if self.name == "cpu" else self.xp.asnumpy(array)


def ghs_transfer(
    values: np.ndarray,
    D: float,
    B: float = 0,
    SP: float = 0,
    LP: float = 0,
    HP: float = 1,
    *,
    xp: ModuleType = np,
) -> np.ndarray:
    """Évalue le GHS réel normalisé sur [0,1], y compris ses tangentes LP/HP.

    D est le paramètre CLI Siril dans [0,10], B dans [-5,15]. LP <= SP <= HP.
    Les entrées doivent être finies dans [0,1] (contrôlées à la frontière image).
    Les branches B=-1, B<0, B=0 et B>0 ont des primitives distinctes.
    D=0 est l'identité. float32 est conservé sur CUDA.
    """
    if not np.isfinite([D, B, SP, LP, HP]).all():
        raise ValueError("Paramètres GHS non finis")
    if not (0 <= D <= 10 and -5 <= B <= 15 and 0 <= LP <= SP <= HP <= 1):
        raise ValueError("Paramètres GHS hors limites Siril")
    if D == 0:
        return values.copy()
    amount = float(np.expm1(D))

    def primitive(distance: np.ndarray | float, module: ModuleType = xp) -> np.ndarray:
        """Intègre le noyau positif depuis le point de symétrie."""
        if B == -1:
            return module.log1p(amount * distance)
        if B < 0:
            beta = -B
            return module.expm1(
                ((beta - 1) / beta) * module.log1p(amount * beta * distance)
            ) / (beta - 1)
        if B == 0:
            return -module.expm1(-amount * distance)
        return -module.expm1((-1 / B) * module.log1p(amount * B * distance))

    def derivative(distance: float) -> float:
        """Calcule la pente commune aux raccords linéaires LP/HP."""
        if B < 0:
            return float(amount * (1 + amount * (-B) * distance) ** (1 / B))
        if B == 0:
            return float(amount * np.exp(-amount * distance))
        return float(amount * (1 + amount * B * distance) ** (-1 - 1 / B))

    lower = float(primitive(SP - LP, np))
    upper = float(primitive(HP - SP, np))
    lower_slope, upper_slope = derivative(SP - LP), derivative(HP - SP)
    zero = -lower - LP * lower_slope
    one = upper + (1 - HP) * upper_slope
    denominator = one - zero
    distance = xp.abs(values - SP)
    # Une seule primitive positive évite de calculer une branche de puissance invalide.
    middle = xp.where(values < SP, -primitive(distance), primitive(distance))
    raw = xp.where(
        values < LP,
        -lower + (values - LP) * lower_slope,
        xp.where(values > HP, upper + (values - HP) * upper_slope, middle),
    )
    result = (raw - zero) / denominator
    # Les bornes exactes ne dépendent pas des arrondis des primitives.
    return xp.where(values == 0, 0, xp.where(values == 1, 1, result))


def luminance(
    data: np.ndarray, mode: str = "human", *, xp: ModuleType = np
) -> np.ndarray:
    """Calcule human (proxy Rec.709) ou moyenne ; aucune calibration supposée."""
    if mode == "even":
        return data.mean(axis=0)
    if mode != "human":
        raise ValueError("Modèle de luminosité inconnu")
    return 0.2126 * data[0] + 0.7152 * data[1] + 0.0722 * data[2]


def ghs_rgb(
    data: np.ndarray,
    D: float,
    B: float = 0,
    SP: float = 0,
    LP: float = 0,
    HP: float = 1,
    *,
    color_mode: str = "human",
    xp: ModuleType = np,
) -> np.ndarray:
    """Applique le GHS lié puis le mélange RGBBlend compatible Siril.

    RGBBlend utilise le plus grand gain de la version liée permettant à chaque
    canal de rester <=1, en mélangeant avec les canaux étirés indépendamment.
    """
    light = luminance(data, color_mode, xp=xp)
    stretched = ghs_transfer(light, D, B, SP, LP, HP, xp=xp)
    gain = xp.where(light > 0, stretched / xp.maximum(light, 1e-30), 0)
    linked = data * gain[None]
    independent = ghs_transfer(data, D, B, SP, LP, HP, xp=xp)
    difference = linked - independent
    ratios = xp.where(linked > 1, (1 - independent) / xp.maximum(difference, 1e-30), 1)
    weight = xp.minimum(1, ratios.min(axis=0))
    return weight[None] * linked + (1 - weight[None]) * independent


def linear_blackpoint(
    data: np.ndarray, BP: float, *, xp: ModuleType = np
) -> np.ndarray:
    """Reproduit linstretch RGB indépendant : max(0,(x-BP)/(1-BP))."""
    if not np.isfinite(BP) or not 0 <= BP < 1:
        raise ValueError("Point noir hors de [0,1[")
    return xp.maximum(0, (data - BP) / (1 - BP))


def render_chroma(
    data: np.ndarray, policy: str = "off", *, xp: ModuleType = np
) -> np.ndarray:
    """Ajuste la chroma RGB autour du gris, avec gain commun limité au gamut.

    off est l'identité de référence. balanced/enhanced appliquent des gains
    maximaux 1.10/1.25, atténués dans les faibles intensités et les hautes
    lumières. La direction RGB par rapport au gris est conservée. Il ne s'agit
    pas d'une conversion perceptuelle : les primaires FITS restent inconnues.
    """
    if policy == "off":
        return data.copy()
    if policy not in ("balanced", "enhanced"):
        raise ValueError("Politique de chroma inconnue")
    light = data.mean(axis=0)
    delta = data - light[None]
    amplitude = 0.10 if policy == "balanced" else 0.25
    # Pas de renforcement sous 2 %, puis rampe jusqu'à 10 % d'intensité.
    weight = xp.clip((light - 0.02) / 0.08, 0, 1) * (1 - light)
    gain = 1 + amplitude * weight
    upper = xp.where(delta > 0, (1 - light)[None] / xp.maximum(delta, 1e-30), xp.inf)
    lower = xp.where(delta < 0, light[None] / xp.maximum(-delta, 1e-30), xp.inf)
    gain = xp.minimum(gain, xp.minimum(upper.min(axis=0), lower.min(axis=0)))
    return light[None] + gain[None] * delta


@dataclass
class HistogramState:
    """Histogramme compact CPU ; seules les statistiques quittent le GPU."""

    counts: np.ndarray
    metrics: JSONReport
    rgb_counts: list[list[int]]

    def report(self) -> JSONReport:
        """Sérialise les effectifs réels pour reproduire les diagnostics lin/log."""
        return {
            "counts": self.counts.astype(int).tolist(),
            "rgb_counts": self.rgb_counts,
            **self.metrics,
        }


class HistogramAnalyzer:
    """Mesure le pic principal sur log1p(effectifs), à demi-hauteur logarithmique."""

    def __init__(
        self,
        bins: int = 4096,
        q_low: float = 0.1,
        q_high: float = 99.9,
        color_mode: str = "human",
    ) -> None:
        """Valide résolution et percentiles en pourcentage, sans changer les pixels."""
        if not isinstance(bins, int) or not 64 <= bins <= 65536:
            raise ValueError("ghs-bins doit être un entier entre 64 et 65536")
        if not np.isfinite([q_low, q_high]).all() or not 0 <= q_low < q_high <= 100:
            raise ValueError("Percentiles GHS invalides")
        if color_mode not in ("human", "even"):
            raise ValueError("ghs-color-mode doit être human ou even")
        self.bins, self.q_low, self.q_high, self.color_mode = (
            bins,
            q_low,
            q_high,
            color_mode,
        )

    def from_counts(self, counts: np.ndarray) -> JSONReport:
        """Mesure largeur de la composante du maximum log lissé et bosses significatives.

        Le filtre [1,2,3,2,1]/9 réduit le bruit de comptage. Les quantiles sont
        interpolés dans la CDF des classes ; les finalistes utilisent les vrais
        quantiles RGB. Un pic tronqué ou plat est marqué ambigu.
        """
        # Garder l'aide et le pipeline désactivé indépendants de scipy.signal
        # et de ses détections optionnelles de bibliothèques de tenseurs.
        from scipy.signal import find_peaks

        smooth = np.convolve(
            np.log1p(counts), np.array([1, 2, 3, 2, 1]) / 9, mode="same"
        )
        peak = int(np.argmax(smooth))
        height = float(smooth[peak])
        left = right = peak
        while left > 0 and smooth[left - 1] >= height * LOG_PEAK_HEIGHT_FRACTION:
            left -= 1
        while (
            right < len(counts) - 1
            and smooth[right + 1] >= height * LOG_PEAK_HEIGHT_FRACTION
        ):
            right += 1
        cdf = np.cumsum(counts, dtype=np.float64)
        cdf /= max(float(cdf[-1]), 1)
        positions = (np.arange(len(counts)) + 0.5) / len(counts)
        low, q25, median, q75, high = np.interp(
            [self.q_low / 100, 0.25, 0.5, 0.75, self.q_high / 100], cdf, positions
        )
        peaks, _ = find_peaks(
            smooth,
            prominence=max(height * LOG_PEAK_PROMINENCE_FRACTION, 0.1),
            distance=max(2, len(counts) // 128),
        )
        right_tail = np.diff(smooth[peak : right + 1])
        return {
            "peak": (peak + 0.5) / len(counts),
            "peak_height_log": height,
            "peak_left": left / len(counts),
            "peak_right": (right + 1) / len(counts),
            "peak_width": (right - left + 1) / len(counts),
            "peak_ambiguous": bool(
                height == 0
                or left == 0
                or right == len(counts) - 1
                or np.count_nonzero(counts) < 3
            ),
            "significant_peaks": len(peaks),
            "right_tail_roughness": float(np.mean(abs(right_tail)))
            if right_tail.size
            else 0.0,
            "q_low": float(low),
            "q_high": float(high),
            "range_width": float(high - low),
            "median": float(median),
            "iqr": float(q75 - q25),
        }

    def analyze(self, data: np.ndarray, backend: ComputeBackend) -> HistogramState:
        """Mesure les histogrammes RGB/Y et les quantiles effectifs sur le moteur choisi."""
        xp = backend.xp
        light = luminance(data, self.color_mode, xp=xp)
        counts = backend.numpy(xp.histogram(light, bins=self.bins, range=(0, 1))[0])
        rgb = [
            backend.numpy(xp.histogram(c, bins=self.bins, range=(0, 1))[0]).tolist()
            for c in data
        ]
        metrics = self.from_counts(counts)
        quantiles = backend.numpy(
            xp.percentile(light, [self.q_low, 25, 50, 75, self.q_high])
        )
        dark_pairs = (light[:, 1:] <= float(quantiles[1])) & (
            light[:, :-1] <= float(quantiles[1])
        )
        differences = (light[:, 1:] - light[:, :-1])[dark_pairs]
        noise_samples = int(differences.size)
        noise = None
        if noise_samples >= 32:
            center = xp.median(differences)
            noise = float(
                (1.4826 * xp.median(xp.abs(differences - center)) / np.sqrt(2)).item()
            )
        metrics.update(
            {
                "q_low": float(quantiles[0]),
                "q_high": float(quantiles[-1]),
                "range_width": float(quantiles[-1] - quantiles[0]),
                "median": float(quantiles[2]),
                "iqr": float(quantiles[3] - quantiles[1]),
                "black_fraction_rgb": [float(xp.mean(c <= 0).item()) for c in data],
                "white_fraction_rgb": [float(xp.mean(c >= 1).item()) for c in data],
                "black_fraction_y": float(xp.mean(light <= 0).item()),
                "white_fraction_y": float(xp.mean(light >= 1).item()),
                "nonfinite_count": int(xp.count_nonzero(~xp.isfinite(data)).item()),
                "background_noise_proxy": noise,
                "noise_sample_count": noise_samples,
            }
        )
        return HistogramState(counts, metrics, rgb)

    def blackpoint(
        self, data: np.ndarray, backend: ComputeBackend,
    ) -> tuple[float, str]:
        """Place BP sur le minimum RGB strictement positif, sans marge de classe.

        Les zéros existants sont ignorés. Le même BP s'applique aux trois canaux.
        Un support positif constant est laissé intact pour ne pas l'annuler.
        Cette règle est indépendante de la résolution et de l'ambiguïté du pic.
        """
        xp = backend.xp
        point = float(xp.min(xp.where(data > 0, data, xp.inf)).item())
        maximum = float(xp.max(data).item())
        if not np.isfinite(point) or point >= maximum or point >= 1:
            return 0.0, "empty or constant positive support"
        return point, "first strictly positive RGB intensity"


class GHSOptimizer(processor):
    """Recherche RANGE indépendante de StarNet et du rendu stellaire."""

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie le moteur AutoGHS sans étoiles."""
        return "ghs"

    @classmethod
    def parameter_children(cls) -> tuple[type[processor], ...]:
        """Déclare le moteur de recherche de présentation finale."""
        return (ProtectedGHSFinalizer,)

    @staticmethod
    def range_strategy(value: str) -> str:
        """Accepte les anciens choix CLI/JSON et les normalise vers RANGE seul.

        Le paramètre caché garde les anciennes commandes utilisables. Il ne
        réactive ni PEAK ni le double calcul. Les autres valeurs sont refusées.
        """
        if value not in ('range', 'both', 'peak'):
            raise ValueError('Stratégie GHS inconnue ; seul RANGE est exécuté')
        return 'range'

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare les réglages locaux de recherche, de couleur et de calcul."""
        parser.add_argument(
            "--ghs-strategy", type=cls.range_strategy, default="range",
            help=argparse.SUPPRESS,
        )
        ComputeConfig.add_arguments(parser)
        parser.add_argument("--ghs-max-iterations", type=int, default=8)
        ProtectedGHSFinalizer.add_arguments(parser)
        parser.add_argument(
            "--ghs-dynamic-target", type=float, default=1.0,
            help="Étendue RGB minimale visée sur [0,1] après GHS (ex. 0.8)",
        )
        parser.add_argument(
            "--ghs-dynamic-max-iterations", type=int, default=32,
            help="Nombre maximal de passages d’expansion de la dynamique",
        )
        parser.add_argument(
            "--ghs-histogram-strength", type=float, default=0.7,
            help="Égalisation partielle de l’histogramme : 0 désactive, 1 maximale",
        )
        parser.add_argument(
            "--ghs-histogram-clip", type=float, default=4.0,
            help="Plafond des effectifs pour limiter le contraste de l’égalisation",
        )
        parser.add_argument("--ghs-bins", type=int, default=4096)
        parser.add_argument("--ghs-q-low", type=float, default=0.1)
        parser.add_argument("--ghs-q-high", type=float, default=99.9)
        parser.add_argument("--ghs-min-gain", type=float, default=0.01)
        parser.add_argument(
            "--ghs-plateau-fraction",
            type=float,
            default=0.2,
            help="Fraction du meilleur gain marginal définissant le plateau",
        )
        parser.add_argument("--ghs-max-clip", type=float, default=0.001)
        parser.add_argument("--ghs-max-noise-gain", type=float, default=30.0)
        parser.add_argument(
            "--ghs-color-mode", choices=("human", "even"), default="human"
        )
        parser.add_argument(
            "--ghs-chroma",
            choices=("off", "balanced", "enhanced"),
            default="off",
            help="Chroma RGB expérimentale ; off pour la référence Siril",
        )

    @staticmethod
    def options(args: argparse.Namespace) -> argparse.Namespace:
        """Résout les défauts et valide toutes les options même sur arrêt immédiat."""
        parser = argparse.ArgumentParser(add_help=False)
        GHSOptimizer.add_arguments(parser)
        defaults = vars(parser.parse_args([]))
        options = argparse.Namespace(
            **{name: getattr(args, name, value) for name, value in defaults.items()}
        )
        options.ghs_strategy = GHSOptimizer.range_strategy(options.ghs_strategy)
        if (
            not isinstance(options.ghs_max_iterations, int)
            or not 1 <= options.ghs_max_iterations <= 32
        ):
            raise ValueError("ghs-max-iterations doit être compris entre 1 et 32")
        if (
            not np.isfinite(
                [
                    options.ghs_min_gain,
                    options.ghs_max_clip,
                    options.ghs_max_noise_gain,
                    options.ghs_plateau_fraction,
                ]
            ).all()
            or not 0 < options.ghs_min_gain < 1
            or not 0 <= options.ghs_max_clip <= 0.01
            or options.ghs_max_noise_gain < 1
            or not 0 < options.ghs_plateau_fraction < 1
        ):
            raise ValueError("Seuils GHS invalides")
        if not np.isfinite(options.ghs_dynamic_target) or not (
            0 < options.ghs_dynamic_target <= 1
        ):
            raise ValueError("ghs-dynamic-target doit être dans ]0,1]")
        if (
            not isinstance(options.ghs_dynamic_max_iterations, int)
            or not 1 <= options.ghs_dynamic_max_iterations <= 64
        ):
            raise ValueError("ghs-dynamic-max-iterations doit être compris entre 1 et 64")
        if (
            not np.isfinite([options.ghs_histogram_strength, options.ghs_histogram_clip]).all()
            or not 0 <= options.ghs_histogram_strength <= 1
            or not 1 <= options.ghs_histogram_clip <= 16
        ):
            raise ValueError("Paramètres d’égalisation GHS invalides")
        if options.ghs_chroma not in ("off", "balanced", "enhanced"):
            raise ValueError("Politique de chroma inconnue")
        if not np.isfinite(options.ghs_final_strength) or not (
            0 <= options.ghs_final_strength <= 10
        ):
            raise ValueError("ghs-final-strength doit être compris entre 0 et 10")
        if options.ghs_final_mode not in ("auto", "fixed"):
            raise ValueError("ghs-final-mode doit être auto ou fixed")
        if (not np.isfinite(options.ghs_highlight_retention)
                or not 0 < options.ghs_highlight_retention <= 1):
            raise ValueError("ghs-highlight-retention doit être dans ]0,1]")
        if (not np.isfinite(options.ghs_log_rms_weight)
                or not 0 <= options.ghs_log_rms_weight <= 10):
            raise ValueError("ghs-log-rms-weight doit être dans [0,10]")
        return options

    def final_stretch(
        self, data: np.ndarray, analyzer: HistogramAnalyzer,
        backend: ComputeBackend, strength: float, mode: str = "auto",
        retention: float = 0.9, stars: np.ndarray | None = None,
        headroom: float = 0.95,
        rms_weight: float = DEFAULT_LOG_RMS_WEIGHT,
    ) -> tuple[np.ndarray, JSONReport]:
        """Délègue la dernière passe à la recherche D/B/SP/HP protégée.

        strength borne D en mode auto, ou fixe D en mode fixed ; zéro désactive.
        retention est la fraction de contraste brillant à conserver dans ]0,1].
        stars permet de vérifier aussi la recomposition screen à étoiles fixes.
        rms_weight pondère le gain relatif de régularité logarithmique ; dans
        [0,10], deux par défaut, zéro désactive sa contribution au classement.
        Le moteur conserve l'identité si aucun candidat n'améliore le signal
        diffus sous contraintes. Les erreurs de données/backend sont propagées.
        Voir docs/scripts/postProcess/ghs-highlight-protection.md.
        """
        if not np.isfinite(strength) or not 0 <= strength <= 10:
            raise ValueError("ghs-final-strength doit être compris entre 0 et 10")
        output, report = ProtectedGHSFinalizer().search(
            data, analyzer, backend, strength, mode, retention, stars, headroom, rms_weight,
        )
        parameters = report["parameters"]
        LOGGER.info(
            "GHS final %s : %s ; D=%.3f B=%.2f SP=%.5f HP=%.5f",
            mode, report["status"], parameters["D"], parameters["B"],
            parameters["SP"], parameters["HP"],
        )
        return output, report

    def balance_histogram(
        self, data: np.ndarray, analyzer: HistogramAnalyzer,
        backend: ComputeBackend, strength: float, clip_limit: float,
    ) -> tuple[np.ndarray, JSONReport]:
        """Étale les intensités par une CDF plafonnée mélangée à l'identité.

        Le plafond vaut clip_limit fois l'effectif moyen des classes ; l'excès
        est redistribué uniformément. La CDF bornée 0→0, 1→1 limite ainsi la pente
        de la courbe. strength=0 désactive cette phase globale, sans filtre spatial.
        Le gain commun aux RGB préserve les rapports de couleur ; il est réduit
        si nécessaire pour garder le canal maximal <=1. Une image de luminance
        constante est préservée. Le rapport mesure aussi l'amplification du bruit.
        """
        state = analyzer.analyze(data, backend)
        before = state.metrics
        if strength == 0 or float(before["range_width"]) <= 1e-7:
            return data, {
                "status": "disabled" if strength == 0 else "constant distribution",
                "strength": strength, "clip_limit": clip_limit,
                "before": before, "after": before,
            }
        counts = state.counts.astype(np.float64)
        cap = clip_limit * float(counts.mean())
        limited = np.minimum(counts, cap)
        limited += (counts.sum() - limited.sum()) / len(counts)
        cdf = np.r_[0.0, np.cumsum(limited)]
        cdf /= cdf[-1]
        xp = backend.xp
        light = luminance(data, analyzer.color_mode, xp=xp)
        mapped = xp.interp(
            light, xp.asarray(np.linspace(0, 1, len(cdf))), xp.asarray(cdf),
        ).astype(data.dtype)
        target = (1 - strength) * light + strength * mapped
        gain = target / xp.maximum(light, 1e-30)
        maximum = xp.max(data, axis=0)
        gain = xp.minimum(gain, 1 / xp.maximum(maximum, 1e-30))
        output = data * gain[None]
        after = analyzer.analyze(output, backend).metrics
        return output, {
            "status": "applied", "method": "clipped global CDF mixed with identity",
            "strength": strength, "clip_limit": clip_limit,
            "maximum_curve_slope": (1 - strength) + strength * (clip_limit + 1),
            "before": before, "after": after,
            "gamut_limited_fraction": float(xp.mean(
                target / xp.maximum(light, 1e-30) > 1 / xp.maximum(maximum, 1e-30)
            ).item()),
        }

    def expand_dynamic_range(
        self, data: np.ndarray, analyzer: HistogramAnalyzer,
        backend: ComputeBackend, target: float, max_iterations: int,
    ) -> tuple[np.ndarray, JSONReport, list[JSONReport]]:
        """Complète GHS par une expansion linéaire commune aux canaux RGB.

        La couverture est max(RGB)-min(RGB), en unités normalisées, sans quantile.
        Chaque passage recale BP sur le premier RGB positif puis multiplie tous
        les canaux par un gain <=2, borné pour atteindre la cible sans dépasser 1.
        La borne d'itérations et une tolérance float32 empêchent les rebouclages.
        Les gardes de bruit de la recherche GHS ne bloquent pas cette expansion
        demandée ; les métriques finales permettent d'en mesurer les effets.
        Retourne l'image, le rapport de boucle et les histogrammes intermédiaires.
        Les données constantes restent intactes, sans dynamique inventée.
        """
        xp = backend.xp
        initial_width = float((xp.max(data) - xp.min(data)).item())
        current = data
        records: list[JSONReport] = []
        histograms: list[JSONReport] = []
        reason = "max_iterations"
        tolerance = float(8 * np.finfo(np.float32).eps)
        for iteration in range(max_iterations):
            width = float((xp.max(current) - xp.min(current)).item())
            if width >= target - tolerance:
                reason = "target reached"
                break
            if width <= 0:
                reason = "constant distribution"
                break
            bp, bp_reason = analyzer.blackpoint(current, backend)
            shifted = linear_blackpoint(current, bp, xp=xp) if bp > 0 else current
            maximum = float(xp.max(shifted).item())
            shifted_width = float((xp.max(shifted) - xp.min(shifted)).item())
            if maximum <= 0 or shifted_width <= 0:
                reason = "constant distribution"
                break
            gain = min(2.0, target / shifted_width, 1.0 / maximum)
            candidate = shifted * gain
            next_width = float((xp.max(candidate) - xp.min(candidate)).item())
            if next_width <= width:
                reason = "no dynamic improvement"
                break
            current = candidate
            state = analyzer.analyze(current, backend)
            histograms.append(state.report())
            records.append({
                "iteration": iteration + 1, "BP": bp, "BP_reason": bp_reason,
                "gain": gain, "width_before": width, "width_after": next_width,
            })
            LOGGER.info(
                "AutoGHS dynamique : passage %d BP=%.6f gain=%.3f étendue=%.6f cible=%.3f",
                iteration + 1, bp, gain, next_width, target,
            )
        final_width = float((xp.max(current) - xp.min(current)).item())
        reached = bool(final_width >= target - tolerance)
        if reached:
            reason = "target reached"
        return current, {
            "metric": "max(RGB)-min(RGB)", "target": target,
            "initial_width": initial_width, "final_width": final_width,
            "target_reached": reached, "stop_reason": reason,
            "max_iterations": max_iterations, "iterations": records,
        }, histograms

    def optimize(
        self,
        data: np.ndarray,
        strategy: str,
        args: argparse.Namespace,
        backend: ComputeBackend,
        rendered_stars: np.ndarray | None = None, headroom: float = 0.95,
    ) -> tuple[np.ndarray, JSONReport]:
        """Optimise l'étendue robuste RANGE par prédiction puis contrôle RGB.

        strategy doit valoir range ; cet argument conserve la signature des
        appels internes existants, sans autre recherche disponible.
        Le plateau est le premier ralentissement relatif du gain de largeur à
        moins de 20 % du gain marginal maximal observé. Trois finalistes voisins
        et les meilleurs scores prédits sont vérifiés sur les vrais pixels RGB.
        BP est recalé avant chaque recherche. L’égalisation, l’expansion de
        dynamique et la chroma précèdent la recherche finale D/B/SP/HP protégée,
        appliquée au fond avant fusion stellaire. rendered_stars et headroom
        permettent d'en vérifier aussi la recomposition à étoiles fixes.
        Une exception backend n'est jamais
        assimilée à une convergence. Voir auto-ghs-algorithm.md.
        """
        validate_rgb(data)
        if strategy != "range":
            raise ValueError("Seule la stratégie range est disponible")
        options = self.options(args)
        analyzer = HistogramAnalyzer(
            options.ghs_bins,
            options.ghs_q_low,
            options.ghs_q_high,
            options.ghs_color_mode,
        )
        xp = backend.xp
        current = xp.asarray(data, dtype=xp.float32)
        initial = analyzer.analyze(current, backend)
        state = initial
        records: list[JSONReport] = []
        blackpoint_records: list[JSONReport] = []
        histograms = [initial.report()]
        stop = "max_iterations"
        metric = "range_width"
        baseline_iqr = max(float(initial.metrics["iqr"]), 1e-12)
        baseline_noise = initial.metrics["background_noise_proxy"]
        for iteration in range(options.ghs_max_iterations):
            if float(state.metrics["range_width"]) <= 1e-7:
                stop = "constant distribution"
                break
            bp_used, bp_reason = analyzer.blackpoint(current, backend)
            blackpoint_records.append({
                "iteration": iteration + 1, "BP": bp_used, "reason": bp_reason,
            })
            if bp_used > 0:
                current = linear_blackpoint(current, bp_used, xp=xp)
                state = analyzer.analyze(current, backend)
                histograms.append(state.report())
            sp = max(0.0, float(state.metrics["peak"]) - 1.5 / options.ghs_bins)
            concentration = CONCENTRATION_SCHEDULE[
                min(iteration, len(CONCENTRATION_SCHEDULE) - 1)
            ]
            parameters = {"B": concentration, "SP": sp, "LP": 0.0, "HP": 1.0}
            centers = (np.arange(options.ghs_bins) + 0.5) / options.ghs_bins
            candidates: list[JSONReport] = []
            previous_width = float(state.metrics[metric])
            best_marginal = 0.0
            plateau_index = None
            for strength in np.arange(
                CANDIDATE_D_STEP,
                CANDIDATE_D_MAXIMUM + CANDIDATE_D_STEP / 2,
                CANDIDATE_D_STEP,
            ):
                mapped = ghs_transfer(centers, float(strength), **parameters)
                counts = np.histogram(
                    mapped, bins=options.ghs_bins, range=(0, 1), weights=state.counts
                )[0]
                prediction = analyzer.from_counts(counts)
                width = float(prediction[metric])
                marginal = (width - previous_width) / CANDIDATE_D_STEP
                if (
                    plateau_index is None
                    and best_marginal > 0
                    and marginal < options.ghs_plateau_fraction * best_marginal
                ):
                    plateau_index = max(0, len(candidates) - 1)
                best_marginal = max(best_marginal, marginal)
                previous_width = width
                candidates.append(
                    {
                        "D": float(strength),
                        **parameters,
                        "predicted_width": width,
                        "predicted_peaks": prediction["significant_peaks"],
                        "predicted_median": prediction["median"],
                        "predicted_iqr": prediction["iqr"],
                        "predicted_marginal": marginal,
                    }
                )
            if plateau_index is None:
                plateau_index = int(
                    np.argmax([c["predicted_width"] for c in candidates])
                )
            # Reclasser sur les vrais histogrammes RGB ; la prédiction n'est pas un oracle.
            indices = {
                max(0, plateau_index - 1),
                plateau_index,
                min(len(candidates) - 1, plateau_index + 1),
                0,
            }
            admissible = [
                index
                for index, candidate in enumerate(candidates)
                if candidate["predicted_median"] <= MAX_BACKGROUND_MEDIAN
                and candidate["predicted_iqr"] / baseline_iqr
                <= options.ghs_max_noise_gain
            ]
            ranked = sorted(
                admissible, key=lambda index: candidates[index]["predicted_width"]
            )
            indices.update(ranked[-2:])
            best = None
            best_width = float(state.metrics[metric])
            for index in sorted(indices):
                candidate = candidates[index]
                transformed = ghs_rgb(
                    current,
                    candidate["D"],
                    **parameters,
                    color_mode=options.ghs_color_mode,
                    xp=xp,
                )
                measured = analyzer.analyze(transformed, backend)
                metrics = measured.metrics
                rejection = None
                if metrics["nonfinite_count"]:
                    raise RuntimeError("GHS a produit des pixels non finis")
                if (
                    max(metrics["white_fraction_rgb"])
                    > max(state.metrics["white_fraction_rgb"]) + options.ghs_max_clip
                ):
                    rejection = "highlight clipping"
                elif metrics["median"] > MAX_BACKGROUND_MEDIAN:
                    rejection = "background too bright"
                elif metrics["iqr"] / baseline_iqr > options.ghs_max_noise_gain or (
                    baseline_noise is not None
                    and baseline_noise > 0
                    and metrics["background_noise_proxy"] is not None
                    and metrics["background_noise_proxy"] / baseline_noise
                    > options.ghs_max_noise_gain
                ):
                    rejection = "background noise/IQR amplification limit"
                elif metrics["significant_peaks"] > max(
                    2, int(state.metrics["significant_peaks"]) + 1
                ):
                    rejection = "histogram bifurcation"
                # L'écart inter-quantiles supérieur ne doit pas s'effondrer en hautes lumières.
                elif (
                    metrics["q_high"] > HIGHLIGHT_BOUNDARY
                    and metrics["range_width"]
                    < state.metrics["range_width"] * MIN_HIGHLIGHT_WIDTH_RATIO
                ):
                    rejection = "highlight compression"
                candidate.update(
                    {
                        "measured": metrics,
                        "rejection": rejection,
                        "prediction_error": float(metrics[metric])
                        - candidate["predicted_width"],
                    }
                )
                width = float(metrics[metric])
                if rejection is None and width > best_width * (
                    1 + options.ghs_min_gain
                ):
                    best, best_width = (index, transformed, measured), width
            if best is None:
                records.append(
                    {
                        "iteration": iteration + 1,
                        "candidates": candidates,
                        "accepted": None,
                        "BP": bp_used,
                        "BP_reason": bp_reason,
                    }
                )
                stop = "no admissible improvement"
                break
            index, transformed, measured = best
            records.append(
                {
                    "iteration": iteration + 1,
                    "candidates": candidates,
                    "accepted": candidates[index],
                    "BP": bp_used,
                    "BP_reason": bp_reason,
                    "metrics": measured.metrics,
                }
            )
            current, state = transformed, measured
            histograms.append(state.report())
            LOGGER.info(
                "AutoGHS %s : itération %d D=%.2f B=%.1f SP=%.6f BP=%.6f",
                strategy,
                iteration + 1,
                candidates[index]["D"],
                concentration,
                sp,
                bp_used,
            )
        post_ghs_metrics = state.metrics
        current, histogram_balance = self.balance_histogram(
            current, analyzer, backend, options.ghs_histogram_strength,
            options.ghs_histogram_clip,
        )
        if histogram_balance["status"] == "applied":
            histograms.append(analyzer.analyze(current, backend).report())
        current, dynamic_report, dynamic_histograms = self.expand_dynamic_range(
            current, analyzer, backend, options.ghs_dynamic_target,
            options.ghs_dynamic_max_iterations,
        )
        histograms.extend(dynamic_histograms)
        state = analyzer.analyze(current, backend)
        before_chroma = backend.numpy(current).astype(np.float32)
        if options.ghs_chroma != "off":
            current = render_chroma(current, options.ghs_chroma, xp=xp)
            state = analyzer.analyze(current, backend)
            histograms.append(state.report())
        chroma_diagnostics = color_drift(before_chroma, backend.numpy(current))
        current, presentation_stretch = self.final_stretch(
            current, analyzer, backend, options.ghs_final_strength,
            options.ghs_final_mode, options.ghs_highlight_retention,
            rendered_stars, headroom,
            options.ghs_log_rms_weight,
        )
        if presentation_stretch["status"] == "applied":
            state = analyzer.analyze(current, backend)
            histograms.append(state.report())
        output = backend.numpy(current).astype(np.float32)
        validate_rgb(output)
        return output, {
            "status": "SUCCESS" if len(histograms) > 1 else "NO_IMPROVEMENT",
            "strategy": strategy,
            "stop_reason": stop,
            "iterations": records,
            "histogram_balance": histogram_balance,
            "dynamic_range": dynamic_report,
            "presentation_stretch": presentation_stretch,
            "blackpoint_iterations": blackpoint_records,
            "post_ghs": post_ghs_metrics,
            "histograms": histograms,
            "initial": initial.metrics,
            "final": state.metrics,
            "color_mode": options.ghs_color_mode,
            "clip_mode": "rgbblend",
            "chroma": options.ghs_chroma,
            "parameters": vars(options),
            "chroma_diagnostics": chroma_diagnostics,
            "colorimetry": "human weights are an intensity proxy, primaries unknown",
            "noise_metric": (
                "MAD of horizontal differences in lower-quartile pairs / sqrt(2); "
                "IQR guard; both are proxies, not physical noise estimates"
            ),
        }

    def post_process(
        self,
        input_path: Path,
        output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Traite un fond seul avec RANGE et écrit un FITS distinct de la source."""
        args = self.options(self._get_args(args))
        data = normalized_planes(input_path)
        backend = ComputeBackend(args.compute_backend, data.nbytes * 16)
        result, report = self.optimize(data, "range", args, backend)
        output = Path(output_path).with_suffix(".fits")
        if Path(input_path).resolve() in (
            output.resolve(),
            Path(output_path).resolve(),
        ):
            raise ValueError("La sortie GHS ne peut pas remplacer la source")
        output.parent.mkdir(parents=True, exist_ok=True)
        write_rgb(Path(input_path), output, result, nonlinear=True, role="STARLESS")
        report["output_image"] = str(output.resolve())
        Path(output_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
