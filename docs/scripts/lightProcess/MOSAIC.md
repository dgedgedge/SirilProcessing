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

Avant d'appeler `create_mosaic()`, `post_process()` appelle
`gradient_processor.process_mosaic()` avec tous les panneaux. Les corrections
sont ajustées **conjointement**, canal par canal, sur les différences de signal
aux mêmes positions du ciel dans leurs recouvrements WCS. La lumière commune
(galaxies, nébuleuses) n'est donc pas ajustée comme un fond indépendant.
Les petites régions comparées sont lissées pour réduire les écarts de PSF ;
les différences aberrantes sont rejetées au cours de la résolution.

Le panneau ayant le plus de points de recouvrement sert de référence inchangée.
Un gradient absolu commun à tous les panneaux reste indéterminé et n'est pas
retiré. Le modèle est additif : il ne corrige pas un écart de gain photométrique.
Les panneaux doivent avoir des WCS valides et former un ensemble connecté.
Sinon le traitement échoue explicitement, sans revenir à une correction indépendante.

`--gradient-method rbf` utilise une base commune affine et RBF thin-plate
(4 × 4 centres) avec pénalisation de la courbure réglée par
`--gradient-smoothing`. En mode `polynomial`, le degré est celui de
`--gradient-min-order` (pas de sélection sur le signal de la galaxie).
`--gradient-samples-per-line` contrôle la densité de la grille de comparaison,
suréchantillonnée pour couvrir les recouvrements étroits. La tolérance de grille
et la conservation des cellules brillantes du traitement autonome ne sont pas
utilisées ici : on rejette les différences aberrantes entre panneaux, pas les
régions brillantes communes du ciel.

Chaque panneau conserve ses fichiers `<index>_gradient_…` dans
`<work_dir>/<mosaic_name>/` : FITS corrigé, rapport `<index>_gradient.json` et
image des points de recouvrement si demandée. `joint_gradient.json` contient
le panneau de référence, les recouvrements, et les écarts RMS avant/après sur
les échantillons retenus. Les sources restent intactes. Ces résultats sont
conservés après nettoyage et transmis à l'assemblage via `output_image`.
L'appel direct historique à `create_mosaic()` effectue seulement l'assemblage.

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
