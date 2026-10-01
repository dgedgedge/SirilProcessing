#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
PY_SCRIPT="${SCRIPT_DIR}/solarEclipseGif.py"

# Fix for GLIBCXX version conflicts with VS Code extension
export LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libstdc++.so.6

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage: bin/solarEclipseGif.sh [OPTIONS]

Creates/uses .venv, installs requirements, then runs bin/solarEclipseGif.py.

Options from docs/SOLAR_ECLIPSE_GIF.md:
  --input-dir PATH
  --dark-calib-frames SPEC
  --manual-seuil-fond-du-ciel NUMBER
  --full-sun-frames SPEC
  --first-nearly-full-sun-frames INT
  --exclude-frames SPEC
  --debug-dir PATH
  --debug-full-sun
  --debug-shifts
  --debug-gif-frames
  --debug-watershed
  --debug-luminosity
  --background-outside-mask-scale NUMBER
  --background-mask-dilate-fraction NUMBER
  --background-s-curve-sigma NUMBER
  --background-s-curve-target-fraction NUMBER
  --enable-background-filter
  --disable-background-filter
  --rotate-clockwise-deg DEG
  --target-duration SECONDS
  --output PATH
  --log-level {DEBUG,INFO,WARNING,ERROR}

Frame selections are 1-based: "1-10,15,20-25".
For detailed help:
  python3 bin/solarEclipseGif.py --help
EOF
  exit 0
fi

# Venv selection priority:
# 1) <project>/.venv (with activate script)
# 2) System Python with LD_PRELOAD fix (fallback)
USE_VENV=false
SELECTED_VENV=""

if [[ -d "${VENV_DIR}" && -f "${VENV_DIR}/bin/activate" ]]; then
  SELECTED_VENV="${VENV_DIR}"
  USE_VENV=true
fi

if [[ "${USE_VENV}" == true ]]; then
  python -m pip install --upgrade pip >/dev/null
  python -m pip install -r "${PROJECT_ROOT}/requirements.txt" >/dev/null
  
  # shellcheck source=/dev/null
  source "${SELECTED_VENV}/bin/activate"
  exec python "${PY_SCRIPT}" "$@"
else
  echo "[solarEclipseGif] No valid venv found. Using system Python with GLIBCXX fix."
  echo "[solarEclipseGif] Installing/upgrading dependencies..."
  python3 -m pip install --break-system-packages --upgrade pip >/dev/null
  python3 -m pip install --break-system-packages -r "${PROJECT_ROOT}/requirements.txt" >/dev/null
  exec python3 "${PY_SCRIPT}" "$@"
fi