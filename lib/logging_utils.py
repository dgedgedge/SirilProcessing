"""Configuration commune des logs console et fichiers de traitement."""

import logging
from pathlib import Path


def setup_logging(log_level: str) -> None:
    """Configure le système de logging."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        force=True
    )


def add_session_file_logging(log_file: Path, log_level: str) -> logging.Handler:
    """Ajoute un fichier de log pour la sous-session courante."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, log_level.upper(), logging.INFO)
    handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logging.getLogger().addHandler(handler)
    return handler


def remove_session_file_logging(handler: logging.Handler) -> None:
    """Retire et ferme un handler de log de sous-session."""
    logging.getLogger().removeHandler(handler)
    handler.close()


