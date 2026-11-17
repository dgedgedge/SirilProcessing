# 1. Calage fractionnaire de l’histogramme stellaire

[postProcess](README.md) · [Rendu stellaire](star-rendering.md) · [Recettes](stretch-transfer.md)

## 1.1. Objectif et paramètre

`--star-boost-fraction=0.25` ne désigne pas un masque spatial. Il indique la
fraction supérieure des pics éligibles utilisée pour caler une courbe
ponctuelle unique. Le traitement ne consulte ni le fond, ni l’image originale,
ni le nom de l’objet. Deux pixels de même intensité reçoivent la même cible,
quelle que soit leur position.

Zéro conserve la base asinh. Un conserve le mode global historique à quantiles.
Entre les deux, le quantile `1-fraction` fixe le point d’inflexion de la nouvelle
courbe. Les options suivent CLI > JSON > défaut et sont persistantes avec `-S`.

## 1.2. Construction de la courbe

Le moteur travaille sur le maximum RGB pour repérer les pics et sur la base
asinh/RGBBlend déjà calculée pour produire le résultat :

1. détecter les maxima locaux dans une fenêtre de 7 pixels ;
2. conserver les pics au-dessus du seuil robuste de bruit déjà enregistré ;
3. calculer `q`, quantile `1-fraction` de leurs amplitudes ;
4. fixer le début de transition à `max(seuil_bruit, q/2)` ;
5. viser 0,08 au niveau `q`, pour un plafond de référence 0,85 ;
6. rejoindre 0,15 au percentile 95 des pics, puis le plafond à l’entrée 1 ;
7. conserver exactement la base sous le début de transition et dès que la
   base atteint 0,15.

Les niveaux 0,08 et 0,15 suivent proportionnellement `--star-ceiling`. Entre
les points, l’interpolation est linéaire en coordonnées logarithmiques. La
cible finale est le maximum de cette proposition et de la base : la courbe ne
diminue aucun pixel. Son gain est commun aux trois canaux après l’asinh/RGBBlend.

Pour une intensité source `x`, une base `B(x)` et la courbe log-log `H(x)` :

```text
T(x) = B(x)                                      si x <= q/2
T(x) = max(B(x), H(x))                           dans la transition
T(x) = B(x)                                      si B(x) >= 0,15
sortie = base_RGB * T(x) / max_RGB(base_RGB)
```

Il n’y a ni coordonnées sauvegardées, ni profil radial, ni raccord spatial.
La courbe peut donc être rejouée sur une autre géométrie. La détection des pics
sert uniquement au calage statistique initial.

## 1.3. Gardes de largeur et de bruit

La porte quartique de bruit appartient à la base et est appliquée avant la
courbe fractionnaire. La zone située sous le début de transition reste donc
inchangée. Un résidu et une étoile de même intensité ne peuvent toutefois pas
être distingués par une transformation d’histogramme.

Après chaque proposition, le moteur mesure les profils à 50 % et 10 % du pic.
Le rapport FWHM médian ne doit pas dépasser la garde configurée et le percentile
10 des rapports ne doit pas descendre sous 0,90. Si nécessaire, la force de la
courbe est divisée par deux, au plus huit fois ; un dernier essai nul conserve
la base. Ce contrôle mesure des profils, mais la transformation elle-même reste
strictement ponctuelle.

## 1.4. Rapport et recette

`boost_selection.mode` vaut `histogram_quantile`. Le rapport conserve la
fraction, les effectifs, `cutoff`, `transition_start`, `visible_target`,
`bright_protection`, les points de la courbe et la force retenue.
`actually_boosted_count` compte les pics dont le sommet a augmenté ; ce n’est
pas un dénombrement astrophysique.

La recette RGB rejoue les points et le seuil de bruit enregistrés, sans
redétecter les étoiles ni recalculer les quantiles. Les anciennes recettes
`profiles` et `spatial` restent lisibles pour reproduire les traitements déjà
enregistrés ; elles ne sont plus produites par le défaut à 25 %.

La courbe scalaire porte `scalar_role: neutral_rgb_restriction`. RGBBlend reste
dépendant du triplet RGB : la recette RGB est la référence de reproduction.

## 1.5. Limites et vérifications

Le seuil 0,08 est un niveau de présentation pour un affichage direct normalisé,
pas une grandeur photométrique ni une garantie de perception après fusion. Les
pics peuvent contenir des résidus StarNet. Une transformation ponctuelle peut
renforcer tout pixel de même intensité ; la porte de bruit et le début de
transition limitent ce risque sans le supprimer.

Les tests couvrent les fractions limites, les champs vides ou constants, la
monotonie, le plafond, les couleurs, les gardes FWHM, l’absence de coordonnées
et la reproduction exacte sur la géométrie native ou recadrée. Les recettes
historiques à profils possèdent leurs tests de compatibilité séparés.
