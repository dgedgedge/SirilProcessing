#!/usr/bin/env python3
"""Build a solar eclipse GIF from monochrome FITS frames.

This implementation follows docs/SOLAR_ECLIPSE_GIF.md:
- calibrate a sky/background threshold from manual value or dark frames;
- build a full-sun session model from full-sun or nearly-full-sun frames;
- center frames by detected solar circle, with CUDA/CuPy mask-correlation fallback;
- crop every useful frame to a fixed square;
- group acquisitions into time slots and write an animated GIF.
"""

from __future__ import annotations

import argparse
import logging
import math
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
from astropy.io import fits
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage
from skimage import filters, measure, morphology, segmentation
from skimage.registration import phase_cross_correlation

try:
    import cupy as cp
except Exception:
    cp = None


FITS_EXTENSIONS = {".fit", ".fits", ".fts"}


@dataclass
class Frame:
    """One source FITS frame, sorted by acquisition time."""

    index1: int
    path: Path
    timestamp: float
    image: np.ndarray
    exposure_s: float | None = None
    gain: float | None = None


@dataclass
class FullSunDetection:
    """Detection result for a full/nearly-full solar disk frame."""

    center_x: float
    center_y: float
    radius: float
    threshold_mask: np.ndarray
    final_mask: np.ndarray
    watershed_mask: np.ndarray
    watershed_boundaries: np.ndarray
    watershed_computed: bool
    least_squares_circle: tuple[float, float, float]
    ransac_circle: tuple[float, float, float] | None
    ransac_confidence: float | None
    ransac_inliers: int
    ransac_candidates: int


@dataclass
class CircleFitResult:
    """Circle fits computed from solar limb candidates."""

    selected_circle: tuple[float, float, float]
    least_squares_circle: tuple[float, float, float]
    ransac_circle: tuple[float, float, float] | None
    ransac_confidence: float | None
    ransac_inliers: int
    ransac_candidates: int


@dataclass
class SolarModel:
    """Session solar model built from full-sun calibration frames."""

    image: np.ndarray
    mask: np.ndarray
    center_x: float
    center_y: float
    radius: float
    side: int
    source_indices: list[int]


@dataclass
class ProcessedFrame:
    """Cropped and centered movie frame."""

    source: Frame
    image: np.ndarray
    center_x: float
    center_y: float


@dataclass
class StackedFrame:
    """GIF slot frame with acquisition metadata averaged from source frames."""

    image: np.ndarray
    source_indices: list[int]
    exposure_s: float | None
    gain: float | None
    exposure_values: list[float | None]
    gain_values: list[float | None]
    source_count: int


@dataclass
class CorrelationEngine:
    """Execution backend used by correlation-heavy operations."""

    use_gpu: bool
    reason: str


RADIUS_COHERENCE_TOLERANCE = 0.20


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a GIF movie from monochrome solar eclipse FITS frames.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input-dir", required=True, help="Repertoire des images sources FITS.")
    parser.add_argument("--dark-calib-frames", default=None, help="Liste 1-based d'images dark: 1-10,15.")
    parser.add_argument(
        "--manual-seuil-fond-du-ciel",
        type=float,
        default=None,
        help="Seuil manuel du fond du ciel. Les pixels inferieurs sont ignores.",
    )
    parser.add_argument("--full-sun-frames", default=None, help="Liste 1-based d'images soleil plein.")
    parser.add_argument(
        "--first-nearly-full-sun-frames",
        type=int,
        default=10,
        help="Nombre de premieres images utilisees comme soleil presque plein si --full-sun-frames est absent.",
    )
    parser.add_argument("--exclude-frames", default=None, help="Liste 1-based d'images a exclure.")
    parser.add_argument(
        "--debug-dir",
        default=None,
        help="Repertoire de debug. Par defaut: <input-dir>/debug.",
    )
    parser.add_argument("--debug-full-sun", action="store_true", help="Exporte le debug du modele plein Soleil.")
    parser.add_argument("--debug-shifts", action="store_true", help="Exporte le debug du calcul des decalages.")
    parser.add_argument("--debug-gif-frames", action="store_true", help="Exporte les images effectivement incluses dans le GIF.")
    parser.add_argument("--debug-watershed", action="store_true", help="Calcule et affiche le watershed dans les images de debug.")
    parser.add_argument("--rotate-clockwise-deg", type=float, default=0.0, help="Rotation horaire appliquee aux crops.")
    parser.add_argument("--target-duration", type=float, default=30.0, help="Duree cible du GIF en secondes.")
    parser.add_argument("--output", required=True, help="Chemin du GIF de sortie.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def select_correlation_engine() -> CorrelationEngine:
    """Use CUDA/CuPy when it is genuinely usable, otherwise keep CPU behavior."""
    if cp is None:
        return CorrelationEngine(False, "CuPy indisponible")
    try:
        device_count = int(cp.cuda.runtime.getDeviceCount())
        if device_count <= 0:
            return CorrelationEngine(False, "aucun GPU CUDA detecte")
        device = cp.cuda.Device()
        props = cp.cuda.runtime.getDeviceProperties(device.id)
        name = props.get("name", b"GPU CUDA")
        if isinstance(name, bytes):
            name = name.decode("utf-8", errors="replace")
        return CorrelationEngine(True, f"GPU CUDA {device.id}: {name}")
    except Exception as exc:
        return CorrelationEngine(False, f"GPU CUDA non utilisable ({exc})")


def parse_frame_selection(spec: str | None, frame_count: int) -> set[int]:
    """Parse a 1-based frame selection into 0-based indices."""
    if not spec:
        return set()

    selected: set[int] = set()
    for token in (part.strip() for part in spec.split(",")):
        if not token:
            continue
        if "-" in token:
            start_s, end_s = token.split("-", 1)
            if not start_s.strip().isdigit() or not end_s.strip().isdigit():
                raise ValueError(f"Selection invalide: {token!r}")
            start = int(start_s)
            end = int(end_s)
            if start > end:
                start, end = end, start
            for idx1 in range(start, end + 1):
                if 1 <= idx1 <= frame_count:
                    selected.add(idx1 - 1)
        else:
            if not token.isdigit():
                raise ValueError(f"Selection invalide: {token!r}")
            idx1 = int(token)
            if 1 <= idx1 <= frame_count:
                selected.add(idx1 - 1)
    return selected


def find_fits_files(input_dir: Path) -> list[Path]:
    return sorted(path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() in FITS_EXTENSIONS)


def parse_timestamp(header, fallback_path: Path) -> float:
    for key in ("DATE-OBS", "DATEOBS", "DATE"):
        value = header.get(key)
        if not value:
            continue
        value_s = str(value).strip()
        candidates = [value_s]
        if value_s.endswith("Z"):
            candidates.append(value_s[:-1] + "+00:00")
        for candidate in candidates:
            try:
                dt = datetime.fromisoformat(candidate)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return float(dt.timestamp())
            except ValueError:
                pass
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(value_s, fmt).replace(tzinfo=timezone.utc)
                return float(dt.timestamp())
            except ValueError:
                pass
    return float(fallback_path.stat().st_mtime)


def optional_header_float(header, keys: Iterable[str]) -> float | None:
    for key in keys:
        value = header.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def ensure_2d_float(data: np.ndarray) -> np.ndarray:
    if data is None:
        raise ValueError("FITS image has no data")
    arr = np.asarray(data, dtype=np.float32)
    while arr.ndim > 2:
        arr = arr[0]
    if arr.ndim != 2:
        raise ValueError(f"Unsupported FITS shape: {arr.shape}")
    return np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32, copy=False)


def load_frames(input_dir: Path) -> list[Frame]:
    files = find_fits_files(input_dir)
    if len(files) < 2:
        raise ValueError(f"Need at least 2 FITS files in {input_dir}")

    frames: list[Frame] = []
    for path in files:
        try:
            with fits.open(path, memmap=False) as hdul:
                header = hdul[0].header
                image = ensure_2d_float(hdul[0].data)
                timestamp = parse_timestamp(header, path)
                exposure_s = optional_header_float(header, ("EXPTIME", "EXPOSURE", "EXP_TIME", "EXPO"))
                gain = optional_header_float(header, ("GAIN", "EGAIN", "OFFSETGAIN", "ISO"))
            frames.append(Frame(0, path, timestamp, image, exposure_s, gain))
        except Exception as exc:
            logging.warning("Image ignoree %s: %s", path, exc)

    if len(frames) < 2:
        raise ValueError("Not enough readable FITS frames")

    frames.sort(key=lambda frame: frame.timestamp)
    for i, frame in enumerate(frames, start=1):
        frame.index1 = i
    return frames


def robust_display_u8(image: np.ndarray, low: float = 1.0, high: float = 99.7) -> np.ndarray:
    finite = image[np.isfinite(image)]
    if finite.size == 0:
        return np.zeros_like(image, dtype=np.uint8)
    lo = float(np.percentile(finite, low))
    hi = float(np.percentile(finite, high))
    if hi <= lo:
        return np.zeros_like(image, dtype=np.uint8)
    return np.clip((image - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)


def compute_sky_threshold(
    frames: list[Frame],
    dark_indices: set[int],
    manual_threshold: float | None,
) -> float:
    if manual_threshold is not None:
        logging.info("Seuil_fond_du_ciel manuel: %.3f", manual_threshold)
        return float(manual_threshold)
    if dark_indices:
        levels = [float(np.percentile(frames[i].image, 99.0)) for i in sorted(dark_indices)]
        threshold = float(np.mean(levels))
        logging.info("Seuil_fond_du_ciel depuis %d dark(s): %.3f", len(levels), threshold)
        return threshold
    logging.info("Seuil_fond_du_ciel par defaut: 0")
    return 0.0


def largest_component(mask: np.ndarray) -> np.ndarray:
    labels = measure.label(mask.astype(bool), connectivity=2)
    if labels.max() == 0:
        return np.zeros_like(mask, dtype=bool)
    counts = np.bincount(labels.ravel())
    counts[0] = 0
    return labels == int(np.argmax(counts))


def remove_components_smaller_than(mask: np.ndarray, min_size: int) -> np.ndarray:
    labels = measure.label(mask.astype(bool), connectivity=2)
    if labels.max() == 0:
        return np.zeros_like(mask, dtype=bool)
    counts = np.bincount(labels.ravel())
    keep = counts >= int(min_size)
    keep[0] = False
    return keep[labels]


def disk_radius_for_shape(shape: tuple[int, int], fraction: float = 0.018) -> int:
    return max(3, int(round(min(shape) * fraction)))


def solar_main_component_mask(
    image: np.ndarray,
    sky_threshold: float,
    gaussian_sigma: float = 1.2,
    threshold_factor: float = 0.65,
    closing_radius_fraction: float = 0.018,
    min_component_area_fraction: float = 0.0007,
    min_component_area_px: int = 64,
    fill_holes: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Return the simple threshold and main solar component masks."""
    smoothed = ndimage.gaussian_filter(image.astype(np.float32), sigma=gaussian_sigma)
    useful = smoothed[smoothed > sky_threshold]
    if useful.size < max(64, int(image.size * 0.001)):
        useful = smoothed.ravel()
    try:
        otsu = float(filters.threshold_otsu(useful))
    except Exception:
        otsu = float(np.percentile(useful, 70.0))
    threshold = max(float(sky_threshold), otsu * threshold_factor)
    simple = smoothed > threshold
    min_size = max(int(min_component_area_px), int(image.size * min_component_area_fraction))
    simple = remove_components_smaller_than(simple, min_size)
    closed = ndimage.binary_closing(simple, structure=morphology.disk(disk_radius_for_shape(image.shape, closing_radius_fraction)))
    component = largest_component(closed)
    if fill_holes:
        component = ndimage.binary_fill_holes(component)
    return simple.astype(bool), component.astype(bool)


def mask_centroid(mask: np.ndarray) -> tuple[float, float]:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        h, w = mask.shape
        return w / 2.0, h / 2.0
    return float(np.mean(xs)), float(np.mean(ys))


def fit_circle_from_mask(mask: np.ndarray, center_hint: tuple[float, float] | None = None) -> CircleFitResult:
    edge = external_limb_candidates(mask)
    ys, xs = np.nonzero(edge)
    if len(xs) < 20:
        cx, cy = mask_centroid(mask)
        area_radius = math.sqrt(max(int(mask.sum()), 1) / math.pi)
        circle = (cx, cy, float(area_radius))
        return CircleFitResult(circle, circle, None, None, 0, int(len(xs)))

    least_squares = fit_circle_least_squares(xs, ys, mask, center_hint)
    ransac_fit = fit_circle_ransac(xs, ys, mask.shape)
    if ransac_fit is not None:
        return CircleFitResult(
            ransac_fit[0],
            least_squares,
            ransac_fit[0],
            ransac_fit[1],
            ransac_fit[2],
            ransac_fit[3],
        )

    return CircleFitResult(least_squares, least_squares, None, None, 0, int(len(xs)))


def external_limb_candidates(mask: np.ndarray) -> np.ndarray:
    """Select boundary points likely to belong to the external solar crescent."""
    edge = segmentation.find_boundaries(mask, mode="inner")
    if not np.any(edge):
        return edge

    try:
        hull = morphology.convex_hull_image(mask.astype(bool))
        hull_edge = segmentation.find_boundaries(hull, mode="inner")
        tolerance = ndimage.binary_dilation(hull_edge, structure=morphology.disk(2))
        candidates = edge & tolerance
        if int(candidates.sum()) >= 20:
            return candidates
    except Exception:
        pass
    return edge


def fit_circle_ransac(xs: np.ndarray, ys: np.ndarray, shape: tuple[int, int]) -> tuple[tuple[float, float, float], float, int, int] | None:
    points = np.column_stack((xs.astype(np.float64), ys.astype(np.float64)))
    residual_threshold = max(2.0, min(shape) * 0.006)
    try:
        model, inliers = measure.ransac(
            points,
            measure.CircleModel,
            min_samples=3,
            residual_threshold=residual_threshold,
            max_trials=500,
            stop_probability=0.99,
            random_state=0,
        )
    except TypeError:
        model, inliers = measure.ransac(
            points,
            measure.CircleModel,
            min_samples=3,
            residual_threshold=residual_threshold,
            max_trials=500,
            stop_probability=0.99,
        )
    except Exception:
        return None

    if model is None or inliers is None:
        return None
    inlier_count = int(np.count_nonzero(inliers))
    candidate_count = int(points.shape[0])
    if inlier_count < max(20, int(candidate_count * 0.25)):
        return None

    try:
        cx, cy = (float(v) for v in model.center)
        radius = float(model.radius)
    except Exception:
        cx, cy, radius = (float(v) for v in model.params)
    max_reasonable_radius = max(shape) * 2.0
    if not all(np.isfinite((cx, cy, radius))) or radius <= 0 or radius > max_reasonable_radius:
        return None
    confidence = inlier_count / max(candidate_count, 1)
    return (cx, cy, radius), float(confidence), inlier_count, candidate_count


def fit_circle_least_squares(
    xs: np.ndarray,
    ys: np.ndarray,
    mask: np.ndarray,
    center_hint: tuple[float, float] | None = None,
) -> tuple[float, float, float]:
    x = xs.astype(np.float64)
    y = ys.astype(np.float64)
    a = np.column_stack((2.0 * x, 2.0 * y, np.ones_like(x)))
    b = x * x + y * y
    try:
        cx, cy, c = np.linalg.lstsq(a, b, rcond=None)[0]
        radius = math.sqrt(max(c + cx * cx + cy * cy, 1.0))
    except Exception:
        cx, cy = center_hint if center_hint is not None else mask_centroid(mask)
        radius = math.sqrt(max(int(mask.sum()), 1) / math.pi)
    return float(cx), float(cy), float(radius)


def watershed_contour_candidate(
    image: np.ndarray,
    current_mask: np.ndarray,
    sky_threshold: float,
    gaussian_sigma: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute a watershed contour candidate seeded from the current solar mask."""
    if not np.any(current_mask):
        empty = np.zeros_like(current_mask, dtype=bool)
        return empty, empty

    smoothed = ndimage.gaussian_filter(image.astype(np.float32), sigma=gaussian_sigma)
    gradient = filters.sobel(smoothed)
    seed_radius = max(2, disk_radius_for_shape(image.shape, 0.01))
    guard_radius = max(seed_radius + 1, disk_radius_for_shape(image.shape, 0.025))

    foreground = ndimage.binary_erosion(current_mask, structure=morphology.disk(seed_radius))
    if not np.any(foreground):
        foreground = current_mask.astype(bool)
    background = ~ndimage.binary_dilation(current_mask, structure=morphology.disk(guard_radius))
    background |= smoothed <= sky_threshold

    markers = np.zeros(image.shape, dtype=np.int32)
    markers[background] = 1
    markers[foreground] = 2
    labels = segmentation.watershed(gradient, markers)
    watershed_mask = labels == 2
    watershed_boundaries = segmentation.find_boundaries(labels, mode="inner")
    return watershed_mask.astype(bool), watershed_boundaries.astype(bool)


def masque_solaire_principal(
    image: np.ndarray,
    sky_threshold: float,
    debug_output_path: Path | None = None,
    debug_info: str | None = None,
    gaussian_sigma: float = 1.2,
    threshold_factor: float = 0.65,
    closing_radius_fraction: float = 0.018,
    min_component_area_fraction: float = 0.0007,
    min_component_area_px: int = 64,
    fill_holes: bool = True,
    compute_watershed: bool = False,
) -> FullSunDetection:
    """Run the shared solar-mask, center, circle and optional debug export method."""
    simple, final = solar_main_component_mask(
        image,
        sky_threshold,
        gaussian_sigma=gaussian_sigma,
        threshold_factor=threshold_factor,
        closing_radius_fraction=closing_radius_fraction,
        min_component_area_fraction=min_component_area_fraction,
        min_component_area_px=min_component_area_px,
        fill_holes=fill_holes,
    )
    cx, cy = mask_centroid(final)
    circle_fit = fit_circle_from_mask(final, (cx, cy))
    cx_fit, cy_fit, radius = circle_fit.selected_circle
    if not np.isfinite(radius) or radius <= 0:
        radius = math.sqrt(max(int(final.sum()), 1) / math.pi)
        circle_fit = CircleFitResult(
            (cx_fit, cy_fit, float(radius)),
            circle_fit.least_squares_circle,
            circle_fit.ransac_circle,
            circle_fit.ransac_confidence,
            circle_fit.ransac_inliers,
            circle_fit.ransac_candidates,
        )
    if compute_watershed:
        watershed_mask, watershed_boundaries = watershed_contour_candidate(image, final, sky_threshold, gaussian_sigma)
    else:
        watershed_mask = np.zeros_like(final, dtype=bool)
        watershed_boundaries = np.zeros_like(final, dtype=bool)
    detection = FullSunDetection(
        cx_fit,
        cy_fit,
        float(radius),
        simple,
        final,
        watershed_mask,
        watershed_boundaries,
        bool(compute_watershed),
        circle_fit.least_squares_circle,
        circle_fit.ransac_circle,
        circle_fit.ransac_confidence,
        circle_fit.ransac_inliers,
        circle_fit.ransac_candidates,
    )
    if debug_output_path is not None:
        debug_solar_main_component_image(image, detection, debug_output_path, debug_info=debug_info)
    return detection


def detect_full_sun(
    image: np.ndarray,
    sky_threshold: float,
    debug_output_path: Path | None = None,
    debug_info: str | None = None,
    compute_watershed: bool = False,
) -> FullSunDetection:
    return masque_solaire_principal(
        image,
        sky_threshold,
        debug_output_path=debug_output_path,
        debug_info=debug_info,
        compute_watershed=compute_watershed,
    )


def crop_with_padding(image: np.ndarray, center_x: float, center_y: float, side: int) -> np.ndarray:
    side = max(1, int(side))
    half = side / 2.0
    x0 = int(round(center_x - half))
    y0 = int(round(center_y - half))
    x1 = x0 + side
    y1 = y0 + side

    out = np.zeros((side, side), dtype=np.float32)
    src_x0 = max(0, x0)
    src_y0 = max(0, y0)
    src_x1 = min(image.shape[1], x1)
    src_y1 = min(image.shape[0], y1)
    if src_x1 <= src_x0 or src_y1 <= src_y0:
        return out

    dst_x0 = src_x0 - x0
    dst_y0 = src_y0 - y0
    out[dst_y0 : dst_y0 + (src_y1 - src_y0), dst_x0 : dst_x0 + (src_x1 - src_x0)] = image[src_y0:src_y1, src_x0:src_x1]
    return out


def draw_center(draw: ImageDraw.ImageDraw, center_x: float, center_y: float, color: tuple[int, int, int]) -> None:
    r = 5
    draw.ellipse((center_x - r, center_y - r, center_x + r, center_y + r), outline=color, width=2)
    draw.line((center_x - 10, center_y, center_x + 10, center_y), fill=color, width=1)
    draw.line((center_x, center_y - 10, center_x, center_y + 10), fill=color, width=1)


def draw_circle(
    draw: ImageDraw.ImageDraw,
    center_x: float,
    center_y: float,
    radius: float,
    color: tuple[int, int, int],
    width: int = 2,
) -> None:
    draw.ellipse(
        (
            center_x - radius,
            center_y - radius,
            center_x + radius,
            center_y + radius,
        ),
        outline=color,
        width=width,
    )


def debug_font(size: int = 18) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size=size)
    except Exception:
        return ImageFont.load_default()


def draw_legend(draw: ImageDraw.ImageDraw, text: str, color: tuple[int, int, int], width: int) -> None:
    font = debug_font(24)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = min(width, max(280, bbox[2] - bbox[0] + 54))
    text_h = max(40, bbox[3] - bbox[1] + 18)
    draw.rectangle((0, 0, text_w, text_h), fill=(0, 0, 0))
    draw.rectangle((10, 12, 30, text_h - 12), fill=color)
    draw.text((42, 8), text, fill=(255, 255, 255), font=font)


def draw_debug_info(draw: ImageDraw.ImageDraw, text: str | None, x: int, y: int, max_width: int) -> None:
    if not text:
        return
    font = debug_font(28)
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return
    line_height = 34
    box_width = min(max_width, max(360, max(len(line) for line in lines) * 17 + 22))
    box_height = line_height * len(lines) + 18
    draw.rectangle((x, y, x + box_width, y + box_height), fill=(0, 0, 0))
    for i, line in enumerate(lines):
        draw.text((x + 11, y + 9 + i * line_height), line, fill=(255, 255, 255), font=font)


def optional_average(values: Iterable[float | None]) -> float | None:
    known = [float(value) for value in values if value is not None and np.isfinite(float(value))]
    if not known:
        return None
    return float(np.mean(known))


def format_optional_float(value: float | None, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    return f"{value:.4g}{suffix}"


def format_optional_float_list(values: Iterable[float | None], suffix: str = "") -> str:
    return "[" + ", ".join(format_optional_float(value, suffix) for value in values) + "]"


def frame_debug_info(frame: Frame, prefix: str | None = None) -> str:
    lines = []
    if prefix:
        lines.append(prefix)
    lines.extend(
        [
            f"Frame {frame.index1}",
            f"Exposure: {format_optional_float(frame.exposure_s, ' s')}",
            f"Gain: {format_optional_float(frame.gain)}",
        ]
    )
    return "\n".join(lines)


def stacked_debug_info(frames: list[Frame], prefix: str) -> str:
    exposure_known = sum(1 for frame in frames if frame.exposure_s is not None)
    gain_known = sum(1 for frame in frames if frame.gain is not None)
    return "\n".join(
        [
            prefix,
            f"Stack n={len(frames)}",
            f"Exposure avg: {format_optional_float(optional_average(frame.exposure_s for frame in frames), ' s')} ({exposure_known}/{len(frames)})",
            f"Gain avg: {format_optional_float(optional_average(frame.gain for frame in frames))} ({gain_known}/{len(frames)})",
        ]
    )


def contour_overlay_panel(
    gray: np.ndarray,
    mask: np.ndarray,
    label: str,
    color: tuple[int, int, int],
) -> Image.Image:
    base = np.stack((gray, gray, gray), axis=-1)
    contour = segmentation.find_boundaries(mask.astype(bool), mode="inner")
    base[contour] = color
    panel = Image.fromarray(base, mode="RGB")
    draw_legend(ImageDraw.Draw(panel), label, color, panel.width)
    return panel


def debug_solar_main_component_image(
    image: np.ndarray,
    detection: FullSunDetection,
    out_path: Path,
    debug_info: str | None = None,
) -> None:
    gray = robust_display_u8(image)
    simple_img = contour_overlay_panel(gray, detection.threshold_mask, "contour seuillage", (80, 180, 255))
    final_img = contour_overlay_panel(gray, detection.final_mask, "contour masque principal", (60, 230, 120))
    watershed_label = "contour watershed" if detection.watershed_computed else "watershed non calcule"
    watershed_img = contour_overlay_panel(gray, detection.watershed_mask, watershed_label, (255, 230, 40))

    circle_img = Image.fromarray(np.stack((gray, gray, gray), axis=-1), mode="RGB")
    circle_draw = ImageDraw.Draw(circle_img)
    ransac_color = (40, 140, 255)
    least_squares_color = (255, 150, 40)
    ls_cx, ls_cy, ls_radius = detection.least_squares_circle
    draw_circle(circle_draw, ls_cx, ls_cy, ls_radius, least_squares_color, width=2)
    if detection.ransac_circle is not None:
        rs_cx, rs_cy, rs_radius = detection.ransac_circle
        draw_circle(circle_draw, rs_cx, rs_cy, rs_radius, ransac_color, width=3)
    draw_center(circle_draw, detection.center_x, detection.center_y, ransac_color)
    confidence = "n/a" if detection.ransac_confidence is None else f"{detection.ransac_confidence:.2f}"
    legend = f"RANSAC bleu conf={confidence} ({detection.ransac_inliers}/{detection.ransac_candidates})  LS orange"
    draw_legend(circle_draw, legend, ransac_color, circle_img.width)

    panel_w = gray.shape[1]
    panel_h = gray.shape[0]
    combined = Image.new("RGB", (panel_w * 2, panel_h * 2))
    combined.paste(simple_img, (0, 0))
    combined.paste(final_img, (panel_w, 0))
    combined.paste(watershed_img, (0, panel_h))
    combined.paste(circle_img, (panel_w, panel_h))
    draw_debug_info(ImageDraw.Draw(combined), debug_info, 12, 58, panel_w - 24)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined.save(out_path)


def build_solar_model(
    frames: list[Frame],
    full_sun_indices: set[int],
    first_nearly_full_count: int,
    excluded_indices: set[int],
    dark_indices: set[int],
    sky_threshold: float,
    debug_dir: Path,
    debug_full_sun: bool,
    debug_watershed: bool,
) -> SolarModel:
    if full_sun_indices:
        model_indices = sorted(full_sun_indices)
    else:
        available = [i for i in range(len(frames)) if i not in excluded_indices and i not in dark_indices]
        model_indices = available[: max(1, int(first_nearly_full_count))]
    if not model_indices:
        raise ValueError("No frame available to build the full-sun model")

    detections: list[tuple[int, FullSunDetection]] = []
    for idx in model_indices:
        debug_output_path = None
        debug_info = None
        if debug_full_sun:
            debug_output_path = debug_dir / "full_sun_frames" / f"full_sun_{frames[idx].index1:04d}_{frames[idx].path.stem}.png"
            debug_info = frame_debug_info(frames[idx], "Full-sun source")
        detection = detect_full_sun(
            frames[idx].image,
            sky_threshold,
            debug_output_path=debug_output_path,
            debug_info=debug_info,
            compute_watershed=debug_watershed,
        )
        detections.append((idx, detection))
        logging.info(
            "Full-sun model frame %d: center=(%.1f, %.1f), radius=%.1f",
            frames[idx].index1,
            detection.center_x,
            detection.center_y,
            detection.radius,
        )

    radii = np.array([det.radius for _, det in detections], dtype=np.float64)
    radius = float(np.mean(radii))
    side = int(math.ceil(radius * 2.5))
    if side % 2:
        side += 1
    side = max(side, 64)

    crops = [crop_with_padding(frames[idx].image, det.center_x, det.center_y, side) for idx, det in detections]
    model_image = np.mean(np.stack(crops, axis=0), axis=0).astype(np.float32)
    model_detection = detect_full_sun(model_image, sky_threshold)
    model_radius = float(np.mean([radius, model_detection.radius]))
    final_side = int(math.ceil(model_radius * 2.5))
    if final_side % 2:
        final_side += 1
    if final_side != side:
        side = max(final_side, 64)
        crops = [crop_with_padding(frames[idx].image, det.center_x, det.center_y, side) for idx, det in detections]
        model_image = np.mean(np.stack(crops, axis=0), axis=0).astype(np.float32)
        model_detection = detect_full_sun(model_image, sky_threshold)
        model_radius = float(np.mean([radius, model_detection.radius]))

    if debug_full_sun:
        debug_model_image(
            model_image,
            model_detection,
            debug_dir / "full_sun_model.png",
            debug_info=stacked_debug_info([frames[i] for i in model_indices], "Full-sun model"),
        )

    logging.info(
        "Solar session model: frames=%s, radius=%.2f px, output side=%d px",
        ",".join(str(frames[i].index1) for i in model_indices),
        model_radius,
        side,
    )
    return SolarModel(
        image=model_image,
        mask=model_detection.final_mask,
        center_x=model_detection.center_x,
        center_y=model_detection.center_y,
        radius=model_radius,
        side=side,
        source_indices=[frames[i].index1 for i in model_indices],
    )


def debug_model_image(
    model: np.ndarray,
    detection: FullSunDetection,
    out_path: Path,
    debug_info: str | None = None,
) -> None:
    gray = robust_display_u8(model)
    src = Image.fromarray(np.stack((gray, gray, gray), axis=-1), mode="RGB")
    mask_rgb = np.stack((gray // 4, gray // 4, gray // 4), axis=-1)
    mask_rgb[detection.final_mask] = (80, 220, 120)
    mask_img = Image.fromarray(mask_rgb, mode="RGB")
    overlay = src.copy()
    draw = ImageDraw.Draw(overlay)
    draw_circle(draw, detection.center_x, detection.center_y, detection.radius, (255, 180, 40), width=2)
    draw_center(draw, detection.center_x, detection.center_y, (255, 80, 80))
    combined = Image.new("RGB", (gray.shape[1] * 3, gray.shape[0]))
    combined.paste(src, (0, 0))
    combined.paste(mask_img, (gray.shape[1], 0))
    combined.paste(overlay, (gray.shape[1] * 2, 0))
    draw_debug_info(ImageDraw.Draw(combined), debug_info, 12, 12, gray.shape[1] - 24)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined.save(out_path)


def prepare_mask_for_correlation(mask: np.ndarray, sigma: float = 1.2) -> np.ndarray:
    out = mask.astype(np.float32, copy=True)
    if sigma > 0:
        out = ndimage.gaussian_filter(out, sigma=sigma)
    out -= float(np.mean(out))
    norm = float(np.linalg.norm(out))
    if norm > 0:
        out /= norm
    return out.astype(np.float32, copy=False)


def radius_is_coherent(radius: float, reference_radius: float, tolerance: float = RADIUS_COHERENCE_TOLERANCE) -> bool:
    if not np.isfinite(radius) or not np.isfinite(reference_radius) or reference_radius <= 0:
        return False
    return abs(float(radius) - float(reference_radius)) <= float(reference_radius) * tolerance


def relative_shift_from_masks_cpu(prev_mask: np.ndarray, current_mask: np.ndarray) -> tuple[float, float]:
    prev_prepared = prepare_mask_for_correlation(prev_mask)
    current_prepared = prepare_mask_for_correlation(current_mask)
    try:
        shift_yx, _, _ = phase_cross_correlation(prev_prepared, current_prepared, upsample_factor=10)
        # phase_cross_correlation returns the shift to apply to current to align it to previous.
        return -float(shift_yx[1]), -float(shift_yx[0])
    except Exception as exc:
        logging.warning("Correlation shift failed, using (0, 0): %s", exc)
        return 0.0, 0.0


def parabolic_peak_offset(values: np.ndarray) -> float:
    left, center, right = (float(v) for v in values)
    denom = left - 2.0 * center + right
    if abs(denom) < 1e-12:
        return 0.0
    return float(np.clip(0.5 * (left - right) / denom, -0.5, 0.5))


def relative_shift_from_masks_gpu(prev_mask: np.ndarray, current_mask: np.ndarray) -> tuple[float, float]:
    if cp is None:
        return relative_shift_from_masks_cpu(prev_mask, current_mask)
    prev_prepared = prepare_mask_for_correlation(prev_mask)
    current_prepared = prepare_mask_for_correlation(current_mask)
    try:
        prev_gpu = cp.asarray(prev_prepared)
        current_gpu = cp.asarray(current_prepared)
        product = cp.fft.fft2(prev_gpu) * cp.conj(cp.fft.fft2(current_gpu))
        magnitude = cp.abs(product)
        product = product / cp.maximum(magnitude, cp.finfo(cp.float32).eps)
        corr = cp.abs(cp.fft.ifft2(product))
        peak_flat = int(cp.asnumpy(cp.argmax(corr)))
        peak_y, peak_x = np.unravel_index(peak_flat, corr.shape)
        corr_cpu = cp.asnumpy(corr)

        shift_y = float(peak_y)
        shift_x = float(peak_x)
        if 0 < peak_y < corr_cpu.shape[0] - 1:
            shift_y += parabolic_peak_offset(corr_cpu[peak_y - 1 : peak_y + 2, peak_x])
        if 0 < peak_x < corr_cpu.shape[1] - 1:
            shift_x += parabolic_peak_offset(corr_cpu[peak_y, peak_x - 1 : peak_x + 2])
        if shift_y > corr_cpu.shape[0] / 2.0:
            shift_y -= corr_cpu.shape[0]
        if shift_x > corr_cpu.shape[1] / 2.0:
            shift_x -= corr_cpu.shape[1]

        # The phase peak is the shift to apply to current to align it to prev.
        dx = -shift_x
        dy = -shift_y
        return dx, dy
    except Exception as exc:
        logging.warning("GPU relative correlation failed, falling back to CPU: %s", exc)
        return relative_shift_from_masks_cpu(prev_mask, current_mask)


def relative_shift_from_masks(
    prev_mask: np.ndarray,
    current_mask: np.ndarray,
    correlation_engine: CorrelationEngine,
) -> tuple[float, float]:
    if correlation_engine.use_gpu:
        return relative_shift_from_masks_gpu(prev_mask, current_mask)
    return relative_shift_from_masks_cpu(prev_mask, current_mask)


def rotate_crop(crop: np.ndarray, clockwise_deg: float) -> np.ndarray:
    if abs(clockwise_deg) < 1e-8:
        return crop
    return ndimage.rotate(crop, -float(clockwise_deg), reshape=False, order=1, mode="constant", cval=0.0).astype(np.float32)


def build_movie_frames(
    frames: list[Frame],
    movie_indices: list[int],
    model: SolarModel,
    sky_threshold: float,
    rotate_clockwise_deg: float,
    correlation_engine: CorrelationEngine,
    debug_shifts_dir: Path | None = None,
    debug_watershed: bool = False,
) -> list[ProcessedFrame]:
    if not movie_indices:
        raise ValueError("No frame left for GIF generation after exclusions/calibration selections")

    processed: list[ProcessedFrame] = []
    previous_mask: np.ndarray | None = None
    previous_center: tuple[float, float] | None = None

    for step, idx in enumerate(movie_indices):
        frame = frames[idx]
        debug_output_path = None
        debug_info = None
        if debug_shifts_dir is not None:
            debug_output_path = debug_shifts_dir / f"mask_{step:04d}_frame_{frame.index1:04d}_{frame.path.stem}.png"
            debug_info = frame_debug_info(frame, "Detection/Crop source")

        detection = masque_solaire_principal(
            frame.image,
            sky_threshold,
            debug_output_path=debug_output_path,
            debug_info=debug_info,
            compute_watershed=debug_watershed,
        )
        current_mask = detection.final_mask.astype(np.float32, copy=False)
        coherent = radius_is_coherent(detection.radius, model.radius)

        if coherent:
            cx_i, cy_i = detection.center_x, detection.center_y
            method = "circle"
        elif previous_mask is not None and previous_center is not None:
            dx, dy = relative_shift_from_masks(previous_mask, current_mask, correlation_engine)
            cx_i = previous_center[0] + dx
            cy_i = previous_center[1] + dy
            method = f"correlation dx={dx:.2f} dy={dy:.2f}"
        else:
            cx_i, cy_i = detection.center_x, detection.center_y
            method = "detected-center-init-incoherent"

        if debug_shifts_dir is not None:
            logging.info(
                "Frame %d/%d source=%d exposure=%s gain=%s center=(%.1f, %.1f), radius=%.1f, reference_radius=%.1f, method=%s",
                step + 1,
                len(movie_indices),
                frame.index1,
                format_optional_float(frame.exposure_s, " s"),
                format_optional_float(frame.gain),
                cx_i,
                cy_i,
                detection.radius,
                model.radius,
                method,
            )

        crop = crop_with_padding(frame.image, cx_i, cy_i, model.side)
        crop = rotate_crop(crop, rotate_clockwise_deg)
        processed.append(ProcessedFrame(frame, crop, cx_i, cy_i))
        previous_mask = current_mask
        previous_center = (cx_i, cy_i)

    return processed


def group_into_slots(processed: list[ProcessedFrame], target_duration: float) -> tuple[list[StackedFrame], list[int]]:
    """Group acquisition frames into at most 10 fps GIF slots."""
    if not processed:
        return [], []
    max_fps = 10.0
    slot_count = max(1, int(math.ceil(max(1.0, target_duration) * max_fps)))
    slot_duration_ms = int(round(max(1.0, target_duration) * 1000.0 / slot_count))

    t0 = processed[0].source.timestamp
    t1 = processed[-1].source.timestamp
    if t1 <= t0:
        buckets = [[frame] for frame in processed]
        return [stack_slot(bucket) for bucket in buckets], [slot_duration_ms] * len(buckets)

    buckets: list[list[ProcessedFrame]] = [[] for _ in range(slot_count)]
    for frame in processed:
        pos = (frame.source.timestamp - t0) / (t1 - t0)
        slot = int(np.clip(math.floor(pos * slot_count), 0, slot_count - 1))
        buckets[slot].append(frame)

    images: list[StackedFrame] = []
    durations: list[int] = []
    pending_ms = 0
    for bucket in buckets:
        if not bucket:
            pending_ms += slot_duration_ms
            continue
        if durations:
            durations[-1] += pending_ms
        elif pending_ms:
            # If the first non-empty slot is not slot 0, add the skipped time to it.
            pending_ms = 0
        images.append(stack_slot(bucket))
        durations.append(slot_duration_ms)
        pending_ms = 0
    if durations and pending_ms:
        durations[-1] += pending_ms
    return images, durations


def stack_slot(frames: list[ProcessedFrame]) -> StackedFrame:
    if len(frames) == 1:
        image = frames[0].image.astype(np.float32, copy=False)
    else:
        image = np.mean(np.stack([frame.image for frame in frames], axis=0), axis=0).astype(np.float32)
    return StackedFrame(
        image=image,
        source_indices=[frame.source.index1 for frame in frames],
        exposure_s=optional_average(frame.source.exposure_s for frame in frames),
        gain=optional_average(frame.source.gain for frame in frames),
        exposure_values=[frame.source.exposure_s for frame in frames],
        gain_values=[frame.source.gain for frame in frames],
        source_count=len(frames),
    )


def log_gif_frame_composition(stacked_frames: list[StackedFrame], durations_ms: list[int]) -> None:
    for idx, (stacked_frame, duration_ms) in enumerate(zip(stacked_frames, durations_ms), start=1):
        first_source = stacked_frame.source_indices[0] if stacked_frame.source_indices else None
        logging.info(
            "GIF frame %d/%d first_source=%s n=%d duration=%dms sources=%s exposures=%s gains=%s",
            idx,
            len(stacked_frames),
            "n/a" if first_source is None else first_source,
            stacked_frame.source_count,
            duration_ms,
            stacked_frame.source_indices,
            format_optional_float_list(stacked_frame.exposure_values, " s"),
            format_optional_float_list(stacked_frame.gain_values),
        )


def images_to_gif_frames(stacked_frames: list[StackedFrame], sky_threshold: float) -> list[Image.Image]:
    if not stacked_frames:
        return []
    images = [frame.image for frame in stacked_frames]
    stack = np.stack(images, axis=0)
    useful = stack[stack > sky_threshold]
    if useful.size < 64:
        useful = stack.ravel()
    low = max(float(sky_threshold), float(np.percentile(useful, 0.5)))
    high = float(np.percentile(useful, 99.7))
    if high <= low:
        high = low + 1.0

    gif_frames: list[Image.Image] = []
    for image in images:
        arr = np.clip((image - low) / (high - low), 0.0, 1.0)
        arr = np.sqrt(arr)
        u8 = np.clip(arr * 255.0, 0, 255).astype(np.uint8)
        gif_frames.append(Image.fromarray(u8, mode="L").convert("P", palette=Image.Palette.ADAPTIVE))
    return gif_frames


def write_gif(frames: list[Image.Image], durations_ms: list[int], output: Path) -> None:
    if not frames:
        raise ValueError("No GIF frame generated")
    output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        output,
        save_all=True,
        append_images=frames[1:],
        duration=durations_ms,
        loop=0,
        optimize=False,
        disposal=2,
    )


def save_debug_gif_frames(
    gif_frames: list[Image.Image],
    stacked_frames: list[StackedFrame],
    durations_ms: list[int],
    debug_gif_frames_dir: Path,
) -> None:
    debug_gif_frames_dir.mkdir(parents=True, exist_ok=True)
    for idx, (gif_frame, stacked_frame, duration_ms) in enumerate(zip(gif_frames, stacked_frames, durations_ms), start=1):
        source_spec = "-".join(str(source_idx) for source_idx in stacked_frame.source_indices)
        safe_source_spec = source_spec[:80] if source_spec else "none"
        out_path = debug_gif_frames_dir / (
            f"gif_{idx:04d}_duration_{duration_ms:04d}ms_sources_{safe_source_spec}"
            f"_exp_{format_optional_float(stacked_frame.exposure_s).replace(' ', '')}"
            f"_gain_{format_optional_float(stacked_frame.gain).replace(' ', '')}.png"
        )
        gif_frame.convert("L").save(out_path)


def clean_debug_outputs(debug_dir: Path, debug_full_sun: bool, debug_shifts: bool, debug_gif_frames: bool) -> None:
    """Remove previous debug outputs generated by this script."""
    targets: list[Path] = []
    if debug_full_sun:
        targets.append(debug_dir / "full_sun_frames")
        targets.append(debug_dir / "full_sun_model.png")
    if debug_shifts:
        targets.append(debug_dir / "shifts")
    if debug_gif_frames:
        targets.append(debug_dir / "gif_frames")

    for target in targets:
        if not target.exists():
            continue
        if target.is_dir():
            shutil.rmtree(target)
            logging.info("Cleaned debug directory: %s", target)
        else:
            target.unlink()
            logging.info("Cleaned debug file: %s", target)


def main() -> int:
    args = parse_args()
    setup_logging(args.log_level)

    input_dir = Path(args.input_dir).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    debug_dir = Path(args.debug_dir).expanduser().resolve() if args.debug_dir else input_dir / "debug"

    try:
        correlation_engine = select_correlation_engine()
        if correlation_engine.use_gpu:
            logging.info("Correlation backend: GPU (%s)", correlation_engine.reason)
        else:
            logging.info("Correlation backend: CPU (%s)", correlation_engine.reason)

        frames = load_frames(input_dir)
        logging.info("Loaded %d sorted FITS frame(s)", len(frames))

        excluded = parse_frame_selection(args.exclude_frames, len(frames))
        darks = parse_frame_selection(args.dark_calib_frames, len(frames))
        full_sun = parse_frame_selection(args.full_sun_frames, len(frames))
        if excluded:
            logging.info("Excluded frame(s): %s", sorted(i + 1 for i in excluded))
        if darks:
            logging.info("Dark calibration frame(s): %s", sorted(i + 1 for i in darks))
        if full_sun:
            logging.info("Full-sun frame(s): %s", sorted(i + 1 for i in full_sun))

        clean_debug_outputs(debug_dir, args.debug_full_sun, args.debug_shifts, args.debug_gif_frames)

        sky_threshold = compute_sky_threshold(frames, darks, args.manual_seuil_fond_du_ciel)
        model = build_solar_model(
            frames,
            full_sun,
            args.first_nearly_full_sun_frames,
            excluded,
            darks,
            sky_threshold,
            debug_dir,
            args.debug_full_sun,
            args.debug_watershed and args.debug_full_sun,
        )

        movie_exclusions = set(excluded) | set(darks) | set(full_sun)
        movie_indices = [i for i in range(len(frames)) if i not in movie_exclusions]
        logging.info("Movie frame count after exclusions/calibrations: %d", len(movie_indices))

        debug_shifts_dir = debug_dir / "shifts" if args.debug_shifts else None
        if debug_shifts_dir is not None:
            logging.info("Shift mask debug directory: %s", debug_shifts_dir)
            if args.debug_watershed:
                logging.info("Watershed debug calculation enabled")
        debug_gif_frames_dir = debug_dir / "gif_frames" if args.debug_gif_frames else None
        if debug_gif_frames_dir is not None:
            logging.info("GIF frame debug directory: %s", debug_gif_frames_dir)

        processed = build_movie_frames(
            frames,
            movie_indices,
            model,
            sky_threshold,
            args.rotate_clockwise_deg,
            correlation_engine,
            debug_shifts_dir,
            args.debug_watershed and args.debug_shifts,
        )
        slot_images, durations_ms = group_into_slots(processed, args.target_duration)
        log_gif_frame_composition(slot_images, durations_ms)
        gif_frames = images_to_gif_frames(slot_images, sky_threshold)
        if debug_gif_frames_dir is not None:
            save_debug_gif_frames(gif_frames, slot_images, durations_ms, debug_gif_frames_dir)
        write_gif(gif_frames, durations_ms, output)

        logging.info(
            "GIF written: %s (%d frame(s), %.2f s target)",
            output,
            len(gif_frames),
            args.target_duration,
        )
        return 0
    except Exception as exc:
        logging.error("solarEclipseGif failed: %s", exc)
        if args.log_level == "DEBUG":
            raise
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
