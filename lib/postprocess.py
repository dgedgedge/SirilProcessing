"""Processeurs et orchestration du post-traitement des images FITS.

Le module regroupe les moteurs de correction du gradient, la séquence
scientifique et le processeur qui publie les rapports et exports partagés entre
``lightProcess`` et ``postProcess``.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from copy import copy, deepcopy
from pathlib import Path
from shutil import copyfile

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS
from PIL import Image, ImageDraw

from lib.output_layout import OutputLayout
from lib.postprocess_paths import reset_work_dir, treatment_file_prefix
from lib.denoising import NoiseReductionProcessor
from lib.deconvolution import DeconvolutionProcessor
from lib.final_annotation import FinalImageAnnotator
from lib.final_color import FinalColorProcessor
from lib.final_render import AutoGHSFinalRenderer
from lib.compute_config import ComputeConfig
from lib.logging_utils import (
    add_session_file_logging, log_duration, log_section, remove_session_file_logging,
)
from lib.postprocess_resume import PostProcessResume
from lib.processor import processor
from lib.quality_preview import QualityMaskPreview
from lib.siril import Siril
from lib.star_separation import StarNetSeparator
from lib.type_defs import ConfigValue, JSONReport


def _write_report(output_path: Path, results: dict) -> None:
    """Crée le dossier parent et écrit le rapport JSON UTF-8 du traitement."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )


class GradientExtractor(processor):
    """
    Extracteur de gradient polynomial pour les images astronomiques.

    Permet d'analyser une image avec différents ordres polynomiaux et de déterminer
    le meilleur ajustement. Génère également une image des points de mesure.

    Ce processeur fait partie du module de post-traitement et hérite de processor.

    Héritage:
        processor: Classe de base abstraite pour le post-traitement

    Exemple:
        # Utilisation basique
        extractor = GradientExtractor()
        results = extractor.post_process(input_path, Path("gradient.json"))

        # Avec des paramètres personnalisés
        extractor = GradientExtractor(
            min_polynomial_order=1,
            max_polynomial_order=3,
            n_measurement_points=200
        )
        results = extractor.extract_gradient_from_fits(input_path)
    """

    parameter_persistence = {"gradient_output_dir": False}

    def __init__(
        self,
        min_polynomial_order: int = 1,
        max_polynomial_order: int = 3,
        sample_fraction: float = 0.3,
        max_samples: int = 10000,
        output_dir: Path | None = None,
        create_measurement_image: bool = True,
        n_measurement_points: int = 100,
        method: str = "rbf",
        smoothing: float = 0.5,
        samples_per_line: int = 20,
        grid_tolerance: float = 2.0,
        keep_all_samples: bool = False,
    ) -> None:
        """
        Initialise l'extracteur de gradient.

        Args:
            min_polynomial_order: Ordre polynomial minimum (défaut: 1)
            max_polynomial_order: Ordre polynomial maximum (défaut: 3)
            sample_fraction: Fraction de pixels à échantillonner (défaut: 0.3)
            max_samples: Nombre maximum d'échantillons (défaut: 10000)
            output_dir: Répertoire de sortie pour les images générées
            create_measurement_image: Si True, crée l'image des points de mesure (défaut: True)
            n_measurement_points: Nombre de points de mesure (défaut: 100)
            method: Méthode de correction du fond, rbf (défaut) ou polynomial
        """
        self.prefix = "gradient"
        self.method = method
        self.smoothing = smoothing
        self.samples_per_line = samples_per_line
        self.grid_tolerance = grid_tolerance
        self.keep_all_samples = keep_all_samples
        self.min_polynomial_order = min_polynomial_order
        self.max_polynomial_order = max_polynomial_order
        self.sample_fraction = sample_fraction
        self.max_samples = max_samples
        self.output_dir = Path(output_dir) if output_dir else None
        self.create_measurement_image = create_measurement_image
        self.n_measurement_points = n_measurement_points

        if self.output_dir:
            self.output_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_prefix(cls) -> str:
        """Retourne l’identifiant de l’étape utilisé dans les options et rapports."""
        return "gradient"

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare une fois les paramètres partagés avec la mosaïque et les étapes."""
        if any(action.dest == "gradient_method" for action in parser._actions):
            return
        prefix = "gradient"
        parser.add_argument(
            f"--{prefix}-method",
            choices=("rbf", "polynomial"),
            default='rbf',
            help="Méthode de correction du fond (défaut : rbf)",
        )
        parser.add_argument(
            f"--{prefix}-smoothing",
            type=float,
            default=0.5,
            help="Lissage RBF entre 0 et 1 (défaut : 0.50)",
        )
        parser.add_argument(
            f"--{prefix}-samples-per-line",
            type=int,
            default=20,
            help="Échantillons par ligne (défaut : 20)",
        )
        parser.add_argument(
            f"--{prefix}-grid-tolerance",
            type=float,
            default=2.0,
            help="Tolérance de rejet des régions brillantes, en sigma (défaut : 2.00)",
        )
        parser.add_argument(
            f"--{prefix}-keep-all-samples",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="Conserver les échantillons des régions brillantes",
        )
        parser.add_argument(
            f"--{prefix}-min-order",
            type=int,
            choices=range(1, 6),
            default=1,
            help="Ordre polynomial minimum",
        )
        parser.add_argument(
            f"--{prefix}-max-order",
            type=int,
            choices=range(1, 6),
            default=3,
            help="Ordre polynomial maximum",
        )
        parser.add_argument(
            f"--{prefix}-points",
            dest=f"{prefix}_n_points",
            type=int,
            default=100,
            help="Nombre de points affichés",
        )
        parser.add_argument(
            f"--{prefix}-sample-fraction",
            type=float,
            default=0.3,
            help="Fraction de pixels ajustés (polynomial), dans ]0, 1]",
        )
        parser.add_argument(
            f"--{prefix}-max-samples",
            type=int,
            default=10000,
            help="Nombre maximal de pixels ajustés (polynomial)",
        )
        parser.add_argument(
            f"--{prefix}-output",
            dest=f"{prefix}_output_dir",
            type=Path,
            default=None,
            help="Répertoire de l'image des points",
        )
        parser.add_argument(
            f"--{prefix}-measurement-image",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Générer l'image des points",
        )

    def argument_defaults(self) -> dict[str, ConfigValue]:
        """Expose les défauts du constructeur pour l’enregistrement historique."""
        return {
            "gradient_method": self.method,
            "gradient_smoothing": self.smoothing,
            "gradient_samples_per_line": self.samples_per_line,
            "gradient_grid_tolerance": self.grid_tolerance,
            "gradient_keep_all_samples": self.keep_all_samples,
            "gradient_min_order": self.min_polynomial_order,
            "gradient_max_order": self.max_polynomial_order,
            "gradient_n_points": self.n_measurement_points,
            "gradient_sample_fraction": self.sample_fraction,
            "gradient_max_samples": self.max_samples,
            "gradient_output_dir": self.output_dir,
            "gradient_measurement_image": self.create_measurement_image,
        }

    def _validate_options(self) -> None:
        """Vérifie les plages des options du gradient avant toute lecture d’image."""
        if not np.isfinite(self.smoothing) or not 0 <= self.smoothing <= 1:
            raise ValueError("gradient-smoothing doit être compris entre 0 et 1")
        if not isinstance(self.samples_per_line, int) or self.samples_per_line < 2:
            raise ValueError(
                "gradient-samples-per-line doit être un entier supérieur ou égal à 2"
            )
        if not np.isfinite(self.grid_tolerance) or self.grid_tolerance < 0:
            raise ValueError("gradient-grid-tolerance doit être positif ou nul")
        if self.method not in ("rbf", "polynomial"):
            raise ValueError("gradient-method doit être rbf ou polynomial")
        if not 1 <= self.min_polynomial_order <= self.max_polynomial_order <= 5:
            raise ValueError(
                "gradient : les ordres doivent vérifier 1 <= min <= max <= 5"
            )
        if not 0 < self.sample_fraction <= 1:
            raise ValueError("gradient-sample-fraction doit être dans ]0, 1]")
        if self.max_samples <= 0:
            raise ValueError("gradient-max-samples doit être strictement positif")
        if self.n_measurement_points <= 0:
            raise ValueError("gradient-points doit être strictement positif")

    def post_process(
        self,
        input_path: Path,
        output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Applique les options de cette invocation sans modifier les valeurs par défaut."""
        if Path(input_path).resolve() == Path(output_path).resolve():
            raise ValueError("Le rapport ne peut pas remplacer le FITS source")
        args = self._get_args(args)
        configured = copy(self)
        options = {
            "method": "method",
            "smoothing": "smoothing",
            "samples_per_line": "samples_per_line",
            "grid_tolerance": "grid_tolerance",
            "keep_all_samples": "keep_all_samples",
            "min_order": "min_polynomial_order",
            "max_order": "max_polynomial_order",
            "n_points": "n_measurement_points",
            "sample_fraction": "sample_fraction",
            "max_samples": "max_samples",
            "output_dir": "output_dir",
            "measurement_image": "create_measurement_image",
        }
        for suffix, attribute in options.items():
            setattr(
                configured,
                attribute,
                getattr(
                    args, f"{self.get_prefix()}_{suffix}", getattr(self, attribute)
                ),
            )
        configured.artifact_prefix = treatment_file_prefix(output_path)
        configured._validate_options()
        configured.output_dir = Path(configured.output_dir or Path(output_path).parent)
        corrected = (
            configured.output_dir
            / f"{configured.artifact_prefix}{Path(input_path).stem}_gradient_corrected.fits"
        )
        if corrected.resolve() in (
            Path(input_path).resolve(),
            Path(output_path).resolve(),
        ):
            raise ValueError(
                "Le FITS corrigé doit être distinct de l'entrée et du rapport"
            )
        if configured.method == "polynomial":
            results = configured.extract_gradient_from_fits(Path(input_path))
        else:
            results = {"image_path": str(input_path), "method": "rbf"}
        results["correction"] = configured.correct_gradient(
            Path(input_path), corrected, results.get("final_recommendation")
        )
        if configured.method == "rbf" and configured.create_measurement_image:
            points = results["correction"]["channels"][0]["measurement_points"]
            measurement = configured.create_measurement_points_image(
                Path(input_path), points
            )
            if measurement is None:
                raise RuntimeError(
                    "Impossible de créer l'image des points de mesure RBF"
                )
            results["measurement_image_path"] = str(measurement)
        results["method"] = configured.method
        results["output_image"] = str(corrected.resolve())
        results["report_saved_to"] = str(output_path)
        _write_report(Path(output_path), results)
        return results

    def process_mosaic(
        self,
        input_paths: Sequence[Path | str],
        output_dir: Path | str,
        args: argparse.Namespace | None = None,
    ) -> list[JSONReport]:
        """Corrige les fonds des panneaux et prépare leurs recouvrements."""
        from lib.joint_gradient import match_backgrounds

        return match_backgrounds(self, input_paths, output_dir, self._get_args(args))

    def _fit_rbf_background(self, plane: np.ndarray) -> tuple[np.ndarray, JSONReport]:
        """Interpôle des médianes locales réparties sur une grille de fond."""
        from scipy.interpolate import RBFInterpolator

        height, width = plane.shape
        side = self.samples_per_line
        if side < 2 or min(height, width) < 2:
            raise ValueError(
                "RBF nécessite au moins quatre échantillons répartis en deux dimensions"
            )
        points, values = [], []
        finite = plane[np.isfinite(plane)]
        if not finite.size:
            raise ValueError("Aucun pixel fini pour ajuster le fond RBF")
        background = np.median(finite)
        sigma = 1.4826 * np.median(np.abs(finite - background))
        threshold = background + self.grid_tolerance * sigma
        rows_count = min(height, max(2, round(side * height / width)))
        rejected = 0
        for rows in np.array_split(np.arange(height), rows_count):
            for columns in np.array_split(np.arange(width), min(side, width)):
                tile = plane[np.ix_(rows, columns)]
                yy, xx = np.nonzero(np.isfinite(tile))
                if not len(xx):
                    continue
                samples = tile[yy, xx]
                median = np.median(samples)
                if not self.keep_all_samples and median > threshold:
                    rejected += 1
                    continue
                scale = 1.4826 * np.median(np.abs(samples - median))
                keep = np.abs(samples - median) <= max(
                    3 * scale, np.finfo(np.float32).eps * max(1.0, abs(median))
                )
                if not keep.any():
                    continue
                points.append(
                    (
                        (np.median(columns[xx[keep]]) - width / 2) / (width / 2),
                        (np.median(rows[yy[keep]]) - height / 2) / (height / 2),
                    )
                )
                values.append(float(np.median(samples[keep])))
        if len(points) < 4:
            raise ValueError("Pas assez de cellules finies pour ajuster le fond RBF")
        model = RBFInterpolator(
            np.asarray(points),
            np.asarray(values),
            kernel="thin_plate_spline",
            smoothing=self.smoothing,
            degree=1,
        )
        return model, {
            "kernel": "thin_plate_spline",
            "smoothing": self.smoothing,
            "samples_per_line": self.samples_per_line,
            "grid_tolerance": self.grid_tolerance,
            "keep_all_samples": self.keep_all_samples,
            "rejected_samples": rejected,
            "measurement_points": [
                (
                    int(round(x * width / 2 + width / 2)),
                    int(round(y * height / 2 + height / 2)),
                )
                for x, y in points
            ],
            "n_background_samples": len(points),
            "success": True,
        }

    def correct_gradient(
        self, input_path: Path, output_path: Path, order: int | None = None
    ) -> JSONReport:
        """Soustrait la variation du fond par canal en conservant son niveau central.

        Les pixels non finis restent inchangés. Aucun écrêtage ni normalisation
        n'est appliqué ; les extensions et les métadonnées WCS sont conservées.
        """
        channel_reports = []
        with fits.open(input_path, memmap=False) as hdul:
            data = hdul[0].data
            if data.ndim != 2 and not (data.ndim == 3 and data.shape[0] == 3):
                raise ValueError("FITS monochrome ou RGB attendu")
            corrected = data.astype(np.float32, copy=True)
            planes = corrected[None, ...] if corrected.ndim == 2 else corrected
            for plane in planes:
                if self.method == "rbf":
                    try:
                        model, fit = self._fit_rbf_background(plane)
                    except (ValueError, np.linalg.LinAlgError) as error:
                        raise RuntimeError(
                            f"Ajustement RBF impossible : {error}"
                        ) from error
                    height, width = plane.shape
                    reference = float(model(np.array([[0.0, 0.0]]))[0])
                    for start in range(0, plane.size, 8192):
                        indices = np.arange(start, min(start + 8192, plane.size))
                        y, x = np.divmod(indices, width)
                        points = np.column_stack(
                            (
                                (x - width / 2) / (width / 2),
                                (y - height / 2) / (height / 2),
                            )
                        )
                        plane.flat[start : start + len(indices)] -= (
                            model(points) - reference
                        ).astype(np.float32)
                    channel_reports.append(fit)
                    continue
                fit = self._fit_polynomial_surface(plane, order)
                if not fit["success"]:
                    raise RuntimeError(
                        f"Ajustement du fond impossible : {fit.get('error')}"
                    )
                coeffs = np.asarray(fit["coefficients"])
                height, width = plane.shape
                # Évaluation par bandes pour limiter la mémoire sur les grands FITS.
                x = (np.arange(width) - width / 2) / (width / 2)
                for start in range(0, height, 128):
                    stop = min(start + 128, height)
                    y = (np.arange(start, stop) - height / 2) / (height / 2)
                    model = np.zeros((stop - start, width), dtype=np.float64)
                    index = 0
                    for i in range(order + 1):
                        for j in range(order + 1 - i):
                            model += coeffs[index] * x[None, :] ** i * y[:, None] ** j
                            index += 1
                    plane[start:stop] -= (model - coeffs[0]).astype(np.float32)
                channel_reports.append(fit)
            hdul[0].data = corrected
            hdul[0].header.add_history(
                f"{self.method} gradient subtracted; central background retained"
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            hdul.writeto(output_path, overwrite=True, checksum=True)
        return {
            "method": self.method,
            "order": order,
            "operation": "subtraction",
            "dither": False,
            "background_reference": "image_center",
            "channels": channel_reports,
        }

    def extract_gradient_from_fits(
        self, fits_path: Path, output_path: Path | None = None
    ) -> dict:
        """Analyse une image et écrit éventuellement son rapport JSON."""
        self._validate_options()
        fits_path = Path(fits_path)
        if not fits_path.is_file():
            raise FileNotFoundError(f"Fichier introuvable: {fits_path}")
        if self.create_measurement_image:
            results, measurement_image = self.extract_with_measurement_points(
                fits_path, self.n_measurement_points
            )
            if measurement_image is None:
                raise RuntimeError("Impossible de créer l'image des points de mesure")
            results["measurement_image_path"] = str(measurement_image)
        else:
            results = self.extract_gradient_all_orders(fits_path)
        if results.get("error") or results.get("final_recommendation") is None:
            raise RuntimeError(
                results.get("error", "Aucun ajustement polynomial réussi")
            )
        if output_path is not None:
            results["report_saved_to"] = str(output_path)
            _write_report(output_path, results)
        return results

    @staticmethod
    def _load_luminance(image_path: Path) -> np.ndarray:
        """Analyse la moyenne des canaux d'un FITS RGB ou le plan monochrome."""
        with fits.open(image_path, memmap=False) as hdul:
            data = hdul[0].data.astype(np.float64)
        if data.ndim == 3 and data.shape[0] == 3:
            data = data.mean(axis=0)
        if data.ndim != 2:
            raise ValueError(
                "Image FITS monochrome ou RGB (3, hauteur, largeur) attendue"
            )
        return data

    def _create_polynomial_matrix(
        self, x_norm: np.ndarray, y_norm: np.ndarray, order: int
    ) -> np.ndarray:
        """
        Crée la matrice de régression polynomiale pour un ordre donné.

        Args:
            x_norm: Coordonnées X normalisées
            y_norm: Coordonnées Y normalisées
            order: Ordre du polynôme

        Returns:
            Matrice de design pour la régression
        """
        # Pour un polynôme d'ordre n, nous avons des termes comme:
        # 1, x, y, x^2, xy, y^2, x^3, x^2y, xy^2, y^3, ...

        features = []

        for i in range(order + 1):
            for j in range(order + 1 - i):
                features.append((x_norm**i) * (y_norm**j))

        return np.column_stack(features)

    def _fit_polynomial_surface(
        self,
        data: np.ndarray,
        order: int,
        sample_fraction: float = None,
        max_samples: int = None,
    ) -> dict:
        """
        Ajuste une surface polynomiale à l'image.

        Args:
            data: Données de l'image 2D
            order: Ordre du polynôme
            sample_fraction: Fraction de pixels à échantillonner
            max_samples: Nombre maximum d'échantillons

        Returns:
            Dictionnaire avec les coefficients, statistiques et le modèle
        """
        if sample_fraction is None:
            sample_fraction = self.sample_fraction
        if max_samples is None:
            max_samples = self.max_samples

        try:
            height, width = data.shape

            # Échantillonnage
            n_samples = max(1, int(data.size * sample_fraction))
            if n_samples > max_samples:
                n_samples = max_samples

            # Créer les grilles de coordonnées
            y_coords, x_coords = np.mgrid[0:height, 0:width]

            # Échantillonnage aléatoire
            if n_samples < data.size:
                indices = np.random.choice(data.size, n_samples, replace=False)
                x_flat = x_coords.flat[indices]
                y_flat = y_coords.flat[indices]
                z_flat = data.flat[indices]
            else:
                x_flat = x_coords.flatten()
                y_flat = y_coords.flatten()
                z_flat = data.flatten()

            # Normaliser les coordonnées
            x_norm = (x_flat - width / 2) / (width / 2)
            y_norm = (y_flat - height / 2) / (height / 2)

            # Créer la matrice de design
            A = self._create_polynomial_matrix(x_norm, y_norm, order)
            valid = np.isfinite(z_flat)
            A, z_flat = A[valid], z_flat[valid]
            if len(z_flat) < A.shape[1]:
                raise ValueError("Pas assez de pixels finis pour ajuster le fond")

            # Résolution par moindres carrés
            coeffs, residuals, rank, s = np.linalg.lstsq(A, z_flat, rcond=None)
            if rank < A.shape[1]:
                raise ValueError(
                    "Échantillons insuffisants pour un ajustement indépendant"
                )
            # Rejeter étoiles et pixels aberrants par écrêtage robuste des résidus.
            mask = np.ones(len(z_flat), dtype=bool)
            for _ in range(8):
                residual = z_flat - A @ coeffs
                center = np.median(residual[mask])
                scale = 1.4826 * np.median(np.abs(residual[mask] - center))
                tolerance = max(
                    3 * scale,
                    np.finfo(float).eps * max(1.0, np.max(np.abs(z_flat))) * 100,
                )
                selected = np.abs(residual - center) <= tolerance
                if np.array_equal(selected, mask) or selected.sum() < A.shape[1]:
                    break
                candidate, _, rank, _ = np.linalg.lstsq(
                    A[selected], z_flat[selected], rcond=None
                )
                if rank < A.shape[1]:
                    break
                mask, coeffs = selected, candidate

            # Calculer les statistiques
            z_pred = A @ coeffs
            residuals_vec = z_flat - z_pred
            mse = np.mean(residuals_vec**2)
            rmse = np.sqrt(mse)

            # Variance expliquée
            if np.var(z_flat) > 0:
                r_squared = 1 - (mse / np.var(z_flat))
            else:
                r_squared = 1.0

            return {
                "order": order,
                "coefficients": coeffs.tolist(),
                "rmse": float(rmse),
                "mse": float(mse),
                "r_squared": float(r_squared),
                "n_samples": len(z_flat),
                "n_background_samples": int(mask.sum()),
                "n_coefficients": len(coeffs),
                "success": True,
            }

        except Exception as e:
            logging.error(
                f"Erreur lors de l'ajustement polynomial d'ordre {order}: {e}"
            )
            return {
                "order": order,
                "coefficients": [],
                "rmse": float("inf"),
                "mse": float("inf"),
                "r_squared": 0.0,
                "n_samples": 0,
                "n_coefficients": 0,
                "success": False,
                "error": str(e),
            }

    def extract_gradient_all_orders(self, image_path: Path) -> dict:
        """
        Extrait le gradient à tous les ordres polynomiaux et détermine le meilleur.

        Args:
            image_path: Chemin vers le fichier FITS

        Returns:
            Dictionnaire complet avec tous les résultats et la recommandation
        """
        try:
            # Charger l'image
            data = self._load_luminance(image_path)

            logging.info(f"Analyse du gradient pour: {image_path}")

            # Ajustement à différents ordres
            results = {}
            best_order = None
            best_r_squared = -1.0
            best_rmse = float("inf")

            for order in range(
                self.min_polynomial_order, self.max_polynomial_order + 1
            ):
                result = self._fit_polynomial_surface(data, order)
                results[f"order_{order}"] = result

                if result["success"] and result["r_squared"] > best_r_squared:
                    best_r_squared = result["r_squared"]
                    best_rmse = result["rmse"]
                    best_order = order

            # Réévaluation: choisir le meilleur compromis entre simplicité et qualité
            # On privilégie l'ordre le plus bas qui explique bien les données
            # (R² > 0.95 par exemple, ou le meilleur R² si aucun n'atteint ce seuil)

            # Trouver le meilleur ordre selon différents critères
            recommendations = {}

            # Critère 1: Meilleur R²
            best_r2_order = max(
                [r["order"] for r in results.values() if r["success"]],
                key=lambda o: results[f"order_{o}"]["r_squared"],
                default=None,
            )
            recommendations["best_r_squared"] = best_r2_order

            # Critère 2: Meilleur compromis (R² / nombre de coefficients)
            best_compromise_order = max(
                [r["order"] for r in results.values() if r["success"]],
                key=lambda o: (
                    results[f"order_{o}"]["r_squared"]
                    / results[f"order_{o}"]["n_coefficients"]
                ),
                default=None,
            )
            recommendations["best_compromise"] = best_compromise_order

            # Critère 3: ordre minimal avec R² > 0.95
            simple_order = None
            for order in range(
                self.min_polynomial_order, self.max_polynomial_order + 1
            ):
                result = results[f"order_{order}"]
                if result["success"] and result["r_squared"] >= 0.95:
                    simple_order = order
                    break
            recommendations["simplest_good_fit"] = simple_order

            # Décision finale: utiliser le meilleur compromis par défaut
            final_recommendation = recommendations["best_compromise"]

            # Calculer les gradients principaux pour l'ordre recommandé
            gradient_info = {}
            if final_recommendation:
                result = results[f"order_{final_recommendation}"]
                coeffs = result["coefficients"]

                # Pour un polynôme, les gradients sont dans les coefficients
                # Le gradient en X est donné par la dérivée partielle par rapport à x
                # Le gradient en Y est donné par la dérivée partielle par rapport à y

                height, width = data.shape

                # Calculer les dérivées partielles à partir des coefficients
                # C'est complexe pour les ordres > 1, donc on fait une approximation numérique
                x_norm_grid, y_norm_grid = np.mgrid[-1:1:100j, -1:1:100j]
                A_grid = self._create_polynomial_matrix(
                    x_norm_grid.flatten(), y_norm_grid.flatten(), final_recommendation
                )
                z_grid = A_grid @ coeffs
                z_grid = z_grid.reshape(100, 100)

                # Calculer le gradient numérique
                dy, dx = np.gradient(z_grid)
                gradient_magnitude = np.sqrt(dx**2 + dy**2)

                gradient_info = {
                    "mean_gradient": float(np.mean(gradient_magnitude)),
                    "max_gradient": float(np.max(gradient_magnitude)),
                    "std_gradient": float(np.std(gradient_magnitude)),
                    "recommended_order": final_recommendation,
                }

            return {
                "image_path": str(image_path),
                "height": int(data.shape[0]),
                "width": int(data.shape[1]),
                "results": results,
                "recommendations": recommendations,
                "final_recommendation": final_recommendation,
                "gradient_info": gradient_info,
            }

        except Exception as e:
            logging.error(
                f"Erreur lors de l'extraction du gradient pour {image_path}: {e}"
            )
            return {
                "image_path": str(image_path),
                "error": str(e),
                "results": {},
                "recommendations": {},
                "final_recommendation": None,
            }

    def create_measurement_points_image(
        self,
        image_path: Path,
        measurement_points: list[tuple[int, int]],
        output_path: Path | None = None,
    ) -> Path | None:
        """
        Crée une image des points de mesure.

        Args:
            image_path: Chemin vers le fichier FITS source
            measurement_points: Liste de tuples (x, y) des points de mesure
            output_path: Chemin de sortie pour l'image (optionnel)

        Returns:
            Chemin vers l'image générée ou None en cas d'erreur
        """
        try:
            # Charger l'image
            data = self._load_luminance(image_path)

            # Normaliser l'image pour l'affichage
            if data.dtype != np.uint8:
                # Normaliser entre 0 et 255
                finite_data = data[np.isfinite(data)]
                data_min = np.percentile(finite_data, 1)
                data_max = np.percentile(finite_data, 99)
                if data_max > data_min:
                    normalized = np.clip(
                        (data - data_min) / (data_max - data_min), 0, 1
                    )
                    data_normalized = (np.nan_to_num(normalized) * 255).astype(np.uint8)
                else:
                    data_normalized = np.zeros_like(data, dtype=np.uint8)
            else:
                data_normalized = data.astype(np.uint8)

            # Créer l'image PIL
            img = Image.fromarray(data_normalized).convert("RGB")
            draw = ImageDraw.Draw(img)

            # Adapter les cadres à la résolution pour rester visibles en vue réduite.
            frame_radius = max(10, round(max(img.size) * 0.008))
            frame_width = max(2, round(max(img.size) * 0.001))
            # La croix repère le pixel exact ; le carré vert facilite sa localisation.
            for x, y in measurement_points:
                # Dessiner une petite croix
                size = 5
                draw.line([(x - size, y), (x + size, y)], fill="red", width=1)
                draw.line([(x, y - size), (x, y + size)], fill="red", width=1)

                draw.rectangle(
                    [
                        (max(0, x - frame_radius), max(0, y - frame_radius)),
                        (
                            min(img.width - 1, x + frame_radius),
                            min(img.height - 1, y + frame_radius),
                        ),
                    ],
                    outline=(0, 255, 0),
                    width=frame_width,
                )

            # Définir le chemin de sortie
            if output_path is None and self.output_dir:
                output_path = (
                    self.output_dir
                    / f"{getattr(self, 'artifact_prefix', '')}{image_path.stem}_measurement_points.png"
                )
            elif output_path is None:
                output_path = (
                    image_path.parent
                    / f"{getattr(self, 'artifact_prefix', '')}{image_path.stem}_measurement_points.png"
                )

            # Sauvegarder l'image
            output_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(output_path)
            logging.info(f"Image des points de mesure sauvegardée: {output_path}")

            return output_path

        except Exception as e:
            logging.error(
                f"Erreur lors de la création de l'image des points de mesure: {e}"
            )
            return None

    def extract_with_measurement_points(
        self, image_path: Path, n_points: int = 100
    ) -> tuple[dict, Path | None]:
        """
        Extrait le gradient et crée une image des points de mesure.

        Args:
            image_path: Chemin vers le fichier FITS
            n_points: Nombre de points de mesure à générer

        Returns:
            Tuple avec les résultats d'extraction et le chemin vers l'image générée
        """
        # Effectuer l'extraction du gradient
        gradient_results = self.extract_gradient_all_orders(image_path)

        if (
            gradient_results.get("error")
            or gradient_results.get("final_recommendation") is None
        ):
            raise RuntimeError(
                gradient_results.get("error", "Aucun ajustement polynomial réussi")
            )

        # Générer des points de mesure aléatoires
        height = gradient_results.get("height", 100)
        width = gradient_results.get("width", 100)

        measurement_points = []
        for _ in range(n_points):
            x = np.random.randint(0, width)
            y = np.random.randint(0, height)
            measurement_points.append((x, y))

        # Créer l'image des points de mesure
        measurement_image = self.create_measurement_points_image(
            image_path, measurement_points
        )

        # Ajouter les points de mesure aux résultats
        gradient_results["measurement_points"] = [
            {"x": int(p[0]), "y": int(p[1])} for p in measurement_points
        ]
        gradient_results["measurement_points_count"] = len(measurement_points)

        return gradient_results, measurement_image


class PhotometricColorCalibrator(processor):
    """Résolution astrométrique puis SPCC Gaia ou PCC sur RGB linéaire (Siril >= 1.4)."""

    parameter_persistence = dict.fromkeys(
        (
            "photometry_object",
            "photometry_coordinates",
            "photometry_force",
            "photometry_output",
        ),
        False,
    )

    enabled_by_default = True

    @classmethod
    def get_prefix(cls) -> str:
        """Retourne l’identifiant de l’étape utilisé dans les options et rapports."""
        return "photometry"

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare les paramètres de cette étape dans le parseur partagé."""
        position = parser.add_mutually_exclusive_group()
        position.add_argument(
            "--photometry-object", help="Nom à rechercher au CDS, par exemple M20"
        )
        position.add_argument(
            "--photometry-coordinates",
            nargs=2,
            type=float,
            metavar=("RA_DEG", "DEC_DEG"),
            help="Centre du champ en degrés ICRS/J2000",
        )
        parser.add_argument(
            "--photometry-focal",
            type=float,
            help="Focale en mm (sinon métadonnées Siril)",
        )
        parser.add_argument(
            "--photometry-pixelsize",
            type=float,
            help="Taille effective des pixels en µm",
        )
        parser.add_argument(
            "--photometry-force",
            action="store_true",
            default=True,
            help="Compatibilité : l’astrométrie est toujours recalculée",
        )
        parser.add_argument(
            "--photometry-noflip",
            action="store_true",
            help="Conserver l'orientation de l'image",
        )
        parser.add_argument(
            "--photometry-downscale",
            action="store_true",
            help="Sous-échantillonner pour la résolution",
        )
        parser.add_argument(
            "--photometry-method", choices=["spcc", "pcc"], default="spcc",
            help="Étalonnage spectrophotométrique SPCC (défaut) ou photométrique PCC",
        )
        sensors = parser.add_mutually_exclusive_group()
        sensors.add_argument(
            "--photometry-osc-sensor", help="Nom du capteur couleur dans SPCC Siril",
        )
        sensors.add_argument(
            "--photometry-mono-sensor", help="Nom du capteur mono du RGB composé dans SPCC",
        )
        for key, description in (
            ("osc-filter", "Filtre devant le capteur couleur"),
            ("osc-lpf", "Filtre passe-bas du boîtier photo"),
            ("red-filter", "Filtre rouge du RGB composé"),
            ("green-filter", "Filtre vert du RGB composé"),
            ("blue-filter", "Filtre bleu du RGB composé"),
            ("white-reference", "Référence de blanc"),
        ):
            parser.add_argument(
                f"--photometry-{key}",
                help=f"{description} : nom exact SPCC (sinon réglage Siril mémorisé)",
            )
        parser.add_argument(
            "--photometry-solve-catalog",
            choices=[
                "tycho2",
                "nomad",
                "localgaia",
                "gaia",
                "ppmxl",
                "brightstars",
                "apass",
            ],
            default="gaia",
            help="Catalogue astrométrique (défaut : Gaia)",
        )
        parser.add_argument(
            "--photometry-catalog",
            choices=["nomad", "apass", "localgaia", "gaia"],
            default="gaia",
            help="Catalogue couleur (défaut : gaia ; SPCC accepte gaia/localgaia)",
        )
        parser.add_argument(
            "--photometry-limitmag",
            type=float,
            help="Magnitude limite absolue pour SPCC/PCC",
        )
        parser.add_argument(
            "--photometry-output",
            type=Path,
            help="FITS étalonné (sinon dans le dossier du rapport)",
        )

    @staticmethod
    def _quote_path(path: Path | str) -> str:
        """Protège un chemin ou argument Siril et refuse les caractères de contrôle."""
        value = str(path)
        if any(character in value for character in ('"', "\n", "\r", "\x00")):
            raise ValueError(
                "Chemin ou argument incompatible avec les scripts Siril (guillemet ou retour à la ligne)"
            )
        return f'"{value}"'

    def post_process(
        self,
        input_path: Path,
        output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Résout l’astrométrie et étalonne le RGB linéaire via SPCC Gaia par défaut.

        SPCC utilise les profils Siril mémorisés sauf options explicites.
        Pour un capteur couleur, le filtre provient des options, puis du champ
        FITS FILTER, sinon de « No filter ». Le mode PCC reste disponible.
        L’astrométrie est toujours recalculée, même si un WCS existe.
        Gaia local vérifié du cache commun est préféré pour l'astrométrie ;
        ses préférences temporaires ne modifient pas celles de l'utilisateur.
        Les catalogues locaux astrométrique et SPCC sont découverts séparément.
        Une couverture spectrale vérifiée sélectionne SPCC local sans modifier les
        préférences utilisateur ; aucune mise à jour réseau n’est exigée.
        Le retournement est autorisé sauf noflip. Le rapport et le journal
        décrivent les commandes exécutées.

        Conserve scripts et journaux directement dans <rapport>_work, ou
        02_photometrie pour le rapport du plan numéroté. Ce dossier est
        réinitialisé à chaque lancement, sans sous-niveau 001 ; écrit le
        FITS de sortie et le rapport JSON.
        Lève ValueError pour une entrée ou des paramètres incompatibles et
        RuntimeError si Siril échoue. Voir docs/scripts/postProcess/photometry.md."""
        args = self._get_args(args)

        def option(name: str, default: ConfigValue = None) -> ConfigValue:
            """Lit une option photométrique, avec repli sur la valeur locale."""
            return getattr(args, f"photometry_{name}", default)

        method = option("method", "spcc")
        solve_catalog = option("solve_catalog", "gaia") or "gaia"
        color_catalog = option("catalog", "gaia") or "gaia"
        spectral_options = {
            "oscsensor": option("osc_sensor"), "monosensor": option("mono_sensor"),
            "oscfilter": option("osc_filter"), "osclpf": option("osc_lpf"),
            "rfilter": option("red_filter"), "gfilter": option("green_filter"),
            "bfilter": option("blue_filter"), "whiteref": option("white_reference"),
        }
        if method not in ("spcc", "pcc"):
            raise ValueError("Méthode d’étalonnage inconnue : choisir spcc ou pcc")
        if method == "spcc" and color_catalog not in ("gaia", "localgaia"):
            raise ValueError("SPCC nécessite Gaia DR3 : choisir gaia ou localgaia")
        if method == "pcc" and any(spectral_options.values()):
            raise ValueError("Les profils capteur/filtres et la référence de blanc nécessitent SPCC")
        if spectral_options["oscsensor"] and spectral_options["monosensor"]:
            raise ValueError("Choisir un capteur couleur ou mono, pas les deux")
        if (spectral_options["oscsensor"] and any(
            spectral_options[name] for name in ("rfilter", "gfilter", "bfilter")
        )) or (spectral_options["monosensor"] and any(
            spectral_options[name] for name in ("oscfilter", "osclpf")
        )):
            raise ValueError("Les profils filtres doivent correspondre au type de capteur")
        for value in spectral_options.values():
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError("Un nom de profil SPCC non vide est requis")
        input_path = Path(input_path).resolve()
        output_path = Path(output_path).resolve()
        with fits.open(input_path, memmap=False) as hdul:
            shape = hdul[0].shape
            input_row_order = hdul[0].header.get("ROWORDER")
            fits_filter = hdul[0].header.get("FILTER")
            input_has_wcs = WCS(hdul[0].header, naxis=2).has_celestial
            if len(shape) != 3 or shape[0] != 3:
                raise ValueError(
                    "SPCC/PCC nécessite une image FITS RGB linéaire à trois canaux, déjà dématricée"
                )

        filter_source = None
        if method == "spcc" and not any(
            spectral_options[name] for name in ("monosensor", "rfilter", "gfilter", "bfilter")
        ):
            if spectral_options["oscfilter"] is not None:
                filter_source = "options"
            else:
                if fits_filter is not None and not isinstance(fits_filter, str):
                    raise ValueError("Le champ FITS FILTER doit contenir un nom de filtre SPCC")
                filter_name = fits_filter.strip() if fits_filter else ""
                spectral_options["oscfilter"] = filter_name or "No filter"
                filter_source = "FITS FILTER" if filter_name else "default"
            self._quote_path(spectral_options["oscfilter"])

        coordinates = option("coordinates")
        object_name = option("object")
        if coordinates is not None and object_name:
            raise ValueError("Choisir un nom d'objet ou des coordonnées, pas les deux")
        for name in ("focal", "pixelsize", "limitmag"):
            value = option(name)
            if value is not None and (not np.isfinite(value) or value <= 0):
                raise ValueError(f"photometry-{name} doit être un nombre positif fini")
        if coordinates is not None:
            if (
                len(coordinates) != 2
                or not all(np.isfinite(coordinates))
                or not 0 <= coordinates[0] < 360
                or not -90 <= coordinates[1] <= 90
            ):
                raise ValueError(
                    "Coordonnées attendues : 0 <= RA < 360 et -90 <= DEC <= 90 degrés"
                )

        tag = treatment_file_prefix(output_path)
        default_image = (
            f"{output_path.stem}.fits" if tag else f"{input_path.stem}_{method}.fits"
        )
        output_image = Path(
            option("output") or output_path.parent / default_image
        ).resolve()
        if output_image in (input_path, output_path):
            raise ValueError(
                "La sortie couleur doit être différente de l'entrée et du rapport"
            )
        if output_image.suffix.lower() not in (".fit", ".fits", ".fts"):
            raise ValueError(
                "photometry-output doit avoir une extension FITS (.fit, .fits, .fts)"
            )
        self._quote_path(input_path)
        self._quote_path(output_image)
        siril = Siril(
            siril_path=getattr(args, "siril_path", None),
            siril_mode=getattr(args, "siril_mode", None),
        )
        if object_name:
            try:
                center = SkyCoord.from_name(object_name, cache=False)
            except Exception as error:
                raise RuntimeError(
                    f"Recherche CDS impossible pour {object_name!r}: {error}"
                ) from error
            coordinates = [float(center.icrs.ra.deg), float(center.icrs.dec.deg)]

        # Une calibration antérieure ou un WCS présent ne dispensent pas
        # de refaire la résolution avant l’étalonnage des couleurs.
        from lib.resource_manager import ResourceManager
        resources = ResourceManager(args)
        gaia_path = resources.gaia_astrometry() if solve_catalog in ('gaia', 'localgaia') else None
        if gaia_path is not None:
            solve_catalog = 'localgaia'
            logging.info('Astrométrie : catalogue Gaia local vérifié : %s', gaia_path)
        spcc_path = resources.gaia_spcc() if method == 'spcc' else None
        if spcc_path is not None:
            color_catalog = 'localgaia'
            logging.info('SPCC : couverture spectrale locale vérifiée : %s', spcc_path)
        solve = ["platesolve", "-force"]
        if coordinates is not None:
            solve.append(f"{coordinates[0]:.10f},{coordinates[1]:.10f}")
        for name in ("focal", "pixelsize"):
            if option(name) is not None:
                solve.append(f"-{name}={option(name):.10g}")
        for name in ("noflip", "downscale"):
            if option(name, False):
                solve.append(f"-{name}")
        solve.append(f"-catalog={solve_catalog}")
        calibration = [method, f"-catalog={color_catalog}"]
        if option("limitmag") is not None:
            calibration.append(f"-limitmag={option('limitmag'):.10g}")
        if method == "spcc":
            for name, value in spectral_options.items():
                if value is not None:
                    calibration.append(self._quote_path(f"-{name}={value}"))
        logging.info(
            "Photométrie : méthode=%s ; catalogue couleur=%s ; commande=%s",
            method.upper(), color_catalog, " ".join(calibration),
        )
        logging.info(
            "Astrométrie : %s ; retournement %s",
            " ".join(solve), "interdit" if option("noflip", False) else "autorisé",
        )
        logging.info(
            "Astrométrie : WCS d’entrée présent=%s ; nouvelle résolution demandée=%s",
            input_has_wcs, True,
        )
        if method == "spcc":
            logging.info(
                "SPCC : filtre OSC=%s ; source=%s",
                spectral_options["oscfilter"], filter_source,
            )
            logging.info(
                "SPCC : autres profils non précisés repris des préférences Siril ; paramètres transmis=%s",
                {name: value for name, value in spectral_options.items() if value is not None},
            )

        # Réinitialiser les produits de cette étape évite les sorties périmées.
        output_path.parent.mkdir(parents=True, exist_ok=True)
        layout = OutputLayout(output_path.parent.parent)
        numbered = (output_path == layout.stage_report("photometry")
                    and layout.calibration.is_dir())
        work_name = "02_photometrie" if numbered else f"{output_path.stem}_work"
        parent_work = output_path.parent / work_name
        if Path(input_path).resolve().is_relative_to(parent_work.resolve()):
            raise ValueError("L’entrée se trouve dans le dossier de travail réinitialisé")
        work_dir = reset_work_dir(output_path, directory_name=work_name)
        staged_image = work_dir / f"{tag}calibrated.fits"
        script = "\n".join(
            [

                f"cd {self._quote_path(work_dir)}",
                "online",
                "set32bits",
                f"load {self._quote_path(input_path)}",
                " ".join(solve),
                " ".join(calibration),
                f"save {self._quote_path(staged_image)}",
                "close",
                "",
            ]
        )
        log_path = work_dir / f"{tag[:-1] if tag else 'photometry'}.log"
        init_options = {}
        if gaia_path is not None or spcc_path is not None:
            from lib.star_separation import StarNetSeparator
            preferences, _ = StarNetSeparator.preferences(args)
            if not preferences.has_section('core'):
                preferences.add_section('core')
            if gaia_path is not None:
                preferences.set('core', 'catalogue_gaia_astro', str(gaia_path))
            if spcc_path is not None:
                preferences.set('core', 'catalogue_gaia_photo', str(spcc_path))
            isolated = work_dir / 'siril-local-gaia.ini'
            with isolated.open('w', encoding='utf-8') as stream:
                preferences.write(stream, space_around_delimiters=False)
            init_options['initfile'] = isolated
        if not siril.run_siril_script(
            script,
            str(work_dir),
            script_name=f"{tag[:-1] if tag else 'photometry'}.sps",
            **init_options,
        ):
            raise RuntimeError(
                f"Résolution astrométrique / {method.upper()} échouée ; consulter {log_path}"
            )
        if not staged_image.is_file():
            raise RuntimeError(
                f"Siril n'a pas produit d'image étalonnée ; consulter {log_path}"
            )
        # Le troisième axe du FITS RGB porte les couleurs ; les distorsions SIP
        # ne concernent que les deux axes spatiaux.
        with fits.open(staged_image, memmap=False) as hdul:
            output_row_order = hdul[0].header.get("ROWORDER")
            if hdul[0].shape != shape or not WCS(hdul[0].header, naxis=2).has_celestial:
                raise RuntimeError(
                    f"Sortie {method.upper()} invalide : dimensions RGB ou solution WCS manquante ; {log_path}"
                )
        output_image.parent.mkdir(parents=True, exist_ok=True)
        # copyfile permet aussi une destination située sur un autre système de fichiers.
        from shutil import copyfile

        copyfile(staged_image, output_image)
        results = {
            "local_gaia_catalogue": str(gaia_path) if gaia_path is not None else None,
            "local_spcc_catalogue": str(spcc_path) if spcc_path is not None else None,
            "image_path": str(input_path),
            "output_image": str(output_image),
            "object": object_name,
            "center_coordinates_deg": coordinates,
            "platesolve_command": " ".join(solve),
            "method": method,
            "input_has_wcs": input_has_wcs,
            "force_solve": True,
            "flip_allowed": not bool(option("noflip", False)),
            "input_row_order": input_row_order,
            "output_row_order": output_row_order,
            "color_catalog": color_catalog,
            "solve_catalog": solve_catalog,
            "calibration_command": " ".join(calibration),
            f"{method}_command": " ".join(calibration),
            "filter_source": filter_source,
            "spectral_profiles": {
                name: value for name, value in spectral_options.items() if value is not None
            },
            "unspecified_profiles_source": "Siril preferences" if method == "spcc" else None,
            "script_path": str(work_dir / f"{tag[:-1] if tag else 'photometry'}.sps"),
            "log_path": str(log_path),
            "report_saved_to": str(output_path),
        }
        _write_report(output_path, results)
        return results


class _PostProcessorSequence(processor):
    """Orchestre des processeurs indépendamment de leur type concret."""

    def __init__(self, processors: Sequence[processor] | None = None) -> None:
        """Installe les étapes standard linéaires puis le rendu AutoGHS facultatif."""
        if processors is not None:
            self.add_arguments = self._add_custom_arguments
            self.parameter_persistence = {
                key: value for treatment in processors
                for key, value in treatment.parameter_persistence.items()
            }
        self._custom_processors = processors is not None
        self.processors = (
            list(processors)
            if processors is not None
            else [
                GradientExtractor(),
                PhotometricColorCalibrator(),
                DeconvolutionProcessor(),
                NoiseReductionProcessor(),
                AutoGHSFinalRenderer(),
            ]
        )
        prefixes = [processor.get_prefix() for processor in self.processors]
        if len(prefixes) != len(set(prefixes)):
            raise ValueError("Chaque processeur doit avoir un préfixe unique")

    def _selected_processors(self, args: argparse.Namespace) -> list[processor]:
        """Sélectionne AutoGHS seul, ou fusionne la restauration de la séquence standard.

        autoghs_only ne modifie pas les séquences personnalisées de l'API.
        """
        if not self._custom_processors and getattr(args, "autoghs_only", False):
            return [treatment for treatment in self.processors
                    if isinstance(treatment, AutoGHSFinalRenderer)]
        if self._custom_processors or not getattr(
            args, "restoration_compare_chains", True
        ):
            return list(self.processors)
        from lib.restoration_chains import RestorationChainProcessor

        selected, inserted = [], False
        for treatment in self.processors:
            if isinstance(treatment, (DeconvolutionProcessor, NoiseReductionProcessor)):
                if not inserted and (
                    getattr(args, "enable_denoise", True)
                    or getattr(args, "enable_deconvolution", True)
                ):
                    selected.append(RestorationChainProcessor())
                    inserted = True
            else:
                selected.append(treatment)
        return selected

    def set_from_args(self, args: argparse.Namespace) -> None:
        """Configure la sélection et les options de chaque traitement."""
        self._args = deepcopy(args)
        for treatment in self.processors:
            treatment.set_from_args(args)

    @staticmethod
    def _is_deconvolution_no_safe_improvement(error: Exception) -> bool:
        """Vrai quand la déconvolution s'arrête sans candidat validé.

        Le moteur Siril lève un RuntimeError dans ce cas avec un message
        explicite ; ce n'est pas un échec technique bloquant pour la suite.
        """
        return "Aucune déconvolution efficace" in str(error)

    @staticmethod
    def _deconvolution_no_safe_result(
        report_path: Path,
        current_path: Path,
        error: Exception,
    ) -> JSONReport:
        """Construit le rapport de continuité pour une déconvolution non retenue."""
        result: JSONReport = {
            "status": "no_safe_improvement",
            "output_image": str(current_path),
            "error": str(error),
        }
        if report_path.is_file():
            try:
                report_data = json.loads(report_path.read_text(encoding="utf-8"))
                if isinstance(report_data, dict):
                    result.update(report_data)
                result["status"] = "no_safe_improvement"
                result["output_image"] = str(current_path)
                result["error"] = str(error)
            except (OSError, ValueError, TypeError):
                pass
        result["continued_with_original_image"] = True
        return result

    parameter_persistence = {
        **GradientExtractor.parameter_persistence,
        **PhotometricColorCalibrator.parameter_persistence,
        **DeconvolutionProcessor.parameter_persistence,
        **NoiseReductionProcessor.parameter_persistence,
    }
    config_path_parameters = AutoGHSFinalRenderer.config_path_parameters

    @classmethod
    def default_processors(cls) -> tuple[type[processor], ...]:
        """Déclare les étapes Siril dans leur ordre d’exécution."""
        return (GradientExtractor, PhotometricColorCalibrator,
                DeconvolutionProcessor, NoiseReductionProcessor, AutoGHSFinalRenderer)

    @classmethod
    def parameter_children(cls) -> tuple[type[processor], ...]:
        """Déclare les étapes ; chacune recense ses propres moteurs enfants."""
        return cls.default_processors()

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare les étapes possibles sans connaître leurs moteurs internes."""
        ComputeConfig.add_arguments(parser)
        cls._declare_processors(parser, cls.default_processors())

    def _add_custom_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Conserve les options et défauts des instances d’une séquence personnalisée."""
        ComputeConfig.add_arguments(parser)
        self._declare_processors(parser, self.processors)

    @staticmethod
    def _declare_processors(
        parser: argparse.ArgumentParser,
        providers: Sequence[type[processor] | processor],
    ) -> None:
        """Déclare activation et options des étapes avec une seule implémentation."""
        class _ToggleTreatmentAction(argparse.Action):
            """Mappe enable/disable d'une étape vers un booléen unique."""

            def __init__(
                self, option_strings: list[str], dest: str, **kwargs: object
            ) -> None:
                super().__init__(
                    option_strings=option_strings, dest=dest, nargs=0, **kwargs
                )

            def __call__(
                self,
                parser: argparse.ArgumentParser,
                namespace: argparse.Namespace,
                values: object,
                option_string: str | None = None,
            ) -> None:
                enabled = bool(option_string) and (
                    option_string.startswith("--enable-")
                    or option_string.startswith("--enable_")
                )
                setattr(namespace, self.dest, enabled)

        for treatment in providers:
            prefix = treatment.get_prefix()
            group = parser.add_argument_group(f"Traitement {prefix}")
            group.add_argument(
                f"--enable-{prefix}",
                f"--enable_{prefix}",
                f"--disable-{prefix}",
                f"--disable_{prefix}",
                dest=f"enable_{prefix}",
                action=_ToggleTreatmentAction,
                default=treatment.enabled_by_default,
                help=(
                    f"Activer (--enable-{prefix}) ou désactiver "
                    f"(--disable-{prefix}) {prefix}"
                ),
            )
            treatment.add_arguments(group)
            if isinstance(treatment, GradientExtractor):
                group.set_defaults(**treatment.argument_defaults())

    def post_process(
        self,
        input_path: Path,
        output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Écrit le rapport global ; seule output_image change l'entrée suivante.

        Chaque traitement reçoit son rapport dans l'étape numérotée lorsqu'une
        arborescence commune est reconnue, sinon sous <rapport>_steps. Le même
        Namespace d'arguments est transmis sans nouveau parsing.
        Les nouveaux dossiers fixent 03 pour la photométrie, 04 pour la
        restauration et 05 pour AutoGHS. Les appels historiques gardent leurs indices.
        La durée murale de chaque appel est journalisée en INFO et enregistrée
        sous processing_duration_seconds dans son rapport, sans modifier les
        mesures internes éventuelles du moteur.
        Chaque étape active est annoncée en INFO par un titre lisible entre
        deux lignes de signes = ; les étapes désactivées n’ont pas de bandeau.
        Avant AutoGHS dans un traitement complet, un rapport partiel et les
        snapshots source/linéaire sont persistés pour permettre une reprise
        même si le rendu final échoue ou est interrompu.
        """
        if args is None:
            from lib.config import Config

            args = self._args if self._args is not None else Config().arguments()
        current_path = Path(input_path).resolve()
        output_path = Path(output_path)
        if current_path == output_path.resolve():
            raise ValueError("Le rapport JSON ne peut pas remplacer l'image d'entrée")
        configured_steps_dir = getattr(args, "postprocess_steps_dir", None)
        if configured_steps_dir is None:
            steps_dir = output_path.parent / f"{output_path.stem}_steps"
        else:
            steps_dir = Path(configured_steps_dir)
        steps_dir.mkdir(parents=True, exist_ok=True)
        all_results = {}
        stage_titles = {
            "gradient": "Correction du gradient",
            "photometry": "Étalonnage photométrique des couleurs",
            "restoration": "Comparaison des chaînes de restauration",
            "deconvolution": "Déconvolution",
            "denoise": "Débruitage",
            "autoghs": "Rendu final AutoGHS et étoiles",
        }
        for index, processor in enumerate(self._selected_processors(args), start=1):
            prefix = processor.get_prefix()
            if not getattr(args, f"enable_{prefix}", processor.enabled_by_default):
                continue
            if not self._custom_processors:
                index = {
                    treatment.get_prefix(): position
                    for position, treatment in enumerate(self.processors, start=1)
                }.get(prefix, index)
            layout = OutputLayout.for_report(output_path) if not self._custom_processors else None
            report_path = (layout.stage_report(prefix) if layout
                           else steps_dir / f"{index:02d}_{prefix}.json")
            report_path.parent.mkdir(parents=True, exist_ok=True)
            if layout:
                index = int(report_path.relative_to(layout.root).parts[0][:2])
            title = stage_titles.get(prefix, prefix)
            log_section(f"Post-traitement — étape {index:02d} : {title}")
            logging.info("Image d’entrée : %s", current_path)
            if prefix == "autoghs" and not getattr(args, "autoghs_only", False):
                # Le rendu peut échouer : conserver sa provenance avant de le lancer.
                from lib.postprocess_resume import PostProcessResume

                checkpoint = {
                    **all_results,
                    "autoghs": {"status": "PENDING", "image_path": str(current_path)},
                }
                if layout:
                    layout.write_summary(output_path, checkpoint)
                else:
                    _write_report(output_path, checkpoint)
                PostProcessResume.save(Path(input_path), output_path, checkpoint)
            stage_durations: dict[str, float] = {}
            try:
                with log_duration(
                    f"Post-traitement — {title}", stage_durations, "processing_seconds",
                ):
                    result = processor.post_process(current_path, report_path, args=args)
                result["processing_duration_seconds"] = stage_durations["processing_seconds"]
                if result.get("error"):
                    raise RuntimeError(result["error"])
                if result.get("output_image"):
                    next_path = Path(result["output_image"])
                    if not next_path.is_absolute():
                        next_path = report_path.parent / next_path
                    if not next_path.is_file():
                        raise FileNotFoundError(
                            f"Image de sortie introuvable: {next_path}"
                        )
                    current_path = next_path.resolve()
                    result["output_image"] = str(current_path)
                if layout:
                    layout.write_summary(report_path, result)
                else:
                    _write_report(report_path, result)
                all_results[prefix] = result
            except Exception as error:
                if (
                    prefix == "deconvolution"
                    and self._is_deconvolution_no_safe_improvement(error)
                ):
                    logging.warning(
                        "Déconvolution non appliquée (aucune amélioration validée). "
                        "Poursuite avec l'image d'origine: %s",
                        current_path,
                    )
                    result = self._deconvolution_no_safe_result(
                        report_path, current_path, error
                    )
                    result["processing_duration_seconds"] = stage_durations["processing_seconds"]
                    if layout:
                        layout.write_summary(report_path, result)
                    else:
                        _write_report(report_path, result)
                    all_results[prefix] = result
                    continue
                raise RuntimeError(f"Échec du traitement {prefix}: {error}") from error
        if OutputLayout.for_report(output_path) and not self._custom_processors:
            OutputLayout.write_summary(output_path, all_results)
        else:
            _write_report(output_path, all_results)
        return all_results


class PostProcessingProcessor(processor):
    """Exécute les étapes, diagnostics et exports d’une image FITS unique.

    ``post_process(image, rapport)`` conserve le schéma historique du rapport
    sur disque (une clé par étape) et retourne une enveloppe ``processor`` avec
    ``output_image``, ``report_path`` et ``treatments``. Les chemins des produits
    dérivent du rapport, jamais du répertoire de travail de la calibration.
    L’annotation finale ajoute un PNG distinct et un audit par étape.
    Voir docs/scripts/postProcess/pipeline.md.
    """

    enabled_by_default = True
    parameter_dependencies = {"config", "log_level", "final_color_explicit"}
    parameter_persistence = {
        **_PostProcessorSequence.parameter_persistence,
        "autoghs_only": False, "color_only": False,
    }
    config_path_parameters = _PostProcessorSequence.config_path_parameters

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie le traitement complet dans un pipeline de processeurs."""
        return "postprocess"

    @classmethod
    def parameter_children(cls) -> tuple[type[processor], ...]:
        """Déclare la séquence, la couleur et l’annotation finales."""
        return (_PostProcessorSequence, FinalColorProcessor, FinalImageAnnotator)

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare les paramètres scientifiques et de présentation du traitement."""
        resume = parser.add_mutually_exclusive_group()
        resume.add_argument(
            "--autoghs-only", action="store_true", default=False,
            help='Reprendre à StarNet/AutoGHS, puis saturation, exports et annotation',
        )
        resume.add_argument(
            '--color-only', action='store_true', default=False,
            help='Relancer couleur, fusion et exports sur les couches GHS précédentes',
        )
        _PostProcessorSequence.add_arguments(parser)
        FinalColorProcessor.add_arguments(parser)
        FinalImageAnnotator.add_arguments(parser)

    @staticmethod
    def prepare_config_save(args: argparse.Namespace) -> None:
        """Résout StarNet avant une sauvegarde demandée avec AutoGHS actif.

        Le mode autoghs-only active également cette découverte, même si AutoGHS
        est désactivé dans la configuration. Modifie uniquement args.starnet_path ;
        le script appelle ensuite Config.save_requested une seule fois, avant ses
        calculs. Les erreurs de découverte restent explicites. Sans -S ou sans
        AutoGHS, ne fait rien. color-only valide ses options sans découvrir
        StarNet ni lancer de séparation.
        """
        FinalColorProcessor.options(args)
        if getattr(args, 'color_only', False):
            return
        if (
            getattr(args, "save_config", False)
            and (getattr(args, "enable_autoghs", True)
                 or getattr(args, "autoghs_only", False))
        ):
            args.starnet_path = StarNetSeparator.resolve_executable(args)

    @staticmethod
    def report_path_for(
        input_path: Path, output_arg: str | Path | None = None,
    ) -> Path:
        """Résout le rapport global numéroté ou le rapport autonome historique.

        Un relais 02_mosaic/image.fits ou une sortie marquée par 01_calibration
        utilise 06_color/postprocess.json. Les autres appels conservent le nom
        historique : output_arg est un répertoire ou un ancien rapport dont
        seul le parent est utilisé. Cette méthode ne crée aucun fichier.
        """
        source = Path(input_path).resolve()
        layout = OutputLayout.for_source(source)
        if output_arg is None and layout:
            return layout.report
        if output_arg is None:
            directory = source.parent
        else:
            provided = Path(output_arg)
            directory = provided if provided.suffix == "" else provided.parent
        if (directory / '01_calibration').is_dir():
            return OutputLayout(directory.resolve()).report
        return directory.resolve() / f"{source.stem}_postprocess.json"

    @staticmethod
    def _final_image(results: JSONReport, fallback: Path) -> Path:
        """Résout la dernière sortie image, ou garde la source faute de sortie."""
        final_image = fallback
        for branch in results.values():
            if isinstance(branch, dict) and branch.get("output_image"):
                final_image = Path(str(branch["output_image"]))
        return final_image.resolve()

    @staticmethod
    def _log_settings(args: argparse.Namespace) -> None:
        """Consigne les réglages effectifs sans charger les moteurs de calcul."""
        logging.info(
            "Configuration: %s", getattr(args, "config", "paramètres liés"),
        )
        logging.info(
            "Siril: mode=%s, path=%s", getattr(args, "siril_mode", "flatpak"),
            getattr(args, "siril_path", "siril"),
        )
        logging.info(
            "Backend: %s",
            "cosmic-clarity" if getattr(args, "enable_clarity", True) else "siril",
        )
        logging.info(
            "Politique de calcul commune : %s",
            getattr(args, "compute_backend", "auto"),
        )
        logging.info(
            "Restauration : comparaison %s ; ordre inverse=%s",
            "des chaînes complètes"
            if getattr(args, "restoration_compare_chains", True)
            else "par étapes isolées",
            getattr(args, "restoration_reverse_order", False),
        )

    def _export_results(
        self, results: JSONReport, source: Path, report: Path,
        quality_preview: Path | None,
    ) -> JSONReport:
        """Copie les exports finaux et retourne leurs chemins avec les rapports.

        Dans l'arborescence numérotée, l'export est <objet>.fits et le GHS
        conservé est sous 05_autoghs/render. Les appels autonomes historiques
        gardent _saturated pour la couleur et _ghs pour le rendu préalable.
        Toute erreur est propagée ; la source n’est jamais modifiée et le rapport
        historique garde ses clés par étape.
        """
        has_color = isinstance(results.get("final_color"), dict)
        layout = OutputLayout.for_report(report)
        if layout:
            final_path = layout.root / f'{layout.root.name}.fits'
        elif has_color:
            final_path = report.with_name(report.stem + '_saturated.fits')
        else:
            final_path = report.with_suffix('.fits')
        final_image = self._final_image(results, source)
        if not final_image.is_file():
            raise FileNotFoundError(f"Image finale introuvable: {final_image}")
        if final_image != final_path:
            copyfile(final_image, final_path)
        logging.info("Image finale exportée: %s", final_path)
        summary: JSONReport = {
            "status": "SUCCESS", "image_path": str(source),
            "output_image": str(final_path), "report_path": str(report),
            "steps_dir": str(layout.root if layout else report.parent / report.stem), "treatments": results,
            "quality_preview_path": str(quality_preview) if quality_preview else None,
        }
        if has_color:
            ghs = results.get("autoghs", {})
            if isinstance(ghs, dict):
                for key, suffix, summary_key in (
                    ("output_image", ".fits", "output_ghs_image"),
                    ("output_jpeg", ".jpg", "output_ghs_jpeg"),
                ):
                    if ghs.get(key):
                        original = Path(str(ghs[key])).resolve()
                        preserved = (layout.root / '05_autoghs' / f'render{suffix}' if layout
                                     else report.with_name(report.stem + "_ghs" + suffix))
                        if original != preserved:
                            copyfile(original, preserved)
                        summary[summary_key] = str(preserved)
                        logging.info("Rendu GHS conservé : %s", preserved)
        autoghs = results.get("final_color", results.get("autoghs"))
        jpeg = autoghs.get("output_jpeg") if isinstance(autoghs, dict) else None
        if jpeg:
            jpeg_path = Path(str(jpeg)).resolve()
            jpeg_export = final_path.with_suffix(".jpg")
            if jpeg_path != jpeg_export:
                copyfile(jpeg_path, jpeg_export)
            summary["output_jpeg"] = str(jpeg_export)
            logging.info("Image finale JPEG exportée: %s", jpeg_export)
        return summary

    def post_process(
        self, input_path: Path, output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Traite une image et publie ses produits avec le contrat processor.

        Parameters
        ----------
        input_path : Path
            FITS linéaire issu du stack, de la mosaïque ou fourni directement.
        output_path : Path
            Rapport JSON global ; son stem nomme le dossier d’étapes et les
            exports FITS/JPEG/PNG. Utiliser report_path_for pour les noms standards.
        args : argparse.Namespace, optional
            Surcharge ponctuelle, copiée sans modifier les paramètres mémorisés.

        Returns
        -------
        JSONReport
            Statut SUCCESS, chemins des sorties et rapports des traitements.

        Raises
        ------
        ValueError, FileNotFoundError, RuntimeError, OSError
            Entrée, traitement ou publication invalide. Les erreurs sont
            journalisées puis propagées ; aucune réussite n’est retournée.
        KeyboardInterrupt
            Propagée après libération des ressources et du journal.

        Notes
        -----
        autoghs_only retrouve le FITS linéaire du traitement précédent avec la
        même entrée/sortie, saute les traitements amont et leurs aperçus, force
        AutoGHS et la saturation pour cet appel (sauf refus CLI explicite de la
        couleur) et conserve les exports, l'annotation et les audits amont.
        Un historique absent ou périmé empêche la reprise.
        color_only saute aussi StarNet/GHS, réutilise leurs couches immuables et
        relance la couleur, la fusion et les exports. Elle ne réécrit pas la
        provenance linéaire et n’exige pas les intermédiaires linéaires préparés.
        Ne parse pas sys.argv, ne sauvegarde pas Config et ne configure pas la
        console. Le journal propre à cette image est fermé même en cas d’échec.
        La protection énergétique est gérée par le script appelant ; ce
        processeur ne demande pas de profil système ni d’inhibition de veille.
        Le début du post-traitement est annoncé par un bandeau INFO commun
        à la console et au journal, avant les réglages et les étapes.
        """
        runtime = deepcopy(self._get_args(args))
        color_only = getattr(runtime, 'color_only', False)
        if color_only and getattr(runtime, 'autoghs_only', False):
            raise ValueError('--color-only et --autoghs-only sont incompatibles')
        if color_only:
            runtime.enable_final_color = True
        elif (getattr(runtime, 'autoghs_only', False)
              and not getattr(runtime, 'final_color_explicit', False)):
            runtime.enable_final_color = True
        color_options = FinalColorProcessor.options(runtime)
        if (color_options.enable_final_color and not color_only
                and not getattr(runtime, 'autoghs_only', False)
                and not getattr(runtime, 'enable_autoghs', True)):
            if getattr(runtime, 'final_color_explicit', False):
                raise ValueError(
                    'La couleur finale exige AutoGHS ou une reprise --color-only'
                )
            runtime.enable_final_color = False
            color_options.enable_final_color = False
            logging.info('Couleur finale non exécutée : AutoGHS désactivé')
        source, report = Path(input_path).resolve(), Path(output_path).resolve()
        if report.suffix.lower() != ".json":
            raise ValueError(
                "Le chemin de sortie du post-traitement doit être un rapport JSON"
            )
        layout = OutputLayout.for_report(report)
        if layout:
            layout.create()
        steps_dir = layout.root if layout else report.parent / report.stem
        saturated_path = report.with_name(report.stem + "_saturated.fits")
        ghs_path = report.with_name(report.stem + "_ghs.fits")
        final_path = (saturated_path if color_options.enable_final_color
                      else report.with_suffix(".fits"))
        if layout:
            final_path = layout.root / f'{layout.root.name}.fits'
        log_file = layout.root / '06_color' / 'postprocess.log' if layout else steps_dir / f"00_{source.stem}_postProcess.log"
        if source in (
            report, report.with_suffix(".fits"), saturated_path, ghs_path, log_file, final_path,
        ):
            raise ValueError(
                "Les produits du post-traitement ne peuvent pas remplacer la source"
            )
        prepared, previous = source, {}
        if color_only:
            previous = PostProcessResume.load_color(source, report)
        elif getattr(runtime, "autoghs_only", False):
            prepared, previous = PostProcessResume.load(source, report)
        runtime.postprocess_steps_dir = steps_dir
        if getattr(runtime, "autoghs_only", False):
            runtime.enable_autoghs = True
        level = getattr(runtime, "log_level", "INFO")
        handler = add_session_file_logging(log_file, level)
        try:
            if not source.is_file():
                raise FileNotFoundError(f"Fichier introuvable: {source}")
            log_section(f"Début du post-traitement : {source.name}")
            logging.info("Log de post-traitement: %s", log_file)
            self._log_settings(runtime)
            logging.info("Analyse de: %s", source)
            logging.info("Rapport global: %s", report)
            logging.info("Dossier des étapes: %s", steps_dir)
            logging.info("Résultat final: %s", final_path)
            if color_only:
                results = {key: value for key, value in previous.items()
                           if key not in ('final_color', 'annotation')}
            else:
                if color_options.enable_final_color:
                    runtime.ghs_chroma = 'off'
                    logging.info(
                        'La couleur finale remplace le renforcement chroma avant GHS'
                    )
                sequence = _PostProcessorSequence()
                logging.info("Entrée linéaire du rendu : %s", prepared)
                results = sequence.post_process(prepared, report, args=runtime)
            if previous and not color_only:
                results = {
                    **{key: value for key, value in previous.items()
                       if key not in ("autoghs", "final_color", "annotation")},
                    **results,
                }
            if color_options.enable_final_color:
                autoghs = results.get('autoghs', {})
                branch = autoghs.get('strategies', {}).get('range', {})
                stars = autoghs.get('stars', {})
                if not branch.get('starless_image') or not stars.get('output_image'):
                    raise ValueError(
                        'La couleur finale exige les couches sans étoiles et étoiles AutoGHS'
                    )
                log_section(
                    'Post-traitement — étape 06 : Couleur finale adaptative'
                )
                if color_only:
                    logging.info(
                        'Réutilisation du fond GHS existant ; chroma antérieure : %s',
                        autoghs.get('parameters', {}).get(
                            'ghs_chroma', 'non renseignée',
                        ),
                    )
                results['final_color'] = FinalColorProcessor().post_process(
                    Path(branch['starless_image']),
                    layout.stage_report('final_color') if layout else steps_dir / '06_final_color.json',
                    runtime, stars_path=Path(stars['output_image']),
                    headroom=autoghs.get('parameters', {}).get(
                        'ghs_headroom', .95,
                    ),
                    color_mode=autoghs.get('parameters', {}).get(
                        'ghs_color_mode', 'human',
                    ),
                )
                if layout:
                    layout.write_summary(layout.stage_report('final_color'), results['final_color'])
                results['final_color']['upstream_chroma'] = autoghs.get(
                    'parameters', {},
                ).get('ghs_chroma')
            quality_preview, gradient_preview = None, None
            if layout:
                # Référencer les masques déjà produits sans diagnostic supplémentaire.
                for name in ("restoration", "deconvolution", "denoise"):
                    stage = results.get(name)
                    preview = stage.get("quality_preview_path") if isinstance(stage, dict) else None
                    if isinstance(preview, str) and Path(preview).is_file():
                        quality_preview = Path(preview)
                        break
            elif not (getattr(runtime, "autoghs_only", False) or color_only):
                quality_preview = QualityMaskPreview.ensure_for_run(
                    results, source, steps_dir, runtime,
                )
                gradient_preview = QualityMaskPreview.write_gradient_points(results, steps_dir)
            summary = self._export_results(results, source, report, quality_preview)
            log_section("Annotation finale : informations de prise de vue")
            annotation = FinalImageAnnotator().post_process(
                Path(summary["output_image"]),
                layout.root / '06_color' / 'annotation.json' if layout else report.with_name(report.stem + "_annotation.json"),
                args=runtime,
                acquisition_source=source,
            )
            results["annotation"] = annotation
            if layout:
                layout.write_summary(layout.root / '06_color/annotation.json', annotation)
            if annotation.get("output_annotated_image"):
                summary["output_annotated_image"] = annotation["output_annotated_image"]
            logging.debug("Rapport détaillé d’annotation finale : %s", annotation)
            if annotation.get("output_annotated_image"):
                logging.info(
                    "Image annotée exportée : %s",
                    annotation["output_annotated_image"],
                )
            else:
                logging.info("Annotation finale : %s", annotation.get("status"))
            if layout:
                layout.write_summary(report, results)
            else:
                report.write_text(
                    json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8",
                )
            logging.info("Image des zones de qualité : %s", quality_preview)
            if gradient_preview:
                logging.info("Points du gradient original : %s", gradient_preview)
            for name, branch in results.items():
                logging.info("Traitement terminé: %s", name)
                if isinstance(branch, dict) and branch.get("summary_path"):
                    logging.info(
                        "Synthèse des essais %s : %s", name, branch["summary_path"],
                    )
            logging.info("Rapport sauvegardé: %s", report)
            if not color_only:
                PostProcessResume.save(source, report, results)
            logging.info("Post-traitements terminés")
            return summary
        except KeyboardInterrupt:
            logging.warning("Traitement interrompu par l’utilisateur")
            raise
        except Exception as error:
            logging.error("Erreur: %s", error, exc_info=level == "DEBUG")
            raise
        finally:
            from lib.processing_html import ProcessingHtmlReport
            ProcessingHtmlReport.publish(layout.root if layout else report.parent)
            remove_session_file_logging(handler)
