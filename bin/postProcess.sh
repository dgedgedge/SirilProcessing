#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Compatibilité avec la libstdc++ des extensions VS Code.
export LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libstdc++.so.6

INSTALL_COSMIC=false
COSMIC_MODEL_DIR="${PROJECT_ROOT}/models/cosmicclarity"
COSMIC_MODEL_DIR_SET=false
FORWARDED_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-cosmic-clarity)
      INSTALL_COSMIC=true
      shift
      ;;
    --cosmic-model-dir)
      if [[ $# -lt 2 || -z "$2" || "$2" == --* ]]; then
        echo "--cosmic-model-dir exige un dossier" >&2
        exit 2
      fi
      COSMIC_MODEL_DIR="$2"
      COSMIC_MODEL_DIR_SET=true
      shift 2
      ;;
    --cosmic-model-dir=*)
      COSMIC_MODEL_DIR="${1#*=}"
      [[ -n "${COSMIC_MODEL_DIR}" ]] || { echo "Dossier vide" >&2; exit 2; }
      COSMIC_MODEL_DIR_SET=true
      shift
      ;;
    --)
      FORWARDED_ARGS+=("$@")
      break
      ;;
    -h|--help)
      echo "Option du lanceur : --install-cosmic-clarity installe PyTorch CUDA et les poids."
      echo "Utilisable seule ou avant un traitement ; --cosmic-model-dir choisit le dossier."
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    *)
      FORWARDED_ARGS+=("$1")
      shift
      ;;
  esac
done

# shellcheck source=bin/venv_helpers.sh
source "${SCRIPT_DIR}/venv_helpers.sh"
prepare_project_venv "${PROJECT_ROOT}/requirements.txt"
if [[ "${INSTALL_COSMIC}" == true ]]; then
  COSMIC_SYSTEM_PYTHON="$(python3 -c 'import sys; print(sys._base_executable)')"
  "${COSMIC_SYSTEM_PYTHON}" -m pip --python "${SELECTED_VENV}/bin/python" \
    install --upgrade --quiet --disable-pip-version-check \
    -r "${PROJECT_ROOT}/requirements-cosmic-clarity.txt"
  "${SELECTED_VENV}/bin/python" "${PROJECT_ROOT}/lib/cosmic_clarity/models.py" \
    --directory "${COSMIC_MODEL_DIR}"
  if [[ ${#FORWARDED_ARGS[@]} -eq 0 ]]; then
    exit 0
  fi
fi
if [[ "${COSMIC_MODEL_DIR_SET}" == true ]]; then
  FORWARDED_ARGS=(--cosmic-model-dir "${COSMIC_MODEL_DIR}" "${FORWARDED_ARGS[@]}")
fi
exec "${SELECTED_VENV}/bin/python" "${SCRIPT_DIR}/postProcess.py" "${FORWARDED_ARGS[@]}"
