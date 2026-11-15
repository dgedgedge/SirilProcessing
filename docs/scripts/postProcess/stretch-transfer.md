# 1. Transformation d’étirement cumulée

[Manuel](README.md) · [Exports](render-exports.md)

## 1.1. Lecture des graphiques

Chaque PNG de `histogram_trials/` des branches `03_ghs_starless/` et
`03_ghs_stars/` comporte un panneau du **facteur multiplicatif cumulé**,
en ordonnée linéaire.

Pour une intensité initiale x > 0, le gain est **G(x) = F(x) / x**, où F est
la transformation complète depuis l’entrée linéaire de cette branche, après
séparation et compensation éventuelle, jusqu’à l’essai affiché. L’abscisse
reste linéaire et calée sur les minimum et maximum RGB réels de cette entrée,
même si sa dynamique est très faible. La ligne pointillée à ×1 représente
l’identité. Une entrée constante produit un point avec une marge d’affichage.

Les intensités écrêtées à zéro ont un gain nul, représenté à zéro.
À x = 0, le rapport est indéfini et masqué.

Le panneau représente la composition sur les gris neutres **R=G=B**.
Le mélange RGBBlend et les limitations de gamut dépendent du triplet RGB :
une seule courbe scalaire ne reproduit donc pas tous les pixels colorés.
Pour ceux-ci, utiliser la recette RGB exportée avec le graphique.

Avec la [sélection stellaire](stellar-selection.md), le panneau est
l’enveloppe neutre des pics sélectionnés (poids spatial égal à 1), explicitement
identifiée comme telle. Le JSON indique
`scalar_role: selected_star_neutral_envelope`. La recette RGB conserve les
coordonnées de sélection, sans redétection ; elle impose la géométrie native.
Cette enveloppe ne représente pas une loi ponctuelle exacte pour toute l’image.

## 1.2. Composition depuis l’origine

`lib/stretch_transfer.py` sépare la reconstruction du chemin
(`TrialTransformResolver`) de son application (`CumulativeStretchTransform`).
Pour des opérations successives f₁, …, fₙ, la transformation affichée est
F(x) = fₙ(…f₂(f₁(x))…). Elle inclut les recalages de point noir avant et
après GHS, les GHS, la CDF fixe d’égalisation éventuelle, les gains de dynamique
et la chroma. Pour les étoiles, elle compose la courbe stellaire et sa table
fixe de quantiles éventuelle, puis la porte douce de bruit au seuil retenu
par le moteur stellaire.

Chaque candidat repart de son propre parent, y compris les essais rejetés
et les chemins alternatifs. La recette utilise les paramètres enregistrés :
aucun histogramme, quantile, point noir ou choix de candidat n’est recalculé
sur l’image fournie lors de sa réapplication. Les deux branches gardent leurs
origines et leurs recettes indépendantes. La recomposition est un traitement
ultérieur, absent de ces courbes.

## 1.3. Précision et fichiers exportés

À côté de `histogram_013.png`, les fichiers suivants sont disponibles dans
la galerie HTML et le manifeste :

- `transform_013.json` : domaine initial, opérations RGB ordonnées, paramètres,
  tables éventuelles et bilan de précision ;
- `transform_013.npz` : tableaux NumPy `input`, `output` et `gain` en float64.
  `output` garde F(x), `gain` contient F(x)/x et vaut NaN à x=0.

Le calcul de la courbe conserve ses intermédiaires en float64. Un maillage
initial de 2 049 points est affiné aux quarts et au milieu de chaque intervalle
jusqu’à une erreur absolue observée d’interpolation linéaire de 10⁻⁷.
Le maillage adaptatif est limité à 262 145 points et 24 raffinements. La résolution float64
peut également empêcher un raffinement. Le bilan indique explicitement si
le seuil a été atteint. C’est un contrôle aux sondes, pas une borne analytique
sur toute la courbe. Ce seuil porte sur F(x), tandis que le gain est calculé
par division float64 à chaque point échantillonné. L’échantillonnage ne dépend pas du nombre de classes de
l’histogramme et ne modifie aucun choix d’étirement.

L’application RGB normale reproduit les étapes et leurs intermédiaires
float32 du moteur CPU ; les arrondis CUDA peuvent différer. La courbe float64
représente la composition continue, sans ses marches de quantification.
Les intermédiaires de cette rampe sont bornés à [0,1], car un gain déterminé
sur des pixels colorés peut dépasser le gamut sur des gris neutres.
Les recettes sont propres au traitement enregistré : elles ne réoptimisent
pas les réglages pour une autre image.

Exemple de lecture des échantillons :

```python
import numpy as np

with np.load('histogram_trials/transform_013.npz') as curve:
    input_levels = curve['input']
    output_levels = curve['output']
    multiplicative_gain = curve['gain']
```

Pour rejouer la recette sur un tableau RGB normalisé de forme `(3, H, W)` :

```python
import json
from pathlib import Path
from lib.stretch_transfer import CumulativeStretchTransform

recipe = json.loads(Path('histogram_trials/transform_013.json').read_text())
transform = CumulativeStretchTransform(tuple(recipe['domain']), recipe['operations'])
# destination_rgb = transform.apply_rgb(original_rgb)
```

Les anciens rapports sans domaine initial ne permettent pas d’ajouter ce
panneau rétroactivement. Une nouvelle exécution produit les informations
nécessaires. Le calcul emploie NumPy, sans dépendance supplémentaire ; le coût
croît avec le nombre de candidats, d’opérations et de points à affiner.
