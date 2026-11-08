# install_dependances — installation et test du venv

[Scripts](../README.md) · [Installation](../../INSTALLATION.md)

## 1. Objectif et détection

`bin/install_dependances.sh` est la commande commune pour installer les
dépendances Python de lightProcess et postProcess, tester les accélérateurs
ou recréer le venv. Le lanceur utilise le Python de base, même si le venv à
recréer est activé dans le terminal. `bin/install_dependances.py` expose la
même interface. L'aide ne crée aucun fichier et n'importe aucun moteur GPU.

Les lanceurs de traitement et de visualisation réutilisent ce venv sans
installation ni mise à jour implicite. Le lanceur documentaire prépare ses
propres dépendances et Chromium automatiquement, selon le
[guide documentaire](../generate_docs_html/README.md).

| Plateforme détectée | Dépendances et calculs de contrôle |
|---|---|
| Linux x86_64, NVIDIA | PyTorch CUDA, ONNX Runtime GPU, CuPy et `dbus-python` pour PowerGuard ; contrôles CUDA des trois moteurs |
| macOS 14+, Apple Silicon, Python arm64 natif | PyTorch MPS et ONNX CoreML ; réseau MPS comparé au CPU et nœud ONNX CoreML réellement exécuté |

Python 3.11 à 3.14, venv et pip avec l'option `--python` sont requis. Le Python
système fournit pip, mais tous les paquets sont installés dans le venv cible.
Sous Linux, `requirements-power.txt` est installé automatiquement depuis PyPI
dans une phase autorisant la compilation de `dbus-python`. Prévoir les outils
de compilation, les en-têtes Python et les bibliothèques de développement
D-Bus/GLib si aucune roue n'est disponible. Les paquets système ne sont pas
installés par le script ; un échec de compilation arrête l'installation.
macOS ne reçoit pas cette dépendance Linux. Les modes `--test` et
`--assets-only` n'installent aucun paquet, y compris PowerGuard.
Un Python Intel sous Rosetta, Linux ARM et les autres plateformes sont refusés
avant installation ou nettoyage. Le script ne choisit pas silencieusement un
ensemble CPU lorsque le pilote NVIDIA manque : corriger le pilote ou utiliser
`--install-only` pour préparer les paquets sans certifier leur fonctionnement.

Les six modèles Cosmic Clarity et le modèle GraXpert sont préchargés par défaut
(environ **572 Mo** au total, dont 94 Mo pour Cosmic Clarity et 478 Mo pour GraXpert).
Les fichiers déjà conformes sont réutilisés après contrôle SHA-256.
Siril ≥ 1.4.4, le CLI StarNet avec son modèle et les pilotes restent des installations externes.
Le script contrôle désormais une connexion réelle à sirilpy ≥ 1.0.25, fourni
par le Python de Siril : aucun paquet PyPI homonyme n’est installé. Ce contrôle
a lieu après pip, y compris en mode `--install-only`, et avec `--test`.
`--assets-only` ne lance pas Siril. Le contrôle n’ouvre aucune image et utilise
des préférences temporaires. Le journal est `tmp/siril-dependency-check/siril-api-check.log`.
Les fichiers JSON de traitement ne sont pas modifiés. Les dépendances générales
de `requirements.txt` sont incluses dans les deux installations.

## 2. Commandes et options

```bash
# Détecter la plateforme, installer puis vérifier les accélérateurs.
./bin/install_dependances.sh

# Tester le venv existant, sans installation ni nettoyage.
./bin/install_dependances.sh --test

# Supprimer entièrement le venv choisi, le recréer, installer et vérifier.
./bin/install_dependances.sh --reset

# Choisir le venv explicitement, même si son chemin contient des espaces.
./bin/install_dependances.sh --venv "/chemin/vers/mon venv" --test

# Installer sans exécuter les calculs de contrôle.
./bin/install_dependances.sh --install-only

# Précharger les modèles dans un venv existant, sans relancer pip ni tester le GPU.
./bin/install_dependances.sh --assets-only

# Préparer toutes les ressources, catalogues Gaia compris.
./bin/install_dependances.sh --assets-only

# Gaia seul, sans acquisition de modèles.
./bin/install_dependances.sh --assets-only --disable-models

# Installer les dépendances sans précharger les modèles.
./bin/install_dependances.sh --no-models
```

| Option | Défaut | Rôle |
|---|---|---|
| `--venv DOSSIER` | `VENV_DIR`, puis `.venv` du projet, puis ancien `venv` | Cible commune aux lanceurs ; créer `.venv` si aucun défaut n'existe |
| `--reset` | faux | Effacer le venv et les modèles avant réinstallation ; conserver Gaia |
| `--test` / `--check-only` | faux | Vérifier sans créer, installer, mettre à jour ou supprimer de paquet |
| `--install-only` | faux | Installer sans contrôle des accélérateurs ; compatible avec `--reset` |
| `--assets-only` | faux | Précharger les ressources dans un venv existant, sans pip ni test GPU |
| `--models` / `--no-models` | vrai | Précharger les poids Cosmic Clarity et GraXpert |
| `--disable-models` | faux | Alias de `--no-models` |
| `--disable-gaia` | faux | Ne préparer aucun catalogue Gaia |
| `--disable-gaia-astrometry` | faux | Désactiver seulement le catalogue astrométrique |
| `--disable-gaia-spcc` | faux | Désactiver seulement le catalogue spectral |
| `--with-gaia` | vrai | Activer Gaia astrométrique (compatibilité) |
| `--config FICHIER` | `~/.siril_darklib_config.json` | Lire les chemins/version des modèles et le mode Siril, sans écrire le JSON |
| `--cosmic-model-dir`, `--graxpert-model-dir` | JSON, puis `tmp/cache/models/...` | Caches utilisés ensuite par les traitements ; CLI prioritaire |
| `--graxpert-path` | JSON, puis nom habituel de l'application | Application officielle pour acquérir un modèle absent |
| `--with-gaia-spcc` | vrai | Activer les régions spectrales SPCC sélectionnées (compatibilité) |
| `--gaia-spcc-coverage` | `north` | `north`, `south`, `remaining` ou `all` ; les régions équatoriales sont incluses dans les deux hémisphères |
| `--gaia-spcc-dir DOSSIER` | `tmp/cache/catalogues/gaia/spcc` | Dossier des spectres SPCC |
| `--gaia-dir DOSSIER` | `tmp/cache/catalogues/gaia` | Dossier d'acquisition Gaia si aucun catalogue Siril conforme n'est trouvé |

`--test` et `--install-only` sont exclusifs. `--reset --test` et
`--reset --check-only` sont refusés avant toute mutation. Un venv absent fait
échouer le test sans le créer. Avec un venv personnalisé, fournir également
`VENV_DIR` aux lanceurs de traitement.
`--assets-only` est également exclusif avec les deux modes précédents et avec
`--reset`. `--test` reste un contrôle des accélérateurs sans acquisition de
modèle ni catalogue, même avec les ressources actives par défaut. `--install-only` précharge
les modèles sauf avec `--no-models`, mais ne certifie aucun accélérateur.

Le même point d'entrée est utilisé sur les deux plateformes, y compris pour
les contrôles internes lancés dans le venv sélectionné.

## 3. Nettoyage et erreurs

`--reset` supprime **tout le contenu du seul venv sélectionné**, y compris les
paquets ajoutés manuellement. Un dossier non vide doit être reconnu par un
`pyvenv.cfg` régulier. Un lien symbolique de cible ou de ce marqueur, le domicile,
le dépôt et ses parents, le Python de base et ses parents sont refusés.
Un dossier vide ou absent est accepté comme cible à recréer.

`--reset` nettoie aussi les modèles : les arbres dédiés
`tmp/cache/models/` et l'ancien `tmp/models/` sont supprimés. Dans les dossiers
personnalisés configurés, seuls les fichiers de poids épinglés Cosmic Clarity
et GraXpert sont effacés, pas les autres documents. Les caches des applications
externes ne sont pas supprimés. **Gaia est conservé**, hors de l'arbre modèles.

La plateforme est validée avant le nettoyage ; sous Linux, le pilote est aussi
vérifié avant suppression, sauf avec `--install-only`. Le nettoyage puis
l'installation ne forment pas une transaction : si le téléchargement ou
l'installation échoue après suppression, l'ancien venv n'est pas restauré.
Corriger l'erreur et relancer la commande. Les images et configurations
**hors du venv** sont conservées. Le projet ne lance aucun traitement astronomique
pendant cette opération.

Un échec d'installation ou de calcul est signalé avec un code de sortie 1 ;
une combinaison d'arguments invalide utilise 2. Le code 0 indique la réussite
du mode demandé : en mode `--install-only`, il ne certifie pas les accélérateurs.

## 4. Dépendances et vérification par plateforme

Sous Linux, PyTorch est d'abord installé depuis son index CUDA exclusif ; les
autres dépendances sont ensuite résolues sur PyPI en conservant le pin PyTorch.
Aucun index complémentaire ne fusionne les candidats. Voir le détail des pins,
des conflits CPU/GPU et des contrôles dans [Installation CUDA](../install_cuda/README.md).

Sur Mac, les roues MPS et CoreML viennent du seul PyPI. Aucun paquet CUDA n'est
installé. CoreML peut répartir les opérations entre CPU, GPU et Neural Engine ;
AutoGHS reste sur CPU ; la couleur finale et les diagnostics bruit/diffus
disposent du moteur PyTorch MPS. Le contrôle ne garantit pas que tous
les modèles GraXpert s'exécutent intégralement sur GPU. Voir
[Installation Apple Silicon](../install_macos/README.md).

Le test s'exécute avec le Python du venv cible et contrôle des calculs réels,
pas seulement des imports. Les contrôles CUDA ont été exécutés sur NVIDIA ;
les chemins Mac sont testés par simulation sous Linux. La validation matérielle
Apple reste à effectuer sur la machine cible avec `--test`.

## 5. Module et tests

`lib.dependency_installation.DependencyInstaller` porte la détection, les gardes
de nettoyage et l'enchaînement `install`/`verify`. Ses méthodes sont
`reset_environment()` et `run(reset=False, test=False, install_only=False)`.
La CLI commune `main()` assure les options et codes de retour. Les moteurs
`CudaEnvironmentInstaller` et `MacOSEnvironmentInstaller` conservent leurs
contrôles spécialisés et partagent `GpuEnvironmentInstaller` pour la cible venv,
pip et les graphes ONNX de diagnostic.

`tests/test_dependency_installation.py` couvre les deux détections, le nettoyage
réel de dossiers temporaires, l'ordre précontrôle/nettoyage/installation/test,
les cibles refusées, les modes incompatibles et l'absence de mutation en test.
Les tests des moteurs couvrent séparément leurs commandes pip et leurs calculs.

## 6. Modèles et catalogue Gaia

L’installation et les traitements utilisent le même
[gestionnaire de ressources](resources.md#resource-manager).
Le préchargement utilise le Python du venv **après** installation des paquets et
**avant** les contrôles GPU. Les chemins suivent CLI > configuration JSON >
défauts du projet. Un dossier personnalisé doit aussi être utilisé lors du
traitement ; l'installateur ne modifie pas le JSON. Les caches par défaut sont
hors venv, sous la même arborescence :

```text
tmp/cache/
├── models/cosmicclarity/       # six fichiers .pth
├── models/graxpert/3.0.2/      # model.onnx
└── catalogues/gaia/           # astrométrie .dat et pointeurs JSON
    └── spcc/                 # régions spectrales choisies (nord par défaut)
```

Les anciens défauts `tmp/models/...` sont redirigés vers les nouveaux chemins
dans les options CLI/JSON, et les fichiers valides de cet ancien cache sont
importés sans réseau. Le nettoyage `--reset` efface les copies des deux arbres.
Les dossiers personnalisés restent des surcharges explicites.

GraXpert réutilise d'abord les caches vérifiés du projet, de Siril ou de
l'application, y compris `~/Library/Application Support/GraXpert` sur Mac.
Si aucun modèle conforme n'existe, l'application **officielle GraXpert doit
être installée** et accessible avec `--graxpert-path`. Son CLI acquiert le
modèle sur un FITS synthétique jetable. L'installateur n'extrait aucun identifiant
interne et n'installe pas l'application GraXpert. Un poids manquant sans moyen
d'acquisition provoque une erreur explicite ; `--no-models` permet de différer
ce préchargement. StarNet utilise le modèle fourni avec son propre CLI.
Un modèle GraXpert local conforme rend ce moteur éligible à son activation
automatique lors des traitements ; les contrôles qualité déterminent toujours
si son résultat est retenu.

L'extrait Gaia DR3 astrométrique officiel Siril représente **1,142 Go à
télécharger et 1,521 Go installé** ; environ **2,7 Go libres** sont nécessaires
pendant l'installation. Il est actif par défaut ; `--disable-gaia-astrometry`
le désactive seul et `--disable-gaia` désactive tous les catalogues Gaia. Un catalogue
déjà référencé dans les préférences Siril du mode choisi est vérifié et réutilisé
dans le cache commun par lien dur, sans duplication si le disque est le même.
Sur un autre disque il est copié et vérifié. Sinon l'archive est téléchargée
depuis Zenodo, contrôlée, décompressée et remplacée atomiquement. Le cache
enregistre aussi un chemin personnalisé si `--gaia-dir` est fourni.

**Les scripts utilisent automatiquement ce Gaia local vérifié** quand Gaia
est demandé pour le plate solving. Ils passent `-catalog=localgaia` et un
fichier de préférences Siril temporaire contenant son chemin. Aucun réglage
personnel Siril n'est modifié et aucune sélection manuelle n'est requise.
En l'absence de cache conforme, le catalogue Gaia distant reste utilisé.
Les autres catalogues explicitement demandés restent respectés.

Le catalogue spectral Gaia est distinct et également installé par défaut
pour le nord céleste (28 régions, environ 5,28 Go compressés et 10,49 Go installés). Pour compléter
uniquement ces spectres :

```bash
./bin/install_dependances.sh --assets-only --disable-models --disable-gaia-astrometry
```

Les fichiers valides et les téléchargements partiels Siril sont réutilisés.
Après installation complète, SPCC sélectionne automatiquement le catalogue local.
Les recherches de noms et l'acquisition des profils Siril peuvent encore nécessiter Internet.
Voir [le préchargement et ses contrôles](resources.md),
[la source officielle Gaia](https://zenodo.org/records/14692304) et
[la documentation Siril](https://siril.readthedocs.io/en/stable/astrometry/platesolving.html#download-astrometric-gaia-dr3).

## 5. Moteur Siril et API Python

`--siril-mode native|flatpak|appimage` et `--siril-path CHEMIN` permettent de
contrôler le même moteur que les traitements. Le mode par défaut est Flatpak
sous Linux ; sous macOS, le contrôle cherche l’installation native.

```bash
./bin/install_dependances.sh --test --siril-mode flatpak
./bin/install_dependances.sh --install-only --siril-mode native --siril-path /opt/siril-cli
```

Le test échoue si Siril, son environnement Python, sa version de sirilpy ou
la connexion ne sont pas utilisables. Installer Siril et laisser son gestionnaire
préparer le Python intégré avant de relancer si nécessaire. Le mode sans
calcul GPU ne dispense pas de cette dépendance des traitements. Voir
[les sessions API](../lightProcess/reference/SIRIL_API.md).
