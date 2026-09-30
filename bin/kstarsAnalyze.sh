#!/usr/bin/env bash
# Prépare le venv du visualiseur KStars puis lui transmet les arguments.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ANALYZE_VENV="${VENV_DIR:-${PROJECT_ROOT}/.venv}"

if [[ ! -d "${ANALYZE_VENV}" ]]; then
  echo "[kstarsAnalyze] Création du venv : ${ANALYZE_VENV}"
  python3 -m venv "${ANALYZE_VENV}"
fi

if [[ ! -f "${ANALYZE_VENV}/bin/activate" || ! -x "${ANALYZE_VENV}/bin/python" ]]; then
  echo "[kstarsAnalyze] Venv invalide : ${ANALYZE_VENV}" >&2
  exit 1
fi

# shellcheck source=/dev/null
source "${ANALYZE_VENV}/bin/activate"

echo "[kstarsAnalyze] Mise à jour de pip et des dépendances"
python -m pip install --upgrade pip
python -m pip install --upgrade -r "${PROJECT_ROOT}/requirements-analyze.txt"

exec python "${SCRIPT_DIR}/kstarsAnalyze.py" --gui "$@"
