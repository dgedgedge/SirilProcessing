# lightProcess — traitement des lights

[Documentation](../../README.md)


`lightProcess.py` et son wrapper `lightProcess.sh` traitent une ou plusieurs sessions, chacune
composée d’une ou plusieurs sous-sessions. Cet ensemble décrit leur utilisation
et le détail des traitements effectués.

## Utilisation

- [Guide d’utilisation](GUIDE.md) : commandes, options et vue d’ensemble du parcours.
- [Sessions et sous-sessions](SESSIONS.md) : organisation des données d’entrée.

## Traitements détaillés

Le [parcours de traitement](treatments/README.md) relie les étapes dans leur
ordre d’exécution :

1. [Découverte, regroupement et calibration](treatments/CALIBRATION.md).
2. [Filtrage et stacking](filter/stacking/README.md), dont la
   [sélection des images](filter/stacking/IMAGE_SELECTION.md), les
   [profils stellaires](filter/stacking/STELLAR_PROFILE_FILTER.md) et le
   [Drizzle](filter/stacking/DRIZZLE_SPECIFICATION.md).
3. [Alignement et empilement final](treatments/STACKING.md).
4. [Sorties, reprise et journaux](treatments/OUTPUTS.md).
5. [Mosaïque optionnelle](MOSAIC.md).
6. [Post-traitement optionnel](../postProcess/pipeline.md) : mêmes traitements
   et exports que `postProcess`, avec `--postprocess`.

## Références

- [Séquences Siril et images associées](reference/SIRIL_SEQUENCE.md).
- [Répertoires de travail et nettoyage](WORKSPACE.md).
- [Rôle général des bibliothèques](../../architecture/LIBRARIES.md).


### Paramètres Siril partagés

`lightProcess` et `postProcess` utilisent les mêmes options `-s/--siril-path`,
`-m/--siril-mode` et le même fichier Config. `--config chemin.json` sélectionne un
fichier différent de `~/.siril_darklib_config.json` ; `-S/--save-config` mémorise les
paramètres. Les options explicites priment sur le JSON, qui prime sur les défauts.
Voir [la configuration partagée](../postProcess/README.md#configuration-et-mémorisation).
`--postprocess` enchaîne sur chaque stack final ou, avec `--mosaic`, sur la
mosaïque finale. Il utilise les mêmes options de qualité, de restauration et
de rendu que `bin/postProcess.sh`. L’enchaînement est activé par défaut, même
sans option ; `--no-postprocess` le désactive. Une désactivation mémorisée dans
le JSON reste prioritaire sur le défaut ; `--postprocess` permet de l’annuler.
Voir le [pipeline commun](../postProcess/pipeline.md)
et les [contrôles de qualité](../postProcess/quality.md).

Le lanceur utilise Python et pip du système pour créer le venv sans pip et
mettre à jour ses dépendances avant chaque exécution. Il lance ensuite le
Python du venv. La sélection de l’environnement, les prérequis et l’exécution
sans mise à jour sont décrits dans [Installation](../../INSTALLATION.md).

Les classes des traitements sont enregistrées avant le parsing. Les valeurs
résolues sont ensuite liées aux classes avant la création des instances, y
compris les enfants. Voir la [liaison des paramètres](treatments/postprocess/SEQUENCE_PROCESSOR.md#résolution-et-liaison-hiérarchique-des-paramètres).

Le résultat empilé est écrit sous `<cible>_combined.fits`. Les anciens résultats
`.fit` restent utilisables en reprise ; `--force-stacking` demande de reconstruire
le résultat avec la nouvelle extension. Voir [les sorties](treatments/OUTPUTS.md).
