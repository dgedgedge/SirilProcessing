"""Aperçus JPEG AutoStretch des restaurations, sans modification des FITS.

Une courbe liée RGB est estimée sur l'entrée et partagée entre les candidats.
Voir docs/scripts/postProcess/restoration-previews.md pour les formules et limites.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from lib.processor import processor
from lib.render_exports import write_array_jpeg
from lib.restoration_quality import normalized_planes
from lib.type_defs import JSONReport

TARGET_BACKGROUND = 0.25
SHADOW_SIGMA = -2.8
MAD_TO_SIGMA = 1.4826
MAX_SAMPLE_PIXELS = 262144
MIN_SPAN = 1e-12
BLOCK_ROWS = 256  # Limite la mémoire des temporaires float64 du rendu.


class RestorationPreviewProcessor(processor):
    """Présente les sorties avec une courbe commune, indépendante des scores."""

    parameter_prefixes = ('restoration_',)

    def __init__(self, reference_path: Path) -> None:
        """Conserve la référence ; son estimation est différée jusqu'au premier JPEG."""
        self.reference_path = Path(reference_path).resolve()
        self._stretch: JSONReport | None = None

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie le service auxiliaire d'aperçu de restauration."""
        return 'restoration_preview'

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Active les aperçus par défaut, avec désactivation explicite persistante."""
        if any(action.dest == 'restoration_previews' for action in parser._actions):
            return
        parser.add_argument(
            '--restoration-previews', action=argparse.BooleanOptionalAction, default=True,
            help='JPEG AutoStretch des restaurations (courbe commune aux candidats)',
        )

    @staticmethod
    def validate(data: NDArray[np.float32]) -> None:
        """Refuse les plans vides, non finis ou autres que mono/RGB CHW."""
        if (data.ndim != 3 or data.shape[0] not in (1, 3)
                or data.size == 0 or not np.isfinite(data).all()):
            raise ValueError('Aperçu : plans finis mono ou RGB non vides requis')

    @classmethod
    def fit(cls, data: NDArray[np.float32]) -> JSONReport:
        """Estime offset, échelle, point noir et tons moyens sur la référence.

        Les statistiques utilisent un échantillon spatial déterministe d'au
        plus 262144 pixels par canal. Médianes et sigma MAD sont moyennés entre
        canaux. Le point noir est médiane - 2,8 sigma, borné à zéro ; une MAD
        nulle conserve le zéro. La MTF vise un fond à 0,25. Les valeurs finies
        hors [0,1] sont placées dans ce domaine par une affine commune.
        """
        cls.validate(data)
        offset = min(0., float(data.min()))
        scale = max(1., float(data.max()) - offset)
        stride = max(1, int(np.ceil(np.sqrt(
            data.shape[1] * data.shape[2] / MAX_SAMPLE_PIXELS,
        ))))
        while ((data.shape[1] + stride - 1) // stride
               * ((data.shape[2] + stride - 1) // stride) > MAX_SAMPLE_PIXELS):
            stride += 1
        sample = (data[:, ::stride, ::stride].astype(np.float64) - offset) / scale
        medians = np.median(sample, axis=(1, 2))
        sigmas = MAD_TO_SIGMA * np.median(
            np.abs(sample - medians[:, None, None]), axis=(1, 2),
        )
        median, sigma = float(medians.mean()), float(sigmas.mean())
        shadow = max(0., median + SHADOW_SIGMA * sigma) if sigma > MIN_SPAN else 0.
        shadow = min(shadow, 1. - MIN_SPAN)
        center = np.clip((median - shadow) / (1. - shadow), 0., 1.)
        denominator = (2 * TARGET_BACKGROUND - 1) * center - TARGET_BACKGROUND
        midtone = float(np.clip(
            (TARGET_BACKGROUND - 1) * center / denominator, MIN_SPAN, 1. - MIN_SPAN,
        ))
        return dict(
            method='linked_median_mad_mtf', offset=offset, scale=scale,
            shadows=shadow, highlights=1., midtones=midtone,
            target_background=TARGET_BACKGROUND, shadows_sigma=SHADOW_SIGMA,
            reference_median=median, reference_sigma=sigma,
            sample_stride=stride, linked_channels=True,
            comparison='fixed_reference_curve',
        )

    @classmethod
    def apply(cls, data: NDArray[np.float32], stretch: JSONReport) -> NDArray[np.float32]:
        """Applique l'affine et la MTF en float64, puis borne la présentation seule.

        La transformation est monotone, commune aux canaux et identique pour
        toutes les images de la comparaison. Les dépassements sont écrêtés
        uniquement pour le JPEG ; les tableaux d'entrée ne sont pas modifiés.
        """
        cls.validate(data)
        shadow, midtone = float(stretch['shadows']), float(stretch['midtones'])
        offset, scale = float(stretch['offset']), float(stretch['scale'])
        if (not np.isfinite([shadow, midtone, offset, scale]).all()
                or not 0 <= shadow < 1 or not 0 < midtone < 1 or scale <= 0):
            raise ValueError('Paramètres AutoStretch invalides')
        output = np.empty_like(data, dtype=np.float32)
        # Limiter les temporaires float64 même pour une grande mosaïque.
        for channel, plane in enumerate(data):
            for start in range(0, plane.shape[0], BLOCK_ROWS):
                rows = slice(start, start + BLOCK_ROWS)
                values = (plane[rows].astype(np.float64) - offset)
                values /= scale
                values = np.clip((values - shadow) / (1. - shadow), 0., 1.)
                numerator = (midtone - 1.) * values
                denominator = (2. * midtone - 1.) * values - midtone
                output[channel, rows] = np.clip(numerator / denominator, 0., 1.)
        return output

    def post_process(
        self, input_path: Path | str, output_path: Path | str,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Écrit un JPEG RGB entier, qualité 95, orienté comme l'affichage Siril.

        Les images mono sont dupliquées en RGB. La source ne peut être remplacée.
        Les erreurs FITS, géométriques et d'écriture remontent à l'appelant.
        """
        source, destination = Path(input_path).resolve(), Path(output_path).resolve()
        if (destination in (source, self.reference_path)
                or destination.suffix.lower() not in ('.jpg', '.jpeg')):
            raise ValueError('Une sortie JPEG distincte des FITS est requise')
        if self._stretch is None:
            self._stretch = self.fit(normalized_planes(self.reference_path))
        rendered = self.apply(normalized_planes(source), self._stretch)
        if rendered.shape[0] == 1:
            rendered = np.repeat(rendered, 3, axis=0)
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_array_jpeg(rendered, destination)
        return dict(
            status='written', image_path=str(source), output_jpeg=str(destination),
            reference_image=str(self.reference_path), stretch=dict(self._stretch),
            scientific_output=False,
        )

    def optional(
        self, source: Path | str, destination: Path, args: argparse.Namespace,
    ) -> JSONReport:
        """Audite un aperçu désactivé ou échoué sans invalider le calcul scientifique."""
        try:
            destination = Path(destination).resolve()
            if (destination in (Path(source).resolve(), self.reference_path)
                    or destination.suffix.lower() not in ('.jpg', '.jpeg')):
                raise ValueError('Une sortie JPEG distincte des FITS est requise')
            destination.unlink(missing_ok=True)
            if not getattr(args, 'restoration_previews', True):
                return dict(status='disabled')
            return self.post_process(source, destination, args)
        except (OSError, ValueError, RuntimeError) as error:
            logging.warning('Aperçu JPEG de restauration impossible pour %s : %s', source, error)
            return dict(status='failed', image_path=str(source), error=str(error))

    def attach(self, result: JSONReport, report: Path, args: argparse.Namespace) -> None:
        """Ajoute le JPEG du résultat et, en cas de rejet, celui du candidat réel.

        Les chemins existants sont seuls présentés ; les échecs n'inventent
        aucun résultat. Le statut de traitement reste indépendant des aperçus.
        """
        output = result.get('output_image') or result.get('result_image_path')
        if isinstance(output, str) and Path(output).is_file():
            result['result_preview'] = self.optional(
                output, report.with_name(report.stem + '_autostretch.jpg'), args,
            )
        candidate = result.get('candidate_image_path')
        if (result.get('status') != 'accepted' and isinstance(candidate, str)
                and Path(candidate).is_file() and candidate != output):
            result['candidate_preview'] = self.optional(
                candidate, report.with_name(report.stem + '_candidate_autostretch.jpg'), args,
            )
