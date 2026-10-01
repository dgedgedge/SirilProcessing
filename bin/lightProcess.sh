#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PY_SCRIPT="${SCRIPT_DIR}/lightProcess.py"
REQUIREMENTS_FILE="${PROJECT_ROOT}/requirements.txt"

# Fix for GLIBCXX version conflicts with VS Code extension
export LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libstdc++.so.6

# Venv selection priority:
# 1) VENV_DIR environment variable
# 2) <project>/.venv (with activate script)
# 3) <project>/venv (with activate script)
# 4) System Python with LD_PRELOAD fix (fallback)
USE_VENV=false
SELECTED_VENV=""

if [[ -n "${VENV_DIR:-}" ]]; then
  if [[ -d "${VENV_DIR}" && -f "${VENV_DIR}/bin/activate" ]]; then
    SELECTED_VENV="${VENV_DIR}"
    USE_VENV=true
  else
    echo "[lightProcess] VENV_DIR specified but invalid: ${VENV_DIR}"
    exit 1
  fi
elif [[ -d "${PROJECT_ROOT}/.venv" && -f "${PROJECT_ROOT}/.venv/bin/activate" ]]; then
  SELECTED_VENV="${PROJECT_ROOT}/.venv"
  USE_VENV=true
elif [[ -d "${PROJECT_ROOT}/venv" && -f "${PROJECT_ROOT}/venv/bin/activate" ]]; then
  SELECTED_VENV="${PROJECT_ROOT}/venv"
  USE_VENV=true
fi

# Check and install dependencies if needed
if [[ -f "${REQUIREMENTS_FILE}" ]]; then
  if [[ "${USE_VENV}" == true ]]; then
    echo "[lightProcess] Checking dependencies in virtual environment..."
    if ! "${SELECTED_VENV}/bin/python" -c "import numpy" 2>/dev/null; then
      echo "[lightProcess] Installing dependencies from ${REQUIREMENTS_FILE}"
      "${SELECTED_VENV}/bin/pip" install -r "${REQUIREMENTS_FILE}"
    fi
    
    # shellcheck source=/dev/null
    source "${SELECTED_VENV}/bin/activate"
    exec python "${PY_SCRIPT}" "$@"
  else
    echo "[lightProcess] No valid venv found. Using system Python with GLIBCXX fix."
    echo "[lightProcess] Checking dependencies in system Python..."
    
    # Check if numpy can be imported with LD_PRELOAD
    if ! python3 -c "import numpy" 2>/dev/null; then
      echo "[lightProcess] Installing dependencies from ${REQUIREMENTS_FILE}"
      python3 -m pip install --break-system-packages -r "${REQUIREMENTS_FILE}"
    fi
    
    exec python3 "${PY_SCRIPT}" "$@"
  fi
else
  if [[ "${USE_VENV}" == true ]]; then
    # shellcheck source=/dev/null
    source "${SELECTED_VENV}/bin/activate"
    exec python "${PY_SCRIPT}" "$@"
  else
    echo "[lightProcess] No valid venv found. Using system Python with GLIBCXX fix."
    exec python3 "${PY_SCRIPT}" "$@"
  fi
fi