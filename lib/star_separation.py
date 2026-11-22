"""Séparation StarNet V2 via Siril 1.4, en domaine linéaire.

L'interface historique Siril réalise le pré-étirement MTF et sa pseudo-inverse.
Le résidu signé est recalculé après inversion, car le masque Siril peut être
un masque « unscreen » et ne constitue pas nécessairement un résidu additif.
Voir docs/scripts/postProcess/auto-ghs.md.
"""

from __future__ import annotations


import argparse
import configparser
import json
import logging
import os
import re
import time
from pathlib import Path

import numpy as np
from astropy.io import fits
from scipy.ndimage import find_objects, label

from lib.processor import processor
from lib.restoration_quality import normalized_planes
from lib.siril import Siril
from lib.type_defs import JSONReport
from lib.siril_api import SirilCommand, command

DEFAULT_MAX_STAR_DIAMETER = 96.0  # Diamètre apparent maximal, pixels du modèle.
STAR_MEASURE_RADIUS = 96
BRIGHT_CORE_LEVEL = .5
MIN_CORE_PIXELS = 2
MIN_STAR_ROUNDNESS = .75
INITIAL_STRETCH_TARGET = .25
MIN_STRETCH_TARGET = .05
STRETCH_REFINEMENTS = 3
MTF_SHADOW_SIGMA = 2.8  # Noir robuste, même convention que le pré-étirement courant.
MTF_MAD_TO_SIGMA = 1.4826
MTF_PARAMETER_EPSILON = 1e-7  # Inverse numérique stable en float64.
MAX_LINEAR_RECONSTRUCTION_ERROR = 1e-6  # Unités normalisées, marge d'arrondi float32.


def quote_siril(value: Path | str) -> str:
    """Protège un chemin Siril et refuse les caractères d'injection de script."""
    text = str(value)
    if any(character in text for character in ('"', "\n", "\r", "\x00")):
        raise ValueError("Chemin incompatible avec un script Siril")
    return f'"{text}"'


def write_rgb(
    source: Path,
    destination: Path,
    data: np.ndarray,
    *,
    nonlinear: bool = False,
    role: str | None = None,
) -> None:
    """Écrit des plans RGB float32 en conservant extensions et métadonnées.

    Les données restent dans l'orientation native du fichier source. Les cartes
    d'échelle entières sont supprimées explicitement et les bornes recalculées.
    """
    with fits.open(source, memmap=False) as hdul:
        hdul[0].data = np.asarray(data, dtype=np.float32)
        for keyword in ("BZERO", "BSCALE", "BLANK"):
            hdul[0].header.pop(keyword, None)
        hdul[0].header["BUNIT"] = ("1", "Dimensionless normalized intensity")
        hdul[0].header["DATAMIN"] = float(np.min(data))
        hdul[0].header["DATAMAX"] = float(np.max(data))
        hdul[0].header["SPNLIN"] = (nonlinear, "Presentation stretch applied")
        if role is not None:
            hdul[0].header["SPROLE"] = (role, "AutoGHS branch role")
        hdul[0].header.add_history(
            "SirilProcessing AutoGHS: nonlinear rendering"
            if nonlinear
            else "SirilProcessing: linear StarNet separation"
        )
        hdul.writeto(destination, overwrite=False, checksum=True)


def validate_rgb(data: np.ndarray, *, allow_upper_overshoot: bool = False) -> None:
    """Exige trois plans finis non négatifs, normalement bornés à 1.

    Parameters
    ----------
    data : np.ndarray
        Plans RGB de forme (3, hauteur, largeur), sans modification en place.
    allow_upper_overshoot : bool, optional
        Réserver à la sortie StarNet brute avant compensation du résidu signé.
        Autorise uniquement les valeurs au-dessus de 1, sans les écrêter.
        Les entrées scientifiques et les couches corrigées restent strictes.

    Raises
    ------
    ValueError
        Géométrie invalide, données non finies, valeurs négatives ou dépassement
        de 1 sans autorisation explicite.
    """
    if data.ndim != 3 or data.shape[0] != 3 or data.size == 0:
        raise ValueError("AutoGHS nécessite un FITS RGB linéaire à trois plans")
    if not np.isfinite(data).all():
        raise ValueError("AutoGHS refuse les pixels NaN/Inf")
    if data.min() < 0 or (not allow_upper_overshoot and data.max() > 1):
        raise ValueError(
            "AutoGHS nécessite des intensités dans [0, 1], sans écrêtage implicite"
        )


def validate_linear_header(header: fits.Header) -> None:
    """Refuse les indicateurs explicites et historiques Siril de rendu déjà effectué.

    L'absence de ces marqueurs ne démontre pas la linéarité d'un fichier externe.
    Le contrat du pipeline reste nécessaire ; aucune inférence photométrique
    n'est faite à partir de la seule forme de l'histogramme.
    """
    history = header.get("HISTORY", [])
    if isinstance(history, str):
        history = [history]
    rendered = any(
        str(line).startswith(("GHS (", "GHS BP shift", "MTF (")) for line in history
    )
    if header.get("SPNLIN", False) or rendered:
        raise ValueError("AutoGHS refuse une entrée déjà non linéaire/étirée")


class StarNetSeparator(processor):
    """Adaptateur StarNet V2 historique, sans remplacement automatique du moteur.

    Une invocation produit une séparation, conserve le journal Siril et mesure
    le résidu additif signé. Toute erreur externe ou géométrique est fatale.
    """

    config_path_parameters = frozenset({"starnet_path"})

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie les paramètres de séparation."""
        return "starnet"

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare le moteur et la cible optionnelle de sa MTF de pré-étirement."""
        parser.add_argument(
            "--starnet-path",
            type=cls.path_argument,
            default=None,
            help="Exécutable StarNet V2 ; sinon préférences Siril",
        )

        parser.add_argument(
            "--starnet-target-background", type=cls.target_argument, default=None,
            help="Cible médiane de la MTF avant StarNet, dans ]0,0.5[ ; "
                 "auto (défaut) : choix par image, ou Siril si adaptation désactivée",
        )

        parser.add_argument(
            '--starnet-adaptive-stretch', action=argparse.BooleanOptionalAction, default=True,
            help='Adapter le pré-étirement à l’étendue des étoiles brillantes (défaut)',
        )
        parser.add_argument(
            '--starnet-max-star-diameter', type=float, default=DEFAULT_MAX_STAR_DIAMETER,
            help='Diamètre apparent maximal à mi-contraste avant StarNet, en pixels (défaut 96)',
        )

    @staticmethod
    def target_argument(value: str | float | None) -> float | None:
        """Accepte une cible médiane dans ]0,0.5[ ou auto pour reprendre Siril.

        La conversion accepte aussi les nombres/None issus du JSON. Une valeur
        invalide lève ArgumentTypeError, exploitable par argparse et Config.
        """
        if value is None or isinstance(value, str) and value.lower() == 'auto':
            return None
        try:
            target = float(value)
        except (TypeError, ValueError) as error:
            raise argparse.ArgumentTypeError('Cible StarNet : auto ou un nombre dans ]0,0.5[') from error
        if isinstance(value, bool) or not np.isfinite(target) or not 0 < target < .5:
            raise argparse.ArgumentTypeError('Cible StarNet : auto ou un nombre dans ]0,0.5[')
        return target

    @staticmethod
    def _mtf(data: np.ndarray, midtones: float) -> np.ndarray:
        """Évalue la MTF sur [0,1], avec bornes exactes et intermédiaires float64.

        L'appelant valide intensités et paramètre. L'inverse emploie 1-midtones.
        Les cas 0 et 1 sont traités explicitement pour éviter les pertes de
        précision par soustraction près du blanc.
        """
        values = np.asarray(data, dtype=np.float64)
        result = ((midtones-1)*values)/((2*midtones-1)*values-midtones)
        return np.where(values == 0, 0., np.where(values == 1, 1., result))

    @staticmethod
    def prepare_stretch(
        data: np.ndarray, target: float,
    ) -> tuple[np.ndarray, JSONReport]:
        """Prépare une MTF par canal, à cible médiane contrôlée, avant StarNet.

        Parameters
        ----------
        data : np.ndarray
            RGB CHW linéaire, fini dans [0,1], non modifié.
        target : float
            Niveau médian demandé dans ]0,0.5[, par exemple 0,08.

        Returns
        -------
        tuple[np.ndarray, JSONReport]
            RGB étiré float32 et paramètres complets pour l'inversion. Le noir
            vaut max(0, médiane - 2,8 sigma), sigma = 1,4826 MAD. Les valeurs
            sous ce noir sont écrêtées pour l'entrée du réseau et dénombrées ;
            leur inversion ne peut pas récupérer l'information perdue. La
            source scientifique reste intacte. Une médiane dégénérée ou un
            inverse non représentable conserve une MTF identité, avec audit.

        Raises
        ------
        ValueError
            Image ou cible invalide. Aucun calcul ONNX ni appel Siril ici.
        """
        validate_rgb(data)
        if isinstance(target, (bool, np.bool_)) or not np.isfinite(target) or not 0 < target < .5:
            raise ValueError('starnet-target-background doit être dans ]0,0.5[')
        result = np.empty_like(data, dtype=np.float32)
        channels: list[JSONReport] = []
        for index, plane in enumerate(data):
            median = float(np.median(plane))
            mad = float(np.median(np.abs(plane-median)))
            shadow = max(0., median-MTF_SHADOW_SIGMA*MTF_MAD_TO_SIGMA*mad)
            fallback = None
            if not 0 < median < 1:
                shadow, midtones, fallback = 0., .5, 'degenerate median'
            else:
                if shadow >= median:
                    shadow = 0.
                scaled = (median-shadow)/(1-shadow)
                midtones = float(StarNetSeparator._mtf(np.asarray(scaled), target))
                if not MTF_PARAMETER_EPSILON <= midtones <= 1-MTF_PARAMETER_EPSILON:
                    shadow, midtones, fallback = 0., .5, 'unrepresentable inverse'
            normalized = np.clip((plane.astype(np.float64)-shadow)/(1-shadow), 0, 1)
            result[index] = StarNetSeparator._mtf(normalized, midtones)
            channels.append(dict(
                channel=index, shadows=shadow, midtones=midtones, highlights=1.,
                median=median, mad=mad, black_clipped_count=int(np.count_nonzero(plane < shadow)),
                prepared_median=float(np.median(result[index])), fallback=fallback,
            ))
        return result, dict(method='controlled_per_channel_mtf', target_background=float(target),
                            shadow_sigma=MTF_SHADOW_SIGMA, channels=channels)

    @staticmethod
    def _star_footprints(
        prepared: np.ndarray, points: list[tuple[int, int]],
    ) -> list[JSONReport]:
        """Mesure les composantes à mi-contraste autour de centres fixes.

        prepared est le maximum RGB 2D du pré-étirement. Le fond est la
        médiane d'un anneau à 80–100 % du rayon de 96 pixels. Un contour
        touchant le bord ou une étoile trop proche du bord reste non mesurable.
        Les retours contiennent diamètre équivalent et rapport des axes des
        moments de la composante ; aucune extrapolation n'est faite.
        """
        reports = []
        radius = STAR_MEASURE_RADIUS
        yy, xx = np.indices((2*radius+1, 2*radius+1))
        outer = np.hypot(yy-radius, xx-radius) > .8*radius
        for y, x in points:
            report: JSONReport = dict(y=y, x=x, measurable=False)
            reports.append(report)
            if min(y, x, prepared.shape[0]-1-y, prepared.shape[1]-1-x) < radius:
                continue
            patch = prepared[y-radius:y+radius+1, x-radius:x+radius+1]
            floor = float(np.median(patch[outer]))
            peak = float(patch[radius, radius])
            if peak <= floor:
                continue
            components, _ = label(patch >= floor+.5*(peak-floor))
            center = components[radius, radius]
            if not center:
                continue
            mask = components == center
            if any(np.any(edge) for edge in (mask[0], mask[-1], mask[:, 0], mask[:, -1])):
                continue
            cy, cx = np.nonzero(mask)
            if len(cy) < MIN_CORE_PIXELS:
                continue
            values = np.linalg.eigvalsh(np.cov(np.array([cy, cx])))
            roundness = float(np.sqrt(max(0., values[0])/values[1])) if values[1] > 0 else 0.
            report.update(measurable=True, diameter_px=float(2*np.sqrt(len(cy)/np.pi)),
                          roundness=roundness, background=floor)
        return reports

    @staticmethod
    def adaptive_stretch(
        data: np.ndarray, diameter_limit: float = DEFAULT_MAX_STAR_DIAMETER,
    ) -> tuple[np.ndarray, JSONReport | None, JSONReport]:
        """Adapte la MTF à l’étendue apparente des étoiles brillantes.

        Parameters
        ----------
        data : np.ndarray
            RGB CHW linéaire dans [0,1], non modifié ; aucun nom de cible utilisé.
        diameter_limit : float
            Diamètre équivalent à mi-contraste maximal, en pixels, dans ]0,192[.

        Returns
        -------
        tuple[np.ndarray, JSONReport or None, JSONReport]
            Entrée de StarNet, MTF éventuelle et audit de décision. Des noyaux
            connexes au-dessus de 0,5 (au moins deux pixels) repèrent les sources
            brillantes. Les contours complets dont le rapport des axes est au
            moins 0,75 fixent les centres examinés. Si leur diamètre dépasse
            la limite à cible 0,25, les cibles sont divisées par deux, jusqu'à
            0,05, puis trois bissections raffinent une cible admissible parmi les essais.
            Les mêmes centres sont mesurés à chaque essai. Si la limite reste
            inaccessible, le meilleur essai mesurable est consigné comme tel.
            Sans source exploitable ou sans dépassement, None conserve exactement
            le pré-étirement automatique Siril. Un seul appel à StarNet suit.

        Raises
        ------
        ValueError
            Image ou limite invalide. Ce critère géométrique est une heuristique,
            pas un certificat d'absence de grilles ni une classification stellaire.
        """
        validate_rgb(data)
        if (isinstance(diameter_limit, (bool, np.bool_))
                or not np.isfinite(diameter_limit)
                or not 0 < diameter_limit < 2*STAR_MEASURE_RADIUS):
            raise ValueError('starnet-max-star-diameter doit être dans ]0,192[')
        diameter_limit = float(diameter_limit)
        light = data.max(axis=0)
        components, _ = label(light >= BRIGHT_CORE_LEVEL, structure=np.ones((3, 3)))
        points = []
        for index, box in enumerate(find_objects(components), start=1):
            if box is None:
                continue
            cy, cx = np.nonzero(components[box] == index)
            if len(cy) >= MIN_CORE_PIXELS:
                points.append((int(round(float(cy.mean())+box[0].start)),
                               int(round(float(cx.mean())+box[1].start))))
        audit: JSONReport = dict(
            enabled=True, method='bright_source_apparent_diameter', diameter_limit_px=diameter_limit,
            core_level=BRIGHT_CORE_LEVEL, minimum_roundness=MIN_STAR_ROUNDNESS,
            measure_radius_px=STAR_MEASURE_RADIUS, initial_target=INITIAL_STRETCH_TARGET,
            minimum_target=MIN_STRETCH_TARGET, bright_source_count=len(points),
            trials=[], selected_target=None, adapted=False, limit_met=None,
            reason='no measurable bright sources',
        )
        if not points:
            return data, None, audit
        initial, initial_parameters = StarNetSeparator.prepare_stretch(data, INITIAL_STRETCH_TARGET)
        measured = StarNetSeparator._star_footprints(initial.max(axis=0), points)
        audit['candidate_measurements'] = measured
        measured = [item for item in measured if item['measurable']
                    and item['roundness'] >= MIN_STAR_ROUNDNESS]
        audit['sources'] = measured
        audit['measured_source_count'] = len(measured)
        if not measured:
            return data, None, audit
        points = [(item['y'], item['x']) for item in measured]
        maximum = max(item['diameter_px'] for item in measured)
        audit['initial_maximum_diameter_px'] = maximum
        audit['trials'].append(dict(target=INITIAL_STRETCH_TARGET, maximum_diameter_px=maximum,
                                    measurable=True, limit_met=maximum <= diameter_limit))
        if maximum <= diameter_limit:
            audit.update(reason='apparent sizes already admissible', limit_met=True)
            return data, None, audit
        best_image, best_parameters, best_size = initial, initial_parameters, maximum
        failed_target = INITIAL_STRETCH_TARGET
        passed_target = None

        def evaluate(target: float) -> tuple[np.ndarray, JSONReport, float | None]:
            """Mesure un essai aux centres fixes, sans masquer les contours devenus ambigus."""
            image, parameters = StarNetSeparator.prepare_stretch(data, target)
            sizes = StarNetSeparator._star_footprints(image.max(axis=0), points)
            size = (max(item['diameter_px'] for item in sizes)
                    if all(item['measurable'] for item in sizes) else None)
            audit['trials'].append(dict(target=target, maximum_diameter_px=size,
                                        measurable=size is not None,
                                        limit_met=size is not None and size <= diameter_limit))
            return image, parameters, size

        target = INITIAL_STRETCH_TARGET
        while target > MIN_STRETCH_TARGET:
            target = max(MIN_STRETCH_TARGET, target/2)
            image, parameters, size = evaluate(target)
            if size is not None and size < best_size:
                best_image, best_parameters, best_size = image, parameters, size
            if size is not None and size <= diameter_limit:
                passed_target = target
                best_image, best_parameters, best_size = image, parameters, size
                break
            failed_target = target
        if passed_target is not None:
            for _ in range(STRETCH_REFINEMENTS):
                target = (passed_target+failed_target)/2
                image, parameters, size = evaluate(target)
                if size is not None and size <= diameter_limit:
                    passed_target = target
                    best_image, best_parameters, best_size = image, parameters, size
                else:
                    failed_target = target
        if best_size >= maximum:
            audit.update(reason='no measurable size improvement', limit_met=False)
            return data, None, audit
        audit.update(adapted=True, selected_target=best_parameters['target_background'],
                     selected_maximum_diameter_px=best_size, limit_met=best_size <= diameter_limit,
                     reason='apparent size controlled' if best_size <= diameter_limit
                     else 'minimum target reached; apparent size remains above limit')
        return best_image, best_parameters, audit

    @staticmethod
    def invert_stretch(data: np.ndarray, parameters: JSONReport) -> np.ndarray:
        """Inverse la MTF enregistrée après l'échange TIFF16 avec StarNet.

        Parameters
        ----------
        data : np.ndarray
            Sortie StarNet RGB CHW, finie dans [0,1].
        parameters : JSONReport
            Trois jeux shadows/midtones/highlights issus de prepare_stretch.

        Returns
        -------
        np.ndarray
            RGB linéaire float32. Les zones écrêtées en entrée reviennent au
            noir enregistré ; ce n'est pas une restitution de leurs pixels.
            La compensation du résidu signé préserve ensuite la somme source.

        Raises
        ------
        ValueError
            Domaine ou paramètres invalides ; aucun écrêtage implicite en sortie.
        """
        validate_rgb(data)
        channels = parameters.get('channels', [])
        if len(channels) != 3:
            raise ValueError('Paramètres MTF StarNet incomplets')
        result = np.empty_like(data, dtype=np.float32)
        for index, settings in enumerate(channels):
            low, mid, high = (settings.get(key, np.nan)
                              for key in ('shadows', 'midtones', 'highlights'))
            if (settings.get('channel') != index or not np.isfinite([low, mid, high]).all()
                    or not 0 <= low < high <= 1
                    or not MTF_PARAMETER_EPSILON <= mid <= 1-MTF_PARAMETER_EPSILON):
                raise ValueError('Paramètres MTF StarNet invalides')
            result[index] = low+(high-low)*StarNetSeparator._mtf(data[index], 1-mid)
        validate_rgb(result)
        return result

    @staticmethod
    def path_argument(value: str) -> Path:
        """Normalise aussi les chemins avec tilde avant leur sauvegarde JSON."""
        return Path(value).expanduser().resolve()

    @staticmethod
    def preferences(
        args: argparse.Namespace,
    ) -> tuple[configparser.ConfigParser, Path | None]:
        """Lit les préférences du mode Siril choisi sans les modifier."""
        config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        if getattr(args, "siril_mode", "flatpak") == "flatpak":
            directory = Path.home() / ".var/app/org.siril.Siril/config/siril"
        else:
            directory = config_home / "siril"
        for name in ("config.1.4.ini", "config.ini"):
            path = directory / name
            if path.is_file():
                preferences = configparser.ConfigParser(interpolation=None)
                preferences.optionxform = str
                preferences.read(path, encoding="utf-8")
                return preferences, path
        return configparser.ConfigParser(interpolation=None), None

    @classmethod
    def resolve_executable(cls, args: argparse.Namespace) -> Path:
        """Résout CLI/JSON puis préférences Siril et vérifie le droit d'exécution.

        Le point d'entrée mémorise ce résultat avec -S, y compris lorsqu'il
        provient d'une découverte dans les préférences Siril.
        """
        preferences, _ = cls.preferences(args)
        configured = getattr(args, "starnet_path", None)
        executable = configured or preferences.get("core", "starnet_exe", fallback="")
        if not executable:
            raise ValueError("StarNet introuvable : renseigner --starnet-path")
        executable = Path(executable).expanduser().resolve()
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError(
                f"Exécutable StarNet absent ou non exécutable : {executable}"
            )
        quote_siril(executable)
        return executable

    def post_process(
        self,
        input_path: Path,
        output_path: Path,
        args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Sépare un RGB linéaire dans le sous-dossier visible du rapport.

        Parameters
        ----------
        input_path : Path
            FITS original, en unités Siril (entiers normalisés ou flottants 0..1).
        output_path : Path
            Rapport JSON dans un dossier de travail neuf appartenant à l'appelant.
        args : argparse.Namespace, optional
            Chemin StarNet, service Siril et cible MTF optionnelle résolus par
            Config. Sans cible explicite, l’adaptation mesure les sources
            brillantes et conserve Siril si aucune correction n’est nécessaire.
            Avec une cible choisie, la MTF est préparée puis inversée explicitement et
            ses paramètres sont enregistrés pour audit.

        Returns
        -------
        dict
            Chemins starless_linear/stars_linear, provenance et diagnostics.

        Raises
        ------
        ValueError
            Entrée non linéaire déclarée, CFA, pixels invalides ou chemin invalide.
        RuntimeError
            Échec Siril/StarNet ou sortie incompatible.
        """
        args = self._get_args(args)
        source, report = Path(input_path).resolve(), Path(output_path).resolve()
        if source == report:
            raise ValueError("Le rapport ne peut pas remplacer la source")
        header = fits.getheader(source)
        validate_linear_header(header)
        if header.get("BAYERPAT"):
            raise ValueError("StarNet exige une image RGB linéaire dématricée")
        if "starless" in str(header.get("FILTER", "")).lower() or header.get(
            "SPROLE"
        ) in ("STARLESS", "STARS"):
            raise ValueError("Entrée déclarée starless : séparation refusée")
        data = normalized_planes(source)
        validate_rgb(data)
        target = getattr(args, 'starnet_target_background', None)
        if target is not None:
            prepared, stretch_parameters = self.prepare_stretch(data, target)
            adaptation = dict(enabled=False, adapted=False, reason='explicit target')
        elif getattr(args, 'starnet_adaptive_stretch', True):
            prepared, stretch_parameters, adaptation = self.adaptive_stretch(
                data, getattr(args, 'starnet_max_star_diameter', DEFAULT_MAX_STAR_DIAMETER),
            )
        else:
            prepared, stretch_parameters = data, None
            adaptation = dict(enabled=False, adapted=False, reason='adaptive stretch disabled')
        if adaptation['adapted']:
            logging.info(
                'StarNet — pré-étirement adapté : diamètre apparent %.1f → %.1f px ; cible %.6g',
                adaptation['initial_maximum_diameter_px'],
                adaptation['selected_maximum_diameter_px'], adaptation['selected_target'],
            )
            if not adaptation['limit_met']:
                logging.warning(
                    'StarNet — limite d’étendue non atteinte : %.1f > %.1f px',
                    adaptation['selected_maximum_diameter_px'], adaptation['diameter_limit_px'],
                )
        preferences, preference_path = self.preferences(args)
        executable = self.resolve_executable(args)
        report.parent.mkdir(parents=True, exist_ok=True)
        work = report.parent / f"{report.stem}_work"
        work.mkdir(exist_ok=False)
        if not preferences.has_section("core"):
            preferences.add_section("core")
        preferences.set("core", "starnet_exe", str(executable))
        preferences.set("core", "force_16bit", "false")
        # V2 utilise son modèle embarqué dans son dossier, pas un chemin V1 résiduel.
        preferences.set("core", "starnet_weights", "")
        isolated = work / "siril.ini"
        with isolated.open("w", encoding="utf-8") as stream:
            preferences.write(stream, space_around_delimiters=False)
        staged = work / "input.fits"
        starless = work / "starless_linear.fits"
        stars = work / "stars_linear.fits"
        write_rgb(source, staged, prepared, nonlinear=stretch_parameters is not None)
        operations = [command('cd', work), command('set32bits'), command('load', staged),
                      (command('starnet') if stretch_parameters is not None
                       else command('starnet', '-stretch')), command('save', starless), command('close')]
        siril = Siril(
            siril_path=getattr(args, "siril_path", None),
            siril_mode=getattr(args, "siril_mode", None),
        )
        started = time.monotonic()
        if not siril.run_commands(
            operations, str(work), name="separate", initfile=isolated
        ):
            raise RuntimeError(
                f"Séparation StarNet échouée ; consulter {work / 'separate.log'}"
            )
        if not starless.is_file():
            raise RuntimeError("StarNet n’a pas produit de starless linéaire")
        result = normalized_planes(starless)
        result_header = fits.getheader(starless)
        if result.shape != data.shape:
            raise RuntimeError("StarNet a modifié les dimensions RGB")
        if result_header.get("ROWORDER", "BOTTOM-UP") != header.get(
            "ROWORDER", "BOTTOM-UP"
        ):
            result = result[:, ::-1, :].copy()
        if stretch_parameters is not None:
            result = self.invert_stretch(result, stretch_parameters)
        # La pseudo-inverse MTF peut dépasser 1 : conserver le résultat brut
        # et son résidu signé jusqu'à la compensation qui conserve leur somme.
        validate_rgb(result, allow_upper_overshoot=True)
        above_one_count = int(np.count_nonzero(result > 1))
        starless_maximum = float(result.max())
        if above_one_count:
            logging.warning(
                "StarNet : %d valeur(s) RGB du fond au-dessus de 1, maximum=%.9g ; "
                "conservées avec le résidu signé pour compensation avant AutoGHS",
                above_one_count, starless_maximum,
            )
        residual = data - result
        reconstruction = data.astype(np.float64) - (
            result.astype(np.float64) + residual.astype(np.float64)
        )
        reconstruction_error = float(np.max(abs(reconstruction)))
        if reconstruction_error > MAX_LINEAR_RECONSTRUCTION_ERROR:
            raise ValueError(
                "La séparation StarNet ne conserve pas la somme linéaire en float32 : "
                f"écart {reconstruction_error:.9g} > {MAX_LINEAR_RECONSTRUCTION_ERROR:g}"
            )
        del reconstruction
        # Réécrire sur le modèle original garantit WCS, extensions et orientation.
        starless.unlink()
        write_rgb(source, starless, result, role="STARLESS")
        write_rgb(source, stars, residual, role="STARS")
        log = work / "separate.log"
        text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        version = re.search(r"siril\s+(\d+\.\d+(?:\.\d+)?)", text, re.IGNORECASE)
        readme = executable.parent / "README.txt"
        banner = readme.read_text(errors="replace")[:256] if readme.is_file() else ""
        starnet_version = re.search(r"StarNet\+\+\s+v([\d.]+)", banner)
        report_data = {
            "status": "SUCCESS",
            "image_path": str(source),
            "output_image": str(starless),
            "starless_linear": str(starless),
            "stars_linear": str(stars),
            "executable": str(executable),
            "siril_preferences": str(preference_path) if preference_path else None,
            "siril_version": version.group(1) if version else None,
            "starnet_version": starnet_version.group(1) if starnet_version else None,
            "method": "siril-1.4-legacy-starnet-stretch",
            "prestretch": ("controlled per-channel MTF + inverse; TIFF16 quantization"
                           if stretch_parameters is not None else
                           "Siril unlinked MTF + pseudoinverse; TIFF16 quantization"),
            "prestretch_parameters": stretch_parameters,
            "stretch_adaptation": adaptation,
            "mask": "signed linear subtraction after inverse MTF",
            "raw_starless_above_one_count": above_one_count,
            "raw_starless_maximum": starless_maximum,
            "negative_fraction": float(np.mean(residual < 0)),
            "negative_minimum": float(residual.min()),
            "reconstruction_max_abs": reconstruction_error,
            "duration_seconds": time.monotonic() - started,
            "command_path": str(work / "separate.commands.json"),
            "log_path": str(log),
            "input_linear": True,
            "linearity_source": "pipeline contract + FITS flags",
            "shape": list(data.shape),
        }
        report.write_text(json.dumps(report_data, indent=2), encoding="utf-8")
        return report_data
