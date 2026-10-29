"""Vibrance liée à la luminosité après GHS, sur un fond sans étoiles.

Les mesures RGB sont des proxys sans primaires FITS supposées. Aucun réglage
ne dépend du nom de l'objet. Voir docs/scripts/postProcess/final-color.md.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from lib.auto_ghs import ComputeBackend, luminance
from lib.postprocess_paths import reset_ordered_work_dir
from lib.processor import processor
from lib.render_exports import write_render_jpeg
from lib.restoration_quality import normalized_planes
from lib.star_rendering import recompose
from lib.star_separation import validate_rgb, write_rgb
from lib.type_defs import JSONReport

CHROMA_PROTECTION = 0.025
MIN_SUPPORT = 32
MAX_RECOMPOSED_LIGHT_CHANGE = 0.01
LOGGER = logging.getLogger(__name__)


class _ColorActivation(argparse.Action):
    """Mémorise le choix CLI pour distinguer un refus du défaut désactivé."""

    def __call__(
        self, parser: argparse.ArgumentParser, namespace: argparse.Namespace,
        values: object, option_string: str | None = None,
    ) -> None:
        """Applique le booléen et marque la surcharge propre à cet appel."""
        setattr(namespace, self.dest, self.const)
        namespace.final_color_explicit = True


class FinalColorProcessor(processor):
    """Renforce la chroma cohérente sous protections du ciel, du cœur et du gamut."""

    enabled_by_default = False
    parameter_dependencies = {
        'star_max_color_drift', 'star_max_chroma_excess', 'star_max_width_ratio',
    }

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie la passe couleur autonome après AutoGHS."""
        return 'final_color'

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare l'activation facultative et le plafond de gain rémanent."""
        group = parser.add_mutually_exclusive_group()
        group.add_argument('--enable-final-color', dest='enable_final_color',
                           action=_ColorActivation, nargs=0, const=True,
                           default=cls.enabled_by_default,
                           help='Renforcer les couleurs après GHS, avant fusion des étoiles')
        group.add_argument('--disable-final-color', dest='enable_final_color',
                           action=_ColorActivation, nargs=0, const=False,
                           help='Désactiver la passe couleur finale')
        parser.add_argument('--final-color-strength', type=float, default=3.0,
                            help='Renforcement maximal [0,3] (défaut : 3) ; '
                                 '1 donne un gain au plus 2 ; '
                                 '0 désactive')

    @staticmethod
    def options(args: argparse.Namespace) -> argparse.Namespace:
        """Résout les défauts locaux et refuse une force non finie ou hors domaine."""
        parser = argparse.ArgumentParser(add_help=False)
        FinalColorProcessor.add_arguments(parser)
        options = argparse.Namespace(**{key: getattr(args, key, value)
                                       for key, value in vars(parser.parse_args([])).items()})
        if (not math.isfinite(options.final_color_strength)
                or not 0 <= options.final_color_strength <= 3):
            raise ValueError('final-color-strength doit être dans [0,3]')
        return options

    @staticmethod
    def render(
        data: np.ndarray, strength: float, color_mode: str = 'human',
        backend: ComputeBackend | None = None,
    ) -> tuple[np.ndarray, JSONReport]:
        """Applique un gain de chroma local en préservant Y et la direction RGB.

        Parameters
        ----------
        data : numpy.ndarray
            Fond RGB rendu dans [0,1], sans étoiles, sur CPU.
        strength : float
            Excès de gain maximal dans [0,3]. Zéro conserve l'identité.
        color_mode : str
            human ou even, identique à celui du GHS.
        backend : ComputeBackend, optional
            Backend préinitialisé, sans bascule en cours de calcul. CPU sinon.

        Returns
        -------
        tuple[numpy.ndarray, JSONReport]
            Fond CPU float32 et audit. Les seuils de ciel et cœur sont des
            proxys statistiques ; la cohérence est estimée sur une image lissée.

        Notes
        -----
        RGB' = Y + g(RGB-Y), g>=1. Le gain est limité analytiquement au gamut,
        sans écrêtage. Le ciel, les couleurs indistinguables du fond et P99,9
        sont protégés. Les couleurs déjà fortes reçoivent un gain plus faible.
        """
        FinalColorProcessor.options(argparse.Namespace(final_color_strength=strength))
        validate_rgb(data)
        backend = backend or ComputeBackend('cpu')
        xp = backend.xp
        image = xp.asarray(data, dtype=xp.float32)
        light = luminance(image, color_mode, xp=xp)
        active = light > 1e-6
        audit: JSONReport = {'status': 'unchanged', 'requested_strength': strength,
                             'color_mode': color_mode, 'backend': backend.name}
        if strength == 0 or int(xp.count_nonzero(active).item()) < MIN_SUPPORT:
            return data.copy(), audit
        if float(xp.ptp(light[active]).item()) < 1e-7:
            return data.copy(), audit
        if backend.name == 'cuda':
            from cupyx.scipy.ndimage import gaussian_filter
        else:
            from scipy.ndimage import gaussian_filter
        sigma = max(1.0, min(6.0, min(data.shape[1:]) / 768))
        smooth = gaussian_filter(image, sigma=(0, sigma, sigma))
        smooth_y = luminance(smooth, color_mode, xp=xp)
        q25, q75, q95, q98, core = [float(v) for v in
            backend.numpy(xp.percentile(light[active], [25, 75, 95, 98, 99.9]))]
        sky = active & (light <= q25)
        floor = float(xp.median(light[sky]).item())
        pairs = sky[:, 1:] & sky[:, :-1]
        if int(xp.count_nonzero(pairs).item()) < MIN_SUPPORT:
            audit['reason'] = 'insufficient sky pairs'
            return data.copy(), audit
        differences = (light[:, 1:] - light[:, :-1])[pairs]
        noise = float((1.4826 * xp.median(abs(differences - xp.median(differences)))
                       / math.sqrt(2)).item())
        normalized = (smooth - smooth_y[None]) / xp.maximum(smooth_y[None], 1e-6)
        sky_color = xp.median(normalized[:, sky], axis=1)
        sky_residual = normalized[:, sky] - sky_color[:, None]
        color_noise = max(float((1.4826 * xp.median(abs(sky_residual))).item()), 1e-5)
        coherent = xp.sqrt(xp.mean((normalized - sky_color[:, None, None]) ** 2, axis=0))

        def ramp(values: np.ndarray) -> np.ndarray:
            """Raccord C1 de zéro à un sur [0,1], constant à l'extérieur."""
            values = xp.clip(values, 0, 1)
            return values * values * (3 - 2 * values)

        signal_weight = ramp((smooth_y - floor - 3 * noise)
                             / max(6 * noise, .1 * floor, 1e-5))
        signal_weight = xp.where(light <= q25, 0, signal_weight)
        confidence = ramp((coherent / color_noise - 1) / 2)
        highlight_weight = ramp((core - light) / max(core - q98, 1e-6))
        chroma = xp.ptp(smooth, axis=0) / xp.maximum(smooth.sum(axis=0), 1e-6)
        vibrance = 1 / (1 + (chroma / CHROMA_PROTECTION) ** 2)
        gain = 1 + strength * signal_weight * confidence * highlight_weight * vibrance
        delta = image - light[None]
        upper = xp.where(delta > 0, (1 - light)[None] / xp.maximum(delta, 1e-30), xp.inf)
        lower = xp.where(delta < 0, light[None] / xp.maximum(-delta, 1e-30), xp.inf)
        limit = xp.maximum(1, xp.minimum(upper.min(axis=0), lower.min(axis=0)))
        gain = 1 + xp.minimum(gain - 1, (limit - 1) * (1 - 4 * np.finfo('f4').eps))
        # La multiplication par (g-1) garde les pixels protégés exactement identiques.
        output = image + (gain - 1)[None] * delta
        validate_rgb(output)
        after_y = luminance(output, color_mode, xp=xp)
        error = float(xp.max(abs(after_y - light)).item())
        if error > 5e-7:
            raise RuntimeError('La passe couleur ne conserve pas la luminosité du fond')
        diffuse = (light >= q75) & (light <= q95)
        audit.update(
            status='applied' if bool(xp.any(gain > 1 + 1e-6).item()) else 'unchanged',
            sigma_pixels=sigma, sky_median=floor, sky_noise_proxy=noise,
            color_noise_proxy=color_noise, core_threshold=core,
            maximum_gain=float(gain.max().item()),
            diffuse_median_gain=float(xp.median(gain[diffuse]).item()),
            sky_median_gain=float(xp.median(gain[sky]).item()),
            luminance_max_abs=error,
        )
        return backend.numpy(output).astype(np.float32), audit

    def post_process(
        self, input_path: Path, output_path: Path, args: argparse.Namespace | None = None,
        *, stars_path: Path | None = None, headroom: float = .95,
        color_mode: str = 'human',
    ) -> JSONReport:
        """Rend un fond GHS et, si fournies, recompose ses étoiles inchangées.

        Parameters
        ----------
        input_path : Path
            FITS RGB non linéaire, marqué SPROLE=STARLESS, dans [0,1].
        output_path : Path
            Rapport JSON ; le travail voisin contient les images distinctes.
        args : argparse.Namespace, optional
            Force couleur, backend et gardes stellaires résolus. L'activation
            globale est gérée par l'orchestrateur ; cet appel direct calcule.
        stars_path : Path, optional
            FITS stellaire rendu, marqué SPROLE=STARS, même géométrie et WCS.
            Absent, le résultat reste sans étoiles.
        headroom : float
            Marge de fusion screen dans ]0,1[, issue du rapport GHS.
        color_mode : str
            Modèle de luminosité human/even déjà utilisé pour GHS.

        Returns
        -------
        JSONReport
            Chemins FITS/JPEG, métriques, essais et snapshots des couches sources.

        Raises
        ------
        ValueError
            Entrée, paramètre ou géométrie invalide, ou fusion de référence rejetée.
        OSError, RuntimeError
            Lecture, écriture ou backend défaillant, sans bascule de moteur.

        Notes
        -----
        Les gardes sont vérifiées après fusion pour les forces S, S/2, S/4,
        puis zéro. La première admissible est publiée sans modifier les sources.
        """
        runtime = self._get_args(args)
        options = self.options(runtime)
        source, report = Path(input_path).resolve(), Path(output_path).resolve()
        inputs = [source] + ([Path(stars_path).resolve()] if stars_path is not None else [])
        work = report.parent / f'{report.stem}_work'
        if (report.suffix.lower() != '.json' or report in inputs
                or any(path.is_relative_to(work) for path in inputs)):
            raise ValueError('Rapport JSON et couches sources doivent être distincts '
                             'du travail couleur réinitialisé')
        header = fits.getheader(source)
        if not header.get('SPNLIN') or header.get('SPROLE') != 'STARLESS':
            raise ValueError('La passe couleur exige un fond déjà étiré par GHS')
        data = normalized_planes(source)
        stars = normalized_planes(inputs[1]) if stars_path is not None else None
        if stars is not None:
            star_header = fits.getheader(inputs[1])
            if not star_header.get('SPNLIN') or star_header.get('SPROLE') != 'STARS':
                raise ValueError('La passe couleur exige la couche stellaire rendue')
            first_wcs = WCS(header, naxis=2).celestial.to_header(relax=True)
            second_wcs = WCS(star_header, naxis=2).celestial.to_header(relax=True)
            if data.shape != stars.shape or first_wcs != second_wcs:
                raise ValueError('Géométrie ou WCS des couches couleur incompatibles')
        backend = ComputeBackend(getattr(runtime, 'compute_backend', 'auto'), data.nbytes * 12)
        reference_y = (luminance(headroom * data + stars * (1 - headroom * data), color_mode)
                       if stars is not None else None)
        attempts = []
        chosen = None
        for fraction in (1, .5, .25, 0):
            colored, metrics = self.render(data, options.final_color_strength * fraction,
                                           color_mode, backend)
            final, fusion = (recompose(colored, stars, headroom) if stars is not None
                             else (colored, {}))
            rejected = []
            if fusion:
                light_change = float(np.max(abs(luminance(final, color_mode) - reference_y)))
                fusion['luminance_change_max_abs'] = light_change
                if light_change > MAX_RECOMPOSED_LIGHT_CHANGE:
                    rejected.append('recomposed luminance change')
                color = fusion['star_color_after_fusion']
                drift_limit = getattr(runtime, 'star_max_color_drift', .08)
                chroma_limit = getattr(runtime, 'star_max_chroma_excess', .1)
                if (color['chromaticity_mean_abs'] > drift_limit
                        or color['chroma_mean_change'] > chroma_limit):
                    rejected.append('star color after fusion')
                width = fusion['star_profiles_after_fusion']['median_ratio']
                if width is not None and width > getattr(runtime, 'star_max_width_ratio', 2.0):
                    rejected.append('star profile width after fusion')
            attempts.append(dict(metrics=metrics, rejections=rejected))
            if not rejected:
                chosen = colored, final, fusion, metrics
                break
        if chosen is None:
            raise ValueError('La fusion du rendu couleur et de sa référence échoue '
                             'aux gardes stellaires')
        colored, final, fusion, metrics = chosen
        destination = reset_ordered_work_dir(report)
        starless_output = destination / f'{source.stem}_color.fits'
        output = destination / f'{source.stem}_color_final.fits'
        write_rgb(source, starless_output, colored, nonlinear=True, role='STARLESS')
        write_rgb(source, output, final, nonlinear=True,
                  role='WITHSTARS' if stars is not None else 'STARLESS')
        jpeg = output.with_suffix('.jpg')
        write_render_jpeg(output, jpeg)
        from lib.postprocess_resume import PostProcessResume
        result: JSONReport = {
            'status': 'SUCCESS', 'image_path': str(source),
            'output_image': str(output), 'output_jpeg': str(jpeg),
            'output_starless_image': str(starless_output), 'metrics': metrics,
            'attempts': attempts, 'recomposition': fusion,
            'inputs': [PostProcessResume.snapshot(path) for path in inputs],
            'parameters': vars(options),
        }
        report.write_text(json.dumps(result, indent=2), encoding='utf-8')
        LOGGER.info('Couleur finale : %s ; gain médian diffus %.2f ; gain maximal %.2f',
                    metrics['status'], metrics.get('diffuse_median_gain', 1),
                    metrics.get('maximum_gain', 1))
        return result
