# Processeur complet et enchaînement depuis lightProcess

[postProcess](README.md) › Pipeline commun

## Responsabilité et interface

[`PostProcessingProcessor`](../../../lib/postprocessing.py) hérite de
[`processor`](../../../lib/processor.py). Il porte l’exécution complète qui
était dans `bin/postProcess.py` : séquence scientifique, journal par image,
diagnostics de qualité et du gradient, puis exports FITS et
JPEG. Les deux scripts appellent cette même classe en mémoire.

La [protection énergétique](power-guard.md) appartient aux scripts de lancement.
`postProcess.py` détient un contexte pour le traitement complet de son image ;
`lightProcess.py` en détient un seul pour la calibration, le stacking, la
mosaïque éventuelle et les post-traitements avec leurs exports. L’appel direct
au processeur ne demande aucun profil système ni inhibition de veille.

`_PostProcessorSequence` reste l’enfant qui orchestre les traitements et leurs
moteurs. Les algorithmes ne sont pas copiés dans les points d’entrée.
Voir [la séquence et la liaison des paramètres](../lightProcess/treatments/postprocess/SEQUENCE_PROCESSOR.md).

Le contrat est `post_process(input_path, output_path, args=None)` :

- `input_path` est le FITS linéaire du stack ou de la mosaïque ; les
  [prérequis AutoGHS](auto-ghs.md#prérequis-et-entrée) s’appliquent si cette étape
  reste active, notamment l’exigence RGB et Siril 1.4/StarNet V2.
- `output_path` est un rapport JSON, jamais un répertoire. Son nom sans
  extension détermine le dossier des étapes et les exports `.fits`/`.jpg`.
- `args` surcharge les paramètres uniquement pour cet appel. Sans surcharge,
  les valeurs liées ou mémorisées par `set_from_args` sont utilisées.
- Le résultat retourné contient `status: SUCCESS`, `output_image`,
  `report_path`, `steps_dir`, `quality_preview_path` et `treatments` ;
  `output_jpeg` est ajouté si AutoGHS a fourni un JPEG.

Le JSON sur disque conserve son schéma historique : une clé par traitement,
comme `gradient`, `photometry`, `restoration` et `autoghs`. Il correspond au
contenu de `treatments` dans l’enveloppe retournée. Le consommateur suivant
reçoit le FITS exporté grâce à `output_image`.

`report_path_for(image, output_arg=None)` fournit le nom standard
`<image>_postprocess.json` à côté de l’image, ou dans le répertoire demandé.
Comme pour le CLI historique, un ancien chemin de rapport passé en second
argument sélectionne uniquement son parent.

## Bandeaux du journal

Le début du post-traitement et chaque étape active sont annoncés en INFO avec
un titre entre deux lignes de `=`. Le même format est écrit dans la console et
dans le journal propre à l’image, depuis postProcess comme depuis lightProcess :

```text
============================================================
Début du post-traitement : M31_mosaic.fits
============================================================
…
============================================================
Post-traitement — étape 01 : Correction du gradient
============================================================
```

Les étapes standard suivantes sont `02 : Étalonnage photométrique des couleurs`,
`03 : Comparaison des chaînes de restauration` et
`05 : Rendu final AutoGHS et étoiles`. Les numéros correspondent aux rapports,
même si une étape est désactivée. Sans comparaison des chaînes, la restauration
se décline en `03 : Déconvolution` puis `04 : Débruitage`.
Une étape désactivée n’affiche pas de bandeau. Les réglages, chemins d’entrée
et diagnostics restent sous le titre de l’étape concernée.

Les [sous-étapes de restauration](restoration.md#sous-étapes-dans-les-journaux)
utilisent des lignes de `-` : préparation, calculs des moteurs, contrôles finaux,
puis classement et publication. Cette présentation les distingue des grandes
étapes encadrées par `=`.

## Paramètres et calcul identiques

Les deux scripts enregistrent `PostProcessingProcessor` avec `Config.register`
avant le parsing, puis lient les paramètres avant l’instanciation. La classe
déclare `_PostProcessorSequence` par `parameter_children` ; la récursion conserve
les instantanés des étapes et des moteurs créés tardivement.

Les options de correction du gradient, photométrie, restauration, étoiles,
AutoGHS et calcul CPU/GPU ont les mêmes noms, défauts et règles de priorité :
**CLI > JSON sauvegardé > défaut local**. AutoGHS reste actif par défaut et
`--compute-backend` reste en `auto`. Le gradient de mosaïque et celui du
post-traitement partagent leurs réglages `--gradient-*`, déclarés une seule fois.
`--no-mosaic-gradient` et `--disable-gradient` contrôlent séparément leurs
activations.

Les scripts restent responsables de `--config`, de la console et de `-S`.
`prepare_config_save(args)` résout le chemin StarNet découvert avant une
sauvegarde demandée avec AutoGHS actif ; `Config.save_requested` l’enregistre
avec les autres options. Dans lightProcess, cette préparation n’est appelée
que si le post-traitement est activé. Une erreur de résolution ou de sauvegarde
arrête le lancement avant les calculs. Le processeur ne parse pas `sys.argv`,
ne sauvegarde pas la configuration et ne change pas celle de son appelant.

## Activation dans lightProcess

```bash
# Calibration, stack, puis même post-traitement que postProcess.sh
bin/lightProcess.sh /chemin/M33 --postprocess

# Même enchaînement avec réglages de rendu mémorisés explicitement
bin/lightProcess.sh /chemin/M33 --postprocess \
  --compute-backend auto --ghs-chroma enhanced --ghs-dynamic-target 0.8 -S

# Assemblage linéaire des panneaux, puis rendu de la mosaïque finale
bin/lightProcess.sh /chemin/nord /chemin/sud \
  --mosaic --mosaic-name M33 --postprocess

# Désactiver un enchaînement enregistré dans la configuration
bin/lightProcess.sh /chemin/M33 --no-postprocess
```

`--postprocess` / `--no-postprocess` est propre à lightProcess. Le défaut est
**activé** : une commande habituelle enchaîne sans ajouter d’option.
`--no-postprocess` désactive cet enchaînement et `--postprocess` l’impose,
notamment face à une désactivation enregistrée dans le JSON.
`-S` mémorise le booléen `enable_postprocess`. Les paramètres des
traitements suivent le même fichier partagé que le lancement autonome.
`postProcess.py` invoque toujours le processeur demandé, indépendamment de ce
booléen d’enchaînement.

Sans mosaïque, chaque stack final disponible est traité une fois, y compris
un stack réutilisé depuis le cache. Avec `--mosaic`, seule l’image finale
validée par l’assemblage est traitée : les panneaux d’entrée restent linéaires.
Si la mosaïque échoue, aucun rendu des panneaux n’est lancé à sa place.

`--dry-run` annonce le post-traitement sans appeler le processeur ni les moteurs.
Comme pour les autres options, une sauvegarde explicitement demandée avec `-S`
reste possible en simulation.

## Produits, erreurs et limites

Pour `<sortie>/M33/M33_combined.fits`, l’enchaînement crée à côté :

```text
M33_combined_postprocess.fits
M33_combined_postprocess.jpg
M33_combined_postprocess.json
M33_combined_postprocess/
  00_M33_combined_postProcess.log
  01_gradient.json
  02_photometry.json
  03_restoration.json
  05_autoghs.json
  05_autoghs_work/
```

Cet extrait suppose les traitements actifs et réussis. Le JPEG est publié
seulement si AutoGHS l’a produit. Les détails des diagnostics, étapes
intermédiaires et noms des variantes sont dans [les sorties](README.md#rapports-images-et-journaux)
et [les exports de rendu](render-exports.md). La mosaïque suit la même convention,
en ajoutant `_postprocess` au nom de son FITS final réel.

Les FITS de calibration, les stacks et la mosaïque linéaire sont conservés.
L’activation du post-traitement ne change pas leurs critères de cache ; elle
relance le post-traitement demandé avec ses réglages actuels. Les dossiers
déterministes des étapes sont réinitialisés suivant leurs règles habituelles.

Une erreur du processeur est journalisée puis propagée après fermeture de son
journal. Le script libère sa protection énergétique à la sortie de son contexte,
y compris après une erreur ou une interruption. Le CLI autonome traduit l’erreur en code 1
(130 pour une interruption clavier). Dans lightProcess, un échec ou une sortie
finale absente entraîne le code 1 ; les autres stacks disponibles peuvent
encore être traités. Les sources et produits partiels restent disponibles.

Les tests `test_postprocessing.py` vérifient les paramètres, la conservation du
contrat JSON, les exports communs, la fermeture du journal et la protection
détenue par le CLI autonome. Les tests
`test_session_pipeline.py` vérifient la sélection des produits, le cache,
la simulation, les échecs et le contexte unique de protection du script complet.
Les tests avec moteurs simulés ne remplacent pas
une validation astrophotographique complète sur plusieurs acquisitions.
