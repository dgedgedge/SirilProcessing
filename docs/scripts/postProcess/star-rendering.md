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

Le script charge un RGB float32 de travail et exécute **`starnet -stretch`**,
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

Le défaut applique désormais une [sélection à 25 %](stellar-selection.md) au
renforcement adaptatif supplémentaire. La base asinh est d’abord rendue
admissible, puis la fraction est sélectionnée sur la couche stellaire seule.
Les étapes globales historiques décrites ci-dessous restent disponibles avec
`--star-boost-fraction 1` ; les essais GHS présentés en fin de page restent
des comparaisons hors pipeline.

Le rendu `conservative` commence par une courbe monotone de référence :

```text
L = max(R,G,B)
base(L) = ceiling * asinh(strength*L) / asinh(strength)
target(L) = faint_boost * base(L) / (1 + (faint_boost-1)*base(L)/ceiling)
fixed_gain = target(L)/L si L>0, sinon 0
```

Le maximum RGB sert uniquement à contrôler le gamut, sans supposer les
primaires colorimétriques de l'image. Un gain identique sur les trois canaux
préserve leurs rapports. Le plafond de 0,85 ménage les hautes lumières, tandis
que la force asinh 2 est complétée par `--star-faint-boost=5` par défaut.
La seconde courbe augmente surtout les faibles intensités et comprime doucement
les fortes vers le même plafond. Avant atténuation du bruit, le gain à l'origine vaut
`ceiling*strength*faint_boost/asinh(strength)`, soit environ 5,89 par défaut,
contre 1,18 avec `--star-faint-boost=1`. Le facteur est paramétrable
de 1 à 10 : c'est le renforcement **demandé**, que la garde de largeur peut
réduire automatiquement. Il est sauvegardé par Config avec les autres options. Les étoiles
fortes ne sont pas toutes ramenées à une intensité commune.

### Atténuation douce du bruit résiduel

Après la courbe de référence et l'éventuelle adaptation des pics, le moteur
applique une porte monotone au maximum RGB `L` :

```text
T = min(1, médiane(L) + star_noise_sigma * sigma)
porte(L) = (1 + T^4) * L^4 / (L^4 + T^4)
target_final(L) = target(L) * porte(L)
```

`--star-noise-sigma=6` règle le seuil de transition en écarts robustes du
**résidu positif**, et non du bruit signé de l'image d'origine. La dispersion
est estimée par `1.4826 * MAD(différences voisines) / sqrt(2)` sur chaque axe ;
le minimum des deux estimations limite la confusion avec un gradient axial.
Le facteur doit être fini dans `[0,20]` ; zéro désactive la porte. Le seuil
est exprimé en intensité normalisée. Les dimensions inférieures à trois pixels,
les champs constants et une dispersion inférieure ou égale à `1e-7` désactivent
également la porte, sans inventer une mesure de bruit exploitable.

La pente devient nulle à l'origine : les très faibles résidus sont atténués,
les intensités significatives retrouvent progressivement la courbe de
renforcement, et la borne `1 → ceiling` reste préservée. Le même gain est
appliqué aux trois canaux ; aucun filtrage spatial ni seuil dur n'est appliqué.
Les étoiles confondues avec le bruit peuvent être atténuées avec lui.

Pour protéger les ailes, le moteur mesure les largeurs à 50 % et à 10 % du
pic au-dessus du fond local, aux mêmes positions avant/après. Le percentile
10 des rapports après/avant doit rester au moins égal à `0,90` pour chacune
des deux hauteurs. Sinon, le seuil `T` est divisé par deux. Après huit essais,
un dernier essai désactive la porte. Si l'une des deux mesures est impossible,
la porte est également réduite jusqu'à désactivation ; cela ne certifie pas
la qualité des profils. La garde supérieure de FWHM reste appliquée au rendu
final et réduit le renforcement demandé si nécessaire.

Le rapport `noise_suppression` contient la médiane, le sigma estimé, le facteur,
les seuils demandé/retenu et chaque essai avec ses mesures et son acceptation.
`wings` décrit les largeurs à 10 % ; `fwhm` celles à 50 %, avec leur percentile
10 (`p10_ratio`). La recette exportée conserve le seuil retenu pour rejouer le
rendu sans réestimer le bruit sur une autre image.

Ces mesures locales ne garantissent pas la conservation de toutes les ailes :
la fenêtre fait 15 pixels de côté et la sélection reste limitée aux 64 pics
les plus brillants au-dessus de `0,005`. Une étoile plus faible peut donc ne
pas participer au contrôle. Le seuil utilisé est enregistré dans
`peak_threshold`. Le bruit corrélé, les résidus écrêtés à zéro et les champs
très denses biaisent l'estimation. La réduction effective du bruit
n'est pas garantie pour toute image, notamment si la protection des profils
impose de réduire ou désactiver la porte. Une inspection sur données réelles
reste nécessaire.

### Adaptation à la répartition des pics

Un gain fixe reste insuffisant lorsque les amplitudes linéaires des étoiles sont
très faibles. Une valeur positive de `--star-histogram-strength` active
une adaptation, active par défaut (`1`), pour rendre visibles les faibles
étoiles dont le gain fixe laisse les pics proches de zéro :

1. Repérer les maxima du maximum RGB sur une fenêtre de 7 pixels. Le seuil est
   `max(1e-5, médiane + 5 × 1.4826 × MAD)`, en intensité normalisée. Un plateau
   connexe fournit une seule position. Ce sont des **pics candidats**, pouvant
   inclure du bruit ou des résidus StarNet, pas des étoiles certifiées.
2. Écarter du calage les pics inférieurs ou égaux à
   `max(1e-5, médiane(L) + 12*sigma)`, où sigma est la dispersion robuste par
   différences voisines décrite plus haut. Cette marge conservatrice limite
   le calage sur le bruit positif lorsque sa MAD de niveaux est presque nulle.
   Le rapport conserve `calibration_peak_threshold` et `calibration_peak_count`.
   Avec au moins huit pics restants et quatre amplitudes distinctes, calculer 17 quantiles
   entre 5 et 95 %. Leur attribuer des niveaux régulièrement espacés en logarithme
   de 0,04 à 0,65 pour un plafond de 0,85 ; ces niveaux suivent proportionnellement
   le plafond configuré. Interpoler en log-log, prolonger linéairement vers zéro
   et ajouter la borne `1 → ceiling`. Garder le maximum de cette courbe et du
   rendu fixe : l'adaptation ne diminue aucune contribution.
3. Mélanger cette proposition au rendu fixe avec la force demandée. Mesurer la
   largeur des profils ; si elle dépasse la garde, diviser la force par deux et
   réessayer, au plus huit fois. Retenir le premier essai admissible, ou conserver
   le gain fixe si aucun ne passe.
4. Appliquer `gain = target(L)/L` aux trois canaux, avec un gain nul si `L=0`.

Si le rendu obtenu dépasse encore la limite de largeur, réduire `faint_boost`
d'une unité et recommencer ces étapes sur les mêmes étoiles linéaires.
La recherche commence à la valeur demandée et s'arrête au **premier rendu
admissible**, sans essayer les renforcements plus faibles. La valeur 1 est
toujours essayée en dernier : par exemple `5 → 4 → 3 → 2 → 1` ou
`2,4 → 1,4 → 1`. La force asinh, le plafond et la limite de largeur restent
inchangés. Les valeurs demandées en CLI/JSON ne sont pas réécrites.
Avec un facteur limité à 10, la boucle comporte au plus dix essais de base.
Si même le dernier essai dépasse la limite, l'erreur indique le seuil et
les renforcements/rapports essayés. Les autres erreurs restent bloquantes.

L'objectif est de répartir davantage les amplitudes des pics en logarithme.
Il ne s'agit pas de forcer l'histogramme de **tous les pixels** à être plat :
le fond nul et les ailes stellaires le dominent, et leur égalisation peut rendre
visibles des résidus. La limitation FWHM peut empêcher d'atteindre la force
demandée. Les cas constants ou insuffisamment peuplés conservent la courbe fixe.
`--star-histogram-strength=0` retrouve cette courbe ; ajouter
`--star-faint-boost=1 --star-noise-sigma=0` retrouve le rendu asinh historique. Ces paramètres suivent
CLI > JSON > défaut et sont persistants avec `--save-config`.

Il n'y a ni rééchantillonnage ni optimisation du fond sur cette branche.
La sélection à 25 % emploie une pondération spatiale du supplément ; le mode
historique global (`--star-boost-fraction 1`) reste une courbe ponctuelle. Une couleur déjà perdue dans un noyau
saturé ne peut pas être rétablie.

Aucun pixel stellaire nul n'est rendu positif par ce renforcement : il révèle
les contributions déjà extraites et ne recrée pas une étoile absente du masque.
Les rapports donnent `requested_faint_boost` (demande), `faint_boost` (retenu),
et `width_adaptation` (pas, essais de base, rapports de largeur, acceptation,
historique de l'adaptation des pics pour chaque essai et motif d'arrêt).
Une largeur non mesurable reste signalée comme telle, sans certifier la garde.
Le journal indique chaque renforcement refusé avec sa mesure et la réduction
finalement retenue. Les rapports donnent aussi le gain à l'origine de la courbe fixe
`fixed_curve_faint_intensity_gain`, et celui de la courbe retenue
`faint_intensity_gain` (nul lorsque la porte de bruit est active). `histogram_balance` contient la force demandée/retenue,
les points de la table, chaque essai et son rapport de largeur, ainsi que le
motif d'arrêt. Les métriques `visibility` comparent, **aux mêmes positions**, le
nombre de pics au-dessus de 0,03 en maximum RGB avant et après. Ce seuil fixe
est un indicateur de contraste normalisé, sans modèle de perception ni SNR.

Les contrôles portent sur les fractions à 0 et 1 par canal, les excursions du
domaine et la chromaticité RGB normalisée. La chroma de diagnostic est l'écart
entre maximum et minimum des composantes RGB normalisées par leur somme :
c'est une mesure relative aux canaux, pas une saturation perceptuelle calibrée.

Une FWHM approximative est mesurée aux mêmes pics avant et après : au plus 64
maxima locaux, au-dessus de 0,005, dans une fenêtre 15 × 15. Deux profils axiaux
sont corrigés du niveau médian des extrémités ; les traversées de demi-hauteur
sont interpolées. Les profils tronqués ou sans traversée exploitable sont
exclus. Le rapport fournit le nombre de profils et les largeurs médianes ;
`null` exprime une mesure impossible. Le seuil de rapport médian
`--star-stretch-max-width-ratio`, hérité de `--star-max-width-ratio` en
l’absence de valeur propre explicite,
déclenche la réduction du renforcement et ne provoque une erreur qu'après
épuisement des essais de base. Il s'agit d'un contrôle de profils locaux,
**pas d'un ajustement PSF**, ni d'une garantie d'absence de halos faibles.
Une limite de 1,5 autorise une augmentation de 50 % de cette largeur médiane.
La valeur par défaut commune est 2, mais une valeur CLI/JSON existante reste
prioritaire. Une valeur propre explicite, par exemple 1,2, reste respectée.
Cette garde est une heuristique de présentation, sans calibration physique.
Le contrôle distinct après fusion conserve `--star-max-width-ratio` et reste bloquant si cette recomposition
introduit un dépassement : il ne relance pas les traitements amont.

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

### Vérification sur les couches M33 et M31

Un essai sur les couches StarNet déjà produites de M33 et M31 a comparé le
rendu limité à un renforcement retenu de 1 avec une adaptation demandée de 1
et une limite de largeur de 1,5. Le renforcement de base demandé reste 5.
Les fonds GHS existants ont été réutilisés sans nouveau calcul.

| Mesure | M33 | M31 |
|---|---:|---:|
| Pics de la couche au-dessus de 0,03, ancien rendu | 304 | 1 600 |
| Pics de la couche au-dessus de 0,03, essai adaptatif | 11 492 | 34 284 |
| Rapport FWHM médian de l'essai | 1,463 | 1,488 |
| Pics de contribution après fusion au-dessus de 0,03 | 10 949 | 32 107 |

Les mêmes positions candidates sont suivies ; ces nombres ne constituent pas
un catalogue d'étoiles astrophysiques. Aucun pixel à 1 n'a été relevé dans les
couches étirées ni dans les recompositions de cet essai. Cette vérification
réutilise la séparation existante et ne valide pas une nouvelle exécution de
StarNet ou de Siril. Les rapports et images d'origine sont préservés.

Une configuration sauvegardée avec `star_histogram_strength: 0` continue de
désactiver l'adaptation ; fournir `--star-histogram-strength 1` pour l'activer.
De même, un `star_stretch_max_width_ratio: 1.2` sauvegardé reste explicite :
le remplacer en CLI par `--star-stretch-max-width-ratio 1.5` pour cet essai.

### Comparaison exploratoire avec une courbe GHS

Une recherche hors pipeline a également évalué `lib.auto_ghs.ghs_transfer`
sur les mêmes couches. La transformation est appliquée au maximum RGB,
normalisée au plafond de 0,85, puis suivie de la même porte de bruit ; un gain
commun conserve les rapports RGB. Le moteur stellaire de production reste
l'asinh adaptatif : ces candidats GHS ne constituent pas une nouvelle option.

La recherche a testé D dans `{2,3,4,5,6,7,8}`, B dans `{-1,0,2,5}` et HP dans
`{0.001,0.01,0.05,0.2,1}`, avec LP=0 et SP égal à la médiane des pics candidats.
Les candidats respectent une FWHM médiane ≤ 1,5 et les contrôles de
rétrécissement à 50 % et 10 %. Les paramètres sélectionnés ont ensuite été
réappliqués aux images complètes, puis fusionnés avec les fonds existants.

| Mesure GHS | M33 | M31 |
|---|---:|---:|
| D / B / HP | 8 / 2 / 0,05 | 7 / 0 / 0,01 |
| SP | 0,000138988 | 0,000304270 |
| Pics de couche au-dessus de 0,03 | 17 021 | 32 843 |
| Rapport FWHM médian, 63 profils | 1,384 | 1,039 |
| Rapport médian à 10 % du pic, 62 profils | 1,431 | 1,015 |
| Pics de contribution après fusion au-dessus de 0,03 | 16 734 | 31 798 |

Aucun pixel à 1 n'est relevé dans ces couches ou leurs recompositions. Le
compromis visibilité/largeur est favorable au GHS dans ces deux essais ; cette
grille finie ne démontre pas un optimum universel. La sélection des profils
reste dominée par les étoiles brillantes et les fenêtres de 15 pixels ne
contrôlent pas les halos étendus.

La [documentation Siril sur les étirements](https://siril.readthedocs.io/en/stable/processing/stretching.html)
décrit SP et la protection linéaire au-delà de HP. Cette protection ne dispense
pas de mesurer les profils après transformation et après fusion.
