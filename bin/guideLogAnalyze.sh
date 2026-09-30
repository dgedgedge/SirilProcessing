#!/usr/bin/env bash
# Même environnement que le visualiseur des fichiers .analyze.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
GUIDE_VENV="${VENV_DIR:-${PROJECT_ROOT}/.venv}"
if [[ ! -d "${GUIDE_VENV}" ]]; then
  python3 -m venv "${GUIDE_VENV}"
fi
if [[ ! -x "${GUIDE_VENV}/bin/python" ]]; then
  echo "Environnement invalide : ${GUIDE_VENV}" >&2
  exit 1
fi
"${GUIDE_VENV}/bin/python" -m pip install -r "${PROJECT_ROOT}/requirements-analyze.txt"
exec "${GUIDE_VENV}/bin/python" "${SCRIPT_DIR}/guideLogAnalyze.py" "$@"
