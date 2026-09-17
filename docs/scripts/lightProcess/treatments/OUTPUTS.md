# Sorties, reprise et journaux

[Documentation](../../../README.md) › [lightProcess](../README.md) › [Traitements](README.md)


## Fichiers produits

Les chemins ci-dessous sont relatifs aux bases de sortie et de travail
configurées. Pour toute session, avec une ou plusieurs sous-sessions :

```text
<sortie>/<session>/
  sessions/<sous-session>/<sous-session>_<groupe>_calibrated/
    pp_light_*.fit(s)
    pp_light_*.seq
  stack/
    <session>_combined.fit(s)
    <session>_combined.drizzle.json

<travail>/<session>/
  sessions/<sous-session>/
  stacking/
    00_inputs/
    01_registration/
    02_quality/stellar_profiles.json
    03_capability/
    04_stacking/
```

La séquence exportée permet de rouvrir les calibrations dans Siril. Les `.seq`
contiennent les références, mesures et transformations, pas les pixels.
Les scripts `.sps` et leurs journaux sont conservés avec les étapes de travail.
Le parcours implémenté ne génère pas de prévisualisation JPG.

## Réutilisation et relance

| Option ou condition | Comportement |
|---|---|
| Calibrations existantes, sans `--force` | Réutilisation du groupe exporté, sauf reconstruction nécessaire pour retrouver le CFA natif |
| `--force` | Retraitement des groupes de calibration |
| `--force-stacking` | Récupération sur disque des `pp_*.fit(s)` dans les dossiers calibrés des sous-sessions et reconstruction du stack combiné |
| Stack existant compatible | Réutilisation si le rapport est terminé, les paramètres correspondent et les entrées ne sont pas plus récentes |
| `--keep-intermediate` | Conservation des répertoires temporaires de séquences de calibration |
| `--purge-target` | Suppression préalable des arborescences de sortie et de travail de la session |

La présence de calibrations suffit à permettre leur réutilisation : le cache
de groupe n’est pas une vérification complète de tous les paramètres de
calibration. Le cache du stack dispose, lui, de son rapport et de ses réglages.
`--force-stacking` ne signifie pas à lui seul que les calibrations sont refaites.

`--dry-run` simule la calibration et annonce les étapes de stacking et de
mosaïque sans les exécuter.
L’option de purge est traitée avant ce parcours et n’est pas protégée par le
mode simulation dans le code actuel.

## Lecture des journaux et rapports

Les statistiques de sous-session distinguent les métadonnées invalides, les types
non light, l’absence de dark et les erreurs de traitement. Le stack compte
ensuite les incompatibilités FITS et les rejets d’alignement ou de qualité.
Les entrées répétées pour la pondération sont distinguées des poses indépendantes.

Le rapport `<session>_combined.drizzle.json` contient les réglages, le diagnostic
Drizzle, les poses retenues et les contrôles géométriques. `quality_selection`
décrit la sélection initiale ; `final_selection` tient compte des contrôles
supplémentaires avant l’application des alignements.

`stellar_profiles` référence le rapport détaillé
`02_quality/stellar_profiles.json` : mesures par étoile, références, seuils,
fractions concordantes et motifs de rejet. Les résultats non concluants y sont
explicites. Voir [les critères de sélection](../filter/stacking/IMAGE_SELECTION.md)
pour les limites de traçabilité de chaque mesure.
