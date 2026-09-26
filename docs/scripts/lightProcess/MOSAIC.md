# Mosaïque optionnelle

[Documentation](../../README.md) › [lightProcess](README.md)

`--mosaic` demande une mosaïque à la suite des traitements des sessions.
Il faut au moins deux sessions fournies en paramètre et deux résultats de
stacking disponibles pour lancer cette étape. Plusieurs sous-sessions d’une
seule session ne constituent pas plusieurs entrées de mosaïque.

```bash
./bin/lightProcess.sh /chemin/champ_nord /chemin/champ_sud --mosaic --mosaic-name champ
```

## Entrées et nom

Le script collecte **un résultat de stacking par session**, créé pendant
l’exécution ou réutilisé depuis le cache. Aucune pose calibrée individuelle
n’est ajoutée. Une session dont le stacking échoue ne contribue pas à la
mosaïque ; les autres résultats restent utilisables s’ils sont au moins deux.
Un échec de stacking ou de mosaïque est signalé par un code de retour non nul.

`--mosaic-name` fixe le nom. Sinon, le script cherche un préfixe commun aux
sessions ayant un résultat de stacking, puis un mot commun suffisamment long. Un nom automatique de moins
de trois caractères n’est pas accepté.

## Recadrage automatique et conservation de la mosaïque complète

Après l’assemblage Siril, le recadrage est **activé par défaut**, sous réserve
de la limite par panneau ci-dessous. Lorsque le recadrage est accepté, deux FITS
sont conservés dans le répertoire de la mosaïque :

- `<nom>_mosaic_uncropped.fits` : mosaïque complète, sauvegardée avant tout recadrage ;
- `<nom>_mosaic.fits` : résultat recadré, transmis aux traitements suivants.

La mosaïque complète reste disponible **sans `--keep-intermediate`**, ainsi
qu’en cas d’échec du recadrage. Le nettoyage ne supprime que les sous-dossiers
`input` et `output`. Une nouvelle exécution sous le même nom remplace ces
résultats ; il ne s’agit pas d’un archivage des versions successives.

Le recadrage cherche le **plus grand rectangle entièrement rempli**, aligné
sur les axes de l’image. Il supprime donc aussi les coins vides dus à la rotation
des panneaux, pas seulement les lignes et colonnes totalement vides du pourtour.
Cela peut retirer des parties valides des panneaux près des bords. À surface
égale, le rectangle le plus proche du centre est préféré.

**Si ce rectangle retire plus de la moitié des pixels d’au moins un panneau,
le recadrage est écarté.** La sortie `output_image` désigne alors directement
`<nom>_mosaic_uncropped.fits`, pour la suite des traitements. Une perte de
50 % exactement reste autorisée. Cette limite est vérifiée pour chaque panneau,
et non sur la surface totale de la mosaïque.

Le contrôle compte les pixels valides de chaque panneau aligné par Siril
(hors remplissage nul et valeurs non finies). Les coordonnées célestes WCS
permettent de déterminer si leur centre tombe dans le rectangle proposé.
Les zones de recouvrement comptent pour chacun des panneaux concernés.
Si les panneaux alignés ne sont pas disponibles, les panneaux fournis à
l’assemblage sont utilisés. Si le contrôle est impossible (notamment WCS absent),
la sortie reste également l’image complète.

Le rapport `crop.panel_retention` donne pour chaque panneau `total_pixels`,
`retained_pixels`, `removed_pixels` et `removed_fraction`.
En cas de refus, `crop.applied` vaut `false`, `crop.reason` vaut
`panel_loss_exceeds_half` (limite dépassée) ou `panel_coverage_unavailable`
(contrôle impossible), et `crop.proposed_bounds` décrit le rectangle refusé.
Aucun nouveau FITS recadré n’est écrit dans ce cas ; un ancien fichier recadré
d’une exécution précédente peut subsister, mais n’est pas désigné par `output_image`.


La détection repose sur le remplissage à zéro de Siril : un pixel monochrome
nul, ou RGB nul dans les trois canaux, est considéré comme vide. Un pixel non
fini (`NaN`, infini) est également exclu. Aucun seuil de luminosité n’est
appliqué : les valeurs négatives et les pixels ayant seulement un ou deux
canaux nuls restent admissibles. Cette méthode ne reconstruit pas les empreintes
géométriques des panneaux ; un véritable pixel noir exactement nul dans tous
les canaux, même à l’intérieur de l’image, sera aussi exclu du rectangle.

Les valeurs des pixels conservés ne sont pas rééchantillonnées. Les coordonnées
de référence FITS/WCS sont décalées pour préserver les coordonnées célestes.
Le rapport JSON contient `uncropped_image` et une section `crop` : dimensions
avant/après (hauteur, largeur), rectangle `bounds` (origine `x`, `y` à partir
de zéro, largeur, hauteur) et fraction de surface conservée `retained_fraction`.
Si aucune zone valide n’existe, le traitement signale un échec et conserve le FITS complet.

Pour désactiver le recadrage :

```bash
./bin/lightProcess.sh /chemin/champ_nord /chemin/champ_sud \
  --mosaic --mosaic-name champ --no-mosaic-crop
```

Dans ce cas, `output_image` désigne directement `<nom>_mosaic_uncropped.fits`.
`--mosaic-crop` le réactive explicitement. Cette préférence peut être mémorisée
avec les autres options de configuration via `-S`.

## Traitement Siril

Les fichiers existants sont préparés par liens symboliques ou copies dans le
dossier de travail de la mosaïque. Les commandes principales sont :

```text
convert mosaic_ -out=<travail_mosaique>
seqplatesolve mosaic_ -force -nocache -disto=ps_distortion
seqapplyreg mosaic_ -framing=max
stack r_mosaic_ rej 3 3 -norm=addscale -output_norm -rgb_equal -maximize -overlap_norm -feather=5 -out=<nom>_mosaic
```

Ce parcours est propre à la mosaïque. Les commandes et critères du
[stack final de session](treatments/STACKING.md) sont documentés séparément.
Le mode simulation ne lance pas la création de mosaïque. L’exécution effective
journalise les fichiers d’entrée, le résultat et les erreurs rencontrées.

## Interface commune des traitements

`Mosaic` hérite de `processor` (`lib.processor`). Elle expose `get_prefix()`
(`mosaic`), `add_arguments(parser)`, `set_from_args(args)` et
`post_process(input_path, output_path, args=None)`.
Cette dernière méthode assemble l’image courante avec les panneaux `input_files`
du constructeur, écrit un rapport JSON dans `output_path` et retourne
`output_image` pour transmettre la mosaïque au traitement suivant.

`Mosaic` crée son `gradient_processor` (`GradientExtractor`) dans son constructeur.
`Mosaic.add_arguments()` délègue aussi à `gradient_processor.add_arguments()` :
le script appelant déclare ainsi les options de la mosaïque et de son gradient
en un seul appel. `Mosaic.set_from_args()` configure également ce composant.

Le gradient des panneaux est activé par défaut. `--no-mosaic-gradient` permet
d'assembler directement les panneaux sources ; `--mosaic-gradient` l'active
explicitement. En Python, passer `mosaic_gradient=False` via `set_from_args()`.
Le rapport indique `gradient_enabled` et contient une liste `gradient_results`
vide lorsque cette étape est désactivée. Aucun rapport de gradient n'est alors créé.

Avec `lightProcess --mosaic`, le nom est résolu avant toute calibration.
La racine de travail devient `<work_dir>/<mosaic_name>/` et sert également
aux sorties : toutes les sessions, calibrations, stacks, logs et fichiers
intermédiaires sont créés directement sous cette racine. `--output` ne définit
pas une seconde racine en mode mosaïque. Sans `--mosaic-name`, le nom commun
des répertoires d'entrée est utilisé. Les stacks restent dans leurs sous-dossiers
de session ; aucune copie supplémentaire n'est créée. Le rapport les liste
dans `stacked_files`. `--keep-intermediate` contrôle toujours leur nettoyage.

## Correction du gradient des panneaux et préparation des raccords

Avant l’assemblage Siril, `gradient_processor.process_mosaic()` estime maintenant
le **fond propre à chaque panneau** et prépare les jonctions dans un même
ajustement. Le mode par défaut est `--mosaic-gradient-mode background`.
La normalisation et le fondu des recouvrements restent ensuite assurés par le
traitement de mosaïque Siril.

1. Une grille de petites régions couvre chaque panneau, au-delà des seules
   bandes de recouvrement. Une marge évite le bord du champ ; les régions
   contenant trop de pixels nuls ou non finis sont exclues. Chaque mesure est
   une médiane après rejet des pixels aberrants, pour réduire l’influence des étoiles.
2. Un plan au quantile inférieur, puis un rejet itératif des excès positifs,
   repèrent les régions brillantes et étendues. Leur masque est agrandi d’une
   cellule par défaut afin de protéger les extensions de la galaxie. En RGB,
   une source détectée dans un canal est exclue du fond dans les trois canaux.
3. Les valeurs des régions de fond retenues contraignent le modèle de chaque
   panneau. Dans les recouvrements WCS, seules les positions admissibles dans
   **les deux panneaux** fournissent les contraintes de jonction. Les différences
   aberrantes sont rejetées séparément pour chaque canal.
4. Le calcul combine les deux familles de mesures, avec un poids total comparable
   pour chaque panneau et chaque paire de recouvrement : une bande très dense
   ne domine donc pas toutes les mesures de fond.
5. Le modèle est soustrait en conservant un niveau de ciel constant commun par
   canal, pris dans les échantillons du panneau le mieux connecté. Ce panneau
   est lui aussi corrigé : il ne sert plus de référence spatiale inchangée.
   Les pixels nuls et non finis, le WCS et les extensions FITS sont préservés.

En mode `rbf`, le modèle combine une tendance quadratique et une base RBF
thin-plate à 4 × 4 centres, avec régularisation des termes RBF par
`--gradient-smoothing`. En mode `polynomial`, le degré est celui de
`--gradient-min-order` ; utiliser `2` pour une courbure quadratique. Il n’y a
pas de sélection automatique du degré à partir de la galaxie.

L’ajustement exige au moins 12 régions de fond bien réparties sur chaque panneau
et des jonctions encore reliées après rejet des points aberrants, dans chaque
canal. Si le fond visible ou les recouvrements sont insuffisants, il s’arrête
explicitement avant de produire les FITS corrigés ; il ne revient pas
silencieusement à un traitement moins protecteur.

### Réglages de sélection

| Option | Défaut | Effet |
|---|---|---|
| `--mosaic-gradient-mode` | `background` | Correction des fonds et raccords ; `relative` pour l’ancien ajustement seul |
| `--gradient-samples-per-line` | `20` | Densité de la grille de fond ; la grille de recouvrement est plus dense |
| `--gradient-grid-tolerance` | `2.0` | Seuil de détection des excès lumineux ; diminuer exclut davantage de régions |
| `--mosaic-gradient-mask-growth` | `1` | Dilatation du masque d’objets en cellules, de 0 à 5 |
| `--mosaic-gradient-border` | `0.02` | Marge exclue, fraction du petit côté du panneau, de 0 à 0.2 |
| `--mosaic-gradient-masks` | aucun | Un masque FITS 2D par panneau, dans le même ordre ; pixels non nuls ou non finis exclus |
| `--gradient-keep-all-samples` | désactivé | Désactive la détection automatique des objets ; les masques fournis et les contrôles de validité restent appliqués |
| `--gradient-measurement-image` | activé | Aperçu de la sélection ; inverse : `--no-gradient-measurement-image` |

Un masque fourni doit avoir les mêmes dimensions et la même grille de pixels
que le **stack** correspondant, après alignement et recadrage. Il ne doit pas
être construit sur une pose brute. Les chemins de ces masques sont propres à
l’exécution et ne sont pas sauvegardés avec `-S`.

Le masquage automatique reste une estimation : il ne peut pas séparer de façon
certaine une émission astronomique très diffuse d’un gradient instrumental.
Pour un objet occupant presque tout le champ, inspecter les aperçus et fournir
un masque explicite si ses extensions restent utilisées comme fond. La correction
est additive ; elle ne remplace pas une calibration des différences de gain.
Le principe de masquage des sources et des zones sans couverture est également
décrit dans la [documentation Photutils sur l’estimation du fond](https://photutils.readthedocs.io/en/2.3.0/user_guide/background.html).
L’implémentation du projet utilise NumPy/SciPy et n’ajoute pas de dépendance Photutils.

### Aperçus et rapports

Chaque panneau conserve son FITS `<index>_gradient_…_gradient_corrected.fits`,
son rapport `<index>_gradient.json` et son aperçu `…_measurement_points.png` dans
`<work_dir>/<mosaic_name>/`. L’aperçu du mode `background` distingue :

| Repère | Interprétation |
|---|---|
| **Cercle vert**, pouvant paraître carré à faible zoom | Région de **fond de ciel retenue** pour estimer le gradient du panneau. |
| **Point plein cyan**, parfois perçu comme vert | Position de **recouvrement retenue** pour comparer deux panneaux et préparer leur jonction. Ce n’est pas une seconde catégorie de mesure du fond. |
| **Croix orange ou marron** | Région **exclue automatiquement** : objet lumineux détecté ou voisinage protégé par la dilatation du masque. Elle n’est pas utilisée comme fond. |
| **Croix grise** | Région invalide, exclue par un masque fourni ou rejetée lors de l’ajustement final. |

Les repères sont dessinés après réduction de l’image pour rester visibles :
les cercles verts ont un rayon de 6 pixels et un trait de 2 pixels, les points
cyan un rayon de 2 pixels, les croix des branches de 5 pixels autour du centre
et un trait de 2 pixels. Un contour sombre améliore leur contraste sur les
étoiles et les régions lumineuses. Les cercles et croix sont dessinés au-dessus
des points de raccord lorsqu’ils se superposent.

Les symboles indiquent les **centres des mesures**, pas les dimensions des
régions analysées. Le calcul du fond utilise une petite région autour du centre ;
agrandir les repères ne change ni les échantillons ni la correction.

Pour M31, les croix orange sont attendues sur la galaxie et ses extensions,
les cercles verts dans le fond environnant, et les points cyan dans les
recouvrements. Des cercles verts sur une extension visible de la galaxie
justifient de vérifier la sélection et, si nécessaire, de fournir un masque.

En RGB, l’aperçu affiche les points retenus dans **au moins un canal**, pas
nécessairement dans les trois. Le JSON donne les coordonnées des candidats,
le rayon des régions mesurées, les masques `used_per_channel`, les nombres de
points et la dispersion du fond avant/après.

Les **grands carrés verts en quadrillage continu** correspondent à l’ancien
style d’affichage, encore utilisé dans le mode `relative`. Ce sont des repères
agrandis, pas des surfaces de mesure. Les images créées avant la correction
de l’affichage pouvaient aussi inclure les candidats rejetés ; les nouvelles
images du mode `relative` ne montrent que les points retenus dans au moins
un canal. Un ancien PNG n’est actualisé que lorsqu’il est régénéré.

Les différences RMS sur les recouvrements sont indiquées dans `joint_gradient.json`.
Ces diagnostics sont calculés sur les échantillons utilisés ; ils ne constituent
pas une mesure indépendante de préservation du flux de la galaxie.

### Comparer avec l’ancien mode

```bash
# Nouvelle correction du fond de chaque panneau, avec préparation des jonctions.
./bin/lightProcess.sh /chemin/champ_nord /chemin/champ_sud \
  --mosaic --mosaic-name champ --mosaic-gradient-mode background

# Ancien ajustement relatif, sans correction absolue du fond des panneaux.
./bin/lightProcess.sh /chemin/champ_nord /chemin/champ_sud \
  --mosaic --mosaic-name champ --mosaic-gradient-mode relative
```

Le mode `relative` ne mesure que les différences de signal aux mêmes positions
du ciel dans les recouvrements. Le panneau le mieux connecté reste inchangé,
et un gradient commun à tous les panneaux est conservé. Ses aperçus affichent
les points de recouvrement retenus dans au moins un canal après rejet.
Les options de sélection de fond, de marge et de dilatation ne s’y appliquent
pas ; les masques explicites nécessitent le mode `background`.
`--no-mosaic-gradient` désactive entièrement cette étape préalable.

Les sources restent intactes. Les résultats de gradient sont conservés après
nettoyage et transmis à l’assemblage via `output_image`. L’appel direct
historique à `create_mosaic()` effectue l’assemblage puis le recadrage,
sans correction préalable du gradient.

Les paramètres parsés `mosaic_name`, `output_dir`, `work_dir`, `siril_path` et
`siril_mode` sont consommés par la classe ; les paramètres du constructeur
restent les valeurs de repli. Seuls les sous-dossiers temporaires `input` et
`output` sont nettoyés après l’appel, même en cas d’échec Siril, sauf avec
`keep_intermediate=True`. Le répertoire nommé de la mosaïque reste présent.

`lightProcess.py` instancie `Mosaic()` et lui délègue la déclaration de
`--mosaic-name` via `add_arguments(parser, include_inputs=False)`. Après le
stacking, il configure la mosaïque avec `set_from_args(args)`, puis appelle
`post_process()` sans repasser les paramètres :
le premier stack est `input_path`, les autres sont `args.mosaic_inputs`.
Le nom calculé est transmis dans `args.mosaic_name`. Le rapport JSON est écrit
dans `<work_dir>/<nom>/<nom>_mosaic.json`, à côté du FITS final.

Pour un usage autonome ou dans une séquence de processeurs, l’option
`--mosaic-inputs FITS [FITS ...]` remplace les panneaux du constructeur pour
cet appel. Elle n’est pas exposée par `lightProcess`, qui fournit exclusivement
ses stacks de sessions. Les chemins sont dédupliqués et au moins deux images
distinctes sont nécessaires. La méthode directe `create_mosaic()` reste
disponible pour les anciens appels Python.
