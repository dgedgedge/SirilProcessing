"""Traitements indépendants des étoiles et du fond, avec exports et diagnostics.

Les moteurs gardent leurs critères propres. Chaque instance possède ses
paramètres et chaque appel ouvre puis ferme son propre collecteur d'essais.
Voir docs/scripts/postProcess/stretch-branches.md pour les contrats et limites.
"""

from __future__ import annotations

import argparse
import logging
from abc import abstractmethod
from pathlib import Path

import numpy as np
from astropy.io import fits

from lib.auto_ghs import ComputeBackend, GHSOptimizer
from lib.ghs_diagnostics import HistogramDiagnostics, atomic_report, histogram_figure
from lib.processor import processor
from lib.render_exports import write_render_jpeg
from lib.restoration_quality import normalized_planes
from lib.star_diagnostics import write_star_diagnostic
from lib.star_rendering import ConservativeStarRenderer
from lib.star_separation import validate_linear_header, validate_rgb, write_rgb
from lib.type_defs import JSONReport

LOGGER = logging.getLogger(__name__)


class LayerStretchProcessor(processor):
    """Partage le cycle de vie d'une couche, sans partager ses choix de rendu."""

    parameter_dependencies = {'ghs_save_trial_jpegs', 'ghs_color_mode'}

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Expose les paramètres des moteurs propres à la branche concrète."""
        for child in cls.parameter_children():
            child.add_arguments(parser)

    def create_backend(self, data: np.ndarray, args: argparse.Namespace) -> ComputeBackend:
        """Choisit le CPU par défaut ; les étoiles sont calculées avec NumPy."""
        return ComputeBackend('cpu')

    def post_process(
        self, input_path: Path, output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Traite un FITS linéaire de couche et publie son rapport JSON.

        input_path doit déjà contenir la couche isolée, RGB normalisée dans
        [0,1]. output_path nomme le rapport dans son dossier d'exports. Cette
        entrée autonome ne réalise ni séparation StarNet ni recomposition.
        Les erreurs de données, de moteur et d'écriture sont propagées.
        """
        source = Path(input_path).resolve()
        validate_linear_header(fits.getheader(source))
        data = normalized_planes(source)
        args = self._get_args(args)
        _, report = self.process(
            data, source, output_path, self.create_backend(data, args), args,
        )
        return report

    def process(
        self, data: np.ndarray, source: Path, output_path: Path,
        backend: ComputeBackend, args: argparse.Namespace | None = None,
    ) -> tuple[np.ndarray, JSONReport]:
        """Étire une couche en mémoire et publie ses produits indépendants.

        data est un RGB linéaire (3, hauteur, largeur) dans [0,1], préservé.
        source fournit le nom et les métadonnées FITS ; output_path est le
        rapport JSON. backend peut être précontrôlé par l'orchestrateur.
        Les diagnostics et JPEG facultatifs appartiennent à cet appel : leur
        cache est fermé même en cas d'échec. Aucun fond ni aucune étoile de
        l'autre branche n'est accepté par cette interface.

        Retourne les pixels étirés et le rapport publié. ValueError signale
        une entrée invalide ou une collision avec la source ; les erreurs du
        moteur et du système de fichiers sont propagées sans faux succès.
        """
        validate_rgb(data)
        source, report_path = Path(source).resolve(), Path(output_path).resolve()
        if source == report_path:
            raise ValueError('Le rapport de couche ne peut pas remplacer la source')
        args = self._get_args(args)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        recorder = HistogramDiagnostics(
            backend if getattr(args, 'ghs_save_trial_jpegs', False) else None,
            row_order=fits.getheader(source).get('ROWORDER', 'BOTTOM-UP'),
        )
        recorder.set_source(data)
        try:
            output, report = self.render_layer(data, source, report_path, backend, args, recorder)
            atomic_report(report_path, report)
            return output, report
        finally:
            recorder.close()

    @staticmethod
    def write_layer(
        source: Path, report_path: Path, data: np.ndarray, suffix: str, role: str,
    ) -> tuple[Path, Path]:
        """Écrit un FITS non linéaire et son JPEG, en protégeant la source.

        suffix et role sont définis par les branches, pas par des entrées
        utilisateur. L'image est écrite à côté du rapport, sans modifier les
        métadonnées géométriques ni les extensions FITS de la source.
        """
        output = report_path.parent / f'{source.stem}_{suffix}.fits'
        jpeg = output.with_suffix('.jpg')
        if source in (output, jpeg):
            raise ValueError('La sortie de couche ne peut pas remplacer la source')
        write_rgb(source, output, data, nonlinear=True, role=role)
        write_render_jpeg(output, jpeg)
        return output, jpeg

    @abstractmethod
    def render_layer(
        self, data: np.ndarray, source: Path, report_path: Path,
        backend: ComputeBackend, args: argparse.Namespace, recorder: HistogramDiagnostics,
    ) -> tuple[np.ndarray, JSONReport]:
        """Calcule, contrôle et exporte une couche avec son seul collecteur."""
        raise NotImplementedError


class StarStretchProcessor(LayerStretchProcessor):
    """Possède le rendu stellaire conservateur, ses exports et tous ses essais."""

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie la branche stellaire dans les paramètres du pipeline."""
        return 'star_stretch'

    @classmethod
    def parameter_children(cls) -> tuple[type[processor], ...]:
        """Déclare le moteur des étoiles, sans moteur de fond."""
        return (ConservativeStarRenderer,)

    def render_layer(
        self, data: np.ndarray, source: Path, report_path: Path,
        backend: ComputeBackend, args: argparse.Namespace, recorder: HistogramDiagnostics,
    ) -> tuple[np.ndarray, JSONReport]:
        """Étire globalement les étoiles et publie images et diagnostics.

        Les paramètres, unités et exceptions de ConservativeStarRenderer.render
        s'appliquent. star_stretch_max_width_ratio contrôle les étoiles seules ;
        None hérite de la garde commune star_max_width_ratio.
        star_contrast_mode est toujours résolu en courbe globale ; les anciennes
        valeurs profiles et star_noise_sigma ne réactivent aucun traitement local
        ni débruitage. Le seul réglage partagé avec
        le fond est ghs_color_mode, qui choisit la luminance de la base asinh.
        """
        output, report = ConservativeStarRenderer.render(
            data, getattr(args, 'star_strength', 2.0), getattr(args, 'star_ceiling', .85),
            ConservativeStarRenderer.stretch_width_limit(args),
            getattr(args, 'star_faint_boost', ConservativeStarRenderer.default_faint_boost),
            getattr(args, 'star_histogram_strength',
                    ConservativeStarRenderer.default_histogram_strength),
            diagnostics=recorder,
            boost_fraction=getattr(
                args, 'star_boost_fraction', ConservativeStarRenderer.default_boost_fraction,
            ),
            noise_sigma=getattr(
                args, 'star_noise_sigma', ConservativeStarRenderer.default_noise_sigma,
            ),
            color_mode=getattr(args, 'ghs_color_mode', 'human'),
            contrast_mode=getattr(
                args, 'star_contrast_mode', ConservativeStarRenderer.default_contrast_mode,
            ),
            visible_fraction=getattr(
                args, 'star_visible_fraction', ConservativeStarRenderer.default_visible_fraction,
            ),
        )
        image, jpeg = self.write_layer(source, report_path, output, 'stars_render', 'STARS')
        diagnostic = report_path.parent / f'{source.stem}_stars_diagnostic.png'
        write_star_diagnostic(
            data, output, report, diagnostic,
            row_order=fits.getheader(source).get('ROWORDER', 'BOTTOM-UP'),
        )
        report.update(recorder.write_stellar_series(report_path.parent / 'histogram_trials'))
        histogram = report_path.parent / f'{source.stem}_stars_histograms.png'
        histogram_figure(recorder.stellar_histogram_report(), histogram)
        report.update(output_image=str(image), output_jpeg=str(jpeg),
                      diagnostic_path=str(diagnostic), histogram_path=str(histogram))
        LOGGER.info('Étoiles : %d pics au-dessus de 0,03 ; adaptation %.3f',
                    report['visibility']['above_threshold'],
                    report['histogram_balance']['accepted_strength'])
        return output, report


class StarlessStretchProcessor(LayerStretchProcessor):
    """Possède le GHS du fond, ses contrôles, ses exports et sa galerie seule."""

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie la branche du fond dans les paramètres du pipeline."""
        return 'starless_stretch'

    @classmethod
    def parameter_children(cls) -> tuple[type[processor], ...]:
        """Déclare le moteur GHS du fond, sans moteur stellaire."""
        return (GHSOptimizer,)

    def create_backend(self, data: np.ndarray, args: argparse.Namespace) -> ComputeBackend:
        """Sélectionne CPU/CUDA en précontrôlant le budget de l'image entière."""
        options = GHSOptimizer.options(args)
        return ComputeBackend(options.compute_backend, GHSOptimizer.estimate_working_bytes(
            data.shape, options.ghs_quartile_block_pixels,
        ))

    def render_layer(
        self, data: np.ndarray, source: Path, report_path: Path,
        backend: ComputeBackend, args: argparse.Namespace, recorder: HistogramDiagnostics,
    ) -> tuple[np.ndarray, JSONReport]:
        """Optimise le fond seul puis publie sa couche et ses essais GHS.

        Les paramètres et exceptions de GHSOptimizer.optimize s'appliquent.
        Les étoiles ne sont transmises ni aux critères ni aux diagnostics.
        """
        capture = recorder if getattr(args, 'ghs_save_trial_jpegs', False) else None
        output, report = GHSOptimizer().optimize(
            data, 'range', args, backend,
            **({'trial_images': capture} if capture is not None else {}),
        )
        image, jpeg = self.write_layer(
            source, report_path, output, 'starless_ghs_range', 'STARLESS',
        )
        report['transfer_source_domain'] = list(recorder.source_domain)
        series = HistogramDiagnostics.write_series(
            report, report_path.parent / 'histogram_trials', capture,
        )
        report.update(series)
        histogram = report_path.parent / f'{source.stem}_ghs_histograms.png'
        histogram_figure(report, histogram)
        from lib.starless_zones import StarlessZoneMap

        report['zones_map'] = StarlessZoneMap.write(
            data, report_path.parent / 'starless_zones.png',
            color_mode=getattr(args, 'ghs_color_mode', 'human'), rendered=output,
            title=str(fits.getheader(source).get('OBJECT', source.stem)),
        )
        report.update(output_image=str(image), output_jpeg=str(jpeg),
                      histogram_path=str(histogram), starless_image=str(image))
        return output, report
