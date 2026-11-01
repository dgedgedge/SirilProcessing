"""Provenance et reprise du rendu final sans réexécuter les traitements amont.

Les chemins viennent des rapports, jamais d'une recherche du dernier FITS par
nom ou date. Les nouvelles exécutions contrôlent aussi taille et date de fichier.
"""

from __future__ import annotations

import json
from pathlib import Path

from astropy.io import fits

from lib.star_separation import validate_linear_header
from lib.type_defs import JSONReport


class PostProcessResume:
    """Mémorise l'entrée initiale et le FITS réellement reçu par AutoGHS."""

    @staticmethod
    def read_object(path: Path) -> JSONReport:
        """Lit un rapport JSON objet ; refuse les racines scalaires ou tableaux."""
        payload = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            raise ValueError(f'Rapport de reprise invalide : {path}')
        return payload

    @staticmethod
    def snapshot(path: Path) -> JSONReport:
        """Identifie un fichier par chemin absolu, taille et mtime en nanosecondes."""
        path = path.resolve()
        stat = path.stat()
        return {'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}

    @staticmethod
    def manifest_path(report: Path) -> Path:
        """Place la provenance de reprise à côté du rapport de post-traitement."""
        return report.with_name(report.stem + '_resume.json')

    @classmethod
    def save(cls, source: Path, report: Path, results: JSONReport) -> None:
        """Conserve les références avant le rendu ou après succès, sans changer les options."""
        prepared = source
        for name, branch in results.items():
            if not isinstance(branch, dict) or name == 'annotation':
                continue
            if name == 'autoghs':
                if branch.get('image_path'):
                    prepared = Path(branch['image_path'])
                break
            if branch.get('output_image'):
                prepared = Path(branch['output_image'])
        if not prepared.is_absolute():
            prepared = report.parent / prepared
        payload = {'version': 1, 'source': cls.snapshot(source),
                   'prepared': cls.snapshot(prepared)}
        cls.manifest_path(report).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8',
        )

    @classmethod
    def _incomplete_history(cls, source: Path, report: Path) -> JSONReport:
        """Valide les rapports d'un ancien traitement interrompu avant publication.

        Les chemins explicites doivent former une chaîne depuis ``source``
        jusqu'à l'entrée du rapport AutoGHS. Chaque fichier doit exister et
        précéder son rapport en date de modification. Cette compatibilité sans
        snapshots ne constitue pas une preuve d'intégrité cryptographique.
        Aucun fichier n'est créé ou réinitialisé pendant cette lecture.
        """
        directory = report.parent / report.stem
        autoghs_path = directory / '05_autoghs.json'
        if not autoghs_path.is_file():
            raise FileNotFoundError(
                f'Aucun rapport AutoGHS permettant de reprendre le traitement : {report}'
            )
        previous: JSONReport = {}
        current = source.resolve()

        def referenced(value: object) -> Path:
            """Résout uniquement un chemin explicitement enregistré dans l'étape."""
            if not isinstance(value, str) or not value:
                raise ValueError('Rapport incomplet : chemin de reprise absent')
            path = Path(value)
            return (path if path.is_absolute() else directory / path).resolve()

        for name in ('01_gradient', '02_photometry', '03_restoration',
                     '03_deconvolution', '04_denoise', '05_autoghs'):
            stage_path = directory / f'{name}.json'
            if not stage_path.is_file():
                continue
            stage = cls.read_object(stage_path)
            if referenced(stage.get('image_path')) != current:
                raise ValueError(f'Chaîne de reprise incohérente : {stage_path}')
            if current.stat().st_mtime_ns > stage_path.stat().st_mtime_ns:
                raise ValueError(f'Entrée modifiée après son traitement : {current}')
            prefix = name[3:]
            if prefix != 'autoghs':
                if stage.get('error') or stage.get('status') == 'ERROR':
                    raise ValueError(f'Étape amont en échec : {stage_path}')
                current = referenced(stage.get('output_image'))
                if current.stat().st_mtime_ns > stage_path.stat().st_mtime_ns:
                    raise ValueError(f'Sortie modifiée après son traitement : {current}')
            previous[prefix] = stage
        validate_linear_header(fits.getheader(current))
        return previous

    @classmethod
    def load(cls, source: Path, report: Path) -> tuple[Path, JSONReport]:
        """Retrouve l'entrée linéaire du précédent traitement de la même commande.

        Un historique invalide ou un intermédiaire disparu/modifié est fatal,
        avant toute remise à zéro d'AutoGHS. Les anciens rapports sont compatibles
        lorsqu'ils enregistrent explicitement autoghs.image_path. Sans rapport
        global ni manifeste, les rapports d'étapes peuvent prouver l'enchaînement
        d'un ancien traitement interrompu avant le rendu final.
        """
        if not report.is_file():
            if not cls.manifest_path(report).exists():
                previous = cls._incomplete_history(source, report)
                prepared = Path(previous['autoghs']['image_path'])
                if not prepared.is_absolute():
                    prepared = report.parent / report.stem / prepared
                return prepared.resolve(), previous
            raise FileNotFoundError(
                'Aucun traitement précédent à reprendre ; lancer le traitement complet '
                f'avec les mêmes entrée et sortie : {report}'
            )
        previous = cls.read_object(report)
        manifest = cls.manifest_path(report)
        if manifest.is_file():
            payload = cls.read_object(manifest)
            if payload.get('version') != 1:
                raise ValueError('Version de provenance de reprise inconnue')
            if payload.get('source') != cls.snapshot(source):
                raise ValueError('L’entrée initiale a changé depuis le traitement précédent')
            stored = payload.get('prepared')
            if not isinstance(stored, dict) or not isinstance(stored.get('path'), str):
                raise ValueError('Référence linéaire de reprise invalide')
            prepared = Path(stored['path'])
            if stored != cls.snapshot(prepared):
                raise ValueError(
                    'Le FITS linéaire préparé a changé ; refaire le traitement complet'
                )
        else:
            autoghs = previous.get('autoghs')
            if not isinstance(autoghs, dict) or not autoghs.get('image_path'):
                raise ValueError('Le rapport ne contient pas l’entrée linéaire AutoGHS ; '
                                 'refaire le traitement complet')
            prepared = Path(autoghs['image_path'])
            if not prepared.is_absolute():
                prepared = report.parent / prepared
        prepared = prepared.resolve()
        validate_linear_header(fits.getheader(prepared))
        return prepared, previous

    @classmethod
    def load_color(cls, source: Path, report: Path) -> JSONReport:
        """Valide une reprise après GHS sans exiger les intermédiaires linéaires.

        Les anciens rapports avec couches explicites restent compatibles. Après
        une passe couleur, leurs snapshots empêchent de réutiliser des couches
        modifiées à l'extérieur du traitement. Aucun dossier n'est réinitialisé.
        """
        previous = cls.read_object(report)
        manifest = cls.manifest_path(report)
        if manifest.is_file():
            provenance = cls.read_object(manifest)
            if provenance.get('version') != 1:
                raise ValueError('Version de provenance de reprise inconnue')
            if provenance.get('source') != cls.snapshot(source):
                raise ValueError('L’entrée initiale a changé depuis le traitement précédent')
        autoghs = previous.get('autoghs')
        if not isinstance(autoghs, dict) or autoghs.get('status') not in (
            'SUCCESS', 'NO_IMPROVEMENT',
        ):
            raise ValueError('Reprise couleur : aucun rendu AutoGHS réussi disponible')
        strategies, stars = autoghs.get('strategies'), autoghs.get('stars')
        if not isinstance(strategies, dict) or not isinstance(stars, dict):
            raise ValueError('Reprise couleur : rapport des couches AutoGHS invalide')
        branch = strategies.get('range')
        if not isinstance(branch, dict):
            raise ValueError('Reprise couleur : aucun fond RANGE disponible')
        for value, role in ((branch.get('starless_image'), 'STARLESS'),
                            (stars.get('output_image'), 'STARS')):
            if not isinstance(value, str):
                raise ValueError('Reprise couleur : couches GHS et étoiles non référencées')
            path = Path(value)
            if not path.is_absolute():
                raise ValueError('Reprise couleur : les chemins des couches doivent être absolus')
            header = fits.getheader(path)
            if not header.get('SPNLIN') or header.get('SPROLE') != role:
                raise ValueError(f'Couche de reprise couleur incompatible : {path}')
        color = previous.get('final_color', {})
        snapshots = color.get('inputs', []) if isinstance(color, dict) else []
        if not isinstance(snapshots, list):
            raise ValueError('Provenance des couches couleur invalide')
        for snapshot in snapshots:
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get('path'), str):
                raise ValueError('Provenance des couches couleur invalide')
            if snapshot != cls.snapshot(Path(snapshot['path'])):
                raise ValueError('Une couche GHS a changé ; relancer AutoGHS avant la couleur')
        return previous

    @staticmethod
    def save_final_images(
        directory: Path, roots: list[Path], mosaic: bool, images: list[Path],
    ) -> None:
        """Enregistre les produits finaux lightProcess, y compris les stacks à une pose."""
        directory.mkdir(parents=True, exist_ok=True)
        payload = {'roots': [str(root.resolve()) for root in roots], 'mosaic': mosaic,
                   'images': [str(image.resolve()) for image in images]}
        (directory / 'lightprocess_final_images.json').write_text(
            json.dumps(payload, indent=2), encoding='utf-8',
        )

    @staticmethod
    def final_images(
        directory: Path, work: Path, roots: list[Path], mosaic_name: str | None,
    ) -> list[Path]:
        """Retrouve les mêmes produits lightProcess sans calibration, stacking ni mosaïque.

        Pour les anciens traitements, le rapport de mosaïque ou le nom standard
        d'un stack est accepté. Une ambiguïté ou un produit absent reste une erreur.
        """
        manifest = directory / 'lightprocess_final_images.json'
        if manifest.is_file():
            payload = PostProcessResume.read_object(manifest)
            if (payload.get('roots') != [str(root.resolve()) for root in roots]
                    or payload.get('mosaic') != bool(mosaic_name)):
                raise ValueError(
                    'Les sessions ou le mode mosaïque diffèrent du traitement précédent'
                )
            paths = payload.get('images')
            if (not isinstance(paths, list) or len(paths) != (1 if mosaic_name else len(roots))
                    or not all(isinstance(path, str) for path in paths)):
                raise ValueError('Liste de produits finaux de reprise invalide')
            images = [Path(path) for path in paths]
        elif mosaic_name:
            report = work / f'{mosaic_name}_mosaic.json'
            payload = PostProcessResume.read_object(report)
            if not isinstance(payload.get('output_image'), str):
                raise ValueError('Le rapport de mosaïque ne référence pas son image finale')
            image = Path(payload['output_image'])
            images = [image if image.is_absolute() else report.parent / image]
        else:
            images = []
            for root in roots:
                candidates = [directory / root.name / f'{root.name}_combined{suffix}'
                              for suffix in ('.fits', '.fit')]
                images.append(next(
                    (path for path in candidates if path.is_file()), candidates[0],
                ))
        if not images or any(not image.is_file() for image in images):
            raise FileNotFoundError('Résultat final précédent absent ; refaire le traitement complet')
        return images
