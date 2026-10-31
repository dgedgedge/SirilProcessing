# Protection automatique des hautes lumières GHS

[postProcess](README.md) › [AutoGHS](auto-ghs.md)

## 1. Objectif et interface

La dernière passe AutoGHS cherche à améliorer le contraste du signal diffus sans
écraser les nuances des régions brillantes. `lib.ghs_final_stretch.ProtectedGHSFinalizer`
est un moteur `processor`, déclaré comme enfant de `GHSOptimizer`. Sa méthode
`search(data, analyzer, backend, strength, mode, retention, stars, headroom, rms_weight)`
retourne le fond retenu et l'audit. L'orchestrateur fournit la couche
d'étoiles au seul rendu RANGE ; aucun paramètre ne dépend du nom de la cible.

Le moteur intervient **après** l'égalisation, l'expansion et la chroma, avant la
fusion screen. Il ne récupère pas les détails perdus dans une étape antérieure.
Les paramètres HP des premières itérations restent à 1 ; la recherche conjointe
D/B/SP/HP décrite ici concerne exclusivement la dernière passe.

| Option | Défaut | Signification |
|---|---|---|
| `--ghs-final-mode` | `auto` | Recherche protégée ; `fixed` conserve la courbe fixe pour comparaison |
| `--ghs-final-strength` | `1.25` | D maximal recherché en mode auto, D imposé en mode fixed ; `[0,10]`, zéro désactive |
| `--ghs-highlight-retention` | `0.9` | Fraction minimale du contraste brillant mesuré à conserver ; `]0,1]` |
| `--ghs-log-rms-weight` | `2.0` | Poids du gain relatif de régularité logarithmique ; `[0,10]`, zéro désactive ce terme |

Ces options suivent CLI > JSON > défaut et sont mémorisables avec `-S`.
`--ghs-highlight-retention 0.95` exige une conservation plus stricte et peut
conduire à un étirement plus faible, voire à conserver l'image précédente.

## 2. Régions fixes et mesures

Les quantiles sont estimés sur une grille régulière contenant au plus 65 536
pixels RGB réels. Les pixels Y ≤ 10⁻⁶ sont exclus de l'estimation pour éviter
que les bordures nulles dominent les seuils. Y est calculé selon le mode
`human` ou `even` déjà sélectionné.

Les seuils sont figés avant les essais et les masques sont toujours définis par
le **fond de référence**, même lors des mesures après recomposition :

- ciel indicatif : `0 < Y ≤ P25`, avec le même plancher de 10⁻⁶ ;
- signal diffus et tons moyens indicatifs : `P75 ≤ Y ≤ P95` ;
- hautes lumières : `Y ≥ max(P98, min(0,5, P99,9))` ;
- extrêmes brillants : `Y ≥ P99,9`, pour contrôler aussi les petits cœurs.

Ce sont des classes d'intensité, sans segmentation physique des objets.
Sur un champ sombre, P98 peut encore désigner du signal peu lumineux : il ne
doit pas recevoir toutes les contraintes destinées aux hautes lumières. Le
seuil brillant monte donc vers 0,5, sans dépasser P99,9 lorsque le cœur lui-même
est plus sombre. Le masque du petit cœur reste contrôlé indépendamment.
Une région de moins de huit pixels est non mesurable, avec `null` dans l'audit.
L'absence des mesures principales ou d'un contraste brillant exploitable
empêche de certifier une amélioration. Le contrôle supplémentaire des extrêmes
est appliqué lorsqu'il est mesurable.

Le contraste relatif brillant est `(P90 − P10)/(P90 + P10)`, mesuré dans chaque
masque brillant. La médiane de `|Y₂−Y₁|/(Y₂+Y₁)` des voisins horizontaux du
masque brillant complète ce contrôle spatial. Ces mesures doivent conserver au moins
la fraction `ghs-highlight-retention` de leur référence.

Les autres gardes sont :

- proxy de bruit du ciel : MAD normalisée des différences horizontales, divisée
  par √2 ; amplification au plus ×3 lorsqu'elle est mesurable et non nulle ;
- médiane du ciel au plus 0,25, médiane brillante au plus 0,95 et médiane P99,9 au plus
  0,98, sans exiger de corriger une référence dépassant déjà ces plafonds ;
- au plus 0,001 de nouvelles composantes RGB ≥ 0,98 dans le champ ;
- dans les deux masques brillants, dérive moyenne absolue des proportions RGB
  au plus 0,01 et perte moyenne de chroma `max(RGB)−min(RGB)` normalisée au plus 0,02.

Les couleurs sont normalisées par R+G+B ; il ne s'agit pas d'une mesure
colorimétrique calibrée. Le bruit et les classes d'intensité sont également des
proxys. Aucune de ces gardes ne prouve seule la qualité visuelle.

## 3. Recherche, classement et validation complète

Tous les essais repartent du même fond rendu, avec LP=0, le modèle de
luminosité commun et RGBBlend. HP marque le début du raccord linéaire de la
courbe ; il ne laisse pas nécessairement ces pixels inchangés.
Voir [la documentation GHS de Siril](https://siril.readthedocs.io/en/stable/processing/stretching.html).

La grille initiale explore :

- D = 0,15 ; 0,30 ; 0,50 ; 0,75 et 1 fois la force maximale ;
- B = 0, 2, 5, 10 et 15 ;
- SP = 0, pic de l'histogramme positif limité à P90, P50, P75, milieu de
  l'intervalle P75–P90 et P90, sans doublons ;
- HP = P90, P95, P98, P99 et 1 de la référence, sans doublons.

Le meilleur candidat admissible est affiné autour de D (± maximum/8),
B (±2,5), SP et HP (milieux des intervalles voisins), dans les domaines autorisés.
Les combinaisons SP > HP sont ignorées. La grille comporte au plus 750 essais
initiaux et 81 essais d'affinement, les doublons étant écartés.
Il ne s'agit pas d'une optimisation continue ni d'une garantie d'optimum visuel.

Le contraste diffus est la différence entre les médianes du signal diffus et du
ciel. Sa visibilité relative vaut
`V = (médiane_diffuse − médiane_ciel)/(médiane_brillante − médiane_ciel)`.
La cible V=0,5 place les tons moyens au milieu de cet écart **propre au champ**,
sans imposer une luminosité absolue à la galaxie ou à la nébuleuse. C'est une
heuristique de présentation ; la recherche peut rester loin de cette cible
pour respecter les gardes.
Un candidat ne doit pas diminuer V sous `min(0,5,V_avant)` : si la cible est
déjà atteinte, il n'est pas nécessaire de dépasser encore cette luminosité relative.

Le score combine :

```text
G = log(gain de contraste diffus) − 0,2 log(max(1,gain de bruit))
Vscore = 0,35 log(min(0,5,V_après)/min(0,5,V_avant))
score = G + Vscore + score_histogramme
```

Les logarithmes du score sont naturels. Les ratios V sont protégés contre
zéro par un plancher numérique de 10⁻¹². **G doit dépasser 0,01**, indépendamment
du bonus d'histogramme : une droite parfaite ne rend pas admissible une courbe
sans gain diffus utile. La référence sans passe supplémentaire reste disponible.
Le score total doit également dépasser 0,01 pour retenir une amélioration.
Pour réserver une marge aux écarts d'échantillonnage, le classement exige
`1 − 0,8 × (1 − retention)` de conservation ; la validation complète utilise
la tolérance demandée.

Avec la couche d'étoiles, le moteur évalue également
`F = headroom × fond + étoiles × (1 − headroom × fond)`. Les gardes doivent
passer sur le fond **et** la fusion. Le score le plus faible des deux sert au
classement, les étoiles restant strictement identiques entre candidats.

Au plus trois finalistes sont appliqués aux dimensions complètes sur le backend
sélectionné : les meilleurs scores sur l'échantillon, avec une place réservée
au meilleur gain RMS admissible si son poids est positif. Leurs métriques sont
recalculées sur **tous les pixels**, aux mêmes seuils. Le meilleur score complet
admissible est retenu, en prenant toujours le minimum fond/fusion ; sinon,
l'identité est conservée avec `no admissible improvement`. Une erreur de backend n'est
jamais convertie en refus contrôlé ou en bascule CPU.

### 3.1. Régression de la décroissance logarithmique

`lib.ghs_log_histogram` mesure `log10(densité(Y)) = a + bY`, avec l'intensité Y
sur l'axe horizontal **linéaire**. La fenêtre commence après le pic principal
et vise **Y=0,875**, début du dernier huitième de l'échelle. Cette extension est
retenue si au moins huit des seize classes de `]0,75;0,875]` contiennent chacune
trois pixels ou plus ; sinon la borne reste **Y=0,75**, début du dernier quart.
Ce sont des intensités, pas des percentiles des pixels.

Les 128 classes uniformes sont identiques pour tous les essais. Les zéros de
bordure sont exclus et les effectifs sont normalisés par le support positif
et la largeur des classes. Le pic vient d'un lissage `[1,2,1]/4` des effectifs ;
la régression utilise ensuite les effectifs bruts des classes contenant au
moins trois pixels, sans ajouter de pseudo-effectif aux classes vides.
Il faut au moins douze classes et une étendue Y de 0,15. Sinon, la pente reste
`null` et le bonus vaut zéro. La fenêtre réellement mesurée est enregistrée :
si le signal s'arrête avant la borne, aucune queue n'est inventée.
La borne haute est choisie sur la référence échantillonnée, séparément pour le
fond et sa fusion, puis figée pour tous les candidats et les validations complètes.
Le début suit le pic de chaque distribution ; les fenêtres ne sont donc pas
strictement identiques avant/après, mais doivent suffisamment se recouvrir.

La régression utilise huit réestimations des poids de Huber, au seuil de
1,5 fois la MAD normalisée des résidus, puis un ajustement final. L'audit donne
la pente en **dex par unité Y**, l'ordonnée à l'origine, le résidu RMS en dex,
le R² pondéré, les bornes et le nombre de classes utilisées.

Pour deux pentes négatives mesurables, à borne haute identique et des fenêtres
se recouvrant sur au moins 0,15 unité Y :

```text
score_pente = 0,15 × borne(log(|b_avant|/|b_après|), −1, 1)
score_RMS = poids_RMS × borne(log((RMS_avant+0,01)/(RMS_après+0,01)), −1, 1)
score_histogramme = score_pente + score_RMS
```

Une pente plus élevée signifie ici **moins négative**, donc moins raide.
Le RMS intervient par sa réduction **relative**, plutôt que par une différence
brute en dex. Son poids par défaut vaut 2, contre 0,15 pour le terme de pente.
Le régularisateur de 0,01 dex (environ 2,3 % de densité) évite de survaloriser des
différences infimes entre deux distributions déjà presque rectilignes. Une
baisse du RMS de 0,20 à 0,15 dex donne environ +0,54 point au score ; une pente
deux fois moins raide apporte environ +0,10 point. Le poids 2 n'est pas un pourcentage.

La contribution totale reste entre −2,15 et +2,15 avec le réglage par défaut.
`--ghs-log-rms-weight 3` renforce encore la préférence pour une queue rectiligne ;
zéro retire ce terme tout en gardant le critère de pente et toutes les protections.
Le mode `fixed` n'utilise pas ce classement.
Il n'existe pas dans cet algorithme
de pente absolue à atteindre, ni de maximisation du seul R² : une distribution
presque linéaire peut encore correspondre à un objet peu visible ou à du bruit
amplifié. Le compromis retenu peut donc avoir une pente plus raide si le gain
diffus et les autres critères le justifient. Les fenêtres, le faible support,
les étoiles résiduelles et les bosses réelles du champ influencent la mesure.

Cette heuristique s'inspire de la décroissance approximativement rectiligne
décrite dans le [tutoriel GHS de Siril](https://siril.org/tutorials/ghs/).
La recherche de SP près du signal suit le rôle de ce paramètre décrit dans
la [documentation des auteurs GHS](https://www.ghsastro.co.uk/doc/tools/GeneralizedHyperbolicStretch/GeneralizedHyperbolicStretch.html).
Les fenêtres, poids et gardes ci-dessus sont des choix du projet, pas une
norme de qualité astronomique ou un modèle de perception humaine.

## 4. Audit, ressources et vérification

`presentation_stretch` conserve le mode, les paramètres choisis, les seuils,
le pas d'échantillonnage, tous les essais et leurs rejets, les validations
complètes et l'indice `selected_validation` du finaliste retenu, ainsi que les
histogrammes avant/après. `histogram_windows` conserve les bornes hautes figées.
Les mesures incluent
`diffuse_visibility` et `log_histogram`. Les figures et le journal indiquent D/B/SP/HP.
Le classement est fait en NumPy sur l'échantillon ; les validations et le
transfert final utilisent le backend CPU/CUDA sélectionné. Les tableaux de
référence, candidats, masques et fusions nécessitent plusieurs fois la taille du
RGB ; les finalistes sont évalués séquentiellement.

En mode `fixed`, B=SP=LP=0 et HP=1 : cette ancienne courbe n'utilise pas les
gardes de recherche et peut amplifier davantage le bruit et comprimer les
hautes lumières. Le domaine et la finitude des pixels restent contrôlés.

`tests/test_ghs_log_histogram.py` couvre une pente analytique exponentielle,
les bosses secondaires, les changements de support, les données absentes,
l'extension conditionnelle à 0,875 et la priorité relative du RMS.
`tests/test_ghs_final_stretch.py` couvre trois distributions synthétiques
(galaxie, nébuleuse et champ à concentrations ponctuelles), les mesures de
contraste, les fusions, les bordures nulles, les paramètres invalides et le
refus contrôlé, ainsi que la parité des mesures CPU/CUDA lorsque disponible.
Les tests d'orchestration vérifient le seul traitement RANGE et le
passage de la couche d'étoiles au moteur, le meilleur score complet et le maintien
de l'identité pour une nébuleuse déjà presque rectiligne. Les essais M31 et M33
repartent chacun du même fond recalculé avant la dernière passe, à partir des
couches linéaires StarNet existantes, avec les mêmes étoiles rendues. Ils
comparent le classement précédent au RMS renforcé ; ils ne constituent pas une
validation sur tous les types de champs réels ni une nouvelle validation de StarNet.
