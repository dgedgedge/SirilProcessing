"""Diagnostic indépendant du gradient : masques fixes, bruit et signal diffus.

Les seuils sont des heuristiques à calibrer. Une segmentation ne prouve jamais
l’absence de nébulosité indétectable. Les mesures sont exprimées dans l’échelle
Siril (entiers normalisés, flottants conservés), sans modifier les images.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
from pathlib import Path
from typing import Literal

import numpy as np
from astropy.io import fits
from scipy.ndimage import (
    binary_dilation,
    find_objects,
    gaussian_filter,
    label,
    maximum_filter,
)

from lib.parameterized_component import ParameterizedComponent
from lib.type_defs import JSONReport

MAD_SCALE = 1.4826
BOOTSTRAP_REPEATS = 256
MIN_FILTER_SUPPORT = 0.5
MAX_SEGMENTATION_SAMPLES = 262144


def criterion(
    status: Literal["PASS", "FAIL", "UNKNOWN"],
    reason: str,
    *,
    value: float | None = None,
    threshold: float | None = None,
    uncertainty: float | None = None,
    sample_count: int = 0,
) -> JSONReport:
    """Décrit un verdict sans transformer une mesure absente en zéro."""
    return dict(
        status=status,
        reason=reason,
        value=value,
        threshold=threshold,
        uncertainty=uncertainty,
        sample_count=sample_count,
    )


def normalized_planes(path: Path | str) -> np.ndarray:
    """Lit un FITS mono/RGB dans son orientation native, en unités Siril."""
    data = fits.getdata(path)
    scale = float(np.iinfo(data.dtype).max) if data.dtype.kind in "ui" else 1.0
    planes = np.asarray(data, dtype=np.float32) / scale
    if planes.ndim == 2:
        planes = planes[None]
    if planes.ndim != 3 or planes.shape[0] not in (1, 3):
        raise ValueError("FITS mono ou RGB requis")
    return planes


def robust_sigma(values: np.ndarray) -> float:
    """Dispersion MAD, dans les unités des pixels, pour une population finie."""
    return float(MAD_SCALE * np.median(abs(values - np.median(values))))


def bootstrap_interval(values: np.ndarray) -> tuple[float, float]:
    """Intervalle percentile à 95 %, rééchantillonné par tuiles indépendantes.

    La graine fixe rend le diagnostic reproductible. Ce bootstrap ne modélise
    pas les corrélations entre tuiles ni les erreurs systématiques de fond.
    """
    rng = np.random.default_rng(2718)
    draws = rng.choice(values, (BOOTSTRAP_REPEATS, len(values)))
    low, high = np.quantile(np.median(draws, axis=1), [0.025, 0.975])
    return float(low), float(high)


def bootstrap_mean_interval(values: np.ndarray) -> tuple[float, float]:
    """Intervalle des contributions intégrées par blocs, pour la photométrie."""
    rng = np.random.default_rng(2718)
    draws = rng.choice(values, (BOOTSTRAP_REPEATS, len(values)))
    low, high = np.quantile(np.mean(draws, axis=1), [0.025, 0.975])
    return float(low), float(high)


class RestorationQuality(ParameterizedComponent):
    """Déclare les options communes, avec rémanence gérée par Config."""

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Expose mode historique, segmentation et seuils diagnostiques."""
        if any(action.dest == "quality_mode" for action in parser._actions):
            return
        group = parser
        group.add_argument(
            "--quality-mode",
            choices=("reliable", "legacy"),
            default="reliable",
            help="Masques et diagnostics fiables (défaut), ou critères historiques",
        )
        group.add_argument("--quality-tile-size", type=int, default=64)
        group.add_argument("--quality-min-tiles", type=int, default=4)
        group.add_argument("--quality-source-sigma", type=float, default=4.0)
        group.add_argument(
            "--quality-scales", type=float, nargs="+", default=[2, 4, 8, 16, 32]
        )
        group.add_argument("--quality-mask-growth", type=int, default=5)
        group.add_argument("--quality-min-noise-gain", type=float, default=0.05)
        group.add_argument(
            "--quality-enforce-significance",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="Exiger un gain utile significatif ; sinon observation",
        )
        group.add_argument("--quality-max-flux-loss", type=float, default=0.05)
        group.add_argument(
            "--quality-enforce-diffuse",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="Rendre bloquants les contrôles diffus ; défaut observation",
        )


class QualityReference:
    """Référence figée d’une comparaison, indépendante des moteurs et du gradient.

    Les exclusions sont l’union conservative des canaux. Les régions brillantes
    étendues et les tendances ambiguës restent exclues même si le fond local les
    absorbe. Un champ sans ciel identifiable donne UNKNOWN. Toutes les cartes
    sont calculées avant les moteurs ; deepcopy partage cette référence immuable.
    """

    def __init__(
        self, path: Path | str, args: argparse.Namespace | None = None
    ) -> None:
        """Construit les cartes sans extraire ni soustraire de gradient au FITS."""
        self.path = Path(path).resolve()
        self.data = normalized_planes(self.path)
        self.top_down = (
            fits.getheader(self.path).get("ROWORDER", "BOTTOM-UP") == "TOP-DOWN"
        )
        self.tile_size = getattr(args, "quality_tile_size", 64)
        self.min_tiles = getattr(args, "quality_min_tiles", 4)
        self.source_sigma = getattr(args, "quality_source_sigma", 4.0)
        self.scales = tuple(getattr(args, "quality_scales", [2, 4, 8, 16, 32]))
        self.growth = getattr(args, "quality_mask_growth", 5)
        self.min_gain = getattr(args, "quality_min_noise_gain", 0.05)
        self.flux_loss = getattr(args, "quality_max_flux_loss", 0.05)
        self.enforce_significance = getattr(args, "quality_enforce_significance", False)
        self.enforce_diffuse = getattr(args, "quality_enforce_diffuse", False)
        if (
            self.tile_size < 16
            or self.min_tiles < 2
            or self.growth < 0
            or not self.scales
            or not np.isfinite(self.scales).all()
            or min(self.scales) < 1
            or max(self.scales) > 128
            or not np.isfinite([self.source_sigma, self.min_gain, self.flux_loss]).all()
            or self.source_sigma < 2
            or not 0 <= self.min_gain < 1
            or not 0 < self.flux_loss < 1
        ):
            raise ValueError("Paramètres de qualité invalides")
        self.fingerprint = hashlib.sha256(self.data.tobytes()).hexdigest()
        shape = self.data.shape[1:]
        self.invalid_mask = ~np.isfinite(self.data).all(axis=0)
        self.invalid_mask[:2] = self.invalid_mask[-2:] = True
        self.invalid_mask[:, :2] = self.invalid_mask[:, -2:] = True
        self.star_mask = np.zeros(shape, dtype=bool)
        self.object_mask = np.zeros(shape, dtype=bool)
        self.uncertain_mask = np.zeros(shape, dtype=bool)
        self.backgrounds, self.sigmas = [], []
        self.segmentation_audit = []
        for plane in self.data:
            clean = np.where(np.isfinite(plane), plane, np.nanmedian(plane))
            background, sigma = self._local_maps(clean, self.invalid_mask)
            self.backgrounds.append(background)
            self.sigmas.append(sigma)
            residual = clean - background
            smooth = gaussian_filter(residual, 1)
            narrow = smooth - gaussian_filter(residual, 3)
            compact = (smooth == maximum_filter(smooth, size=7)) & (
                narrow > 2 * self.source_sigma * sigma
            )
            self.star_mask |= binary_dilation(
                compact, iterations=max(8, 2 * self.growth)
            )
        # Toutes les étoiles RGB sont exclues avant toute détection diffuse :
        # sinon leur lumière lissée devient un faux fond diffus jusque dans le ciel.
        for plane, background, sigma in zip(self.data, self.backgrounds, self.sigmas):
            clean = np.where(np.isfinite(plane), plane, np.nanmedian(plane))
            objects, unsupported, scales_audit = self._diffuse_objects(
                clean - background, sigma
            )
            self.object_mask |= objects
            self.uncertain_mask |= unsupported
            self.segmentation_audit.append(scales_audit)
            # Diagnostic conservateur à grande échelle, indépendant du fond local.
            floor = (
                float(np.quantile(clean[~self.invalid_mask], 0.1))
                if (~self.invalid_mask).any()
                else 0
            )
            broad = (
                gaussian_filter(
                    np.where(self.star_mask, background, clean), max(self.scales)
                )
                - floor
            )
            self.uncertain_mask |= broad > 3 * sigma
        self.object_mask = binary_dilation(
            self.object_mask, iterations=max(1, self.growth)
        )
        self.object_mask &= ~self.star_mask & ~self.invalid_mask
        self.uncertain_mask &= ~self.object_mask & ~self.star_mask
        self.sky_mask = ~(
            self.invalid_mask | self.star_mask | self.object_mask | self.uncertain_mask
        )
        # Réestimation diagnostique limitée, sans réviser les exclusions à la baisse.
        self.backgrounds, self.sigmas = zip(
            *[
                self._local_maps(np.nan_to_num(plane), ~self.sky_mask)
                for plane in self.data
            ]
        )
        self.tiles = tuple(self._sky_tiles())
        self.mask_fingerprint = hashlib.sha256(
            np.packbits(
                np.stack(
                    [
                        self.sky_mask,
                        self.star_mask,
                        self.object_mask,
                        self.uncertain_mask,
                    ]
                )
            ).tobytes()
        ).hexdigest()
        for array in (
            self.data,
            self.sky_mask,
            self.star_mask,
            self.object_mask,
            self.uncertain_mask,
            self.invalid_mask,
            *self.backgrounds,
            *self.sigmas,
        ):
            array.setflags(write=False)
        self._sealed = True
        logging.info(
            "Sélection du ciel : %.2f %% de pixels ; %d tuiles dans %d quadrants ; "
            "minimum %d tuiles dans 3 quadrants ; exclusions locales, sans veto global de couverture",
            100 * self.sky_mask.mean(),
            len(self.tiles),
            self.audit()["quadrant_count"],
            self.min_tiles,
        )

    def _diffuse_objects(
        self, residual: np.ndarray, sigma: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, list[JSONReport]]:
        """Détecte les structures étendues sans diffuser les étoiles dans le ciel.

        La convolution normalisée exclut étoiles et pixels invalides ; un
        support inférieur à 50 % reste incertain. Le seuil conserve le plancher
        théorique historique et le relève selon la dispersion MAD effectivement
        observée après lissage, pour ne pas supposer le bruit blanc. Cette MAD
        peut inclure du signal : c’est une limite conservative de segmentation,
        jamais l’estimateur utilisé pour classer les débruitages. Un échantillon
        spatial régulier limite le coût sur les mosaïques. Voir quality.md.
        """
        admissible = ~(self.star_mask | self.invalid_mask)
        weights = admissible.astype(np.float32)
        masked = np.where(admissible, residual, 0)
        objects = np.zeros(residual.shape, dtype=bool)
        unsupported = np.zeros(residual.shape, dtype=bool)
        audit = []
        stride = max(1, int(np.ceil(np.sqrt(residual.size / MAX_SEGMENTATION_SAMPLES))))
        for scale in self.scales:
            support = gaussian_filter(weights, scale)
            usable = admissible & (support >= MIN_FILTER_SUPPORT)
            unsupported |= admissible & ~usable
            filtered = gaussian_filter(masked, scale) / np.maximum(
                support, MIN_FILTER_SUPPORT
            )
            sample = filtered[::stride, ::stride][usable[::stride, ::stride]]
            observed = robust_sigma(sample) if sample.size >= 32 else None
            theoretical = sigma / max(1, min(2 * np.sqrt(np.pi) * scale, 8))
            dispersion = np.maximum(
                theoretical, observed if observed is not None else 0
            )
            threshold = (
                self.source_sigma
                * dispersion
                / np.sqrt(np.maximum(support, MIN_FILTER_SUPPORT))
            )
            regions, count = label(usable & (filtered > threshold))
            sizes = np.bincount(regions.ravel(), minlength=count + 1)
            retained = sizes >= max(12, round(scale * scale))
            retained[0] = False
            objects |= retained[regions]
            audit.append(
                dict(
                    scale_px=scale,
                    observed_dispersion=observed,
                    sample_count=int(sample.size),
                    source_sigma=self.source_sigma,
                )
            )
        return objects, unsupported, audit

    def __setattr__(self, name: str, value: object) -> None:
        """Interdit de remplacer les cartes figées entre moteurs concurrents."""
        if getattr(self, "_sealed", False):
            raise AttributeError("Référence qualité immuable")
        object.__setattr__(self, name, value)

    def __deepcopy__(self, memo: dict[int, object]) -> QualityReference:
        """Partage les cartes en lecture seule entre contextes d’exécution."""
        return self

    def _local_maps(
        self, plane: np.ndarray, excluded: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Estime médiane et MAD par tuiles, interpolées sans correction d’image."""
        from scipy.ndimage import zoom

        height, width = plane.shape
        rows, columns = max(1, int(np.ceil(height / self.tile_size))), max(
            1, int(np.ceil(width / self.tile_size))
        )
        levels, sigmas = np.full((rows, columns), np.nan), np.full(
            (rows, columns), np.nan
        )
        for iy, ys in enumerate(np.array_split(np.arange(height), rows)):
            for ix, xs in enumerate(np.array_split(np.arange(width), columns)):
                values = plane[np.ix_(ys, xs)][~excluded[np.ix_(ys, xs)]]
                values = values[np.isfinite(values)]
                if len(values) < 32:
                    continue
                for _ in range(3):
                    sigma = robust_sigma(values)
                    keep = abs(values - np.median(values)) <= max(3 * sigma, 1e-12)
                    if keep.all() or keep.sum() < 32:
                        break
                    values = values[keep]
                tile = plane[np.ix_(ys, xs)]
                admissible = ~excluded[np.ix_(ys, xs)]
                adjacent = admissible[:, 1:] & admissible[:, :-1]
                differences = (tile[:, 1:] - tile[:, :-1])[adjacent]
                # Pour segmentation seulement : moins sensible à une pente lente.
                dispersion = (
                    robust_sigma(differences) / np.sqrt(2)
                    if len(differences) >= 32
                    else robust_sigma(values)
                )
                levels[iy, ix], sigmas[iy, ix] = np.median(values), max(
                    dispersion, 1e-12
                )
        finite = ~excluded & np.isfinite(plane)
        fallback = plane[finite]
        level = float(np.median(fallback)) if fallback.size else 0.0
        sigma = max(robust_sigma(fallback), 1e-12) if fallback.size else 1e-12
        levels = np.where(np.isfinite(levels), levels, level)
        sigmas = np.where(np.isfinite(sigmas), sigmas, sigma)
        factors = height / rows, width / columns
        return (
            zoom(levels, factors, order=1).astype(np.float32),
            zoom(sigmas, factors, order=1).astype(np.float32),
        )

    def _sky_tiles(self) -> list[tuple[slice, slice]]:
        """Retient les tuiles assez couvertes ; ne fabrique pas de ciel manquant."""
        tiles = []
        height, width = self.sky_mask.shape
        for y in range(0, height, self.tile_size):
            for x in range(0, width, self.tile_size):
                ys, xs = slice(y, min(y + self.tile_size, height)), slice(
                    x, min(x + self.tile_size, width)
                )
                mask = self.sky_mask[ys, xs]
                if mask.sum() >= max(128, 0.5 * mask.size):
                    tiles.append((ys, xs))
        return tiles

    def audit(self) -> JSONReport:
        """Résume provenance, couverture, politique et limites scientifiques."""
        quadrants = {
            int(ys.start >= self.sky_mask.shape[0] / 2) * 2
            + int(xs.start >= self.sky_mask.shape[1] / 2)
            for ys, xs in self.tiles
        }
        return dict(
            metrics_version=3,
            reference_image=str(self.path),
            reference_sha256=self.fingerprint,
            masks_sha256=self.mask_fingerprint,
            shape=list(self.data.shape),
            tile_count=len(self.tiles),
            quadrant_count=len(quadrants),
            fractions={
                name: float(getattr(self, name + "_mask").mean())
                for name in ("sky", "star", "object", "uncertain")
            },
            classification="heuristic_not_proof_of_empty_sky",
            scales_px=list(self.scales),
            source_sigma=self.source_sigma,
            enforce_diffuse=self.enforce_diffuse,
            segmentation=dict(
                method="star_masked_normalized_convolution",
                min_filter_support=MIN_FILTER_SUPPORT,
                per_channel=self.segmentation_audit,
            ),
        )

    def save_masks(self, destination: Path) -> Path:
        """Écrit les masques FITS et leur aperçu PNG ; retourne le chemin du PNG.

        L’aperçu expose les exclusions et les tuiles retenues sans réviser les
        masques. Il reste produit même lorsqu’aucune tuile de ciel n’est disponible.
        """
        header = fits.getheader(self.path)
        header["BUNIT"] = "mask bits"
        header["MASKBITS"] = "1=sky 2=star 4=object 8=uncertain 16=invalid"
        mask = sum(
            getattr(self, name + "_mask").astype(np.uint8) * bit
            for name, bit in [
                ("sky", 1),
                ("star", 2),
                ("object", 4),
                ("uncertain", 8),
                ("invalid", 16),
            ]
        )
        fits.writeto(destination, mask, header, overwrite=True)
        from lib.quality_preview import QualityMaskPreview

        return QualityMaskPreview.write(
            self.data,
            {
                name: getattr(self, name + "_mask")
                for name in ("sky", "star", "object", "uncertain")
            },
            self.tiles,
            destination.with_suffix(".png"),
            top_down=self.top_down,
            tile_size=self.tile_size,
        )

    @staticmethod
    def _detrend(plane: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """Retire uniquement une tendance plane pour la mesure locale de dispersion."""
        yy, xx = np.indices(plane.shape)
        design = np.column_stack((np.ones(mask.sum()), xx[mask], yy[mask]))
        coeff = np.linalg.lstsq(design, plane[mask], rcond=None)[0]
        return plane - (coeff[0] + coeff[1] * xx + coeff[2] * yy)

    def noise(self, candidate: np.ndarray) -> JSONReport:
        """Mesure dispersion, corrélation courte et intervalle par blocs communs."""
        coverage = self.audit()
        if len(self.tiles) < self.min_tiles or coverage["quadrant_count"] < 3:
            return dict(
                criterion("UNKNOWN", "NO_RELIABLE_SKY", sample_count=len(self.tiles)),
                per_channel=[],
                reference=coverage,
                max_noise_ratio=None,
            )
        channels = []
        for before, after in zip(self.data, candidate):
            ratios, correlations, dispersions = [], [], []
            for ys, xs in self.tiles:
                mask = self.sky_mask[ys, xs]
                b = self._detrend(before[ys, xs], mask)
                a = self._detrend(after[ys, xs], mask)
                nb, na = robust_sigma(b[mask]), robust_sigma(a[mask])
                if nb <= 1e-10:
                    continue
                ratios.append(na / nb)
                dispersions.append([nb, na])
                correlation = []
                for residual in (b, a):
                    adjacent = mask[:, :-1] & mask[:, 1:]
                    left, right = residual[:, :-1][adjacent], residual[:, 1:][adjacent]
                    correlation.append(
                        float(np.corrcoef(left, right)[0, 1])
                        if len(left) > 3 and left.std() > 0 and right.std() > 0
                        else None
                    )
                correlations.append(correlation)
            if len(ratios) < self.min_tiles:
                channels.append(
                    dict(
                        criterion("UNKNOWN", "TOO_FEW_TILES", sample_count=len(ratios))
                    )
                )
                continue
            ratio = float(np.median(ratios))
            lo, hi = bootstrap_interval(np.array(ratios))
            gain = 1 - ratio
            proven = 1 - hi >= self.min_gain
            channels.append(
                dict(
                    criterion(
                        "PASS" if proven else "UNKNOWN",
                        "USEFUL_NOISE_REDUCTION" if proven else "GAIN_NOT_DEMONSTRATED",
                        value=gain,
                        threshold=self.min_gain,
                        uncertainty=(hi - lo) / 2,
                        sample_count=len(ratios),
                    ),
                    noise_ratio=ratio,
                    ratio_interval=[lo, hi],
                    tile_sigma_before_after=dispersions,
                    lag1_correlation_before_after=correlations,
                )
            )
        known = all("noise_ratio" in item for item in channels)
        return dict(
            status=(
                "PASS"
                if known and all(c["status"] == "PASS" for c in channels)
                else "UNKNOWN"
            ),
            reason="MEASURED" if known else "TOO_FEW_TILES",
            per_channel=channels,
            reference=coverage,
            max_noise_ratio=max(c["noise_ratio"] for c in channels) if known else None,
            proven_gain=(
                min(1 - c["ratio_interval"][1] for c in channels) if known else None
            ),
        )

    def signal(self, candidate: np.ndarray) -> JSONReport:
        """Audit de flux diffus et de résidu multiscalaire sur régions fixes.

        La perte de flux corroborée par un résidu structuré est FAIL ; le mode
        observation ne la rend pas bloquante. Incertitudes par blocs spatiaux,
        sans prétendre quantifier les erreurs systématiques du fond local.
        """
        regions, count = label(self.object_mask)
        sizes = np.bincount(regions.ravel())
        entries = []
        for index, bounds in enumerate(find_objects(regions), start=1):
            if bounds is None or sizes[index] < 64:
                continue
            margin = int(4 * max(self.scales)) + 2 * self.growth + 8
            ys, xs = (
                slice(max(0, axis.start - margin), min(length, axis.stop + margin))
                for axis, length in zip(bounds, regions.shape)
            )
            local_regions = regions[ys, xs]
            mask = local_regions == index
            # Inclure une marge de redistribution ; les étoiles restent exclues.
            aperture = (
                binary_dilation(mask, iterations=max(8, 2 * self.growth))
                & ~self.star_mask[ys, xs]
                & ~self.invalid_mask[ys, xs]
            )
            annulus = (
                binary_dilation(aperture, iterations=self.growth + 4)
                & self.sky_mask[ys, xs]
            )
            for channel, (before_full, after_full) in enumerate(
                zip(self.data, candidate)
            ):
                before, after = before_full[ys, xs], after_full[ys, xs]
                if annulus.sum() < 64:
                    entries.append(
                        dict(
                            criterion(
                                "UNKNOWN",
                                "NO_LOCAL_SKY",
                                sample_count=int(aperture.sum()),
                            ),
                            channel=channel,
                            region=index,
                        )
                    )
                    continue
                bg_before, bg_after = np.median(before[annulus]), np.median(
                    after[annulus]
                )
                fb = float(np.sum(before[aperture] - bg_before))
                fa = float(np.sum(after[aperture] - bg_after))
                difference = (before - after) - (bg_before - bg_after)
                blocks = []
                for y in range(0, mask.shape[0], self.tile_size):
                    for x in range(0, mask.shape[1], self.tile_size):
                        tile = aperture[y : y + self.tile_size, x : x + self.tile_size]
                        if tile.sum() >= 16:
                            blocks.append(
                                float(
                                    np.sum(
                                        difference[
                                            y : y + self.tile_size,
                                            x : x + self.tile_size,
                                        ][tile]
                                    )
                                )
                            )
                ci = (
                    bootstrap_mean_interval(np.array(blocks))
                    if len(blocks) >= 4
                    else None
                )
                sigma = float(np.median(self.sigmas[channel][ys, xs][annulus]))
                uncertainty = sigma * np.sqrt(aperture.sum())
                if fb <= 10 * uncertainty:
                    entries.append(
                        dict(
                            criterion(
                                "UNKNOWN",
                                "LOW_DIFFUSE_SNR",
                                sample_count=int(aperture.sum()),
                            ),
                            channel=channel,
                            region=index,
                        )
                    )
                    continue
                loss = (fb - fa) / fb
                scale_results = []
                corroborated = False
                for scale in self.scales:
                    structure = gaussian_filter(before - bg_before, scale)
                    removed = gaussian_filter(difference, scale)
                    valid = aperture & (
                        structure > self.source_sigma * self.sigmas[channel][ys, xs] / 8
                    )
                    if valid.sum() >= 32:
                        amplitude = float(np.median(removed[valid]))
                        detectable = bool(
                            abs(amplitude)
                            > 3 * sigma / max(1, min(2 * np.sqrt(np.pi) * scale, 8))
                        )
                        corroborated |= detectable
                        scale_results.append(
                            dict(
                                scale_px=scale,
                                residual_median=amplitude,
                                significant=detectable,
                            )
                        )
                significant = (
                    ci is not None
                    and (ci[0] > 0 or ci[1] < 0)
                    and abs(fb - fa) > 3 * uncertainty
                )
                lost = abs(loss) > self.flux_loss and significant and corroborated
                entries.append(
                    dict(
                        criterion(
                            "FAIL" if lost else "PASS" if ci is not None else "UNKNOWN",
                            (
                                "CORROBORATED_DIFFUSE_LOSS"
                                if lost
                                else "NO_CONFIRMED_DIFFUSE_LOSS"
                            ),
                            value=loss,
                            threshold=self.flux_loss,
                            uncertainty=float(uncertainty / fb),
                            sample_count=int(aperture.sum()),
                        ),
                        channel=channel,
                        region=index,
                        flux_before=fb,
                        flux_after=fa,
                        unit="normalized_pixel_sum",
                        block_interval=ci,
                        multiscale=scale_results,
                    )
                )
        status = (
            "FAIL"
            if any(item["status"] == "FAIL" for item in entries)
            else (
                "PASS"
                if entries and all(item["status"] == "PASS" for item in entries)
                else "UNKNOWN"
            )
        )
        return dict(
            status=status,
            reason="NO_DETECTED_DIFFUSE_OBJECT" if not entries else "DIFFUSE_AUDIT",
            blocking=self.enforce_diffuse,
            per_region=entries,
        )

    def stellar_flux(
        self, candidate: np.ndarray, pairs: list[dict[str, float]]
    ) -> JSONReport:
        """Photométrie d’ouverture complémentaire à FWHM, avec marges communes.

        Les flux sont soustraits d’un fond local séparé avant/après. Une étoile
        trop proche du bord ou sans signal suffisant reste UNKNOWN. Le verdict
        global utilise un bootstrap de médianes par blocs spatiaux.
        """
        entries, channels = [], []
        height, width = self.sky_mask.shape
        for channel, (before, after) in enumerate(zip(self.data, candidate)):
            groups = {}
            for pair in pairs:
                radius = max(6, int(np.ceil(3 * pair["fwhm_before_px"])))
                x = int(round(pair["x_before"] - 0.5))
                y = int(
                    round(
                        height - pair["y_before"] - 0.5
                        if self.top_down
                        else pair["y_before"] - 0.5
                    )
                )
                outer = 2 * radius
                if min(x, y) < outer or x + outer >= width or y + outer >= height:
                    continue
                yy, xx = np.indices((2 * outer + 1, 2 * outer + 1))
                rr = np.hypot(xx - outer, yy - outer)
                aperture, annulus = rr <= radius, (rr > 1.5 * radius) & (rr <= outer)
                b = before[y - outer : y + outer + 1, x - outer : x + outer + 1]
                a = after[y - outer : y + outer + 1, x - outer : x + outer + 1]
                if not np.isfinite(b).all():
                    continue
                fb = float(np.sum(b[aperture] - np.median(b[annulus])))
                fa = float(np.sum(a[aperture] - np.median(a[annulus])))
                uncertainty = robust_sigma(b[annulus]) * np.sqrt(aperture.sum())
                if fb <= 10 * uncertainty or fb <= 1e-10:
                    continue
                change = (fa - fb) / fb
                entries.append(
                    dict(
                        reference_id=pair["reference_id"],
                        channel=channel,
                        flux_before=fb,
                        flux_after=fa,
                        relative_change=change,
                        uncertainty=uncertainty / fb,
                        unit="normalized_pixel_sum",
                    )
                )
                groups.setdefault((x // 128, y // 128), []).append(change)
            values = np.array([np.median(group) for group in groups.values()])
            if len(values) < 4:
                channels.append(
                    criterion(
                        "UNKNOWN", "TOO_FEW_APERTURE_GROUPS", sample_count=len(values)
                    )
                )
            else:
                lo, hi = bootstrap_interval(values)
                changed = hi < -self.flux_loss or lo > self.flux_loss
                channels.append(
                    dict(
                        criterion(
                            "FAIL" if changed else "PASS",
                            "STELLAR_FLUX_CHANGE",
                            value=float(np.median(values)),
                            threshold=self.flux_loss,
                            uncertainty=(hi - lo) / 2,
                            sample_count=len(values),
                        ),
                        interval=[lo, hi],
                    )
                )
        return dict(
            status=(
                "FAIL"
                if any(c["status"] == "FAIL" for c in channels)
                else (
                    "PASS"
                    if channels and all(c["status"] == "PASS" for c in channels)
                    else "UNKNOWN"
                )
            ),
            blocking=self.enforce_diffuse,
            per_channel=channels,
            apertures=entries,
        )

    def evaluate(
        self, path: Path | str, pairs: list[dict[str, float]] | None = None
    ) -> JSONReport:
        """Évalue un FITS aux coordonnées fixes ; refuse géométrie et WCS modifiés."""
        candidate = normalized_planes(path)
        if candidate.shape != self.data.shape or not np.isfinite(candidate).all():
            raise ValueError("Géométrie différente ou pixels candidats non finis")
        self.validate_metadata(self.path, path)
        clipping = []
        for before, after in zip(self.data, candidate):
            added = int(np.count_nonzero((after >= 1) & (before < 1)))
            clipping.append(
                dict(
                    criterion(
                        "UNKNOWN" if added else "PASS",
                        "NEW_SATURATION_PIXELS" if added else "NO_NEW_SATURATION",
                        value=float(added),
                        sample_count=after.size,
                    )
                )
            )
        return dict(
            noise=self.noise(candidate),
            diffuse=self.signal(candidate),
            stellar_flux=self.stellar_flux(candidate, pairs or []),
            clipping=dict(per_channel=clipping, unit="normalized_pixel"),
            reference=self.audit(),
        )

    @staticmethod
    def validate_metadata(source: Path | str, candidate: Path | str) -> None:
        """Refuse orientation, WCS ou unités altérés ; ne rééchantillonne rien."""
        before, after = fits.getheader(source), fits.getheader(candidate)
        keys = {"ROWORDER", "BUNIT", "BAYERPAT"}
        keys |= {
            key
            for key in before
            if key.startswith(
                (
                    "CTYPE",
                    "CRPIX",
                    "CRVAL",
                    "CDELT",
                    "CD",
                    "PC",
                    "PV",
                    "PS",
                    "RADESYS",
                    "EQUINOX",
                    "A_",
                    "B_",
                    "AP_",
                    "BP_",
                )
            )
        }
        if any(before.get(key) != after.get(key) for key in keys):
            raise ValueError("Orientation, WCS ou unités modifiés par le moteur")
        with fits.open(source) as original, fits.open(candidate) as changed:
            if len(original) != len(changed):
                raise ValueError("Extensions FITS perdues par le moteur")
            for left, right in zip(original[1:], changed[1:]):
                same_data = (left.data is None and right.data is None) or (
                    left.data is not None
                    and right.data is not None
                    and np.array_equal(
                        left.data, right.data, equal_nan=left.data.dtype.kind in "fc"
                    )
                )
                if left.header != right.header or not same_data:
                    raise ValueError("Extension FITS auxiliaire modifiée")


def reference_for(
    path: Path | str, args: argparse.Namespace | None = None
) -> QualityReference:
    """Réutilise la référence d’étape uniquement pour son entrée effective."""
    reference = getattr(args, "_quality_reference", None)
    if reference is not None and reference.path == Path(path).resolve():
        return reference
    return QualityReference(path, args)
