# Étirements GHS : allonger la droite logarithmique

[postProcess](README.md) › [AutoGHS](auto-ghs.md)

## 1. Objectif et place dans le rendu

`QuartileGHSProcessor` complète la recherche de présentation protégée, après
l'optimisation RANGE, l'égalisation, l'expansion et la chroma éventuelle, avant
la fusion des étoiles. Son objectif est **la plus grande longueur continue
quasi rectiligne** dans l'histogramme logarithmique des effectifs. La pente
peut être descendante ou nulle : un plateau central n'est pas privilégié face
à une droite descendante plus longue. Un second critère favorise un niveau
suffisant vers 0,875 : par défaut, 25 % de la hauteur logarithmique du canal
rouge, chaque canal étant normalisé par son propre maximum.

La recherche porte sur l'axe linéaire des intensités, après le pic principal
et jusqu'à **0,875**, fin du septième octile. Ces limites ne sont pas des
quantiles de population. Le pic principal doit rester sous 0,25. Le début du
segment est libre : il peut notamment être proche de 0,0625, moitié du premier
octile, si la distribution le permet. Cette valeur n'est pas imposée.

Le premier étirement RANGE rend le support plus exploitable. Chaque passage
réévalue la droite et les changements de pente, dont un SP proche de 0,5 si
une inflexion y est détectée. Les étoiles restent fixes. Les mêmes gardes
cumulatives protègent le fond et sa recomposition screen ; elles peuvent
limiter l'extension atteignable, même si une droite plus longue paraît possible.

## 2. Paramètres

Les options appartiennent à `QuartileGHSProcessor` et sont collectées par
`GHSOptimizer.parameter_children` / `add_arguments`. Elles suivent la priorité
CLI > JSON > défauts et sont sauvegardées uniquement sur demande explicite.

| Option | Défaut | Domaine et effet |
| --- | --- | --- |
| `--ghs-quartile-iterations` | `12` | 0 à 64 passages ; 0 désactive cette phase |
| `--ghs-quartile-max-strength` | `10` | D maximal par passage, dans ]0,10] |
| `--ghs-linearity-tolerance` | `0.05` | RMS maximal en dex autour de la droite ; de 0,005 à 0,5 |
| `--ghs-tail-height` | `0.25` | Hauteur log cible à 0,875, entre 0 et 1 ; 0 désactive ce complément |
| `--ghs-tail-channel` | `red` | Canal de référence : `red`, `green` ou `blue` ; aucune égalisation des canaux |
| `--ghs-blackpoint-loss` | `0.001` | Fraction cumulée de pixels dont un canal positif peut être annulé ; de 0 à 0,01 |

`--ghs-final-strength 0` désactive aussi cette phase. Le mode
`--ghs-final-mode fixed` conserve sa courbe comparative exacte et ne l'exécute
pas. `--ghs-quartile-iterations 0` permet de conserver seulement l'ancienne
recherche finale protégée.

Pour autoriser jusqu'à 24 passes et limiter la perte noire à 0,05 % :

```bash
.venv/bin/python bin/postProcess.py image_RGB_lineaire.fits rapports/ \
  --enable-autoghs --ghs-quartile-iterations 24 --ghs-blackpoint-loss 0.0005
```

Cet exemple décrit les options ; il nécessite une installation StarNet et une
image conforme aux [prérequis AutoGHS](auto-ghs.md#prérequis-et-entrée).

## 3. Critère et recherche

Le moteur utilise 128 classes uniformes de [0,1]. Les utilitaires sans état
`lib.ghs_linear_histogram` mesurent tous les intervalles contigus après le pic,
jusqu'à 0,875. Une droite admissible exige :

- au moins 20 classes contiguës, avec au moins trois pixels dans **chaque** classe ;
- une pente négative ou nulle, sans pénalité liée à son inclinaison ;
- un ajustement par moindres carrés de `log10(densité) = a + bY` dont le RMS
  ne dépasse pas `ghs-linearity-tolerance` ;
- aucun écart individuel dépassant trois fois cette tolérance, pour ne pas
  dissimuler une bosse isolée dans un bon RMS global.

Les classes vides interrompent le segment. Aucun pseudo-effectif ni poids
robuste n'est utilisé pour masquer une rupture. La tolérance par défaut de
0,05 dex correspond à environ 12 % d'écart multiplicatif de densité pour le
RMS ; c'est une heuristique de présentation, pas une tolérance photométrique.
Les sommes préfixées donnent les ajustements sans régressions itératives.

Dans un histogramme, le segment retenu est le **plus long** ; à longueur égale,
le RMS le plus faible départage les segments. Son score de longueur est :

`longueur + (0,25 / 128) × (1 − RMS / tolérance)`.

Le bonus RMS reste inférieur à un quart de classe : il ne peut jamais compenser
la perte d'une classe de longueur. Sans droite mesurable, un score négatif
classe seulement l'allongement du support continu pour amorcer la recherche ;
aucune linéarité n'est annoncée.

### 3.1. Niveau relatif à la fin du septième octile

Le niveau est mesuré sur un histogramme RGB distinct de **512 classes fixes**
sur [0,1], zéros inclus, sans lissage du pic. À l'intérieur d'un canal,
la hauteur est `log(effectif) / log(effectif maximal)` ; les classes de zéro
ou un pixel sont au bas de l'axe. Les deux hauteurs qui encadrent 0,875 sont
interpolées. Si le maximum ne dépasse pas un pixel, la hauteur est inconnue
(`null`), pas inventée. Chaque canal utilise son propre maximum, comme
l'affichage Siril. Le niveau 0,25 correspond à deux petites graduations sur huit,
pas à 25 % des pixels du pic.

La résolution fixe évite que redimensionner une fenêtre modifie le critère.
Elle ne reproduit pas exactement une fenêtre Siril d'une autre largeur.
Le rapport des logarithmes dépend aussi de la population totale : le nombre
réel de pixels de l'image est conservé. Pour la grille de recherche, les
effectifs sont multipliés par `pixels du champ / pixels échantillonnés` avant
les logarithmes. Ces profils portent `estimated=true` ; leur échantillon peut
manquer une queue rare. Les finalistes et la mesure finale sont recomptés sur
**tous** les pixels : seul ce résultat confirme `tail_height_achieved`.

Si les étoiles rendues sont fournies, la hauteur porte sur la recomposition
screen visible avec ces mêmes étoiles ; sans étoiles, elle porte sur le fond
seul. La longueur rectiligne reste mesurée sur le fond. Le canal choisi sert
uniquement au classement d'un étirement RGB commun, jamais à lui appliquer
une transformation séparée ni à aligner les trois histogrammes.

Le score de comparaison des rendus devient :

`score de longueur + 0,05 × min(hauteur / cible, 1)`.

Une hauteur inconnue ne donne aucun bonus. Le bonus plafonne à la cible :
aucun gain supplémentaire ne récompense un niveau supérieur. Il peut compenser
quelques classes de longueur pour éclaircir le rendu ; avec le bonus RMS,
au plus six classes sur 128 peuvent être perdues entre l'entrée mesurable et
une sortie mieux classée. Il ne peut donc pas remplacer une longue droite par
un petit plateau brillant. Avec `--ghs-tail-height 0`, le score de longueur
antérieur est conservé exactement. Toutes les gardes restent obligatoires.
Si aucune courbe admissible n'atteint la cible ou si la limite de passes est
atteinte, le meilleur rendu trouvé est conservé et la cible non atteinte est
signalée, sans forcer un étirement supplémentaire.

Une amélioration du score doit dépasser `1e-5`.

### 3.2. Exploration et validation

Chaque passage explore douze forces jusqu'à D maximal, ainsi que D=0 pour
un point noir seul, B=15 ou 5 et HP=0,75 ou 1. SP explore zéro, le pic et
jusqu'à deux changements de pente. Ceux-ci sont estimés par deux droites
locales sur huit classes de chaque côté, uniquement lorsque ces classes sont
peuplées. Les changements réguliers les plus marqués sont retenus, espacés
d'au moins un octile ; SP peut ainsi dépasser 0,25 et approcher 0,5.
Les primitives sont évaluées en float64 puis converties au type source, sans
écrêtage final. HP est le début du raccord linéaire du GHS : il ne garantit
pas que les pixels au-dessus restent inchangés.

Le classement CPU porte sur une grille régulière d'au plus 65 536 pixels.
Les candidats doivent passer les gardes sur cet échantillon ; trois au plus
sont ensuite validés sur tous les pixels du backend NumPy/CuPy déjà choisi.
Le meilleur gain admissible en pleine résolution est retenu. L'arrêt survient
sans amélioration admissible ou à la limite de passages. L'entrée reste intacte.

## 4. Point noir et protection cumulée

Le moteur essaie l'absence de recalage, un recalage juste sous le minimum RGB
positif du champ entier, et éventuellement le creux après un petit pic sombre.
La recherche du creux emploie 1 024 classes RGB, limitées au percentile 99,9 des
composantes positives. Le creux doit descendre sous 20 % de la hauteur des deux
pics voisins. Son annulation doit respecter le budget configuré.
Une distribution monotone ou un simple début de signal diffus n'est pas
assimilé systématiquement à un pic sacrifiable.

Le budget compte les **pixels**, pas les composantes RGB : un pixel est compté
une fois dès qu'un de ses canaux initialement positifs devient nul. Les pixels
entièrement noirs au départ sont exclus du dénominateur. La validation complète
compare chaque candidat à l'entrée de cette phase, pas seulement à la passe
précédente. La tolérance ne se renouvelle donc pas à chaque passage.

Les gardes de contraste et de couleur réutilisent les mesures de la
[recherche protégée](ghs-highlight-protection.md), avec des régions fixes
`Y ≥ 0,75` et `Y ≥ 0,95` pour cette phase. Une région absente ne produit pas
une mesure inventée ; le candidat est néanmoins contrôlé contre l'apparition
de nouvelles composantes proches du blanc. Le contraste mesurable doit garder
au moins `ghs-highlight-retention` de sa valeur initiale. La dérive des
proportions RGB reste limitée à 0,01 et la perte de chroma à 0,02. La médiane
du fond ne doit pas dépasser 0,25 (ou sa valeur déjà supérieure), le proxy de
bruit du fond ne doit pas être amplifié de plus de trois, et l'augmentation
de composantes RGB ≥ 0,98 est limitée à 0,001. Ces limites portent sur toute
la phase, fond et fusion compris.

## 5. Audit et limites

Le rapport conserve le nom historique `quartile_stretch` et les options
`ghs-quartile-*`. Il indique explicitement l’objectif
`longest_log_linear_span_with_tail_height` (ou
`longest_contiguous_log_linear_span` si le complément est désactivé),
les profils avant/après, les bornes
`line_start`/`line_end`, la longueur `line_span`, la pente, le RMS, l’écart
maximal, la tolérance, le score, les gardes initiales, les passages et D/B/SP/HP/BP,
les rejets sur échantillon, les validations complètes et la fraction cumulée annulée.
Chaque validation conserve ses mesures de protection. Le journal indique les
paramètres de chaque passe acceptée et sa hauteur.

Les profils `tail` précisent hauteur, cible, canal, maximum, effectif à 0,875,
échelle de population et caractère estimé. `tail_channels_before/after`
auditent séparément les trois canaux, chacun avec son maximum. `tail_image_role`
indique le fond seul ou la recomposition ; `tail_height_achieved` est confirmé
sur tous les pixels après la phase active.

`loss` vaut l’opposé du score lorsque la droite est mesurable, et reste nul
au sens JSON (`null`) sinon ; ce champ ne désigne plus une perte de platitude.

Les histogrammes ne mesurent ni la valeur scientifique ni la qualité visuelle
d'une galaxie. Une grille régulière peut manquer une petite structure ; la
validation complète rejette alors le candidat, sans augmenter le budget de
validations. Une image insuffisamment peuplée, déjà rectiligne sur toute la zone
exploitable ou contrainte par le bruit ou les hautes lumières peut rester inchangée. Les gardes ne restaurent
pas les informations perdues dans les étapes antérieures.


## 6. Vérification

Les tests utilisent une courbe avec plateau sur les octiles 2–4 puis décroissance,
et une droite continue allant de 0,0625 à 0,875 : la seconde doit être préférée.
Ils vérifient aussi qu'une pente descendante n'est pas pénalisée face à un
plateau de même longueur, qu'un trou ou une bosse coupe le segment, qu'une
rupture près de 0,5 fournit un SP candidat, et que les passes restent déterministes,
sous les budgets cumulés et sans modification des sources.
La formule de hauteur est vérifiée avec un pic de 10 000 pixels et une queue
de 10 pixels (hauteur 0,25), les maxima indépendants des canaux, le changement
de population, les niveaux inconnus, la désactivation et le plafonnement du
bonus. Un essai échantillonné est confronté au recomptage du champ complet et
la hauteur de la recomposition est vérifiée avec des étoiles fixes.
Ces cas synthétiques vérifient le critère. Ils ne constituent pas une validation
visuelle de M31 ou M33 ; une nouvelle exécution sur ces images reste nécessaire.
Le niveau d’histogramme est un proxy de présentation, pas une garantie de
luminosité perçue ni une mesure photométrique.


Référence de la normalisation : fonction `display_histo` du
[code Siril](https://github.com/lock042/siril/blob/master/src/gui-gtk4/histogram_utils.c).
