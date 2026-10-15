"""Aperçu des exclusions de qualité et des tuiles de mesure, sans modifier le FITS."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import argparse
import json
import logging
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from astropy.io import fits
from astropy.wcs import WCS
from scipy.ndimage import binary_erosion

from lib.type_defs import JSONReport


class QualityMaskPreview:
    """Superpose les masques exacts et les cadres des tuiles sur une luminance étirée."""

    COLORS = {
        "star": (255, 40, 40),
        "object": (255, 140, 0),
        "uncertain": (60, 140, 255),
    }
    GRID_COLOR = (140, 140, 140)

    @classmethod
    def gradient_positions(
        cls, gradient: JSONReport, reference_path: Path
    ) -> np.ndarray | None:
        """Transporte les échantillons RBF par retournement/rotation des axes FITS.

        La nouvelle résolution astrométrique peut modifier CRPIX/CRVAL sans
        déplacer les pixels : seule l’orientation des matrices WCS est utilisée.
        Une permutation signée des axes est exigée ; une géométrie ambiguë est
        signalée sans dessiner de points ni de croix trompeurs.
        """
        correction = gradient.get("correction")
        channels = correction.get("channels") if isinstance(correction, dict) else None
        if (
            gradient.get("method") != "rbf"
            or not isinstance(channels, list)
            or not channels
        ):
            return None
        points = channels[0].get("measurement_points")
        if not isinstance(points, list) or not points:
            return None
        positions = np.asarray(points, dtype=float)
        source = Path(str(gradient["image_path"]))
        original, target = fits.getheader(source), fits.getheader(reference_path)
        source_shape = (original["NAXIS1"], original["NAXIS2"])
        target_shape = (target["NAXIS1"], target["NAXIS2"])
        if source.resolve() == reference_path.resolve():
            return positions
        corrected = gradient.get("output_image")
        if (
            corrected
            and Path(str(corrected)).resolve() == reference_path.resolve()
            and source_shape == target_shape
        ):
            return positions
        origin_wcs, target_wcs = WCS(original, naxis=2), WCS(target, naxis=2)
        if origin_wcs.has_celestial and target_wcs.has_celestial:
            matrix = np.linalg.solve(
                target_wcs.pixel_scale_matrix, origin_wcs.pixel_scale_matrix
            )
            orientation = np.rint(matrix)
            if (
                np.allclose(matrix, orientation, atol=0.01)
                and np.array_equal(orientation @ orientation.T, np.eye(2))
                and np.array_equal(np.abs(orientation) @ source_shape, target_shape)
            ):
                positions = (
                    positions - (np.asarray(source_shape) - 1) / 2
                ) @ orientation.T
                return positions + (np.asarray(target_shape) - 1) / 2
        logging.warning(
            "Points du gradient non superposés : orientation FITS non déterminable sans ambiguïté"
        )
        return None

    @classmethod
    def overlay_gradient(
        cls, results: JSONReport, reference_path: Path, preview: Path, tile_size: int
    ) -> None:
        """Régénère l’aperçu enregistré avec les échantillons du gradient alignés."""
        gradient = results.get("gradient")
        if not isinstance(gradient, dict):
            return
        positions = cls.gradient_positions(gradient, reference_path)
        if positions is None:
            return
        from lib.restoration_quality import normalized_planes

        bits = fits.getdata(preview.with_suffix(".fits"))
        masks = {
            name: (bits & bit) != 0
            for name, bit in (("sky", 1), ("star", 2), ("object", 4), ("uncertain", 8))
        }
        tiles = []
        height, width = bits.shape
        for y in range(0, height, tile_size):
            for x in range(0, width, tile_size):
                ys, xs = slice(y, min(y + tile_size, height)), slice(
                    x, min(x + tile_size, width)
                )
                mask = masks["sky"][ys, xs]
                if mask.sum() >= max(128, 0.5 * mask.size):
                    tiles.append((ys, xs))
        cls.write(
            normalized_planes(reference_path),
            masks,
            tiles,
            preview,
            top_down=fits.getheader(reference_path).get("ROWORDER", "BOTTOM-UP")
            == "TOP-DOWN",
            tile_size=tile_size,
            gradient_points=positions,
        )

    @classmethod
    def write_gradient_points(cls, results: JSONReport, steps_dir: Path) -> Path | None:
        """Produit un second PNG avec les points réellement choisis sur le FITS original.

        Utilise les échantillons RBF enregistrés du premier canal, comme l’aperçu
        du gradient, ou les points d’illustration de l’analyse polynomiale. Aucune
        nouvelle sélection n’est effectuée. Sans gradient exécuté ou points
        enregistrés, ne fabrique pas de points et retourne None.
        """
        gradient = results.get("gradient")
        if not isinstance(gradient, dict):
            return None
        points = gradient.get("measurement_points")
        correction = gradient.get("correction")
        if isinstance(correction, dict):
            channels = correction.get("channels")
            if (
                isinstance(channels, list)
                and channels
                and isinstance(channels[0], dict)
            ):
                points = channels[0].get("measurement_points", points)
        if not isinstance(points, list) or not points or not gradient.get("image_path"):
            return None
        from lib.postprocess import GradientExtractor

        path = GradientExtractor().create_measurement_points_image(
            Path(str(gradient["image_path"])),
            [
                (
                    (int(point["x"]), int(point["y"]))
                    if isinstance(point, dict)
                    else (int(point[0]), int(point[1]))
                )
                for point in points
            ],
            steps_dir / "quality_gradient_points.png",
        )
        if path is None:
            raise RuntimeError(
                "Impossible de produire l’aperçu des points du gradient original"
            )
        gradient["quality_gradient_points_path"] = str(path)
        return path

    @classmethod
    def ensure_for_run(
        cls,
        results: JSONReport,
        source: Path,
        steps_dir: Path,
        args: argparse.Namespace,
    ) -> Path:
        """Garantit un aperçu pour toute exécution achevée du script postProcess.

        Réutilise l’aperçu d’une restauration exécutée. Sinon, construit un
        diagnostic sur l’entrée après gradient/photométrie, même en mode legacy
        ou lorsque la restauration est désactivée. Ce diagnostic ne change pas
        les métriques des moteurs et publie son propre rapport quality_masks.json.
        Les erreurs d’entrée FITS ou d’écriture restent explicites.
        """
        for prefix in ("restoration", "deconvolution", "denoise"):
            result = results.get(prefix)
            if isinstance(result, dict) and result.get("quality_preview_path"):
                path = Path(str(result["quality_preview_path"]))
                if path.is_file():
                    cls.overlay_gradient(
                        results,
                        Path(str(result.get("image_path", source))),
                        path,
                        getattr(args, "quality_tile_size", 64),
                    )
                    return path
        from lib.restoration_quality import QualityReference

        reference_path = source
        for prefix in ("gradient", "photometry"):
            result = results.get(prefix)
            if isinstance(result, dict) and result.get("output_image"):
                reference_path = Path(str(result["output_image"]))
        steps_dir.mkdir(parents=True, exist_ok=True)
        reference = QualityReference(reference_path, args)
        preview = reference.save_masks(steps_dir / "quality_masks.fits")
        cls.overlay_gradient(results, reference_path, preview, reference.tile_size)
        report = dict(
            diagnostic_only=True,
            quality_reference=reference.audit(),
            quality_preview_path=str(preview),
        )
        (steps_dir / "quality_masks.json").write_text(
            json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
        )
        return preview

    @classmethod
    def write(
        cls,
        data: np.ndarray,
        masks: Mapping[str, np.ndarray],
        tiles: Sequence[tuple[slice, slice]],
        destination: Path,
        *,
        top_down: bool,
        tile_size: int = 64,
        gradient_points: np.ndarray | None = None,
    ) -> Path:
        """Écrit un PNG avec légende, contours et cadres en coordonnées natives.

        Parameters
        ----------
        data : ndarray
            Plans mono/RGB normalisés, de forme (canaux, hauteur, largeur).
        masks : Mapping[str, ndarray]
            Masques booléens star, object, uncertain et sky, dans la même géométrie.
        tiles : Sequence[tuple[slice, slice]]
            Tuiles retenues pour mesurer le bruit ; leurs pixels exclus restent masqués.
        destination : Path
            PNG distinct du FITS source. Le dossier parent est créé si nécessaire.
        top_down : bool
            Vrai si la première ligne native est en haut ; sinon l’aperçu est retourné.
        tile_size : int
            Pas de la grille en pixels natifs, identique aux tuiles de mesure.
        gradient_points : ndarray or None
            Points RBF déjà transportés dans les coordonnées natives de l’image.
            Cyan : points ; grandes croix blanches : tuiles sans point. None
            désactive cette annotation plutôt que simuler une sélection vide.

        Returns
        -------
        Path
            PNG limité à 1600 pixels de largeur, plus une légende. La grille
            grise montre les tuiles possibles ; seuls les cadres verts indiquent
            une sélection. Les exclusions suivent leurs contours exacts.
        """
        if tile_size < 1:
            raise ValueError("Le pas de la grille doit être positif")
        luminance = np.nanmean(data, axis=0)
        finite = luminance[np.isfinite(luminance)]
        if not finite.size:
            raise ValueError("Aucun pixel fini pour l’aperçu de qualité")
        low, high = np.percentile(finite, [1, 99.5])
        stretched = np.clip((luminance - low) / max(float(high - low), 1e-12), 0, 1)
        stretched = np.arcsinh(10 * np.nan_to_num(stretched)) / np.arcsinh(10)
        pixels = np.repeat((255 * stretched).astype(np.uint8)[..., None], 3, axis=2)
        pixels[::tile_size, :] = cls.GRID_COLOR
        pixels[:, ::tile_size] = cls.GRID_COLOR
        for name, color in cls.COLORS.items():
            mask = masks[name]
            pixels[mask] = (0.75 * pixels[mask] + 0.25 * np.array(color)).astype(
                np.uint8
            )
            pixels[mask & ~binary_erosion(mask)] = color
        image = Image.fromarray(pixels)
        draw = ImageDraw.Draw(image)
        if gradient_points is not None:
            height, width = pixels.shape[:2]
            valid = gradient_points[
                (gradient_points[:, 0] >= 0)
                & (gradient_points[:, 0] < width)
                & (gradient_points[:, 1] >= 0)
                & (gradient_points[:, 1] < height)
            ]
            occupied = {(int(y) // tile_size, int(x) // tile_size) for x, y in valid}
            for y in range(0, height, tile_size):
                for x in range(0, width, tile_size):
                    if (y // tile_size, x // tile_size) not in occupied:
                        right, bottom = (
                            min(x + tile_size, width) - 3,
                            min(y + tile_size, height) - 3,
                        )
                        if right > x + 3 and bottom > y + 3:
                            draw.line(
                                (x + 3, y + 3, right, bottom), fill="white", width=1
                            )
                            draw.line(
                                (x + 3, bottom, right, y + 3), fill="white", width=1
                            )
            for x, y in valid:
                draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(0, 255, 255))
        for ys, xs in tiles:
            draw.rectangle(
                (xs.start, ys.start, xs.stop - 1, ys.stop - 1),
                outline=(0, 255, 80),
                width=2,
            )
        if not top_down:
            image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        if image.width > 1600:
            image = image.resize(
                (1600, round(image.height * 1600 / image.width)),
                Image.Resampling.LANCZOS,
            )
        canvas = Image.new(
            "RGB", (max(680, image.width), image.height + 90), (20, 20, 20)
        )
        canvas.paste(image, (0, 0))
        legend = ImageDraw.Draw(canvas)
        captions = [
            ("Rouge : etoiles exclues", cls.COLORS["star"]),
            ("Orange : signal diffus exclu", cls.COLORS["object"]),
            ("Bleu : zones incertaines exclues", cls.COLORS["uncertain"]),
            (f"Vert : {len(tiles)} tuile(s) de ciel retenue(s)", (0, 255, 80)),
        ]
        for index, (text, color) in enumerate(captions):
            x, y = 10 + (index % 2) * 340, image.height + 8 + (index // 2) * 20
            legend.rectangle((x, y, x + 10, y + 10), outline=color, width=2)
            legend.text((x + 16, y), text, fill=color)
        legend.text(
            (10, image.height + 52),
            f"Grille grise : {tile_size} x {tile_size} pixels natifs ; voiles/contours : exclusions exactes.",
            fill="white",
        )
        legend.text(
            (10, image.height + 69),
            (
                "Cyan : points RBF alignes ; croix blanche : tuile sans point du gradient."
                if gradient_points is not None
                else "Seuls les pixels de ciel dans les cadres verts servent a la mesure du bruit."
            ),
            fill="white",
        )
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(destination)
        logging.info(
            "Image des zones de qualite : %s ; %d tuiles de ciel retenues",
            destination,
            len(tiles),
        )
        return destination
