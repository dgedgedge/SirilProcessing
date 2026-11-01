# Installation et démarrage

[Documentation](README.md)

Les lanceurs de traitement utilisent Bash sous Linux et macOS Apple Silicon.
La [commande commune d'installation](scripts/install_dependances/README.md)
détecte la plateforme : `./bin/install_dependances.sh`. Ajouter `--test` pour
vérifier sans installer, ou `--reset` pour recréer complètement le venv.
Les modèles Cosmic Clarity et GraXpert sont préchargés par défaut ; `--assets-only`
permet de les préparer dans un venv existant. Ajouter `--with-gaia` pour l'extrait
Gaia astrométrique facultatif (1,14 Go à télécharger, 1,52 Go installé).
GraXpert nécessite un cache vérifié ou son application officielle pour acquérir
un modèle absent. Le catalogue spectral Gaia SPCC n'est pas inclus.
Le [guide Mac MPS/CoreML](scripts/install_macos/README.md) détaille les limites
de validation et les logiciels natifs requis. L’installation comprend les
paquets Python du projet et les logiciels astronomiques externes. **Installer
les dépendances Python n’installe ni Siril, ni StarNet, ni l’application GraXpert.**

<a id="d-pendances-pr-voir"></a>
<a id="dépendances-à-prévoir"></a>

## 1. Dépendances à prévoir

| Dépendance | Utilisation | Installation |
|---|---|---|
| Python 3, `venv` et pip système | Tous les lanceurs | Gestionnaire de paquets du système ; pip doit prendre en charge `--python` |
| Siril 1.4.x | Calibration, alignement, stacking, photométrie et contrôles de restauration | Logiciel externe ; modes Flatpak, natif ou AppImage |
| StarNet++ V2 historique et son modèle | Séparation des étoiles pour AutoGHS, actif par défaut | Archive CLI externe, puis chemin configuré dans Siril ou avec `--starnet-path` |
| ONNX Runtime et poids GraXpert 3.0.2 | Moteur de débruitage GraXpert, facultatif | `install_dependances.sh` pour le runtime adapté et les poids |
| Application GraXpert | Acquisition des poids manquants uniquement | Logiciel externe, inutile si un modèle local conforme est déjà présent |
| PyTorch et poids Cosmic Clarity | Moteurs de netteté et débruitage Cosmic Clarity | `install_dependances.sh` pour les dépendances et les poids |
| CuPy ou fournisseur ONNX GPU | Accélération AutoGHS ou GraXpert | Dépendances GPU distinctes ; le calcul CPU reste disponible |
| Tkinter | Interfaces graphiques des visualiseurs | Paquet système, par exemple `python3-tk` sous Debian/Ubuntu |
| MkDocs, Playwright et Chromium | Construction du site, du HTML autonome et du PDF | `requirements-docs.txt`, puis navigateur Chromium séparément |
| `dbus-python` et services d’énergie Linux | PowerGuard | `requirements-power.txt` installé automatiquement sous Linux ; l’absence des services ne bloque pas le calcul |

Siril reste nécessaire même si les réseaux GraXpert ou Cosmic Clarity sont
utilisés : les mesures et contrôles stellaires passent aussi par Siril.
Les visualiseurs KStars/guide logs et la génération documentaire seule n’ont
pas besoin de Siril, StarNet ou des modèles de traitement.

<a id="environnement-python-et-lanceurs"></a>

## 2. Environnement Python et lanceurs

Les lanceurs de traitement et de visualisation utilisent
uniquement le **venv déjà installé**. Ils ne créent aucun environnement et
n’installent ni ne mettent à jour ses dépendances. L’aide `--help` ne déclenche
aucune opération pip.

Le lanceur documentaire `build_docs.sh` prépare en revanche automatiquement son
venv, met à jour `requirements-docs.txt` et installe Chromium. Ce parcours de
construction peut nécessiter Internet, y compris avec `--help`. Voir le
[guide documentaire](scripts/generate_docs_html/README.md) pour un appel direct
sans préparation automatique.

Préparer les dépendances séparément, puis lancer le traitement :

```bash
./bin/install_dependances.sh
./bin/lightProcess.sh /chemin/vers/la/session
./bin/postProcess.sh --help
```

Le module partagé `bin/venv_helpers.sh` sélectionne `VENV_DIR` s’il est défini,
sinon `.venv`, puis le dossier historique `venv`. Le venv activé dans le terminal
ne remplace pas cette sélection. Si le Python choisi manque ou n’est pas
exécutable, le lanceur s’arrête et affiche la commande d’installation ciblant
cet environnement. Aucun venv n’est créé implicitement.

L’[installeur commun](scripts/install_dependances/README.md) assure la préparation
Linux CUDA ou Mac, les mises à jour et le préchargement des modèles. Les
traitements peuvent encore avoir besoin du réseau pour une ressource absente
ou un service astronomique distant ; le choix du venv n’utilise pas le réseau.
On peut aussi appeler directement le Python du venv :

```bash
.venv/bin/python bin/postProcess.py --help
.venv/bin/python -m pytest -q tests/test_deconvolution.py tests/test_denoising.py
```

`requirements.txt` inclut les traitements, les visualiseurs, la documentation
HTML/PDF et les tests. Pour une installation limitée aux visualiseurs KStars et guide logs, utiliser
`requirements-analyze.txt`. Le fichier `requirements-docs.txt` permet une
installation documentaire seule, avec MkDocs et Playwright. Chromium est installé
séparément pour les exports HTML/PDF ; voir le [guide documentaire](scripts/generate_docs_html/README.md).
Tkinter reste une dépendance système (`python3-tk`
sous Debian/Ubuntu).

<a id="installer-et-s-lectionner-siril"></a>
<a id="installer-et-sélectionner-siril"></a>

## 3. Installer et sélectionner Siril

Installer Siril depuis les [distributions officielles](https://siril.org/download/),
qui proposent notamment AppImage et un lien vers la distribution Flatpak.
Le parcours complet du projet utilise Siril **1.4.x** ; l’installation de
référence validée est Siril **1.4.4**. Une ancienne version 1.2 ne couvre pas
toutes les commandes de photométrie, de restauration et d’AutoGHS utilisées.

Les options sont communes à lightProcess et postProcess :

| Installation Siril | Options de sélection | Exécutable lancé |
|---|---|---|
| Flatpak, mode par défaut | `--siril-mode flatpak` | `flatpak run --command=siril-cli org.siril.Siril` |
| Installation native | `--siril-mode native --siril-path /chemin/siril-cli` | Le programme indiqué par `--siril-path` |
| AppImage | `--siril-mode appimage --siril-path /chemin/Siril.AppImage` | L’AppImage indiquée, qui doit être exécutable |

Pour vérifier la présence et la version de l’application Flatpak :

```bash
flatpak info org.siril.Siril
```

Les données, dossiers de sortie et programmes externes doivent être accessibles
depuis le mode choisi, notamment dans l’environnement Flatpak. Les wrappers
Python ne changent pas les droits d’accès du bac à sable.
`-S/--save-config` mémorise les réglages dans `~/.siril_darklib_config.json` par
défaut ; `--config fichier.json` sélectionne un autre fichier. Les valeurs de
la ligne de commande priment sur ce fichier. Voir la
[configuration partagée](scripts/postProcess/README.md#configuration-et-mémorisation).

<a id="installer-starnet-pour-autoghs"></a>

## 4. Installer StarNet pour AutoGHS

AutoGHS utilise un logiciel externe pour séparer étoiles et fond :
**StarNet++ V2 historique en version CLI**, avec ses bibliothèques et son modèle.
La validation du projet porte sur StarNet++ **2.0.2** avec Siril 1.4.4.

1. Récupérer l’archive CLI adaptée au système parmi les
   [archives historiques officielles StarNet](https://starnetastro.com/cli-tools/legacy/).
   Extraire l’ensemble et suivre son README, en conservant les fichiers associés
   au programme. L’adaptateur utilise typiquement l’exécutable `starnet++`.
2. Définir le chemin du programme dans les préférences Siril, ou fournir
   `--starnet-path /chemin/StarNetv2CLI_linux/starnet++` au traitement.
3. Ajouter `-S/--save-config` au lancement pour mémoriser ce chemin dans la
   configuration du projet. Sans chemin explicite ou sauvegardé, le programme
   recherche celui des préférences Siril du mode sélectionné.

Le projet appelle la commande Siril historique `starnet -stretch`. Il ne lance
pas le script `StarNet.py` des nouvelles versions : une installation récente
2.5/2.6 ne remplace donc pas automatiquement cette dépendance. La
[documentation Siril sur StarNet](https://siril.readthedocs.io/en/stable/processing/stars/starnet.html)
décrit cette distinction d’interfaces.

AutoGHS est **actif par défaut**, également lors de l’enchaînement depuis
lightProcess. StarNet est donc nécessaire pour ce parcours par défaut.
`--disable-autoghs` permet de conserver un parcours sans rendu AutoGHS ;
`--no-postprocess` désactive l’ensemble du post-traitement dans lightProcess.
Le contrat et les chemins de découverte sont détaillés dans
[AutoGHS : prérequis et configuration](scripts/postProcess/auto-ghs.md).

<a id="installer-le-d-bruitage-graxpert"></a>
<a id="installer-le-débruitage-graxpert"></a>

## 5. Installer le débruitage GraXpert

Le moteur du projet utilise **ONNX Runtime et les poids GraXpert 3.0.2**.
L’application GraXpert sert seulement à acquérir un modèle absent des caches ;
l’inférence sur les images se fait ensuite directement en Python/ONNX.

Préparer le runtime adapté à la plateforme et les modèles avec :

```bash
bin/install_dependances.sh
```

Cette commande ne fournit pas l’application GraXpert, nécessaire uniquement
pour acquérir des poids absents. Pour une installation CPU manuelle limitée,
utiliser `requirements-graxpert.txt` dans un venv distinct du venv CUDA.
Les lanceurs de traitement utilisent ensuite l’environnement préparé sans le modifier.

Le moteur s’active automatiquement si un poids local conforme est présent
dans le cache du projet ou dans un cache GraXpert/Siril, sauf désactivation
explicite. Pour acquérir un modèle absent :

1. Installer l’[application GraXpert officielle](https://github.com/Dark-Matters-Astro/GraXpert/releases).
2. La rendre accessible sous le nom `GraXpert-linux` dans le `PATH`, ou indiquer
   `--graxpert-path /chemin/GraXpert-linux`.
3. Utiliser `--enable-graxpert` lors d’un traitement pour autoriser l’acquisition.
   Un accès Internet est alors nécessaire si le poids manque dans tous les caches.

Le modèle pris en charge reste celui du manifeste du projet, même si le numéro
de version de l’application externe diffère. Une fois le poids validé importé,
l’exécutable GraXpert n’est plus nécessaire à l’inférence. Le cache du projet
est `tmp/cache/models/graxpert/`, exclu de Git et de l’archive.
`--no-enable-graxpert` désactive ce moteur. Voir
[GraXpert : modèles, activation et périphériques](scripts/postProcess/graxpert.md)
pour les contrôles d’intégrité, les erreurs d’acquisition et l’option GPU.

<a id="cosmic-clarity-et-acc-l-ration-gpu"></a>
<a id="cosmic-clarity-et-accélération-gpu"></a>

## 6. Cosmic Clarity et accélération GPU

Les dépendances Cosmic Clarity sont optionnelles et séparées de
`requirements.txt`. L’installeur commun fournit :

```bash
bin/install_dependances.sh
```

Cette commande sélectionne les dépendances adaptées à Linux CUDA ou Mac,
puis précharge les modèles, dont les six poids Cosmic Clarity dans
`tmp/cache/models/cosmicclarity/`. Ce moteur effectue son inférence dans Python :
il ne nécessite pas l’installation de l’application graphique Cosmic Clarity.
Le fichier PyTorch sélectionne son index CUDA officiel avec `--index-url`,
sans index complémentaire PyPI. L'installateur CUDA commun installe ensuite
les paquets PyPI dans une phase séparée.
La comparaison Cosmic Clarity est activée par défaut ; `--disable-clarity`
permet de l’écarter. Siril reste requis pour les contrôles.
Voir [Cosmic Clarity](scripts/postProcess/cosmic-clarity.md) pour les versions
épinglées, les prérequis GPU, les erreurs et les volumes de téléchargement.

Pour installer ensemble **PyTorch CUDA, ONNX Runtime GPU et CuPy** dans le
même venv utilisé par lightProcess et postProcess :

```bash
./bin/install_dependances.sh
```

Le [guide de l'installateur CUDA](scripts/install_cuda/README.md) décrit les
prérequis NVIDIA, les versions cohérentes CUDA 12.8, `--check-only`,
`--install-only` et la sélection du venv. La commande commune précharge aussi
les poids par défaut ; utiliser `--no-models` pour différer leur acquisition.

`--compute-backend auto`, réglage général par défaut, choisit le GPU disponible
pour chaque moteur ou le CPU avant calcul. **Ce choix n’installe aucune
dépendance GPU.** PyTorch CUDA, `onnxruntime-gpu` pour GraXpert et CuPy pour
AutoGHS sont trois installations distinctes. Le pilote et les bibliothèques
doivent être compatibles avec le runtime choisi ; voir la
[politique de calcul CPU/GPU](scripts/postProcess/compute-backend.md).

<a id="documentation-visualiseurs-et-gestion-de-l-nergie"></a>
<a id="documentation-visualiseurs-et-gestion-de-lénergie"></a>

## 7. Documentation, visualiseurs et gestion de l’énergie

Pour les visualiseurs seuls, utiliser `requirements-analyze.txt` et prévoir
Tkinter pour les interfaces graphiques. Pour construire la documentation,
utiliser `requirements-docs.txt` et installer Chromium pour Playwright selon
le [guide documentaire](scripts/generate_docs_html/README.md).

L'installeur inclut `requirements-power.txt` sous Linux, sans l'installer sous
macOS. `dbus-python` peut nécessiter une compilation et des en-têtes système
Python/D-Bus/GLib, qui restent à fournir séparément. PowerGuard utilise les services
D-Bus Linux d’inhibition de veille et de profil énergétique. Les scripts de
haut niveau l’activent pendant les calculs ; son indisponibilité laisse le
traitement continuer. Voir la [gestion de l’énergie](scripts/postProcess/power-guard.md).

Consulter ensuite la [documentation du script](scripts/README.md) concerné
pour les entrées attendues, les paramètres et les vérifications propres à chaque moteur.
