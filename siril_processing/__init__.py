"""
SirilProcessing - A tool for managing FITS calibration libraries for Siril.

This package provides functionality for managing dark and bias master FITS files,
with both CLI and GUI interfaces.
"""

__version__ = "0.1.0"
__author__ = "dgedgedge"

from .core.dark_library import DarkLibrary
from .core.bias_library import BiasLibrary

__all__ = ["DarkLibrary", "BiasLibrary"]
