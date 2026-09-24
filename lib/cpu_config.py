"""Configuration CPU commune à tous les traitements Siril."""
import os


class CpuConfig:
    """Par défaut, réserve un processeur logique pour le reste du système."""

    _limit = None

    @classmethod
    def configure(cls, limit=None):
        """Fixe une limite globale ; None rétablit le mode automatique (N - 1)."""
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError("La limite CPU doit être un entier positif ou None")
        cls._limit = limit

    @classmethod
    def get_limit(cls):
        """Calcule la limite sur la machine d'exécution, avec au moins un CPU."""
        available = os.cpu_count() or 1
        if cls._limit is None:
            return max(1, available - 1)
        return min(cls._limit, available)
