"""Sérialisation des synthèses et audits détaillés de restauration.

Le rapport global v2 garde les décisions, mesures agrégées et chemins des audits.
Les listes scientifiques restent dans les audits locaux et finaux. La projection
ne modifie jamais les dictionnaires utilisés pour les calculs ou leur cache.
Voir docs/scripts/postProcess/restoration-reports.md pour le schéma et ses limites.
"""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from lib.type_defs import JSONReport, JSONValue


class RestorationReportStorage:
    """Sépare les données de calcul, la synthèse publiée et les audits autonomes."""

    SCHEMA_VERSION = 2
    MAX_SCALAR_LIST_LENGTH = 32
    RECORD_LISTS = frozenset({
        'variants', 'operations', 'steps', 'attempts', 'children',
        'per_channel', 'rows',
    })
    BULK_FIELDS = frozenset({
        'pairs', 'reference_measurements', 'high_frequency_residual',
    })

    @classmethod
    def compact(cls, value: JSONValue, field: str = '') -> JSONValue:
        """Projette un audit en mesures agrégées sans recopier ses populations.

        Conserve les champs scalaires, les dictionnaires, les petites listes de
        scalaires (au plus 32 éléments) et les listes sémantiques d'étapes, essais,
        canaux, récursivement résumées. Les listes non retenues sont
        omises, pas remplacées par une mesure nulle. ``channels`` dans les
        artefacts est une population historique ; ``per_channel`` contient les
        synthèses des critères. Aucun calcul de qualité n'est effectué.
        """
        if isinstance(value, dict):
            result: JSONReport = {}
            for key, item in value.items():
                if key in cls.BULK_FIELDS:
                    continue
                if isinstance(item, list):
                    if key == 'channels':
                        continue
                    scalar_list = (
                        len(item) <= cls.MAX_SCALAR_LIST_LENGTH
                        and all(not isinstance(child, (dict, list)) for child in item)
                    )
                    if key not in cls.RECORD_LISTS and not scalar_list:
                        continue
                result[key] = cls.compact(item, key)
            return result
        if isinstance(value, list):
            return [cls.compact(item) for item in value]
        return value

    @classmethod
    def summary(cls, audit: JSONReport, audit_path: Path | None = None) -> JSONReport:
        """Retourne une synthèse v2, liée au rapport détaillé lorsqu'il existe."""
        result = cls.compact(audit)
        assert isinstance(result, dict)
        result['report_schema_version'] = cls.SCHEMA_VERSION
        result['report_detail'] = 'summary'
        if audit_path is not None:
            result['audit_path'] = str(audit_path)
        return result

    @classmethod
    def detailed(cls, audit: JSONReport) -> JSONReport:
        """Conserve les populations une fois par audit, sans alias de diagnostics.

        Les ``artifacts`` gardent leurs listes complètes ; leurs copies dans
        ``criteria`` et ``quality.criteria`` deviennent des synthèses. Les essais
        internes suivent la même règle. Les paires et mesures de référence
        restent présentes pour permettre l'inspection scientifique hors ligne.
        """
        result = dict(audit)
        result['report_schema_version'] = cls.SCHEMA_VERSION
        result['report_detail'] = 'detailed'
        artifacts = audit.get('artifacts')
        if isinstance(artifacts, dict):
            result['artifacts'] = {
                key: value for key, value in artifacts.items()
                if key != 'high_frequency_residual'
            }
        if isinstance(audit.get('criteria'), dict):
            result['criteria'] = cls.compact(audit['criteria'])
        quality = audit.get('quality')
        if isinstance(quality, dict):
            result['quality'] = dict(quality)
            if isinstance(quality.get('criteria'), dict):
                result['quality']['criteria'] = cls.compact(quality['criteria'])
        attempts = audit.get('attempts')
        if isinstance(attempts, list):
            result['attempts'] = [
                cls.detailed(item) if isinstance(item, dict) else item
                for item in attempts
            ]
        return result

    @staticmethod
    def write(path: Path, payload: JSONReport) -> None:
        """Publie un JSON complet par remplacement atomique dans le même dossier.

        Un échec de sérialisation ou d'écriture laisse la publication précédente
        intacte et supprime le temporaire. Les répertoires doivent déjà exister.
        Ce contrat évite de lire un fichier momentanément tronqué lors d'un run.
        """
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=path.parent,
                prefix=f'.{path.name}.', suffix='.tmp', delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(payload, stream, indent=2, allow_nan=False)
                stream.write('\n')
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
