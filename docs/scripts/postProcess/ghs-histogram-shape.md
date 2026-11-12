# 1. Profil AutoGHS par morceaux et séries d'histogrammes

[postProcess](README.md) · [AutoGHS](auto-ghs.md) · [Mode droite](ghs-quartiles.md)

## 1.1. Mode par défaut

L'option `--ghs-histogram-model piecewise` remplace la recherche d'une droite
unique par une régularisation locale conservant la forme. `piecewise` est
sélectionné par défaut ; `line` conserve la recherche historique sur demande
explicite. Les options suivent CLI > configuration JSON > défauts locaux,
avec sauvegarde uniquement sur demande explicite.

Le [recalage du BP après chaque courbe](ghs-blackpoint.md) précède les mesures
et les tests de conformité. Une préférence `line` ou des seuils plus stricts
déjà sauvegardés gardent leur priorité sur les nouveaux défauts.

La cible de conservation du contraste brillant des populations rares vaut **45 %** de
sa valeur initiale (`--ghs-sparse-highlight-retention 0.45`), avec retour
progressif à la protection normale lorsque la fraction brillante augmente.
Dans la recherche finale, un déficit brillant est pénalisé progressivement ; le cœur
garde sa protection obligatoire, indépendamment de cette adaptation.

Le moteur `ShapeGHSProcessor`, dans `lib.ghs_log_render`, utilise les mêmes
courbes GHS D/B/SP/HP et les mêmes transformations par blocs. Il représente
les populations rares par le sondage stratifié pondéré existant, puis contrôle
ses finalistes sur tous les pixels. Le lissage décrit ci-dessous concerne
les **mesures d'histogramme**, sans filtrer l'image ni remplacer le GHS par
une spline de transformation.

## 1.2. Mesure de l'enveloppe et des morceaux

Le module sans état `lib.ghs_histogram_shape` reçoit 128 classes uniformes
de luminosité sur [0,1]. L'analyse commence à la première classe suivant
le maximum des effectifs lissés `[1,2,1]/4`, et s'arrête au plus à 0,875.
L'épaulement du pic fait partie de la forme étudiée. Le seuil de sortie à
2 % utilisé dans le mode droite ne détermine pas ce départ.

Les classes rares sont regroupées jusqu'à 32 pixels ou quatre classes au
maximum. Un groupe totalisant moins de trois pixels termine le support :
la spline ne relie pas une grande lacune à une queue isolée. Chaque groupe
fournit son centre, sa densité réellement comptée et sa population n.
Aucun pseudo-effectif n'est ajouté aux classes nulles.

Une spline cubique de lissage ajuste `log10(densité)`, avec les poids
`n/(n+16)` et un paramètre de régularisation **fixe pour tous les essais**.
Le poids borné empêche le pic de dicter seul l'ajustement ; les groupes peu
peuplés influencent moins la queue. Le paramètre n'est pas réoptimisé pour
rendre chaque candidat artificiellement plus droit. L'implémentation utilise
[`scipy.interpolate.make_smoothing_spline`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.make_smoothing_spline.html),
disponible avec la dépendance SciPy ≥ 1.10 du projet, sur CPU.

L'enveloppe est évaluée sur 61 points. Une programmation dynamique cherche
une couverture complète par **au plus six morceaux contigus**, avec au moins
cinq points par morceau. Les voisins partagent leur extrémité. Chaque
ajustement par moindres carrés doit respecter le RMS local demandé et un
résidu maximal de trois fois cette tolérance. Le nombre de morceaux est
minimisé sous ces conditions ; il n'apporte pas de bonus d'aplatissement.
Une analyse demande au moins huit groupes et vingt classes de support.

Le pic initial possède aussi un contrôle **local**, indépendant de cette
spline globale : les 128 effectifs sont lissés par `[1,2,1]/4`, puis suivis
depuis le sommet jusqu'à la première classe sous **10 %** de sa hauteur.
La remontée maximale depuis un minimum courant est divisée par la hauteur
du sommet. Elle mesure notamment un second mode près du pic, même si la
spline du corps le gomme. `initial_peak` expose la méthode, la fenêtre et
`secondary_rise_fraction`. Les bosses déjà présentes dans la référence
restent admises ; le contrôle porte sur leur aggravation.

## 1.3. Conservation de la forme

La référence pleine résolution est figée après RANGE, **avant l'égalisation**,
puis transmise à la présentation et à la recherche par morceaux. Chaque
passage est comparé à cette même référence, sans autoriser une succession
de petites dégradations cumulatives entre les phases. Un appel indépendant
sans référence fournie utilise sa propre entrée.

Les indicateurs de forme portent sur les propriétés suivantes. Leurs seuils
restent obligatoires pendant la préparation CDF et la présentation. Dans la
recherche finale `piecewise`, les déformations mesurées contribuent au score
progressif ci-dessous ; seuls la mesurabilité et les plafonds de position du
pic restent des limites de sécurité de forme.

Les valeurs de référence sont :

- **L'ordre des variations de pente.** Six régions relatives au support
  donnent des pentes ; leurs variations, divisées par la décroissance globale,
  sont significatives à partir de 0,3. Les signes successifs identiques sont
  regroupés. L'ordre significatif est conservé ; un raccord final descendant
  supplémentaire est permis lorsque la dernière pente reste négative. Sans
  variation significative dans la référence, une nouvelle courbure est admise
  si toutes les pentes restent négatives et les autres protections passent.
- **L'amplitude de décroissance.** La chute entre les trois premiers et les
  trois derniers points lissés est mesurée en dex. La cible de conservation de la chute
  initiale est de 65 % par défaut. Chaque pente régionale
  significative vise séparément **10 %** de son amplitude
  (`--ghs-shape-local-retention`). Cela admet un étirement visible des tons
  moyens tout en pénalisant une région devenue horizontale ou croissante.
  Le rejet « conservation de décroissance insuffisante » compare la chute
  globale à son seuil ; il **ne signifie pas que la courbe est horizontale**.
  Une droite descendante peut échouer à ce contrôle si sa chute reste sous
  65 % de celle de la référence. L'encart donne le ratio et le minimum
  enregistrés, par exemple 57,5 % < 65 %.
- **L'évolution homogène des pentes.** Si `s_i` est la pente dans la région i
  sur l'abscisse relative et `d` la chute globale en dex, on compare
  `a_i = s_i / d` à la référence `a_i⁰`. La déformation relative est
  `e_i = (a_i - a_i⁰) / max(|a_i⁰|, 0,2)`. Les maxima de `|e_i|` et de
  `|e_(i+1) - e_i|` ont pour limites de référence
  `--ghs-shape-stiffness` (1,25 par défaut), pour les cinq régions intermédiaires.
  La dernière région peut devenir plus raide pour rejoindre le blanc ; son
  aplatissement et une remontée relative de sa pente restent contrôlés.
  La première borne protège les
  proportions des pentes ; la seconde empêche une déformation concentrée
  dans un coude. Une variation commune de l'amplitude reste possible, sous
  la protection de décroissance. Le plancher 0,2 évite les divisions instables
  dans une région initialement presque horizontale ; ces régions ne sont
  pas soumises à un ratio de conservation de pente non mesurable.
- **Les proportions.** La distance moyenne entre les profils normalisés en
  abscisse et en décroissance a pour limite de référence 0,15. La largeur relative
  de l'épaulement du pic doit respecter cette même limite de variation.
- **La position du pic.** Le déplacement absolu depuis la référence est mesuré
  après recalage du BP. Sa limite de sécurité vaut **0,25** par défaut, au lieu
  de bloquer de petites différences autour de 0,05. Un réglage explicite de
  `--ghs-shape-peak-shift` garde sa priorité. Le pic doit toujours rester sous
  0,25 d'intensité et les gardes de luminosité du ciel restent actives.

Une chute inférieure à 0,2 dex ne fournit pas de référence informative :
le contrôle est non mesurable, pas déclaré réussi. Les objectifs complets
ne peuvent alors pas être certifiés. Les protections existantes de bruit,
diffus, couleurs, contraste et écrêtage restent nécessaires.

Le classement final est calculé par `lib.ghs_render_quality.HistogramRenderQuality`,
un composant de mesure sans état dont les options appartiennent à sa classe. Il compare
les rendus après BP avec les régions fixes de l'entrée de cette phase, et la
forme avec la référence commune avant égalisation. Il ne prescrit aucune
position idéale du pic, aucun nom d'objet et aucun ancien numéro d'essai.

Pour les régions ciel et diffus, on définit `C = médiane(diffus) − médiane(ciel)`
et `σ` par le MAD des différences entre pixels voisins du ciel, divisé par √2.
Le gain de visibilité vaut `G = C_final / C_initial` et la conservation de
contraste/bruit vaut `R = G × σ_initial / σ_final`. Ce rapport est un **indicateur
de rendu sur masques fixes**, pas un gain de rapport signal/bruit physique.
Une mesure absente ou non exploitable ne certifie pas la qualité : le score
reste `null`, sans remplacer une mesure par zéro.

Le score sans unité est la somme de trois bonus bornés, diminuée des pénalités :

- contraste/bruit : `min(1, max(0, R))`, plafonné dès 100 % ;
- visibilité : `0,2 × min(1, log2(max(1, G)))`, plafonné dès un doublement de C ;
- luminosité : au plus `--ghs-quality-tail-bonus` (**0,1** par défaut), partagé
  également entre les progressions vers dix pixels/classe à 0,8 et vers la
  hauteur cible à 0,875. Chaque progression est bornée à 1 ; dépasser les
  niveaux n'apporte aucun gain supplémentaire. Un objectif désactivé ou non
  mesurable n'apporte pas de bonus ;
- perte de contraste/bruit : `2 × max(0, (r − R)/r)²`, où
  `r = --ghs-quality-cnr-floor` (**0,9** par défaut) ;
- forme : la moitié de la pénalité continue déjà mesurée par l'audit, augmentée
  de la moitié de la somme des déficits ou excès quadratiques aux seuils de
  décroissance, pente locale, distance, épaulement et hétérogénéité. Chaque
  écart est divisé par son seuil ; le diviseur de pente locale est au moins 1
  pour éviter une amplification instable autour de zéro ;
- bosse nouvelle : `0,5 × (excès / 0,1 dex)²`. On mesure la remontée maximale
  de l'enveloppe **déjà lissée**, depuis la sortie du pic à 80 % du support relatif, après
  soustraction de la remontée présente dans la référence. La classe du pic et la queue
  rare sont exclues ; aucun segment initial après le pic n’est sauté. Une bosse native n'est donc pas pénalisée à nouveau ;
- bosse du pic initial : `0,5 × (excès de remontée / 0,1)²`. La remontée est
  une fraction du sommet, mesurée sur les effectifs lissés localement décrits
  ci-dessus, et comparée à la référence fixe. Une seconde bosse de 16 % du
  sommet coûte ainsi environ 1,28 point si la référence est unimodale, même
  lorsque la spline du corps reste descendante. Une référence ancienne sans
  cette mesure reste `null`, sans être déclarée unimodale ;
- creux avant le pic : même pénalité quadratique de 0,5 par 0,1 dex d'excès
  par rapport à la référence. La baisse maximale avant le pic dominant est
  mesurée sur les effectifs positifs lissés `[1,2,1]/4`. Un nouveau pic dominant
  ne peut ainsi masquer un creux créé à sa gauche. Une référence absente garde
  une mesure `null`, sans être remplacée par un creux nul ;
- compression du ciel : `0,5 × max(0, 1 − gain_bruit/0,25)²`. Un GHS qui
  écrase les variations du ciel de plus d'un facteur quatre peut effacer du
  signal faible avec elles. Ce gain artificiel de R ne suffit donc pas à
  favoriser le rendu. Le seuil est relatif à l'entrée de phase et ne prescrit
  aucune position du pic ;
- compression brillante : `0,5 × max(déficit relatif)²`, avec le déficit sous
  la conservation adaptée à la population. Le maximum porte sur les contrastes
  brillant et local du fond et de sa fusion. Un écart minime sous 45 % n'entraîne
  plus un rejet brutal, tandis qu'une conservation de 8 % coûte davantage.

Le score de qualité prime ; le **déplacement absolu du pic ne départage que
les scores égaux**. Une grande amélioration de R au-delà de 100 % ne compense
ainsi pas une bosse ou une forte compression brillante. Atteindre les niveaux
à 0,8 et 0,875 ne permet pas non plus d'imposer un rendu trop poussé.
Les gardes d'écrêtage cumulatif, quasi-blanc, couleurs, chroma, cœur, ciel,
luminosité diffuse et bruit relatif restent obligatoires. Le mode `line`
garde son classement et ses seuils historiques.

Le classement est identique pour l'exploration, les finalistes complets et
la sortie. L'estimation de R
utilise une grille **spatiale régulière** ; les pixels du sondage stratifié
pondéré servent à l'histogramme, jamais à simuler des voisins pour le bruit.
Les finalistes sont recomptés sur le champ entier, avec les masques fixes
et les mesures brillantes de fusion. Le rapport expose `render_quality`, ses
contributions, `available`, `estimated` et `sufficient_cnr`, ainsi que
`quality_sufficient` pour la sortie. Ce dernier indique uniquement la
suffisance de R, sans certifier une conformité esthétique globale.
La politique est consignée dans `selection_policy`. Le seuil à 0,8 reste
mesuré et marqué atteint/non atteint dans `regression_floor` ; il n'est pas
un rejet éliminatoire dans ce classement. Les gardes de perte noire restent
prioritaires : si tous les finalistes échouent, l'entrée peut être conservée
avec `regression_floor` incomplet. Une perte créée en préparation ne doit donc
pas être masquée ; la CDF vérifie aussi ce budget après son BP.
L'ordre d'exploration réserve toutefois des places aux différentes familles et
positions du pic, pour que les meilleurs scores estimés n'occupent pas tout le
budget de validations complètes (`--ghs-quartile-validations`, 12 par défaut).
Les variantes de BP préalable
inférieures à un epsilon d'intensité du type source sont écartées, afin de ne
pas consommer ce budget sur des doublons presque identiques. Chaque finaliste
complet admissible est comparé aux précédents ; le premier admissible ne gagne
pas automatiquement. Un budget épuisé reste indiqué : le résultat est le
meilleur des candidats effectivement vérifiés, sans garantie d'exhaustivité
de toutes les images possibles.

La recherche `piecewise` conserve jusqu'à **trois chemins** de l'exécution
courante (`--ghs-shape-branches`, de 1 à 8). Le premier est le meilleur candidat
vérifié ; les suivants privilégient une autre famille GHS et une autre classe
de position du pic, de largeur **2/128 ≈ 0,0156** en intensité. Les familles
distinguent forces faibles, raccords précoces et courbes larges, ainsi que B.
La position à gauche ou à droite ne reçoit aucun bonus d'exploration. Une
seule branche est gardée par couple famille/classe de pic.

Chaque profondeur partage les douze validations entre ses parents : ce n'est
pas un budget de douze par branche. Un parent sans progrès ne bloque pas les
autres. Le nombre effectif de chemins ne dépasse ni la limite configurée ni le
budget de validations. Les protections restent comparées à l'entrée immuable
de la phase, jamais réinitialisées à chaque parent. La sortie est le meilleur
candidat complet admissible rencontré à n'importe quelle profondeur, y compris
si sa branche a terminé plus tôt.

Lorsque des D ou SP voisins, à autres paramètres fixes, donnent des pics
espacés de plus de 2/128, le moteur essaie des valeurs **D et SP intermédiaires
par bissection**, en alternant les deux axes. Au plus huit
intervalles sont affinés par tour, pendant trois tours, soit vingt-quatre
évaluations estimées supplémentaires par parent. Le BP est recalé avant ces
mesures comme pour la grille initiale. L'affinage n'impose pas de déplacement
maximal par passe : un changement du mode dominant d'un histogramme peut
rester discontinu, même avec des paramètres continus, et certains intermédiaires peuvent
être rejetés par les protections.

Seuls les paramètres des chemins sont conservés ; leurs images sont
reconstruites successivement depuis l'entrée, sans accumulation de tableaux
GPU. Le calcul échantillonné et les reconstructions coûtent davantage qu'une
recherche à un chemin. Aucun candidat d'un ancien traitement n'est chargé et
aucun nom d'objet n'intervient dans l'exploration.
Le rapport expose `branch_generations`, `selected_branch_id`, `selected_path`,
les identifiants parent/enfant et le nombre de `peak_refinements` par parent.

L'égalisation CDF commence à la force demandée, puis la divise par deux
après chaque rejet de forme, avec huit réductions au maximum. Chaque essai
part du même fond RANGE ; il ne transforme pas l'essai précédent. Le premier
essai admissible est retenu. Sans référence mesurable ou si tous les essais
échouent, l'égalisation conserve son entrée. Les forces effectivement essayées,
les profils et les motifs sont consignés dans `histogram_balance.full_validation`.
Cette préparation reste plus conservative : elle conserve au moins 65 % de
chaque pente locale et borne la déformation à 0,35, ou aux limites utilisateur
si elles sont plus strictes. Son déplacement maximal de pic reste borné à 0,05,
indépendamment de la marge plus souple de la recherche finale. Celle-ci ne reçoit ainsi pas une
référence déjà aplatie par l'égalisation.

Dans la passe de présentation protégée qui précède la recherche finale, le mode
par morceaux remplace également les bonus de pente moins raide et de RMS
global par ce contrôle de forme. Le gain
diffus reste évalué. Cela évite d'aplatir systématiquement la référence avant
la recherche suivante. La recomposition comprenant les étoiles conserve sa
référence propre à cette passe. RANGE et l'expansion préparent le rendu ; la
forme du FITS linéaire brut n'est pas une cible à reproduire telle quelle.
Dans la préparation de présentation, les candidats passant ses gardes de forme,
de bruit et de contraste sont eux aussi départagés par le plus petit déplacement
du pic, puis par le gain diffus ; les objectifs lumineux finaux sont évalués
par la recherche suivante.

La protection du bruit porte sur le **bruit relatif au contraste utile** :
`gain_bruit / max(1, gain_diffus_moins_ciel)` ne doit pas dépasser 3, sur des
régions fixes. Une amplification commune du signal et du bruit par le GHS ne
constitue pas à elle seule une dégradation de ce rapport. Sans contraste diffus
mesurable, la limite reste absolue. L'audit `noise_assessment` distingue ces
rapports ; une mesure absente n'est pas remplacée par zéro. Le mode `line`
garde sa limite historique de gain de bruit absolu.

## 1.4. Niveau à 0,8 et limites

La cible de dix pixels par classe du graphique demeure active par défaut.
Il est mesuré sur l'enveloppe lissée **dans son support observé**, et non par
projection d'une droite globale. La densité est ramenée aux unités de
`--ghs-bins`, comme dans le mode droite. La hauteur rouge à 0,875 reste un
objectif distinct, sur la recomposition lorsque les étoiles sont disponibles.

Les objectifs `targets_achieved` et les écarts stricts de forme restent exposés
indépendamment du classement de qualité. Une image choisie peut donc garder
un objectif incomplet, notamment à 0,8, sans être rejetée pour ce seul motif.

Une spline ne garantit ni le respect des protections ni la possibilité de
satisfaire simultanément les objectifs de niveau et de forme. Un résultat
incomplet conserve `TARGETS_NOT_REACHED` après transformation, avec ses motifs.
L'expansion affine garde son propre objectif de couverture : si son changement
de support fait échouer la comparaison à la référence commune et qu'aucun
candidat ne corrige cet écart, `histogram_shape` reste un objectif non atteint.
L'entrée peut être conservée si aucun candidat admissible ne la dépasse.
Ces heuristiques sont destinées au rendu ; elles ne sont pas une mesure
photométrique ni une qualification esthétique universelle.

| Option | Défaut | Domaine |
| --- | --- | --- |
| `--ghs-quality-cnr-floor` | `0.9` | strictement positif, au plus 1 ; début de pénalité de perte contraste/bruit |
| `--ghs-quality-tail-bonus` | `0.1` | de 0 à 1 ; bonus lumineux total maximal |
| `--ghs-histogram-model` | `piecewise` | `line` ou `piecewise` |
| `--ghs-post-blackpoint` | `rise` | `rise` ou `off`, actif en mode `piecewise` |
| `--ghs-shape-smoothing` | `0.0001` | de 1e-8 à 0,1 |
| `--ghs-shape-retention` | `0.65` | strictement positif, au plus 1 |
| `--ghs-shape-local-retention` | `0.1` | strictement positif, au plus 1 |
| `--ghs-shape-distance` | `0.15` | strictement positif, au plus 1 |
| `--ghs-shape-peak-shift` | `0.25` | de 0 à 0,25 ; zéro fige la classe du pic |
| `--ghs-shape-branches` | `3` | de 1 à 8 chemins ; borné aussi par le budget complet |
| `--ghs-shape-stiffness` | `1.25` | strictement positif, au plus 4 ; diminuer renforce la contrainte |
| `--ghs-linearity-tolerance` | `0.05` dex | de 0,005 à 0,5, appliquée par morceau |

## 1.5. Série de graphiques exclusivement logarithmiques

`lib.ghs_diagnostics.HistogramDiagnostics` exporte une série dans
`05_diagnostics/histogram_trials/`, pour les deux modes d'évaluation :

- l'entrée, chaque passage RANGE accepté, l'égalisation et l'expansion ;
- les essais complets d'égalisation et les finalistes de la présentation protégée ;
- chaque passage accepté, son meilleur rejet complet et, s'il existe,
  son meilleur rejet de forme estimé sur l'échantillon ;
- en recherche par branches, tous les enfants vérifiés admissibles, y compris
  les alternatives non poursuivies, avec leur branche et leur parent ;
- le fond retenu et le rendu final avec étoiles lorsqu'il est disponible.

Chaque PNG possède **un seul panneau d'effectifs logarithmiques**. L'intensité
en abscisse reste linéaire sur [0,1]. Les classes nulles sont masquées ; les
limites Y sont identiques dans toute la série. Une légende et un encart
utilisent un **fond noir ou très sombre et des textes clairs**, comme la galerie,
pour faciliter la consultation dans l'obscurité. Les courbes colorées, l'enveloppe
et le repère à 0,8 restent contrastés. Le thème commun de `lib.diagnostic_style`
s'applique à chaque figure sans modifier les réglages globaux Matplotlib.
La légende et l'encart
identifient la phase, les paramètres et le verdict. Les rejets ne sont pas
présentés comme des sorties retenues. Les estimations pondérées sont signalées
explicitement et distinguées des recomptages complets.
L'encart commence par le **nom de la série** (étapes, égalisation, présentation,
profil par morceaux ou résultat retenu), puis **IMAGE NNN / total**. Les essais
de recherche indiquent leur passe, leur branche et leur branche parente.
Seuls les enfants appartenant au chemin finalement choisi sont étiquetés
« accepté » ; les autres enfants sans rejet restent « admissibles ».

Les deux dernières figures, « Fond retenu » et « Rendu avec étoiles », indiquent
**Essai sélectionné : NNN** et le nom `histogram_NNN.png` correspondant. Le numéro
désigne le candidat complet retenu, même si d'autres branches ont été explorées
après lui. Si l'entrée de recherche est conservée, il désigne son étape dans
la série. Un ancien rapport sans provenance suffisante affiche « non renseigné ».
Le numéro et la phase source sont exposés dans `selected_trial_index` et
`selected_trial_stage`, ainsi que dans le manifeste ; la galerie propose un
lien direct vers l'essai. Les deux histogrammes principaux reprennent aussi
ce numéro lors du traitement complet.
Lorsque les JPEG des essais sont activés, l'encart et la galerie donnent aussi
le nom et le lien de l'image `image_NNN.jpg` sélectionnée.

L'encart indique aussi le **déplacement absolu du pic depuis la référence de
forme**, en intensité sur [0,1], ainsi que son plafond autorisé lorsqu'il est
enregistré. La mention « estimé » identifie une mesure sur échantillon. La
position du pic est également affichée lorsqu'elle est mesurée. Cette
information figure également sur les histogrammes de synthèse et sur les
finalistes de présentation contrôlés. Une mesure absente est indiquée comme
« non renseignée », sans être remplacée par zéro.

Les encarts `piecewise` donnent aussi le score, R, les trois bonus et les
pénalités de perte contraste/bruit, déformation, bosse et compression brillante.
Ces pénalités sont distinctes des motifs de rejet obligatoires. Une qualité
non mesurable est explicitement signalée.

L'enveloppe lissée, les morceaux et le repère à 0,8 sont superposés lorsqu'ils
sont disponibles. Les deux figures de synthèse existantes deviennent elles
aussi exclusivement logarithmiques ; le panneau des effectifs linéaires
est supprimé.

La galerie est `histograms.html` et le manifeste `histograms.json` ; le nom
`index.html` reste réservé au générateur HTML commun. Les chemins sont exposés
dans `histogram_gallery`, `histogram_series_manifest` et `histogram_series`.
Le générateur commun peut ainsi continuer à indexer l'arborescence.

Les graphiques sont produits depuis les effectifs et paramètres CPU consignés.
Aucune collection d'images candidates n'est conservée sur le GPU. Les courbes
de spline détaillées sont gardées pour les références et essais majeurs ; les nombreux rejets échantillonnés ont un audit compact.
Une nouvelle exportation retire les PNG et JPEG numérotés de sa propre série
qui ne correspondent plus à un export courant.

### 1.5.1. Images JPEG de chaque essai

L'option **`--ghs-save-trial-jpegs`**, désactivée par défaut, enregistre les
images de tous les essais représentés dans cette galerie, en mode `line`
comme en mode `piecewise`. Elle appartient à `HistogramDiagnostics`, enfant
du rendu final AutoGHS, et suit la priorité CLI > configuration JSON > défaut.
`--no-ghs-save-trial-jpegs` désactive un réglage mémorisé ; `-S` le sauvegarde
explicitement avec les autres paramètres.

```bash
bin/postProcess.sh image_RGB_lineaire.fits rapports/ \
  --autoghs-only --ghs-save-trial-jpegs
```

Dans `05_diagnostics/histogram_trials/`, les numéros correspondent :

| Graphique | Image correspondante |
| --- | --- |
| `histogram_014.png` du fond | `image_014.jpg`, fond sans étoiles |
| Même essai, aperçu recomposé | `image_014_with_stars.jpg` |
| Histogramme du rendu avec étoiles | `image_NNN.jpg`, déjà recomposée |

Chaque carte de `histograms.html` donne ces liens. Le manifeste ajoute
`image_jpeg`, `with_stars_jpeg` lorsqu'il existe et
`image_histogram_estimated`. Le rapport expose `trial_jpegs_enabled`.
Le numéro identifie un essai **dans cette exécution** ; il peut changer entre
deux traitements. Un essai rejeté conserve son verdict et ses motifs.

Les JPEG gardent la pleine résolution, l'orientation FITS `ROWORDER`, les
intensités directes sur [0,1], la qualité 95 et les canaux RGB sans
sous-échantillonnage couleur. Aucun gamma ni auto-étirement supplémentaire
n'est appliqué. La quantification JPEG sur huit bits est destinée à l'examen
visuel et ne permet pas de retrouver exactement les effectifs du FITS.
Les premières étapes encore linéaires peuvent donc paraître sombres.

Pour un rejet dont l'histogramme est estimé sur échantillon, les paramètres
de l'essai et son point noir sont appliqués au **fond entier à l'entrée de
la passe** pour produire le JPEG. L'histogramme reste identifié comme estimé ;
cette reconstruction visuelle ne valide pas le candidat et ne change pas le
classement. Les autres essais majeurs sont capturés depuis leurs pixels réels.
L'aperçu avec étoiles réutilise les étoiles fixes et le même facteur de fusion,
sans appliquer la correction finale des couleurs.

Les captures passent par un cache JPEG temporaire sur disque. Seuls leurs
identifiants et chemins restent en mémoire ; aucune collection d'images n'est
retenue sur le GPU. Le cache est supprimé après publication, et aussi après
échec. L'option augmente le temps de calcul, les transferts CPU et l'espace
disque, en particulier pour les mosaïques et les reconstructions des rejets
estimés. Une erreur d'écriture arrête le traitement et produit un rapport ERROR.

L'option doit être active **pendant le traitement**. Un ancien rapport
d'histogrammes ne contient pas les pixels nécessaires à cet export ;
`HistogramDiagnostics.post_process` refuse une demande JPEG sur ce seul rapport.

## 1.6. Vérification

```bash
.venv/bin/python -m pytest -q tests/test_ghs_histogram_shape.py \
  tests/test_ghs_diagnostics.py tests/test_render_exports.py \
  tests/test_final_render.py tests/test_processor_parameters.py
```

Les tests couvrent une courbe à trois pentes connues, le bruit de queue,
l'aplatissement conservant pourtant le même ordre de pentes, la référence
cumulative, le plateau intermédiaire malgré une chute globale préservée,
l'évolution homogène des pentes, l'épaulement et le déplacement du pic, les niveaux sans
extrapolation et les unités d'effectif. L'export est exécuté sur un rapport
de test, avec vérification des axes Matplotlib, des verdicts, de la galerie
et de sa coexistence avec le générateur HTML commun.
`tests/test_auto_ghs.py` vérifie également la réduction de force CDF sur une
population exponentielle, la monotonie, les couleurs et la conservation de
l'entrée lorsqu'aucune référence de forme n'est mesurable.

Les comparaisons réelles sur copies de M31, M33 et NGC7331 réutilisent les
couches StarNet et étoiles déjà calculées ; elles exécutent le GHS et les
exports sur CUDA. Elles ne réexécutent ni StarNet ni la couleur finale. Les
rapports et graphiques d'essai restent externes au dépôt ; leurs objectifs
incomplets ne sont pas assimilés à une validation complète du rendu.
