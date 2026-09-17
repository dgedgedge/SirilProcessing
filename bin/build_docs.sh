#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PY_SCRIPT="${SCRIPT_DIR}/generate_docs_html.py"

# Venv selection priority:
# 1) VENV_DIR environment variable
# 2) <project>/.venv
# 3) <project>/venv
if [[ -n "${VENV_DIR:-}" ]]; then
  SELECTED_VENV="${VENV_DIR}"
elif [[ -d "${PROJECT_ROOT}/.venv" ]]; then
  SELECTED_VENV="${PROJECT_ROOT}/.venv"
elif [[ -d "${PROJECT_ROOT}/venv" ]]; then
  SELECTED_VENV="${PROJECT_ROOT}/venv"
else
  echo "[build_docs] Aucun venv trouve."
  echo "Creez-en un puis relancez:"
  echo "  python3 -m venv ${PROJECT_ROOT}/.venv"
  exit 1
fi

if [[ ! -f "${SELECTED_VENV}/bin/activate" ]]; then
  echo "[build_docs] Script d'activation introuvable: ${SELECTED_VENV}/bin/activate"
  exit 1
fi

# shellcheck source=/dev/null
source "${SELECTED_VENV}/bin/activate"

exec python "${PY_SCRIPT}" \
  --source-root "${PROJECT_ROOT}" \
  --output-dir "${PROJECT_ROOT}/out" \
  --pdf \
  "$@"
