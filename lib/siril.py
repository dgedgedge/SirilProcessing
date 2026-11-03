#!/bin/env python3
"""Exécution de Siril et manipulation de séquences FITS Siril."""

from __future__ import annotations

import argparse
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Iterable, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from lib.cpu_config import CpuConfig
from lib.parameterized_component import ParameterizedComponent


class Siril(ParameterizedComponent):
    """
    Classe pour gérer l'exécution de Siril avec validation et mémorisation des configurations.
    """

    CONFIG_DEFAULTS = {
        "siril_path": "siril",
        "siril_mode": "flatpak",
    }

    @classmethod
    def add_arguments(
        cls,
        parser: argparse.ArgumentParser,
    ) -> None:
        """Déclare les options du service avec leurs valeurs locales."""
        group = parser.add_argument_group("Exécution Siril")
        group.add_argument(
            "-s",
            "--siril-path",
            "--photometry-siril-path",
            dest="siril_path",
            default=cls.CONFIG_DEFAULTS["siril_path"],
            help="Exécutable Siril (en mode native/appimage ; sans effet en flatpak)",
        )
        group.add_argument(
            "-m",
            "--siril-mode",
            "--photometry-siril-mode",
            dest="siril_mode",
            choices=["native", "flatpak", "appimage"],
            default=cls.CONFIG_DEFAULTS["siril_mode"],
            help="Mode d'exécution de Siril",
        )

    # Attributs de classe pour la configuration globale par défaut
    _default_siril_path = "siril"
    _default_siril_mode = "flatpak"

    def __init__(
        self, siril_path: str | None = None, siril_mode: str | None = None
    ) -> None:
        """
        Initialise et valide Siril : constructeur > paramètres liés > défauts.

        Args:
            siril_path: Chemin explicite ; sinon paramètres liés, puis défauts globaux
            siril_mode: Mode explicite ; sinon paramètres liés, puis défauts globaux

        Raises:
            ValueError: Si la configuration n'est pas valide
        """
        bound = self._args
        self._siril_path = (
            siril_path
            if siril_path is not None
            else getattr(bound, "siril_path", self._default_siril_path)
        )
        self._siril_mode = (
            siril_mode
            if siril_mode is not None
            else getattr(bound, "siril_mode", self._default_siril_mode)
        )
        self._validated = False

        # Validation lors de l'initialisation
        if not self._validate_configuration():
            raise ValueError(
                f"Configuration Siril invalide: path='{self._siril_path}', mode='{self._siril_mode}'"
            )

    @classmethod
    def configure_defaults(
        cls, siril_path: str | None = None, siril_mode: str | None = None
    ) -> None:
        """
        Configure les valeurs par défaut pour toutes les instances Siril futures.
        Valide immédiatement la configuration.

        Args:
            siril_path: Chemin par défaut vers l'exécutable Siril
            siril_mode: Mode d'exécution par défaut ('native', 'flatpak', ou 'appimage')

        Raises:
            ValueError: Si la configuration n'est pas valide
        """
        if siril_path is not None:
            cls._default_siril_path = siril_path
        if siril_mode is not None:
            cls._default_siril_mode = siril_mode

        # Validation immédiate de la nouvelle configuration
        temp_instance = cls(
            siril_path=cls._default_siril_path,
            siril_mode=cls._default_siril_mode,
        )
        if not temp_instance._validated:
            # Restaurer les anciennes valeurs en cas d'échec
            if siril_path is not None:
                cls._default_siril_path = "siril"
            if siril_mode is not None:
                cls._default_siril_mode = "flatpak"
            raise ValueError(
                f"Configuration Siril invalide: path='{cls._default_siril_path}', mode='{cls._default_siril_mode}'"
            )

        logging.info(
            f"Configuration Siril globale mise à jour et validée: path={cls._default_siril_path}, mode={cls._default_siril_mode}"
        )

    @classmethod
    def get_default_config(cls) -> tuple[str, str]:
        """
        Retourne la configuration par défaut actuelle.

        Returns:
            Tuple (siril_path, siril_mode)
        """
        return cls._default_siril_path, cls._default_siril_mode

    @classmethod
    def create_with_defaults(cls) -> Siril:
        """
        Crée une nouvelle instance Siril avec la configuration par défaut.

        Returns:
            Instance Siril configurée avec les valeurs par défaut
        """
        return cls(
            siril_path=cls._default_siril_path,
            siril_mode=cls._default_siril_mode,
        )

    @property
    def siril_path(self) -> str:
        """Retourne le chemin vers Siril."""
        return self._siril_path

    @siril_path.setter
    def siril_path(self, path: str) -> str:
        """Définit le chemin vers Siril et re-valide la configuration."""
        old_path = self._siril_path
        self._siril_path = path
        self._validated = False
        if not self._validate_configuration():
            # Restaurer l'ancienne valeur en cas d'échec
            self._siril_path = old_path
            self._validated = True  # L'ancienne configuration était valide
            raise ValueError(f"Chemin Siril invalide: '{path}'")

    @property
    def siril_mode(self) -> str:
        """Retourne le mode d'exécution de Siril."""
        return self._siril_mode

    @siril_mode.setter
    def siril_mode(self, mode: str) -> str:
        """Définit le mode d'exécution de Siril et re-valide la configuration."""
        old_mode = self._siril_mode
        self._siril_mode = mode
        self._validated = False
        if not self._validate_configuration():
            # Restaurer l'ancienne valeur en cas d'échec
            self._siril_mode = old_mode
            self._validated = True  # L'ancienne configuration était valide
            raise ValueError(f"Mode Siril invalide: '{mode}'")

    @property
    def is_validated(self) -> bool:
        """Retourne True si la configuration a été validée avec succès."""
        return self._validated

    def _validate_configuration(self) -> bool:
        """
        Valide la configuration Siril actuelle.

        Returns:
            True si la configuration est valide, False sinon
        """
        try:
            # Validation du mode
            valid_modes = ["native", "flatpak", "appimage"]
            if self._siril_mode not in valid_modes:
                logging.error(
                    f"Mode Siril invalide: {self._siril_mode}. Modes valides: {valid_modes}"
                )
                self._validated = False
                return False

            # Validation selon le mode
            if self._siril_mode == "flatpak":
                # Vérifier si flatpak est disponible
                result = subprocess.run(
                    ["flatpak", "--version"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0:
                    logging.error("Flatpak n'est pas disponible sur ce système")
                    self._validated = False
                    return False

                # Vérifier si Siril est installé via flatpak
                result = subprocess.run(
                    ["flatpak", "list", "--app"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0 or "org.siril.Siril" not in result.stdout:
                    logging.error("Siril n'est pas installé via Flatpak")
                    self._validated = False
                    return False

            elif self._siril_mode in ["native", "appimage"]:
                # Vérifier si l'exécutable existe et est accessible
                if not shutil.which(self._siril_path) and not os.path.isfile(
                    self._siril_path
                ):
                    logging.error(f"Exécutable Siril introuvable: {self._siril_path}")
                    self._validated = False
                    return False

                # Vérifier si le fichier est exécutable
                if os.path.isfile(self._siril_path) and not os.access(
                    self._siril_path, os.X_OK
                ):
                    logging.error(
                        f"Le fichier Siril n'est pas exécutable: {self._siril_path}"
                    )
                    self._validated = False
                    return False

            logging.info(
                f"Configuration Siril validée: mode={self._siril_mode}, path={self._siril_path}"
            )
            self._validated = True
            return True

        except Exception as e:
            logging.error(
                f"Erreur lors de la validation de la configuration Siril: {e}"
            )
            self._validated = False
            return False

    def run_siril_script(
        self,
        siril_script_content: str,
        working_dir: str,
        script_name: str | None = None,
        *,
        initfile: Path | None = None,
    ) -> bool:
        """Exécute un script Siril avec l’exigence commune de version minimale.

        La première commande du script sauvegardé est ``requires 1.4`` ; toute
        exigence de version fournie dans son en-tête est remplacée. ``setcpu``
        est ajouté juste après cette directive. La sortie standard et les erreurs
        sont conservées dans le fichier journal associé.

        Parameters
        ----------
        siril_script_content : str
            Commandes Siril à exécuter, avec commentaires et espaces initiaux
            éventuels.
        working_dir : str
            Répertoire de travail utilisé pour le script et son journal.
        script_name : str, optional
            Nom du script sauvegardé ; les caractères non sûrs sont remplacés.
        initfile : Path, optional
            Préférences isolées passées avec ``-i``, sans modifier les préférences
            de l’utilisateur.

        Returns
        -------
        bool
            ``True`` si Siril termine avec le code zéro.

        Raises
        ------
        RuntimeError
            Configuration invalide, mode inconnu ou script Siril terminé avec
            un code non nul. En cas d’échec du script, l’exception indique son
            code de sortie et le chemin du journal.
        OSError
            Une erreur d’accès au répertoire, au script ou au journal empêche
            l’exécution.
        """
        if not self._validated:
            raise RuntimeError(
                "Configuration Siril non valide. Impossible d'exécuter le script."
            )

        working_path = Path(working_dir)
        working_path.mkdir(parents=True, exist_ok=True)
        if script_name:
            safe_name = "".join(
                ch if ch.isalnum() or ch in "._-" else "_" for ch in script_name
            )
            if not safe_name.endswith(".sps"):
                safe_name = f"{safe_name}.sps"
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            safe_name = f"siril_script_{timestamp}.sps"

        script_path = working_path / safe_name
        log_path = script_path.with_suffix(".log")
        lines = siril_script_content.splitlines(keepends=True)
        header: list[str] = []
        body_start = 0
        for line in lines:
            stripped = line.strip()
            command = stripped.split(maxsplit=1)[0] if stripped else ""
            if not stripped or stripped.startswith("#"):
                header.append(line)
                body_start += 1
                continue
            if command.lower() == "requires":
                body_start += 1
                continue
            break
        lines = lines[body_start:]
        if header and not header[-1].endswith("\n"):
            header[-1] += "\n"
        script_lines = (
            header
            + ["requires 1.4\n", f"setcpu {CpuConfig.get_limit()}\n"]
            + lines
        )
        siril_script_content = "".join(script_lines)
        logging.info(
            "Exécution du script Siril %s dans %s", script_path, working_dir
        )
        script_path.write_text(siril_script_content, encoding="utf-8")
        logging.debug(
            "Contenu du script Siril %s :\n%s", script_path, siril_script_content
        )

        if self._siril_mode in ("native", "appimage"):
            cmd = [self._siril_path, "-s", script_path]
        elif self._siril_mode == "flatpak":
            cmd = [
                "flatpak",
                "run",
                "--command=siril-cli",
                "org.siril.Siril",
                "-s",
                script_path,
            ]
        else:
            raise RuntimeError(f"Mode Siril inconnu: {self._siril_mode}")

        if initfile is not None:
            cmd.extend(["-i", str(Path(initfile).resolve())])
        with log_path.open("w", encoding="utf-8") as log_file:
            result = subprocess.run(
                cmd,
                cwd=working_dir,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                check=False,
            )
        if result.returncode != 0:
            logging.error(
                "Le script Siril a échoué avec le code d'erreur %d.",
                result.returncode,
            )
            if result.returncode < 0:
                try:
                    name = signal.Signals(-result.returncode).name
                except ValueError:
                    name = str(-result.returncode)
                logging.error(
                    "Processus Siril interrompu par le signal %s (script %s).",
                    name,
                    script_path,
                )
                if result.returncode == -signal.SIGKILL:
                    logging.error(
                        "SIGKILL : vérifier les journaux système "
                        "(OOM / pression mémoire ou arrêt externe)."
                    )
            output = log_path.read_text(encoding="utf-8", errors="replace")
            logging.error("Sortie Siril (%s) :\n%s", log_path, output)
            raise RuntimeError(
                f"Échec fatal du script Siril {script_path} "
                f"(code {result.returncode}); consulter {log_path}"
            )
        logging.info("Script Siril exécuté avec succès.")
        return True


@dataclass
class RegistrationData:
    """Quality measurements and image-to-reference transformation for one layer."""

    fwhm: float
    weighted_fwhm: float
    roundness: float
    quality: float
    background: float
    number_of_stars: int
    homography: np.ndarray
    _original: str = field(default="", repr=False)
    _snapshot: tuple = field(default=(), repr=False)

    @property
    def values(self) -> list[float]:
        """Retourne les six mesures Siril dans l’ordre du format de séquence."""
        return [
            self.fwhm,
            self.weighted_fwhm,
            self.roundness,
            self.quality,
            self.background,
            self.number_of_stars,
        ]

    @property
    def valid(self) -> bool:
        """Vérifie les mesures finies et une homographie 3 × 3 non singulière."""
        h = self.homography
        return (
            np.isfinite(self.values).all()
            and self.fwhm > 0
            and h.shape == (3, 3)
            and np.isfinite(h).all()
            and abs(np.linalg.det(h)) > 1e-8
            and abs(h[2, 2]) >= 1e-8
        )

    def to_line(self, layer: str) -> str:
        """Sérialise le canal et son homographie sans arrondir les mesures inchangées.

        Raises:
            ValueError: Si la matrice n’a pas la forme 3 × 3."""
        if np.asarray(self.homography).shape != (3, 3):
            raise ValueError("La transformation doit être une matrice 3 × 3")
        values = self.values + list(np.asarray(self.homography).ravel())
        if (
            self._original
            and self._original.split()[0] == layer
            and np.array_equal(values, self._snapshot, equal_nan=True)
        ):
            return self._original
        return (
            f"{layer} "
            + " ".join(format(v, ".17g") for v in self.values)
            + " H "
            + " ".join(format(v, ".17g") for v in np.asarray(self.homography).ravel())
        )


@dataclass
class SequenceImage:
    """One sequence entry; filename is its FITS, source_path its optional input."""

    number: int
    included: bool
    filename: Path
    source_path: Path | None = None
    registrations: dict[str, RegistrationData] = field(default_factory=dict)
    extra_fields: list[str] = field(default_factory=list)
    statistics: dict[str, str] = field(default_factory=dict)
    _original: str = field(default="", repr=False)

    @property
    def processing_path(self) -> Path:
        """Path to pass to native-pixel treatments, before sequence conversion."""
        return self.source_path if self.source_path is not None else self.filename

    def registration(self, layer: str | None = None) -> RegistrationData:
        """Retourne le canal demandé ou le premier disponible.

        Raises:
            ValueError: Si aucune transformation ne correspond au canal."""
        key = layer if layer is not None else next(iter(self.registrations), None)
        if key not in self.registrations:
            raise ValueError(
                f"Alignement manquant pour image {self.number}, canal {key}"
            )
        return self.registrations[key]

    def to_line(self) -> str:
        """Sérialise le numéro, l’inclusion et les champs supplémentaires de l’image."""
        tokens = ["I", str(self.number), str(int(self.included)), *self.extra_fields]
        return self._original if self._original.split() == tokens else " ".join(tokens)


class SirilSequence:
    """Read, edit and atomically write a sequence without rewriting FITS pixels.

    Use image.included to exclude an exposure. Keeping entries preserves Siril's
    positional references (reference image, statistics, overlap and astrometry).
    """

    EXTENSIONS = (".fit", ".fits", ".fts", ".fit.fz", ".fits.fz")

    def __init__(
        self,
        path: Path | str,
        header: Sequence[str],
        images: Sequence[SequenceImage],
        lines: Sequence[str],
    ) -> None:
        """Copie les métadonnées de séquence sans lire ni modifier les pixels FITS."""
        self.path = Path(path)
        self.header = list(header)
        self._images = list(images)
        self._lines = list(lines)

    @property
    def images(self) -> tuple[SequenceImage, ...]:
        """Stable sequence order; mutate image fields, not the positional list."""
        return tuple(self._images)

    @property
    def name(self) -> str:
        """Retourne le préfixe des fichiers images déclaré dans l’en-tête."""
        return self.header[1]

    @property
    def version(self) -> int:
        """Retourne la version entière du format Siril déclarée dans l’en-tête."""
        return int(self.header[7])

    @property
    def registration_layers(self) -> list[str]:
        """Liste les canaux d’alignement de la première image, ou aucun si vide."""
        return list(self.images[0].registrations) if self.images else []

    @classmethod
    def read(
        cls, path: Path | str, source_files: Iterable[Path | str] | None = None
    ) -> SirilSequence:
        """Lit une séquence FITS Siril de version 4 à 7 et valide ses indices.

        Les fichiers sources facultatifs sont associés dans l’ordre de la séquence.
        Les pixels ne sont pas chargés.

        Raises:
            OSError: Si le fichier de séquence ne peut pas être lu.
            ValueError: Si l’en-tête, la numérotation ou les alignements sont incohérents."""
        path = Path(path)
        lines = path.read_text(encoding="utf-8").splitlines()
        headers = [shlex.split(line) for line in lines if line.startswith("S ")]
        if len(headers) != 1 or len(headers[0]) < 8:
            raise ValueError("En-tête de séquence absent ou invalide")
        header = headers[0]
        if int(header[7]) not in (4, 5, 6, 7):
            raise ValueError("Version de séquence non prise en charge")
        if any(line.strip() in ("TS", "TA", "TF") for line in lines):
            raise ValueError(
                "Seules les séquences FITS en fichiers individuels sont prises en charge"
            )
        images = []
        for line in lines:
            if not line.startswith("I "):
                continue
            fields = line.split()
            if len(fields) < 3 or fields[2] not in ("0", "1"):
                raise ValueError("Entrée image invalide")
            number = int(fields[1])
            stem = f"{header[1]}{number:0{int(header[5])}d}"
            candidates = [path.parent / (stem + ext) for ext in cls.EXTENSIONS]
            existing = [p for p in candidates if p.exists()]
            if len(existing) > 1:
                raise ValueError(f"Fichier image ambigu : {stem}")
            filename = existing[0] if existing else candidates[0]
            images.append(
                SequenceImage(
                    number,
                    fields[2] == "1",
                    filename,
                    extra_fields=fields[3:],
                    _original=line,
                )
            )
        if len(images) != int(header[3]) or len({i.number for i in images}) != len(
            images
        ):
            raise ValueError("Nombre ou numérotation des images incohérent")
        if sum(i.included for i in images) != int(header[4]):
            raise ValueError("Nombre d’images incluses incohérent")
        groups = {}
        for line in lines:
            if re.match(r"^R[0-9*]\s", line):
                fields = line.split()
                if len(fields) != 17 or fields[7] != "H":
                    raise ValueError("Transformation Siril invalide")
                values = list(map(float, fields[1:7]))
                if not np.isfinite(values[5]) or not values[5].is_integer():
                    raise ValueError("Nombre d’étoiles invalide")
                h = np.array(fields[8:], dtype=float).reshape(3, 3)
                data = RegistrationData(
                    *values[:5],
                    int(values[5]),
                    h,
                    _original=line,
                    _snapshot=tuple(values + list(h.ravel())),
                )
                groups.setdefault(fields[0], []).append(data)
            match = re.match(r"^(M[0-9*])-(\d+)\s+(.*)$", line)
            if match:
                index = int(match[2])
                if index >= len(images):
                    raise ValueError("Statistiques hors des indices de séquence")
                images[index].statistics[match[1]] = match[3]
        for layer, rows in groups.items():
            if len(rows) != len(images):
                raise ValueError("Alignements manquants")
            for image, registration in zip(images, rows):
                image.registrations[layer] = registration
        sequence = cls(path, header, images, lines)
        if source_files is not None:
            sequence.bind_sources(source_files)
        return sequence

    @classmethod
    def from_files(
        cls, path: Path | str, prefix: str, files: Iterable[Path | str], layers: int = 1
    ) -> SirilSequence:
        """Construit une séquence incluse à partir de fichiers au préfixe commun.

        Raises:
            ValueError: Si la liste est vide ou sa numérotation ambiguë."""
        images, widths = [], set()
        for filename in sorted(map(Path, files)):
            match = re.fullmatch(
                re.escape(prefix) + r"(\d+)\.(?:fit|fits|fts)(?:\.fz)?", filename.name
            )
            if not match:
                raise ValueError(
                    f"Nom incompatible avec le préfixe {prefix} : {filename}"
                )
            widths.add(len(match[1]))
            images.append(SequenceImage(int(match[1]), True, filename))
        if (
            not images
            or len(widths) != 1
            or len({i.number for i in images}) != len(images)
        ):
            raise ValueError("Numérotation ambiguë ou séquence vide")
        images.sort(key=lambda i: i.number)
        header = [
            "S",
            prefix,
            str(images[0].number),
            str(len(images)),
            str(len(images)),
            str(widths.pop()),
            "0",
            "4",
            "0",
        ]
        return cls(
            path,
            header,
            images,
            [f"S '{prefix}' " + " ".join(header[2:]), f"L {layers}", "I"],
        )

    def bind_sources(self, files: Iterable[Path | str]) -> None:
        """Bind source filenames by sequence order, validating the correspondence size."""
        files = list(files)
        if len(files) != len(self.images):
            raise ValueError("Nombre de fichiers sources différent de la séquence")
        for image, path in zip(self.images, files):
            image.source_path = Path(path)

    def selected_registration_copy(self) -> SirilSequence:
        """Compact selected independent frames for a fresh registration stage.

        Preserve file numbers, measurements and the reference when retained.
        Build fresh sequence metadata rather than retaining index-based overlap
        or distortion records which would be invalid after compaction.
        FITS headers remain the source of astrometry for the next registration.
        """
        selected, seen = [], set()
        for image in self.images:
            source = image.processing_path.resolve()
            if image.included and source not in seen:
                selected.append(image)
                seen.add(source)
        if not selected:
            raise ValueError("Aucune pose sélectionnée")
        layers = next(
            (int(line.split()[1]) for line in self._lines if line.startswith("L ")), 1
        )
        result = self.from_files(
            self.path, self.name, [image.filename for image in selected], layers
        )
        # The version-specific S fields (e.g. variable-size / Drizzle flags in
        # v6) are mandatory for Siril, even though the Python reader is lenient.
        result.header = list(self.header)
        result.header[2] = str(result.images[0].number)
        originals = {image.number: image for image in selected}
        for image in result.images:
            original = originals[image.number]
            image.source_path = original.source_path
            image.registrations = deepcopy(original.registrations)
            image.statistics = deepcopy(original.statistics)
        ref_index = int(self.header[6])
        reference = (
            self.images[ref_index].number if 0 <= ref_index < len(self.images) else None
        )
        result.header[6] = str(
            next(
                (
                    i
                    for i, image in enumerate(result.images)
                    if image.number == reference
                ),
                0,
            )
        )
        return result

    def duplicate(self, image: SequenceImage) -> SequenceImage:
        """Append an independent entry and a FITS symlink for discrete weighting."""
        if not any(item is image for item in self.images):
            raise ValueError("Image étrangère à cette séquence")
        if not image.filename.is_file():
            raise ValueError(f"Image de séquence introuvable : {image.filename}")
        number = max(i.number for i in self.images) + 1
        extension = next(
            ext
            for ext in sorted(self.EXTENSIONS, key=len, reverse=True)
            if image.filename.name.endswith(ext)
        )
        filename = (
            self.path.parent / f"{self.name}{number:0{int(self.header[5])}d}{extension}"
        )
        clone = deepcopy(image)
        clone.number, clone.filename, clone._original = number, filename, ""
        filename.symlink_to(
            image.filename.resolve()
        )  # Refuse to overwrite any existing file.
        self._images.append(clone)
        return clone

    def to_lines(self) -> list[str]:
        """Reconstruit les lignes et indices de séquence en conservant les métadonnées.

        Raises:
            ValueError: Si la séquence est vide ou contient des numéros dupliqués."""
        if not self.images or len({i.number for i in self.images}) != len(self.images):
            raise ValueError("Séquence vide ou numérotation dupliquée")
        if any(i.number < 0 for i in self.images):
            raise ValueError("Numéro d’image négatif")
        if any(i.included not in (False, True) for i in self.images):
            raise ValueError("Inclusion attendue : True ou False")
        if any(c in self.name for c in "'\r\n"):
            raise ValueError("Nom de séquence incompatible avec le format Siril")
        header = list(self.header)
        header[3:5] = [str(len(self.images)), str(sum(i.included for i in self.images))]
        layers = self.registration_layers
        if any(set(i.registrations) != set(layers) for i in self.images):
            raise ValueError("Canaux d’alignement incohérents entre les images")
        output, written = [], set()
        for line in self._lines:
            key = line.split()[0] if line.strip() else ""
            if key == "S":
                output.append(
                    line
                    if shlex.split(line) == header
                    else f"S '{self.name}' " + " ".join(header[2:])
                )
            elif key == "I":
                if key not in written:
                    output.extend(i.to_line() for i in self.images)
                    written.add(key)
            elif re.fullmatch(r"R[0-9*]", key):
                if key not in written:
                    output.extend(i.registration(key).to_line(key) for i in self.images)
                    written.add(key)
            elif re.fullmatch(r"M[0-9*]-\d+", key):
                # Emit this layer's per-image statistics with correct positional indices.
                layer = key.split("-")[0]
                if layer not in written:
                    output.extend(
                        f"{layer}-{index} {image.statistics[layer]}"
                        for index, image in enumerate(self.images)
                        if layer in image.statistics
                    )
                    written.add(layer)
            else:
                output.append(line)
        for layer in layers:
            if layer not in written:
                output.extend(i.registration(layer).to_line(layer) for i in self.images)
        for layer in dict.fromkeys(
            layer for image in self.images for layer in image.statistics
        ):
            if layer not in written:
                output.extend(
                    f"{layer}-{index} {image.statistics[layer]}"
                    for index, image in enumerate(self.images)
                    if layer in image.statistics
                )
        return output

    def write(self, path: Path | str | None = None) -> Path:
        """Atomic metadata write; writing elsewhere does not copy the FITS files."""
        destination = Path(path) if path is not None else self.path
        lines = self.to_lines()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=destination.parent,
                prefix=destination.name + ".",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write("\n".join(lines) + "\n")
            temporary.replace(destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return destination
