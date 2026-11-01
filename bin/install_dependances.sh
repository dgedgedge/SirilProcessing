#!/usr/bin/env bash
# Utiliser le Python de base, même lorsqu'un venv à remettre à zéro est activé.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPENDENCIES_PYTHON="$(python3 -c 'import sys; print(sys._base_executable)')"
exec "${DEPENDENCIES_PYTHON}" "${SCRIPT_DIR}/install_dependances.py" "$@"
