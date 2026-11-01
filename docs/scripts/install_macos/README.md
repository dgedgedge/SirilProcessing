# Installation Apple Silicon : MPS et CoreML

[Scripts](../README.md) · [Installation commune](../../INSTALLATION.md)

La [commande unique `install_dependances`](../install_dependances/README.md)
détecte Apple Silicon automatiquement. Elle accepte `--reset` pour recréer le
venv et `--test` pour vérifier sans installer. Cette page détaille les
dépendances et contrôles de sa branche Apple Silicon.

## 1. Objectif et prérequis

`bin/install_dependances.sh` appelle `bin/install_dependances.py` pour préparer le venv de
lightProcess et postProcess sur Mac M1, M2 ou génération suivante. Le calcul
préfère les accélérateurs Apple disponibles avec `--compute-backend auto`.

Prérequis : **macOS 14 ou ultérieur**, Python **arm64 natif 3.11 à 3.14**, venv
et pip avec l'option `--python`. Un Python Intel sous Rosetta est refusé. Les
versions épinglées disposent de roues arm64 sur PyPI. Aucun paquet CUDA ni
pilote NVIDIA n'est nécessaire. Les téléchargements peuvent être volumineux.

| Traitement | Calcul sur Mac avec la politique `auto` |
|---|---|
| Cosmic Clarity | PyTorch **MPS**, CPU si MPS indisponible avant le traitement |
| GraXpert | ONNX **CoreML**, CPU si aucun fournisseur utilisable au démarrage |
| AutoGHS et couleur finale | **CPU** NumPy/SciPy ; leur moteur CuPy/CUDA ne cible pas Metal |
| Siril et StarNet | Applications externes, paramètres et compatibilité propres |

CoreML choisit la répartition CPU/GPU/Neural Engine selon le modèle. La présence
de CoreML ne garantit pas que toutes les opérations de GraXpert passent sur GPU.
L'utilisation de MPS est un choix GPU explicite pour les réseaux Cosmic Clarity.

## 2. Installation et contrôles

```bash
# Installer les dépendances puis exécuter les contrôles MPS et CoreML.
./bin/install_dependances.sh

# Contrôler le venv existant sans installer ou désinstaller de paquet.
./bin/install_dependances.sh --check-only

# Venv personnalisé ; transmettre aussi VENV_DIR aux lanceurs de traitement.
./bin/install_dependances.sh --venv /chemin/vers/venv

# Installer les paquets sans certifier le fonctionnement des accélérateurs.
./bin/install_dependances.sh --install-only
```

| Option | Défaut | Rôle |
|---|---|---|
| `--venv DOSSIER` | `VENV_DIR`, puis `.venv`, puis ancien `venv` | Même sélection que les lanceurs |
| `--check-only` | faux | Contrôles sans installation |
| `--install-only` | faux | Installation sans calcul de validation |

Les modes spéciaux sont mutuellement exclusifs ; `--help` n'a aucun effet de
bord. Les options sont identiques avec `python3 bin/install_dependances.py`.

L'installateur utilise le pip du Python de base avec `--python` ciblant le
venv. `requirements.txt` et `requirements-macos.txt` sont résolus sur le seul
index PyPI. Ce dernier épingle **torch 2.10.0** et **onnxruntime 1.26.0**.
Sur Mac, le paquet `onnxruntime` inclut CoreML ; le paquet NVIDIA
`onnxruntime-gpu` n'est pas requis. Les variantes ONNX GPU/DirectML éventuelles
sont retirées après résolution réussie et le runtime natif est restauré.
Un échec de résolution ne retire aucun paquet ; une installation interrompue
peut laisser le venv incomplet. `pip check` valide la cohérence finale.

Le contrôle s'exécute dans le venv choisi. Il compare le réseau Cosmic Clarity
sur MPS au même réseau CPU avec des poids aléatoires, en float32 (tolérances
relatives `1e-3`, absolues `1e-4`). Il exécute aussi un petit graphe ONNX Add et
exige un événement CoreML dans le profil d'exécution, pas seulement un fournisseur
annoncé. Les traces sont temporaires et aucun poids astronomique n'est téléchargé.
Cela ne certifie ni les performances, ni tous les modèles GraXpert, ni le
matériel GPU/Neural Engine effectivement choisi en interne par CoreML.

Codes de sortie : 0 pour la réussite du mode demandé, 1 pour un échec, 2 pour
des arguments invalides. Le mode `--install-only` ne certifie aucun accélérateur.

## 3. Traitement et paramètres

Installer **Siril 1.4+ natif et StarNet pour macOS** séparément. Leurs exécutables
doivent être accessibles ou fournis explicitement. Le mode Siril par défaut
historique reste Flatpak : sélectionner `native` sur Mac. Exemple avec les deux
exécutables disponibles dans le PATH et un FITS RGB linéaire :

```bash
./bin/postProcess.sh image_RGB.fits resultats/ \
  --siril-mode native --siril-path siril --starnet-path starnet++ \
  --compute-backend auto
```

Ce scénario complet nécessite ces logiciels et une validation sur Mac réel ;
il n'a pas été exécuté depuis l'environnement Linux de développement.
La commande commune précharge les modèles par défaut ; `--assets-only` permet
de le faire séparément. Un modèle encore absent peut être acquis à la demande.
L’installeur commun sélectionne les dépendances Mac ; le dossier personnalisé
suit `--cosmic-model-dir`. Les lanceurs de traitement utilisent le venv préparé.
Pour préparer GraXpert, suivre [son guide de modèles](../postProcess/graxpert.md).

`--cosmic-device mps` force MPS pour ce moteur et échoue s'il est indisponible.
`--cosmic-device cpu` force son CPU. Ces surcharges suivent CLI > JSON > défaut,
se mémorisent avec `-S` et priment sur la politique globale. Utiliser
`--compute-backend cpu` pour tous les moteurs sans surcharge explicite.
Les choix globaux sont `auto` et `cpu`, sans distinction de fabricant.
L'ancien `--compute-backend cuda` est converti en `auto` avec avertissement.
Un moteur qui choisit le CPU en auto émet un WARNING ; AutoGHS et la saturation
précisent l'absence de moteur Metal implémenté.

Les réglages NVIDIA/CPU enregistrés sur une autre machine restent prioritaires :
mettre `cosmic_device` et `graxpert_gpu` à `null` pour retrouver l'héritage de
`compute_backend: "auto"`. L'installation ne modifie pas cette configuration.

Aucune panne MPS en cours d'inférence ne relance silencieusement le calcul sur
CPU. GraXpert conserve son repli après panne uniquement sur demande explicite
avec `--graxpert-cpu-fallback`. Les opérations ONNX auxiliaires peuvent rester
sur CPU dans une même session CoreML.

## 4. Modules, tests et limites

`MacOSEnvironmentInstaller` dans `lib/macos_environment.py` expose `install`,
`verify` et `verify_worker`. Le socle `GpuEnvironmentInstaller` partage avec
l'installateur CUDA la sélection du venv, les commandes pip et le graphe ONNX.
L'inférence MPS est sélectionnée par `select_device` ; GraXpert configure
CoreML en `MLProgram`, avec `MLComputeUnits=ALL`. Aucun import GPU n'est requis
pour afficher l'aide des installateurs.

`test_macos_environment.py`, `test_compute_config.py`, `test_graxpert.py` et
`test_venv_helpers.py` couvrent les plateformes simulées, les erreurs, les
priorités de configuration et les index d'installation. Les contrôles simulés
Linux ne valident pas le matériel Apple. Un test matériel est exécuté sur un
hôte Darwin arm64 ; ailleurs il est ignoré. Sur le Mac cible, exécuter :

```bash
./bin/install_dependances.sh --check-only
```

Sources : [PyTorch 2.10 MPS](https://docs.pytorch.org/docs/2.10/notes/mps.html),
[ONNX CoreML](https://onnxruntime.ai/docs/execution-providers/CoreML-ExecutionProvider.html).
