#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
PY_SCRIPT="${SCRIPT_DIR}/solarEclipseGif.py"

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

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "[solarEclipseGif] Creation du venv dans ${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
fi

# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"

python -m pip install --upgrade pip >/dev/null
python -m pip install -r "${PROJECT_ROOT}/requirements.txt" >/dev/null

exec python "${PY_SCRIPT}" "$@"
