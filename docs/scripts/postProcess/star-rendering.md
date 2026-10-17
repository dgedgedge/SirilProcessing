# Séparation, étoiles et recomposition

[postProcess](README.md) › [AutoGHS](auto-ghs.md) › Étoiles

## Modules et ordre des opérations

[`StarNetSeparator`](../../../lib/star_separation.py) hérite de `processor` et
réutilise le service `Siril.run_siril_script`, ses modes d'exécution et ses
journaux. [`ConservativeStarRenderer`](../../../lib/star_rendering.py), également
un `processor`, possède une méthode de calcul indépendante `render`.
`recompose` est une opération sans état du même module.
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

Un résidu négatif trop grand indique une extraction incompatible avec le
rendu conservateur. Si son amplitude dépasse `ghs-negative-tolerance`, le
traitement échoue. Pour les faibles résidus dans cette tolérance :

```text
negative = min(stars_linear, 0)
background_corrected = starless_linear + negative
stars_positive = stars_linear - negative
```

Le flux est transféré dans le fond avant tout étirement, avec conservation de
la somme linéaire. Les deux stratégies reçoivent ce **même fond corrigé**.
La fraction de pixels concernés, le flux transféré et l'écart de conservation
sont consignés. Le masque signé original reste dans les diagnostics : il
n'est pas remplacé silencieusement par un masque écrêté.

## Étirement propre aux étoiles

Le rendu `conservative` utilise une seule courbe monotone :

```text
L = max(R,G,B)
base(L) = ceiling * asinh(strength*L) / asinh(strength)
target(L) = faint_boost * base(L) / (1 + (faint_boost-1)*base(L)/ceiling)
gain = target(L)/L si L>0, sinon 0
stars_stretched = stars_positive * gain
```

Le maximum RGB sert uniquement à contrôler le gamut, sans supposer les
primaires colorimétriques de l'image. Un gain identique sur les trois canaux
préserve leurs rapports. Le plafond de 0,85 ménage les hautes lumières, tandis
que la force asinh 2 est complétée par `--star-faint-boost=5` par défaut.
La seconde courbe augmente surtout les faibles intensités et comprime doucement
les fortes vers le même plafond. Le gain à l'origine vaut
`ceiling*strength*faint_boost/asinh(strength)`, soit environ 5,89 par défaut,
contre 1,18 avec `--star-faint-boost=1` (rendu antérieur). Le facteur est paramétrable
de 1 à 10 et est sauvegardé par Config avec les autres options. Les étoiles
fortes ne sont pas toutes ramenées à une intensité commune. Il n'y a ni
rééchantillonnage, ni filtre spatial, ni boucle d'optimisation du fond sur cette
branche. Une couleur déjà perdue dans un noyau saturé ne peut pas être rétablie.

Aucun pixel stellaire nul n'est rendu positif par ce renforcement : il révèle
les contributions déjà extraites et ne recrée pas une étoile absente du masque.
Les rapports donnent `faint_boost` et `faint_intensity_gain`.

Les contrôles portent sur les fractions à 0 et 1 par canal, les excursions du
domaine et la chromaticité RGB normalisée. La chroma de diagnostic est l'écart
entre maximum et minimum des composantes RGB normalisées par leur somme :
c'est une mesure relative aux canaux, pas une saturation perceptuelle calibrée.

Une FWHM approximative est mesurée aux mêmes pics avant et après : au plus 64
maxima locaux, au-dessus de 0,005, dans une fenêtre 15 × 15. Deux profils axiaux
sont corrigés du niveau médian des extrémités ; les traversées de demi-hauteur
sont interpolées. Les profils tronqués ou sans traversée exploitable sont
exclus. Le rapport fournit le nombre de profils et les largeurs médianes ;
`null` exprime une mesure impossible. Le seuil de rapport médian, 1,5 par défaut,
provoque une erreur s'il est dépassé. Il s'agit d'un contrôle de profils locaux,
**pas d'un ajustement PSF**, ni d'une garantie d'absence de halos faibles.

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
