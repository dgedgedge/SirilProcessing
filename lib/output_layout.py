"""Arborescence numérotée des sorties lightProcess et relais du post-traitement.

Les chemins sont calculés sans modifier les sources. Les anciens rapports
restent lisibles ; seule une racine marquée par 01_calibration utilise ce plan.
Voir docs/scripts/lightProcess/output-layout.md.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from lib.type_defs import JSONReport


@dataclass(frozen=True)
class OutputLayout:
    """Décrit les six étapes d'un objet et leurs rapports, sans état partagé."""

    root: Path

    @property
    def calibration(self) -> Path:
        """Retourne la racine des calibrations et des stacks de panneaux."""
        return self.root / '01_calibration'

    @property
    def mosaic(self) -> Path:
        """Retourne le relais linéaire commun, avec ou sans assemblage."""
        return self.root / '02_mosaic'

    @property
    def image(self) -> Path:
        """Retourne le FITS linéaire transmis au post-traitement."""
        return self.mosaic / 'image.fits'

    @property
    def report(self) -> Path:
        """Place la synthèse globale auprès de la dernière étape."""
        return self.root / '06_color' / 'postprocess.json'

    def panel(self, index: int, mosaic: bool) -> Path:
        """Numérote les panneaux dans l'ordre des entrées ; un champ reste direct."""
        if type(index) is not int or index < 1:
            raise ValueError('Index de panneau positif requis')
        return self.calibration / f'part_{index:02d}' if mosaic else self.calibration

    def calibration_plan(self, sources: list[Path], mosaic: bool) -> list[Path]:
        """Conserve l'identité des panneaux même si les entrées sont réordonnées.

        Les source.json explicites associent une entrée absolue à part_XX. Un
        nouveau panneau reçoit un numéro libre, jamais un dossier déjà occupé.
        La méthode ne crée rien. Pour un champ simple, un changement de source
        sous la même racine nécessite une purge explicite des anciens produits.
        """
        if not sources or (not mosaic and len(sources) != 1):
            raise ValueError('Une seule entrée hors mosaïque, au moins deux en mosaïque')
        if mosaic and len(sources) < 2:
            raise ValueError('Au moins deux entrées sont nécessaires pour une mosaïque')
        if len({source.resolve() for source in sources}) != len(sources):
            raise ValueError('Les entrées de calibration doivent être distinctes')
        if not mosaic:
            marker = self.calibration / 'source.json'
            if marker.is_file():
                stored = json.loads(marker.read_text(encoding='utf-8'))
                if not isinstance(stored, dict) or stored.get('source') != str(sources[0].resolve()):
                    raise ValueError('La source de cette sortie a changé ; utiliser --purge-target')
            return [self.calibration]
        occupied = {path for path in self.calibration.glob('part_*') if path.is_dir()}
        known = {}
        for directory in occupied:
            marker = directory / 'source.json'
            if marker.is_file():
                stored = json.loads(marker.read_text(encoding='utf-8'))
                source = stored.get('source') if isinstance(stored, dict) else None
                if not isinstance(source, str) or not source or source in known:
                    raise ValueError(f'Identité de panneau invalide : {marker}')
                known[source] = directory
        result = []
        for source in sources:
            key = str(source.resolve())
            directory = known.get(key)
            if directory is None:
                index = 1
                while self.panel(index, True) in occupied:
                    index += 1
                directory = self.panel(index, True)
                occupied.add(directory)
            result.append(directory)
        return result

    def stage_report(self, prefix: str) -> Path:
        """Range les moteurs dans leur étape, y compris les variantes sans chaînes."""
        stages = {
            'gradient': '03_photometrie/01_gradient/gradient.json',
            'photometry': '03_photometrie/photometry.json',
            'restoration': '04_restoration/restoration.json',
            'deconvolution': '04_restoration/01_deconvolution/deconvolution.json',
            'denoise': '04_restoration/02_denoise/denoise.json',
            'autoghs': '05_autoghs/autoghs.json',
            'final_color': '06_color/color.json',
        }
        if prefix not in stages:
            raise ValueError(f'Étape inconnue de l’arborescence : {prefix}')
        return self.root / stages[prefix]

    def create(self) -> None:
        """Crée les six répertoires sans supprimer d'anciens produits."""
        for name in ('01_calibration', '02_mosaic', '03_photometrie',
                     '04_restoration', '05_autoghs', '06_color'):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def handoff(self, source: Path) -> Path:
        """Copie le produit linéaire dans le relais sans le modifier ni changer sa date.

        Une copie identique en taille et mtime est conservée pour ne pas invalider
        les snapshots de reprise. Les noms génériques ne désignent jamais les bruts.
        """
        source = source.resolve(strict=True)
        if self.image.is_symlink():
            raise ValueError('Le relais doit être une copie, pas un lien symbolique')
        destination = self.image.resolve()
        if source == destination:
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file():
            before, after = source.stat(), destination.stat()
            if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns):
                return destination
        shutil.copy2(source, destination)
        return destination

    @classmethod
    def for_source(cls, source: Path) -> OutputLayout | None:
        """Reconnaît uniquement le relais explicite d'une nouvelle arborescence."""
        source = source.resolve()
        root = source.parent.parent
        if (source.parent.name == '02_mosaic' and source.name == 'image.fits'
                and (root / '01_calibration').is_dir()):
            return cls(root)
        return None

    @classmethod
    def for_report(cls, report: Path) -> OutputLayout | None:
        """Distingue la synthèse numérotée des rapports autonomes historiques."""
        report = report.resolve()
        root = report.parent.parent
        if (report.parent.name == '06_color' and report.name == 'postprocess.json'
                and (root / '01_calibration').is_dir()):
            return cls(root)
        return None

    @staticmethod
    def write_summary(report: Path, payload: JSONReport) -> None:
        """Écrit JSON et synthèse texte au même endroit, sans journaliser les tableaux."""
        if report.suffix.lower() != '.json':
            raise ValueError('Un rapport JSON est requis')
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
        lines = [f'Rapport : {report.name}']
        for key, value in payload.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                lines.append(f'{key} : {value}')
            elif isinstance(value, dict):
                lines.append(f"{key} : {value.get('status', 'détails dans le JSON')}")
                if isinstance(value.get('output_image'), str):
                    lines.append(f"  sortie : {value['output_image']}")
            elif isinstance(value, list):
                lines.append(f'{key} : {len(value)} élément(s), détails dans {report.name}')
                if key == 'panels':
                    for panel in value:
                        if isinstance(panel, dict) and isinstance(panel.get('source'), str):
                            lines.append(f"  {panel['source']} -> {panel.get('output_image')}")
        report.with_suffix('.txt').write_text('\n'.join(lines)+'\n', encoding='utf-8')
