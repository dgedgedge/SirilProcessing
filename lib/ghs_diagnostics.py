"""Export des histogrammes indépendants du fond et des étoiles en ordonnée log.

La série utilise les effectifs CPU et, en option, des instantanés JPEG sur
disque capturés pendant le calcul. Aucun tableau candidat n'est retenu sur
GPU. Voir docs/scripts/postProcess/ghs-histogram-shape.md.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import textwrap
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from lib.diagnostic_style import DIAGNOSTIC_COLORS, apply_dark_diagnostic_style
from lib.processor import processor
from lib.type_defs import JSONReport

if TYPE_CHECKING:
    from lib.auto_ghs import ComputeBackend


class HistogramDiagnostics(processor):
    """Publie une série PNG, son manifeste JSON et une galerie HTML locale."""

    def __init__(
        self, backend: ComputeBackend | None = None, stars: np.ndarray | None = None,
        headroom: float = .95, row_order: str = 'BOTTOM-UP',
    ) -> None:
        """Ouvre un cache JPEG temporaire seulement lorsqu'un backend est fourni.

        Parameters
        ----------
        backend : ComputeBackend or None
            Backend partagé, y compris sa reprise CPU ; None désactive le cache.
        stars : numpy.ndarray or None
            Étoiles fixes déjà rendues ; ajoute un aperçu recomposé si fourni.
        headroom : float
            Fraction du fond dans la fusion, dans ]0,1[.
        row_order : str
            Carte FITS BOTTOM-UP ou TOP-DOWN, validée. Les captures suivent
            toujours l'affichage natif Siril, première ligne FITS en bas.

        Notes
        -----
        close libère le cache ; les seules données retenues sont les chemins
        JPEG et une référence aux étoiles, aucune image candidate.
        """
        if not np.isfinite(headroom) or not 0 < headroom < 1:
            raise ValueError('La réserve de fusion doit être dans ]0,1[')
        if row_order not in ('BOTTOM-UP', 'TOP-DOWN'):
            raise ValueError(f'ROWORDER inconnu : {row_order}')
        self._backend, self._stars = backend, stars
        self._headroom, self._row_order = headroom, row_order
        self._cache = (tempfile.TemporaryDirectory(prefix='autoghs-trial-images-')
                       if backend is not None else None)
        self._images: dict[str, dict[str, Path]] = {}
        self._stellar_trials: list[JSONReport] = []
        self.source_domain: tuple[float, float] | None = None

    def set_source(self, data: np.ndarray) -> None:
        """Conserve uniquement le min/max RGB de l'entrée linéaire de cette branche."""
        from lib.star_separation import validate_rgb

        validate_rgb(data)
        self.source_domain = float(data.min()), float(data.max())

    def record_stellar_trial(
        self, data: np.ndarray, stage: str, status: str,
        parameters: JSONReport | None = None,
        transform_operations: list[JSONReport] | None = None,
    ) -> None:
        """Mesure un essai sur les étoiles seules et capture son JPEG si activé.

        data est un RGB normalisé de forme (3, hauteur, largeur). stage nomme
        la courbe, status son verdict et parameters ses réglages sans unité.
        Seuls les histogrammes et chemins sont conservés ; aucun tableau RGB
        n'est retenu. Les mesures ne participent pas au choix du rendu.
        """
        from lib.auto_ghs import ComputeBackend, HistogramAnalyzer

        if self.source_domain is None:
            self.set_source(data)
        record = dict(stage=stage, status=status, parameters=parameters or {},
                      histogram=HistogramAnalyzer().analyze(data, ComputeBackend('cpu')).report())
        if transform_operations is not None:
            record['transform_operations'] = transform_operations
        if status == 'rejected':
            record['rejections'] = ['stellar width limit']
        if self._cache is not None:
            self.capture(record, data)
        self._stellar_trials.append(record)

    def write_stellar_series(self, destination: Path) -> JSONReport:
        """Publie les essais stellaires indépendants, avec leur résultat retenu.

        Une série vide est refusée. Les JPEG facultatifs doivent avoir été
        capturés pendant le rendu, avant la fermeture du cache.
        """
        if not self._stellar_trials:
            raise ValueError('Aucun essai stellaire enregistré')
        return self.write_series(
            self.stellar_histogram_report(),
            destination, self if self._cache is not None else None,
        )

    def stellar_histogram_report(self) -> JSONReport:
        """Retourne les histogrammes stellaires enregistrés, sans image du fond."""
        if not self._stellar_trials:
            raise ValueError('Aucun essai stellaire enregistré')
        return dict(histograms=[item['histogram'] for item in self._stellar_trials],
                    histogram_stages=self._stellar_trials, diagnostic_subject='stars',
                    transfer_source_domain=list(self.source_domain) if self.source_domain is not None else None,
                    trial_image_id=self._stellar_trials[-1].get('trial_image_id'))

    def close(self) -> None:
        """Retire les JPEG temporaires, après leur publication ou après échec."""
        if self._cache is not None:
            self._cache.cleanup()
            self._cache = None
        self._images.clear()
        self._stellar_trials.clear()
        self._stars = None
        self.source_domain = None

    def capture(
        self, record: JSONReport, data: np.ndarray, *, with_stars: bool = False,
    ) -> None:
        """Capture les pixels réels d'un essai, sans changer son classement.

        record reçoit un identifiant de cache ; les pixels ne sont pas stockés
        dans le rapport. data est l'image entière, sur CPU ou sur le backend.
        with_stars signale une image déjà recomposée et empêche une seconde
        fusion. Un histogramme estimé reste étiqueté comme tel dans la galerie,
        même lorsque ses paramètres sont appliqués à tous les pixels pour ce JPEG.
        """
        from lib.render_exports import write_array_jpeg

        if self._cache is None or self._backend is None:
            raise RuntimeError('Le cache des images AutoGHS est fermé ou absent')
        image = data if isinstance(data, np.ndarray) else self._backend.numpy(data)
        identifier = f'{len(self._images)+1:06d}'
        base = Path(self._cache.name)/identifier
        paths = {'image_jpeg': base.with_suffix('.jpg')}
        write_array_jpeg(image, paths['image_jpeg'], self._row_order)
        if self._stars is not None and not with_stars:
            if self._stars.shape != image.shape:
                raise ValueError('Étoiles et essai de géométries différentes')
            composed = self._headroom*image+self._stars*(1-self._headroom*image)
            paths['with_stars_jpeg'] = base.with_name(
                base.name+'_with_stars',
            ).with_suffix('.jpg')
            write_array_jpeg(composed, paths['with_stars_jpeg'], self._row_order)
        self._images[identifier] = paths
        record['trial_image_id'] = identifier

    def publish(self, identifier: str, destination: Path) -> dict[str, str]:
        """Copie un instantané sous le numéro de son histogramme, sans réencodage.

        destination est le chemin image_NNN.jpg ; la variante recomposée
        porte le suffixe _with_stars. Un identifiant absent est une erreur.
        """
        result = {}
        for kind, source in self._images[identifier].items():
            target = (destination if kind == 'image_jpeg' else
                      destination.with_name(destination.stem+'_with_stars.jpg'))
            shutil.copyfile(source, target)
            result[kind] = str(target)
        return result

    @classmethod
    def get_prefix(cls) -> str:
        """Identifie l'export indépendant des mesures enregistrées."""
        return 'ghs_histogram_diagnostics'

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare la sauvegarde facultative des images des essais majeurs."""
        parser.add_argument(
            '--ghs-save-trial-jpegs', action=argparse.BooleanOptionalAction,
            default=False,
            help='Sauvegarder les JPEG des essais des histogrammes (défaut désactivé)',
        )

    @staticmethod
    def write_series(
        report: JSONReport, destination: Path,
        trial_images: HistogramDiagnostics | None = None,
    ) -> JSONReport:
        """Écrit les étapes, gagnants et meilleurs rejets complets en log.

        Parameters
        ----------
        report : JSONReport
            Branche RANGE avec histogram_stages, présentation et recherches,
            ou essais stellaires avec diagnostic_subject='stars'.
        destination : pathlib.Path
            Dossier réservé à la série ; les sources et les FITS sont ignorés.
        trial_images : HistogramDiagnostics or None
            Cache encore ouvert des captures faites pendant le calcul. None
            exporte seulement les graphiques ; un ancien rapport ne suffit
            pas à reconstruire les pixels de ses essais.

        Returns
        -------
        JSONReport
            Liste histogram_series et chemins de la galerie et du manifeste.
            Chaque figure garde la même échelle Y, masque les classes nulles
            et compare le candidat à son entrée de phase. Les essais rejetés
            sont explicitement identifiés et ne deviennent pas des sorties.
            En recherche par branches, tous les enfants complets admissibles
            sont visibles, avec parent, continuation et appartenance au chemin
            finalement retenu ; un enfant alternatif reste « admissible ».
            Si le domaine source est enregistré, ajoute la transformation
            cumulée neutre et exporte sa recette RGB et ses points float64.
            Les encarts identifient leur série et leur numéro. Les résultats
            finaux référencent l'essai choisi par son numéro dans cette série,
            sans comparer des histogrammes pour deviner leur provenance.
        """
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        states = report.get('histograms', [])
        if not states:
            raise ValueError('Aucun histogramme enregistré pour la série AutoGHS')
        first = states[0]
        from lib.stretch_transfer import CumulativeStretchTransform, TrialTransformResolver

        domain = report.get('transfer_source_domain')
        resolver = TrialTransformResolver(report) if domain is not None else None
        subject = report.get('diagnostic_subject', 'starless')
        trials = [dict(item, reference=first, series_label='Étapes du traitement')
                  for item in report.get('histogram_stages', [])]
        balance_reference = next((item['histogram'] for item in reversed(
            report.get('histogram_stages', [])) if item['stage'].startswith('RANGE')), first)
        balance_reference = report.get('histogram_balance', {}).get('before_histogram', balance_reference)
        balance_trials = []
        for index, item in enumerate(report.get('histogram_balance', {}).get('full_validation', []), 1):
            balance_trials.append(dict(item, stage=f'Égalisation — essai {index}',
                                       series_label='Égalisation',
                                       reference=balance_reference,
                                       status='rejected' if item.get('rejections') else 'accepted'))
        position = next((index for index, trial in enumerate(trials)
                         if not (trial['stage'].startswith('RANGE')
                                 or trial['stage'] == 'Entrée linéaire')), len(trials))
        trials[position:position] = balance_trials
        phase_entry_trial = trials[-1] if trials else None
        entry = report.get('shape_entry_histogram', first)
        for index, item in enumerate(report.get('presentation_stretch', {}).get('full_validation', []), 1):
            if item.get('histogram'):
                trials.append(dict(item, stage=f'Présentation — finaliste {index}',
                                   series_label='Présentation protégée',
                                   reference=report.get('presentation_entry_histogram', first),
                                   status='rejected' if item.get('rejections') else 'admissible'))
        quartiles = report.get('quartile_stretch', {})
        searches = quartiles.get('searches', {})
        selected_name = quartiles.get('selected_search')
        if selected_name is None and len(searches) == 1:
            selected_name = next(iter(searches))
        selected_report = searches.get(selected_name, {}).get('report', quartiles)
        selected_branch = selected_report.get('selected_branch_id')
        source_trial = phase_entry_trial if selected_name == 'unchanged' or selected_branch == 'root' else None
        if subject == 'stars':
            source_trial = next((item for item in reversed(trials)
                                 if item['status'] == 'selected'), None)
        series_labels = {'shape': 'Profil par morceaux', 'length': 'Longueur rectiligne',
                         'reference_level': 'Niveau à 0,8', 'population': 'Populations rares'}
        for name, search in searches.items():
            for iteration in search.get('report', {}).get('iterations', []):
                accepted = iteration.get('accepted')
                rejected = iteration.get('major_rejected')
                verified = iteration.get('verified_candidates')
                winners = ([(item, 'accepted' if item.get('on_selected_path') else 'admissible')
                            for item in verified] if verified is not None else [(accepted, 'accepted')])
                for item, verdict in [*winners, (rejected, 'rejected'),
                                      (iteration.get('major_estimated_rejected'), 'rejected')]:
                    if item and item.get('histogram'):
                        label = f"{name} — passe {iteration['iteration']}"
                        if item.get('branch_id'):
                            label += f" — branche {item['branch_id']}"
                        trial = dict(item, stage=label, series_label=series_labels.get(name, name),
                                           search_name=name, iteration=iteration['iteration'],
                                           parent_branch_id=item.get('parent_branch_id', iteration.get('parent_branch_id')),
                                           profile=item.get('full_profile', item.get('profile', {})),
                                           status=verdict, reference=entry)
                        trials.append(trial)
                        if name == selected_name and verdict != 'rejected':
                            if selected_branch is not None:
                                if trial.get('branch_id') == selected_branch:
                                    source_trial = trial
                            elif verdict == 'accepted':
                                source_trial = trial
        selected_index = next((index for index, trial in enumerate(trials, 1)
                               if trial is source_trial), None)
        selection = dict(selected_trial_index=selected_index,
                         selected_trial_stage=source_trial['stage'] if source_trial is not None else None)
        trials.append(dict(stage='Étoiles retenues' if subject == 'stars' else 'Fond retenu', series_label='Résultat retenu',
                           status='selected', histogram=states[-1], reference=entry, **selection,
                           profile=report.get('quartile_stretch', {}).get('after', {}),
                           trial_image_id=report.get('trial_image_id')))
        if report.get('recomposed_histogram'):
            trials.append(dict(stage='Rendu avec étoiles', series_label='Résultat retenu',
                               status='selected', **selection,
                               histogram=report['recomposed_histogram'], reference=entry, with_stars=True,
                               trial_image_id=report.get('recomposed_trial_image_id')))
        maximum = max(max(item['histogram']['counts'], default=0) for item in trials)*1.2
        series, cards = [], []
        for index, trial in enumerate(trials, 1):
            trial.update(series_index=index, series_count=len(trials))
            source_index = trial.get('selected_trial_index')
            if source_index is not None and trial_images is not None:
                trial['selected_trial_jpeg'] = series[source_index-1]['image_jpeg']
            path = destination / f'histogram_{index:03d}.png'
            plotted = dict(histograms=[trial['reference'], trial['histogram']],
                           diagnostic_subject=subject,
                           diagnostic_trial=trial, histogram_ymax=maximum)
            transfer_metadata = {}
            if resolver is not None and not trial.get('with_stars'):
                transform = CumulativeStretchTransform(tuple(domain), resolver.for_trial(trial))
                curve, transfer_metadata = transform.export(destination / f'transform_{index:03d}')
                plotted['cumulative_transfer'] = curve
                plotted['transfer_domain'] = domain
            if trial.get('with_stars'):
                plotted['recomposed_histogram'] = trial['histogram']
            histogram_figure(plotted, path, with_stars=bool(trial.get('with_stars')))
            metadata = {key: trial[key] for key in
                        ('stage', 'status', 'parameters', 'BP', 'post_BP', 'post_blackpoint', 'rejections',
                         'branch_id', 'parent_branch_id', 'continued', 'on_selected_path')
                        if key in trial}
            metadata.update({key: trial[key] for key in (
                'series_label', 'series_index', 'series_count', 'iteration', 'search_name',
                'selected_trial_index', 'selected_trial_stage', 'selected_trial_jpeg',
            ) if key in trial})
            metadata.update(transfer_metadata)
            metadata.update(index=index, path=str(path), yscale='log', xscale='linear',
                            estimated=bool(trial.get('estimated', False)),
                            population='with_stars' if trial.get('with_stars') else subject)
            image_links = ''
            if trial_images is not None:
                identifier = trial.get('trial_image_id')
                if identifier is None:
                    raise ValueError(f"Image AutoGHS absente pour {trial['stage']}")
                metadata.update(trial_images.publish(
                    identifier, destination/f'image_{index:03d}.jpg',
                ))
                metadata['image_histogram_estimated'] = metadata['estimated']
                image_label = ('Rendu avec étoiles' if trial.get('with_stars')
                               else 'Étoiles seules' if subject == 'stars' else 'Image sans étoiles')
                image_links = '<p>'+' — '.join(
                    f'<a href="{Path(metadata[key]).name}">{label}</a>'
                    for key, label in (
                        ('image_jpeg', image_label),
                        ('with_stars_jpeg', 'Aperçu avec étoiles'),
                    ) if key in metadata
                )+'</p>'
                if metadata['estimated']:
                    image_links += '<p>Histogramme estimé ; image calculée sur tout le champ.</p>'
            if transfer_metadata:
                recipe_name = Path(transfer_metadata['transform_recipe']).name
                samples_name = Path(transfer_metadata['transform_samples']).name
                image_links += (f'<p><a href="{recipe_name}">Transformation RGB cumulée (JSON)</a> — '
                                f'<a href="{samples_name}">Courbe float64 (NPZ)</a></p>')
            series.append(metadata)
            caption = html.escape(f"{index:03d} — {trial['stage']} — {trial['status']}")
            if trial.get('selected_trial_index') is not None:
                source_index = trial['selected_trial_index']
                image_links += (f'<p>Essai sélectionné : <a href="histogram_{source_index:03d}.png">'
                                f'{source_index:03d}</a></p>')
                if trial.get('selected_trial_jpeg'):
                    filename = Path(trial['selected_trial_jpeg']).name
                    image_links += f'<p><a href="{filename}">Image de l’essai sélectionné</a></p>'
            cards.append(f'<figure><figcaption>{caption}</figcaption>'
                         f'<a href="{path.name}"><img loading="lazy" src="{path.name}" alt="{caption}"></a>{image_links}</figure>')
        # index.html appartient au générateur commun ProcessingHtmlReport.
        gallery, manifest = destination/'histograms.html', destination/'histograms.json'
        gallery.write_text('<!doctype html><html lang="fr"><meta charset="utf-8">'
                           '<title>AutoGHS — essais d’histogrammes</title>'
                           '<style>:root{color-scheme:dark}'
                           'body{font-family:sans-serif;margin:2em;background:#000;color:#dedede}'
                           'figure{background:#101010;padding:1em;margin:1em 0}img{max-width:100%}'
                           'a{color:#8ac5ff}a:visited{color:#d4a9f7}'
                           'figcaption{font-weight:bold}</style><h1>AutoGHS — essais majeurs</h1>'
                           '<p>Effectifs logarithmiques ; intensité linéaire. '
                           'Les essais rejetés sont présentés à titre de diagnostic.</p>'
                           +''.join(cards)+'</html>', encoding='utf-8')
        manifest.write_text(json.dumps(series, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        obsolete_index = destination/'index.html'
        if (obsolete_index.is_file() and not obsolete_index.is_symlink()
                and '<title>AutoGHS — essais d’histogrammes</title>' in obsolete_index.read_text(encoding='utf-8')):
            obsolete_index.unlink()
        for stale in destination.glob('histogram_*.png'):
            suffix = stale.stem.removeprefix('histogram_')
            if suffix.isdigit() and int(suffix) > len(series):
                stale.unlink()
        kept_jpegs = {item[key] for item in series
                      for key in ('image_jpeg', 'with_stars_jpeg') if key in item}
        for stale in destination.glob('image_*.jpg'):
            suffix = stale.stem.removeprefix('image_').removesuffix('_with_stars')
            if suffix.isdigit() and str(stale) not in kept_jpegs:
                stale.unlink()
        return dict(histogram_series=series, histogram_gallery=str(gallery),
                    histogram_series_manifest=str(manifest),
                    **selection,
                    trial_jpegs_enabled=trial_images is not None)

    def post_process(
        self, input_path: Path, output_path: Path, args: argparse.Namespace | None = None,
    ) -> JSONReport:
        """Exporte les graphiques d'un rapport ; les JPEG exigent les captures du calcul."""
        if getattr(self._get_args(args), 'ghs_save_trial_jpegs', False):
            raise ValueError('Les JPEG des essais doivent être capturés pendant AutoGHS ; '
                             'un histogramme existant ne permet pas de reconstruire ses pixels')
        source = Path(input_path)
        report = json.loads(source.read_text(encoding='utf-8'))
        branch = report.get('strategies', {}).get('range', report)
        return self.write_series(branch, Path(output_path))


def atomic_report(path: Path, report: JSONReport) -> None:
    """Remplace un rapport JSON seulement après une sérialisation complète et finie."""
    temporary = path.with_name(path.name + ".pending")
    try:
        payload = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)
        temporary.write_text(payload + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _peak_shift_annotation(audit: JSONReport, *, estimated: bool = False) -> list[str]:
    """Formate le déplacement absolu du pic depuis la référence de forme.

    Parameters
    ----------
    audit : JSONReport
        Audit de conservation de forme ; peak_shift et maximum_peak_shift
        sont des différences d'intensité sur [0,1]. Une valeur absente ou
        non finie reste non renseignée, sans être remplacée par zéro.
    estimated : bool
        Signale une mesure sur échantillon plutôt qu'un recomptage complet.

    Returns
    -------
    list[str]
        Lignes de l'encart avec la mesure et, s'il est connu, son plafond.
    """
    shift = audit.get('peak_shift')
    label = 'Déplacement du pic' + (' (estimé)' if estimated else '')
    value = f'{shift:.6f}' if shift is not None and np.isfinite(shift) else 'non renseigné'
    lines = [f'{label} : {value}', 'Intensité 0–1 ; référence de forme']
    limit = audit.get('maximum_peak_shift')
    if limit is not None and np.isfinite(limit):
        lines.append(f'Plafond du déplacement : {limit:.6f}')
    return lines


def _slope_ratio_annotation(audit: JSONReport) -> list[str]:
    """Affiche les mesures de la nouvelle politique sans seuil global obsolète."""
    if audit.get('policy') != 'shoulder_and_slope_ratios':
        return []
    geometry = audit.get('candidate_geometry', {})
    position = geometry.get('shoulder_position')
    advance = audit.get('shoulder_advance')
    ratios = geometry.get('ratios', [])
    return [
        'Épaulement : '+(f'{position:.4f}' if position is not None else 'absent'),
        'Avance de l’épaulement : '+(f'{advance:.4f}' if advance is not None else 'non mesurable'),
        'Limite d’avance : '+str(audit.get('maximum_shoulder_advance')),
        textwrap.fill('Ratios après épaulement : '+', '.join(
            f'{value:.3f}' if value is not None else '?' for value in ratios), 38),
        f"Écart isolé des ratios : {audit.get('ratio_break', 0):.3f}",
        f"Poids du raidissement terminal : {audit.get('terminal_weight', .2):.2f}",
    ]


def _render_quality_annotation(profile: JSONReport) -> list[str]:
    """Formate les contributions du score, sans confondre pénalités et rejets.

    Parameters
    ----------
    profile : JSONReport
        Profil après BP ; render_quality peut manquer dans un ancien rapport.

    Returns
    -------
    list[str]
        Score sans unité, conservation relative contraste/bruit en pourcentage
        et pénalités. Une mesure indisponible reste explicitement non renseignée.
    """
    quality = profile.get('render_quality')
    if quality is None:
        return []
    if not quality.get('available'):
        return ['', 'QUALITÉ : non mesurable']
    lines = ['', 'QUALITÉ DU RENDU' + (' (estimée)' if quality.get('estimated') else ''),
             f"Score : {quality['score']:.4f}",
             f"Contraste/bruit conservé : {100*quality['cnr_retention']:.1f} %",
             f"Bonus contraste/bruit : {quality['cnr_reward']:.3f} (plafond 1)",
             f"Bonus visibilité : {quality['visibility_reward']:.3f}",
             f"Bonus niveaux : {quality['light_reward']:.3f}"]
    for key, label in (('cnr_penalty', 'Perte contraste/bruit'),
                       ('sky_compression_penalty', 'Compression du ciel'),
                       ('shape_penalty', 'Déformation'), ('rise_penalty', 'Bosse intermédiaire'),
                       ('initial_peak_penalty', 'Bosse du pic initial'),
                       ('prepeak_valley_penalty', 'Creux avant le pic'),
                       ('bright_penalty', 'Compression brillante')):
        if key in quality:
            lines.append(f"Pénalité {label.lower()} : {quality[key]:.3f}")
    warnings = profile.get('shape_preservation', {}).get('rejections', [])
    if warnings:
        lines.append('Écarts de forme consignés dans l’audit')
    if quality.get('reference_reward') is not None:
        lines.append(f"Bonus niveau à 0,8 : {quality['reference_reward']:.3f}")
    if quality.get('reference_target_log10') is not None:
        lines.append(f"Cible à 0,8 (log10) : {quality['reference_target_log10']:.3f}")
    return lines


def ghs_annotation(report: JSONReport) -> str:
    """Résume l'objectif et les recherches GHS consignées dans le rapport.

    Les recherches comptent les passages ayant évalué des candidats ; un arrêt
    préalable sur distribution constante ou pic ambigu n'ajoute aucun passage.
    Les applications acceptées et les corrections BP sont comptées séparément.
    Les métriques décrivent le fond optimisé avant fusion, même sur le PNG avec
    étoiles. Le GHS final explicite est distingué des recherches sous contraintes.
    Une information absente est affichée comme non renseignée. Le seuil de
    régression à 0,8 est affiché avec sa prédiction en effectifs logarithmiques.
    Les conservations de contraste brillante et du cœur sont distinguées
    lorsque la protection de queue rare est renseignée. Le déplacement absolu
    du pic est exprimé en intensité [0,1] depuis la référence de forme ; les
    estimations sur échantillon et le plafond connu sont indiqués.
    Un rejet de conservation de décroissance explicite son ratio et son seuil
    lorsqu'ils sont enregistrés ; il ne qualifie pas la pente d'horizontale.
    Les figures de série portent leur groupe et leur numéro ; les synthèses
    finales indiquent le numéro d'essai retenu lorsque sa provenance est connue.
    """
    if report.get('diagnostic_subject') == 'stars':
        trial = report.get('diagnostic_trial', {})
        lines = ['Étoiles seules — étirement indépendant',
                 trial.get('stage', 'Asinh et renforcement des faibles intensités')]
        if trial:
            statuses = {'input': 'entrée', 'candidate': 'candidat', 'selected': 'retenu',
                        'rejected': 'rejeté', 'admissible': 'admissible'}
            labels = {'strength': 'Force asinh', 'ceiling': 'Plafond',
                      'faint_boost': 'Renforcement des faibles',
                      'histogram_strength': 'Adaptation des pics'}
            lines.append('Statut : ' + statuses.get(trial['status'], trial['status']))
            lines.extend(f'{labels.get(name, name)} : {value:g}'
                         for name, value in trial.get('parameters', {}).items())
            lines.append(f"Essai {trial['series_index']}/{trial['series_count']}")
        return '\n'.join(lines)
    trial = report.get('diagnostic_trial')
    if trial is not None:
        statuses = {'accepted': 'accepté', 'rejected': 'rejeté', 'selected': 'retenu',
                    'reference': 'référence', 'applied': 'appliqué',
                    'admissible': 'admissible', 'no admissible improvement': 'entrée conservée'}
        lines = [trial['stage'], '', 'Statut : '+statuses.get(trial['status'], trial['status'])]
        if trial.get('series_label'):
            lines = [textwrap.fill('SÉRIE — '+trial['series_label'].upper(), 38),
                     f"IMAGE {trial['series_index']:03d} / {trial['series_count']:03d}", '', *lines]
        if trial.get('parent_branch_id') is not None:
            lines.append(f"Branche parente : {trial['parent_branch_id']}")
        if 'selected_trial_index' in trial:
            index = trial['selected_trial_index']
            lines.extend(['', f'Essai sélectionné : {index:03d}' if index is not None
                          else 'Essai sélectionné : non renseigné'])
            if index is not None:
                lines.append(f'Histogramme : histogram_{index:03d}.png')
                if trial.get('selected_trial_jpeg'):
                    lines.append('Image : '+Path(trial['selected_trial_jpeg']).name)
        if trial.get('estimated'):
            lines.append('Estimation sur échantillon pondéré')
        parameters = trial.get('parameters') or {}
        lines.extend(f'{key} : {value:.5g}' for key, value in parameters.items()
                     if isinstance(value, (int, float)))
        if trial.get('BP') is not None and 'BP' not in parameters:
            lines.append(f"BP : {trial['BP']:.5g}")
        if trial.get('post_BP') is not None:
            lines.append(f"BP après courbe : {trial['post_BP']:.5g}")
        blackpoint = trial.get('post_blackpoint') or {}
        if blackpoint.get('threshold_basis') == 'minimum_initially_positive_rgb_channel':
            lines.append('BP commun RGB ; perte d’un canal comptée par pixel')
        if blackpoint.get('loss_fraction_before') is not None:
            lines.append(f"Perte avant BP : {100*blackpoint['loss_fraction_before']:.5f} %")
        if blackpoint.get('remaining_loss_budget') is not None:
            lines.append(f"Marge avant BP : {100*blackpoint['remaining_loss_budget']:.5f} %")
        if blackpoint.get('budget') is not None:
            lines.append(f"Budget BP total : {100*blackpoint['budget']:.5f} %")
        limit = blackpoint.get('limit_reason')
        if limit:
            lines.append({'below_histogram_resolution': 'BP limité par la résolution du quantile',
                          'below_intensity_resolution': 'BP inférieur à la précision utile du type',
                          'no_safe_positive_shift': 'Aucun décalage positif sûr du BP'}.get(limit, limit))
        profile = trial.get('profile') or trial.get('after', {})
        audit = profile.get('shape_preservation', trial.get('shape_preservation', {}))
        reasons = {'global histogram flattening': 'Conservation de décroissance insuffisante',
                   'local histogram flattening': 'Conservation de pente locale insuffisante',
                   'histogram slope deformation': 'Proportions des pentes trop modifiées',
                   'histogram slope stiffness': 'Évolution des pentes trop hétérogène',
                   'histogram slope sequence': 'Ordre des variations de pente modifié',
                   'histogram shape distortion': 'Forme globale trop modifiée',
                   'histogram peak shoulder distortion': 'Épaulement du pic trop modifié',
                   'histogram peak displacement': 'Déplacement excessif du pic',
                   'unmeasurable render quality': 'Qualité de rendu non mesurable',
                   'no full-resolution render quality improvement':
                       'Pas de gain de qualité sur tout le champ',
                   'unmeasurable histogram shape': 'Forme non mesurable'}
        lines.append('')
        reasons.update({
            'histogram shoulder lost': 'Épaulement disparu',
            'histogram shoulder advance': 'Épaulement trop avancé',
            'histogram slope sign inversion': 'Pente descendante devenue croissante ou nulle',
            'histogram slope ratio inversion': 'Ordre des pentes inversé',
            'histogram slope ratio distortion': 'Rapport de pentes excessivement modifié',
            'histogram slope ratio break': 'Rupture isolée des rapports de pentes',
        })
        for reason in trial.get('rejections', []):
            label = reasons.get(reason, reason)
            fields = {
                'global histogram flattening': ('drop_retention', 'minimum_drop_retention'),
                'local histogram flattening': ('local_slope_retention', 'minimum_local_retention'),
            }.get(reason)
            if fields is not None:
                value, minimum = (audit.get(field) for field in fields)
                if value is not None and minimum is not None:
                    label += f' : {100*value:.1f} % < {100*minimum:.1f} %'
            lines.append(textwrap.fill(label, 38))
        if profile.get('shape'):
            lines.extend(['', f"Morceaux : {len(profile.get('pieces', []))}",
                          f"Décroissance : {profile['shape']['drop_dex']:.3f} dex",
                          'Variations de pente : '+str(profile['shape']['slope_sequence'])])
        if profile.get('peak') is not None:
            lines.append(f"Position du pic : {profile['peak']:.6f}")
        lines.extend(['', *_peak_shift_annotation(audit, estimated=bool(trial.get('estimated')))])
        lines.extend(_slope_ratio_annotation(audit))
        if audit.get('policy') != 'shoulder_and_slope_ratios' and audit.get('drop_retention') is not None:
            lines.append(f"Décroissance conservée : {100*audit['drop_retention']:.1f} %")
        if audit.get('policy') != 'shoulder_and_slope_ratios' and audit.get('local_slope_retention') is not None:
            lines.append(f"Pente locale conservée : {100*audit['local_slope_retention']:.1f} %")
            lines.append(f"Hétérogénéité des pentes : {audit['slope_stiffness']:.3f}")
        peak = profile.get('initial_peak')
        if peak is not None:
            lines.append(f"Remontée près du pic : {100*peak['secondary_rise_fraction']:.1f} % du sommet")
        lines.extend(_render_quality_annotation(profile))
        return '\n'.join(lines)
    lines = ["OBJECTIF", textwrap.fill(
        "Élargir l’étendue robuste des intensités entre les quantiles "
        "configurés, sous contraintes de bruit et d’écrêtage.", width=38
    ), "", "BOUCLE GHS — FOND SANS ÉTOILES"]
    if 'selected_trial_index' in report:
        index = report['selected_trial_index']
        lines.extend(['', f'Essai sélectionné : {index:03d}' if index is not None
                      else 'Essai sélectionné : non renseigné'])
    records = report.get("iterations")
    if records is None:
        lines.append("Itérations : non renseignées")
    else:
        accepted = [item for item in records if item.get("accepted") is not None]
        limit = report.get("parameters", {}).get("ghs_max_iterations", "?")
        blackpoint_records = report.get("blackpoint_iterations", records)
        blackpoint_count = sum(item.get("BP", 0) > 0 for item in blackpoint_records)
        lines.extend([
            f"Recherches effectuées : {len(records)} / {limit}",
            f"GHS appliqués : {len(accepted)}",
            f"Recherches sans application : {len(records) - len(accepted)}",
            "Candidats examinés : "
            f"{sum(len(item.get('candidates', [])) for item in records)}",
            f"Corrections du point noir : {blackpoint_count}",
        ])
    balance = report.get("histogram_balance")
    if balance is not None:
        lines.extend([
            "", "ÉGALISATION PARTIELLE",
            f"Force : {balance['strength']:.2f} — plafond : {balance['clip_limit']:.1f}",
        ])
    dynamic = report.get("dynamic_range")
    if dynamic is not None:
        lines.extend([
            "", "BOUCLE D’EXPANSION RGB",
            f"Passages : {len(dynamic['iterations'])} / {dynamic['max_iterations']}",
            f"Étendue : {dynamic['initial_width']:.3f} → {dynamic['final_width']:.3f}",
            f"Cible : {dynamic['target']:.2f} — "
            + ("atteinte" if dynamic['target_reached'] else "non atteinte"),
        ])
    presentation = report.get("presentation_stretch")
    if presentation is not None:
        lines.extend([
            "", "GHS FINAL — SANS ÉTOILES",
            f"D : {presentation['parameters']['D']:.2f} — "
            + {"applied": "appliqué", "disabled": "désactivé",
               "constant distribution": "distribution constante"}.get(
                presentation["status"], presentation["status"],
            ),
        ])
        if presentation.get("mode") == "auto":
            parameters = presentation["parameters"]
            lines.extend([
                f"B : {parameters['B']:.2f} — SP : {parameters['SP']:.4f}",
                f"HP : {parameters['HP']:.4f}",
            ])
    quartiles = report.get("quartile_stretch", {})
    after = quartiles.get("after", {})
    lines.extend(_render_quality_annotation(after))
    if after.get('peak') is not None:
        lines.append(f"Position du pic : {after['peak']:.6f}")
    lines.extend(['', *_peak_shift_annotation(
        after.get('shape_preservation', {}),
        estimated=bool(after.get('regression_floor', {}).get('estimated')),
    )])
    lines.extend(_slope_ratio_annotation(after.get('shape_preservation', {})))
    longest = quartiles.get('longest_line_after', after)
    if longest.get('measurable'):
        lines.extend([
            "", "PROFIL PAR MORCEAUX" if longest.get('model') == 'piecewise' else "RECTITUDE MESURÉE",
            f"Segment : {longest['line_start']:.3f} → {longest['line_end']:.3f}",
        ])
    elif longest.get('anchored_to_peak'):
        lines.extend(["", "RECTITUDE NON VALIDÉE", "Depuis la sortie du pic principal"])
    if quartiles.get('selected_search'):
        labels = {'length': 'longueur', 'reference_level': 'niveau à 0,8',
                  'population': 'populations rares',
                  'shape': 'forme préservée',
                  'unchanged': 'entrée conservée'}
        lines.append('Recherche : ' + labels.get(quartiles['selected_search'], quartiles['selected_search']))
    tail = after.get('tail')
    if tail is not None and tail.get('target', 0) > 0:
        height = tail.get('height')
        lines.append('Queue à 0,875 : ' +
                     (f"{100*height:.1f} % / {100*tail['target']:.1f} %"
                      if height is not None else 'non mesurable'))
    if 'targets_achieved' in quartiles:
        lines.append('Objectifs : ' + ('atteints' if quartiles['targets_achieved'] else 'incomplets'))
    sparse_tail = quartiles.get("sparse_tail")
    if sparse_tail is not None:
        progressive = after.get('render_quality') is not None
        protection = quartiles.get('protection_before', {})
        bright_missing = ('highlight_contrast' in protection
                          and protection['highlight_contrast'] is None)
        core_missing = 'core_contrast' in protection and protection['core_contrast'] is None
        lines.extend([
            "", "CONTRASTE : CIBLE ET GARDE" if progressive else "PROTECTION DU CONTRASTE",
            ('Cible brillante' if progressive else 'Brillant')
            + f" : {100 * sparse_tail['effective_retention']:.1f} %"
            + (' (non mesuré)' if bright_missing else ''),
            ('Cœur (minimum)' if progressive else 'Cœur')
            + f" : {100 * sparse_tail['requested_retention']:.1f} %"
            + (' (non mesuré)' if core_missing else ''),
        ])
    floor = quartiles.get("after", {}).get("regression_floor")
    if floor is not None and floor['minimum_count'] > 0:
        predicted = floor['predicted_log10_count']
        lines.extend([
            "", "NIVEAU LISSÉ À 0,8" if after.get('model') == 'piecewise' else "DROITE LOG À 0,8",
            f"Seuil : {floor['minimum_count']:g} pixels/classe",
            "Prédiction log10 : " + (f"{predicted:.3f}" if predicted is not None else "non mesurable"),
            "Seuil " + ("atteint" if floor['achieved'] else "non atteint"),
        ])
        if floor.get('extrapolated'):
            lines.append('Extrapolation hors du segment mesuré')
        elif after.get('measurable'):
            lines.append(f"Segment de contrôle : {after['line_start']:.3f} → {after['line_end']:.3f}")
        global_prediction = floor.get('longest_line_predicted_log10_count')
        if global_prediction is not None and after.get('model') != 'piecewise':
            lines.append(f"Droite globale log10 : {global_prediction:.3f}")
    reasons = {
        "max_iterations": "limite d’itérations atteinte",
        "iteration limit": "limite d’itérations atteinte",
        "no admissible render quality improvement": "aucun gain de qualité admissible",
        "constant distribution": "distribution constante",
        "no admissible improvement": "aucune amélioration admissible",
    }
    reason = report.get("stop_reason", "non renseigné")
    lines.extend(["", "Arrêt GHS :", textwrap.fill(reasons.get(reason, reason), 38)])
    metric = "range_width"
    initial = report.get("initial", {}).get(metric)
    final = report.get("final", {}).get(metric)
    if initial is not None and final is not None:
        lines.extend(["", "Largeur du critère (intensité 0–1)",
                      f"{initial:.5f} → {final:.5f}"])
        if initial > 0:
            lines.append(f"Gain relatif : {100 * (final / initial - 1):+.1f} %")
    return "\n".join(lines)


def histogram_figure(
    report: JSONReport, destination: Path, *, with_stars: bool = False
) -> None:
    """Trace les effectifs sur un seul axe Y logarithmique, avec X linéaire.

    Les classes vides sont masquées dans la vue logarithmique, sans ajouter
    de pseudo-effectif. Le log1p utilisé par l'analyse GHS reste indépendant
    de cette présentation. Une distribution entièrement vide garde un axe
    log valide de 1 à 10 pixels.
    Un seul panneau d'effectifs logarithmiques est tracé. L'abscisse conserve
    les intensités linéaires ; aucun panneau d'effectifs linéaires n'est produit.
    Avec ``with_stars=True``, seuls les histogrammes finaux recomposés sont
    tracés ; sinon, la figure contient uniquement les états sans étoiles.
    Si cumulative_transfer est fourni, un panneau ajoute F(x)/x en ordonnée
    linéaire sur les min/max initiaux. Le gain indéfini à x=0 est masqué ;
    les gains nuls sont représentés à zéro.
    Pour une sélection spatiale d'étoiles, ce panneau est explicitement
    l'enveloppe neutre des pics sélectionnés ; seule la recette RGB avec sa
    géométrie native reproduit tous les pixels.
    Un encart rappelle l’objectif et les statistiques de boucle
    du fond avant fusion. Le PNG est écrit dans ``destination``.
    Le fond noir, les textes clairs et les courbes contrastées s'appliquent
    uniquement à cette figure, sans modifier les autres affichages Matplotlib.
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    annotation_text = ghs_annotation(report)
    # 9 points, interligne 1,2 et marge pour titre/légende : aucun encart tronqué.
    annotation_height = len(annotation_text.splitlines()) * 9 * 1.2 / 72 + 1.4
    figure = Figure(figsize=(12, max(9 if report.get('cumulative_transfer') is not None else 6.5,
                                     annotation_height)), constrained_layout=True)
    figure.suptitle(
        "Images finales avec étoiles recomposées"
        if with_stars else "Évolution des étoiles seules"
        if report.get('diagnostic_subject') == 'stars' else "Évolution de l’image sans étoiles"
    )
    FigureCanvasAgg(figure)
    transfer = report.get('cumulative_transfer')
    grid = figure.add_gridspec(2 if transfer is not None else 1, 2, width_ratios=(2, 1))
    axis = figure.add_subplot(grid[0, 0])
    legend_handles = {}
    states = report["histograms"]
    selection = sorted({0, len(states) // 2, len(states) - 1})
    curves = []
    population_label = 'Étoiles seules' if report.get('diagnostic_subject') == 'stars' else 'Sans étoiles'
    for index in ([] if with_stars else selection):
        if index == 0:
            label = population_label + " — entrée linéaire"
            color = DIAGNOSTIC_COLORS[0]
        elif index == len(states) - 1:
            label = population_label + " — rendu final"
            color = DIAGNOSTIC_COLORS[2]
        else:
            label = population_label + " — étape intermédiaire"
            color = DIAGNOSTIC_COLORS[1]
        if report.get('diagnostic_trial'):
            label = 'Référence de la phase' if index == 0 else 'Essai évalué'
        curves.append((states[index]["counts"], label, color))
    if with_stars:
        curves.append((
            report["recomposed_histogram"]["counts"],
            "Image finale — étoiles recomposées", DIAGNOSTIC_COLORS[3],
        ))
    maximum = 0.0
    for raw_counts, label, color in curves:
        counts = np.asarray(raw_counts)
        maximum = max(maximum, float(np.max(counts)))
        x = (np.arange(len(counts)) + 0.5) / len(counts)
        stellar = report.get('diagnostic_subject') == 'stars'
        line, = axis.plot(
            x, np.ma.masked_less_equal(counts, 0), label=label, color=color,
            marker='.' if stellar else None, markersize=3,
        )
        legend_handles[label] = line
    axis.set_ylim(.8 if report.get('diagnostic_subject') == 'stars' else 1,
                  max(10, maximum * 1.2, report.get('histogram_ymax', 0)))
    axis.set_yscale("log")
    axis.set_title('Effectifs en échelle logarithmique')
    axis.set_xlabel('Intensité Y')
    axis.set_xlim(0, 1)
    axis.set_ylabel('Nombre de pixels par classe')
    trial = report.get('diagnostic_trial', {})
    profile = trial.get('profile') or report.get('quartile_stretch', {}).get('after', {})
    shape = profile.get('shape')
    if shape and curves and not with_stars:
        density = np.array(shape['log_density'])
        values = 10**density*profile['support']/len(curves[-1][0])
        line, = axis.plot(shape['x'], values, color='#dedede', linestyle='--',
                          linewidth=1.3, label='Enveloppe lissée')
        legend_handles['Enveloppe lissée'] = line
        for index, piece in enumerate(profile.get('pieces', [])):
            xx = np.array([piece['start'], piece['end']])
            yy = 10**(piece['intercept']+piece['slope']*xx)*profile['support']/len(curves[-1][0])
            line, = axis.plot(xx, yy, color='#dd99ee', linewidth=1, marker='o', markersize=3)
            if index == 0:
                legend_handles['Approximation par morceaux'] = line
        floor = profile.get('regression_floor', {})
        if floor.get('minimum_count', 0) > 0:
            target = floor['minimum_count']*floor['reference_bins']/len(curves[-1][0])
            line, = axis.plot([.8], [target], marker='x', color='white', linestyle='none', markersize=8)
            legend_handles['Seuil à 0,8'] = line
    if transfer is not None:
        from matplotlib.ticker import AutoMinorLocator, ScalarFormatter

        from lib.stretch_transfer import CumulativeStretchTransform

        x, y = transfer['x'], transfer['y']
        gain = CumulativeStretchTransform.multiplicative_gain(np.asarray(x), np.asarray(y))
        low, high = report['transfer_domain']
        curve_axis = figure.add_subplot(grid[1, 0])
        stellar_envelope = transfer.get('scalar_role') == 'selected_star_neutral_envelope'
        curve_axis.plot(x, np.ma.array(gain, mask=~np.isfinite(gain)),
                        color='#ffd166', linewidth=1.3,
                        marker='o' if len(x) == 1 else None,
                        label=('Enveloppe — pics sélectionnés' if stellar_envelope
                               else 'Gain cumulé — gris neutres'))
        curve_axis.axhline(1., color='#aaaaaa', linestyle='--',
                           linewidth=.8, label='Identité : ×1')
        if low == high:
            margin = max(1e-12, abs(low)*1e-6)
            curve_axis.set_xlim(low-margin, high+margin)
        else:
            curve_axis.set_xlim(low, high)
        finite = gain[np.isfinite(gain)]
        maximum = float(np.max(finite)) if finite.size else 1.
        curve_axis.set_ylim(0., max(1., maximum)*1.1)
        curve_axis.set_title(
            'Enveloppe du gain stellaire — R=G=B' if stellar_envelope
            else 'Gain cumulé depuis l’entrée — échelle linéaire — R=G=B'
        )
        curve_axis.set_xlabel(f'Entrée initiale : min={low:.10g}, max={high:.10g}')
        curve_axis.set_ylabel('Facteur multiplicatif F(x) / x')
        formatter = ScalarFormatter(useOffset=False)
        formatter.set_powerlimits((-3, 3))
        curve_axis.xaxis.set_major_formatter(formatter)
        curve_axis.xaxis.set_minor_locator(AutoMinorLocator())
        curve_axis.grid(True, alpha=.2)
        curve_axis.legend(fontsize=8, loc='best')
        status = 'atteinte' if transfer['precision_achieved'] else 'limite atteinte'
        note = (f"{len(x)} points float64 ; erreur F(x) {transfer['observed_error']:.2g} "
                f"({status})\nÀ x=0 : gain indéfini ; RGB coloré : recette cumulée")
        if stellar_envelope:
            note += '\nPoids spatial fixé à 1 ; reproduction exacte : recette et géométrie natives'
        curve_axis.text(.02, .96, note, transform=curve_axis.transAxes, va='top', fontsize=8)
    annotation = figure.add_subplot(grid[:, 1] if transfer is not None else grid[0, 1])
    annotation.set_axis_off()
    annotation.text(
        0.03, 0.98, annotation_text,
        transform=annotation.transAxes, va="top", fontsize=9,
        bbox={"boxstyle": "round,pad=0.7"},
    )
    figure.legend(
        handles=list(legend_handles.values()),
        labels=list(legend_handles),
        # Les audits de qualité sont longs : garder la légende sous le graphe,
        # à gauche, évite son chevauchement avec le bas de l'encart à droite.
        loc="outside lower left", ncol=2, fontsize=9,
    )
    apply_dark_diagnostic_style(figure)
    figure.savefig(destination, dpi=130, facecolor=figure.get_facecolor(),
                   edgecolor=figure.get_edgecolor(), transparent=False)
    figure.clear()
