# Options de lightProcess

[Documentation des scripts](../README.md) › [lightProcess](GUIDE.md)

<a id="options-par-tape"></a>
<a id="options-par-étape"></a>

## 1. Options par étape

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
| Post-traitement | `--postprocess`, `--no-postprocess`, mêmes options que `postProcess` |

Les défauts d’empilement sont `--rejection-method winsorizedsigma` et
`--stack-method average`, comme dans `darkLibUpdate`. Le nom court historique
`winsor` reste accepté par `lightProcess`. Les valeurs JSON explicites restent
prioritaires sur ces défauts.

`--help` expose toutes les options. Les réglages peuvent provenir du fichier
persistant de configuration ; `--save-config` enregistre les arguments.

`--temperature-precision` vaut **0,5 °C** par défaut, comme dans
`darkLibUpdate`. Ce pas d'arrondi sert au regroupement des poses et à la
recherche des masters darks. Une valeur explicite en configuration ou en ligne
de commande reste prioritaire.

Pendant la calibration, les titres `=== Création du master flat … ===` puis
`=== Calibration des lights … ===` mettent les opérations principales en
évidence au niveau INFO. Une reprise annonce les lights déjà calibrées.
`--log-level DEBUG` donne les caractéristiques et préparations détaillées.
Voir [les étapes dans le journal](treatments/CALIBRATION.md#étapes-dans-le-journal).

Le stacking affiche aussi un bandeau pour chaque opération : préparation,
alignement, sélection, choix Drizzle, éventuels dématriçage et réalignement,
application des transformations, pondérations finales et empilement.
Voir [les bandeaux du stacking](treatments/STACKING.md#bandeaux-de-progression).

Le script détient une [protection énergétique facultative](../postProcess/power-guard.md)
pendant tout le parcours de calcul, de la calibration aux exports finaux.
Ce contexte unique reste actif entre les sous-sessions et les traitements,
même avec `--no-postprocess`, et est libéré sur erreur ou interruption.
La simulation `--dry-run` ne l’active pas.

Le [post-traitement commun](../postProcess/pipeline.md) est activé par
défaut dans lightProcess : les commandes habituelles l’enchaînent sans option.
`--no-postprocess` le désactive. Il traite chaque stack final, ou uniquement
la mosaïque si `--mosaic` est demandé. Ses paramètres suivent les mêmes défauts
et la même configuration que le script autonome. `--dry-run` l’annonce sans
l’exécuter ; une erreur de rendu entraîne un code de retour 1.

## Reprendre seulement le rendu final

Ajouter `--autoghs-only` à la même commande, avec les mêmes sessions, chemins,
mode mosaïque et réglages. Le programme réutilise les produits finaux et le FITS
préparé du traitement précédent, sans calibration, stacking ni mosaïque.
Les rapports et les intermédiaires nécessaires doivent exister ; une reprise
impossible est signalée sans recalcul automatique. Les exports et annotations
sont remis à jour. La saturation suit GHS par défaut dans cette reprise, même
si le JSON la désactive ; `--disable-final-color` permet de l'écarter explicitement.
Les options `only` indiquent le point de reprise du processus.
Voir [la référence commune](../postProcess/README.md#relance-autoghs-seul).

`--color-only` reprend encore plus tard, sur les couches GHS/étoiles rendues,
sans StarNet ni AutoGHS. La passe couleur suit AutoGHS par défaut dans le
traitement complet ; `--disable-final-color` la désactive, et
`--final-color-strength` règle sa force (3 par défaut, dans `[0,3]`). Ces réglages
sont communs avec postProcess et mémorisables avec `-S`, sauf le mode de reprise.
Voir [couleur finale et provenance](../postProcess/final-color.md).
