# Classes de traitement des deux couches

[postProcess](README.md) › [AutoGHS](auto-ghs.md) › Branches indépendantes

## 1. Responsabilités

Le module [`stretch_branches.py`](../../../lib/stretch_branches.py) expose
un traitement distinct pour chaque couche :

| Classe | Moteur propre | Produits dans le pipeline |
| --- | --- | --- |
| `StarStretchProcessor` | `ConservativeStarRenderer` : asinh, renforcement des faibles intensités, adaptation optionnelle des pics et atténuation douce du bruit | `03_ghs_stars/` : FITS/JPEG, diagnostic stellaire, histogrammes, `stars.json`, `histogram_trials/` |
| `StarlessStretchProcessor` | `GHSOptimizer` : optimisation RANGE, présentation protégée et recherche d’histogramme | `03_ghs_starless/` : FITS/JPEG, histogrammes, `starless.json`, `histogram_trials/` |

Les deux classes héritent de `LayerStretchProcessor`, lui-même un `processor`.
La base partage la lecture d’un FITS de couche, la validation RGB, la protection
contre le remplacement de la source, l’écriture du rapport et la fermeture du
collecteur d’essais. Chaque classe concrète possède le calcul, les contrôles,
les exports et les diagnostics de son sujet. Aucun tableau de l’autre couche
n’entre dans son interface. Les fonctions de tracé et d’écriture atomique
sont partagées via `ghs_diagnostics`, sans dépendance des branches envers
l’orchestrateur.

`AutoGHSFinalRenderer` organise la séparation, la compensation des résidus,
les deux appels de traitement et la recomposition. Les rapports des deux
branches sont écrits avant la fusion ; un refus de fusion les laisse disponibles.
Les étoiles gardent leur traitement conservateur : cette organisation ne
remplace pas leur asinh par l’optimisation RANGE du fond.

## 2. Interfaces et paramètres

`post_process(input_path, output_path, args=None)` traite de manière autonome
un FITS contenant **déjà une couche isolée et linéaire**. `output_path` est le
chemin du rapport JSON ; les exports et `histogram_trials/` vont à côté.
Ce point d’entrée ne réalise ni StarNet ni recomposition.

`process(data, source, output_path, backend, args=None)` est l’interface utilisée
par l’orchestrateur. `data` est un tableau RGB normalisé de forme
`(3, hauteur, largeur)`, fini et borné dans `[0,1]`. `source` fournit le nom,
l’orientation et les métadonnées FITS. Le retour contient le tableau étiré et
le rapport déjà publié. La source et le tableau d’entrée sont préservés.

Chaque branche déclare son moteur par `parameter_children` et expose ses
options par `add_arguments`. La configuration conserve la priorité
**CLI > JSON > défauts**, les instantanés propres aux instances et les
surcharges ponctuelles. Les paramètres stellaires sont décrits dans
[Étoiles](star-rendering.md), ceux du fond dans
[l’algorithme AutoGHS](auto-ghs-algorithm.md).

Chaque appel ouvre un collecteur distinct. `--ghs-save-trial-jpegs` ajoute les
JPEG aux deux galeries ; sa valeur est également disponible dans les
instantanés des branches. Les histogrammes ne mélangent jamais les couches.
Le backend du fond est choisi avec le précontrôle mémoire existant ;
l’orchestrateur peut transmettre le backend déjà précontrôlé. Le moteur
stellaire reste NumPy. La reprise CPU de GHS appartient à `GHSOptimizer`.

## 3. Vérification et limites

Les tests des classes couvrent leurs entrées autonomes, les images RGB invalides,
la protection des sources, les instantanés de paramètres et la fermeture des
caches en cas d’erreur. Les tests du pipeline vérifient les deux appels concrets,
les sorties indépendantes et leur conservation en cas de refus de fusion.
Changer les paramètres d’une branche doit laisser les pixels et les PNG de
l’autre identiques pour une même séparation linéaire.

Les erreurs de données, moteur et écriture sont propagées. Cette indépendance
ne garantit pas la fidélité de la séparation StarNet ni l’absence de halos.
Les validations synthétiques avec StarNet simulé ne remplacent pas une nouvelle
validation complète dans Siril sur M31 ou M33.
