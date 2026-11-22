# Séparation, étoiles et recomposition

[postProcess](README.md) › [AutoGHS](auto-ghs.md) › Étoiles

## Modules et ordre des opérations

[`StarNetSeparator`](../../../lib/star_separation.py) hérite de `processor` et
réutilise le service `Siril.run_commands`, ses modes d'exécution et ses
journaux. [`ConservativeStarRenderer`](../../../lib/star_rendering.py), également
un `processor`, possède une méthode de calcul indépendante `render`.
`recompose` est une opération sans état du même module.
Les [classes de branche](stretch-branches.md) `StarStretchProcessor` et
`StarlessStretchProcessor` possèdent respectivement les exports et diagnostics
des étoiles et du fond, avec une base commune pour le cycle de vie.
[`star_diagnostics`](../../../lib/star_diagnostics.py) produit les vues et
histogrammes de la couche d'étoiles, sans intervenir dans le calcul.
[`AutoGHSFinalRenderer`](../../../lib/final_render.py) orchestre les enfants,
contrôle et publie leurs sorties ; il ne contient pas le moteur GHS.

L'ordre est strict : **une séparation → une couche d'étoiles étirée → un fond
GHS RANGE → une fusion**. Les critères d’étirement du fond sont évalués sur
le fond seul : changer un réglage stellaire ne modifie pas ce fond pour une
même entrée linéaire et les mêmes paramètres GHS. Les tests contractuels enregistrent ces appels et
vérifient sur disque la contribution non nulle des étoiles au rendu final.

## Adaptateur StarNet V2 / Siril 1.4

L'exécutable provient de `--starnet-path`, du JSON mémorisé ou des préférences
Siril du mode choisi. Le chemin est validé et transmis dans une copie isolée
des préférences avec l'option Siril `-i`, sans dépendance à un chemin local codé
en dur. Les caractères permettant d'injecter une commande dans un script
Siril sont refusés. Les versions sont relevées dans le journal Siril et,
si identifiable, dans le README du dossier StarNet ; une version absente
n'est pas inventée.

Le chemin historique charge un RGB float32 de travail et exécute **`starnet -stretch`**,
sans `-nostarmask`. L'interface Siril 1.4 applique un pré-étirement MTF non lié
aux canaux, effectue l'échange TIFF 16 bits requis par StarNet V2, puis applique
la pseudo-inverse au résultat sans étoiles. Il y a donc un impact possible de
la quantification et du faible écrêtage de l'autostretch. Le rapport et le
journal tracent cette opération ; le retour linéaire n'est pas une conservation
exacte des informations perdues dans le TIFF.

Le script `StarNet.py` présent sur la machine vise le nouveau CLI StarNet 2.5.
Il refuse le binaire V2 ; la présente intégration choisit donc l'interface
historique disponible dans Siril 1.4.4. Elle devra être adaptée pour Siril 1.6,
où l'interface historique est annoncée retirée. Aucune substitution automatique
vers un script non validé n'est effectuée.

<a id="pre-etirement-adapte-a-limage"></a>

### Pré-étirement adapté à l'image

`--starnet-adaptive-stretch` est activé par défaut. Le choix dépend de
l'image linéaire, sans nom d'objet ni valeur prédéfinie par cible :

1. Les noyaux connexes au-dessus de 0,5 en maximum RGB repèrent des sources
   brillantes potentielles ; une composante d'un pixel est ignorée.
2. Une MTF de référence à cible médiane 0,25 prépare une mesure de leur
   étendue apparente. Dans une fenêtre de rayon 96 pixels, le fond est estimé
   dans l'anneau extérieur, puis la composante contenant le centre est mesurée
   à mi-contraste. Son diamètre équivalent vaut `2 × sqrt(surface / pi)`.
3. Les contours tronqués et les rapports des axes des moments inférieurs à
   0,75 sont exclus. Il s'agit d'une heuristique, pas d'une classification
   certaine entre étoile et structure diffuse. Les centres retenus restent
   fixes pendant tous les essais.
4. Si le diamètre maximal dépasse `--starnet-max-star-diameter` (**96 pixels**
   par défaut, dans ]0 ; 192[), la cible est divisée par deux, sans descendre
   sous 0,05. Trois bissections affinent ensuite la cible la plus claire qui
   respecte la limite. Ces essais ne font que des transformations et mesures
   d'image ; **StarNet n'est lancé qu'une fois**.
5. Sans source mesurable, sans dépassement ou sans amélioration mesurable,
   la commande historique `starnet -stretch` reste utilisée. Si même la cible
   minimale ne satisfait pas la limite, le meilleur essai est utilisé avec
   `limit_met: false`, sans certifier la disparition d'un artefact.

Le diamètre mesuré appartient à l'image étirée destinée au réseau : il ne
représente pas une FWHM optique ni une mesure photométrique. Le seuil est
une garde commune de préparation du modèle, pas un détecteur garanti de grille.
Une source étendue ronde peut être ambiguë ; les bords non mesurables restent
explicitement signalés. La méthode peut atténuer les motifs réguliers autour
des grandes étoiles, sans garantir tous les résidus du modèle StarNet V2.

Quand une adaptation est choisie, la MTF est calculée par canal avec
`noir = max(0, médiane − 2,8 × 1,4826 × MAD)`, blanc fixé à 1. Le paramètre
médian est résolu pour atteindre la cible choisie. Une médiane dégénérée ou
un inverse numériquement non représentable conserve une MTF identité, avec
motif d'audit. Le FITS préparé est envoyé à `starnet` **sans `-stretch`**, puis
la MTF enregistrée est inversée après StarNet. L'échange TIFF16 reste quantifié,
et les valeurs sous le noir sont écrêtées pour le réseau : cet inverse ne
récupère pas leurs détails perdus. La source scientifique reste inchangée ;
le résidu signé est calculé à partir de cette source, puis compensé comme
décrit ci-dessous. Les formules de MTF suivent la
[documentation Siril](https://siril.readthedocs.io/en/stable/processing/stretching.html).

`--starnet-target-background 0.08` impose une cible et prend priorité sur
l'adaptation ; ce réglage n'est **pas** le défaut universel. La valeur `auto`
rétablit le choix automatique. `--no-starnet-adaptive-stretch` avec une cible
`auto` conserve le pré-étirement historique de Siril. Ces paramètres suivent
CLI > JSON > défaut et ne sont mémorisés qu'avec `-S`.

Le rapport `separation.json` conserve `stretch_adaptation` : critère, sources,
diamètres, essais, cible retenue, conformité et motif. `prestretch_parameters`
contient les MTF par canal et le nombre de valeurs sous le noir. Le journal
annonce le changement de diamètre et la cible quand l'adaptation intervient.
Les contrôles algébriques de reconstruction ne certifient pas la fidélité
astrophysique des structures reconstruites par le réseau.

Après succès du processus, les dimensions, les canaux et les valeurs finies
non négatives sont contrôlés. Le fond brut peut dépasser 1 lors du retour
linéaire Siril/StarNet : il est conservé tel quel avec son résidu signé, et
non écrêté indépendamment. Le journal et le rapport relèvent le nombre de
valeurs RGB concernées (`raw_starless_above_one_count`) et le maximum
(`raw_starless_maximum`). Un changement de convention `ROWORDER` est corrigé par un
retournement vertical explicite du tableau ; la géométrie et les métadonnées
publiées restent celles de la source. Les extensions FITS sont conservées.

Le masque produit par Siril peut employer une loi **unscreen**. Pour disposer
d'une couche additive vérifiable, l'adaptateur calcule donc, **après inversion
MTF**, `stars_linear = original_linear - starless_linear`. La soustraction
reste signée. Le rapport mesure la fraction négative, son minimum et le résidu
`original-(starless+stars)` en float64. L'essai réel M33 a mesuré un maximum
de reconstruction de `2.8e-8` environ ; cette cohérence algébrique ne prouve
pas que StarNet a conservé toutes les structures astrophysiques compactes.

## Politique des résidus négatifs

Un résidu négatif indique que StarNet a produit localement un fond plus intense
que l’image d’entrée. Ce dépassement peut rester corrigeable, même s’il est
fort sur quelques pixels. Avant tout étirement, les branches sont compensées :

```text
negative = min(stars_linear, 0)
background_corrected = starless_linear + negative
stars_positive = stars_linear - negative
```

Le flux est transféré dans le fond avant tout étirement, avec conservation de
la somme linéaire. RANGE reçoit ce **fond corrigé**.
Cette compensation s'applique également à un fond brut supérieur à 1 : son
résidu signé négatif ramène le fond à l'intensité de l'entrée originale.
La somme des couches doit elle-même rester dans `[0,1]`, puis les deux couches
corrigées sont soumises au contrôle strict `[0,1]`. L'entrée scientifique,
les valeurs négatives du fond brut et les NaN/Inf ne bénéficient d'aucune
exception. Les diagnostics conservent le dépassement avant compensation.
La séparation vérifie aussi que le fond brut et le résidu float32 reconstruisent
l'entrée originale à `1e-6` près en intensité normalisée. Au-delà de cette marge
d'arrondi, elle échoue explicitement : un dépassement extrême ne doit pas
faire perdre le signal par annulation numérique.
Dans les pixels concernés, le fond corrigé retrouve l’intensité originale et
la couche d’étoiles devient nulle. Cela revient à borner le fond par l’original,
en maintenant `fond_corrigé + étoiles_positives = image_originale`, à l’arrondi
près. Les valeurs des deux branches doivent rester finies dans `[0,1]` ; une
géométrie incompatible ou un domaine corrigé invalide reste une erreur.

`--ghs-negative-tolerance=0.005` est un **seuil d’alerte**, dans `[0,0.05]`,
et ne provoque plus l’abandon d’une décomposition corrigeable. Un avertissement
indique le minimum, le nombre et la fraction de valeurs RGB qui dépassent ce
seuil. Le rapport `residual_compensation` conserve ces mesures dans
`minimum`, `above_tolerance_count`, `above_tolerance_fraction`,
`tolerance_exceeded`, ainsi que le flux transféré et l’écart de conservation.
Son statut est `CORRECTED` en cas de dépassement, `SUCCESS` sinon.
Le masque signé original reste dans les diagnostics : il n’est pas remplacé
par un masque écrêté sans correction simultanée du fond.

Sur la mosaïque M31 de validation, 1 221 valeurs RGB sur 69 277 800 étaient
inférieures à `-0.005` (environ 0,00176 %), avec un minimum de `-0.36232`.
La compensation garde les branches dans `[0,1]` et reconstruit l’entrée avec
un écart maximal de `2.98e-8`. Cette propriété algébrique ne certifie pas la
fidélité des structures retirées par le réseau ; l’inspection reste nécessaire.

## Étirement propre aux étoiles

**Contrainte utilisateur durable : cette branche réalise uniquement une
transformation globale de l'histogramme.** La même transformation RGB est
appliquée partout : deux triplets RGB identiques ont la même sortie,
indépendamment de leurs coordonnées ou de leur voisinage. Aucun masque,
renforcement par étoile, traitement local du piédestal, débruitage, filtrage ou
déconvolution n'est autorisé dans cette branche. Cette règle figure également
dans les consignes communes du dépôt.

Le mode effectif est `--star-contrast-mode pointwise`. Une ancienne valeur
`profiles`, en CLI ou dans la configuration, est acceptée comme alias de
compatibilité : elle produit la même courbe globale et ne réactive aucun
traitement local. `--star-noise-sigma` vaut zéro par défaut ; les anciennes
valeurs valides sont acceptées mais n'ont plus d'effet sur les pixels.

Le rendu `conservative` utilise l'asinh lié à la luminosité, puis une
transformation rationnelle des faibles intensités :

```text
Yhuman = 0,2126 R + 0,7152 G + 0,0722 B
Yeven  = (R + G + B) / 3
base(Y) = ceiling * asinh(strength*Y) / asinh(strength)
target(Y) = faint_boost * base(Y) / (1 + (faint_boost-1)*base(Y)/ceiling)
```

Le mode couleur suit `--ghs-color-mode human|even`. Le gain lié est commun aux
trois canaux. RGBBlend protège le plafond en mélangeant des courbes RGB
ponctuelles lorsque nécessaire ; son calcul dépend uniquement du triplet RGB,
jamais de sa position. Le plafond est 0,85, la force asinh 2 et le renforcement
des faibles niveaux 5 par défaut. Ce rendu est un étirement asinh et une courbe
d'intensité, même si la branche est rangée dans `03_ghs_stars`.

<a id="adaptation-a-la-repartition-des-pics"></a>
<a id="adaptation-à-la-répartition-des-pics"></a>

### Calibration de la courbe globale

`--star-histogram-strength 1` autorise une courbe monotone de quantiles,
calibrée sur la distribution des amplitudes des pics. Le quantile
`--star-boost-fraction 0.25` fixe son raccord entre intensités faibles et
brillantes. La courbe résultante est appliquée à tous les pixels ; ce n'est
ni un budget de corrections locales ni une sélection de coordonnées.
Zéro conserve la courbe de base ; un conserve l'adaptation globale complète.
Voir [construction de la transformation](stellar-selection.md).

Les mesures de profils à 50 % et 10 % du pic restent des diagnostics et des
gardes pour choisir des paramètres **communs à toute l'image**. Elles ne
produisent aucun gain local. Un dépassement de la limite de largeur diminue
la force ou le renforcement global. La limite des étoiles seules est
`--star-stretch-max-width-ratio`, qui hérite de `--star-max-width-ratio`
(valeur commune par défaut 2). Une largeur non mesurable ne certifie pas la
garde. Le contrôle distinct après recomposition reste inchangé.

<a id="attenuation-douce-du-bruit-residuel"></a>
<a id="atténuation-douce-du-bruit-résiduel"></a>

### Absence de débruitage dans cette branche

Aucune porte de bruit ne modifie le rendu. Les rapports indiquent
`histogram_only: true`, `spatially_varying: false`,
`contrast_mode: pointwise` et `noise_suppression.threshold: 0`.
Les anciennes valeurs de `star_noise_sigma` sont enregistrées à titre de
compatibilité, sans activer de traitement. L'estimation du bruit utilisée
pour calibrer les pics n'est pas une modification des pixels.
Les recettes de nouvelles images sont globales et se rejouent sans géométrie
stellaire native. Les anciens audits ou recettes spatiales restent lisibles
pour leur reproduction historique, mais ne sont plus produits par le rendu.

### Diagnostic spécifique des étoiles

Dans `05_autoghs/03_ghs_stars/` (ou `03_ghs_stars/` à côté du rapport autonome), `<image>_stars_diagnostic.png` présente :

- les mêmes extraits centraux (512 pixels de côté au plus) avant/après, avec les
  intensités affichées directement sans auto-étirement ni gamma ;
- l'histogramme de tous les pixels positifs en maximum RGB, axes X et Y log ;
- l'histogramme des amplitudes des pics aux mêmes positions, axes X et Y log ;
- un encart : objectif, effectif candidat, dépassements du seuil 0,03 pour le
  linéaire/le gain fixe/le rendu adapté, essais, force retenue et FWHM.
  Ce diagnostic ne dépend pas du fond ni de la recomposition.

Les zéros sont exclus des axes logarithmiques et leur fraction est affichée.
Le graphique, ses légendes et son encart utilisent le thème sombre commun
`lib.diagnostic_style` : fond noir et textes clairs. Les intensités des extraits
RGB restent affichées directement, sans inversion des intensités. Leur sens
haut/bas suit l'affichage Siril, même lorsque `ROWORDER=TOP-DOWN`.
`<image>_stars_render.jpg` fournit aussi la couche entière en pleine résolution,
à côté du FITS. Les chemins sont dans `stars.diagnostic_path` et
`stars.output_jpeg`. Le processeur stellaire autonome produit les mêmes types
d'exports à côté de son rapport. Ces vues sont des diagnostics de présentation.

Dans le pipeline AutoGHS, `stars.json` et `histogram_trials/` décrivent les
essais sur la couche stellaire seule : entrée, courbes fixes, adaptations des
pics si activées, renforcements rejetés et résultat retenu. Ces histogrammes d’essais utilisent la luminosité Y (proxy Rec.709), avec
4096 classes uniformes sur [0,1], indépendamment des paramètres GHS du fond.
Ils complètent les histogrammes en maximum RGB du diagnostic stellaire.
Les classes isolées à un pixel sont affichées avec des marqueurs pour rester
visibles sur les axes logarithmiques. Les PNG, la galerie
`histograms.html` et le manifeste `histograms.json` sont toujours produits.
`--ghs-save-trial-jpegs` ajoute les JPEG des mêmes essais dans chacune des deux
branches, sans aperçu recomposé. Aucun réglage GHS du fond ne participe à ces
essais stellaires.

## Deux recompositions screen contrôlées

Pour chaque fond étiré `B` et l'unique couche d'étoiles rendue `S` :

```text
B_safe = headroom * B
final = B_safe + S*(1-B_safe)
      = 1-(1-B_safe)*(1-S)
```

Cette loi screen correspond à une expression PixelMath, et non à une simple
addition de branches non linéaires. Elle est bornée pour des entrées bornées,
conserve une contribution stellaire positive et ne recadre aucune branche.
`headroom=0.95` réserve une marge commune aux canaux du fond. Le rendu
RANGE est écrit en FITS puis vérifié après fusion.

Les diagnostics comparent l'addition hypothétique `B_safe+S` (potentiellement
écrêtée) au résultat screen réellement retenu. Ils mesurent la contribution
réelle `final-B_safe`, ses rapports de couleur par rapport à S et ses largeurs
stellaires. Les dérives moyennes de chromaticité/chroma et le gonflement
mesurable sont soumis aux limites configurées. L'effet du fond coloré sur les
étoiles reste mesuré même si chaque branche isolée est non écrêtée.
Le rapport `recomposition.peak_contrast` compte également les pics dont la
contribution réinsérée dépasse 0,03 aux positions d'origine ; un fond très
brillant peut réduire ce contraste malgré un bon rendu des étoiles seules.

Cette loi est comparée à `pm "1-(1-0.95*$background$)*(1-$stars$)" -nosum`
dans Siril 1.4.4 : écart maximal mesuré `5.96e-8`.
Elle est également validée numériquement sur fonds brillants, étoiles colorées,
profils gaussiens centrés et sous-pixels. Le comportement exact de l'outil
**graphique** Star Recomposition de Siril n'est pas revendiqué. La mesure de
profils et le contrôle des négatifs ne remplacent pas l'inspection de halos,
trous ou détails compacts supprimés par le réseau StarNet.

Références : [StarNet Siril](https://siril.readthedocs.io/en/stable/processing/stars/starnet.html),
[tutoriel séparation et recomposition](https://siril.org/fr/tutorials/integrated-starnet/),
[recomposition graphique](https://siril.readthedocs.io/en/stable/processing/stars/star-recomp.html),
[algorithme GHS](auto-ghs-algorithm.md), [guide et paramètres](auto-ghs.md).

<a id="verification-sur-les-couches-m33-et-m31"></a>
<a id="comparaison-exploratoire-avec-une-courbe-ghs"></a>

### Vérification du rendu courant

Les tests de `test_stellar_contrast.py` vérifient un fond non nul laissé
inchangé, les profils gaussiens faibles et brillants, la couleur RGB, les
recouvrements, les exclusions et la reproduction exacte de la recette.
Les rendus réels sont comparés sur les mêmes couches StarNet et les mêmes
fonds GHS ; cette vérification ne valide pas une nouvelle séparation StarNet.
Les sources et les FITS scientifiques restent inchangés.

## Objectifs indicatifs de visibilité et de forme

Les [indicateurs d'apparence stellaire](stellar-appearance.md) mesurent un objectif
non bloquant d'au moins 25 % de candidats visibles par contraste local,
le diamètre apparent, la pente de transition et l'étendue de la jupe. Ils
figurent dans `appearance_quality` et dans le diagnostic PNG. Ils ne modifient
ni les pixels ni le choix de la courbe globale. Aucun seuil perceptuel de
netteté ou de jupe n'est encore déclaré validé.
