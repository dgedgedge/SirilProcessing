# Analyser les guide logs KStars / Ekos

[Documentation des scripts](../README.md)

`bin/guideLogAnalyze.sh` ouvre une fenêtre Tkinter/Matplotlib pour les journaux
de guidage `.txt` produits par le guideur interne de KStars au format PHD2.
Il est indépendant de Siril et du visualiseur de fichiers `.analyze`.

```bash
bin/guideLogAnalyze.sh ~/Téléchargements/guidelogs/
```

Sans argument, le programme charge les fichiers `*.txt` du répertoire KStars
`~/.local/share/kstars/guidelogs/`. Si ce dossier est absent, la variante
`~/.local/share/kstars/guideLogs/` est recherchée. Si aucun journal n'est trouvé,
un message indique le chemin et la fenêtre permet d'ajouter des fichiers ou un
dossier. En mode `--html`, l'absence de journaux est une erreur.
Un chemin explicite remplace cette entrée par défaut.

Le chemin par défaut figure également dans l'aide :

```bash
bin/guideLogAnalyze.sh --help
```
Les dossiers sont parcourus sans récursion pour les fichiers `*.txt`.
Les chemins et motifs glob sont acceptés ; les fichiers sont dédupliqués.
Un fichier non reconnu est signalé. En ligne de commande, il interrompt le
chargement ; depuis la fenêtre, les autres fichiers restent disponibles.

Le lanceur utilise `.venv` à la racine du projet (ou `VENV_DIR`), le crée au
besoin et installe les dépendances de `requirements-analyze.txt`. Cette étape
peut nécessiter Internet. Tkinter est une dépendance système (`python3-tk`
sous Debian/Ubuntu). Python 3.10 ou plus récent est nécessaire.

Pour utiliser directement les dépendances déjà installées :

```bash
.venv/bin/python bin/guideLogAnalyze.py ~/Téléchargements/guidelogs/
```

## Explorer une nuit

Chaque début de guidage ou de calibration devient une entrée distincte.
Les sections sans mesures restent visibles avec leur compteur à zéro.
Elles ne prouvent pas que le guidage était arrêté : aucune mesure n'y est
enregistrée. Les sections ne sont jamais reliées par une courbe.

- Sélectionner une ou plusieurs sections avec Ctrl/clic, ou tout sélectionner.
- Choisir les erreurs AD/DEC, impulsions, RMS glissant et SNR à afficher.
- Passer des secondes d'arc aux pixels ; le temps relatif aligne les débuts
  des séquences pour les comparer.
- Déplacer les deux poignées du curseur temporel, ou utiliser le zoom et le
  déplacement de la barre Matplotlib. Les panneaux temporels sont synchronisés.
- Le zoom recalcule les statistiques et le nuage de dispersion sur la période
  affichée, ainsi que l'échelle verticale des mesures visibles.
- Les calibrations restent complètes : trajectoires dx/dy en pixels,
  séparées par direction West/East/North/South.
- Les détails contiennent les en-têtes, les avertissements et les événements
  INFO. « Copier les noms » copie les chemins des fichiers sélectionnés.

La barre Matplotlib permet également d'enregistrer les graphiques en image.

## Mesures et statistiques

Les distances du format PHD2 sont en **pixels de la caméra de guidage**.
L'affichage en secondes d'arc multiplie les erreurs `RARawDistance` et
`DECRawDistance` par le `Pixel scale` de chaque section. Sans échelle valide,
les distances angulaires restent indisponibles ; choisir `px` pour les voir.
Les heures sont celles inscrites dans le journal, sans conversion de fuseau.

Seules les lignes `Mount` avec `ErrorCode=0` et deux erreurs finies sont retenues
pour les statistiques. Les lignes `DROP`, les erreurs et les lignes AO sont
exclues ; elles ne sont jamais traitées comme une erreur nulle. Les croix rouges
à zéro sont des repères de rejet, pas des mesures de position. Les lignes CSV
incomplètes et les temps invalides ou décroissants sont signalés et ignorés.

Les statistiques sont calculées par séquence, sur les mesures disponibles :

- RMS AD : `sqrt(mean(AD²))`, RMS DEC : `sqrt(mean(DEC²))` ;
- RMS total : `sqrt(mean(AD² + DEC²))` ;
- biais AD/DEC : moyenne de l'erreur sur chaque axe ;
- rayon P95 : percentile 95 de `sqrt(AD² + DEC²)`.

Ce RMS mesure l'écart à zéro, **pas l'écart-type autour de la moyenne**.
Le RMS glissant utilise jusqu'aux 40 dernières mesures consécutives valides,
avec remise à zéro après un rejet ou une interruption supérieure à 30 secondes.
Les courbes sont également interrompues aux rejets et aux longues pauses.
Les mesures pendant le dithering ne sont pas automatiquement retirées.

Les impulsions sont en millisecondes : E/N positives, W/S négatives.
Leur signe décrit la direction de commande, pas une conversion en déplacement.
Un message INFO sans horodatage exact est associé à la dernière mesure connue
et présenté comme tel ; aucune heure précise n'est inventée.

Ces vues aident à examiner le suivi et les corrections. Elles ne fournissent
pas un diagnostic mécanique automatique de la monture ou une mesure de son
erreur périodique sans guidage.

## Export HTML autonome

Le bouton « Exporter HTML » exporte les sections sélectionnées **complètes**,
indépendamment du zoom. Le rapport contient Plotly et s'ouvre sans Internet.
Un menu sélectionne une section ou toutes ; les légendes permettent de masquer
les courbes et le zoom temporel est partagé entre les cinq panneaux de gauche.
Les statistiques écrites dans les détails portent sur les sections complètes,
elles ne changent pas avec le zoom HTML.

```bash
.venv/bin/python bin/guideLogAnalyze.py ~/Téléchargements/guidelogs/ \
  --html -o out/guide-log-analyze.html

.venv/bin/python bin/guideLogAnalyze.py nuit1.txt nuit2.txt \
  --html --relative --unit px -o out/comparaison-guidage.html
```

Les sorties doivent avoir l'extension `.html` ou `.htm`. Les sources sont lues
uniquement et un export vers un fichier source est refusé.

Référence du format : [PHD2 Guide Log Format](https://github.com/OpenPHDGuiding/phd2/wiki/PHD2GuideLog).
Emplacement des journaux : [documentation du guideur Ekos](https://kstars-docs.kde.org/en/user_manual/ekos-guide.html#guiding-logs).

## État du suivi et pertes de mesure

Le panneau temporel « État du suivi », activé par défaut, partage le zoom des
courbes. Chaque point correspond à une mesure enregistrée : vert pour une
mesure Mount valide, rouge pour DROP ou un code d’erreur Mount non nul, gris
pour une mesure indéterminée (valeurs absentes/invalides ou autre dispositif).
Dans le HTML, le survol affiche le numéro de trame et le code d’erreur.
Aucune ligne ne prolonge un état à travers une pause ou une section vide.

Les guide logs fournis ne contiennent pas le nombre d’étoiles de guidage.
`StarMass` est une mesure du signal stellaire, pas un comptage. Ce panneau
permet de repérer les pertes de mesures et leur coïncidence avec les variations
du SNR ou des erreurs AD/DEC ; un rejet ne prouve pas à lui seul une perte
physique de suivi de la monture, ni une disparition de toutes les étoiles.

## Calibration associée aux guidages

Sélectionner un guidage affiche aussi la dernière calibration qui le précède
**dans le même fichier**, même si la calibration n'est pas sélectionnée.
Sa trajectoire complète et ses informations (date, angles, vitesses et autres
métadonnées enregistrées) restent disponibles jusqu'à la calibration suivante.
L'association s'applique aussi aux sections de guidage vides et à l'export HTML,
y compris au menu de sélection du rapport. Une calibration partagée par plusieurs
guidages n'est tracée qu'une fois.

Si aucune calibration ne précède le guidage, son absence est indiquée ; aucune
calibration d'un autre fichier n'est supposée applicable. Il s'agit de la dernière
calibration **enregistrée**, sans présumer qu'une tentative incomplète a réussi :
les messages du journal restent affichés pour en vérifier le résultat.

Le lanceur utilise Python et pip du système pour créer le venv sans pip et
mettre à jour ses dépendances avant chaque exécution. Il lance ensuite le
Python du venv. La sélection de l’environnement, les prérequis et l’exécution
sans mise à jour sont décrits dans [Installation](../../INSTALLATION.md).
