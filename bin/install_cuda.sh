#!/usr/bin/env bash
# Le Python système orchestre pip ; tous les paquets vont dans le venv cible.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/install_cuda.py" "$@"
