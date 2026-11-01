#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Compatibilité avec la libstdc++ des extensions VS Code.
if [[ "$(uname -s)" == Linux && -f /usr/lib/x86_64-linux-gnu/libstdc++.so.6 ]]; then
  export LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libstdc++.so.6
fi

# shellcheck source=bin/venv_helpers.sh
source "${SCRIPT_DIR}/venv_helpers.sh"
select_project_venv
exec "${SELECTED_VENV}/bin/python" "${SCRIPT_DIR}/postProcess.py" "$@"
