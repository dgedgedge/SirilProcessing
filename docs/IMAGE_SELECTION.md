# Sélection des images pour le stacking

Dernière vérification du code : 16 septembre 2026.

Ce document décrit le comportement implémenté pour le stack final des lights.
Il sert de référence à mettre à jour lors des changements de sélection, de
pondération ou de valeurs par défaut. Les limites et les améliorations envisagées
sont distinguées des contrôles existants.

## Ordre des contrôles

1. Compatibilité des fichiers FITS : conservation du groupe majoritaire de même
   structure d’image ; exclusion des structures incompatibles et des fichiers
   illisibles lorsqu’un groupe lisible existe. Si aucun fichier n’est lisible,
   ce contrôle laisse passer les entrées. Une seule entrée restante est retournée
   directement, sans passer par les étapes suivantes.
2. Préfiltrage par une estimation FWHM calculée en Python, puis pondération FWHM.
3. Détection des étoiles et alignement avec Siril (`seqfindstar`, `register`).
4. Dans `02_quality`, filtres successifs : FWHM pondérée, rondeur, nombre d’étoiles.
5. Contrôle de l’inclusion Siril et de la validité des transformations, puis
   pondération de rondeur.
6. Analyse Drizzle, application de l’alignement aux entrées sélectionnées avec
   `seqapplyreg -filter-included`, puis empilement.

Le pipeline de qualité fonctionne aussi avec Drizzle désactivé. Le chemin décrit
est celui utilisé par la configuration CLI habituelle. Un appel Python à
`stack_session_outputs` sans les clés `drizzle`, `nbstars_filter` et
`roundness_weighted` utilise un ancien chemin qui transmet uniquement les filtres
FWHM et rondeur directement à Siril.

## Réglages par défaut

Ces valeurs sont celles de `Config.DEFAULTS` dans [lib/config.py](../lib/config.py).
Une configuration enregistrée ou des arguments CLI peuvent les remplacer.

| Critère | Option CLI | Défaut | Effet |
|---|---|---|---|
| Préfiltrage FWHM | `--fwhm-reject-percent` | `10` | Écarte une proportion des FWHM estimées les plus élevées |
| FWHM pondérée Siril | `--fwhm-filter` | `1.8k` | Conserve les valeurs faibles |
| Rondeur Siril | `--roundness-filter` | `1.8k` | Conserve les valeurs élevées |
| Nombre d’étoiles Siril | `--nbstars-filter` | `1.8k` | Rejette les écarts dans les deux sens autour de la médiane |
| Pondération FWHM | `--no-fwhm-weighted` pour désactiver | Activée | Répète davantage les meilleures entrées |
| Répétitions FWHM supplémentaires | `--fwhm-weight-max-extra` | `1` | Maximum supplémentaire par entrée mesurée |
| Pondération rondeur | `--no-roundness-weighted` pour désactiver | Activée | Répète davantage les entrées les plus rondes |
| Répétitions rondeur supplémentaires | `--roundness-weight-max-extra` | `1` | Maximum supplémentaire par entrée |

## Provenance des mesures

Le préfiltrage utilise `_estimate_frame_fwhm()` et `_rank_files_by_fwhm()` dans
[lib/lightprocessor.py](../lib/lightprocessor.py). Cette estimation sur les FITS
est distincte des mesures utilisées après l’alignement.

Pour `quality_mask()` dans [lib/quality_filter.py](../lib/quality_filter.py), les mesures proviennent du fichier `<séquence>.seq` produit
par Siril à l’étape `01_registration`, puis copié dans `02_quality`.
`read_registration()` dans [lib/drizzle.py](../lib/drizzle.py) lit les lignes `R`
et extrait six valeurs ainsi que la matrice de transformation. Le premier groupe
de lignes d’alignement est utilisé.

| Indice dans les six valeurs | Mesure utilisée par le code |
|---|---|
| `0` | FWHM : doit être positive ; utilisée dans le diagnostic Drizzle |
| `1` | FWHM pondérée : utilisée par `fwhm_filter` |
| `2` | Rondeur : utilisée par `roundness_filter` |
| `5` | Nombre d’étoiles : utilisé par `nbstars_filter` |

`quality_mask()` ne mesure donc pas les étoiles dans les pixels. Elle calcule des
seuils à partir de ces mesures et renvoie un masque de sélection.

## Calcul des seuils de qualité

Les filtres FWHM et rondeur acceptent les formats suivants. Les comparaisons incluent
l’égalité au seuil.

| Format | Exemple | FWHM pondérée | Rondeur |
|---|---|---|---|
| Seuil fixe | `100` | Valeur ≤ seuil | Valeur ≥ seuil |
| Pourcentage | `80%` | Environ 80 % des valeurs les plus faibles | Environ 80 % des valeurs les plus élevées |
| Dispersion robuste | `1.8k` | Valeur ≤ médiane + 1,8 × dispersion | Valeur ≥ médiane − 1,8 × dispersion |
| Désactivation | `none` | Pas de filtre sur ce critère | Pas de filtre sur ce critère |

`off`, `false`, `0` et une chaîne vide désactivent également le filtre. Un
pourcentage doit être strictement supérieur à 0 et inférieur ou égal à 100.
Les égalités au seuil peuvent conserver plus d’images que le pourcentage demandé.

La dispersion robuste est calculée comme suit :

```text
m = médiane des valeurs
MAD = médiane des |valeur − m|
dispersion = 1,4826 × MAD
```

Les seuils sont calculés sur les poses indépendantes : les répétitions dues à la
pondération FWHM ne comptent qu’une fois. Le calcul est séquentiel : la rondeur
utilise les survivantes du filtre FWHM, puis le nombre d’étoiles utilise les
survivantes des deux premiers filtres. Les six mesures doivent être finies et
la FWHM non pondérée doit être positive dès le début.

### Filtre bilatéral du nombre d’étoiles

L’hypothèse est que les défauts sont exceptionnels et que la majorité des poses
possède un nombre d’étoiles presque constant. On conserve une pose si
`|nombre − médiane| ≤ tolérance`.

| Réglage | Tolérance autour de la médiane |
|---|---|
| `1.8k` (défaut) | 1,8 × dispersion robuste MAD |
| `30` | ±30 étoiles |
| `20%` | ±20 % du nombre médian, sans quota d’images à conserver |
| `none` | Filtre désactivé |

Pour une médiane de 200 et une dispersion de 30, `1.8k` conserve l’intervalle
[146, 254]. La MAD est préférée à la variance classique pour limiter l’influence
des valeurs aberrantes sur le seuil. Les tolérances doivent être finies et
positives ou nulles ; `0` reste un alias de désactivation, tandis que `0k` ou
`0%` ne conservent que la médiane. Une MAD nulle produit cette même sélection
stricte ; utiliser un écart absolu pour tolérer de petites fluctuations.

**Changement de comportement (version de pipeline 3)** : une valeur numérique
était auparavant un minimum, un pourcentage un quota des meilleures images et
`k` un filtre uniquement inférieur. Les configurations existantes doivent être
interprétées selon les nouvelles règles ; les anciens caches sont invalidés.

La médiane concerne le groupe de stacking après FWHM et rondeur, sans séparation
par nuit. Des conditions ou cadrages différents peuvent invalider l’hypothèse
d’un nombre constant. Avec peu de poses ou une majorité défectueuse, la médiane
ne permet pas d’identifier de façon fiable les anomalies.

Un coefficient `k` plus petit rend le filtre plus strict. Si la MAD est nulle,
le seuil est exactement la médiane. Un seuil relatif peut laisser passer une
série uniformément mauvaise : il compare les images entre elles.

## Préfiltrage FWHM et pondérations

Le préfiltrage trie les images mesurables de la meilleure à la moins bonne FWHM.
Pour `N` images mesurables et un rejet de `p` pour cent, le nombre conservé est :

```text
min(N, max(2, plafond(N × (1 − p / 100))))
```

Le pourcentage est borné entre 0 et 95. Les arrondis et le minimum de deux images
peuvent donc réduire le rejet effectif. `--no-fwhm-reject` le désactive.
Les images dont la FWHM Python est non mesurable restent dans les entrées ; si
aucune n’est mesurable, ce rejet et cette pondération sont ignorés. Le compteur
`rejected_fwhm_unmeasurable` est trompeur : il compte ces images sans traduire
une exclusion effective à cette étape.

Les pondérations ajoutent des répétitions discrètes des meilleures entrées.
Elles ne constituent pas un critère de rejet. Les multiplicités FWHM et rondeur
se multiplient ; avec les maxima par défaut, une pose peut contribuer jusqu’à
quatre entrées. Les diagnostics utilisent les poses indépendantes.

## Étoiles dédoublées : limite actuelle

**Le filtre bilatéral détecte un nombre d’étoiles aberrant, pas directement le dédoublement.**
Le code ne recherche ni deux pics dans le profil d’une étoile, ni un déplacement
commun entre des paires d’étoiles sur l’image.

La FWHM ou la rondeur peuvent entraîner un rejet si le défaut dégrade ces mesures.
Le filtre bilatéral peut rejeter un excès d’étoiles associé à des composantes
dédoublées, ou un déficit associé aux nuages. Cela reste un indicateur : si le
défaut ne modifie pas assez le nombre détecté, la pose peut être conservée.
Le filtre ne détermine pas la cause physique d’une anomalie.

Pour analyser un cas, comparer les FITS d’entrée, les images après application
de l’alignement et le stack final permet de situer l’apparition du défaut.
Il faut ensuite examiner les mesures des poses concernées pour comprendre
pourquoi elles passent les filtres existants.

Une évolution à étudier serait un indicateur de double pic ou de paires présentant
un décalage commun, validé sur des images bonnes et défectueuses, en distinguant
les véritables étoiles doubles. **Cette évolution n’est pas implémentée.**

## Rapports et limites de traçabilité

Le fichier `<sortie>.drizzle.json` contient notamment :

- `settings` : réglages employés ;
- `quality_selection` : nombre de poses retenues, rejetées et entrées effectives ;
- `frames` : poses retenues, FWHM non pondérée, rondeur, nombre d’étoiles,
  transformations et multiplicité de rondeur ;
- `analysis` : décision Drizzle et ses raisons.

Le rapport ne fournit pas les seuils effectifs de chaque filtre, ni les mesures
et le motif individuel de rejet pour toutes les poses exclues. La FWHM pondérée
utilisée pour sélectionner n’est pas enregistrée dans `frames`. Pour examiner
ces valeurs, consulter la séquence de `01_registration`. Le journal indique
la médiane, la tolérance et les bornes effectives du filtre du nombre d’étoiles.

La limite FWHM du diagnostic Drizzle décide de l’activation de Drizzle ; elle
n’est pas un filtre supplémentaire de sélection des poses. Le rejet de pixels
pendant l’empilement est également distinct du rejet d’images décrit ici.

## Maintenance

Le test `test_nbstars_default_rejects_cloud_and_doubled_counts` dans
[tests/test_quality_filter.py](../tests/test_quality_filter.py) simule une séquence Siril de
22 poses : 20 normales (198 à 202 étoiles), une à 70 étoiles et une à 390.
Il vérifie que seules les deux anomalies sont rejetées avec la configuration
par défaut, ainsi qu’avec des tolérances absolue et relative. Sans filtre du
nombre d’étoiles, toutes passent les autres critères. Il teste les comptages,
pas la détection des défauts dans les pixels de vraies images.

Pour l’exécuter seul depuis la racine du projet :

```bash
.venv/bin/python -m pytest tests/test_quality_filter.py -k nbstars_default -v
```

Lors d’une modification des critères, mettre à jour ce document dans le même
changement que le code : ordre, provenance des mesures, formule, sens du seuil,
défaut, désactivation, cas limites et champs du rapport. Pour tout nouveau
critère de dédoublement, remplacer la limite actuelle par son comportement
effectivement validé et conserver ses limites connues.

Points de référence :

- [lib/config.py](../lib/config.py) et [bin/lightProcess.py](../bin/lightProcess.py) : valeurs par défaut et options ;
- [lib/lightprocessor.py](../lib/lightprocessor.py) : compatibilité, préfiltrage et pondération FWHM ;
- [lib/quality_filter.py](../lib/quality_filter.py) : `quality_mask()`, filtres FWHM, rondeur et nombre d’étoiles, application de la sélection et pondération de rondeur ;
- [lib/drizzle.py](../lib/drizzle.py) : lecture des mesures Siril, orchestration du stack et diagnostic Drizzle ;
- [tests/test_quality_filter.py](../tests/test_quality_filter.py), [tests/test_drizzle.py](../tests/test_drizzle.py) et [tests/test_lightprocessor_calibration.py](../tests/test_lightprocessor_calibration.py) : vérification des comportements ;
- [Spécification Drizzle](DRIZZLE_SPECIFICATION.md) : diagnostic et rééchantillonnage.
