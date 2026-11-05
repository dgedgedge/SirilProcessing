# Échantillonnage des régions diffuses

[postProcess](README.md) › [Contrôles de qualité](quality.md)

## 1. Objectif et périmètre

`DiffuseSignalProcessor` compare le signal diffus à l'image qui entre dans le
traitement évalué. Les régions viennent des composantes connexes du masque
d'objets de cette référence ; les régions de moins de 64 pixels sont exclues.
Ce ne sont ni les tuiles de bruit ni nécessairement des objets astronomiques
indépendants. Une galaxie peut être fragmentée par la segmentation.

Le contrôle évalue par défaut au plus **256 régions**, sélectionnées avant les
mesures photométriques et les filtrages multiscales. Les candidats utilisent les
mêmes régions pour une même référence et un même plafond. Aucune sélection ne
dépend du résultat après restauration, de sa perte de flux ou de son verdict.

## 2. Option et compatibilité

| Option | Défaut | Signification |
| --- | ---: | --- |
| `--quality-max-diffuse-regions` | 256 | Nombre maximal de régions évaluées ; **0 = toutes** |

L'option appartient à `DiffuseSignalProcessor`. Sa déclaration remonte par
`parameter_children` et `add_arguments` ; la priorité reste CLI > JSON > défaut.
La référence conserve son réglage pour les méthodes historiques `signal` et
`evaluate`, sans dépendre d'une modification ultérieure de la configuration.
Un appel explicite au processeur peut fournir une surcharge ponctuelle.
Un plafond négatif, booléen ou non entier est refusé. Un plafond non nul trop
petit pour représenter toutes les classes de taille présentes est refusé avec
le nombre minimal de classes, plutôt que d'en supprimer silencieusement.

Exemples d'options à ajouter à une commande postProcess habituelle :

```text
--quality-max-diffuse-regions 128
--quality-max-diffuse-regions 0
```

Cette option ne change pas `quality-max-noise-tiles`, ni le nombre d'étoiles
mesurées. Le choix CPU/GPU des filtrages est décrit dans la section 6.

## 3. Classes de taille et répartition spatiale

`lib.diffuse_regions.select_diffuse_regions(bounds, sizes, shape, maximum)`
retourne des descripteurs immuables `DiffuseRegion` et un audit JSON.
L'aire est le nombre de pixels du masque de l'objet, avant dilatation et
exclusion stellaire. Les classes sont logarithmiques, par octaves d'aire :
64–127, 128–255, 256–511 pixels, etc. Il ne s'agit pas de la taille de la
fenêtre incluant les marges.

Chaque classe occupée reçoit au moins un représentant. Si le budget le permet,
chaque quadrant occupé de chaque classe reçoit un représentant. Le reste est
réparti selon des quotas proportionnels à la racine de l'effectif disponible,
sans dépasser cet effectif. L'allocation successive privilégie le rapport
`racine(effectif) / quota courant`. Les tailles rares pèsent ainsi davantage
que dans un tirage uniforme de toutes les régions.

Dans chaque classe :

1. La région de plus grande aire est conservée.
2. Les quadrants manquants sont couverts lorsque le quota le permet, en
   privilégiant leur plus grande région.
3. Les places restantes maximisent la distance minimale aux centres déjà
   sélectionnés. Les coordonnées sont relatives à la hauteur et à la largeur
   du champ pour tenir compte des mosaïques allongées.

La position utilisée est le centre de la boîte englobante, pas le centre de
flux. Les ex aequo favorisent l'aire puis l'identifiant de région ; il n'y a
pas de tirage aléatoire. Le résultat final retrouve l'ordre des identifiants.
Avec un plafond suffisant ou zéro, toutes les régions admissibles sont gardées.

## 4. Mesures et audit

Les ouvertures, marges, exclusions, estimations de fond, bootstrap et filtres
multiscales restent ceux du [contrôle du signal diffus](quality.md).
Les calculs de flux avant/après et les seuils sont inchangés pour chaque région
retenue. Une région non retenue n'est pas évaluée ni comptée comme PASS.
Le verdict agrégé porte uniquement sur les régions évaluées : un échantillon
sans perte détectée ne prouve pas l'absence de perte dans les régions omises.

Le champ `diffuse.sampling` du rapport contient :

- le plafond, les effectifs disponibles, évalués et omis, et le mode exhaustif ;
- les limites d'aire, effectifs et quadrants disponibles/évalués de chaque classe ;
- les identifiants, aires et fenêtres des régions retenues ;
- la méthode déterministe utilisée.

Les objets et canaux évalués restent détaillés dans `diffuse.per_region`.
Le chronométrage `Qualité — signal diffus` comprend la sélection et les mesures.
La dépendance `diffuse_regions.py` est incluse dans l'empreinte des chaînes de
restauration afin de ne pas réutiliser un résultat calculé avec une autre sélection.

## 5. Vérifications et limites

`test_diffuse_regions.py` couvre des populations de tailles très déséquilibrées,
les quadrants, les grandes régions rares, les coordonnées identiques, les champs
vides, le mode exhaustif, les options récursives et la priorité CLI/JSON.
Un essai contrôlé compare les mesures retenues au calcul exhaustif et vérifie
que changer les intensités du candidat ne change pas la sélection.

Un plafond très réduit peut ne pas représenter tous les quadrants de chaque
classe ; l'audit le montre. Le choix par aire et position ne garantit pas la
représentativité de la luminosité, du contraste ou du type physique d'objet.
Les ouvertures de régions distinctes peuvent se chevaucher. Une grande région
coûte davantage qu'une petite : le gain de temps n'est pas nécessairement
proportionnel au nombre de régions supprimées. Utiliser le mode exhaustif
pour confronter ponctuellement l'échantillon à une référence complète.

Un essai réel sur M31 a retenu 256 régions sur 3 689, représentant les 11 classes
de taille présentes et les quadrants disponibles dans chacune. Le contrôle
diffus a pris 46 s, contre 351 s dans le rapport historique exhaustif. Ces temps
proviennent de deux exécutions sous des charges différentes, pas d'un benchmark
contrôlé côte à côte. Les empreintes de la référence et des masques ont été
vérifiées identiques ; les mesures des régions communes sont identiques après
normalisation JSON. Le verdict agrégé de cet exemple reste FAIL.

## 6. Filtrages multiscales sur GPU

`DiffuseFilterBackend` dans `lib.diffuse_filters`, construit sur le
[moteur PyTorch commun](torch-compute.md), exécute les convolutions des
fenêtres retenues. Le choix suit la politique commune `--compute-backend auto`
(par défaut) ou `cpu`. La surcharge locale appartient à `DiffuseSignalProcessor` :

| Option | Défaut | Effet |
| --- | --- | --- |
| `--quality-diffuse-backend` | non définie | Suit `compute-backend` ; surcharge possible `auto`, `cpu`, `cuda` ou `mps` |

`--quality-diffuse-backend cuda` exige CUDA et `mps` exige Metal sur Mac.
Avec `auto`, PyTorch privilégie CUDA puis MPS ; le contrôle initial choisit
SciPy CPU si les opérations ou la mémoire nécessaires sont indisponibles.
Une erreur de calcul après initialisation se propage sans reprise CPU.
PyTorch est déjà installé pour les moteurs de restauration ; aucune dépendance
supplémentaire n'est nécessaire pour ces filtres.

La segmentation, l'échantillonnage, les ouvertures et couronnes, les flux,
les estimations de fond et le bootstrap restent sur CPU. Chaque fenêtre
admissible est transférée sur le GPU. Les deux plans de structure et de résidu
sont filtrés par convolutions PyTorch séparables, sans mélange entre plans.
Si leurs types diffèrent, ils sont filtrés séparément pour conserver leur
précision. Le repliement des indices reproduit le mode `reflect` de SciPy,
qui inclut les pixels de bord, contrairement au padding `reflect` natif PyTorch.
La troncature vaut quatre écarts types. La médiane paire moyenne les deux
valeurs centrales, comme NumPy. TF32 est désactivé pendant les convolutions
CUDA pour préserver la précision. Seuls les effectifs et amplitudes scalaires
reviennent au CPU.

MPS ne prend pas en charge float64 : en présence de tels plans, `auto` choisit
CPU avant les fenêtres ; `mps` imposé produit une erreur explicite. Il n'y a
pas de conversion silencieuse vers float32.

Les fenêtres restent successives. Le contrôle mémoire utilise une estimation
de 128 octets par pixel de la plus grande fenêtre et une limite de 80 % de
la mémoire disponible. L'image entière n'est pas transférée. L'audit
`diffuse.backend` indique le moteur effectif, l'implémentation, la politique
demandée, la raison du choix, la mémoire estimée, la version PyTorch et le
nombre de fenêtres/échelles filtrées. La préparation des masques reste distincte.

Les définitions de flux, seuils et verdicts sont identiques aux calculs CPU.
Les tests comparent les amplitudes avec une tolérance de
`3e-9 + 3e-5 × abs(amplitude CPU)` et vérifient les décisions : cette tolérance
est une vérification numérique, pas un nouveau seuil scientifique.
Les bords, petits axes et médianes sont aussi vérifiés avec PyTorch sur CPU.
Le test matériel MPS est ignoré en l'absence de Mac compatible ; la validation
sur un vrai Mac reste à effectuer lorsque les vérifications sont faites sous Linux.

Références : [convolution PyTorch](https://docs.pytorch.org/docs/stable/generated/torch.nn.functional.conv2d.html),
[backend MPS](https://docs.pytorch.org/docs/stable/notes/mps.html)
et [filtres SciPy](https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.gaussian_filter.html).

Un essai réel M31 avec PyTorch 2.10.0/CUDA a comparé les deux moteurs dans
le même processus, sur les mêmes 256 régions et la même référence préparée :
**5,36 s avec PyTorch CUDA contre 47,96 s avec SciPy CPU**, soit environ ×9.
Les 377 fenêtres région/canal représentent 1 885 évaluations d'échelle.
Aucun verdict régional ou agrégé n'a changé ; l'écart maximal d'amplitude
multiscale est `2,04e-10` en unités de pixels normalisés. Les autres mesures,
flux et intervalles du bootstrap sont identiques. Ce candidat reste FAIL
dans les deux modes. Ces temps excluent la préparation des masques et dépendent
du matériel et de sa charge (d'autres traitements étaient en cours pendant
cet essai) ; ils ne garantissent pas ce gain sur d'autres
champs ni sur la restauration complète.
