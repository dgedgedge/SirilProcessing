#!/usr/bin/env bash
# Même environnement que le visualiseur des fichiers .analyze.
set -euo pipefail

# Fix for GLIBCXX version conflicts with VS Code extension
export LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libstdc++.so.6

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
GUIDE_VENV="${VENV_DIR:-${PROJECT_ROOT}/.venv}"

# Venv selection with fallback to system Python
USE_VENV=false
if [[ -d "${GUIDE_VENV}" && -f "${GUIDE_VENV}/bin/activate" && -x "${GUIDE_VENV}/bin/python" ]]; then
  USE_VENV=true
fi

if [[ "${USE_VENV}" == true ]]; then
  "${GUIDE_VENV}/bin/python" -m pip install -r "${PROJECT_ROOT}/requirements-analyze.txt"
  exec "${GUIDE_VENV}/bin/python" "${SCRIPT_DIR}/guideLogAnalyze.py" "$@"
else
  echo "[guideLogAnalyze] No valid venv found. Using system Python with GLIBCXX fix."
  echo "[guideLogAnalyze] Installing dependencies..."
  python3 -m pip install --break-system-packages -r "${PROJECT_ROOT}/requirements-analyze.txt"
  exec python3 "${SCRIPT_DIR}/guideLogAnalyze.py" "$@"
fi