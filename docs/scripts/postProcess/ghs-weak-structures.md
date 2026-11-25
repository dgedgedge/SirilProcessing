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

Le seuil d'extension faible est réglable avec `--ghs-weak-sigma`, entre **1 et
3 inclus**, avec **1,1 par défaut**. Par exemple, `--ghs-weak-sigma 1.5` réduit
l'extension par rapport au défaut, tandis que `--ghs-weak-sigma 1.0` l'élargit.
La ligne de commande utilise le point décimal. La clé JSON est `ghs_weak_sigma` ;
la priorité reste CLI > configuration sauvegardée > défaut, avec mémorisation
par `-S`. Les valeurs non finies, booléennes ou hors domaine sont refusées.
Le même réglage est transmis aux mesures des essais, aux validations complètes
et aux cartes de zones ; leurs légendes indiquent le seuil réellement utilisé.
Les seuils intermédiaire (3 sigma) et fort (6 sigma et >P95) restent fixes.
Les valeurs ci-dessous décrivent le réglage par défaut. Ce paramètre modifie
les régions de mesure et peut donc changer la courbe globale sélectionnée ;
il ne commande aucun étirement local.
À la borne 3, la bande faible `[seuil,3[` est vide : le repli sur le centile
inférieur du signal retenu décrit ci-dessous est alors utilisé.

La carte spatiale distingue les cellules **hors ciel et hors objets retenus**
par trois couleurs, selon la significativité multi-échelle `S` :

- gris sombre : `S < 1` (classe 2) ;
- gris moyen : `1 < S < seuil` (classe 7) ;
- jaune clair : `seuil < S` (classe 8).

Avec le défaut, le seuil affiché est **1,1**. À la borne 1, la bande moyenne
est vide. Les cellules exactement égales à 1 ou au seuil sont repérées
en blanc (classe 9), sans les attribuer à un intervalle strict. Ces diagnostics ne changent aucun masque de qualité : le ciel bleu et
les objets colorés conservent leur priorité. Sans significativité mesurable,
la légende précise « S indisponible » au lieu de lui attribuer un intervalle.
Le rapport conserve les effectifs réellement affichés dans `spatial_counts` ;
`subthreshold_count` reste le nombre total de cellules actives avec
`1 < S < seuil`, y compris celles retenues comme ciel.

Chaque mesure utilise une grille spatiale régulière d'au plus 192 pixels sur
le plus grand côté, avec les mêmes coordonnées dans référence et candidat.
Sur GPU, seule cette petite grille est transférée vers le CPU. Une instance de
`WeakStructureEvaluator` construit les régions une seule fois sur la référence,
puis conserve ses masques et les statistiques initiales pendant la phase.
Chaque candidat est mesuré aux mêmes coordonnées, sans relancer la segmentation.
La référence échantillonnée est copiée et les masques sont non modifiables ;
chaque audit retourné est une copie indépendante. La réutilisation avec une
autre grille de référence ou un autre seuil est refusée.
La signature SHA-256 de la grille de référence, ses dimensions,
les effectifs et les seuils doivent correspondre pour comparer deux mesures.
Les phases qui commencent sur une autre image possèdent leur propre référence.
Les recherches par quartiles et la passe finale gardent une instance par
référence/grille : essais CPU échantillonnés, validation sur le champ complet
et, pour les quartiles, sondes brillantes lorsqu'elles existent. Ces dernières
ne remplacent pas les mesures spatiales de qualité. Les contrôles après
recomposition réutilisent les zones du fond correspondant. Les instances sont
libérées avec la recherche ; il n'existe pas de singleton ni de cache global.
La validation complète applique la courbe à toute l'image, mais cette mesure
spécifique reste bornée à 192 cellules par côté, comme dans les essais.
L'API autonome `WeakStructureQuality.measure` accepte `evaluator=instance` ;
sans instance fournie, elle prépare un évaluateur temporaire pour cet appel.

Le détecteur utilise la luminosité du mode GHS `human` ou `even`. Il combine
une sélection de graines lumineuses et une croissance par hystérésis :

1. Une tendance gaussienne lente de sigma 12 cellules sert de fond de référence.
2. Aux échelles 1, 2 et 4, l'excès positif par rapport à cette tendance est
   divisé par le bruit filtré estimé. Le maximum de ces significativités est
   conservé par cellule. Les seuils sigma sont heuristiques : ils ne constituent
   pas une probabilité calibrée sur du bruit corrélé ou un fond structuré.
3. Les graines fortes doivent être **strictement au-dessus de P95** des
   luminosités actives de la grille et atteindre au moins **6 sigma**.
   Une graine doit contenir au moins **quatre cellules connexes**. P95 porte
   ici sur la grille, pas sur les percentiles du champ complet de l'autre mesure.
4. Les graines s'étendent d'abord dans le support **≥3 sigma**, puis dans le
   support **≥1,1 sigma**, en connexité à huit voisins. Aucun trou sous le seuil
   faible n'est franchi et aucune dilatation artificielle ne relie les objets.
5. Les composantes initiales doivent avoir au moins **16 cellules**. Les parties
   faibles sont leurs cellules entre **1,1 inclus et 3 sigma exclus**, reliées
   à une graine forte. La sélection par P25–P50 regroupant tous les objets
   n'est plus utilisée.
6. La marge d'exclusion du ciel est construite **une seule fois sur ces objets
   initiaux**, par deux dilatations à quatre voisins. Les cellules jaunes
   (`S > seuil`, hors objets et hors ciel) de cette marge servent d'**amorces**.
   La sélection se propage à huit voisins dans leur **composante jaune entière**,
   même au-delà de la marge. Le signal faible connecté à l'ensemble retenu est
   ensuite rattaché dans le support `S ≥ seuil`. Les trous sous le seuil, les
   cellules inactives et le **masque du ciel figé** arrêtent la propagation.
   Les ajouts avec `S < 3` rejoignent le vert et ses mesures de qualité ; ceux
   avec `S ≥ 3` rejoignent l'orange. Ils ne créent ni graines, ni nouvelle
   dilatation. Un autre îlot séparé par un trou reste exclu, même s'il se trouve
   dans ce qui aurait été la marge d'une zone nouvellement admise. Aucun nouveau
   seuil de surface n'est appliqué à ces ajouts. Sans objet initial, aucune
   admission n'est possible.

La contrainte est topologique, sans décroissance stricte pixel par pixel :
une extension peut contenir des fluctuations, filaments ou renforcements
secondaires. Une région faible sans connexion à un objet et sans amorce dans la marge
initiale n'entre pas dans cette mesure, mais
n'est pas déclarée bruit et n'est jamais supprimée de l'image. P95 seul ne
suffit pas à prouver un signal réel ; il est combiné avec la significativité
et la taille des graines. Ces constantes sont génériques et nécessitent une
calibration sur plusieurs types de champs, sans réglage par nom d'objet.

Le **masque de ciel bleu** est l'intersection de quatre conditions :

- luminosité de référence `Y > 10⁻⁶` : les zéros inactifs sont exclus ;
- exclusion des objets **initiaux** et de leur voisinage, obtenu par **deux
  itérations de dilatation** sur la grille d'analyse, avec la connexité en
  croix à quatre voisins ;
- proximité du fond local :
  `|G₁(Y) − G₁₂(Y)| ≤ max(2σ/√(4π), 10⁻⁷)` ;
- luminosité lissée `G₁(Y) ≤ P75(G₁(Y)[Y > 10⁻⁶])` : le quart le plus
  lumineux des cellules actives après lissage est exclu, hors égalités au seuil.

`G₁` et `G₁₂` sont les lissages gaussiens d'échelles respectives 1 et 12
cellules. `σ` est le bruit estimé avant lissage ; `σ/√(4π)` estime le bruit
après lissage à l'échelle 1. Hors effet du plancher numérique `10⁻⁷`, le
troisième critère s'écrit **`|S₁| ≤ 2`**, donc **`−2 ≤ S₁ ≤ 2`**, avec
`S₁ = (G₁(Y) − G₁₂(Y))/(σ/√(4π))`. Il ne signifie pas `S < 1`.
Ce `S₁` utilise uniquement l'échelle 1, tandis que le `S` des émissions et des
couleurs diagnostiques est le maximum des échelles 1, 2 et 4. Les critères
du ciel et des émissions ne sont donc pas complémentaires.
Le masque du ciel est figé avant l'admission et sert de barrière à la
propagation. Les ajouts peuvent dépasser la marge initiale, mais ne peuvent
jamais prendre une cellule déjà retenue comme ciel ; celui-ci reste identique.

L'option `--ghs-weak-sigma` ne modifie pas le seuil de ciel à deux sigmas.
Elle peut toutefois modifier indirectement son masque en étendant les objets
exclus et leur voisinage. Les cartes de classes de luminosité utilisent une
autre définition du bleu, `Y ≤ P25`, distincte de ce masque spatial.
Si au moins **32 cellules faibles** sont détectées, elles servent de
référence faible. Sinon, leur masque de mesure est **remplacé par les 1 % des
cellules les moins lumineuses du signal retenu**, graines et signal intermédiaire
compris. La sélection utilise la luminosité de référence avant étirement,
avec un effectif `ceil(0,01 × nombre de cellules retenues)` et un minimum d'une
cellule si un objet existe. Les égalités de luminosité sont départagées par
l'ordre spatial stable, pour ne pas sélectionner tout un plateau. Le minimum
32 ne s'applique pas une seconde fois à ce repli ; une population très petite
reste donc moins représentative. Les conditions existantes sur les effectifs
objets/ciel et sur la mesurabilité du bruit restent applicables.

Le masque de repli est calculé une seule fois avec la référence et reste fixe
pour tous les candidats. Les objets et le ciel ne changent pas. Le vert de la
carte représente le masque réellement mesuré, avec une légende spécifique
« centile inférieur du signal retenu », prioritaire sur la couleur des graines
si nécessaire. Il ne prétend pas que ces cellules satisfont la bande sigma
faible. Le rapport `faint_selection` précise `mode: lowest_percentile`, la
fraction, l'effectif sélectionné et l'effectif faible détecté d'origine.
Sans signal retenu, ou sans ciel suffisamment mesurable, le score reste
indisponible ; aucun gain n'est inventé.
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
3. La détection spatiale : rose pour les graines >P95 et ≥6 sigma, orange
   pour le signal retenu ≥3 sigma hors graines, vert pour les extensions
   retenues entre 1,1 et 3 sigma, bleu pour le ciel admissible, gris pour les
   cellules non classées et noir pour les inactives.

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
fausses classes de structures. Les faibles effectifs déclenchent le repli sur le centile inférieur ; le
statut reste indisponible si les autres régions requises ne sont pas mesurables. Les essais synthétiques vérifient l'identité
avec les masques du score, les quantiles confondus, la grille, le PNG et la
conservation des sources (`tests/test_starless_zones.py`).

Le rapport enregistre `segmentation_version: connected_hysteresis_v5`, les seuils
sigma et P95, les effectifs de graines et de support intermédiaire, ainsi que
les cellules faibles. `faint_reference_range` décrit désormais l'étendue des
luminosités lissées de ces extensions, pas des quantiles. Les comparaisons
entre méthodes de segmentation ou seuils différents sont indisponibles plutôt que de
comparer des populations incompatibles.
Les effectifs `initial_object_count`, `admitted_count`, `admission_start_count`
et `exclusion_count`
permettent d'auditer les objets initiaux, les ajouts, les amorces et la marge figée.
