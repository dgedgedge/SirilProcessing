"""Processeurs récursifs des préparations et contrôles de qualité d'image.

Les calculs indépendants utilisent l'interface processor. Les enfants partagent
un contexte d'image et une référence, et produisent des diagnostics JSON. Les
frontières scientifiques et les unités sont décrites dans
``docs/scripts/postProcess/quality-processors.md``.
"""

from __future__ import annotations

import argparse
from abc import abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from copy import deepcopy
import json
import logging
from pathlib import Path

import numpy as np
from scipy.ndimage import (
    binary_dilation, find_objects, gaussian_filter, label, maximum_filter, median_filter,
)

from lib.logging_utils import log_duration
from lib.processor import processor
from lib.restoration_quality import (
    QualityReference, bootstrap_interval, bootstrap_mean_interval,
    criterion, robust_sigma,
)
from lib.type_defs import ConfigValue, JSONReport


@dataclass(frozen=True)
class QualityContext:
    """Données partagées d'un audit, sans état d'exécution global.

    before et candidate sont des plans normalisés mono/RGB, non modifiés par
    les processeurs. reference porte les masques fixes si le mode l'exige.
    Seule la préparation peut initialiser les cartes d'une référence non scellée.
    pairs désigne les mesures stellaires appariées, dans les coordonnées Siril.
    """

    source: Path
    candidate_path: Path | None
    before: np.ndarray
    candidate: np.ndarray
    reference: QualityReference | None = None
    pairs: Sequence[dict[str, float]] = ()
    top_down: bool = False

    @classmethod
    def for_catalogs(
        cls, source: Path | str, candidate: Path | str | None = None,
    ) -> QualityContext:
        """Référence les chemins stellaires sans charger les grandes images.

        Les processeurs de catalogue lisent uniquement les plans dont ils ont
        besoin. Ce contexte n'est pas destiné aux diagnostics sur tuiles.
        """
        empty = np.empty((0, 0, 0), dtype=np.float32)
        return cls(Path(source), Path(candidate) if candidate is not None else None, empty, empty)


class QualityProcessor(processor):
    """Étape d'analyse avec contexte partagé, audit récursif et durée propre."""

    stage_key = "quality"
    stage_label = "Qualité"
    CONFIG_DEFAULTS: dict[str, ConfigValue] = {}

    def __init__(self, context: QualityContext) -> None:
        """Attache les données de cet appel sans recopier les grandes images."""
        self.context = context
        self._child_processing: list[JSONReport] = []
        self.last_processing: JSONReport | None = None

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Délègue récursivement les options aux enfants déclarés par la classe."""
        for child in cls.parameter_children():
            child.add_arguments(parser)

    @classmethod
    def parameter_children(cls) -> tuple[type[QualityProcessor], ...]:
        """Déclare l'absence d'enfant par défaut pour un processeur feuille."""
        return ()

    @classmethod
    def argument_defaults(cls) -> dict[str, ConfigValue]:
        """Copie les défauts locaux et ceux des bases portant des options partagées."""
        defaults = {}
        for base in reversed(cls.__mro__):
            defaults.update(deepcopy(base.__dict__.get('CONFIG_DEFAULTS', {})))
        return defaults

    @classmethod
    def parameter_defaults(cls) -> dict[str, ConfigValue]:
        """Collecte récursivement les défauts, sans redéclarer de paramètres."""
        defaults = cls.argument_defaults()
        for child in cls.parameter_children():
            defaults.update(child.parameter_defaults())
        return defaults

    @staticmethod
    def has_argument(parser: argparse.ArgumentParser, destination: str) -> bool:
        """Évite de redéclarer une option lorsque deux branches partagent un enfant."""
        return any(action.dest == destination for action in parser._actions)

    @classmethod
    def get_prefix(cls) -> str:
        """Retourne l'identifiant stable du diagnostic dans l'audit."""
        return cls.stage_key

    def child(
        self, cls: type[QualityProcessor], args: argparse.Namespace,
        context: QualityContext | None = None,
    ) -> JSONReport:
        """Exécute récursivement un enfant et conserve son audit de temps seul."""
        context = self.context if context is None else context
        return self.run_child(cls(context), args)

    def run_child(self, child: QualityProcessor, args: argparse.Namespace) -> JSONReport:
        """Exécute un enfant configuré explicitement et conserve aussi son audit d'échec."""
        context = child.context
        try:
            return child.post_process(context.candidate_path or context.source, None, args)
        finally:
            if child.last_processing is not None:
                self._child_processing.append(child.last_processing)

    def post_process(
        self, input_path: Path, output_path: Path | None,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Analyse les données du contexte et écrit le JSON si un chemin est fourni.

        input_path doit identifier le candidat (ou la référence pour la
        préparation). output_path=None sert aux sous-étapes en mémoire : les
        rapports moteurs englobants conservent leurs résultats et durées.
        Chaque appel réinitialise son audit d'enfants. Une erreur est chronométrée,
        éventuellement écrite dans son rapport, puis propagée sans changement.
        """
        expected = self.context.candidate_path or self.context.source
        if Path(input_path).resolve() != expected.resolve():
            raise ValueError("Le chemin d'entrée ne correspond pas au contexte qualité")
        if output_path is not None and Path(output_path).resolve() in {
            expected.resolve(), self.context.source.resolve(),
        }:
            raise ValueError("Le rapport qualité doit être distinct des images")
        options = self._get_args(args)
        self._child_processing = []
        durations: dict[str, float] = {}
        result: JSONReport = {}
        failed = True
        self.last_processing = None
        try:
            with log_duration(self.stage_label, durations, 'duration_seconds'):
                result = self._process(options)
                failed = False
            return result
        except Exception as error:
            result.update(status='failed', error=str(error))
            raise
        finally:
            result['processing'] = dict(
                processor=type(self).__name__, stage=self.stage_key,
                duration_seconds=durations['duration_seconds'],
                execution_status='failed' if failed else 'completed',
                children=self._child_processing,
            )
            self.last_processing = result['processing']
            if output_path is not None:
                destination = Path(output_path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    json.dumps(result, indent=2, allow_nan=False), encoding='utf-8',
                )

    @abstractmethod
    def _process(self, args: argparse.Namespace) -> JSONReport:
        """Réalise le calcul spécialisé ou l'assemblage des résultats enfants."""
        raise NotImplementedError

    def reference(self) -> QualityReference:
        """Exige la référence préparée par l'orchestrateur propriétaire."""
        if self.context.reference is None:
            raise ValueError("Une référence de qualité préparée est requise")
        return self.context.reference


class SkyNoiseProcessor(QualityProcessor):
    """Qualité — bruit sur tuiles ; conserve les définitions et seuils scientifiques existants."""

    stage_key = "noise"
    stage_label = "Qualité — bruit sur tuiles"

    CONFIG_DEFAULTS = {
        'quality_max_noise_tiles': 256, 'quality_noise_batch_size': 32,
        'quality_noise_backend': None,
        'quality_min_tiles': 4, 'quality_min_noise_gain': .05,
        'quality_enforce_significance': False,
    }
    # Cible mémoire des temporaires ; une très grande tuile exige au moins un lot unitaire.
    BATCH_MEMORY_BYTES = 64 * 1024 * 1024
    TEMPORARY_ARRAYS = 24
    MIN_GPU_TILES = 32

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare effectif, gain significatif, plafond et lots du bruit sur tuiles."""
        super().add_arguments(parser)
        defaults = cls.argument_defaults()
        if not cls.has_argument(parser, 'quality_min_tiles'):
            parser.add_argument('--quality-min-tiles', type=int, default=defaults['quality_min_tiles'])
        if not cls.has_argument(parser, 'quality_min_noise_gain'):
            parser.add_argument('--quality-min-noise-gain', type=float, default=defaults['quality_min_noise_gain'])
        if not cls.has_argument(parser, 'quality_enforce_significance'):
            parser.add_argument(
                '--quality-enforce-significance', action=argparse.BooleanOptionalAction,
                default=defaults['quality_enforce_significance'],
                help='Exiger un gain utile significatif ; sinon observation',
            )
        if not any(action.dest == 'quality_max_noise_tiles' for action in parser._actions):
            parser.add_argument(
                '--quality-max-noise-tiles', type=int, default=cls.CONFIG_DEFAULTS['quality_max_noise_tiles'],
                help='Plafond des tuiles de bruit réparties sur le champ (défaut 256 ; 0 = toutes)',
            )
        if not any(action.dest == 'quality_noise_batch_size' for action in parser._actions):
            parser.add_argument(
                '--quality-noise-batch-size', type=int, default=cls.CONFIG_DEFAULTS['quality_noise_batch_size'],
                help='Taille maximale des lots de tuiles NumPy/PyTorch, entre 1 et 256 (défaut 32)',
            )

        if not cls.has_argument(parser, 'quality_noise_backend'):
            parser.add_argument('--quality-noise-backend', choices=('auto', 'cpu', 'cuda', 'mps'),
                                default=defaults['quality_noise_backend'],
                                help='Moteur du bruit sur tuiles ; sinon compute-backend')

    @classmethod
    def validate_settings(cls, maximum: int, batch_size: int, minimum: int) -> None:
        """Refuse un plafond incompatible avec l'effectif minimal ou un lot non borné."""
        if (not isinstance(maximum, int) or isinstance(maximum, bool)
                or maximum < 0 or (maximum != 0 and maximum < max(3, minimum))):
            raise ValueError('quality-max-noise-tiles doit être 0 ou au moins max(3, quality-min-tiles)')
        if (not isinstance(batch_size, int) or isinstance(batch_size, bool)
                or not 1 <= batch_size <= 256):
            raise ValueError('quality-noise-batch-size doit être un entier entre 1 et 256')

    def _process(self, args: argparse.Namespace) -> JSONReport:
        """Calcule ce diagnostic sur les données partagées sans modifier les pixels."""
        import logging
        from lib.compute_config import ComputeConfig
        from lib.noise_tiles import NoiseTileBackend, spatial_noise_tiles

        reference = self.reference()
        candidate = self.context.candidate
        maximum = getattr(args, 'quality_max_noise_tiles', reference.max_noise_tiles)
        requested_batch_size = getattr(args, 'quality_noise_batch_size', reference.noise_batch_size)
        self.validate_settings(maximum, requested_batch_size, reference.min_tiles)
        coverage = reference.audit()
        tiles = spatial_noise_tiles(reference.tiles, reference.sky_mask.shape, maximum)
        height, width = reference.sky_mask.shape
        quadrants = {
            2 * int(ys.start >= height / 2) + int(xs.start >= width / 2) for ys, xs in tiles
        }
        sampling = dict(
            available_tiles=len(reference.tiles), evaluated_tiles=len(tiles),
            maximum_tiles=maximum, quadrant_count=len(quadrants),
            method='all' if len(tiles) == len(reference.tiles) else 'spatial_farthest_points',
            selected_tiles=[dict(y_start=ys.start, y_stop=ys.stop, x_start=xs.start, x_stop=xs.stop)
                            for ys, xs in tiles],
        )
        if len(tiles) < reference.min_tiles or len(quadrants) < 3:
            return dict(
                criterion('UNKNOWN', 'NO_RELIABLE_SKY', sample_count=len(tiles)),
                per_channel=[], reference=coverage, sampling=sampling, max_noise_ratio=None,
            )
        tile_height = max(ys.stop - ys.start for ys, _ in tiles)
        tile_width = max(xs.stop - xs.start for _, xs in tiles)
        bytes_per_tile = tile_height * tile_width * 8 * self.TEMPORARY_ARRAYS
        batch_size = min(requested_batch_size, len(tiles), max(1, self.BATCH_MEMORY_BYTES // bytes_per_tile))
        policy = getattr(args, 'compute_backend', reference.noise_compute_backend)
        requested = ComputeConfig.requested(argparse.Namespace(
            compute_backend=policy, quality_noise_backend=getattr(
                args, 'quality_noise_backend', getattr(reference, 'noise_backend_override', None)),
        ), 'quality_noise_backend')
        # Les petits lots ne justifient pas l'initialisation et le transfert GPU.
        small_cpu = requested == 'auto' and len(tiles) < self.MIN_GPU_TILES
        backend = NoiseTileBackend(
            'cpu' if small_cpu else requested, batch_size * bytes_per_tile,
            engine='Bruit sur tuiles',
        )
        backend_audit = backend.report()
        backend_audit.update(
            requested=requested,
            reason='Small tile set processed on CPU' if small_cpu else backend.reason,
            precision='cpu_float64_residuals_gpu_float32_statistics' if backend.name == 'mps' else 'float64',
            batch_size=batch_size, requested_batch_size=requested_batch_size,
            batches_per_channel=(len(tiles) + batch_size - 1) // batch_size,
            memory_budget_bytes=self.BATCH_MEMORY_BYTES,
            estimated_batch_bytes=batch_size * bytes_per_tile,
        )
        logging.info('Bruit : %d/%d tuiles, %d quadrants, lots de %d, backend %s',
                     len(tiles), len(reference.tiles), len(quadrants), batch_size, backend.name)
        channels = []
        for before, after in zip(reference.data, candidate):
            ratios, correlations, dispersions = [], [], []
            for start in range(0, len(tiles), batch_size):
                selected = tiles[start:start + batch_size]
                shape = (len(selected), tile_height, tile_width)
                before_batch, after_batch = np.zeros(shape, dtype=np.float64), np.zeros(shape, dtype=np.float64)
                masks = np.zeros(shape, dtype=bool)
                for index, (ys, xs) in enumerate(selected):
                    h, w = ys.stop - ys.start, xs.stop - xs.start
                    before_batch[index, :h, :w] = before[ys, xs]
                    after_batch[index, :h, :w] = after[ys, xs]
                    masks[index, :h, :w] = reference.sky_mask[ys, xs]
                sigma, correlation = backend.statistics(before_batch, after_batch, masks)
                valid = sigma[:, 0] > 1e-10
                ratios.extend((sigma[valid, 1] / sigma[valid, 0]).tolist())
                dispersions.extend(sigma[valid].tolist())
                correlations.extend([
                    [float(value) if np.isfinite(value) else None for value in pair]
                    for pair in correlation[valid]
                ])
            if len(ratios) < reference.min_tiles:
                channels.append(
                    dict(
                        criterion("UNKNOWN", "TOO_FEW_TILES", sample_count=len(ratios))
                    )
                )
                continue
            ratio = float(np.median(ratios))
            lo, hi = bootstrap_interval(np.array(ratios))
            gain = 1 - ratio
            proven = 1 - hi >= reference.min_gain
            channels.append(
                dict(
                    criterion(
                        "PASS" if proven else "UNKNOWN",
                        "USEFUL_NOISE_REDUCTION" if proven else "GAIN_NOT_DEMONSTRATED",
                        value=gain,
                        threshold=reference.min_gain,
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
            sampling=sampling, backend=backend_audit,
            per_channel=channels,
            reference=coverage,
            max_noise_ratio=max(c["noise_ratio"] for c in channels) if known else None,
            proven_gain=(
                min(1 - c["ratio_interval"][1] for c in channels) if known else None
            ),
        )



class FluxQualityProcessor(QualityProcessor):
    """Porte les gardes de flux réellement communes au diffus et aux étoiles."""

    CONFIG_DEFAULTS = {'quality_max_flux_loss': .05, 'quality_enforce_diffuse': False}

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare une seule fois la tolérance et l'activation des gardes photométriques."""
        super().add_arguments(parser)
        defaults = cls.argument_defaults()
        if not cls.has_argument(parser, 'quality_max_flux_loss'):
            parser.add_argument('--quality-max-flux-loss', type=float, default=defaults['quality_max_flux_loss'])
        if not cls.has_argument(parser, 'quality_enforce_diffuse'):
            parser.add_argument(
                '--quality-enforce-diffuse', action=argparse.BooleanOptionalAction,
                default=defaults['quality_enforce_diffuse'],
                help='Rendre les contrôles de flux bloquants ; défaut observation',
            )


class DiffuseSignalProcessor(FluxQualityProcessor):
    """Qualité — signal diffus ; conserve les définitions et seuils scientifiques existants."""

    stage_key = "diffuse"
    stage_label = "Qualité — signal diffus"

    CONFIG_DEFAULTS = {'quality_max_diffuse_regions': 256, 'quality_diffuse_backend': None}

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare le budget des régions, après les gardes de flux partagées."""
        super().add_arguments(parser)
        if not cls.has_argument(parser, 'quality_max_diffuse_regions'):
            parser.add_argument(
                '--quality-max-diffuse-regions', type=int,
                default=cls.CONFIG_DEFAULTS['quality_max_diffuse_regions'],
                help='Plafond des régions diffuses par tailles et positions (défaut 256 ; 0 = toutes)',
            )

        if not cls.has_argument(parser, 'quality_diffuse_backend'):
            parser.add_argument(
                '--quality-diffuse-backend', choices=('auto', 'cpu', 'cuda', 'mps'), default=None,
                help='Surcharge du moteur des filtres diffus ; sinon compute-backend',
            )

    def _process(self, args: argparse.Namespace) -> JSONReport:
        """Calcule ce diagnostic sur les données partagées sans modifier les pixels."""
        reference = self.reference()
        candidate = self.context.candidate
        from lib.diffuse_regions import select_diffuse_regions

        regions, _ = label(reference.object_mask)
        sizes = np.bincount(regions.ravel())
        maximum = getattr(args, 'quality_max_diffuse_regions', reference.max_diffuse_regions)
        selected, sampling = select_diffuse_regions(
            find_objects(regions), sizes, regions.shape, maximum,
        )
        logging.getLogger(__name__).info(
            'Signal diffus : %d/%d régions, %d classes de taille, plafond %d (0 = exhaustif)',
            len(selected), sampling['available_regions'], len(sampling['area_strata']), maximum,
        )
        from lib.compute_config import ComputeConfig
        from lib.diffuse_filters import DiffuseFilterBackend

        policy = ComputeConfig.requested(argparse.Namespace(
            compute_backend=getattr(args, 'compute_backend', getattr(reference, 'noise_compute_backend', 'auto')),
            quality_diffuse_backend=getattr(args, 'quality_diffuse_backend',
                                            getattr(reference, 'diffuse_compute_backend', None)),
        ), 'quality_diffuse_backend')
        margin = int(4 * max(reference.scales)) + 2 * reference.growth + 8
        maximum_pixels = max((
            (min(regions.shape[0], region.bounds[0].stop + margin)
             - max(0, region.bounds[0].start - margin))
            * (min(regions.shape[1], region.bounds[1].stop + margin)
               - max(0, region.bounds[1].start - margin)) for region in selected
        ), default=0)
        filters = DiffuseFilterBackend(
            policy, maximum_pixels,
            requires_float64=any(array.dtype == np.float64 for array in (reference.data, candidate)),
        ) if selected else None
        entries = []
        for region in selected:
            index, bounds = region.identifier, region.bounds
            ys, xs = (
                slice(max(0, axis.start - margin), min(length, axis.stop + margin))
                for axis, length in zip(bounds, regions.shape)
            )
            local_regions = regions[ys, xs]
            mask = local_regions == index
            # Inclure une marge de redistribution ; les étoiles restent exclues.
            aperture = (
                binary_dilation(mask, iterations=max(8, 2 * reference.growth))
                & ~reference.star_mask[ys, xs]
                & ~reference.invalid_mask[ys, xs]
            )
            annulus = (
                binary_dilation(aperture, iterations=reference.growth + 4)
                & reference.sky_mask[ys, xs]
            )
            for channel, (before_full, after_full) in enumerate(
                zip(reference.data, candidate)
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
                for y in range(0, mask.shape[0], reference.tile_size):
                    for x in range(0, mask.shape[1], reference.tile_size):
                        tile = aperture[y : y + reference.tile_size, x : x + reference.tile_size]
                        if tile.sum() >= 16:
                            blocks.append(
                                float(
                                    np.sum(
                                        difference[
                                            y : y + reference.tile_size,
                                            x : x + reference.tile_size,
                                        ][tile]
                                    )
                                )
                            )
                ci = (
                    bootstrap_mean_interval(np.array(blocks))
                    if len(blocks) >= 4
                    else None
                )
                sigma = float(np.median(reference.sigmas[channel][ys, xs][annulus]))
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
                scale_results, corroborated = filters.multiscale(
                    before - bg_before, difference, aperture, reference.sigmas[channel][ys, xs],
                    reference.scales, reference.source_sigma, sigma,
                )
                significant = (
                    ci is not None
                    and (ci[0] > 0 or ci[1] < 0)
                    and abs(fb - fa) > 3 * uncertainty
                )
                lost = abs(loss) > reference.flux_loss and significant and corroborated
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
                            threshold=reference.flux_loss,
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
            blocking=reference.enforce_diffuse,
            per_region=entries,
            sampling=sampling,
            backend=filters.report() if filters is not None else {'name': 'not_used', 'reason': 'No diffuse region'},
        )



class StellarFluxProcessor(FluxQualityProcessor):
    """Qualité — flux stellaire ; conserve les définitions et seuils scientifiques existants."""

    stage_key = "stellar_flux"
    stage_label = "Qualité — flux stellaire"

    def _process(self, args: argparse.Namespace) -> JSONReport:
        """Calcule ce diagnostic sur les données partagées sans modifier les pixels."""
        reference = self.reference()
        candidate = self.context.candidate
        pairs = self.context.pairs
        entries, channels = [], []
        height, width = reference.sky_mask.shape
        for channel, (before, after) in enumerate(zip(reference.data, candidate)):
            groups = {}
            for pair in pairs:
                radius = max(6, int(np.ceil(3 * pair["fwhm_before_px"])))
                x = int(round(pair["x_before"] - 0.5))
                y = int(
                    round(
                        height - pair["y_before"] - 0.5
                        if reference.top_down
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
                changed = hi < -reference.flux_loss or lo > reference.flux_loss
                channels.append(
                    dict(
                        criterion(
                            "FAIL" if changed else "PASS",
                            "STELLAR_FLUX_CHANGE",
                            value=float(np.median(values)),
                            threshold=reference.flux_loss,
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
            blocking=reference.enforce_diffuse,
            per_channel=channels,
            apertures=entries,
        )



class MaskPreparationProcessor(QualityProcessor):
    """Qualité — constitution des masques ; conserve les définitions et seuils scientifiques existants."""

    stage_key = "masks"
    stage_label = "Qualité — constitution des masques"

    CONFIG_DEFAULTS = {
        'quality_source_sigma': 4.0, 'quality_scales': [2, 4, 8, 16, 32],
        'quality_mask_growth': 5,
    }

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare les seuils, échelles et marges de constitution des masques."""
        super().add_arguments(parser)
        defaults = cls.argument_defaults()
        if not cls.has_argument(parser, 'quality_source_sigma'):
            parser.add_argument('--quality-source-sigma', type=float, default=defaults['quality_source_sigma'])
        if not cls.has_argument(parser, 'quality_scales'):
            parser.add_argument('--quality-scales', type=float, nargs='+', default=defaults['quality_scales'])
        if not cls.has_argument(parser, 'quality_mask_growth'):
            parser.add_argument('--quality-mask-growth', type=int, default=defaults['quality_mask_growth'])

    def _process(self, args: argparse.Namespace) -> JSONReport:
        """Calcule ce diagnostic sur les données partagées sans modifier les pixels."""
        reference = self.reference()
        shape = reference.data.shape[1:]
        reference.invalid_mask = ~np.isfinite(reference.data).all(axis=0)
        reference.invalid_mask[:2] = reference.invalid_mask[-2:] = True
        reference.invalid_mask[:, :2] = reference.invalid_mask[:, -2:] = True
        reference.star_mask = np.zeros(shape, dtype=bool)
        reference.object_mask = np.zeros(shape, dtype=bool)
        reference.uncertain_mask = np.zeros(shape, dtype=bool)
        reference.backgrounds, reference.sigmas = [], []
        reference.segmentation_audit = []
        for plane in reference.data:
            clean = np.where(np.isfinite(plane), plane, np.nanmedian(plane))
            background, sigma = reference._local_maps(clean, reference.invalid_mask)
            reference.backgrounds.append(background)
            reference.sigmas.append(sigma)
            residual = clean - background
            smooth = gaussian_filter(residual, 1)
            narrow = smooth - gaussian_filter(residual, 3)
            compact = (smooth == maximum_filter(smooth, size=7)) & (
                narrow > 2 * reference.source_sigma * sigma
            )
            reference.star_mask |= binary_dilation(
                compact, iterations=max(8, 2 * reference.growth)
            )
        # Toutes les étoiles RGB sont exclues avant toute détection diffuse :
        # sinon leur lumière lissée devient un faux fond diffus jusque dans le ciel.
        for plane, background, sigma in zip(
            reference.data, reference.backgrounds, reference.sigmas,
        ):
            clean = np.where(np.isfinite(plane), plane, np.nanmedian(plane))
            objects, unsupported, scales_audit = reference._diffuse_objects(
                clean - background, sigma
            )
            reference.object_mask |= objects
            reference.uncertain_mask |= unsupported
            reference.segmentation_audit.append(scales_audit)
            # Diagnostic conservateur à grande échelle, indépendant du fond local.
            floor = (
                float(np.quantile(clean[~reference.invalid_mask], 0.1))
                if (~reference.invalid_mask).any()
                else 0
            )
            broad = (
                gaussian_filter(
                    np.where(reference.star_mask, background, clean), max(reference.scales)
                )
                - floor
            )
            reference.uncertain_mask |= broad > 3 * sigma
        reference.object_mask = binary_dilation(
            reference.object_mask, iterations=max(1, reference.growth)
        )
        reference.object_mask &= ~reference.star_mask & ~reference.invalid_mask
        reference.uncertain_mask &= ~reference.object_mask & ~reference.star_mask
        reference.sky_mask = ~(
            reference.invalid_mask | reference.star_mask
            | reference.object_mask | reference.uncertain_mask
        )
        # Réestimation diagnostique limitée, sans réviser les exclusions à la baisse.
        reference.backgrounds, reference.sigmas = zip(
            *[
                reference._local_maps(np.nan_to_num(plane), ~reference.sky_mask)
                for plane in reference.data
            ]
        )
        return {'status': 'prepared'}



class SkyTileProcessor(QualityProcessor):
    """Qualité — sélection des tuiles ; conserve les définitions et seuils scientifiques existants."""

    stage_key = "tiles"
    stage_label = "Qualité — sélection des tuiles"

    CONFIG_DEFAULTS = {'quality_tile_size': 64}

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare la taille des tuiles de ciel, en pixels."""
        super().add_arguments(parser)
        if not cls.has_argument(parser, 'quality_tile_size'):
            parser.add_argument('--quality-tile-size', type=int, default=cls.CONFIG_DEFAULTS['quality_tile_size'])

    def _process(self, args: argparse.Namespace) -> JSONReport:
        """Calcule ce diagnostic sur les données partagées sans modifier les pixels."""
        reference = self.reference()
        tiles = []
        height, width = reference.sky_mask.shape
        for y in range(0, height, reference.tile_size):
            for x in range(0, width, reference.tile_size):
                ys, xs = slice(y, min(y + reference.tile_size, height)), slice(
                    x, min(x + reference.tile_size, width)
                )
                mask = reference.sky_mask[ys, xs]
                if mask.sum() >= max(128, 0.5 * mask.size):
                    tiles.append((ys, xs))
        return {
            'tile_count': len(tiles),
            'tiles': [dict(y_start=ys.start, y_stop=ys.stop, x_start=xs.start, x_stop=xs.stop)
                      for ys, xs in tiles],
        }



class ReferencePreparationProcessor(QualityProcessor):
    """Prépare les masques, puis les tuiles qui dépendent de ces exclusions."""

    stage_key = 'reference_preparation'
    stage_label = 'Qualité — préparation de référence'

    @classmethod
    def parameter_children(cls) -> tuple[type[processor], ...]:
        """Déclare la dépendance de sélection des tuiles sur les masques."""
        return (MaskPreparationProcessor, SkyTileProcessor)

    def _process(self, args: argparse.Namespace) -> JSONReport:
        """Initialise les cartes, puis les tuiles, sur la référence non scellée."""
        self.child(MaskPreparationProcessor, args)
        result = self.child(SkyTileProcessor, args)
        self.reference().tiles = tuple(
            (slice(tile['y_start'], tile['y_stop']), slice(tile['x_start'], tile['x_stop']))
            for tile in result['tiles']
        )
        return {'tile_count': result['tile_count']}


class SaturationQualityProcessor(QualityProcessor):
    """Qualité — nouveaux pixels saturés ; conserve les définitions et seuils scientifiques existants."""

    stage_key = "clipping"
    stage_label = "Qualité — nouveaux pixels saturés"

    def _process(self, args: argparse.Namespace) -> JSONReport:
        """Calcule ce diagnostic sur les données partagées sans modifier les pixels."""
        reference = self.reference()
        candidate = self.context.candidate
        clipping = []
        for before, after in zip(reference.data, candidate):
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
        return dict(per_channel=clipping, unit='normalized_pixel')



class GeometryQualityProcessor(QualityProcessor):
    """Qualité — géométrie et métadonnées ; conserve les définitions et seuils scientifiques existants."""

    stage_key = "geometry"
    stage_label = "Qualité — géométrie et métadonnées"

    def _process(self, args: argparse.Namespace) -> JSONReport:
        """Calcule ce diagnostic sur les données partagées sans modifier les pixels."""
        reference = self.reference()
        candidate = self.context.candidate
        if candidate.shape != reference.data.shape or not np.isfinite(candidate).all():
            raise ValueError("Géométrie différente ou pixels candidats non finis")
        if self.context.candidate_path is not None:
            reference.validate_metadata(reference.path, self.context.candidate_path)
        return {'status': 'PASS'}



class ReferenceEvaluationProcessor(QualityProcessor):
    """Valide le candidat puis exécute les diagnostics indépendants de référence."""

    stage_key = 'reference_evaluation'
    stage_label = 'Qualité — diagnostics sur référence fixe'

    @classmethod
    def parameter_children(cls) -> tuple[type[processor], ...]:
        """Expose tous les diagnostics, sans les faire dépendre les uns des autres."""
        return (GeometryQualityProcessor, SkyNoiseProcessor, DiffuseSignalProcessor,
                StellarFluxProcessor, SaturationQualityProcessor)

    def _process(self, args: argparse.Namespace) -> JSONReport:
        """Valide les données une fois et assemble les résultats nommés des feuilles."""
        self.child(GeometryQualityProcessor, args)
        result = {}
        for cls in self.parameter_children()[1:]:
            result[cls.get_prefix()] = self.child(cls, args)
        result['reference'] = self.reference().audit()
        return result


class LegacyNoiseProcessor(QualityProcessor):
    """Qualité — bruit haute fréquence historique, sans dépendre des autres mesures de qualité."""

    stage_key = 'legacy_noise'
    stage_label = 'Qualité — bruit haute fréquence historique'

    def _process(self, args: argparse.Namespace) -> JSONReport:
        """Analyse les mêmes plans avant/après et conserve les unités existantes."""
        channels = []
        original, changed = self.context.before, self.context.candidate
        pairs, top_down = self.context.pairs, self.context.top_down
        for before, after in zip(original, changed):
            finite = np.isfinite(before)
            if not finite.any():
                raise ValueError("Image originale sans pixels finis")
            # Masque fixe : pixels faibles, déterminés exclusivement sur l'original.
            mask = finite & (before <= np.percentile(before[finite], 40))
            mask[:2] = mask[-2:] = False
            mask[:, :2] = mask[:, -2:] = False

            def noise(plane: np.ndarray) -> float:
                """Estime le bruit par MAD du résidu au filtre médian sur le masque de ciel fixe."""
                high = plane - median_filter(plane, size=3)
                values = high[mask]
                return float(1.4826 * np.median(abs(values - np.median(values))))

            nb, na = noise(before), noise(after)
            ratio = na / max(nb, np.finfo(np.float32).eps)
            if mask.sum() < 100 and getattr(args, 'quality_mode', 'reliable') == 'legacy':
                raise ValueError(
                    "Pas assez de ciel ou d’étoiles intérieures pour contrôler les artefacts"
                )
            channels.append(dict(noise_before=nb, noise_after=na, noise_ratio=ratio,
                                 background_pixels=int(mask.sum())))
        return {"channels": channels}



class StellarRingProcessor(QualityProcessor):
    """Qualité — profils radiaux et anneaux stellaires, sans dépendre des autres mesures de qualité."""

    stage_key = 'rings'
    stage_label = 'Qualité — profils radiaux et anneaux stellaires'

    def _process(self, args: argparse.Namespace) -> JSONReport:
        """Analyse les mêmes plans avant/après et conserve les unités existantes."""
        channels = []
        original, changed = self.context.before, self.context.candidate
        pairs, top_down = self.context.pairs, self.context.top_down
        for before, after in zip(original, changed):
            scores = []
            for pair in pairs:
                radius = max(8, int(np.ceil(3 * pair["fwhm_before_px"])))
                x = int(round(pair["x_before"] - 0.5))
                y = int(round(
                    before.shape[0] - pair["y_before"] - .5
                    if top_down else pair["y_before"] - .5
                ))
                if (
                    min(x, y) < radius
                    or x + radius >= before.shape[1]
                    or y + radius >= before.shape[0]
                ):
                    continue
                b = before[y - radius : y + radius + 1, x - radius : x + radius + 1]
                a = after[y - radius : y + radius + 1, x - radius : x + radius + 1]
                yy, xx = np.indices(b.shape)
                rr = np.hypot(xx - radius, yy - radius)
                outer = rr >= 2.5 * pair["fwhm_before_px"]
                bins = np.arange(max(1, 0.8 * pair["fwhm_before_px"]), radius - 1, 1)

                def rings(stamp: np.ndarray) -> float:
                    """Mesure le creux ou rebond maximal du profil radial normalisé autour de l’étoile."""
                    bg = float(np.median(stamp[outer]))
                    peak = max(
                        float(
                            stamp[radius - 1 : radius + 2, radius - 1 : radius + 2].max()
                            - bg
                        ),
                        1e-8,
                    )
                    profile = (
                        np.array(
                            [np.median(stamp[(rr >= r) & (rr < r + 1)]) - bg for r in bins]
                        )
                        / peak
                    )
                    dark = max(0.0, -float(profile.min()))
                    bump = max(0.0, float(np.max(profile - np.minimum.accumulate(profile))))
                    return max(dark, bump)

                if np.isfinite(b).all() and outer.sum() >= 12 and len(bins) >= 3:
                    rb, ra = rings(b), rings(a)
                    scores.append(
                        dict(
                            reference_id=pair["reference_id"],
                            before=rb,
                            after=ra,
                            added=max(0, ra - rb),
                        )
                    )
            if not scores and getattr(args, 'quality_mode', 'reliable') == 'legacy':
                raise ValueError(
                    "Pas assez de ciel ou d’étoiles intérieures pour contrôler les artefacts"
                )
            channels.append(dict(
                rings=scores,
                ring_fraction=float(np.mean([s['added'] > 0.02 for s in scores]))
                if scores else None,
            ))
        return {"channels": channels}



class ArtifactQualityProcessor(QualityProcessor):
    """Orchestre récursivement bruit historique, anneaux et diagnostics fiables."""

    CONFIG_DEFAULTS = {'quality_mode': 'reliable'}

    stage_key = 'artifacts'
    stage_label = "Qualité — ensemble des contrôles d'artefacts"

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare son mode, puis les options de ses diagnostics enfants."""
        if not cls.has_argument(parser, 'quality_mode'):
            parser.add_argument(
                '--quality-mode', choices=('reliable', 'legacy'),
                default=cls.CONFIG_DEFAULTS['quality_mode'],
                help='Masques et diagnostics fiables (défaut), ou critères historiques',
            )
        super().add_arguments(parser)

    @classmethod
    def parameter_children(cls) -> tuple[type[processor], ...]:
        """Déclare le graphe complet des mesures selon le mode scientifique."""
        return (LegacyNoiseProcessor, StellarRingProcessor, ReferenceEvaluationProcessor)

    def _process(self, args: argparse.Namespace) -> JSONReport:
        """Assemble les diagnostics indépendants en conservant les champs historiques."""
        noise = self.child(LegacyNoiseProcessor, args)
        rings = self.child(StellarRingProcessor, args)
        channels = [
            dict(before, **ring) for before, ring in zip(noise['channels'], rings['channels'])
        ]
        result = dict(
            channels=channels,
            max_noise_ratio=max(c['noise_ratio'] for c in channels),
            max_ring_fraction=max(c['ring_fraction'] for c in channels)
                if all(c['ring_fraction'] is not None for c in channels) else None,
            legacy_noise_ratio=max(c['noise_ratio'] for c in channels),
            high_frequency_residual=channels,
            quality_mode=getattr(args, 'quality_mode', 'reliable'),
        )
        if result['quality_mode'] != 'legacy':
            from dataclasses import replace
            from lib.restoration_quality import reference_for

            reference = reference_for(self.context.source, args)
            context = replace(self.context, before=reference.data, reference=reference)
            diagnostics = self.child(ReferenceEvaluationProcessor, args, context)
            result.update(diagnostics)
            result['max_noise_ratio'] = diagnostics['noise']['max_noise_ratio']
            result['enforce_significance'] = reference.enforce_significance
        return result


class RestorationEvaluationProcessor(QualityProcessor):
    """Orchestre récursivement l'évaluation stellaire puis les diagnostics d'image.

    Les contrôles sur tuiles et ouvertures sont indépendants entre eux. Une
    cohorte stellaire insuffisante n'empêche pas les diagnostics indépendants ;
    les mesures exigeant des étoiles restent UNKNOWN si elles manquent. Cette
    classe ne sélectionne aucun moteur : la restauration conserve cette décision.
    """

    stage_key = 'restoration_evaluation'
    stage_label = 'Qualité — évaluation complète de restauration'

    def __init__(self, context: QualityContext, work_dir: Path) -> None:
        """Attache le contexte partagé et le dossier des catalogues du candidat."""
        super().__init__(context)
        self.work_dir = Path(work_dir)

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare les options en parcourant récursivement les contrôles enfants."""
        super().add_arguments(parser)

    @classmethod
    def parameter_children(cls) -> tuple[type[processor], ...]:
        """Déclare les deux branches récursives stellaire et pixels."""
        from lib.quality_star_processors import StellarAssessmentProcessor

        return (ReferencePreparationProcessor, StellarAssessmentProcessor, ArtifactQualityProcessor)

    def _process(self, args: argparse.Namespace) -> JSONReport:
        """Séquence les préparations nécessaires et assemble leurs audits indépendants."""
        from copy import copy
        from dataclasses import replace
        from lib.logging_utils import log_section
        from lib.quality_star_processors import StellarAssessmentProcessor

        reused_reference = self.context.reference is not None
        if not reused_reference:
            reference = QualityReference(self.context.source, args)
            self._child_processing.append(reference.preparation_processing)
            self.context = replace(self.context, before=reference.data, reference=reference)
        comparison = self.run_child(StellarAssessmentProcessor(self.context, self.work_dir), args)
        result = dict(comparison=comparison, reference_preparation_reused=reused_reference)
        log_section('Restauration — Contrôle final : bruit, flux et artefacts', separator='-')
        final_args = copy(args)
        final_args._quality_reference = self.reference()
        context = replace(self.context, pairs=tuple(comparison.get('pairs', ())))
        result['artifacts'] = self.child(ArtifactQualityProcessor, final_args, context)
        return result
