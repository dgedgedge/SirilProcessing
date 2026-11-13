# Étirements GHS : allonger la droite logarithmique

[postProcess](README.md) › [AutoGHS](auto-ghs.md)

Cette page décrit le mode historique `--ghs-histogram-model line`, sélectionnable explicitement.
Le mode par défaut `piecewise`, ses splines et ses contraintes de conservation
de forme sont décrits dans [Profil par morceaux](ghs-histogram-shape.md).
Dans la recherche finale `piecewise`, les déficits de contraste brillant et de
forme deviennent des pénalités progressives ; le cœur et les gardes de sécurité
restent obligatoires. Les niveaux lumineux sont des bonus bornés du score de
rendu ; les critères stricts décrits ci-dessous concernent le mode `line`.
La [série des essais](ghs-histogram-shape.md#15-série-de-graphiques-exclusivement-logarithmiques)
est exportée dans les deux modes, uniquement en échelle logarithmique.

## 1. Objectif et place dans le rendu

`LogHistogramRenderer` ([module de sélection](../../../lib/ghs_log_render.py))
complète la recherche de présentation protégée, après
l'optimisation RANGE, l'égalisation, l'expansion et la chroma éventuelle, avant
la fusion des étoiles. Son objectif est **la plus grande longueur continue
quasi rectiligne** dans l'histogramme logarithmique des effectifs. La pente
peut être descendante ou nulle : un plateau central n'est pas privilégié face
à une droite descendante plus longue. Un second critère favorise un niveau
suffisant vers 0,875 : par défaut, 25 % de la hauteur logarithmique du canal
rouge, chaque canal étant normalisé par son propre maximum.

La recherche porte sur l'axe linéaire des intensités, après le pic principal
et jusqu'à **0,875**, fin du septième octile. Ces limites ne sont pas des
quantiles de population. Le pic principal doit rester sous 0,25. Le segment
commence à sa sortie : première classe à droite du maximum où les effectifs
lissés `[1,2,1]/4` descendent à 2 % du maximum lissé. Cette définition
de la sortie à 2 % est une heuristique explicite. Aucun trou ni bosse ne
peut être contourné en choisissant une droite située plus loin.
Si les vingt classes suivant immédiatement le maximum sont déjà rectilignes
selon les mêmes tolérances, cette décroissance n'a pas d'épaulement à exclure :
la droite commence directement après le maximum (`peak_exit_method=already_linear`).
Le seuil relatif ne déplace donc pas le début d'une distribution exponentielle
déjà rectiligne vers sa queue rare.

Deux moteurs indépendants produisent des candidats sur la même entrée :

- `LogSpanGHSProcessor` conserve la recherche de longueur : B=15/5,
  HP=0,75/1, douze forces et trois finalistes par passe. Ses transformations
  intermédiaires peuvent réduire la luminosité diffuse, mais sa sortie finale
  est rejetée si elle n'en retrouve pas la valeur initiale et la séparation
  au ciel. Il ne modifie pas les paramètres demandés du second moteur.
- `QuartileGHSProcessor` recherche le niveau à 0,8 avec les faibles forces,
  raccords précoces, finalistes diversifiés et conservation brillante adaptée.
  Il contrôle aussi la luminosité diffuse pendant ses passes.

Si leurs sorties n'atteignent pas les objectifs, `PopulationGHSProcessor`
effectue une troisième recherche depuis la même entrée. Il représente les
4096 classes uniformes de luminosité par au plus 16 pixels chacune, répartis
dans l'ordre spatial, avec un poids égal à leur population divisée par le
nombre de représentants. Les pixels Y ≤ 1e-6 ont leur strate distincte.
Les classes rares sont ainsi conservées dans les prédictions, au lieu de
disparaître dans une grille spatiale clairsemée. Les mêmes étoiles aux mêmes
positions servent à prédire le rendu recomposé. Ces histogrammes pondérés
restent des estimations : les gardes utilisent la grille spatiale et la sonde
brillante, puis les finalistes sont vérifiés sur tous les pixels. Cette reprise
ne relâche aucune protection et est omise si les deux premières recherches
fournissent déjà un rendu atteignant les objectifs.

Le sélecteur recompte leurs objectifs sur tous les pixels. Il préfère les
sorties qui atteignent l'ensemble des objectifs, puis le nombre d'objectifs
atteints, puis la longueur rectiligne globale et le score de contrôle. Une
entrée inchangée reste candidate ; un moteur dont le résultat final assombrit
le diffus ne peut pas être sélectionné. Le choix est indépendant du nom de
l'objet. Les deux rendus restent en RAM après leur calcul, sans conserver
leurs images sur le GPU ni produire deux sorties publiées.

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
| `--ghs-quartile-block-pixels` | `262144` | Maximum de pixels spatiaux par bloc de transformation float64, de 1 à 4194304 |
| `--ghs-quartile-validations` | `12` | Budget de validations du champ entier par passe, de 1 à 64 |
| `--ghs-quartile-max-strength` | `10` | D maximal par passage, dans ]0,10] |
| `--ghs-linearity-tolerance` | `0.05` | RMS maximal en dex autour de la droite ; de 0,005 à 0,5 |
| `--ghs-regression-min-count` | `10` | Effectif minimal prédit par la droite à 0,8, par classe du graphique ; 0 désactive |
| `--ghs-tail-height` | `0.25` | Hauteur log cible à 0,875, entre 0 et 1 ; 0 désactive ce complément |
| `--ghs-tail-channel` | `red` | Canal de référence : `red`, `green` ou `blue` ; aucune égalisation des canaux |
| `--ghs-sparse-tail-fraction` | `0.001` | Fraction du signal actif à Y >= 0,8 rétablissant la protection normale ; 0 désactive l’adaptation |
| `--ghs-sparse-highlight-retention` | `0.45` | Plancher de conservation du contraste brillant lorsque la queue est rare, dans ]0,1] |
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
`lib.ghs_linear_histogram` mesurent les préfixes contigus issus du bord droit
à 2 % du maximum du pic, jusqu'à 0,875. Une droite admissible exige :

- au moins 20 classes contiguës, avec au moins trois pixels dans **chaque** classe ;
- une pente négative ou nulle, sans pénalité liée à son inclinaison ;
- un ajustement par moindres carrés de `log10(densité) = a + bY` dont le RMS
  ne dépasse pas `ghs-linearity-tolerance` ;
- aucun écart individuel dépassant trois fois cette tolérance, pour ne pas
  dissimuler une bosse isolée dans un bon RMS global.

Les classes vides interrompent le segment ; la recherche ne redémarre pas
après la rupture. Aucun pseudo-effectif ni poids robuste de régression
n'est utilisé pour masquer une rupture. La tolérance par défaut de
0,05 dex correspond à environ 12 % d'écart multiplicatif de densité pour le
RMS ; c'est une heuristique de présentation, pas une tolérance photométrique.
Les sommes préfixées donnent les ajustements sans régressions itératives.

L'évaluation globale conserve le **plus long** segment ; à longueur égale,
le RMS le plus faible départage les segments. Le contrôle du niveau à 0,8
utilise séparément le plus long segment admissible qui contient réellement
cette abscisse, s'il existe. Le rapport distingue `longest_line_after`
(portion globale) et `after` (segment de contrôle). Les deux segments doivent
satisfaire les mêmes conditions de population et de rectitude. Son score de longueur est :

`longueur + (0,25 / 128) × (1 − RMS / tolérance)`.

Le bonus RMS reste inférieur à un quart de classe : il ne peut jamais compenser
la perte d'une classe de longueur. Sans droite mesurable, un score négatif
classe seulement l'allongement du support continu pour amorcer la recherche ;
aucune linéarité n'est annoncée. Lorsque les vingt premières classes sont
peuplées mais ne forment pas encore une droite admissible, l'amorçage utilise
`−1 + support_contigu / (1 + RMS_initial + montée_initiale)`, avec le support
en intensité, le RMS en dex et la montée positive de la première régression
en dex. Cette progression ne valide jamais la rectitude.

### Seuil de la droite à 0,8

La recherche demande également que la droite retenue passe au-dessus du
repère **(0,8 ; 10¹ pixels par classe)**, égalité admise. L'option
`--ghs-regression-min-count` vaut 10 ; zéro désactive ce critère.
Il porte sur la luminosité du fond sans étoiles, comme la régression.

La régression utilise une densité normalisée sur 128 classes, tandis que le
graphique utilise `--ghs-bins` (4096 par défaut). Pour ses coefficients a et b,
le niveau prédit sur le graphique est :

`log10(effectif à 0,8) = a + b × 0,8 + log10(support actif du champ) − log10(ghs-bins)`.

Le seuil est donc 1 dex. La conversion tient compte de la population totale
lors des recherches sur échantillon. Si aucun segment admissible ne couvre 0,8, une prédiction extrapolée peut
être conservée pour diagnostic, mais **elle ne valide jamais le seuil**.
Le contrôle final exige une régression soutenue par des classes peuplées qui
contiennent 0,8. La projection de la droite globale doit aussi atteindre le
niveau demandé ; sa hauteur est auditée séparément dans
`longest_line_predicted_log10_count`. Il ne s'agit pas du compte brut d'une
seule classe fine du graphique. Changer le nombre de classes du graphique ou
la taille de l'image change les effectifs et peut modifier la sélection.

Un profil mesurable atteignant ce seuil est prioritaire sur les profils qui
restent en dessous. Parmi les profils qui l'atteignent, le score de longueur,
de RMS et de hauteur de queue décrit ci-dessous reste utilisé, sans bonus
pour dépasser davantage le seuil. Lorsque 0,8 est couvert mais reste sous le seuil, la recherche de niveau
progresse avec `score = −2 + 1 / (1 + déficit_en_dex) + 0,001 × score_précédent`.
Si la portion mesurée ne couvre pas 0,8, le score est `−3 + score_précédent` :
seule la longueur et la queue effectivement mesurées guident la recherche,
jamais une extrapolation haute. Sans droite mesurable, elle utilise
`−4 + 0,001 × score_précédent` pour amorcer le support continu. Cela peut conduire à retenir un segment plus court pour
atteindre le niveau demandé ; toutes les conditions de rectitude restent exigées.
Une fois le seuil atteint, une passe qui le perd ne peut pas être retenue.

Les gardes de bruit, couleur et hautes lumières restent obligatoires. Si le
seuil demeure inaccessible, la meilleure progression admissible est conservée,
sans forçage. Le rapport expose `regression_floor` avant/après (abscisse,
seuil, nombre de classes de référence, prédiction en log10, population et
état d'atteinte), ainsi que `regression_floor_achieved` mesuré sur tout le champ.
L'encart du graphique et le journal signalent un seuil non atteint.

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

Après application du critère de régression à 0,8, une amélioration du score doit dépasser `1e-5`.

### 3.2. Exploration et validation

Cette section décrit le parcours historique `line`. Le mode par défaut
`piecewise` partage les transformations et protections, mais explore plusieurs
branches et affine les grands écarts de position du pic ; voir
[la recherche par morceaux](ghs-histogram-shape.md).

Chaque passage explore douze forces jusqu'à D maximal, complétées par les
forces faibles D=0,0625, 0,125, 0,25, 0,5, 0,75 et 1 lorsqu'elles restent sous
la limite. D=0 permet un point noir seul ; son identité n'est évaluée qu'une
fois par point noir. B vaut 15 ou 5. HP explore 0,25, 0,5, 0,75 et 1, ainsi
que Q90, Q95 et Q98 de luminosité estimés au début de la phase et figés.
Seules les combinaisons SP <= HP sont essayées. SP explore zéro, le pic et
jusqu'à deux changements de pente. Ceux-ci sont estimés par deux droites
locales sur huit classes de chaque côté, uniquement lorsque ces classes sont
peuplées. Les changements réguliers les plus marqués sont retenus, espacés
d'au moins un octile ; SP peut ainsi dépasser 0,25 et approcher 0,5.
Les primitives sont évaluées en float64 puis converties au type source, sans
écrêtage final. HP est le début du raccord linéaire du GHS : il ne garantit
pas que les pixels au-dessus restent inchangés.

Le classement CPU porte sur une grille régulière d'au plus 65 536 pixels.
Les courbes améliorant le score sont classées par gain, puis diversifiées en
alternant trois groupes : faibles forces D <= 0,5, raccords précoces HP < 0,75
pour les autres forces, puis courbes larges. Le groupe contenant le meilleur
gain commence. Dans chaque groupe, le meilleur candidat de chaque famille
(B, HP, octile de SP) précède ses variantes de force et de point noir.
Cette diversification ne modifie pas le score qui choisit le résultat final.

Les candidats doivent passer les gardes sur l'échantillon avant validation
sur tous les pixels du backend NumPy/CuPy déjà choisi. Le budget est de
**12 validations complètes par passe**, réglable par
`--ghs-quartile-validations` de 1 à 64. Un rejet n'arrête pas la recherche :
elle poursuit les autres familles jusqu'au budget ou à épuisement des candidats.
Les rejets sur échantillon ne consomment pas de validation complète.
Le meilleur gain admissible mesuré sur tout le champ est retenu.

Un sondage ciblé complète la grille régulière, qui peut manquer un petit
cœur brillant. Il prélève jusqu'à **2048 pixels Y >= 0,75**, chacun avec
son voisin horizontal, à des coordonnées figées depuis l'entrée. S'il y a
moins de 2048 pixels brillants, tous sont représentés ; sinon une sélection
régulière dans leur liste ordonnée borne le coût. Le sondage contrôle seulement
le contraste et les couleurs des mêmes régions avant/après, fond et fusion
compris. Les paires peuvent répéter des voisins ; elles n'autorisent jamais
une mesure que le champ entier ne supporte pas. Ainsi sept pixels brillants
uniques restent sous le minimum de huit, même si leur répétition dans les
paires ferait apparaître un effectif supérieur. Les métriques initiales
absentes sur le champ entier restent absentes dans le contrôle du sondage. Il ne mesure ni bruit du ciel, ni fractions de pixels perdus ou proches
du blanc, car sa population est volontairement biaisée. Ses rejets portent le
préfixe `highlight probe:` et ne consomment pas de validation complète.
Le rapport conserve `highlight_probe_count` et `highlight_probe_before`.
Ce filtre supplémentaire reste une approximation ; la validation complète
conserve toutes les gardes initiales et décide de l'admissibilité finale.

Le rapport expose `candidate_count`, `unexamined_candidates` et
`validation_budget_exhausted` pour chaque passe, ainsi que le budget
`max_full_validations` et les seuils `protected_hp`. Si aucun candidat
n'est accepté alors que le budget laisse des candidats non examinés, l'arrêt
est `full-resolution validation budget exhausted` : il ne prouve pas qu'aucune
courbe admissible n'existe. Sinon l'arrêt survient sans amélioration admissible
ou à la limite de passages. L'entrée reste intacte. Les forces faibles et
les raccords précoces augmentent le nombre d'essais sur l'échantillon ;
le coût sur le champ entier reste borné par le budget configuré.

### 3.3. Mémoire CPU et CUDA

Les transformations ponctuelles GHS/RGBBlend sont calculées par blocs de
**262144 pixels au maximum**, réglables avec `--ghs-quartile-block-pixels`
(de 1 à 4194304). Une ligne dépassant le budget est aussi découpée en colonnes.
Le point noir est calculé dans le type source, puis le GHS en float64 avant
retour au type source, dans le même ordre numérique que le calcul plein champ.
Aucun halo, lissage ou changement d'histogramme n'est introduit par les blocs.

Sur M31 (2950 × 7828), un tableau RGB float64 plein champ représente environ
529 Mio ; avec le budget par défaut, chaque tableau temporaire de bloc est
limité à **6 Mio**. Plusieurs temporaires coexistent dans le noyau, mais leur
taille est bornée par le bloc. La sortie d'un candidat reste une image complète
dans le type source (environ 264 Mio en RGB float32 sur ce champ).

Chaque validation libère son image candidate dès que ses mesures sont
consignées. Le meilleur essai est conservé sous forme de **paramètres et de
mesures seulement**, puis recalculé une fois après la sélection de la passe.
Cela évite de garder une seconde image gagnante pendant les autres essais,
sans décharger/recharger des fichiers temporaires. Le rapport expose
`transform_block_pixels` et `candidate_storage=parameters_only`.

La reprise stratifiée ajoute en RAM une copie RGB du champ, sa luminosité et
les indices de tri par classe. Elle conserve au plus 65552 représentants
pondérés par passe, en comptant la strate des pixels inactifs. Ce coût hôte
supplémentaire n'ajoute pas de collection d'images candidates sur CUDA ; les
validations complètes et leurs blocs gardent le même budget. La reprise
conditionnelle augmente aussi le temps de recherche lorsque les objectifs
des deux premiers moteurs restent incomplets.

Sur un essai CUDA réel de M31 (2950 × 7828), le GHS complet, depuis RANGE,
a terminé cinq passes de rectitude et 60 validations complètes, avec quatre
passes appliquées. Le maximum de mémoire CuPy active mesuré par hook est
3,69 Go pour le GHS complet, dont 2,24 Go pendant la rectitude ; le pool a
réservé jusqu'à 3,98 Go sur un GPU de 8 Go. Ces valeurs dépendent de cette
entrée, du pilote et du budget par défaut ; elles ne constituent pas une
borne universelle. Ce contrôle de mémoire est distinct de la validation des critères de qualité
et ne prouve pas à lui seul leur atteinte.

Les références nécessaires aux protections cumulatives restent disponibles :
entrée de phase, résultat courant, et recomposition de référence si des étoiles
sont fournies. Les histogrammes, quantiles et gardes continuent à mesurer tout
le champ produit. Le découpage ne signifie donc pas que toute la consommation
mémoire du traitement devient indépendante de la taille de l'image.

Les anciennes images temporaires de RANGE sont libérées après leur sélection.
À l'entrée de la rectitude, le cache **inutilisé** du pool CuPy est rendu au
pilote ; les tableaux encore référencés ne sont pas libérés. Cette opération
est sans effet sur CPU. Une saturation CUDA remonte au moteur GHS : en mode
`auto`, celui-ci reprend une seule fois tout le GHS sur CPU depuis son entrée
linéaire, avec les mêmes étoiles déjà rendues. Les autres erreurs restent
fatales. Voir la [politique de calcul](compute-backend.md).

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

La sortie finale doit préserver la médiane de luminosité du diffus et sa
séparation au ciel, mesurées sur les mêmes régions initiales P75–P95 et P25.
Une marge absolue de 1e-7 absorbe les arrondis float32. Une droite plus longue
n'autorise pas à réduire ces mesures. Les motifs `diffuse brightness loss`
et `diffuse separation loss` sont consignés. Le moteur de niveau impose ces
gardes pendant ses passes ; le moteur de longueur les impose sur son résultat
complet pour permettre ses transformations intermédiaires. Le sélecteur
revérifie la luminosité diffuse du rendu final.

Les gardes de contraste et de couleur réutilisent les mesures de la
[recherche protégée](ghs-highlight-protection.md), avec des régions fixes
`Y ≥ 0,75` et `Y ≥ 0,95` pour cette phase. Une région absente ne produit pas
une mesure inventée ; le candidat est néanmoins contrôlé contre l'apparition
de nouvelles composantes proches du blanc. Le cœur Y >= 0,95 conserve au moins
`ghs-highlight-retention` de son
contraste initial (90 % par défaut). Pour le contraste brillant et le contraste
local, la conservation s'adapte à la rareté de la queue du fond initial.

Sur **tous** les pixels du fond sans étoiles, on mesure :

`f = nombre(Y >= 0,8) / nombre(Y > 1e-6)`.

Les zéros de bordure sont exclus du dénominateur. Avec `r` la conservation
normale, `m = min(ghs-sparse-highlight-retention, r)` et
`s = ghs-sparse-tail-fraction`, la conservation effective est :

`r_effectif = m + (r − m) × min(f / s, 1)`.

Par défaut, elle varie donc de **45 %** pour une queue absente à **90 %**
lorsque la queue contient au moins **0,1 % du signal actif**. Si s=0,
l'adaptation est désactivée et r est conservé exactement ; une population
active vide garde aussi r avec une fraction inconnue. Un plancher supérieur
à r ne durcit jamais un réglage explicitement plus permissif.

Cette adaptation concerne uniquement la phase de rectitude, pas les recherches
RANGE ou de présentation précédentes. Elle est calculée une seule fois depuis
l'entrée entière et reste fixe pendant toutes les passes, sur la grille,
le sondage brillant et les validations du fond et de sa fusion. Elle autorise
une compression relative du contraste brillant ; elle ne garantit pas un
éclaircissement de toutes les structures faibles. Le cœur, les couleurs, le
bruit et les budgets de pixels restent contrôlés séparément.
Le rapport `sparse_tail` expose les effectifs, la fraction, les limites et la
conservation effective, aussi publiée dans `effective_highlight_retention`.
Le journal et l'encart du graphique affichent cette conservation.

La dérive des proportions RGB reste limitée à 0,01 et la perte de chroma à 0,02. La médiane
du fond ne doit pas dépasser 0,25 (ou sa valeur déjà supérieure), le proxy de
bruit du fond ne doit pas être amplifié de plus de trois, et l'augmentation
de composantes RGB ≥ 0,98 est limitée à 0,001. Ces limites portent sur toute
la phase, fond et fusion compris.

## 5. Audit et limites

Le rapport conserve le nom historique `quartile_stretch` et les options
`ghs-quartile-*`. Il indique explicitement l’objectif
`longest_log_linear_span_with_tail_height` (ou
`longest_contiguous_log_linear_span` si le complément est désactivé),
avec le suffixe `_with_regression_floor` lorsque le seuil à 0,8 est actif,
les profils avant/après, les bornes
`line_start`/`line_end`, la longueur `line_span`, la pente, le RMS, l’écart
maximal, la tolérance, le score, les gardes initiales, les passages et D/B/SP/HP/BP,
les rejets sur échantillon, les validations complètes et la fraction cumulée annulée.
`selected_search` indique le moteur retenu (`length`, `reference_level`,
`population` ou `unchanged`) ; `searches` conserve les audits des moteurs
effectivement essayés. `peak_exit` et `anchored_to_peak` décrivent le début
imposé de la régression. Le booléen
`targets_achieved` indique la conjonction réellement vérifiée de la rectitude,
du niveau à 0,8 et de la hauteur à 0,875, en tenant compte des options désactivées.
Le statut interne `applied` signifie qu'une transformation a été appliquée.
Si cette phase active n'atteint pas ses cibles, le statut global devient
`TARGETS_NOT_REACHED` et un WARNING liste `unmet_targets`. Le meilleur rendu
admissible est exporté et le pipeline peut continuer ; il n'est pas annoncé
comme `SUCCESS`. L'encart affiche les bornes des segments,
la hauteur de queue et les objectifs incomplets, sans texte tronqué.
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
validation complète rejette alors le candidat et poursuit les autres familles
dans le budget de validations configuré. Une image insuffisamment peuplée, déjà rectiligne sur toute la zone
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
Ces cas synthétiques vérifient le critère et ne remplacent pas une validation
sur des images réelles.

Les essais réels réutilisent les couches StarNet et les entrées de phase de
NGC7331, M33 et M31, sur des copies externes au dépôt. Ils vérifient le départ
imposé à la sortie du pic, la régression soutenue à 0,8, la hauteur rouge à
0,875 et les mesures diffuses avant/après. Ils ne réexécutent ni StarNet ni
la couleur finale. La sortie du pic est auditée au même titre que les
bornes de la droite : les anciens segments de queue situés plus loin ne
suffisent pas à satisfaire ce contrôle. Un résultat incomplet reste indiqué
comme tel, même si son rendu visuel progresse.

| Champ | Recherche retenue | Segment depuis la sortie du pic | Niveau à 0,8 (pixels/classe) | Objectifs |
| --- | --- | --- | --- | --- |
| M33 | niveau | 0,1172 → 0,8594 | 420,0 | atteints |
| M31 | population | 0,2813 → 0,8047 | 10,5 | atteints |
| NGC7331 | population | 0,1641 → 0,4375 | non validé : hors du segment | incomplets |

Sur NGC7331, la reprise pondérée applique six passes puis s'arrête à la
septième : ses deux derniers candidats améliorant le score estimé sont
rejetés par les protections de contraste brillant et de chroma, avant le
recomptage complet. Il ne s'agit pas d'un épuisement du budget de validations.
Le seuil à 0,8 et la hauteur rouge à 0,875 restent non atteints. La recherche
ne prouve pas qu'aucune autre courbe serait possible ; elle n'en trouve pas
dans la grille explorée sous ces protections.

Le niveau d’histogramme est un proxy de présentation, pas une garantie de
luminosité perçue ni une mesure photométrique.


Référence de la normalisation : fonction `display_histo` du
[code Siril](https://github.com/lock042/siril/blob/master/src/gui-gtk4/histogram_utils.c).
