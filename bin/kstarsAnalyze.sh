#!/usr/bin/env bash
# Prépare le venv du visualiseur KStars puis lui transmet les arguments.
set -euo pipefail

# Fix for GLIBCXX version conflicts with VS Code extension
export LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libstdc++.so.6

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ANALYZE_VENV="${VENV_DIR:-${PROJECT_ROOT}/.venv}"

# Venv selection with fallback to system Python
USE_VENV=false
if [[ -d "${ANALYZE_VENV}" && -f "${ANALYZE_VENV}/bin/activate" && -x "${ANALYZE_VENV}/bin/python" ]]; then
  USE_VENV=true
fi

if [[ "${USE_VENV}" == true ]]; then
  # shellcheck source=/dev/null
  source "${ANALYZE_VENV}/bin/activate"
  
  echo "[kstarsAnalyze] Mise à jour de pip et des dépendances"
  python -m pip install --upgrade pip
  python -m pip install --upgrade -r "${PROJECT_ROOT}/requirements-analyze.txt"
  
  exec python "${SCRIPT_DIR}/kstarsAnalyze.py" --gui "$@"
else
  echo "[kstarsAnalyze] No valid venv found. Using system Python with GLIBCXX fix."
  echo "[kstarsAnalyze] Mise à jour de pip et des dépendances"
  python3 -m pip install --break-system-packages --upgrade pip
  python3 -m pip install --break-system-packages --upgrade -r "${PROJECT_ROOT}/requirements-analyze.txt"
  
  exec python3 "${SCRIPT_DIR}/kstarsAnalyze.py" --gui "$@"
fi