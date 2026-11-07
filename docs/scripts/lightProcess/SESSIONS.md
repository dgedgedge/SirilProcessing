# Sessions et sous-sessions

[Documentation](../../README.md) › [lightProcess](README.md)

Une **session** est chacun des répertoires fournis en paramètre à la commande.
Une **sous-session** est un répertoire d’acquisition contenant `light/` ou
`Light/`, avec éventuellement `flat/` ou `Flat/`. Les sous-sessions sont
recherchées dans les sous-répertoires immédiats de la session.

Si le répertoire fourni contient directement `light/`, il constitue lui-même
l’unique sous-session. Une session contenant un seul sous-répertoire
d’acquisition suit exactement le même traitement qu’une session en contenant
plusieurs ; elle conserve le nom du répertoire fourni.

```text
M33/                    ← session fournie en paramètre
  nuit_1/               ← sous-session
    light/
    flat/
  nuit_2/               ← sous-session
    light/
    flat/
```

```bash
./bin/lightProcess.sh /chemin/M33
```

Chaque sous-session est calibrée séparément, par groupe de métadonnées.
Tous les FITS calibrés de M33 sont réunis, sélectionnés puis empilés pour
produire le résultat de cette session sous
`M33/01_calibration/stack_combined.fits`. Le relais vers le rendu est une copie
indépendante sous `M33/02_mosaic/image.fits`.

Fournir directement `/chemin/M33/nuit_1` définit une session nommée `nuit_1`,
avec une seule sous-session : elle est également calibrée puis empilée.
Fournir plusieurs chemins exige `--mosaic` et produit un résultat par panneau
sous `01_calibration/part_XX`. Seuls ces résultats de stacking sont assemblés.
Sans `--mosaic`, fournir exactement un répertoire de données.

Le dossier `01_session/` sous `01_calibration` (ou son panneau) contient les calibrations des **sous-sessions** ;
son nom sur disque est conservé. Voir [le parcours](treatments/README.md),
[les sorties et la reprise](treatments/OUTPUTS.md) et [les mosaïques](MOSAIC.md).

Voir [le plan des sorties et les identités de panneaux](output-layout.md).
