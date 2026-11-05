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
    gaussian_filter,
    label,
    maximum_filter,
)

from lib.parameterized_component import ParameterizedComponent
from lib.type_defs import ConfigValue, JSONReport

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
    """Façade compatible qui délègue les options aux processeurs propriétaires."""

    @classmethod
    def parameter_children(cls) -> tuple[type[ParameterizedComponent], ...]:
        """Déclare les deux branches de préparation et de diagnostic d'image."""
        from lib.quality_processors import ArtifactQualityProcessor, ReferencePreparationProcessor

        return (ReferencePreparationProcessor, ArtifactQualityProcessor)

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Expose les options par récursion, sans dictionnaire central de déclarations."""
        for child in cls.parameter_children():
            child.add_arguments(parser)

    @classmethod
    def argument_defaults(cls) -> dict[str, ConfigValue]:
        """Collecte les défauts déclarés par les propriétaires, pour les API historiques."""
        defaults = {}
        for child in cls.parameter_children():
            defaults.update(child.parameter_defaults())
        return defaults


class QualityReference:
    """Référence figée d’une comparaison, indépendante des moteurs et du gradient.

    Les exclusions sont l’union conservative des canaux. Les régions brillantes
    étendues et les tendances ambiguës restent exclues même si le fond local les
    absorbe. Un champ sans ciel identifiable donne UNKNOWN. Toutes les cartes
    sont calculées avant les moteurs par ReferencePreparationProcessor et ses
    enfants masques/tuiles ; deepcopy partage cette référence immuable.
    Les méthodes historiques de mesure délèguent aux processeurs indépendants
    décrits dans docs/scripts/postProcess/quality-processors.md.
    """

    def __init__(
        self, path: Path | str, args: argparse.Namespace | None = None
    ) -> None:
        """Prépare les cartes par processeurs, puis scelle les tableaux partagés.

        La préparation est chronométrée par phase, sans extraire ni soustraire
        de gradient au FITS. Les options scientifiques restent celles de args.
        """
        self.path = Path(path).resolve()
        self.data = normalized_planes(self.path)
        self.top_down = (
            fits.getheader(self.path).get("ROWORDER", "BOTTOM-UP") == "TOP-DOWN"
        )
        defaults = RestorationQuality.argument_defaults()
        self.tile_size = getattr(args, 'quality_tile_size', defaults['quality_tile_size'])
        self.min_tiles = getattr(args, 'quality_min_tiles', defaults['quality_min_tiles'])
        self.source_sigma = getattr(args, 'quality_source_sigma', defaults['quality_source_sigma'])
        self.max_noise_tiles = getattr(args, 'quality_max_noise_tiles', defaults['quality_max_noise_tiles'])
        self.max_diffuse_regions = getattr(
            args, 'quality_max_diffuse_regions', defaults['quality_max_diffuse_regions'],
        )
        from lib.diffuse_regions import validate_region_limit
        validate_region_limit(self.max_diffuse_regions)
        self.noise_batch_size = getattr(args, 'quality_noise_batch_size', defaults['quality_noise_batch_size'])
        self.noise_compute_backend = getattr(args, 'compute_backend', 'auto')
        self.noise_backend_override = getattr(args, 'quality_noise_backend', None)
        if self.noise_backend_override not in (None, 'auto', 'cpu', 'cuda', 'mps'):
            raise ValueError('quality-noise-backend doit être auto, cpu, cuda ou mps')
        self.diffuse_compute_backend = getattr(args, 'quality_diffuse_backend', None)
        if self.diffuse_compute_backend not in (None, 'auto', 'cpu', 'cuda', 'mps'):
            raise ValueError('quality-diffuse-backend doit être auto, cpu, cuda ou mps')
        from lib.quality_processors import SkyNoiseProcessor

        SkyNoiseProcessor.validate_settings(self.max_noise_tiles, self.noise_batch_size, self.min_tiles)
        self.scales = tuple(getattr(args, 'quality_scales', defaults['quality_scales']))
        self.growth = getattr(args, 'quality_mask_growth', defaults['quality_mask_growth'])
        self.min_gain = getattr(args, 'quality_min_noise_gain', defaults['quality_min_noise_gain'])
        self.flux_loss = getattr(args, 'quality_max_flux_loss', defaults['quality_max_flux_loss'])
        self.enforce_significance = getattr(args, 'quality_enforce_significance', defaults['quality_enforce_significance'])
        self.enforce_diffuse = getattr(args, 'quality_enforce_diffuse', defaults['quality_enforce_diffuse'])
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
        from lib.quality_processors import QualityContext, ReferencePreparationProcessor

        context = QualityContext(self.path, None, self.data, self.data, self)
        self.preparation_processing = ReferencePreparationProcessor(context).post_process(
            self.path, None, args,
        )['processing']
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

    def audit(self) -> JSONReport:
        """Résume provenance, couverture, politique et limites scientifiques."""
        quadrants = {
            int(ys.start >= self.sky_mask.shape[0] / 2) * 2
            + int(xs.start >= self.sky_mask.shape[1] / 2)
            for ys, xs in self.tiles
        }
        return dict(
            metrics_version=3,
            preparation_processing=self.preparation_processing,
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
        """Délègue au processeur SkyNoiseProcessor, avec chronométrage propre."""
        from lib.quality_processors import QualityContext, SkyNoiseProcessor

        context = QualityContext(self.path, None, self.data, candidate, self)
        return SkyNoiseProcessor(context).post_process(
            self.path, None, argparse.Namespace(
                quality_max_noise_tiles=self.max_noise_tiles,
                quality_noise_batch_size=self.noise_batch_size,
                quality_noise_backend=self.noise_backend_override,
                compute_backend=self.noise_compute_backend,
            ),
        )

    def signal(self, candidate: np.ndarray) -> JSONReport:
        """Délègue au processeur DiffuseSignalProcessor, avec chronométrage propre."""
        from lib.quality_processors import QualityContext, DiffuseSignalProcessor

        context = QualityContext(self.path, None, self.data, candidate, self)
        return DiffuseSignalProcessor(context).post_process(
            self.path, None, argparse.Namespace(
                quality_max_diffuse_regions=self.max_diffuse_regions,
                quality_diffuse_backend=self.diffuse_compute_backend,
                quality_noise_backend=self.noise_backend_override,
                compute_backend=self.noise_compute_backend,
            ),
        )

    def stellar_flux(
        self, candidate: np.ndarray, pairs: list[dict[str, float]]
    ) -> JSONReport:
        """Délègue au processeur StellarFluxProcessor, avec chronométrage propre."""
        from lib.quality_processors import QualityContext, StellarFluxProcessor

        context = QualityContext(self.path, None, self.data, candidate, self, pairs=tuple(pairs))
        return StellarFluxProcessor(context).post_process(self.path, None)

    def evaluate(
        self, path: Path | str, pairs: list[dict[str, float]] | None = None
    ) -> JSONReport:
        """Délègue récursivement aux contrôles indépendants avec la référence fixe."""
        from lib.quality_processors import QualityContext, ReferenceEvaluationProcessor

        context = QualityContext(
            self.path, Path(path), self.data, normalized_planes(path), self,
            pairs=tuple(pairs or ()),
        )
        return ReferenceEvaluationProcessor(context).post_process(
            Path(path), None, argparse.Namespace(
                quality_max_diffuse_regions=self.max_diffuse_regions,
                quality_diffuse_backend=self.diffuse_compute_backend,
                quality_noise_backend=self.noise_backend_override,
                compute_backend=self.noise_compute_backend,
            ),
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
