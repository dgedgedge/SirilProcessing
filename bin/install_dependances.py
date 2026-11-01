#!/usr/bin/env python3
"""Installe ou teste le venv détecté ; guide docs/scripts/install_dependances/README.md."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.dependency_installation import main


if __name__ == '__main__':
    sys.exit(main())
