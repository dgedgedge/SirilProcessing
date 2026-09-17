# Sélection des images pour le stacking

[Documentation](../../README.md) › [Filtrage](../README.md) › [Stacking](README.md)

Dernière vérification du code : 17 septembre 2026.

Ce document décrit le comportement implémenté pour le stack final des lights.
Il sert de référence à mettre à jour lors des changements de sélection, de
pondération ou de valeurs par défaut. Les limites des contrôles existants sont précisées.

## Ordre des contrôles

1. Compatibilité des fichiers FITS : conservation du groupe majoritaire de même
   structure d’image ; exclusion des structures incompatibles et des fichiers
   illisibles lorsqu’un groupe lisible existe. Si aucun fichier n’est lisible,
   ce contrôle laisse passer les entrées. Une seule entrée restante est retournée
   directement, sans passer par les étapes suivantes.
2. Détection des étoiles et alignement avec Siril sur toutes les entrées compatibles,
   sans rejet de qualité ni pondération préalable.
3. Dans `02_quality`, exclusion des entrées non incluses par Siril, des mesures
   invalides et des transformations géométriquement incohérentes, puis filtres successifs :
   plafond FWHM non pondérée, FWHM pondérée par seuil, rejet proportionnel FWHM,
   rondeur, nombre d’étoiles, puis profils stellaires (R80 et allongement cohérent).
4. Pondérations FWHM et rondeur, uniquement sur les poses retenues.
5. Analyse Drizzle, application de l’alignement aux entrées sélectionnées avec
   `seqapplyreg -filter-included`, puis empilement. Tout nouvel alignement,
   après dématriçage ou réalignement robuste, est contrôlé avant son application.

Ce parcours unique s’applique aux appels CLI et Python, avec ou sans Drizzle.
La sélection précède le rééchantillonnage pour travailler sur les mesures natives.
La compatibilité FITS reste contrôlée avant l’alignement, car Siril a besoin
d’une séquence homogène.

## Cohérence géométrique des alignements

`filter_registration_geometry()` dans `lib/quality_filter.py` contrôle les
transformations via les objets `SirilSequence` et `SequenceImage`. Ce contrôle
est systématique avant l’application de chaque alignement ; il ne recalcule
pas les seuils de qualité FWHM, rondeur ou nombre d’étoiles.

Il rejette une entrée si :

- ses mesures ou sa matrice sont invalides ;
- sa projection devient singulière dans le champ ;
- sa transformation inverse l’orientation (déterminant local négatif) ;
- une échelle locale sort de l’intervalle `[0,5 ; 2]`, vérifié au centre et aux
  quatre coins de l’image ;
- la boîte englobante des coins transformés est entièrement hors du cadre de référence.

Les seuils d’échelle sont des garde-fous contre les erreurs grossières pour
les poses d’un même groupe, attendues à un échantillonnage comparable. Ils
ne constituent pas un critère fin de netteté. Une rotation de 180° conserve
l’orientation et reste acceptée. Un décalage plaçant seulement une partie de
l’image hors du cadre reste accepté. Le contrôle de recouvrement par boîte
englobante est conservateur : il ne mesure pas une fraction exacte de recouvrement.

Dans le traitement standard, le dématriçage est suivi d’un nouvel alignement.
Le script `04_debayer_registration.sps` se termine après ce calcul. Python
contrôle alors la séquence `debayer_*.seq` et écrit les exclusions avant de
permettre son rééchantillonnage. Le réalignement robuste utilise de même
`04_realign.sps`, puis un contrôle avant `04_stacking.sps`. Ces scripts restent
dans `04_stacking/`. Les alignements supplémentaires sont inutilisés en mode
Drizzle actif, où les transformations initiales contrôlées sont appliquées.

Une nouvelle séquence ne peut pas réinclure une entrée rejetée précédemment.
Chaque rejet est journalisé avec le fichier, son indice et sa raison. Si une
séquence est illisible ou ne contient plus aucune entrée admissible après le
contrôle, le stack s’arrête avant d’appliquer cette transformation.

Le rapport JSON conserve les contrôles dans `geometry_checks` et les comptes
finaux dans `final_selection` (poses indépendantes et entrées pondérées).
`quality_selection` décrit la sélection initiale dans `02_quality`, qui peut
être réduite par les contrôles d’alignement ultérieurs.

## Réglages par défaut

Ces valeurs sont celles de `Config.DEFAULTS` dans [lib/config.py](../../../lib/config.py).
Une configuration enregistrée ou des arguments CLI peuvent les remplacer.

| Critère | Option CLI | Défaut | Effet |
|---|---|---|---|
| Rejet FWHM proportionnel | `--fwhm-reject-percent` | `0` | Désactivé ; cumulable avec le filtre par seuil |
| FWHM pondérée Siril | `--fwhm-filter` | `1.8k` | Conserve les valeurs faibles |
| Rondeur Siril | `--roundness-filter` | `1.8k` | Conserve les valeurs élevées |
| Nombre d’étoiles Siril | `--nbstars-filter` | `1.8k` | Rejette les écarts dans les deux sens autour de la médiane |
| Plafond FWHM non pondérée | `--max-fwhm` | `0` | Désactivé ; plafond absolu en pixels natifs |
| Profils stellaires | `--stellar-profile-filter` / `--no-stellar-profile-filter` | Activé | Rejette un étalement ou un allongement généralisé sur les poses restantes |
| Seuil robuste des profils | `--stellar-profile-sigma` | `3` | Coefficient de dispersion robuste, avec marges minimales |
| Pondération FWHM | `--no-fwhm-weighted` pour désactiver | Activée | Répète davantage les meilleures entrées |
| Répétitions FWHM supplémentaires | `--fwhm-weight-max-extra` | `1` | Maximum supplémentaire par entrée mesurée |
| Pondération rondeur | `--no-roundness-weighted` pour désactiver | Activée | Répète davantage les entrées les plus rondes |
| Répétitions rondeur supplémentaires | `--roundness-weight-max-extra` | `1` | Maximum supplémentaire par entrée |

## Provenance des mesures

La FWHM, la rondeur et le nombre d’étoiles proviennent de Siril.
Le contrôle complémentaire des profils mesure directement les pixels des FITS
natifs via les objets `SequenceImage`. Voir [sa méthode et ses sources](STELLAR_PROFILE_FILTER.md).

Pour `quality_mask()` dans [lib/quality_filter.py](../../../lib/quality_filter.py), les mesures proviennent du fichier `<séquence>.seq` produit
par Siril à l’étape `01_registration`, puis copié dans `02_quality`.
`SirilSequence.read()` dans [lib/siril_sequence.py](../../../lib/siril_sequence.py)
lit les entrées et expose les mesures via les objets `SequenceImage` et
`RegistrationData`. Voir [le module de séquences](../../SIRIL_SEQUENCE.md) pour la
lecture, les chemins de fichiers, la modification et l’écriture. Le premier groupe
de lignes d’alignement est utilisé.

| Indice dans les six valeurs | Mesure utilisée par le code |
|---|---|
| `0` | FWHM non pondérée : doit être positive ; plafond `--max-fwhm`, pondération et diagnostic Drizzle |
| `1` | FWHM pondérée : utilisée par `--fwhm-filter` et `--fwhm-reject-percent` |
| `2` | Rondeur : utilisée par `roundness_filter` |
| `5` | Nombre d’étoiles : utilisé par `nbstars_filter` |

`quality_mask()` ne mesure donc pas les étoiles dans les pixels. Elle calcule des
seuils à partir de ces mesures et renvoie un masque de sélection.

## Calcul des seuils de qualité

Les filtres `--fwhm-filter` et `--roundness-filter` acceptent les formats suivants. Les comparaisons incluent
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

Les seuils sont calculés sur les poses indépendantes avant toute pondération. Le calcul est séquentiel : la rondeur
utilise les survivantes des filtres FWHM, puis le nombre d’étoiles utilise les
survivantes du filtre de rondeur. Les six mesures doivent être finies et
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

La médiane concerne le groupe de stacking après FWHM et rondeur, sans séparation
par nuit. Des conditions ou cadrages différents peuvent invalider l’hypothèse
d’un nombre constant. Avec peu de poses ou une majorité défectueuse, la médiane
ne permet pas d’identifier de façon fiable les anomalies.

Un coefficient `k` plus petit rend le filtre plus strict. Si la MAD est nulle,
le seuil est exactement la médiane. Un seuil relatif peut laisser passer une
série uniformément mauvaise : il compare les images entre elles.

## Plafond absolu sur la FWHM non pondérée

`--max-fwhm` fixe la largeur maximale des étoiles en **pixels natifs**,
mesurée par Siril (première valeur d’une ligne `R`). Une pose est conservée si
sa FWHM est inférieure ou égale au plafond. Le défaut `0` désactive ce contrôle ;
une valeur négative ou non finie est invalide.

Ce plafond s’applique dans `quality_mask()` après la validité des mesures,
avant les filtres sur la FWHM pondérée, la rondeur et le nombre d’étoiles.
Il est indépendant de ces filtres et se cumule avec eux. Il ne dépend ni de
la dispersion du groupe ni d’un excès d’étoiles.

Exemple : `--max-fwhm 6` rejette les poses mesurées à 7,18 et 6,40 pixels,
et conserve celle à exactement 6 pixels. **6 pixels est un exemple, pas une
valeur recommandée universelle.** Le plafond doit être choisi en comparant des
poses bonnes et mauvaises à même échantillonnage. Le filtre statistique porte
ensuite sur les survivantes de ce plafond.

La FWHM décrit la largeur du cœur des étoiles. Un dédoublement ou des ailes
étalées peuvent échapper à cette mesure ; ce contrôle ne garantit donc pas
le rejet de tout bougé. Le filtre complémentaire des profils stellaires mesure
le R80 et l’allongement directement dans les pixels.

## Les deux modes de rejet sur la FWHM pondérée

Les deux options utilisent **la même mesure par image : la FWHM pondérée fournie
par Siril**. Une valeur plus faible est mieux classée. Elles interviennent au
même endroit, dans `02_quality`, après la validité et le plafond non pondéré, avant les
filtres de rondeur et du nombre d’étoiles. Elles diffèrent par la règle qui
décide combien d’images garder.

### 1. `--fwhm-filter` : définir un seuil de qualité

C’est le mode actif par défaut, avec `1.8k`. Il calcule un plafond :

```text
seuil = médiane des FWHM pondérées + 1,8 × dispersion robuste
image conservée si sa FWHM pondérée ≤ seuil
```

La dispersion robuste est `1,4826 × MAD`, calculée sur les poses valides restantes après le plafond non pondéré.
**Le nombre d’images rejetées n’est pas fixé à l’avance** : seules celles qui
dépassent ce plafond sont retirées. Toutes peuvent passer si aucune ne dépasse
le seuil.

Exemple : médiane de 4 et dispersion robuste de 0,5 → seuil de 4,9.
Les images à 4,8 sont conservées ; celles à 5,2 sont rejetées. Le même seuil
peut retirer 2 images sur une série et 20 sur une autre.

Ce mode accepte aussi :

- `--fwhm-filter 5` : plafond fixe de 5 dans la mesure FWHM pondérée Siril ;
- `--fwhm-filter '90%'` : plafond calculé au 90e percentile, pour conserver
  environ les 90 % de valeurs les plus faibles ;
- `--fwhm-filter none` : désactivation de ce mode.

### 2. `--fwhm-reject-percent` : retirer une proportion des moins bonnes

Ce mode est désactivé par défaut (`0`). Il fonctionne indépendamment du filtre
par seuil et peut se cumuler avec lui. Pour utiliser uniquement le pourcentage :

```bash
--fwhm-filter none --fwhm-reject-percent 10
```

Il trie les images par FWHM pondérée croissante et retire les dernières selon
le pourcentage demandé. **Il ne cherche pas à savoir si elles sont anormalement
mauvaises** : il peut retirer des images très proches des meilleures.

Exemple : sur 100 images valides, `10` retire les 10 valeurs les plus élevées,
même si toutes les FWHM sont comprises entre 4 et 4,1. Avec `1.8k`, le rejet
dépendrait de leur répartition et pourrait être nul.

Pour `N` images restantes après le filtre par seuil (ou toutes les images
valides si ce filtre est désactivé) et un pourcentage `p`, le nombre conservé est :

```text
min(N, max(2, plafond(N × (1 − p / 100))))
```

`p` doit être compris entre 0 et 95. Le minimum de deux images et l’arrondi
réduisent parfois le rejet effectif : sur 313 images valides, `10` en conserve
282 et en retire 31. En cas d’égalité de FWHM à la frontière, l’ordre de la
séquence départage les images pour respecter ce nombre.

### Différence entre les deux options en pourcentage

`--fwhm-filter '90%'` indique la **proportion à conserver** et compare chaque
valeur à un percentile. `--fwhm-reject-percent 10` indique la **proportion à
retirer** et sélectionne un nombre d’images après tri.

Les résultats sont souvent proches, mais les égalités et les arrondis peuvent
les distinguer. Si 100 images ont toutes la même FWHM, `--fwhm-filter '90%'`
les conserve toutes, car elles sont toutes égales au seuil ; le mode de rejet
à `10` en retire 10. Le mode percentile n’impose pas le minimum de deux images
du rejet proportionnel.

### Cumul et désactivation

| Réglages | Règle effectivement appliquée |
|---|---|
| `--fwhm-filter 1.8k` | Seuil médiane + 1,8 × dispersion robuste |
| `--fwhm-filter 1.8k --fwhm-reject-percent 10` | Seuil statistique, puis retrait de 10 % des images restantes, sous réserve des arrondis et du minimum de deux |
| `--fwhm-filter none --fwhm-reject-percent 10` | Rejet proportionnel des moins bonnes FWHM |
| `--fwhm-filter none --no-fwhm-reject --max-fwhm 0` | Aucun rejet FWHM ; les contrôles de validité restent actifs |

**Les deux filtres sont indépendants et cumulables dans la même étape de qualité.**
Le seuil est appliqué d’abord, puis le pourcentage sur ses survivantes, avant
la rondeur et le nombre d’étoiles. Chaque filtre a son propre log de retraits.

Exemple : sur 100 images valides, si `1.8k` en retire 20, un rejet proportionnel
à `10` retire ensuite 8 des 80 restantes. Il reste 72 images avant les autres
critères. Le pourcentage porte sur les survivantes, pas sur les 100 entrées.

Par défaut, seul `1.8k` est actif parmi les filtres FWHM.
`--no-fwhm-reject` désactive seulement le mode proportionnel ; cette option
seule ne désactive pas `--fwhm-filter`. Réciproquement, `--fwhm-filter none`
ne désactive pas un pourcentage explicitement configuré. Aucune des deux options
ne désactive le plafond non pondéré `--max-fwhm` s’il est configuré.

## Pondérations après sélection

La « FWHM pondérée » est le nom de la mesure Siril utilisée pour la sélection.
La « pondération FWHM » ci-dessous est une autre opération : elle augmente la
contribution des images retenues au stack. Ces répétitions ne constituent pas un filtre de sélection.

`apply_quality_weights()` dans `lib/quality_filter.py` utilise les objets de
séquence pour ajouter les répétitions
après tous les filtres. La pondération FWHM utilise la FWHM non pondérée Siril,
avec une qualité normalisée entre la meilleure et la moins bonne pose retenue.
Elle est appliquée au-delà de deux poses retenues. La rondeur utilise une
normalisation analogue, dans l’autre sens. Les multiplicités se multiplient ;
avec les maxima par défaut, une pose peut contribuer jusqu’à quatre entrées.
Elles ne constituent pas un rejet et ne modifient pas les seuils calculés.
Les diagnostics utilisent les poses indépendantes.

## Étoiles dédoublées : limite actuelle

**Le filtre bilatéral détecte un nombre d’étoiles aberrant, pas directement le dédoublement.**
Le filtre du nombre d’étoiles ne recherche ni deux pics dans le profil d’une
étoile, ni un déplacement commun entre des paires. Le
[filtre des profils stellaires](STELLAR_PROFILE_FILTER.md) complète ce contrôle
en mesurant l’étalement R80 et l’allongement sur plusieurs étoiles, même si
leur nombre total est ordinaire. Il ne cherche pas à ajuster un modèle double.

La FWHM ou la rondeur peuvent entraîner un rejet si le défaut dégrade ces mesures.
Le filtre bilatéral peut rejeter un excès d’étoiles associé à des composantes
dédoublées, ou un déficit associé aux nuages. Cela reste un indicateur : si le
défaut ne modifie pas assez le nombre détecté, la pose peut être conservée.
Le filtre ne détermine pas la cause physique d’une anomalie.

Pour analyser un cas, comparer les FITS d’entrée, les images après application
de l’alignement et le stack final permet de situer l’apparition du défaut.
Il faut ensuite examiner les mesures des poses concernées pour comprendre
pourquoi elles passent les filtres existants.

## Rapports et limites de traçabilité

Les logs INFO de sélection indiquent les images retirées et restantes pour
chaque contrôle : validité des mesures, plafond FWHM non pondérée, FWHM pondérée par seuil, FWHM
proportionnelle, rondeur et nombre d’étoiles, puis un bilan des mesures Siril.
Le contrôle des profils journalise ensuite les retraits R80, les retraits
d’allongement et son bilan. Les comptes sont séquentiels : une image déjà
retirée ne compte pas à nouveau pour le critère suivant. Ils portent sur les
poses indépendantes avant toute pondération. Les filtres désactivés ou sans
image restante sont explicitement signalés. Ces comptes concernent
`quality_mask()` et `filter_stellar_profiles()` ; la compatibilité FITS possède ses propres logs. Le contrôle
« mesures valides » inclut les échecs d’inclusion ou de transformation Siril.

Le fichier `<sortie>.drizzle.json` contient notamment :

- `settings` : réglages employés ;
- `quality_selection` : nombre de poses retenues, rejetées et entrées effectives ;
- `frames` : poses retenues, FWHM non pondérée, rondeur, nombre d’étoiles,
  transformations, `fwhm_multiplicity`, `roundness_multiplicity` et
  `quality_multiplicity` (produit des deux) ;
- `analysis` : décision Drizzle et ses raisons.
- `stellar_profiles` : bilan du filtre de profils et chemin vers les mesures
  détaillées dans `02_quality/stellar_profiles.json`.

Pour les critères Siril, le rapport ne fournit pas les seuils effectifs de chaque filtre, ni les mesures
et le motif individuel de rejet pour toutes les poses exclues. La FWHM pondérée
utilisée pour sélectionner n’est pas enregistrée dans `frames`. Pour examiner
ces valeurs, consulter la séquence de `01_registration`. Le journal indique
la médiane, la tolérance et les bornes effectives du filtre du nombre d’étoiles.

La limite FWHM du diagnostic Drizzle décide de l’activation de Drizzle ; elle
n’est pas un filtre supplémentaire de sélection des poses. Le rejet de pixels
pendant l’empilement est également distinct du rejet d’images décrit ici.

## Maintenance

Le test `test_nbstars_default_rejects_cloud_and_doubled_counts` dans
[tests/test_quality_filter.py](../../../tests/test_quality_filter.py) simule une séquence Siril de
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

- [lib/config.py](../../../lib/config.py) et [bin/lightProcess.py](../../../bin/lightProcess.py) : valeurs par défaut et options ;
- [lib/lightprocessor.py](../../../lib/lightprocessor.py) : compatibilité FITS et préparation des entrées ;
- [lib/quality_filter.py](../../../lib/quality_filter.py) : `quality_mask()`, filtres FWHM, rondeur et nombre d’étoiles, application de la sélection et pondérations FWHM/rondeur ;
- [lib/drizzle.py](../../../lib/drizzle.py) : lecture des mesures Siril, orchestration du stack et diagnostic Drizzle ;
- [tests/test_quality_filter.py](../../../tests/test_quality_filter.py), [tests/test_drizzle.py](../../../tests/test_drizzle.py) et [tests/test_lightprocessor_calibration.py](../../../tests/test_lightprocessor_calibration.py) : vérification des comportements ;
- [Spécification Drizzle](DRIZZLE_SPECIFICATION.md) : diagnostic et rééchantillonnage.
