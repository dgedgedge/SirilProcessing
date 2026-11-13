"""Index HTML locaux des traitements, audits et images, sans lecture des FITS.

Chaque dossier réel reçoit un index autonome. Les rapports restent la source
scientifique ; leurs données sont affichées sans recalcul de verdict. Voir
docs/scripts/lightProcess/html-reports.md pour les limites et la régénération.
"""
from __future__ import annotations

import argparse
from html import escape
import json
import logging
import math
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import quote


class ProcessingHtmlReport:
    """Publie une navigation hiérarchique hors ligne sur des résultats existants.

    Ne suit pas les dossiers symboliques et ne lit jamais les pixels FITS.
    Échappe textes et chemins. Refuse de remplacer un index HTML étranger au
    générateur. Les erreurs de lecture des audits sont visibles dans la page ;
    les erreurs d'accès au dossier ou d'écriture sont propagées.
    """

    MARKER = '<!-- SirilProcessing processing report -->'
    TEXT_LIMIT = 64000
    JSON_LIMIT = 32 * 1024 * 1024
    NODE_LIMIT = 500
    IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png'}
    FITS_EXTENSIONS = {'.fits', '.fit', '.fts'}
    DESCRIPTIONS = {
        '01_calibration': ('Calibration des acquisitions',
            'Préparation et calibration des poses avec les darks et flats disponibles. '
            'Les sous-sessions sont réunies par champ ou panneau avant sélection et empilement.'),
        '01_session': ('Sous-sessions',
            'Calibration séparée de chaque acquisition. Les rapports indiquent les sources, '
            'les poses conservées et les éventuels échecs ; les fichiers calibrés restent accessibles.'),
        '02_registration': ('Alignement initial',
            'Création de la séquence FITS, détection des étoiles, résolution astrométrique '
            'si activée, puis mesure des transformations géométriques entre poses.'),
        '03_quality': ('Sélection des poses',
            'Contrôle de validité des transformations et sélection selon FWHM, rondeur, '
            'nombre d’étoiles et profils stellaires, selon les options actives. '
            'Les exclusions concernent l’empilement et ne suppriment pas les acquisitions sources.'),
        '04_drizzle': ('Décision Drizzle',
            'Analyse de l’échantillonnage, du dithering, de la couverture sous-pixel, du '
            'nombre de poses, de l’alignement et des ressources. SKIPPED.txt détaille '
            'la désactivation. Le script préalable vérifie le démarrage/version de Siril ; '
            'le rééchantillonnage est effectué dans l’étape d’empilement.'),
        '05_stacking': ('Empilement',
            'Application des transformations aux poses retenues, reconstruction CFA et '
            'Drizzle si sélectionnés, puis combinaison avec les paramètres de rejet et '
            'de pondération enregistrés dans le rapport.'),
        '02_mosaic': ('Assemblage et relais linéaire',
            'Assemblage des panneaux en mosaïque ou copie du stack pour un champ simple. '
            'Le FITS linéaire est transmis à la photométrie ; assembled précise le parcours réel.'),
        '03_photometrie': ('Préparation photométrique',
            'Correction du gradient et étalonnage des couleurs sur une image linéaire. '
            'Les sous-dossiers exposent les moteurs et contrôles effectivement exécutés.'),
        '01_gradient': ('Correction du gradient',
            'Estimation puis correction du fond spatial. Les images de points de mesure '
            'et les rapports documentent les zones utilisées et la correction obtenue.'),
        '02_photometrie': ('Étalonnage des couleurs',
            'Résolution astrométrique puis étalonnage SPCC ou PCC selon les paramètres. '
            'Les catalogues, profils spectraux et commandes utilisés figurent dans les audits.'),
        '04_restoration': ('Restauration et sélection',
            'Comparaison des candidats de netteté et débruitage. Les contrôles locaux '
            'évaluent chaque opération ; les contrôles finaux comparent les chaînes à '
            'l’image d’origine. Un rejet conserve l’entrée, un gain non démontré ne vaut pas amélioration.'),
        '01_chains': ('Anciens essais de restauration',
            'Calculs numérotés et mutualisés entre chaînes. Le nom indique les traitements '
            'successifs séparés par then. Chaque essai conserve son audit, résultat FITS '
            'et vérifications. Les rapports distinguent accepted_output de retained_input.'),
        '00_mask_origine': ('Masques de l’image initiale',
            'Masques de ciel, étoiles, objets, incertitudes et pixels invalides de la '
            'référence initiale. Le FITS code ces catégories et le PNG en fournit l’aperçu. '
            'Les premiers traitements et contrôles finaux partagent cette référence.'),
        'verification_finale': ('Vérifications finales',
            'Comparaison à l’image initiale : appariement stellaire, FWHM, bruit, anneaux, '
            'flux stellaire et structures diffuses selon les critères présents dans l’audit. '
            'PASS signifie satisfait, FAIL non satisfait, UNKNOWN non vérifiable. '
            'Les valeurs, seuils et motifs du compte rendu font foi.'),
        '05_autoghs': ('Rendu GHS',
            'Séparation des étoiles et du fond, étirement GHS du fond, rendu conservateur '
            'des étoiles puis recomposition et diagnostics. Les couches restent disponibles pour la reprise couleur.'),
        '01_separation': ('Séparation des étoiles',
            'Extraction du fond sans étoiles et de la couche stellaire. Les FITS intermédiaires, '
            'scripts et journaux documentent la décomposition de l’image linéaire.'),
        '02_stars': ('Rendu des étoiles',
            'Rendu conservateur de la couche stellaire, avec contrôle des profils et des '
            'couleurs. Les diagnostics illustrent les paramètres et mesures obtenus.'),
        '03_ghs': ('Étirement du fond',
            'Optimisation GHS RANGE du fond sans étoiles, avec contrôles d’écrêtage et '
            'de préservation du signal. Le rapport indique les paramètres effectivement retenus.'),
        '04_recomposition': ('Recomposition du rendu',
            'Fusion du fond étiré et des étoiles rendues, avec contrôles de couleur et '
            'de profils stellaires. Les FITS et JPEG conservent le rendu obtenu.'),
        '05_diagnostics': ('Diagnostics du rendu',
            'Histogrammes, aperçus et rapports détaillés servant à examiner les résultats '
            'du rendu. Une image de diagnostic ne remplace pas les critères chiffrés.'),
        '06_color': ('Couleur et publication finale',
            'Passe couleur éventuelle après GHS, recomposition, export et annotation. '
            'Les contrôles de luminosité et de couleur et les snapshots des couches '
            'documentent les résultats et leur provenance.'),
    }
    CSS = '''body{font:16px/1.55 system-ui,sans-serif;max-width:1200px;margin:auto;
    padding:2rem;background:#111827;color:#e5e7eb}a{color:#93c5fd}h1,h2,h3{color:#fff}
    nav,section,details{padding:1rem;margin:1rem 0;background:#1f2937;border-radius:8px}
    summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere}
    table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:.5rem;
    border-bottom:1px solid #4b5563;overflow-wrap:anywhere}.gallery{display:flex;
    flex-wrap:wrap;gap:1rem}figure{margin:0;max-width:360px}img{max-width:100%;
    max-height:320px;object-fit:contain}.criteria-scroll{overflow-x:auto}
    .criteria-table{width:max-content;min-width:100%}.criteria-table th{vertical-align:top}
    .criteria-table td{white-space:nowrap}.criteria-table:not(.presentation-table) td:first-child{white-space:normal;
    width:10rem;min-width:10rem;max-width:10rem}.criteria-table th,.criteria-table td{border:1px solid #64748b}
    .presentation-table .table-text{white-space:normal;max-width:24rem}
    .presentation-table .table-label{white-space:normal;word-wrap:break-word;
    width:10rem;min-width:10rem;max-width:10rem}
    .selection-mark{font-size:1.2rem;font-weight:700;line-height:1}
    .selection-kept{color:#4ade80}.selection-rejected{color:#f87171}.selection-skipped{color:#fff}
    .criteria-table thead{background:#111827}
    .criteria-table thead tr:first-child>th{border:2px solid #94a3b8}
    .criteria-table .criteria-group-start{border-left:2px solid #94a3b8}
    .criteria-table tr>:last-child{border-right:2px solid #94a3b8}
    .criteria-table thead th[colspan]{max-width:0;line-height:1.2;
    vertical-align:bottom;font-size:.8rem}
    .criteria-table thead th[colspan="1"]{min-width:3rem}
    .criteria-table th.criteria-vertical{height:13rem;vertical-align:bottom;padding:0 .4rem}
    .criteria-vertical>span{display:block;position:relative;width:1.4rem;height:13rem;margin:0 auto}
    .criteria-vertical>span>span{position:absolute;bottom:.4rem;left:50%;
    transform:rotate(-90deg) translateY(50%);transform-origin:left bottom;white-space:nowrap}
    .criteria-table th[colspan].criteria-vertical>span>span{width:12rem;
    white-space:normal;overflow-wrap:normal}small{color:#cbd5e1}.warning{color:#fcd34d}'''

    def __init__(self, root: Path | str) -> None:
        """Fixe une racine existante ; la génération n’édite que ses index HTML."""
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError('Une racine de résultats est requise')

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare le dossier à indexer, sans option de calcul scientifique."""
        parser.add_argument('directory', type=Path, help='Racine des résultats à indexer')

    @staticmethod
    def _link(path: Path, base: Path, label: str | None = None) -> str:
        """Encode un lien relatif local, y compris espaces, accents et caractères HTML."""
        relative = path.relative_to(base).as_posix()
        return f'<a href="{escape(quote(relative, safe="/"), quote=True)}">{escape(label or path.name)}</a>'

    @staticmethod
    def _source_link(source: object, directory: Path | None = None) -> str:
        """Affiche le basename et un lien relatif vers la pose si elle existe.

        Rebase les chemins 01_session sur la calibration courante pour prendre
        en charge les résultats déplacés. Le suffixe de session est conservé,
        sans recherche par basename qui confondrait des poses homonymes.
        """
        if not isinstance(source, str) or not source:
            return '?'
        path = Path(source)
        label = escape(path.name)
        if directory is None:
            return label
        calibration = next((parent for parent in (directory, *directory.parents)
                            if parent.name == '01_calibration'), None)
        candidates = []
        if calibration is not None and '01_session' in path.parts:
            index = path.parts.index('01_session')
            candidates.append(calibration.joinpath(*path.parts[index:]))
        candidates.append(path if path.is_absolute() else directory / path)
        target = next((candidate for candidate in candidates if candidate.is_file()), None)
        if target is None:
            return label
        relative = Path(os.path.relpath(target, directory)).as_posix()
        return '<a href="' + escape(quote(relative, safe='/'), quote=True) + '">' + label + '</a>'

    @classmethod
    def _description(cls, directory: Path) -> tuple[str, str]:
        """Décrit le rôle d’une étape, sans présumer de son exécution effective."""
        if directory.name in cls.DESCRIPTIONS:
            return cls.DESCRIPTIONS[directory.name]
        if directory.name.startswith('part_'):
            return ('Panneau ' + directory.name,
                    'Traitement indépendant d’un panneau de mosaïque : calibration de ses '
                    'acquisitions, alignement, sélection et empilement. source.json associe '
                    'ce numéro à son répertoire d’entrée ; les résultats alimentent la mosaïque.')
        if directory.name.endswith('_calibrated'):
            return ('Poses calibrées',
                    'Séquence des poses après correction instrumentale selon les options '
                    'de dark et flat actives. Ces FITS alimentent la sélection et l’empilement ; '
                    'ils ne constituent pas des images finales rendues.')
        if 'denoise' in directory.name or 'sharpen' in directory.name:
            engines = [
                name for name in ('siril', 'cosmic_clarity', 'graxpert')
                if name in directory.name
            ]
            return ('Essai : ' + directory.name,
                'Le nom décrit le parcours appliqué à l’entrée : ' + directory.name + '. '
                'Moteurs identifiés : ' + ', '.join(engines) + '. '
                'Le débruitage réduit le bruit ; la netteté cherche une amélioration des profils '
                'stellaires. Les critères locaux et finaux décident de l’acceptation. '
                'Siril utilise le débruitage VST ou une déconvolution Richardson–Lucy selon '
                'l’opération ; Cosmic Clarity et GraXpert utilisent leurs modèles neuronaux. '
                'Seuls les moteurs nommés dans l’audit sont exécutés. Consulter les comptes '
                'rendus pour distinguer une sortie acceptée d’une entrée conservée.')
        return directory.name, ('Dossier de produits du traitement. Les fichiers et audits ci-dessous '
            'documentent son contenu réel ; la présence d’un dossier ne prouve pas qu’une étape a réussi.')

    @classmethod
    def _json_view(cls, value: object, budget: list[int], depth: int = 0) -> str:
        """Affiche les données structurées en bornant les tableaux et la profondeur."""
        if budget[0] <= 0 or depth > 7:
            return '<small>Détails supplémentaires dans le rapport JSON.</small>'
        budget[0] -= 1
        if isinstance(value, dict):
            rows = []
            for key, item in value.items():
                if budget[0] <= 0:
                    rows.append('<tr><td colspan="2">Suite dans le rapport JSON.</td></tr>')
                    break
                detail = cls._json_view(item, budget, depth + 1)
                rows.append(
                    f'<tr><th>{escape(str(key))}</th><td>{detail}</td></tr>'
                )
            return '<table>' + ''.join(rows) + '</table>'
        if isinstance(value, list):
            return f'<details><summary>{len(value)} éléments</summary>' + ''.join(
                cls._json_view(item, budget, depth+1) for item in value[:50]
            ) + ('<p>Suite dans le rapport JSON.</p>' if len(value) > 50 else '') + '</details>'
        return escape('indisponible' if value is None else str(value))

    @classmethod
    def _ranking_view(cls, payload: object) -> str:
        """Présente le classement enregistré en tableau, sans recalcul scientifique."""
        if not isinstance(payload, dict):
            return ''
        summary = payload.get('summary')
        ranking = summary.get('accepted_ranking') if isinstance(summary, dict) else None
        if not isinstance(ranking, dict):
            variants = payload.get('variants')
            if not isinstance(variants, list):
                return ''
            def mapping(value: object) -> dict[str, object]:
                """Ignore un champ mal formé plutôt que d'inventer des mesures."""
                return value if isinstance(value, dict) else {}

            accepted = [v for v in variants if isinstance(v, dict)
                        and v.get('status') == 'accepted']
            selected = payload.get('selected_chain')
            retained = payload.get('selection_score')
            rows = []
            for variant in accepted:
                score = variant.get('selection_score')
                numeric = isinstance(score, (int, float)) and math.isfinite(score)
                details = dict(mapping(variant.get('score_details')))
                comparison = mapping(variant.get('comparison'))
                artifacts = mapping(variant.get('artifacts'))
                before = mapping(comparison.get('before_fwhm_px')).get('mean')
                after = mapping(comparison.get('after_fwhm_px')).get('mean')
                if isinstance(before, (int, float)) and before > 0 and isinstance(after, (int, float)):
                    details.setdefault('fwhm_mean_ratio', after / before)
                details.setdefault('fwhm_median_ratio', mapping(comparison.get('paired_ratio')).get('median'))
                details.setdefault('useful_noise_gain', mapping(artifacts.get('noise')).get('proven_gain'))
                for key in ('max_ring_fraction', 'max_noise_ratio'):
                    details.setdefault(key, artifacts.get(key))
                decision = ('RETENUE' if variant.get('chain') == selected else
                            'ACCEPTÉE NON RETENUE' if selected is not None else
                            'CHOIX NON ENREGISTRÉ')
                chain = variant.get('chain')
                label = ' → '.join(map(str, chain)) if isinstance(chain, list) else str(chain or '')
                rows.append(dict(
                    rank=None, label=label,
                    decision=decision, selection_score=score if numeric else None,
                    gap_to_retained_score=(retained - score if numeric
                        and isinstance(retained, (int, float)) else None),
                    score_details=details,
                ))
            rows.sort(key=lambda row: (row['selection_score'] is None,
                      -(row['selection_score'] or 0)))
            for row in rows:
                if row['selection_score'] is not None:
                    row['rank'] = 1 + sum(other['selection_score'] is not None
                        and other['selection_score'] > row['selection_score'] for other in rows)
            ranking = dict(rows=rows, formula=next((row['score_details'].get('formula')
                           for row in rows if row['score_details'].get('formula')), None), policy='Classement d’affichage par score enregistré ; '
                           'le choix reste celui du rapport. Aucun score n’est recalculé.')
        columns = (
            ('rank', 'Rang'), ('label', 'Chaîne'), ('decision', 'Choix'),
            ('fwhm_mean_ratio', 'Ratio FWHM moyen'),
            ('fwhm_median_ratio', 'Ratio FWHM médian'),
            ('useful_noise_gain', 'Gain utile bruit'),
            ('max_ring_fraction', 'Anneaux'), ('max_noise_ratio', 'Ratio bruit'),
            ('selection_score', 'Score'), ('gap_to_retained_score', 'Écart au retenu'),
        )
        rows = []
        for row in ranking.get('rows', []):
            if not isinstance(row, dict):
                continue
            details = row.get('score_details')
            data = dict(details if isinstance(details, dict) else {}, **row)
            rows.append([data.get(key) for key, _ in columns])
        return ('<section><h3>Comparatif des chaînes acceptées</h3><p>'
                + escape(str(ranking.get('formula') or 'Formule du score non enregistrée'))
                + '</p>' + cls._presentation_legend()
                + cls._presentation_table([title for _, title in columns], rows,
                                          verdict_columns={2}, label_columns={1}) + '</section>')

    @classmethod
    def _combinations_view(cls, payload: object) -> str:
        """Rappelle les audits finaux et locaux de chaque combinaison enregistrée.

        Relie les étapes aux opérations par leur identifiant, y compris les
        calculs réutilisés. Les contrôles absents restent explicitement absents.
        Aucun verdict ni seuil n'est déduit des paramètres globaux.
        """
        if not isinstance(payload, dict) or not isinstance(payload.get('variants'), list):
            return ''
        operations = payload.get('operations', [])
        if not isinstance(operations, list):
            operations = []
        parts = ['<section><h3>Critères de qualité par combinaison</h3>']
        for index, variant in enumerate(payload['variants'], start=1):
            if not isinstance(variant, dict):
                continue
            chain = variant.get('chain')
            label = (' → '.join(str(item) for item in chain)
                     if isinstance(chain, list) and chain else 'image inchangée')
            parts.append('<section><h4>Combinaison ' + str(index) + ' : '
                         + escape(label) + '</h4>')
            # Ne pas réintroduire les variantes dans le parcours de synthèse.
            final = {key: value for key, value in variant.items()
                     if key not in {'variants', 'steps'}}
            parts.append('<h4>Contrôle final</h4>' + cls._summary_view(final, compact=True))
            quality = variant.get('quality')
            criteria = variant.get('criteria')
            if not isinstance(criteria, dict) or not criteria:
                if not isinstance(quality, dict) or not quality.get('criteria'):
                    parts.append('<p>Critères finaux non enregistrés ; contrôle '
                                 'non réalisé ou non disponible dans cet audit.</p>')
            steps = variant.get('steps', [])
            for number, step in enumerate(steps if isinstance(steps, list) else [], start=1):
                if not isinstance(step, dict):
                    continue
                identifier = step.get('operation_id')
                audit = next((op for op in operations if isinstance(op, dict)
                              and identifier is not None
                              and op.get('operation_id') == identifier), None)
                parts.append('<h4>Contrôle local — étape ' + str(number) + '</h4>')
                if audit is None:
                    parts.append('<p>Audit local non enregistré.</p>')
                else:
                    local = {key: value for key, value in audit.items()
                             if key != 'variants'}
                    parts.append(cls._summary_view(local, compact=True))
            parts.append('</section>')
        parts.append('</section>')
        return ''.join(parts)

    @classmethod
    def _summary_view(cls, payload: object, *, compact: bool = False) -> str:
        """Extrait les informations utiles des JSON, sans recalculer les critères.

        Affiche les rapports imbriqués des traitements, paramètres, verdicts et
        seuils. Ignore les tableaux de poses et limite profondeur et effectifs.
        compact applique la présentation verticale des pages de restauration ;
        les autres étapes gardent leur présentation. Tous les textes sont échappés.
        """
        labels = {
            'status': 'Statut', 'description': 'Description', 'objective': 'Objectif',
            'method': 'Méthode', 'engine': 'Moteur', 'operation': 'Traitement',
            'selected_chain': 'Chaîne retenue', 'selected_engine': 'Moteur retenu',
            'selection_score': 'Score de sélection', 'output_image': 'Image produite',
            'result_image_path': 'Résultat FITS sauvegardé',
            'result_image_role': 'Nature du résultat sauvegardé',
            'input_image': 'Image d’entrée', 'image_path': 'Image de référence',
            'output_jpeg': 'Image JPEG', 'output_annotated_image': 'Image annotée',
            'verification_path': 'Compte rendu des vérifications',
            'input_mask_path': 'Masque utilisé',
            'input_mask_preview_path': 'Aperçu du masque',
            'error': 'Erreur', 'reason': 'Motif', 'rejection_reasons': 'Motifs de rejet',
            'mode': 'Mode', 'enabled': 'Activé', 'decision': 'Décision',
            'assembled': 'Mosaïque assemblée', 'images_retained': 'Poses retenues',
            'images_analysed': 'Poses analysées', 'fwhm_pixels': 'FWHM médiane (px)',
            'calibrated_subsessions': 'Sous-sessions calibrées',
            'total_subsessions': 'Nombre de sous-sessions',
        }
        excluded = {'criteria', 'parameters', 'settings', 'summary', 'score_details',
                    'comparison', 'artifacts', 'processing', 'quality_reference', 'quality'}
        sections = []
        count = [0]

        def display(value: object) -> str:
            """Préserve le format historique hors des tableaux de restauration."""
            if value is None:
                return 'UNKNOWN'
            if isinstance(value, list):
                return ', '.join(str(item) for item in value[:20])
            return f'{value:.8g}' if isinstance(value, float) else str(value)

        def table(
            headers: tuple[str, ...], rows: list[list[object]], *, key_value: bool = False,
            verdict_columns: set[int] | None = None, label_columns: set[int] | None = None,
            verdict_cells: set[tuple[int, int]] | None = None,
        ) -> str:
            """Limite la nouvelle présentation à la restauration demandée."""
            if compact:
                return cls._presentation_table(headers, rows, verdict_columns=verdict_columns,
                                               label_columns=label_columns, verdict_cells=verdict_cells)
            parts = ['<table>']
            if not key_value:
                parts.append('<tr>' + ''.join('<th>' + escape(title) + '</th>' for title in headers) + '</tr>')
            for row in rows:
                parts.append('<tr>')
                for index, value in enumerate(row):
                    tag = 'th' if key_value and index == 0 else 'td'
                    parts.append('<' + tag + '>' + escape(display(value)) + '</' + tag + '>')
                parts.append('</tr>')
            parts.append('</table>')
            return ''.join(parts)

        def visit(node: object, name: str, depth: int) -> None:
            """Parcourt au plus 25 sections et quatre niveaux de rapports imbriqués."""
            if not isinstance(node, dict) or depth > 4 or count[0] >= 25:
                return
            fields = {key: value for key, value in node.items() if key in labels}
            if not fields and depth == 0:
                fields = {key: value for key, value in node.items()
                          if isinstance(value, (str, bool, int, float)) or value is None}
            criteria = node.get('criteria')
            if not isinstance(criteria, dict) and isinstance(node.get('quality'), dict):
                criteria = node['quality'].get('criteria')
            gates = node.get('gates')
            ranking = cls._ranking_view(node)
            if (fields or isinstance(criteria, dict) or isinstance(gates, dict)
                    or ranking or isinstance(node.get('parameters'), dict)
                    or isinstance(node.get('settings'), dict)):
                count[0] += 1
                parts = [f'<h4>{escape(name)}</h4>']
                field_rows = []
                verdict_cells = set()
                for key, value in list(fields.items())[:40]:
                    if isinstance(value, dict):
                        continue
                    if isinstance(value, list) and any(isinstance(v, (dict, list)) for v in value):
                        continue
                    if key in ('status', 'decision'):
                        verdict_cells.add((len(field_rows), 1))
                    field_rows.append([labels.get(key, key), value])
                if compact and verdict_cells:
                    parts.append(cls._presentation_legend())
                parts.append(table(('Élément', 'Valeur'), field_rows, key_value=True,
                                   verdict_cells=verdict_cells))
                for group in ('parameters', 'settings'):
                    config = node.get(group)
                    if isinstance(config, dict):
                        parts.append('<h4>Paramètres enregistrés</h4>')
                        parameter_rows = []
                        for key, value in list(config.items())[:40]:
                            if isinstance(value, (str, bool, int, float)) or value is None:
                                parameter_rows.append([key, value])
                        parts.append(table(('Paramètre', 'Valeur'), parameter_rows, key_value=True))
                if isinstance(criteria, dict):
                    parts.append('<h4>Résultats des tests qualité</h4>'
                                 + (cls._presentation_legend() if compact else ''))
                    criterion_rows = []
                    for key, item in list(criteria.items())[:40]:
                        if isinstance(item, dict):
                            criterion_rows.append([key, item.get('status'), item.get('value'),
                                                   item.get('threshold'), *([item.get('blocking')] if compact else []),
                                                   item.get('reason')])
                    headers = ('Critère', 'Verdict', 'Valeur', 'Seuil', *(['Bloquant'] if compact else []), 'Motif')
                    parts.append(table(headers, criterion_rows, verdict_columns={1},
                                       label_columns={0, len(headers) - 1}))
                if isinstance(gates, dict):
                    gate_rows = [[key, 'PASS' if passed is True else 'FAIL' if passed is False else None]
                                 for key, passed in gates.items()]
                    parts.append('<h4>Conditions de décision</h4>')
                    if compact:
                        parts.append(cls._presentation_legend()
                                     + table(('Condition', 'Verdict'), gate_rows, verdict_columns={1}))
                    else:
                        parts.append('<ul>' + ''.join('<li>' + escape(str(key)) + ' : '
                                     + ('SATISFAIT' if value is True else 'NON SATISFAIT' if value is False else 'UNKNOWN')
                                     + '</li>' for key, value in gates.items()) + '</ul>')
                details = node.get('reason_details')
                if isinstance(details, list):
                    parts.append('<ul>' + ''.join('<li>' + escape(str(v['message']))
                        + '</li>' for v in details if isinstance(v, dict) and 'message' in v) + '</ul>')
                parts.append(ranking)
                sections.append(''.join(parts))
            for key, child in node.items():
                if key not in excluded and isinstance(child, dict):
                    visit(child, name + ' / ' + str(key), depth + 1)

        visit(payload, 'Synthèse du rapport', 0)
        return (''.join(sections) + cls._combinations_view(payload)
                or '<p>Aucun champ synthétique reconnu ; consulter les données détaillées.</p>')

    @staticmethod
    def _quality_guide() -> str:
        """Retourne le texte pédagogique fixe commun à toutes les pages HTML.

        Définit les mesures sans interpréter les données du rapport courant.
        Les résultats et les seuils effectifs restent dans les tableaux d'audit.
        """
        definitions = {
            'registration_validity — validité avant les filtres qualité':
                'Regroupe les conditions d’admission aux filtres qualité. '
                'Mesures finies signifie que les mesures exploitées ne contiennent '
                'ni NaN ni valeur infinie. FWHM positive signifie que la largeur '
                'stellaire non pondérée est strictement supérieure à zéro. '
                'Incluse signifie que la pose reste sélectionnée après la '
                'registration Siril et le contrôle géométrique Python. '
                'Transformation valide indique des données d’alignement valides, '
                'avec une matrice 3 × 3 finie et non singulière. Ces conditions '
                'sont liées : le contrôle géométrique a déjà retiré les '
                'transformations invalides. La colonne Validité synthétise '
                'le verdict enregistré : PASS donne une coche verte et FAIL '
                'une croix rouge. Un tiret blanc indique un contrôle non exécuté '
                'ou désactivé, et ? un résultat absent. '
                'Ce verdict précède les filtres FWHM, de rondeur et de profils '
                'stellaires. L’affichage ne le recalcule pas ; les entrées détaillées '
                'restent conservées dans l’audit interne.',
            'weighted_fwhm — FWHM pondérée':
                'Indicateur de largeur stellaire pondérée fourni par Siril, '
                'distinct de la FWHM non pondérée et du poids final d’une pose. '
                'Les filtres FWHM relatifs et le classement proportionnel '
                'utilisent cette mesure ; le plafond absolu utilise la FWHM '
                'non pondérée.',
            'fwhm_filter — filtre de FWHM pondérée':
                'Compare la FWHM pondérée au seuil enregistré : une valeur '
                'strictement supérieure est rejetée. Le réglage k calcule '
                'médiane + k × 1,4826 × médiane des écarts absolus à la médiane '
                'sur les poses encore admissibles. Le mode pourcentage utilise '
                'le percentile configuré ; une valeur numérique fixe directement '
                'le seuil. La colonne Mesure contient la FWHM pondérée.',
            'roundness_filter — filtre de rondeur des poses':
                'Compare la rondeur mesurée au seuil enregistré : une valeur '
                'strictement inférieure est rejetée. Le réglage k utilise '
                'médiane − k × 1,4826 × médiane des écarts absolus à la médiane. '
                'Le mode pourcentage définit le percentile bas correspondant '
                'à la proportion à conserver ; une valeur numérique est un '
                'seuil fixe. Les statistiques portent sur les survivantes '
                'des contrôles précédents.',
            'nbstars_filter — filtre du nombre d’étoiles':
                'Compare le nombre d’étoiles à l’intervalle enregistré '
                '[médiane − tolérance, médiane + tolérance], bornes incluses. '
                'En mode k, la tolérance vaut k × 1,4826 × médiane des écarts '
                'absolus à la médiane ; en pourcentage elle vaut cette fraction '
                'de la médiane ; une valeur numérique donne une tolérance '
                'absolue. Les deux extrêmes peuvent entraîner un rejet.',
            'pair_matched — paires appariées':
                'Nombre de couples d’étoiles que Siril a associés entre une pose '
                'et l’image de référence. Il ne s’agit pas du nombre total '
                'd’étoiles détectées. Un compteur nul peut aussi correspondre à '
                'des données non initialisées ; il ne prouve pas seul un motif de rejet.',
            'inliers — correspondances retenues':
                'Nombre de paires conservées après RANSAC, l’estimation robuste '
                'qui écarte les correspondances incompatibles avec une même '
                'transformation géométrique. Ce compteur mesure la cohérence de '
                'l’appariement. L’API ne publie pas le verdict de chaque test '
                'interne et les compteurs de la référence ne sont pas interprétés.',
            'minpairs — minimum de paires communes':
                'Minimum de paires d’étoiles exigé par Siril entre une pose '
                'et la référence pour admettre son alignement. Une pose avec '
                'un nombre insuffisant de paires est exclue. Le rapport conserve '
                'l’argument explicite lorsqu’il existe ; la valeur interne '
                'implicite et le motif détaillé par pose ne sont pas exposés par l’API.',
            'maxstars — limite de recherche des étoiles':
                'Nombre maximal d’étoiles à rechercher par pose pour la '
                'registration. Ce réglage borne le travail de détection et '
                'd’appariement ; il ne rejette pas une pose parce qu’elle '
                'contient davantage d’étoiles.',
            'registration_transform — transformation géométrique':
                'Modèle qui fait correspondre la pose au repère de la référence : '
                'translation, similitude, transformation affine ou homographie. '
                'Siril doit trouver des correspondances compatibles avec ce '
                'modèle. Une homographie est une matrice 3 × 3 ; son absence '
                'ne révèle pas à elle seule la cause physique de l’échec.',
            'detection_sigma — seuil de détection des étoiles':
                'Seuil de détection au-dessus du bruit, exprimé en multiples '
                'de sa dispersion. Il sélectionne les candidats stellaires '
                'utilisables pour l’alignement ; ce n’est pas un filtre FWHM '
                'appliqué aux poses. Sa valeur courante n’est pas exposée par '
                'les structures de registration de Sirilpy.',
            'detection_roundness — rondeur minimale de détection':
                'Limite de rondeur utilisée par Siril pour accepter un candidat '
                'stellaire lors de la détection. Elle porte sur les étoiles, '
                'alors que le filtre Python de rondeur porte ensuite sur une '
                'mesure globale par pose. Sa valeur courante n’est pas exposée '
                'par les structures de registration de Sirilpy.',
            'registration_geometry — contrôle géométrique Python':
                'Contrôle de la transformation au centre et aux quatre coins '
                'de l’image. Python exclut les mesures non finies, une FWHM '
                'non positive, une matrice singulière, une inversion '
                'd’orientation, des échelles locales hors de l’intervalle '
                'enregistré ou un champ transformé entièrement hors du cadre '
                'de référence. Les bornes et le motif effectivement retenu '
                'figurent dans l’audit ; ce contrôle suit la registration Siril.',
            'fwhm_reject_percent — Pourcentage de rejet FWHM':
                'Pourcentage des poses restantes à écarter en privilégiant les '
                'plus grandes FWHM, donc les étoiles les plus larges. Ce filtre '
                's’applique après le filtre FWHM précédent. La valeur 0 le '
                'désactive. Le nombre conservé est arrondi au supérieur et le '
                'filtre évite de descendre sous deux poses lorsque deux sont disponibles.',
            'max_fwhm — Plafond FWHM':
                'Limite absolue de largeur stellaire, en pixels, appliquée avant '
                'les filtres relatifs et la pondération. Une pose dont la FWHM '
                'dépasse strictement cette limite est écartée ; une valeur égale '
                'reste admissible. La valeur 0 désactive ce plafond.',
            'stellar_profile_filter — Contrôle des profils stellaires':
                'Active l’analyse des pixels des étoiles dans les poses encore '
                'retenues. Ce contrôle complète les mesures de registration '
                'par le rayon de flux R80 et l’allongement cohérent des étoiles '
                'dans plusieurs secteurs du champ. Il peut écarter une pose '
                'dont les profils sont excessivement étalés ou allongés. '
                'Une analyse non concluante ne suffit pas à la rejeter.',
            'stellar_profile_sigma — Coefficient des seuils de profils':
                'Coefficient k utilisé pour établir les seuils du groupe de '
                'poses comparables : médiane + k × dispersion robuste. La '
                'dispersion robuste vaut 1,4826 fois la médiane des écarts '
                'absolus à la médiane. Le seuil R80 est au moins égal à 1,25 '
                'fois sa médiane et le seuil d’allongement au moins égal à 1,5. '
                'Un coefficient plus élevé rend ces limites plus tolérantes ; '
                'ce coefficient n’est pas un seuil directement comparé à une étoile.',
            'roundness_weighted — Pondération par rondeur':
                'Accorde davantage de poids aux poses retenues dont les étoiles '
                'sont les plus rondes. Le programme calcule un nombre entier '
                'de participations de chaque pose à l’empilement, entre une '
                'participation et une participation augmentée du nombre '
                'maximal de répétitions supplémentaires configuré. '
                'Cette pondération ne rejette pas de pose et ne crée pas de '
                'nouvelles observations indépendantes.',
            'fwhm_weighted — Pondération par FWHM':
                'Accorde davantage de poids aux poses retenues dont la FWHM '
                'est la plus faible, donc les étoiles les plus fines. Pour '
                'plus de deux poses, un nombre entier de participations à '
                'l’empilement est calculé dans la limite configurée. Avec '
                'deux poses ou moins, cette pondération n’ajoute aucune '
                'participation. Elle se combine par multiplication avec '
                'la pondération de rondeur, sans ajouter de pose indépendante.',
            'fwhm — largeur stellaire de registration':
                'Largeur à mi-hauteur du profil des étoiles, en pixels. Une valeur '
                'élevée correspond à des étoiles plus étalées. Le filtre et son réglage '
                'effectif sont indiqués dans le rapport de sélection.',
            'registration_roundness — rondeur de registration':
                'Rondeur des profils stellaires mesurée par Siril pour chaque pose. '
                'Cette mesure décrit la forme des étoiles de la pose, et non sa '
                'variation après restauration.',
            'nbstars — nombre d’étoiles de registration':
                'Nombre d’étoiles détectées pour une pose lors de la registration. '
                'Le filtre vérifie sa compatibilité avec le groupe de poses ; un '
                'comptage inhabituel ne prouve pas à lui seul la cause du défaut.',
            'r80 — étalement des profils stellaires':
                'Rayon contenant 80 % du flux mesuré autour des étoiles après '
                'soustraction du fond local, en pixels natifs. Le rapport fournit '
                'la médiane par pose, le seuil du groupe et la fraction d’étoiles '
                'hors seuil. La fraction est le nombre d’étoiles hors seuil '
                'divisé par le nombre mesuré. Le rejet exige au moins 60 % '
                'd’étoiles hors seuil, réparties sur au moins trois secteurs.',
            'elongation — allongement des profils stellaires':
                'Rapport du grand axe au petit axe des profils stellaires : 1 '
                'correspond à un profil rond. Le rapport donne la médiane, le seuil '
                'et la fraction hors seuil. Cette fraction compte les étoiles '
                'dépassant le seuil et allongées dans une direction cohérente '
                'avec celle de la pose. Le rejet exige au moins 60 % des étoiles '
                'mesurées, réparties sur au moins trois secteurs.',
            'stars — étoiles appariées':
                'Mesure la proportion d’étoiles que l’on retrouve entre l’image '
                'de référence et l’image traitée. Ces étoiles communes permettent '
                'de comparer les mêmes objets avant et après le traitement.',
            'roundness — rondeur':
                'Compare la rondeur moyenne d’une sélection d’étoiles appariées '
                'avant et après traitement. La mesure représente la perte de '
                'rondeur : une valeur positive indique des étoiles moins rondes.',
            'fwhm_mean — netteté moyenne':
                'Mesure la réduction relative de la largeur moyenne des étoiles '
                '(FWHM, largeur à mi-hauteur de leur profil lumineux). '
                'Une valeur positive indique des étoiles plus fines.',
            'fwhm_median — netteté médiane':
                'Mesure le gain médian de largeur sur les étoiles appariées. '
                'La médiane limite l’influence des étoiles aux mesures extrêmes.',
            'blur — flou':
                'Mesure l’élargissement relatif des étoiles après débruitage, '
                'en retenant le moins favorable des rapports FWHM moyen et médian. '
                'Une valeur positive indique un flou supplémentaire.',
            'fwhm_significance — fiabilité du gain de netteté':
                'Estime l’incertitude du gain de netteté en rééchantillonnant '
                'des groupes d’étoiles proches. Ce contrôle indique si le gain '
                'mesuré est suffisamment étayé ; il est diagnostique.',
            'noise — bruit du ciel':
                'Compare le bruit mesuré dans les zones de ciel avant et après '
                'traitement, sur le canal le moins favorable. Un rapport de 1 '
                'indique un bruit inchangé ; moins de 1, une réduction ; plus '
                'de 1, une augmentation.',
            'useful_noise_gain — gain utile de bruit':
                'Mesure la réduction du bruit dans les zones de ciel jugées '
                'fiables, par canal, avec une estimation de son incertitude. '
                'Il distingue une baisse mesurée d’un gain suffisamment démontré.',
            'global_noise_reduction — réduction globale du bruit':
                'Compare le bruit de la chaîne complète à celui de l’image '
                'd’origine commune aux chaînes. Un rapport inférieur à 1 '
                'indique une réduction du bruit.',
            'rings — anneaux':
                'Mesure la proportion d’étoiles autour desquelles le traitement '
                'a créé des anneaux ou des artefacts lumineux ou sombres.',
            'stellar_flux — flux des étoiles':
                'Compare la quantité de lumière mesurée autour des étoiles '
                'appariées, par canal. Il vérifie que le traitement ne modifie '
                'pas excessivement leur flux photométrique.',
        }
        parts = ['<section><h2>Comprendre les critères de qualité</h2>'
                 '<p>Ce guide décrit les critères d’alignement, de qualité et de restauration. '
                 'Seuls les critères présents dans les résultats ont été audités. '
                 'Les mesures et les seuils propres au traitement figurent dans '
                 'les tableaux ; les variations relatives sont exprimées en '
                 'fractions : 0,02 correspond à 2 %.</p>']
        for title, description in definitions.items():
            parts.append('<p id="' + title.split(' — ', 1)[0] + '"><strong>' + escape(title) + '.</strong> '
                         + escape(description) + '</p>')
        parts.append(
            '<p id="diffuse"><strong>diffuse — conservation du signal diffus.</strong> '
            'Ce critère vérifie que le traitement préserve la lumière des '
            'structures étendues, comme les nébuleuses et les parties diffuses '
            'des galaxies. Les étoiles sont exclues des zones mesurées. Pour '
            'chaque région et chaque canal, le programme estime et soustrait '
            'le fond de ciel local, puis compare le flux total avant et après '
            'traitement. La variation est rapportée au flux initial : une '
            'valeur positive représente une perte, une valeur négative un gain. '
            'Une variation au-delà de la tolérance ne suffit pas à conclure : '
            'elle doit aussi être significative au regard de l’incertitude et '
            'corroborée par une analyse à plusieurs échelles.</p>'
            '<p>Pour <strong>diffuse</strong>, <strong>PASS</strong> signifie '
            'qu’aucune modification hors tolérance n’a été confirmée par ces '
            'contrôles. <strong>FAIL</strong> signale une modification excessive '
            'confirmée dans au moins une région. <strong>UNKNOWN</strong> '
            'signifie que le contrôle ne permet pas de conclure : par exemple, '
            '<strong>NO_LOCAL_SKY</strong> indique qu’il manque une zone de ciel '
            'locale fiable pour estimer le fond, et <strong>LOW_DIFFUSE_SNR</strong> '
            'que le signal diffus est trop faible par rapport au bruit. '
            'UNKNOWN ne prouve ni une perte ni la conservation du signal. '
            'Le verdict global est UNKNOWN si certaines régions restent '
            'indéterminées et qu’aucune n’est en échec. Ce critère ne rejette '
            'une chaîne en cas de FAIL que si son contrôle bloquant est activé '
            '; sinon il reste une observation.</p></section>'
        )
        return ''.join(parts)

    @staticmethod
    def _link_quality_terms(content: str, prefix: str) -> str:
        """Lie les noms de critères dans le texte HTML à leur définition commune.

        Ne modifie ni les attributs HTML, ni le contenu des liens existants.
        Le préfixe relatif rejoint la racine depuis la page courante.
        """
        names = ('registration_validity', 'weighted_fwhm', 'fwhm_filter', 'roundness_filter',
                 'nbstars_filter', 'pair_matched', 'inliers', 'minpairs', 'maxstars', 'registration_transform',
                 'detection_sigma', 'detection_roundness', 'registration_geometry', 'fwhm', 'registration_roundness', 'nbstars', 'r80', 'elongation', 'stars', 'roundness', 'fwhm_mean', 'fwhm_median', 'blur',
                 'fwhm_significance', 'noise', 'useful_noise_gain',
                 'global_noise_reduction', 'rings', 'stellar_flux', 'diffuse')
        aliases = {
            'Ratio FWHM moyen': 'fwhm_mean',
            'Ratio FWHM médian': 'fwhm_median',
            'Gain utile bruit': 'useful_noise_gain', 'Anneaux': 'rings', 'Ratio bruit': 'noise',
            'Validité': 'registration_validity',
            'Validité de registration': 'registration_validity',
            'Filtre FWHM pondérée': 'fwhm_filter',
            'Filtre de rondeur': 'roundness_filter',
            'Filtre du nombre d’étoiles': 'nbstars_filter',
            'Contrôle géométrique Python': 'registration_geometry',
            'paires appariées': 'pair_matched',
            'Contrôles géométriques enregistrés': 'registration_geometry',
            'Pourcentage de rejet FWHM': 'fwhm_reject_percent',
            'Plafond FWHM': 'max_fwhm',
            'Contrôle des profils stellaires': 'stellar_profile_filter',
            'Coefficient des seuils de profils': 'stellar_profile_sigma',
            'Pondération par rondeur': 'roundness_weighted',
            'Pondération par FWHM': 'fwhm_weighted',
        }
        terms = {name: name for name in names}
        terms.update({name: name for name in aliases.values()})
        terms.update(aliases)
        pattern = re.compile(r'(?<![\w])(' + '|'.join(
            re.escape(term) for term in sorted(terms, key=len, reverse=True)
        ) + r')(?![\w])')
        parts = re.split(r'(<[^>]*>)', content)
        in_link = False
        for index, part in enumerate(parts):
            if part.startswith('<'):
                if re.match(r'<a(?:\s|>)', part):
                    in_link = True
                elif part.startswith('</a>'):
                    in_link = False
            elif not in_link:
                parts[index] = pattern.sub(
                    lambda match: '<a href="' + prefix + 'quality-criteria.html#'
                    + terms[match.group(0)] + '">' + match.group(0) + '</a>', part)
        return ''.join(parts)

    @classmethod
    def _restoration_view(cls, payload: object) -> str:
        """Présente choix et critères en colonnes, sans recalcul scientifique.

        Les contrôles non bloquants gardent leur drapeau explicite. Les motifs
        de rejet restent enregistrés séparément du score et de l'admissibilité.
        Les absences de valeurs agrégées ne sont pas comblées par des calculs.
        """
        if not isinstance(payload, dict):
            return '<p>Rapport de restauration invalide.</p>'
        if 'variants' not in payload and isinstance(payload.get('restoration'), dict):
            return cls._summary_view(payload, compact=True)
        summary = payload.get('summary')
        summary = summary if isinstance(summary, dict) else {}
        selected = payload.get('selected_chain')
        label = (' → '.join(map(str, selected)) if isinstance(selected, list) and selected
                 else 'image inchangée' if selected == [] else 'choix non enregistré')
        variants = summary.get('rows', payload.get('variants', []))
        variants = [row for row in variants if isinstance(row, dict)] if isinstance(variants, list) else []
        criteria_names = list(dict.fromkeys(
            str(name) for row in variants if isinstance(row.get('criteria'), dict)
            for name in row['criteria']
        ))
        fields_by_criterion = {}
        for name in criteria_names:
            blocking = any(isinstance(row.get('criteria'), dict)
                           and isinstance(item := row['criteria'].get(name), dict)
                           and 'blocking' in item for row in variants)
            fields_by_criterion[name] = ['value', 'threshold', *(['blocking'] if blocking else []), 'status']
        parts = ['<section><h3>Choix de la chaîne</h3><p>' + escape(label) + '</p>',
                 cls._ranking_view(payload), '<h4>Décision et contrôles par combinaison</h4>',
                 cls._presentation_legend(),
                 '<div class="criteria-scroll"><table class="criteria-table presentation-table">'
                 '<thead><tr>']
        for title in ('Chaîne', 'Admissibilité', 'Choix', 'Score'):
            parts.append(cls._vertical_header(title, rowspan=2, group_start=True))
        for name, fields in fields_by_criterion.items():
            parts.append(cls._vertical_header(name, colspan=len(fields), group_start=True))
        parts.append(cls._vertical_header('Motif enregistré', rowspan=2, group_start=True) + '</tr><tr>')
        for fields in fields_by_criterion.values():
            for field in fields:
                title = {'value': 'Valeur', 'threshold': 'Seuil', 'blocking': 'Bloquant', 'status': 'Verdict'}[field]
                parts.append(cls._vertical_header(title, group_start=field == fields[0]))
        parts.append('</tr></thead><tbody>')
        for row in variants:
            chain = row.get('chain')
            name = row.get('label') or (' → '.join(map(str, chain))
                                      if isinstance(chain, list) and chain else 'image inchangée')
            decision = row.get('decision')
            if decision is None and isinstance(selected, list) and isinstance(chain, list):
                decision = 'selected' if chain == selected else ('lower_score' if row.get('status') == 'accepted'
                                                                 else row.get('status'))
            admissibility = 'admissible' if row.get('status') == 'accepted' else row.get('status')
            cells = [name, admissibility, decision, row.get('selection_score')]
            verdict_columns = {1, 2}
            group_starts = {0, 1, 2, 3}
            criteria = row.get('criteria', {})
            criteria = criteria if isinstance(criteria, dict) else {}
            for key, fields in fields_by_criterion.items():
                group_starts.add(len(cells))
                check = criteria.get(key)
                check = check if isinstance(check, dict) else {}
                for field in fields:
                    if field == 'status':
                        verdict_columns.add(len(cells))
                    cells.append(check.get(field))
            reasons = row.get('reasons') or row.get('reason') or row.get('rejection_reasons')
            group_starts.add(len(cells))
            cells.append(reasons or None)
            parts.append('<tr>')
            for index, value in enumerate(cells):
                classes = ['criteria-group-start'] if index in group_starts else []
                if index in (0, len(cells) - 1):
                    classes.append('table-label')
                attribute = ' class="' + ' '.join(classes) + '"' if classes else ''
                content = (cls._selection_symbol(value)
                           if index in verdict_columns
                           else escape(cls._presentation_value(value)))
                parts.append('<td' + attribute + '>' + content + '</td>')
            parts.append('</tr>')
        parts.append('</tbody></table></div></section>')
        return ''.join(parts)

    @staticmethod
    def _vertical_header(
        title: str, *, rowspan: int = 1, colspan: int | None = None,
        group_start: bool = False,
    ) -> str:
        """Oriente à 90° un titre échappé, simple ou regroupant des colonnes."""
        span = f' rowspan="{rowspan}"' if rowspan > 1 else ''
        span += f' colspan="{colspan}"' if colspan is not None else ''
        classes = 'criteria-vertical criteria-group-start' if group_start else 'criteria-vertical'
        return ('<th class="' + classes + '"' + span + '><span><span>'
                + escape(title) + '</span></span></th>')

    @classmethod
    def _presentation_value(cls, value: object, depth: int = 0) -> str:
        """Formate les mesures composées à deux décimales, sans données brutes illimitées."""
        if depth > 3:
            return '…'
        if isinstance(value, dict):
            return ', '.join(str(key) + ' : ' + cls._presentation_value(item, depth + 1)
                             for key, item in list(value.items())[:20])
        if isinstance(value, (list, tuple)):
            return ', '.join(cls._presentation_value(item, depth + 1) for item in value[:20])
        return cls._quality_value(value)

    @classmethod
    def _presentation_legend(cls) -> str:
        """Explique les symboles sans confondre rejet et chaîne acceptée non retenue."""
        return ('<p class="quality-legend"><strong>Légende :</strong> '
                + cls._selection_symbol('PASS') + ' critère satisfait ou chaîne retenue ; '
                + cls._selection_symbol('SKIPPED')
                + ' contrôle non exécuté/désactivé, référence ou chaîne acceptée non retenue ; '
                + cls._selection_symbol('FAIL') + ' critère non satisfait ou chaîne rejetée ; '
                '? résultat non enregistré. Un contrôle non bloquant peut échouer '
                'sans rejeter la chaîne ; la colonne Bloquant indique 1 pour oui et 0 pour non.</p>')

    @classmethod
    def _presentation_table(
        cls, headers: tuple[str, ...] | list[str], rows: list[list[object]], *,
        verdict_columns: set[int] | None = None, label_columns: set[int] | None = None,
        verdict_cells: set[tuple[int, int]] | None = None,
    ) -> str:
        """Présente les valeurs enregistrées avec titres verticaux et grille commune.

        Seules les colonnes explicitement déclarées comme verdict sont remplacées
        par des symboles ; les données et libellés restent échappés.
        """
        verdict_columns = verdict_columns or set()
        verdict_cells = verdict_cells or set()
        label_columns = {0} if label_columns is None else label_columns
        parts = ['<div class="criteria-scroll"><table class="criteria-table presentation-table">'
                 '<thead><tr>', *(cls._vertical_header(title) for title in headers),
                 '</tr></thead><tbody>']
        for row_number, row in enumerate(rows):
            parts.append('<tr>')
            for index, value in enumerate(row):
                classes = []
                if index in label_columns:
                    classes.append('table-label')
                elif isinstance(value, (str, list, tuple, dict)) and index not in verdict_columns:
                    classes.append('table-text')
                attribute = ' class="' + ' '.join(classes) + '"' if classes else ''
                content = (cls._selection_symbol(value)
                           if index in verdict_columns or (row_number, index) in verdict_cells
                           else escape(cls._presentation_value(value)))
                parts.append('<td' + attribute + '>' + content + '</td>')
            parts.append('</tr>')
        parts.append('</tbody></table></div>')
        return ''.join(parts)

    @staticmethod
    def _selection_symbol(status: object) -> str:
        """Affiche une sélection ou un verdict par un symbole coloré accessible.

        Les contrôles ignorés et désactivés partagent un tiret blanc, avec un
        libellé distinct au survol et pour les lecteurs d'écran. Les états
        absents ou non concluants ne deviennent jamais des succès ou rejets.
        Les symboles et classes proviennent de cette table fixe ; les autres
        données restent échappées sans modifier l'audit source.
        """
        markers = {
            'accepted': ('selection-kept', 'Retenue', '✓'),
            'retained': ('selection-kept', 'Retenue', '✓'),
            'rejected': ('selection-rejected', 'Écartée', '✗'),
            'excluded': ('selection-rejected', 'Écartée', '✗'),
            'PASS': ('selection-kept', 'Satisfait (PASS)', '✓'),
            'FAIL': ('selection-rejected', 'Rejet (FAIL)', '✗'),
            'FAILED': ('selection-rejected', 'Rejet (FAILED)', '✗'),
            'SKIPPED': ('selection-skipped', 'Non évalué : exclusion antérieure (SKIPPED)', '-'),
            'DISABLED': ('selection-skipped', 'Désactivé (DISABLED)', '-'),
            'admissible': ('selection-kept', 'Chaîne admissible', '✓'),
            'selected': ('selection-kept', 'Chaîne retenue', '✓'),
            'RETENUE': ('selection-kept', 'Chaîne retenue', '✓'),
            'lower_score': ('selection-skipped', 'Chaîne acceptée, score inférieur au meilleur', '-'),
            'SCORE INFÉRIEUR AU MEILLEUR': ('selection-skipped', 'Chaîne acceptée, score inférieur au meilleur', '-'),
            'ACCEPTÉE NON RETENUE': ('selection-skipped', 'Chaîne acceptée non retenue', '-'),
            'reference': ('selection-skipped', 'Image de référence', '-'),
            'local_rejection': ('selection-rejected', 'Rejet lors d’un contrôle local', '✗'),
            'final_rejection': ('selection-rejected', 'Rejet lors du contrôle final', '✗'),
            'CHOIX NON ENREGISTRÉ': ('selection-skipped', 'Choix non enregistré', '?'),
        }
        marker = markers.get(status) if isinstance(status, str) else None
        if marker is None:
            return escape(ProcessingHtmlReport._quality_value(status))
        style, label, symbol = marker
        return ('<span class="selection-mark ' + style + '" role="img" '
                'aria-label="' + label + '" title="' + label + '">' + symbol + '</span>')

    @staticmethod
    def _quality_value(value: object) -> str:
        """Formate les flottants à deux décimales et les absences par ?.

        Ce format de présentation ne modifie pas les valeurs de l'audit ni
        les verdicts. Les entiers gardent leur écriture entière, les booléens
        utilisent 1/0 et les flottants non finis sont indisponibles.
        """
        if value is None or isinstance(value, str) and value == 'UNKNOWN':
            return '?'
        if isinstance(value, bool):
            return '1' if value else '0'
        if isinstance(value, float):
            return f'{value:.2f}' if math.isfinite(value) else '?'
        return str(value)

    @staticmethod
    def _profile_thresholds(
        references: object, frames: list[dict],
    ) -> tuple[str, list[str]]:
        """Présente les limites une fois par groupe et résout les références.

        Accepte les nouveaux reference_id et les anciens limits par image.
        Une référence absente reste ? ; aucune limite n'est recalculée.
        """
        groups = []
        if isinstance(references, list):
            for index, reference in enumerate(references, start=1):
                if not isinstance(reference, dict):
                    continue
                groups.append(dict(
                    identifier=reference.get('reference_id', index),
                    group=reference.get('group'),
                    limits={key: value.get('limit') for key in ('r80', 'elongation')
                            if isinstance(value := reference.get(key), dict)},
                ))
        labels = []
        for frame in frames:
            identifier = frame.get('reference_id')
            limits = frame.get('limits')
            if identifier is not None:
                matches = [group for group in groups if group['identifier'] == identifier]
            elif isinstance(limits, dict) and limits:
                matches = [group for group in groups if group['limits'] == limits]
                exact = [group for group in matches if group.get('group') == frame.get('group')]
                matches = exact or matches
                if not matches:
                    group = dict(identifier='ancien-' + str(len(groups) + 1), limits=limits)
                    groups.append(group)
                    matches = [group]
            else:
                matches = []
            labels.append(', '.join('Groupe ' + str(group['identifier']) for group in matches)
                          or '?')
        parts = ['<h4>Seuils communs des profils stellaires par groupe</h4>'
                 '<p>Chaque seuil s’applique à toutes les poses du groupe indiqué. '
                 'Les groupes rassemblent des images de caractéristiques comparables.</p>'
                 '<table><tr><th>Groupe</th><th>Seuil r80 (px natifs)</th>'
                 '<th>Seuil elongation</th></tr>']
        for group in groups:
            values = ['Groupe ' + str(group['identifier']), group['limits'].get('r80'),
                      group['limits'].get('elongation')]
            parts.append('<tr>' + ''.join('<td>' + escape(ProcessingHtmlReport._quality_value(value))
                                         + '</td>' for value in values) + '</tr>')
        parts.append('</table>')
        if not groups:
            parts.append('<p>Seuils de profils non enregistrés.</p>')
        return ''.join(parts), labels

    @staticmethod
    def _decision_trace_view(
        frames: object, references: object = None, directory: Path | None = None,
    ) -> str:
        """Présente les critères en colonnes, sans texte de règle par pose.

        Les mesures, entrées, rangs et verdicts sont lus dans le JSON. Les
        seuils identiques sont affichés une fois avant les poses ; des seuils
        variables restent dans leurs colonnes propres. Tous les titres sont
        verticaux, y compris les groupes, sans élargir les colonnes de valeurs.
        Les contrôles répétés
        sont distingués par étape et occurrence, sans recalcul des décisions.
        La validité initiale utilise les mêmes symboles que les autres verdicts ; ses
        sous-conditions restent documentées dans le guide et dans l'audit.
        """
        if not isinstance(frames, list):
            return '<p>Décisions Python par image non enregistrées dans cet ancien rapport.</p>'

        def display(value: object) -> str:
            """Formate une donnée enregistrée sans remplacer une absence par zéro."""
            if isinstance(value, dict):
                names = {'median': 'Médiane', 'tolerance': 'Tolérance',
                         'lower': 'Borne basse', 'upper': 'Borne haute',
                         'percent': 'Pourcentage', 'retained_count': 'Nombre à conserver',
                         'min_scale': 'Échelle minimale', 'max_scale': 'Échelle maximale'}
                return ', '.join(names.get(str(key), str(key)) + ' : ' + display(item)
                                 for key, item in value.items())
            if isinstance(value, list):
                return ', '.join(display(item) for item in value)
            return ProcessingHtmlReport._quality_value(value)

        def flatten(check: dict) -> dict[str, object]:
            """Aplatit uniquement les données du contrôle, en excluant son texte."""
            values = {}
            for field in ('value', 'rank', 'inputs', 'threshold'):
                value = check.get(field)
                if isinstance(value, dict):
                    values.update({field + '.' + str(key): item for key, item in value.items()})
                else:
                    values[field] = value
            values['status'] = check.get('status')
            return values

        vertical_header = ProcessingHtmlReport._vertical_header

        frames = [frame for frame in frames if isinstance(frame, dict)]
        criteria: list[tuple[str, str, int]] = []
        checks_by_frame: list[dict[tuple[str, str, int], dict]] = []
        for frame in frames:
            indexed = {}
            occurrences = {}
            checks = frame.get('checks', [])
            for check in checks if isinstance(checks, list) else []:
                if not isinstance(check, dict):
                    continue
                identity = (str(check.get('criterion', '?')), str(check.get('stage') or ''))
                occurrence = occurrences.get(identity, 0)
                occurrences[identity] = occurrence + 1
                key = (*identity, occurrence)
                indexed[key] = check
                if key not in criteria:
                    criteria.append(key)
            checks_by_frame.append(indexed)

        profiles = [frame.get('profile') if isinstance(frame.get('profile'), dict) else {}
                    for frame in frames]
        thresholds_html, group_labels = ProcessingHtmlReport._profile_thresholds(references, profiles)
        labels = {
            'registration_validity': 'Validité',
            'max_fwhm': 'Plafond FWHM', 'fwhm_filter': 'Filtre FWHM pondérée',
            'fwhm_reject_percent': 'Pourcentage de rejet FWHM',
            'roundness_filter': 'Filtre de rondeur', 'nbstars_filter': 'Filtre du nombre d’étoiles',
            'registration_geometry': 'Contrôle géométrique Python',
        }
        field_labels = {
            'value': 'Mesure', 'rank': 'Rang', 'status': 'Verdict', 'threshold': 'Seuil',
            'inputs.finite_measurements': 'Mesures finies',
            'inputs.positive_fwhm': 'fwhm positive', 'inputs.included': 'Incluse',
            'inputs.registration_valid': 'Transformation valide',
            'threshold.lower': 'Seuil bas', 'threshold.upper': 'Seuil haut',
            'threshold.median': 'Médiane', 'threshold.tolerance': 'Tolérance',
            'threshold.percent': 'Pourcentage', 'threshold.retained_count': 'Nombre à conserver',
            'threshold.min_scale': 'Échelle minimale', 'threshold.max_scale': 'Échelle maximale',
        }
        groups = []
        common = []
        for key in criteria:
            name, stage, occurrence = key
            title = labels.get(name, name)
            if stage:
                title += ' — ' + stage
            if occurrence:
                title += ' — ' + str(occurrence + 1)
            checks = [indexed[key] for indexed in checks_by_frame if key in indexed]
            if name == 'registration_validity':
                groups.append((key, title, ['status']))
                continue
            thresholds = []
            for check in checks:
                threshold = check.get('threshold')
                if threshold is not None and threshold not in thresholds:
                    thresholds.append(threshold)
            disabled = all(check.get('status') == 'DISABLED' for check in checks)
            shared = len(thresholds) <= 1
            common.append((title, display(thresholds[0]) if len(thresholds) == 1 else
                           'Variables selon la pose' if thresholds else '?',
                           'DÉSACTIVÉ' if disabled else 'ACTIF'))
            fields = []
            if not disabled:
                for check in checks:
                    for field, value in flatten(check).items():
                        if field == 'status' or value is None:
                            continue
                        if shared and (field == 'threshold' or field.startswith('threshold.')):
                            continue
                        if field not in fields:
                            fields.append(field)
            fields.append('status')
            groups.append((key, title, fields))

        parts = ['<section><h3>Décisions Python pour chaque image</h3>']
        if common:
            parts.append('<h4>Seuils enregistrés des contrôles</h4><table><tr>'
                         '<th>Critère</th><th>Seuil ou paramètres communs</th><th>Activation</th></tr>')
            for row in common:
                parts.append('<tr>' + ''.join('<td>' + escape(value) + '</td>' for value in row) + '</tr>')
            parts.append('</table>')
        group_starts = {0, 1, 2}
        verdict_columns = {1, 2}
        column = 3
        for key, _, fields in groups:
            group_starts.add(column)
            verdict_columns.add(column + fields.index('status'))
            column += len(fields)
        group_starts.update((column, column + 8))
        verdict_columns.add(column + 7)  # Verdict du contrôle des profils.

        parts.append(thresholds_html)
        parts.append('<p class="quality-legend"><strong>Légende :</strong> '
                     + ProcessingHtmlReport._selection_symbol('PASS')
                     + ' critère satisfait ou pose retenue ; '
                     + ProcessingHtmlReport._selection_symbol('SKIPPED')
                     + ' NON ÉVALUÉ : EXCLUSION ANTÉRIEURE ou DÉSACTIVÉ ; '
                     + ProcessingHtmlReport._selection_symbol('FAIL')
                     + ' critère non satisfait ou pose écartée ; '
                     '? résultat non enregistré. '
                     'INCONCLUSIVE : contrôle non concluant. Valeurs logiques : 1 = oui, 0 = non.</p>')
        parts.append('<div class="criteria-scroll"><table class="criteria-table"><thead><tr>'
                     + vertical_header('Pose', rowspan=2, group_start=True)
                     + vertical_header('Sélection qualité', rowspan=2, group_start=True)
                     + vertical_header('Sélection finale', rowspan=2, group_start=True))
        for key, title, fields in groups:
            if key[0] == 'registration_validity':
                parts.append(vertical_header(title, rowspan=2, group_start=True))
            else:
                parts.append(vertical_header(title, colspan=len(fields), group_start=True))
        parts.append(vertical_header('Contrôle des profils stellaires', colspan=8, group_start=True)
                     + vertical_header('Pondération', colspan=4, group_start=True) + '</tr><tr>')
        for key, _, fields in groups:
            if key[0] == 'registration_validity':
                continue
            for field in fields:
                title = field_labels.get(field, field)
                if field == 'value':
                    title = {'registration_validity': 'fwhm (px)', 'max_fwhm': 'fwhm (px)',
                             'fwhm_filter': 'weighted_fwhm', 'fwhm_reject_percent': 'weighted_fwhm',
                             'roundness_filter': 'registration_roundness',
                             'nbstars_filter': 'nbstars'}.get(key[0], title)
                parts.append(vertical_header(title, group_start=field == fields[0]))
        for title in ('Groupe des seuils', 'Étoiles mesurées', 'Secteurs', 'r80 (px natifs)',
                      'Fraction r80', 'elongation', 'Fraction elongation', 'Verdict',
                      'Pondération par rondeur', 'Pondération par FWHM',
                      'Multiplicité totale', 'Entrées pondérées'):
            parts.append(vertical_header(title, group_start=title in ('Groupe des seuils', 'Pondération par rondeur')))
        parts.append('</tr></thead><tbody>')
        for position, frame in enumerate(frames):
            parts.append('<tr><td class="criteria-group-start">' + ProcessingHtmlReport._source_link(
                frame.get('source') or 'Image ' + str(frame.get('number', '?')), directory) + '</td>')
            cells = [frame.get('status'), frame.get('final_status')]
            for key, _, fields in groups:
                values = flatten(checks_by_frame[position].get(key, {}))
                cells.extend(values.get(field) for field in fields)
            profile = profiles[position]
            profile_status = profile.get('status', frame.get('profile_status'))
            profile_status = {'accepted': 'PASS', 'rejected': 'FAIL',
                              'inconclusive': 'INCONCLUSIVE', 'unknown': 'INCONCLUSIVE',
                              'insufficient_reference': 'INCONCLUSIVE',
                              'insufficient_stars': 'INCONCLUSIVE'}.get(profile_status, profile_status)
            cells.extend([group_labels[position].replace('Groupe ', ''), profile.get('stars_measured'),
                          profile.get('sectors'), profile.get('r80'), profile.get('r80_fraction'),
                          profile.get('elongation'), profile.get('elongation_fraction'),
                          profile_status])
            weighting = frame.get('weighting', {})
            weighting = weighting if isinstance(weighting, dict) else {}
            cells.extend(weighting.get(key) for key in ('roundness_multiplicity', 'fwhm_multiplicity',
                                                       'quality_multiplicity', 'effective_stack_entries'))
            for column, cell in enumerate(cells, start=1):
                boundary = ' class="criteria-group-start"' if column in group_starts else ''
                content = (ProcessingHtmlReport._selection_symbol(cell) if column in verdict_columns
                           else escape(display(cell)))
                parts.append('<td' + boundary + '>' + content + '</td>')
            parts.append('</tr>')
        parts.append('</tbody></table></div></section>')
        return ''.join(parts)

    @staticmethod
    def _selection_flow_rows(payload: dict) -> list[dict]:
        """Lit les bilans par étape ou les agrégats certains des anciens JSON.

        Les anciens rapports ne permettent pas de séparer les filtres qualité.
        Leur entrée de géométrie est connue par restantes + rejets, seulement
        lorsque les entrées sont indépendantes, sans répétitions de pondération.
        """
        flow = payload.get('selection_flow')
        if isinstance(flow, list):
            return [row for row in flow if isinstance(row, dict)]
        metadata = payload.get('metadata', {})
        geometry = payload.get('geometry_checks', [])
        if not isinstance(metadata, dict) or not isinstance(geometry, list) or not geometry:
            return []
        first = geometry[0]
        if not isinstance(first, dict) or not isinstance(first.get('remaining'), int):
            return []
        total = metadata.get('unique_inputs')
        if not isinstance(total, int) or metadata.get('effective_inputs', total) != total:
            return []
        rejected = first.get('rejected')
        if not isinstance(rejected, list):
            return []
        aligned = first['remaining'] + len(rejected)
        if not 0 <= aligned <= total:
            return []
        rows = [dict(stage='registration', label='Registration Siril', input_count=total,
                     rejected_count=total-aligned, remaining_count=aligned),
                dict(stage='registration_geometry', label='Géométrie Python', input_count=aligned,
                     rejected_count=len(rejected), remaining_count=first['remaining'])]
        profiles = payload.get('stellar_profiles', {})
        if isinstance(profiles, dict):
            profile_input = profiles.get('input_count')
            remaining, removed = profiles.get('remaining'), profiles.get('rejected')
            if profile_input is None and isinstance(remaining, int) and isinstance(removed, int):
                profile_input = remaining + removed
            if isinstance(profile_input, int) and 0 <= profile_input <= first['remaining']:
                rows.append(dict(stage='quality_filters', label='Filtres qualité précédant les profils',
                                 input_count=first['remaining'], rejected_count=first['remaining']-profile_input,
                                 remaining_count=profile_input))
                if isinstance(remaining, int) and isinstance(removed, int):
                    rows.append(dict(stage='stellar_profiles', label='Profils stellaires',
                                     input_count=profile_input, rejected_count=removed,
                                     remaining_count=remaining))
        return rows

    @staticmethod
    def _selection_flow_view(rows: list[dict]) -> str:
        """Affiche un entonnoir d'effectifs sans confondre les étapes."""
        if not rows:
            return '<p>Effectifs par étape non enregistrés dans cet ancien rapport.</p>'
        parts = ['<section><h3>Effectifs par étape</h3><table><tr><th>Étape</th>'
                 '<th>Entrée</th><th>Écartées</th><th>Restantes</th></tr>']
        expanded = []
        for row in rows:
            children = row.get('children')
            if isinstance(children, list) and children:
                expanded.extend(child for child in children if isinstance(child, dict)
                                and child.get('enabled', True))
            else:
                expanded.append(row)
        for row in expanded:
            values = [row.get('label', row.get('stage', '?')), row.get('input_count'),
                      row.get('rejected_count'), row.get('remaining_count')]
            parts.append('<tr>' + ''.join('<td>' + escape(ProcessingHtmlReport._quality_value(value))
                                         + '</td>' for value in values) + '</tr>')
        parts.append('</table></section>')
        return ''.join(parts)

    @staticmethod
    def _siril_registration_view(payload: object, directory: Path) -> str:
        """Présente les faits API et les réglages sans inventer un motif de rejet.

        Les seuils communs sont affichés une fois par appel. Les compteurs
        d'appariement de l'image de référence ne sont pas interprétés comme un
        échec. Un audit incomplet reste explicitement visible, sans faux succès.
        """
        if not isinstance(payload, dict) or not isinstance(payload.get('events'), list):
            return '<p>Données détaillées Sirilpy non enregistrées pour ce traitement.</p>'
        parts = ['<section><h3>Registration : mesures et décisions par image</h3>'
                 '<p>Les paires appariées associent les mêmes étoiles entre une pose '
                 'et la référence. Les inliers sont les correspondances retenues par '
                 'RANSAC pour calculer la transformation. Sirilpy ne fournit pas de '
                 'code individuel expliquant chaque rejet ; un compteur nul ne '
                 'prouve pas à lui seul sa cause. Les filtres Python interviennent '
                 'séparément après ces résultats.</p>']
        for event in payload['events']:
            if not isinstance(event, dict):
                continue
            parts.append('<h4>Alignement de ' + escape(str(event.get('sequence', event.get('requested_sequence', '?')))) + '</h4>')
            if event.get('data_source') == 'siril_sequence':
                parts.append('<p>Source : séquence enregistrée par Siril. La lecture '
                             'de séquence via Sirilpy est indisponible dans ce mode '
                             'd’exécution. Les compteurs de paires et d’inliers ne '
                             'sont pas enregistrés dans ce format : ?.</p>')
            elif event.get('data_source') == 'sirilpy':
                parts.append('<p>Source : API publique Sirilpy, état capturé en mémoire.</p>')
            if event.get('command_succeeded') is False:
                parts.append('<p class="warning">Commande Siril échouée : '
                             + escape(str(event.get('command_error', 'motif absent'))) + '</p>')
            if event.get('complete') is not True:
                parts.append('<p class="warning">Audit API incomplet : '
                             + escape(str(event.get('error', 'données manquantes'))) + '</p>')
            parameters = event.get('parameters', {})
            if isinstance(parameters, dict):
                parts.append('<p>Réglages communs à cet alignement :</p><ul>')
                for key, label in [('transformation', 'registration_transform'),
                                   ('minpairs', 'minpairs'), ('maxstars', 'maxstars')]:
                    value = parameters.get(key)
                    text = str(value) + ' (argument explicite)' if value is not None else 'valeur interne Siril non exposée'
                    parts.append('<li>' + label + ' : ' + escape(text) + '</li>')
                parts.append('<li>Canal API : ' + escape(str(event.get('channel', '?')))
                             + ' ; recherche de référence en deux passes : '
                             + ('oui' if parameters.get('two_pass') else 'non')
                             + ' ; traitement des seules poses sélectionnées : '
                             + ('oui' if parameters.get('selected_only') else 'non') + '.</li></ul>')
                parts.append('<p>La détection utilise notamment detection_sigma et '
                             'detection_roundness. Leurs valeurs courantes ne sont '
                             'pas exposées par cette API et ne sont pas déduites du journal. '
                             'minpairs est une règle d’admission ; maxstars limite la '
                             'recherche et ne constitue pas un seuil de rejet par image.</p>')
            if event.get('input_count') is not None:
                parts.append('<p>Images dans la séquence : ' + escape(str(event['input_count']))
                             + ' ; incluses selon Siril : ' + escape(str(event.get('included_count')))
                             + ' ; exclues selon Siril : ' + escape(str(event.get('excluded_count'))) + '.</p>')
            parts.append('<p>Le drapeau ci-dessous décrit l’état après la commande. '
                         'Avec l’option de traitement des seules poses sélectionnées, '
                         'les exclusions antérieures peuvent rester présentes. Une FWHM '
                         'nulle indique une mesure non initialisée ; les compteurs de '
                         'la référence peuvent rester nuls.</p><table><tr><th>Pose</th>'
                         '<th>Décision Siril</th><th>fwhm (px)</th>'
                         '<th>registration_roundness</th><th>nbstars</th>'
                         '<th>pair_matched</th><th>inliers</th><th>Information disponible</th></tr>')
            frames = event.get('frames', [])
            for frame in frames if isinstance(frames, list) else []:
                if not isinstance(frame, dict):
                    continue
                included = frame.get('included')
                status = 'INCLUSE' if included is True else 'EXCLUE' if included is False else '?'
                notes = []
                if frame.get('is_reference'):
                    notes.append('Image de référence : compteurs d’appariement non interprétés')
                if frame.get('registration_available') is False:
                    notes.append('Données de registration absentes')
                if included is False:
                    notes.append('Motif détaillé non exposé par Sirilpy')
                measurements = frame.get('measurements', {})
                measurements = measurements if isinstance(measurements, dict) else {}
                values = [measurements.get(key) for key in ('fwhm', 'roundness', 'nbstars', 'pair_matched', 'inliers')]
                parts.append('<tr><td>' + ProcessingHtmlReport._source_link(frame.get('source'), directory)
                             + '</td><td>' + status + '</td>'
                             + ''.join('<td>' + escape(ProcessingHtmlReport._quality_value(value)) + '</td>' for value in values)
                             + '<td>' + escape(' ; '.join(notes) or '—') + '</td></tr>')
            parts.append('</table>')
        parts.append('</section>')
        return ''.join(parts)

    @staticmethod
    def _registration_view(payload: object, directory: Path) -> str:
        """Présente uniquement la sortie Siril, avant toute sélection qualité."""
        if not isinstance(payload, dict):
            return '<p>Rapport de registration invalide.</p>'
        row = dict(stage='registration', label='Registration Siril',
                   input_count=payload.get('input_count'),
                   rejected_count=payload.get('rejected_count'),
                   remaining_count=payload.get('aligned_count', payload.get('remaining_count')))
        parts = [ProcessingHtmlReport._selection_flow_view([row]),
                 '<p>Ces résultats décrivent l’alignement initial Siril. Les filtres '
                 'qualité sont appliqués ensuite, dans 03_quality.</p>']
        if isinstance(payload.get('siril_api'), dict):
            invalid = payload.get('invalid_measurements_count')
            if isinstance(invalid, int) and invalid:
                parts.append('<p>Poses incluses par Siril dont les mesures ou la '
                             'transformation sont invalides : ' + str(invalid)
                             + '. Ce diagnostic est traité par le contrôle géométrique Python '
                             'suivant et ne constitue pas une exclusion décidée par Siril.</p>')
            parts.append(ProcessingHtmlReport._siril_registration_view(payload['siril_api'], directory))
            return ''.join(parts)
        parts.append('<p>Règles de registration : minpairs définit le minimum de '
                     'paires communes ; maxstars borne la recherche ; '
                     'registration_transform désigne le modèle géométrique. '
                     'Leurs valeurs effectives ne sont pas enregistrées dans cet ancien audit.</p>'
                     '<table><tr><th>Pose</th><th>Résultat de registration</th>'
                     '<th>fwhm (px)</th><th>registration_roundness</th><th>nbstars</th>'
                     '<th>Motifs enregistrés</th></tr>')
        frames = payload.get('frames', [])
        for frame in frames if isinstance(frames, list) else []:
            if not isinstance(frame, dict):
                continue
            reasons = frame.get('reasons', [])
            if isinstance(reasons, list):
                labels = {'excluded_by_siril': 'Exclusion Siril : motif détaillé non fourni',
                          'invalid_registration': 'Diagnostic Python : mesures ou transformation invalides'}
                reasons = ', '.join(labels.get(str(reason), str(reason)) for reason in reasons) or '—'
            measurements = frame.get('measurements', {})
            measurements = measurements if isinstance(measurements, dict) else {}
            status = {'aligned': 'INCLUSE', 'excluded': 'EXCLUE'}.get(frame.get('status'), str(frame.get('status', '?')))
            parts.append('<tr><td>' + ProcessingHtmlReport._source_link(frame.get('source'), directory)
                         + '</td><td>' + escape(status) + '</td>'
                         + ''.join('<td>' + escape(ProcessingHtmlReport._quality_value(measurements.get(key)))
                                   + '</td>' for key in ('fwhm', 'roundness', 'nbstars'))
                         + '<td>' + escape(str(reasons)) + '</td></tr>')
        parts.append('</table>')
        parts.append(ProcessingHtmlReport._siril_registration_view(payload.get('siril_api'), directory))
        return ''.join(parts)

    @staticmethod
    def _selection_view(payload: object, directory: Path | None = None) -> str:
        """Présente les sélections enregistrées, sans déduire les rejets absents.

        Sépare le bilan global des poses indépendantes, les entrées pondérées
        et les mesures des survivantes. Les paramètres sont ceux du JSON ;
        les seuils numériques non publiés ne sont jamais recalculés.
        """
        if not isinstance(payload, dict) or not isinstance(payload.get('quality_selection'), dict):
            return ''
        selection = payload['quality_selection']
        metadata = payload.get('metadata', {})
        metadata = metadata if isinstance(metadata, dict) else {}
        parts = ['<section><h3>Bilan global après registration et qualité</h3>',
                 ProcessingHtmlReport._selection_flow_view(ProcessingHtmlReport._selection_flow_rows(payload)), '<p>']
        for label, value in [
            ('Inventaire initial des poses indépendantes', metadata.get('unique_inputs')),
            ('Retenues', selection.get('independent_retained')),
            ('Écartées au total', selection.get('rejected')),
            ('Entrées pondérées pour l’empilement', selection.get('effective_stack_entries')),
        ]:
            parts.append(escape(label) + ' : ' + escape(ProcessingHtmlReport._quality_value(value)) + '. ')
        parts.append('</p><p>Le total des rejets peut inclure des échecs d’alignement '
                     'et des exclusions de qualité. Il ne fournit pas le motif '
                     'individuel des poses absentes de la liste finale. Les répétitions '
                     'de pondération ne sont pas des poses indépendantes supplémentaires.</p>')
        settings = payload.get('settings', {})
        if isinstance(settings, dict):
            labels = {
                'max_fwhm': 'Plafond FWHM (0 : désactivé)',
                'fwhm_filter': 'Filtre fwhm',
                'fwhm_reject_percent': 'Pourcentage de rejet FWHM',
                'roundness_filter': 'Filtre registration_roundness',
                'nbstars_filter': 'Filtre nbstars',
                'stellar_profile_filter': 'Contrôle des profils stellaires',
                'stellar_profile_sigma': 'Coefficient des seuils de profils',
                'roundness_weighted': 'Pondération par rondeur',
                'fwhm_weighted': 'Pondération par FWHM',
            }
            parts.append('<h4>Filtres configurés dans le rapport</h4><ul>')
            for key, label in labels.items():
                if key in settings:
                    parts.append('<li>' + escape(label) + ' : ' + escape(str(settings[key])) + '</li>')
            parts.append('</ul><p>Ces réglages ne constituent pas un motif de rejet '
                         'par image. Un seuil numérique absent du rapport reste inconnu.</p>')
        geometry = payload.get('geometry_checks', [])
        if isinstance(geometry, list) and geometry:
            parts.append('<h4>Contrôles géométriques enregistrés</h4>')
            for check in geometry:
                if not isinstance(check, dict):
                    continue
                parts.append('<p>Séquence : ' + escape(str(check.get('sequence', '?')))
                             + ' ; échelles locales autorisées : '
                             + escape(str(check.get('min_scale', '?'))) + ' à '
                             + escape(str(check.get('max_scale', '?')))
                             + ' ; restantes : ' + escape(str(check.get('remaining', '?'))) + '.</p>')
                rejected = check.get('rejected')
                if isinstance(rejected, list):
                    if not rejected:
                        parts.append('<p>Aucun rejet géométrique enregistré pour cette séquence.</p>')
                    else:
                        parts.append('<ul>')
                        for frame in rejected:
                            if isinstance(frame, dict):
                                parts.append('<li>' + ProcessingHtmlReport._source_link(frame.get('filename'), directory)
                                             + ' : ' + escape(str(frame.get('reason', '?'))) + '</li>')
                        parts.append('</ul>')
        profile_summary = payload.get('stellar_profiles', {})
        references = profile_summary.get('references') if isinstance(profile_summary, dict) else None
        parts.append(ProcessingHtmlReport._decision_trace_view(payload.get('selection_decisions'), references, directory))
        profiles = payload.get('stellar_profiles')
        if isinstance(profiles, dict):
            parts.append('<p>Profils stellaires : ' + escape(str(profiles.get('rejected', '?')))
                         + ' rejet(s), ' + escape(str(profiles.get('remaining', '?')))
                         + ' pose(s) restante(s), ' + escape(str(profiles.get('inconclusive', '?')))
                         + ' contrôle(s) non concluant(s). Ces rejets sont inclus dans le bilan global.</p>')
        frames = payload.get('frames', [])
        if isinstance(frames, list):
            parts.append('<details><summary>Poses retenues et mesures enregistrées</summary><table>'
                         '<tr><th>Pose</th><th>fwhm (px)</th><th>registration_roundness</th>'
                         '<th>nbstars</th><th>Entrées pondérées</th></tr>')
            for frame in frames:
                if not isinstance(frame, dict):
                    continue
                values = [frame.get('source', '?'), frame.get('fwhm', '?'),
                          frame.get('roundness', '?'), frame.get('nbstars', '?'),
                          frame.get('effective_stack_entries', '?')]
                parts.append('<tr>' + ''.join('<td>' + (ProcessingHtmlReport._source_link(value, directory)
                             if index == 0 else escape(ProcessingHtmlReport._quality_value(value))) + '</td>'
                             for index, value in enumerate(values)) + '</tr>')
            parts.append('</table></details>')
        parts.append('</section>')
        return ''.join(parts)

    @staticmethod
    def _stellar_selection_view(payload: object, directory: Path | None = None) -> str:
        """Expose décisions, mesures et limites par pose du contrôle des profils.

        Conserve tous les enregistrements, y compris non concluants, sans
        assimiler leurs verdicts à la sélection finale de toute la registration.
        """
        if not isinstance(payload, dict):
            return '<p>Rapport de profils invalide.</p>'
        parts = ['<section><h3>Décisions du contrôle des profils stellaires</h3>']
        input_count = payload.get('input_count')
        if input_count is None and payload.get('enabled') is True and isinstance(payload.get('frames'), list):
            input_count = len(payload['frames'])
        parts.append('<p>Images considérées en entrée du contrôle des profils : '
                     + escape(str(input_count) if input_count is not None else '?') + '.</p>')
        for key, label in [('enabled', 'Contrôle activé'), ('rejected', 'Rejets de profils'),
                           ('remaining', 'Restantes après ce contrôle'), ('inconclusive', 'Non concluantes')]:
            parts.append('<p>' + label + ' : ' + escape(str(payload.get(key, '?'))) + '.</p>')
        frames = payload.get('frames', [])
        frames = [frame for frame in frames if isinstance(frame, dict)] if isinstance(frames, list) else []
        thresholds_html, group_labels = ProcessingHtmlReport._profile_thresholds(payload.get('references'), frames)
        parts.append('<p>Ce contrôle porte sur les poses ayant survécu aux filtres '
                     'précédents. Les motifs et limites proviennent du rapport ; '
                     'les mesures manquantes sont ?. Une mesure non concluante '
                     'ne suffit pas à rejeter la pose.</p>' + thresholds_html + '<table><tr>'
                     '<th>Pose</th><th>Groupe des seuils</th><th>Décision</th><th>Motifs</th>'
                     '<th>Étoiles mesurées</th><th>r80 (px natifs)</th>'
                     '<th>Fraction hors seuil r80</th><th>elongation</th>'
                     '<th>Fraction hors seuil elongation</th></tr>')
        statuses = {'inconclusive': 'NON CONCLUANTE', 'unknown': 'NON CONCLUANTE'}
        for position, frame in enumerate(frames):
            reasons = frame.get('reasons', frame.get('reason', '?'))
            if isinstance(reasons, list):
                labels = {'excluded_by_siril': 'Exclusion Siril : motif détaillé non fourni',
                          'invalid_registration': 'Diagnostic Python : mesures ou transformation invalides'}
                reasons = ', '.join(labels.get(str(reason), str(reason)) for reason in reasons) or '—'
            values = [frame.get('source', '?'), group_labels[position],
                      statuses.get(frame.get('status'), frame.get('status', '?')),
                      reasons, frame.get('stars_measured', '?'),
                      frame.get('r80', '?'),
                      frame.get('r80_fraction', '?'), frame.get('elongation', '?'),
                      frame.get('elongation_fraction', '?')]
            parts.append('<tr>' + ''.join('<td>' + (ProcessingHtmlReport._source_link(value, directory)
                         if index == 0 else ProcessingHtmlReport._selection_symbol(value)
                         if index == 2 else escape(ProcessingHtmlReport._quality_value(value)))
                         + '</td>' for index, value in enumerate(values)) + '</tr>')
        parts.append('</table></section>')
        return ''.join(parts)

    def _registration_selection(self, directory: Path) -> str:
        """Affiche le bilan de registration ou l’effectif initial de la qualité.

        Seuls les rapports directs du parent sont lus, sous la limite JSON ;
        aucun chemin arbitraire contenu dans le rapport n'est suivi.
        """
        if directory.name not in {'02_registration', '03_quality'}:
            return ''
        parts = []
        paths = ([] if directory.name == '02_registration' and (directory / 'registration.json').is_file()
                 else sorted(directory.parent.glob('*.drizzle.json')))
        for path in paths:
            if path.is_symlink():
                continue
            try:
                if path.stat().st_size > self.JSON_LIMIT:
                    continue
                with path.open(encoding='utf-8') as stream:
                    payload = json.load(stream)
                if not isinstance(payload, dict):
                    continue
                rows = self._selection_flow_rows(payload)
                registration = next((row for row in rows if row.get('stage') == 'registration'), {})
                if directory.name == '03_quality':
                    count = registration.get('remaining_count')
                    metadata = payload.get('metadata', {})
                    inventory = metadata.get('unique_inputs') if isinstance(metadata, dict) else None
                    parts.append('<section><h3>Entrée de l’étape qualité</h3>'
                                 '<p>Images indépendantes admissibles en entrée : '
                                 + escape(str(count) if count is not None else '?')
                                 + '. Inventaire initial conservé dans les rapports : '
                                 + escape(str(inventory) if inventory is not None else '?') + '.</p>'
                                 '<p>Le contrôle des profils est une sous-étape : il ne mesure '
                                 'que les survivantes des filtres précédents.</p></section>'
                                 + (self._selection_flow_view(rows)
                                    if not (directory / 'selection_decisions.json').is_file() else '')
                                 + ('<p>Ancien rapport : les retraits des filtres précédant '
                                    'les profils sont connus globalement ; leur détail par '
                                    'filtre n’est pas enregistré dans ce JSON.</p>'
                                    if not isinstance(payload.get('selection_flow'), list) else ''))
                    # Les réglages concernent la qualité et ne sont plus présentés comme registration.
                    settings = payload.get('settings')
                    if isinstance(settings, dict):
                        parts.append('<details><summary>Filtres de qualité configurés</summary><ul>')
                        labels = {'fwhm_reject_percent': 'Pourcentage de rejet FWHM',
                                  'max_fwhm': 'Plafond FWHM',
                                  'stellar_profile_filter': 'Contrôle des profils stellaires',
                                  'stellar_profile_sigma': 'Coefficient des seuils de profils',
                                  'roundness_weighted': 'Pondération par rondeur',
                                  'fwhm_weighted': 'Pondération par FWHM',
                                  'fwhm_filter': 'fwhm', 'roundness_filter': 'registration_roundness',
                                  'nbstars_filter': 'nbstars'}
                        for key, label in labels.items():
                            if key in settings:
                                parts.append('<li>' + escape(label) + ' : ' + escape(str(settings[key])) + '</li>')
                        parts.append('</ul></details>')
                elif not (directory / 'registration.json').is_file():
                    parts.append(self._selection_flow_view([registration] if registration else []))
                    parts.append('<p>Sortie de l’alignement initial Siril, avant les filtres qualité. '
                                 'La sortie finale après sélection appartient à 03_quality.</p>')
            except (OSError, ValueError):
                parts.append('<p>Bilan de sélection illisible.</p>')
        if not any(parts) and not (directory / 'registration.json').is_file():
            parts.append('<p>Aucun bilan JSON de sélection disponible à ce niveau.</p>')
        quality_page = directory.parent / '03_quality/index.html'
        if directory.name == '02_registration' and quality_page.parent.is_dir() and not quality_page.parent.is_symlink():
            parts.append('<p><a href="../03_quality/index.html">'
                         'Détail des décisions de qualité par pose</a></p>')
        return ''.join(parts)

    @classmethod
    def _audit(cls, path: Path, directory: Path) -> str:
        """Présente un audit ou son erreur de lecture sans inventer de résultat."""
        title = (escape(path.stem) if path.suffix == '.json'
                 else cls._link(path, directory))
        try:
            if path.name == 'restoration_summary.txt':
                return ''
            if path.name == 'restoration.json':
                with path.open(encoding='utf-8') as stream:
                    return cls._restoration_view(json.load(stream))
            if path.stat().st_size > cls.JSON_LIMIT:
                return '' if path.suffix == '.json' else f'<p>{title} : fichier volumineux.</p>'
            if path.suffix == '.json':
                payload = json.loads(path.read_text(encoding='utf-8'))
                if path.name.endswith('.registration.json'):
                    content = cls._siril_registration_view(payload, directory)
                elif path.name == 'registration.json':
                    content = cls._registration_view(payload, directory)
                elif path.name == 'selection_decisions.json':
                    content = cls._selection_flow_view(cls._selection_flow_rows(payload)) + cls._decision_trace_view(
                        payload.get('frames') if isinstance(payload, dict) else None,
                        payload.get('profile_references') if isinstance(payload, dict) else None, directory)
                    if isinstance(payload, dict) and not payload.get('complete'):
                        content = '<p>Audit incomplet : ' + escape(str(payload.get('error'))) + '</p>' + content
                else:
                    content = (cls._stellar_selection_view(payload, directory) if path.name == 'stellar_profiles.json'
                               else cls._selection_view(payload, directory) + cls._summary_view(
                                   payload, compact=any(parent.name == '04_restoration'
                                                        for parent in (directory, *directory.parents))))
                return f'<section><h3>{title}</h3>{content}</section>'
            else:
                with path.open(encoding='utf-8', errors='replace') as stream:
                    text = stream.read(cls.TEXT_LIMIT + 1)
                content = '<pre>' + escape(text[:cls.TEXT_LIMIT]) + '</pre>'
                if len(text) > cls.TEXT_LIMIT:
                    content += '<p>Texte abrégé ; ouvrir le fichier complet.</p>'
            return f'<details><summary>{title}</summary>{content}</details>'
        except (OSError, ValueError) as error:
            return f'<p class="warning">{title} : lecture impossible — {escape(str(error))}</p>'

    @classmethod
    def _gallery(cls, images: list[Path], directory: Path) -> str:
        """Inclut les JPEG/PNG par liens locaux ; aucune copie ni conversion d’image."""
        figures = []
        for path in images:
            url = escape(quote(path.relative_to(directory).as_posix(), safe='/'), quote=True)
            figures.append(f'<figure><a href="{url}"><img src="{url}" loading="lazy" '
                f'alt="{escape(path.name, quote=True)}"></a><figcaption>'
                f'{cls._link(path, directory)}</figcaption></figure>')
        return '<div class="gallery">' + ''.join(figures) + '</div>'

    @classmethod
    def _children_summary(cls, children: list[Path], base: Path) -> str:
        """Résume les verdicts déclarés, avec la grille verticale en restauration.

        Préserve les liens vers les sous-étapes et ne déduit aucun succès de
        l'absence d'un audit. Les autres étapes conservent leur présentation.
        """
        compact = any(parent.name == '04_restoration' for parent in (base, *base.parents))
        rows = []
        for child in sorted(children):
            findings = []
            for path in sorted(child.glob('*.json')):
                if path.is_symlink():
                    continue
                try:
                    if path.stat().st_size > cls.JSON_LIMIT:
                        continue
                    payload = json.loads(path.read_text(encoding='utf-8'))
                    if isinstance(payload, dict) and payload.get('status'):
                        findings.append(
                            escape(path.stem)
                            + ' : ' + (cls._selection_symbol(payload['status'])
                                       if compact else escape(str(payload['status'])))
                        )
                except (OSError, ValueError):
                    findings.append(escape(path.stem) + ' : audit illisible')
            title, _ = cls._description(child)
            cell = '<td class="table-label">' if compact else '<td>'
            rows.append(
                '<tr>' + cell + cls._link(child / 'index.html', base, child.name)
                + '</td>' + cell + escape(title) + '</td>' + cell
                + ('<br>'.join(findings) or 'Aucun verdict enregistré à ce niveau')
                + '</td></tr>'
            )
        if compact:
            return (cls._presentation_legend()
                    + '<div class="criteria-scroll"><table class="criteria-table presentation-table">'
                    '<thead><tr>'
                    + ''.join(cls._vertical_header(title) for title in ('Dossier', 'Rôle', 'Audits'))
                    + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>')
        return ('<table><tr><th>Dossier</th><th>Rôle</th><th>Audits</th></tr>'
                + ''.join(rows) + '</table>')

    @classmethod
    def publish(cls, root: Path | str) -> Path | None:
        """Indexe une sortie existante ; journalise un échec sans masquer le calcul.

        Une panne de publication ne modifie pas les résultats scientifiques et
        reste signalée dans le journal. Retourne None en cas d'erreur d'accès,
        de fichier étranger ou de dossier absent.
        """
        try:
            result = cls(root).generate()
            logging.info("Index HTML des traitements : %s", result)
            return result
        except (OSError, ValueError) as error:
            logging.warning("Index HTML non généré pour %s : %s", root, error)
            return None

    def generate(self) -> Path:
        """Écrit index.html à chaque niveau, puis retourne celui de la racine.

        Prévalide tous les index avant écriture pour préserver les pages étrangères.
        Les dossiers cachés et symboliques sont exclus. Les index sont remplacés
        atomiquement. Les dates des dossiers sont restaurées pour ne pas
        invalider la reprise des calibrations. Les liens restent relatifs.
        """
        directories = [self.root]
        for directory in directories:
            directories.extend(sorted(
                path for path in directory.iterdir()
                if not path.name.startswith('.')
                and path.is_dir() and not path.is_symlink()
            ))
        targets = [(directory, 'index.html') for directory in directories]
        targets.append((self.root, 'quality-criteria.html'))
        for directory, filename in targets:
            target = directory / filename
            if target.is_symlink():
                raise FileExistsError(f'Index HTML symbolique : {target}')
            foreign = target.exists() and self.MARKER not in target.read_text(
                encoding='utf-8', errors='replace',
            )
            if foreign:
                raise FileExistsError(f'Index HTML étranger : {target}')
        for directory, filename in reversed(targets):
            directory_times = directory.stat()
            files = sorted(
                path for path in directory.iterdir()
                if path.is_file() and not path.name.startswith('.')
                and path.name != 'index.html'
            )
            title, description = self._description(directory)
            depth = len(directory.relative_to(self.root).parts)
            navigation = '<nav>'
            if directory != self.root:
                navigation += '<a href="../index.html">← Niveau supérieur</a> · '
            navigation += (
                f'<a href="{"../" * depth}index.html">Accueil des résultats</a> · '
                + escape(directory.relative_to(self.root).as_posix()) + '</nav>'
            )
            children = [path for path in directory.iterdir()
                        if path.is_dir() and not path.is_symlink()
                        and not path.name.startswith('.')]
            content = [
                f'<h1>{escape(title)}</h1>', navigation,
                '<section><h2>Traitements</h2>'
                + f'<p>{escape(description)}</p>'
                + '<p>Les descriptions expliquent le rôle des étapes. Les audits '
                'indiquent ce qui a réellement été exécuté et les verdicts.</p></section>',
            ]
            images = [p for p in files if p.suffix.lower() in self.IMAGE_EXTENSIONS]
            if directory == self.root:
                extensions = self.IMAGE_EXTENSIONS | self.FITS_EXTENSIONS
                exports = [p for p in files if p.suffix.lower() in extensions]
                content.append(
                    '<section><h2>Images finales et exports à la racine</h2>'
                    '<p>Exports présents : leur statut final est à vérifier '
                    'dans les rapports, particulièrement sur une sortie partielle.</p>'
                    + self._gallery(images, directory) + '<ul>'
                    + ''.join('<li>' + self._link(p, directory) + '</li>'
                              for p in exports) + '</ul></section>'
                )
            if children:
                content.append(
                    '<section><h2>Sous-traitements et résultats</h2>'
                    + self._children_summary(children, directory) + '</section>'
                )
            if images and directory != self.root:
                content.append(
                    '<section><h2>Images et diagnostics JPEG / PNG</h2>'
                    + self._gallery(images, directory) + '</section>'
                )
            content.append(self._registration_selection(directory))
            audits = [p for p in files if p.suffix in {'.json', '.txt', '.sps'}
                      and not p.is_symlink() and not p.name.endswith('.commands.json')
                      and not (p.name == '01_registration.registration.json'
                               and (directory / 'registration.json').is_file())]
            if any(p.name == 'restoration.json' for p in audits):
                audits = [p for p in audits if p.name == 'restoration.json']
            content.append(
                '<section><h2>Rapports, paramètres et vérifications</h2>'
                + ''.join(self._audit(p, directory) for p in audits) + '</section>'
            )
            content.append(
                '<section><h2>Fichiers et résultats</h2><ul>'
                + ''.join('<li>' + self._link(p, directory) + '</li>'
                          for p in files if p.suffix.lower() != '.json')
                + '</ul></section>'
            )
            if filename == 'quality-criteria.html':
                title = 'Définition des critères de qualité'
                body = ('<h1>' + title + '</h1><nav><a href="index.html">'
                        'Accueil des résultats</a></nav>' + self._quality_guide())
            else:
                body = self._link_quality_terms(''.join(content), '../' * depth)
            page = (
                self.MARKER + '<!doctype html><html lang="fr"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                + '<title>' + escape(title) + '</title><style>' + self.CSS
                + '</style><body>' + body + '</body></html>'
            )
            target = directory / filename
            with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=directory,
                prefix='.processing-index-', suffix='.tmp', delete=False,
            ) as stream:
                stream.write(page)
                temporary = Path(stream.name)
            try:
                temporary.replace(target)
                os.utime(directory, ns=(
                    directory_times.st_atime_ns, directory_times.st_mtime_ns,
                ))
            finally:
                temporary.unlink(missing_ok=True)
        return self.root / 'index.html'
