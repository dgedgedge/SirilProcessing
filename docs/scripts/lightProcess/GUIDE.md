# Guide d’utilisation de lightProcess

[Documentation](../../README.md) › [lightProcess](README.md)


Le wrapper `bin/lightProcess.sh` active le venv puis appelle `bin/lightProcess.py`.
Le script calibre les groupes de lights de chaque sous-session et construit
un stack par session fournie, quel que soit son nombre de sous-sessions.

## Commandes de démarrage

```bash
# Calibration et stacking d’une session
./bin/lightProcess.sh /chemin/session_M31

# Calibration et stacking d’une session comportant plusieurs sous-sessions
./bin/lightProcess.sh /chemin/M33 --dark-lib /chemin/master_darks

# Reconstruction du stack à partir des calibrations disponibles
./bin/lightProcess.sh /chemin/M33 --force-stacking
```

Chaque chemin fourni définit une session contenant une ou plusieurs sous-sessions
d’acquisition (`light/`, éventuellement `flat/`), ou directement ces dossiers.
Voir [les structures d’entrée](SESSIONS.md).

## Options par étape

| Étape | Principales options |
|---|---|
| Chemins | `--dark-lib`, `--output`, `--work-dir` |
| Calibration | `--no-dark`, `--temperature-precision`, `--force` |
| Exécution | `--siril-path`, `--siril-mode`, `--dry-run`, `--log-level` |
| Alignement | `--align-transform`, `--stack-platesolve` |
| Sélection | `--fwhm-filter`, `--max-fwhm`, `--fwhm-reject-percent`, `--roundness-filter`, `--nbstars-filter` |
| Profils stellaires | `--stellar-profile-filter`, `--no-stellar-profile-filter`, `--stellar-profile-sigma` |
| Pondération | `--no-fwhm-weighted`, `--no-roundness-weighted` et maxima de répétitions associés |
| Empilement | `--stack-method`, `--rejection-method`, `--rejection-param1`, `--rejection-param2` |
| Drizzle | `--drizzle`, `--drizzle-scale`, `--drizzle-pixfrac`, `--drizzle-kernel` |
| Relance et conservation | `--force-stacking`, `--keep-intermediate`, `--purge-target` |
| Mosaïque | `--mosaic`, `--mosaic-name` |

`--help` expose toutes les options. Les réglages peuvent provenir du fichier
persistant de configuration ; `--save-config` enregistre les arguments.

## Schéma du processus

Le [parcours détaillé](treatments/README.md) décrit l’ordre des opérations et
les références pour chaque traitement. Il détaille la calibration par sous-session, le stacking par session et
la mosaïque optionnelle des résultats de stacking.

## Références de traitement

- [Calibration](treatments/CALIBRATION.md) : correspondance des masters, sélection des flats et conservation du CFA.
- [Filtrage et stacking](filter/stacking/README.md) : critères, profils stellaires et Drizzle.
- [Empilement final](treatments/STACKING.md) : scripts exécutés, alignements et comportement des modes.
- [Sorties et reprise](treatments/OUTPUTS.md) : rapports, cache, nettoyage et limites de la simulation.
- [Mosaïque](MOSAIC.md) : assemblage optionnel des champs.
