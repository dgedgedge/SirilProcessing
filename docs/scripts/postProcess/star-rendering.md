# Séparation, étoiles et recomposition

[postProcess](README.md) › [AutoGHS](auto-ghs.md) › Étoiles

## Modules et ordre des opérations

[`StarNetSeparator`](../../../lib/star_separation.py) hérite de `processor` et
réutilise le service `Siril.run_siril_script`, ses modes d'exécution et ses
journaux. [`ConservativeStarRenderer`](../../../lib/star_rendering.py), également
un `processor`, possède une méthode de calcul indépendante `render`.
`recompose` est une opération sans état du même module.
[`star_diagnostics`](../../../lib/star_diagnostics.py) produit les vues et
histogrammes de la couche d'étoiles, sans intervenir dans le calcul.
[`AutoGHSFinalRenderer`](../../../lib/final_render.py) orchestre les enfants,
contrôle et publie leurs sorties ; il ne contient pas le moteur GHS.

L'ordre est strict : **une séparation → une couche d'étoiles étirée → deux
fonds GHS indépendants → deux fusions**, en mode both. Les deux recompositions
reçoivent le même tableau d'étoiles rendu. Les tests contractuels enregistrent
ces appels et vérifient sur disque leur contribution non nulle.

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
sont contrôlés. Un changement de convention `ROWORDER` est corrigé par un
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
la somme linéaire. Les deux stratégies reçoivent ce **même fond corrigé**.
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
les fortes vers le même plafond. Le gain à l'origine vaut
`ceiling*strength*faint_boost/asinh(strength)`, soit environ 5,89 par défaut,
contre 1,18 avec `--star-faint-boost=1`. Le facteur est paramétrable
de 1 à 10 et est sauvegardé par Config avec les autres options. Les étoiles
fortes ne sont pas toutes ramenées à une intensité commune.

### Adaptation à la répartition des pics

Un gain fixe reste insuffisant lorsque les amplitudes linéaires des étoiles sont
très faibles. Par défaut, `--star-histogram-strength=0.8` ajoute une adaptation :

1. Repérer les maxima du maximum RGB sur une fenêtre de 7 pixels. Le seuil est
   `max(1e-5, médiane + 5 × 1.4826 × MAD)`, en intensité normalisée. Un plateau
   connexe fournit une seule position. Ce sont des **pics candidats**, pouvant
   inclure du bruit ou des résidus StarNet, pas des étoiles certifiées.
2. Avec au moins huit pics et quatre amplitudes distinctes, calculer 17 quantiles
   entre 5 et 95 %. Leur attribuer des niveaux régulièrement espacés en logarithme
   de 0,04 à 0,65 pour un plafond de 0,85 ; ces niveaux suivent proportionnellement
   le plafond configuré. Interpoler en log-log, prolonger linéairement vers zéro
   et ajouter la borne `1 → ceiling`. Garder le maximum de cette courbe et du
   rendu fixe : l'adaptation ne diminue aucune contribution.
3. Mélanger cette proposition au rendu fixe avec la force demandée. Mesurer la
   largeur des profils ; si elle dépasse la garde, diviser la force par deux et
   réessayer, au plus huit fois. Retenir le premier essai admissible, ou conserver
   le gain fixe si aucun ne passe. Un gain fixe lui-même hors limite reste une erreur.
4. Appliquer `gain = target(L)/L` aux trois canaux, avec un gain nul si `L=0`.

L'objectif est de répartir davantage les amplitudes des pics en logarithme.
Il ne s'agit pas de forcer l'histogramme de **tous les pixels** à être plat :
le fond nul et les ailes stellaires le dominent, et leur égalisation peut rendre
visibles des résidus. La limitation FWHM peut empêcher d'atteindre la force
demandée. Les cas constants ou insuffisamment peuplés conservent la courbe fixe.
`--star-histogram-strength=0` retrouve cette courbe ; ajouter
`--star-faint-boost=1` retrouve le rendu asinh historique. Ces paramètres suivent
CLI > JSON > défaut et sont persistants avec `--save-config`.

Il n'y a ni rééchantillonnage, ni filtre spatial appliqué à l'image, ni
optimisation du fond sur cette branche. Une couleur déjà perdue dans un noyau
saturé ne peut pas être rétablie.

Aucun pixel stellaire nul n'est rendu positif par ce renforcement : il révèle
les contributions déjà extraites et ne recrée pas une étoile absente du masque.
Les rapports donnent `faint_boost`, le gain à l'origine de la courbe fixe
`fixed_curve_faint_intensity_gain`, et celui de la courbe retenue
`faint_intensity_gain`. `histogram_balance` contient la force demandée/retenue,
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
`null` exprime une mesure impossible. Le seuil de rapport médian, 2 par défaut,
provoque une erreur s'il est dépassé. Il s'agit d'un contrôle de profils locaux,
**pas d'un ajustement PSF**, ni d'une garantie d'absence de halos faibles.
Ce seuil autorise un doublement de la largeur apparente médiane ; il constitue
une garde heuristique de présentation, sans calibration physique.

### Diagnostic spécifique des étoiles

Dans `05_autoghs_work/002_stars/`, `<image>_stars_diagnostic.png` présente :

- les mêmes extraits centraux (512 pixels de côté au plus) avant/après, avec les
  intensités affichées directement sans auto-étirement ni gamma ;
- l'histogramme de tous les pixels positifs en maximum RGB, axes X et Y log ;
- l'histogramme des amplitudes des pics aux mêmes positions, axes X et Y log ;
- un encart : objectif, effectif candidat, dépassements du seuil 0,03 pour le
  linéaire/le gain fixe/le rendu adapté, essais, force retenue et FWHM.
  Après recomposition, l'encart ajoute les effectifs dont le contraste réinséré
  dépasse ce même seuil pour chaque stratégie.

Les zéros sont exclus des axes logarithmiques et leur fraction est affichée.
`<image>_stars_render.jpg` fournit aussi la couche entière en pleine résolution,
à côté du FITS. Les chemins sont dans `stars.diagnostic_path` et
`stars.output_jpeg`. Le processeur stellaire autonome produit les mêmes types
d'exports à côté de son rapport. Ces vues sont des diagnostics de présentation.

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
`headroom=0.95` réserve une marge commune aux canaux du fond. Les deux variantes
produisent leurs propres FITS étoilés et sont vérifiées après fusion.

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
