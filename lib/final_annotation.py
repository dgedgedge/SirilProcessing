"""Dernier export de présentation : encart de prise de vue sur le rendu final.

Les valeurs viennent exclusivement de l'en-tête FITS, sans reconstitution d'une
intégration totale de mosaïque. Voir docs/scripts/postProcess/annotations.md.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import textwrap

from astropy.io import fits
from PIL import Image, ImageDraw, ImageFont

from lib.annotation_catalog import AnnotationCatalog, SkyObject
from lib.processor import processor
from lib.render_exports import read_render_image
from lib.type_defs import JSONReport


class FinalImageAnnotator(processor):
    """Produit un PNG distinct, orienté comme le JPEG, avec noms célestes et encart inférieur gauche.

    Aucun pixel scientifique n'est modifié. Les données manquantes sont omises,
    et les poses décrivent l'en-tête de référence, jamais un total supposé.
    """

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie cette étape de présentation dans les rapports."""
        return "annotation"

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare l'export final, activé par défaut et mémorisable via Config."""
        parser.add_argument(
            "--final-annotation", action=argparse.BooleanOptionalAction,
            default=True,
            help="Exporter un PNG final annoté avec les informations de prise de vue",
        )

        parser.add_argument(
            "--annotation-objects", action=argparse.BooleanOptionalAction,
            default=True, help="Ajouter les noms OpenNGC avec le WCS du rendu final",
        )
        parser.add_argument(
            "--annotation-magnitude", type=float, default=14.0,
            help="Magnitude V sinon B maximale (0 à 25), hors objets Messier",
        )
        parser.add_argument(
            "--annotation-max-objects", type=int, default=40,
            help="Nombre maximal de noms dans le champ (0 à 200)",
        )

    @staticmethod
    def acquisition_lines(header: fits.Header) -> list[str]:
        """Formate les cartes présentes, sans déduire le nombre ou la durée des poses.

        EXPTIME est en secondes, CCD-TEMP en degrés Celsius. DATE-OBS est
        reproduite sans inventer de fuseau. Les valeurs numériques non finies
        sont omises ; ni STACKCNT ni LIVETIME ne sont totalisés sur une mosaïque.
        """
        lines = ["Prise de vue · en-tête de référence"]
        for key, label, unit in (
            ("OBJECT", "Objet", ""), ("DATE-OBS", "Date", ""),
            ("TELESCOP", "Instrument", ""), ("INSTRUME", "Caméra", ""),
            ("FILTER", "Filtre", ""), ("EXPTIME", "Pose de référence", " s"),
            ("GAIN", "Gain", ""), ("OFFSET", "Offset", ""),
            ("CCD-TEMP", "Température capteur", " °C"),
        ):
            value = header.get(key)
            if value is None or isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                if not math.isfinite(value) or (key == "EXPTIME" and value <= 0):
                    continue
                value = f"{value:g}"
            value = " ".join(str(value).split())
            if value:
                lines.append(f"{label} : {value}{unit}")
        if len(lines) == 1:
            lines.append("Informations non renseignées")
        return lines

    @staticmethod
    def draw_panel(image: Image.Image, lines: list[str]) -> tuple[int, int, int, int]:
        """Compose un encart ajusté automatiquement, sans déborder de l'image.

        La police et les retours à la ligne dépendent des dimensions. La police
        DejaVu livrée avec Matplotlib évite une dépendance aux fontes système.
        Retourne le rectangle du panneau en pixels écran (gauche, haut, droite, bas).
        """
        from matplotlib import get_data_path

        font_path = Path(get_data_path()) / "fonts/ttf/DejaVuSans.ttf"
        margin = max(4, min(image.size) // 60)
        available_width = image.width - 4 * margin
        size = max(8, min(image.size) // 70)
        while True:
            font = ImageFont.truetype(str(font_path), size)
            columns = max(8, int(available_width / font.getlength("M")))
            wrapped = [part for line in lines for part in textwrap.wrap(
                line, width=columns, break_long_words=True, break_on_hyphens=False,
            )]
            spacing = max(2, size // 4)
            draw = ImageDraw.Draw(image)
            text = "\n".join(wrapped)
            bounds = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing)
            width = bounds[2] - bounds[0] + 2 * margin
            height = bounds[3] - bounds[1] + 2 * margin
            if height <= image.height - 2 * margin and width <= image.width - 2 * margin:
                break
            size -= 1
            if size < 1:
                raise ValueError("Image trop petite pour l'encart de prise de vue")
        box = (margin, image.height - margin - height,
               margin + width, image.height - margin)
        overlay = Image.new("RGBA", image.size)
        panel = ImageDraw.Draw(overlay)
        panel.rectangle(box, fill=(5, 10, 18, 205))
        panel.multiline_text(
            (box[0] + margin - bounds[0], box[1] + margin - bounds[1]),
            text, font=font, spacing=spacing, fill=(240, 244, 250, 255),
        )
        with image.convert("RGBA") as background:
            with Image.alpha_composite(background, overlay) as composed:
                image.paste(composed.convert("RGB"))
        overlay.close()
        return box

    @staticmethod
    def draw_objects(
        image: Image.Image, objects: list[tuple[SkyObject, float, float]],
        panel_box: tuple[int, int, int, int], maximum: int,
    ) -> JSONReport:
        """Place les contours catalogués et les noms autour des objets.

        Huit positions sont testées autour du centre catalogué. Les rectangles
        de texte et les segments de rappel déjà occupés sont exclus. Les objets
        sans place sont audités, sans déplacer leurs positions astronomiques.
        """
        from matplotlib import get_data_path

        font = ImageFont.truetype(
            str(Path(get_data_path()) / "fonts/ttf/DejaVuSans.ttf"),
            max(9, min(image.size) // 65),
        )
        draw = ImageDraw.Draw(image)
        radius = max(6, font.size // 2)
        gap = max(4, font.size // 3)
        occupied = [panel_box]
        leaders: list[tuple[float, float, float, float]] = []
        placed: list[JSONReport] = []
        omitted = []

        def intersects(
            first: tuple[float, float, float, float],
            second: tuple[float, float, float, float],
        ) -> bool:
            """Détecte l'intersection de deux rectangles fermés en pixels écran."""
            return not (first[2] < second[0] or first[0] > second[2]
                        or first[3] < second[1] or first[1] > second[3])

        for obj, x, y in objects:
            if len(placed) >= maximum:
                omitted.append(obj.name)
                continue
            circle_box = (x - radius, y - radius, x + radius, y + radius)
            if intersects(circle_box, panel_box):
                omitted.append(obj.name)
                continue
            bounds = draw.textbbox((0, 0), obj.label, font=font, stroke_width=1)
            width = bounds[2] - bounds[0] + 2 * gap
            height = bounds[3] - bounds[1] + 2 * gap
            rx = max(abs(px - x) for px, _ in obj.outline) if obj.outline else radius
            ry = max(abs(py - y) for _, py in obj.outline) if obj.outline else radius
            offset = rx + 2 * gap
            vertical_offset = ry + 2 * gap
            positions = (
                (x + offset, y - height / 2), (x - offset - width, y - height / 2),
                (x - width / 2, y - vertical_offset - height), (x - width / 2, y + vertical_offset),
                (x + offset, y - vertical_offset - height), (x - offset - width, y + vertical_offset),
                (x + offset, y + vertical_offset), (x - offset - width, y - vertical_offset - height),
            )
            # Les grands objets peuvent dépasser le champ : chercher aussi une
            # étiquette le long des portions visibles de leur contour projeté.
            positions += tuple(
                (px + gap, py - height / 2)
                for px, py in obj.outline[::4]
                if gap <= px < image.width - gap and gap <= py < image.height - gap
            )
            chosen = None
            for left, top in positions:
                box = (left, top, left + width, top + height)
                if (left < gap or top < gap or box[2] >= image.width - gap
                        or box[3] >= image.height - gap):
                    continue
                end_x = min(max(x, left), box[2])
                end_y = min(max(y, top), box[3])
                leader_box = (min(x, end_x), min(y, end_y), max(x, end_x), max(y, end_y))
                if any(intersects(box, other) or intersects(leader_box, other)
                       for other in occupied):
                    continue
                if any(intersects(box, other) or intersects(leader_box, other)
                       for other in leaders):
                    continue
                if any(left - radius <= ox <= box[2] + radius
                       and top - radius <= oy <= box[3] + radius
                       for _, ox, oy in objects):
                    continue
                chosen = box, end_x, end_y, leader_box
                break
            if chosen is None:
                omitted.append(obj.name)
                continue
            box, end_x, end_y, leader_box = chosen
            # Le rappel part du contour, sans barrer le centre de l'objet.
            length = math.hypot(end_x - x, end_y - y)
            start = (min(obj.outline, key=lambda point: math.hypot(
                point[0] - end_x, point[1] - end_y,
            )) if obj.outline else (x + radius * (end_x - x) / length,
                                    y + radius * (end_y - y) / length))
            color = (135, 220, 245)
            draw.line((*start, end_x, end_y), fill=color, width=max(1, font.size // 18))
            if obj.outline:
                for first, last in zip(obj.outline, obj.outline[1:]):
                    segment_box = (min(first[0], last[0]), min(first[1], last[1]),
                                   max(first[0], last[0]), max(first[1], last[1]))
                    if not intersects(segment_box, panel_box):
                        draw.line((*first, *last), fill=color, width=max(1, font.size // 18))
            else:
                draw.line((x - radius, y, x + radius, y), fill=color)
                draw.line((x, y - radius, x, y + radius), fill=color)
            draw.rectangle(box, fill=(5, 10, 18))
            draw.text((box[0] + gap - bounds[0], box[1] + gap - bounds[1]),
                      obj.label, font=font, fill=color, stroke_width=1,
                      stroke_fill=(5, 10, 18))
            occupied.append(box)
            leaders.append(leader_box)
            placed.append({"name": obj.name, "label": obj.label, "x": x, "y": y,
                           "ra_deg": obj.ra, "dec_deg": obj.dec,
                           "magnitude": obj.magnitude, "label_bounds": list(box),
                           "common_names": list(obj.common_names),
                           "common_name_fr": obj.common_name_fr or None,
                           "outline_kind": "catalog_extent" if obj.outline else "center_marker",
                           "major_arcmin": obj.major_arcmin, "minor_arcmin": obj.minor_arcmin,
                           "position_angle_deg": obj.position_angle_deg,
                           "outline_pixels": [list(point) for point in obj.outline]})
        return {"labels": placed, "omitted": omitted}

    def post_process(
        self, input_path: Path, output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Annote un FITS rendu et écrit le PNG et son audit JSON.

        Parameters
        ----------
        input_path : Path
            Image finale exportée, RGB normalisée, SPNLIN=True.
        output_path : Path
            Rapport JSON ; le PNG voisin du FITS porte le suffixe `_annotated.png`.
        args : argparse.Namespace, optional
            Surcharge locale ; final_annotation=False désactive l'export.

        Returns
        -------
        JSONReport
            Statut, lignes affichées et chemins. Un FITS encore linéaire ou une
            image miniature est ignoré explicitement. Les erreurs d'E/S restent fatales.
        """
        runtime = self._get_args(args)
        source, report = Path(input_path).resolve(), Path(output_path).resolve()
        destination = source.with_name(source.stem + "_annotated.png")
        if source in (report, destination):
            raise ValueError("L'annotation ne peut pas remplacer sa source")
        report.parent.mkdir(parents=True, exist_ok=True)
        result: JSONReport = {"status": "DISABLED"}
        if getattr(runtime, "final_annotation", True):
            header = fits.getheader(source)
            if not header.get("SPNLIN", False):
                result = {"status": "SKIPPED", "reason": "Aucun rendu non linéaire"}
            elif min(header.get("NAXIS1", 0), header.get("NAXIS2", 0)) < 96:
                result = {"status": "SKIPPED", "reason": "Image trop petite (<96 px)"}
            else:
                limit = getattr(runtime, "annotation_magnitude", 14.0)
                maximum = getattr(runtime, "annotation_max_objects", 40)
                if not math.isfinite(limit) or not 0 <= limit <= 25:
                    raise ValueError("annotation-magnitude doit être entre 0 et 25")
                if not isinstance(maximum, int) or not 0 <= maximum <= 200:
                    raise ValueError("annotation-max-objects doit être entre 0 et 200")
                lines = self.acquisition_lines(header)
                objects, sky_audit = [], {"status": "DISABLED"}
                if getattr(runtime, "annotation_objects", True):
                    objects, sky_audit = AnnotationCatalog.project(header, limit)
                    if sky_audit["status"] == "SUCCESS":
                        lines.append("Objets : OpenNGC · Mattia Verga · CC BY-SA 4.0")
                with read_render_image(source) as image:
                    box = self.draw_panel(image, lines)
                    if sky_audit["status"] == "SUCCESS":
                        sky_audit.update(self.draw_objects(image, objects, box, maximum))
                    image.save(destination, format="PNG")
                result = {
                    "status": "SUCCESS", "output_annotated_image": str(destination),
                    "lines": lines, "panel_bounds": list(box),
                    "metadata_scope": "reference_fits_header",
                    "objects": sky_audit,
                }
        report.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        return result
