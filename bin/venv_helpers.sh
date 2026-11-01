#!/usr/bin/env bash
# Bibliothèque sourcée par les lanceurs : sélection d'un environnement déjà installé.

locate_project_venv() {
  # Même priorité que l'installeur : VENV_DIR, .venv, puis le dossier historique venv.
  if [[ -n "${VENV_DIR:-}" ]]; then
    SELECTED_VENV="${VENV_DIR}"
  elif [[ -d "${PROJECT_ROOT}/.venv" ]]; then
    SELECTED_VENV="${PROJECT_ROOT}/.venv"
  elif [[ -d "${PROJECT_ROOT}/venv" ]]; then
    SELECTED_VENV="${PROJECT_ROOT}/venv"
  else
    SELECTED_VENV="${PROJECT_ROOT}/.venv"
  fi
}

select_project_venv() {
  # Les traitements exigent un environnement existant et ne le modifient pas.
  locate_project_venv
  if [[ ! -x "${SELECTED_VENV}/bin/python" ]]; then
    printf 'Environnement Python absent ou incomplet : %s\n' "${SELECTED_VENV}" >&2
    printf 'Installez les dépendances avec : "%s/bin/install_dependances.sh" --venv "%s"\n' \
      "${PROJECT_ROOT}" "${SELECTED_VENV}" >&2
    return 1
  fi
}
