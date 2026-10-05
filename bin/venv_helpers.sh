#!/usr/bin/env bash
# Bibliothèque sourcée par les lanceurs ; Python et pip restent fournis par le système.

prepare_project_venv() {
  # Reçoit le fichier requirements et expose SELECTED_VENV pour l'exécution.
  # Retrouve le Python de base même si un venv est déjà activé dans le terminal.
  local requirements_file="$1"
  local system_python
  system_python="$(python3 -c 'import sys; print(sys._base_executable)')" || return

  if [[ -n "${VENV_DIR:-}" ]]; then
    SELECTED_VENV="${VENV_DIR}"
  elif [[ -d "${PROJECT_ROOT}/.venv" ]]; then
    SELECTED_VENV="${PROJECT_ROOT}/.venv"
  elif [[ -d "${PROJECT_ROOT}/venv" ]]; then
    SELECTED_VENV="${PROJECT_ROOT}/venv"
  else
    SELECTED_VENV="${PROJECT_ROOT}/.venv"
  fi

  "${system_python}" -m venv --without-pip "${SELECTED_VENV}" || return
  "${system_python}" -m pip --python "${SELECTED_VENV}/bin/python" \
    install --upgrade --quiet --disable-pip-version-check -r "${requirements_file}" || return
}
