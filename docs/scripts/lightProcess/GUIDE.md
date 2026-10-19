# Guide d’utilisation de lightProcess

[Documentation](../../README.md) › [lightProcess](README.md)


Le wrapper `bin/lightProcess.sh` active le venv puis appelle `bin/lightProcess.py`.
Le script calibre les groupes de lights de chaque sous-session et construit
un stack par session fournie, quel que soit son nombre de sous-sessions.

<a id="commandes-de-d-marrage"></a>
<a id="commandes-de-démarrage"></a>

## 1. Commandes de démarrage

```bash
# Calibration et stacking d’une session
./bin/lightProcess.sh /chemin/session_M31

# Calibration et stacking d’une session comportant plusieurs sous-sessions
./bin/lightProcess.sh /chemin/M33 --dark-lib /chemin/master_darks

# Reconstruction du stack à partir des calibrations disponibles
./bin/lightProcess.sh /chemin/M33 --force-stacking

# Calibration, stacking et post-traitement complet
./bin/lightProcess.sh /chemin/M33 --postprocess
```

Chaque chemin fourni définit une session contenant une ou plusieurs sous-sessions
d’acquisition (`light/`, éventuellement `flat/`), ou directement ces dossiers.
Voir [les structures d’entrée](SESSIONS.md).


<a id="options-par-tape"></a>
<a id="options-par-étape"></a>

## 2. Options par étape

Consulter la [référence des options de lightProcess](OPTIONS.md). Les tableaux et réglages y sont regroupés.


<a id="sch-ma-du-processus"></a>
<a id="schéma-du-processus"></a>

## 3. Schéma du processus

Le [parcours détaillé](treatments/README.md) décrit l’ordre des opérations et
les références pour chaque traitement. Il détaille la calibration par sous-session, le stacking par session et
la mosaïque optionnelle des résultats de stacking.

<a id="r-f-rences-de-traitement"></a>
<a id="références-de-traitement"></a>

## 4. Références de traitement

- [Calibration](treatments/CALIBRATION.md) : correspondance des masters, sélection des flats et conservation du CFA.
- [Filtrage et stacking](filter/stacking/README.md) : critères, profils stellaires et Drizzle.
- [Empilement final](treatments/STACKING.md) : scripts exécutés, alignements et comportement des modes.
- [Sorties et reprise](treatments/OUTPUTS.md) : rapports, cache, nettoyage et limites de la simulation.
- [Mosaïque](MOSAIC.md) : assemblage optionnel des champs.
- [Pipeline de post-traitement](../postProcess/pipeline.md) : configuration,
  enchaînement et exports FITS/JPEG communs.
