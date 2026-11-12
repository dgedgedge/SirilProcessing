"""Export des histogrammes majeurs AutoGHS, exclusivement en ordonnée log.

La série utilise les effectifs CPU et, en option, des instantanés JPEG sur
disque capturés pendant le calcul. Aucun tableau candidat n'est retenu sur
GPU. Voir docs/scripts/postProcess/ghs-histogram-shape.md.
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

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
            Orientation FITS BOTTOM-UP ou TOP-DOWN.

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

    def close(self) -> None:
        """Retire les JPEG temporaires, après leur publication ou après échec."""
        if self._cache is not None:
            self._cache.cleanup()
            self._cache = None
        self._images.clear()
        self._stars = None

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
            Branche RANGE avec histogram_stages, présentation et recherches.
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
            Les encarts identifient leur série et leur numéro. Les résultats
            finaux référencent l'essai choisi par son numéro dans cette série,
            sans comparer des histogrammes pour deviner leur provenance.
        """
        from lib.final_render import histogram_figure

        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        states = report.get('histograms', [])
        if not states:
            raise ValueError('Aucun histogramme enregistré pour la série AutoGHS')
        first = states[0]
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
        trials.append(dict(stage='Fond retenu', series_label='Résultat retenu',
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
                           diagnostic_trial=trial, histogram_ymax=maximum)
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
            metadata.update(index=index, path=str(path), yscale='log', xscale='linear',
                            estimated=bool(trial.get('estimated', False)),
                            population='with_stars' if trial.get('with_stars') else 'starless')
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
                               else 'Image sans étoiles')
                image_links = '<p>'+' — '.join(
                    f'<a href="{Path(metadata[key]).name}">{label}</a>'
                    for key, label in (
                        ('image_jpeg', image_label),
                        ('with_stars_jpeg', 'Aperçu avec étoiles'),
                    ) if key in metadata
                )+'</p>'
                if metadata['estimated']:
                    image_links += '<p>Histogramme estimé ; image calculée sur tout le champ.</p>'
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
