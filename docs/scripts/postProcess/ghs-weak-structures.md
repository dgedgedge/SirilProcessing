# 1. Structures faibles et sobriété du ciel en GHS starless

[AutoGHS](auto-ghs.md) · [Forme de l'histogramme](ghs-histogram-shape.md)

## 1.1. Objectif et périmètre

`lib.ghs_weak_structure.WeakStructureQuality` ajoute une mesure des structures
faibles détectées dans l'entrée de phase à la comparaison historique ciel
P25 / diffus P75–P95. Il intervient dans le classement `piecewise`, sans
masque de correction, filtre ou étirement spatial : les pixels restent
transformés par une courbe GHS globale. Aucun nom d'objet, numéro d'essai
ou position de bord privilégiée ne participe au score.

Le but est de permettre un ciel plus sombre lorsque les structures faibles
restent lisibles, plutôt que d'exiger une hausse de leur seule luminosité.
Le mode historique `line` garde sa politique de sélection ; les nouvelles
mesures peuvent apparaître dans les rapports sans modifier ce classement.

## 1.2. Référence fixe et détection

Chaque mesure utilise une grille spatiale régulière d'au plus 192 pixels sur
le plus grand côté, avec les mêmes coordonnées dans référence et candidat.
Sur GPU, seule cette petite grille est transférée vers le CPU. Les régions
sont reconstruites de façon déterministe sur la référence, jamais sur le
candidat : elles ne se déplacent donc pas pour améliorer artificiellement
un essai. La signature SHA-256 de la grille de référence, ses dimensions,
les effectifs et les seuils doivent correspondre pour comparer deux mesures.
Les phases qui commencent sur une autre image possèdent leur propre référence.

Le détecteur utilise la luminosité du mode GHS `human` ou `even` :

- une tendance gaussienne lente de sigma 12 cellules ;
- des excès positifs aux échelles 1, 2 et 4, au-dessus de trois sigmas de
  bruit filtré estimés à partir des différences de voisins ;
- des composantes d'au moins 16 cellules ;
- les niveaux P25–P50 **à l'intérieur des objets détectés**, pour leurs parties faibles.

Le masque de ciel exclut les objets et leur voisinage dilaté, demande une
proximité avec la tendance lente à deux sigmas et exclut le quart le plus
lumineux de la référence. Les zéros inactifs ne deviennent pas du ciel.
Au moins 32 cellules faibles et suffisamment de ciel sont nécessaires.
Les filtres ne servent qu'à cette détection ; ils ne remplacent aucun pixel.

Cette segmentation est une heuristique, pas une preuve de ciel vide ni une
identification astrophysique. De grandes structures lisses, des gradients ou
un champ très rempli peuvent biaiser les masques. Les régions insuffisantes,
une référence constante ou un bruit non mesurable ne donnent aucun bonus :
le classement antérieur est conservé dans ces cas.

## 1.3. Mesures

Sur les mêmes masques, avant et après, on mesure :

- `faint_median` : luminosité médiane des parties faibles de l'objet ;
- `sky_median` : luminosité médiane du ciel ;
- `contrast = (faint_median - sky_median) / (faint_median + sky_median)` ;
- `cnr = (faint_median - sky_median) / noise`, avec le bruit MAD des différences
  de voisins dont les deux cellules sont classées ciel ;
- `sky_upper_median` : percentile 90 des médianes de ciel sur une grille de
  4 × 4 régions, avec au moins quatre régions mesurables.

La dernière mesure limite le masquage d'un ciel régional trop clair par une
seule médiane globale. Elle n'assimile pas les bordures à du ciel.
Les luminosités sont normalisées ; contraste et CNR sont sans unité. Les
mesures sont des estimations sur grille, même lors de la validation complète
du candidat : `estimated: true` et le pas sont enregistrés explicitement.
Les contrôles historiques de validation restent effectués par leurs moteurs.

## 1.4. Préférence et limites de visibilité

Les conditions mesurées de préservation demandent :

- contraste relatif conservé à au moins 98 % ;
- CNR conservé à au moins 98 % ;
- niveau faible au moins égal à 98 % de `min(niveau faible de référence, 0,08)`.

Le niveau 0,08 est une cible d'affichage heuristique, pas un seuil physique.
Cette dernière garde empêche d'obtenir un meilleur ciel en rendant tout
arbitrairement noir. La réduction du ciel doit dépasser 1 % pour lever les
seuls refus de luminosité diffuse et d'absence de gain de présentation.
Les gardes de bruit, couleurs, hautes lumières et écrêtage restent actives.

Le score ajoute une contribution de poids 0,5 :

1. La progression de visibilité combine le contraste relatif, saturé à 0,1,
   et le niveau faible, saturé à 0,08.
2. Un ciel plus sombre peut recevoir jusqu'à 0,5 point si les trois conditions
   de préservation sont réunies.
3. L'éclaircissement du ciel est pénalisé lorsqu'il dépasse le budget
   `max(1, 0,08 / niveau_faible_initial) × min(2, max(1, conservation_CNR))`.
   Ainsi, un objet linéaire très sombre peut d'abord être révélé sans être
   assimilé à une suramplification du fond.
4. Si la référence atteint déjà les deux cibles visuelles, un éclaircissement
   du ciel de plus de 1 % sans gain de CNR supérieur à 2 % ni progression
   visuelle supérieure à 0,02 reçoit une pénalité supplémentaire
   `2 × log2(gain_du_ciel)`. Cela empêche les seuls bonus d’histogramme de
   justifier une forte remontée du ciel sur un objet déjà lisible.
5. Les déficits de contraste, de CNR et de niveau faible sont pénalisés par
   deux fois la somme des carrés de leurs déficits relatifs sous 1.

Dans la présentation, une mesure fiable fait passer le score avant le seul
déplacement du pic dans le départage. Sans mesure fiable, l'ancien départage
reste disponible. Dans les recherches de forme, cette contribution complète
le score existant. Ce n'est pas une garantie d'identité des résultats avec
les versions précédentes ni une calibration universelle de la perception.

## 1.5. Pentes et protections

Si les structures faibles sont préservées et les cinq pentes restent
strictement descendantes, la pénalité de courbure est plafonnée à 0,25 avant
la pondération du moteur. La présentation peut alors conserver un candidat
malgré une inversion d'ordre signé ou une déformation relative de pente.
Les valeurs et écarts restent enregistrés : ils ne deviennent pas des PASS.

Une pente devenue croissante ou quasi nulle, une nouvelle bosse sombre, la
perte de l'épaulement, un déplacement de pic excessif ou une forme non
mesurable gardent leurs rejets structurels. Une pénalité faible ne peut pas
les contourner. Sans régions faibles exploitables ou sans préservation,
la pénalité et les rejets de présentation antérieurs sont conservés.

## 1.6. Rapports et vérifications

Les mesures sont dans `weak_structure`, leur comparaison dans
`weak_structure_comparison` et/ou `render_quality.weak_structure`. Les PNG
présentent le contraste faible, la conservation du CNR, le niveau régional du
ciel, le niveau faible et la contribution au score. Les cinq segments de
pente et leur diagnostic restent affichés séparément.

Les tests vérifient les masques fixes, la conservation des sources, la
préférence d'un ciel moins lumineux, les contre-exemples de perte de signal
et de noir artificiel, les références incompatibles, les champs uniformes,
le repli historique et la protection contre les nouvelles bosses. Les essais
sur recettes réelles sont des relectures sur grille ; une validation sur
images complètes reste distincte d'une comparaison de recettes archivées.

<a id="zones-map"></a>

## 1.7. Carte colorée des populations de mesure

Chaque nouveau rendu de la branche starless produit `starless_zones.png`,
dans `06_autoghs/03_ghs_starless/` pour une sortie numérotée. Le rapport
`starless.json` contient `zones_map`, avec chemin du PNG, seuils, effectifs,
pas de grille, disponibilité et signature de la référence spatiale.
Le module `lib.starless_zones.StarlessZoneMap` expose `classify(data, color_mode)`
et `write(data, destination, rendered=..., color_mode=...)`. Les entrées sont
des RGB normalisés ; aucun FITS n'est modifié. La carte est un diagnostic,
pas un masque appliqué à l'étirement et pas une segmentation astrophysique.

Les trois panneaux montrent :

1. Le rendu starless retenu comme contexte. Sans rendu fourni à l'API, un
   aperçu asinh global sert uniquement à la visualisation.
2. Les classes de luminosité de référence : bleu pour le fond ≤ P25, orange
   pour P75–P95, rose pour > P95, gris pour les autres niveaux et noir pour les
   pixels inactifs. Si P25 et P75 sont confondus, un violet distinct signale
   le recouvrement fond/diffus au lieu de cacher l'un des masques.
3. La détection spatiale : bleu pour le ciel admissible, orange pour les
   structures hors parties faibles, vert pour les parties faibles P25–P50,
   gris pour les cellules non classées et noir pour les inactives.

Les deux cartes sont séparées parce que leurs populations peuvent se
recouvrir. Les percentiles globaux sont calculés sur le champ complet ; les
structures utilisent la grille régulière et le moteur exact des mesures
`WeakStructureQuality.classify_grid`. La grille d'au plus 192 cellules sur
le plus grand côté est affichée sans interpolation. Son pas et sa taille
sont inscrits sur le PNG ; ce n'est pas une carte à résolution native.

La référence représentée est **l'entrée de la branche starless**, après
compensation éventuelle des résidus négatifs de StarNet. Les phases internes
qui utilisent une nouvelle référence peuvent avoir d'autres masques : cette
carte ne prétend pas représenter simultanément chaque phase de recherche.
Le contexte étiré ne sert jamais à recalculer les masques de cette carte.
Une détection indisponible est indiquée explicitement et ne produit pas de
fausses classes de structures. Les essais synthétiques vérifient l'identité
avec les masques du score, les quantiles confondus, la grille, le PNG et la
conservation des sources (`tests/test_starless_zones.py`).
