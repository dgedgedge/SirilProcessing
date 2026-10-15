#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Compatibilité avec la libstdc++ des extensions VS Code.
export LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libstdc++.so.6

# shellcheck source=bin/venv_helpers.sh
source "${SCRIPT_DIR}/venv_helpers.sh"
prepare_project_venv "${PROJECT_ROOT}/requirements-docs.txt"
# Chromium est réservé au rendu documentaire, dans un cache exclu de Git.
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-${PROJECT_ROOT}/tmp/docs-browsers}"
"${SELECTED_VENV}/bin/python" -m playwright install chromium
exec "${SELECTED_VENV}/bin/python" "${SCRIPT_DIR}/build_documentation.py" --source-root "${PROJECT_ROOT}" --output-dir "${PROJECT_ROOT}/out" "$@"
