"""Object model for Siril multi-file FITS sequences (format versions 4–7)."""
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import re
import shlex
import tempfile

import numpy as np


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
    _original: str = field(default='', repr=False)
    _snapshot: tuple = field(default=(), repr=False)

    @property
    def values(self):
        return [self.fwhm, self.weighted_fwhm, self.roundness, self.quality,
                self.background, self.number_of_stars]

    @property
    def valid(self):
        h = self.homography
        return (np.isfinite(self.values).all() and self.fwhm > 0 and h.shape == (3, 3)
                and np.isfinite(h).all() and abs(np.linalg.det(h)) > 1e-8
                and abs(h[2, 2]) >= 1e-8)

    def to_line(self, layer):
        if np.asarray(self.homography).shape != (3, 3):
            raise ValueError('La transformation doit être une matrice 3 × 3')
        values = self.values + list(np.asarray(self.homography).ravel())
        if self._original and self._original.split()[0] == layer and np.array_equal(values, self._snapshot, equal_nan=True):
            return self._original
        return f'{layer} ' + ' '.join(format(v, '.17g') for v in self.values) + ' H ' + ' '.join(format(v, '.17g') for v in np.asarray(self.homography).ravel())


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
    _original: str = field(default='', repr=False)

    @property
    def processing_path(self):
        """Path to pass to native-pixel treatments, before sequence conversion."""
        return self.source_path if self.source_path is not None else self.filename

    def registration(self, layer=None):
        key = layer if layer is not None else next(iter(self.registrations), None)
        if key not in self.registrations:
            raise ValueError(f'Alignement manquant pour image {self.number}, canal {key}')
        return self.registrations[key]

    def to_line(self):
        tokens = ['I', str(self.number), str(int(self.included)), *self.extra_fields]
        return self._original if self._original.split() == tokens else ' '.join(tokens)


class SirilSequence:
    """Read, edit and atomically write a sequence without rewriting FITS pixels.

    Use image.included to exclude an exposure. Keeping entries preserves Siril's
    positional references (reference image, statistics, overlap and astrometry).
    """
    EXTENSIONS = ('.fit', '.fits', '.fts', '.fit.fz', '.fits.fz')

    def __init__(self, path, header, images, lines):
        self.path = Path(path)
        self.header = list(header)
        self._images = list(images)
        self._lines = list(lines)

    @property
    def images(self):
        """Stable sequence order; mutate image fields, not the positional list."""
        return tuple(self._images)

    @property
    def name(self):
        return self.header[1]

    @property
    def version(self):
        return int(self.header[7])

    @property
    def registration_layers(self):
        return list(self.images[0].registrations) if self.images else []

    @classmethod
    def read(cls, path, source_files=None):
        path = Path(path)
        lines = path.read_text(encoding='utf-8').splitlines()
        headers = [shlex.split(line) for line in lines if line.startswith('S ')]
        if len(headers) != 1 or len(headers[0]) < 8:
            raise ValueError('En-tête de séquence absent ou invalide')
        header = headers[0]
        if int(header[7]) not in (4, 5, 6, 7):
            raise ValueError('Version de séquence non prise en charge')
        if any(line.strip() in ('TS', 'TA', 'TF') for line in lines):
            raise ValueError('Seules les séquences FITS en fichiers individuels sont prises en charge')
        images = []
        for line in lines:
            if not line.startswith('I '):
                continue
            fields = line.split()
            if len(fields) < 3 or fields[2] not in ('0', '1'):
                raise ValueError('Entrée image invalide')
            number = int(fields[1])
            stem = f'{header[1]}{number:0{int(header[5])}d}'
            candidates = [path.parent/(stem+ext) for ext in cls.EXTENSIONS]
            existing = [p for p in candidates if p.exists()]
            if len(existing) > 1:
                raise ValueError(f'Fichier image ambigu : {stem}')
            filename = existing[0] if existing else candidates[0]
            images.append(SequenceImage(number, fields[2] == '1', filename,
                                        extra_fields=fields[3:], _original=line))
        if len(images) != int(header[3]) or len({i.number for i in images}) != len(images):
            raise ValueError('Nombre ou numérotation des images incohérent')
        if sum(i.included for i in images) != int(header[4]):
            raise ValueError('Nombre d’images incluses incohérent')
        groups = {}
        for line in lines:
            if re.match(r'^R[0-9*]\s', line):
                fields = line.split()
                if len(fields) != 17 or fields[7] != 'H':
                    raise ValueError('Transformation Siril invalide')
                values = list(map(float, fields[1:7]))
                if not np.isfinite(values[5]) or not values[5].is_integer():
                    raise ValueError('Nombre d’étoiles invalide')
                h = np.array(fields[8:], dtype=float).reshape(3, 3)
                data = RegistrationData(*values[:5], int(values[5]), h,
                                        _original=line, _snapshot=tuple(values+list(h.ravel())))
                groups.setdefault(fields[0], []).append(data)
            match = re.match(r'^(M[0-9*])-(\d+)\s+(.*)$', line)
            if match:
                index = int(match[2])
                if index >= len(images):
                    raise ValueError('Statistiques hors des indices de séquence')
                images[index].statistics[match[1]] = match[3]
        for layer, rows in groups.items():
            if len(rows) != len(images):
                raise ValueError('Alignements manquants')
            for image, registration in zip(images, rows):
                image.registrations[layer] = registration
        sequence = cls(path, header, images, lines)
        if source_files is not None:
            sequence.bind_sources(source_files)
        return sequence

    @classmethod
    def from_files(cls, path, prefix, files, layers=1):
        images, widths = [], set()
        for filename in sorted(map(Path, files)):
            match = re.fullmatch(re.escape(prefix)+r'(\d+)\.(?:fit|fits|fts)(?:\.fz)?', filename.name)
            if not match:
                raise ValueError(f'Nom incompatible avec le préfixe {prefix} : {filename}')
            widths.add(len(match[1]))
            images.append(SequenceImage(int(match[1]), True, filename))
        if not images or len(widths) != 1 or len({i.number for i in images}) != len(images):
            raise ValueError('Numérotation ambiguë ou séquence vide')
        images.sort(key=lambda i: i.number)
        header = ['S', prefix, str(images[0].number), str(len(images)), str(len(images)),
                  str(widths.pop()), '0', '4', '0']
        return cls(path, header, images, [f"S '{prefix}' "+' '.join(header[2:]), f'L {layers}', 'I'])

    def bind_sources(self, files):
        """Bind source filenames by sequence order, validating the correspondence size."""
        files = list(files)
        if len(files) != len(self.images):
            raise ValueError('Nombre de fichiers sources différent de la séquence')
        for image, path in zip(self.images, files):
            image.source_path = Path(path)

    def duplicate(self, image):
        """Append an independent entry and a FITS symlink for discrete weighting."""
        if not any(item is image for item in self.images):
            raise ValueError('Image étrangère à cette séquence')
        if not image.filename.is_file():
            raise ValueError(f'Image de séquence introuvable : {image.filename}')
        number = max(i.number for i in self.images)+1
        extension = next(ext for ext in sorted(self.EXTENSIONS, key=len, reverse=True) if image.filename.name.endswith(ext))
        filename = self.path.parent/f'{self.name}{number:0{int(self.header[5])}d}{extension}'
        clone = deepcopy(image)
        clone.number, clone.filename, clone._original = number, filename, ''
        filename.symlink_to(image.filename.resolve())  # Refuse to overwrite any existing file.
        self._images.append(clone)
        return clone

    def to_lines(self):
        if not self.images or len({i.number for i in self.images}) != len(self.images):
            raise ValueError('Séquence vide ou numérotation dupliquée')
        if any(i.number < 0 for i in self.images):
            raise ValueError('Numéro d’image négatif')
        if any(i.included not in (False, True) for i in self.images):
            raise ValueError('Inclusion attendue : True ou False')
        if any(c in self.name for c in "'\r\n"):
            raise ValueError('Nom de séquence incompatible avec le format Siril')
        header = list(self.header)
        header[3:5] = [str(len(self.images)), str(sum(i.included for i in self.images))]
        layers = self.registration_layers
        if any(set(i.registrations) != set(layers) for i in self.images):
            raise ValueError('Canaux d’alignement incohérents entre les images')
        output, written = [], set()
        for line in self._lines:
            key = line.split()[0] if line.strip() else ''
            if key == 'S':
                output.append(line if shlex.split(line) == header else f"S '{self.name}' "+' '.join(header[2:]))
            elif key == 'I':
                if key not in written:
                    output.extend(i.to_line() for i in self.images)
                    written.add(key)
            elif re.fullmatch(r'R[0-9*]', key):
                if key not in written:
                    output.extend(i.registration(key).to_line(key) for i in self.images)
                    written.add(key)
            elif re.fullmatch(r'M[0-9*]-\d+', key):
                # Emit this layer's per-image statistics with correct positional indices.
                layer = key.split('-')[0]
                if layer not in written:
                    output.extend(f'{layer}-{index} {image.statistics[layer]}'
                                  for index, image in enumerate(self.images) if layer in image.statistics)
                    written.add(layer)
            else:
                output.append(line)
        for layer in layers:
            if layer not in written:
                output.extend(i.registration(layer).to_line(layer) for i in self.images)
        for layer in dict.fromkeys(layer for image in self.images for layer in image.statistics):
            if layer not in written:
                output.extend(f'{layer}-{index} {image.statistics[layer]}'
                              for index, image in enumerate(self.images) if layer in image.statistics)
        return output

    def write(self, path=None):
        """Atomic metadata write; writing elsewhere does not copy the FITS files."""
        destination = Path(path) if path is not None else self.path
        lines = self.to_lines()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=destination.parent,
                                             prefix=destination.name+'.', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write('\n'.join(lines)+'\n')
            temporary.replace(destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return destination
