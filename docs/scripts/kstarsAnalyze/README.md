# Visualiser les analyses KStars / Ekos

[Documentation des scripts](../README.md)

`bin/kstarsAnalyze.sh` ouvre une interface Python native (Tkinter et Matplotlib)
pour consulter les fichiers `.analyze`, sans navigateur et sans Siril. Un export
HTML interactif autonome reste disponible. Les fichiers
sources sont seulement lus. Python 3.10 ou plus récent est nécessaire.

Le lanceur shell crée `.venv` si nécessaire, l’active, puis met à jour pip et
les dépendances de `requirements-analyze.txt` à chaque lancement (accès Internet
nécessaire pour rechercher les mises à jour). Il transmet tous les arguments au
script Python :

```bash
bin/kstarsAnalyze.sh ~/Téléchargements/analyze
```

La fenêtre propose une liste de sessions (Ctrl/clic pour en sélectionner plusieurs),
un filtre « Avec acquisitions uniquement », des cases pour choisir les mesures,
et un mode temps relatif. Le champ des noms et le bouton « Copier les noms », dans la colonne de gauche sous la liste des sessions,
permettent de récupérer les noms des fichiers sélectionnés. Cliquez sur un
intervalle pour lire ses détails. La barre d’outils propose zoom, déplacement,
réinitialisation et enregistrement d’une image.

Le curseur temporel à deux poignées, au-dessus des graphiques, règle le début
(poignée gauche) et la fin (poignée droite) de la zone affichée. Les panneaux visibles
zooment ensemble. Un graphique sans mesure activée disponible dans les sessions
sélectionnées est masqué et son espace est redistribué aux autres. La chronologie
est masquée en l’absence d’intervalles. Cocher une mesure disponible fait
réapparaître son graphique sans perdre la période zoomée. Les axes verticaux des mesures se recalculent sur les courbes
visibles dans la période choisie, avec une marge de 5 %. Les points hors période
ne fixent plus l’échelle. Sans donnée visible, la dernière échelle est conservée.
Les bornes et la durée sont indiquées au-dessus du curseur,
en heure locale ou en secondes selon le mode choisi. « Toute la période »
rétablit la vue complète. Le zoom est conservé lorsqu’on coche une mesure et
réinitialisé lorsqu’on change de sessions ou de mode temporel. Les poignées
suivent aussi le zoom et le déplacement effectués avec la barre d’outils.
L’export HTML conserve les sessions complètes, indépendamment du zoom courant.

Sans argument, le lanceur ouvre une fenêtre vide ; les boutons « Ajouter des
fichiers » et « Ajouter un dossier » permettent de charger les journaux.
Tkinter doit être installé au niveau système (`python3-tk` sur Debian/Ubuntu).
Il est déjà disponible sur la machine de développement.

Pour lancer directement la fenêtre sans mise à jour :

```bash
.venv/bin/python bin/kstarsAnalyze.py --gui ~/Téléchargements/analyze --with-captures
```

Pour générer le rapport HTML avec le lanceur, utiliser explicitement `--html` :

```bash
bin/kstarsAnalyze.sh --html ~/Téléchargements/analyze --with-captures --open
```

Les options `-o` et `--open` concernent le mode HTML. Dans la fenêtre, le bouton
« Exporter HTML » enregistre les sessions sélectionnées.

Il fonctionne depuis n’importe quel répertoire lorsqu’on l’appelle avec son
chemin. Les chemins d’entrée et de sortie relatifs restent relatifs au répertoire
d’appel. Pour choisir un autre environnement :

```bash
VENV_DIR=/chemin/vers/venv bin/kstarsAnalyze.sh ~/Téléchargements/analyze
```

Pour utiliser les dépendances déjà installées sans les mettre à jour :

```bash
.venv/bin/python bin/kstarsAnalyze.py ~/Téléchargements/analyze --open
```

Choisir plusieurs fichiers, ou comparer leurs mesures depuis le début de chaque
session plutôt que selon la date locale :

```bash
.venv/bin/python bin/kstarsAnalyze.py nuit1.analyze nuit2.analyze -o out/nuits.html --open
.venv/bin/python bin/kstarsAnalyze.py '~/Téléchargements/analyze/ekos-2026-09-*.analyze' --relative -o out/comparaison.html
```

Pour ne conserver que les fichiers contenant au moins une acquisition :

```bash
bin/kstarsAnalyze.sh ~/Téléchargements/analyze --with-captures
```

`--with-captures` retient les événements `CaptureStarting`, `CaptureComplete` ou
`CaptureAborted` : les poses abandonnées ou encore en cours sont donc incluses.
Le filtre concerne la liste de la fenêtre ou les sessions du rapport HTML ; toutes les
mesures des sessions retenues restent disponibles. Sans cette option, tous les
fichiers sont conservés. Si aucun fichier ne correspond, la liste de la fenêtre reste vide et le filtre
peut être décoché. En mode HTML, la commande signale une erreur sans remplacer le rapport.

Le rapport est enregistré par défaut dans `out/kstars-analyze.html`. Les dossiers
sont parcourus sans récursion et les chemins en double sont dédupliqués. `--open`
ouvre le navigateur ; sans cette option, la génération fonctionne sans affichage.

Le menu sélectionne toutes les sessions ou une session particulière. Le champ
« Fichier(s) affiché(s) » suit cette sélection : cliquer dedans sélectionne le
texte, puis `Ctrl+C` permet de copier le nom (ou la liste des noms en vue globale). Les six
panneaux partagent le zoom temporel : chronologie des tâches et des équipements,
guidage/HFR, RMS dédié, signal et étoiles, température, position de la monture. Survoler une
barre affiche son état, sa durée et les champs du journal, dont le chemin de
l’image pour les acquisitions terminées. Glisser pour zoomer, double-cliquer pour
réinitialiser ; la barre d’outils permet de déplacer la vue et d’exporter un PNG.
Cliquer sur une légende affiche ou masque une série. Les séries secondaires
(impulsions, fond du ciel, étoiles, excentricité, etc.) sont masquées au départ.
Le retour à une sélection de session rétablit cette sélection de mesures initiale.

Les états traduits (par exemple « Suivi » ou « Parquée ») sont conservés tels quels.
Pour l’alignement, `AlignState` décrit les étapes actives (résolution,
synchronisation, pointage, rotation). Succès, terminé, échec, interruption,
suspension et arrêt ferment l’étape précédente sans créer de barre persistante.
L’état de sortie figure dans les détails de l’intervalle.
Les acquisitions et autofocus sont associés à leur début et leur fin ; les
intervalles ouverts sont limités à la dernière donnée, avec une indication de fin
inconnue. Une fin sans début n’invente pas de durée, mais ses mesures sont lues.
Les sessions restent séparées : aucune courbe ne relie deux fichiers.

Les HFR sont en pixels, les erreurs de guidage en secondes d’arc, les impulsions
en millisecondes. Les mesures de différentes unités ne sont pas normalisées :
utiliser la légende pour isoler les séries de même échelle. Les valeurs −1 de HFR
et d’excentricité indiquent une mesure indisponible et sont masquées. Le tableau
récapitule les images terminées, les abandons et le RMS global
`sqrt(mean(AD² + DEC²))`, incluant les phases perturbées ; ce n’est pas le RMS
mobile affiché par KStars. La température est celle consignée par Ekos, sans
supposition sur le capteur physique.

Les dates sont les heures locales écrites dans les journaux ; l’abréviation de
fuseau figure dans le tableau, sans conversion UTC. Utiliser `--relative` pour
comparer des sessions de fuseaux différents. Les sessions sans mesure restent
visibles dans le tableau. Les lignes illisibles sont signalées dans le terminal et
le rapport ; les événements inconnus sont conservés pour la durée mais ne sont
pas représentés. L’absence d’un en-tête valide bloque la génération.

Le rapport inclut Plotly et les chemins d’images consignés dans les journaux :
aucun accès Internet n’est nécessaire pour le consulter. Il n’ouvre pas les FITS
et ne reproduit pas les courbes d’ajustement détaillées d’autofocus de KStars.

Références utilisées pour le format et l’affichage :

- [Documentation officielle du module Analyze](https://kstars-docs.kde.org/en/user_manual/ekos-analyze.html)
- [Lecteur et rédacteur officiel des journaux (analyze.cpp)](https://github.com/KDE/kstars/blob/master/kstars/ekos/analyze/analyze.cpp)

Le RMS du guidage possède son propre graphique, affiché par défaut en secondes
d’arc (″). Le calcul reprend `RmsFilter` du code KStars consulté : racine de la
somme des variances AD et DEC corrigées (diviseur N−1), sur les 40 derniers
échantillons. Le premier point vaut zéro. Après une pause de plus de 30 secondes,
la courbe est interrompue et le filtre réinitialisé. Les valeurs non finies sont
ignorées. C’est une dispersion autour de la moyenne, distincte du RMS global
par rapport à zéro présenté dans le tableau HTML.

Le SNR reste disponible comme mesure optionnelle dans le panneau Signal, masquée
par défaut. Ses valeurs brutes sont conservées, sans division par 1000.

Les couleurs de la ligne Monture suivent exclusivement les marqueurs `MountState` :
gris clair pour l’arrêt, vert pour le suivi, jaune pour le mouvement, bleu pour
le pointage, violet pour le parcage, gris ardoise pour l’état parqué, rouge pour
une erreur. Une légende identifie les états dans un bandeau réservé au-dessus des
graphiques, sur toute la largeur. Elle ne recouvre pas les barres et adapte
son nombre de colonnes à la largeur de la fenêtre. Les états inconnus restent neutres.
Une barre grise indique un état d’inactivité, pas un déplacement. Les messages
`MountCoords` alimentent uniquement les courbes de position : ils ne démarrent
aucune activité et ne changent jamais l’état déclaré de la monture.

La ligne **Scheduler** (anciennement « Tâche ») représente l’exécution des tâches
entre `SchedulerJobStart` et `SchedulerJobEnd`. Un clic sur la barre affiche le
nom de la cible et le motif de fin consigné, par exemple `twilight`.
Sans marqueur de fin, la barre s’arrête à la dernière donnée avec une indication
de fin inconnue. Sans événement Scheduler, aucune activité n’est inventée.

Les acquisitions alternent entre deux teintes dans l’ordre de leur début,
pour rendre visibles les poses successives même sans intervalle perceptible.
Les acquisitions abandonnées ou interrompues sont rouges. L’alternance reste
stable lors du zoom et recommence au début de chaque session. Une acquisition
sans marqueur de fin garde sa couleur d’alternance et sa mention de fin inconnue.

Les dossiers `/light/`, `/flat/` et `/dark/` dans le chemin `CaptureComplete`
déterminent la palette : deux verts pour Light, jaune/orange pour Flat, deux
violets pour Dark. La détection ignore la casse (`/Light/` est accepté) et
reconnaît aussi les séparateurs Windows. Seuls les noms de dossiers complets
comptent, pas un mot inclus dans le nom du fichier. Chaque type possède son
alternance indépendante et figure dans la légende. Sans type reconnu ou sans
fichier de fin, la palette cyan/orange est conservée ; les abandons et
interruptions restent rouges.
