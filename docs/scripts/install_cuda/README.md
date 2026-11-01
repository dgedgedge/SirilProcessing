# Dépendances CUDA du venv commun

[Scripts](../README.md) › Installation CUDA

Utiliser la [commande commune](../install_dependances/README.md)
`bin/install_dependances.sh`, qui détecte la plateforme et propose `--reset`
et `--test`. Cette page décrit les dépendances et contrôles de sa branche CUDA.

## 1. Objectif et prérequis

`bin/install_dependances.sh` appelle `bin/install_dependances.py` pour installer l'ensemble des
dépendances Python GPU de Cosmic Clarity, GraXpert et AutoGHS dans le venv commun
à `lightProcess` et `postProcess`. Il ne lance aucun traitement astronomique.

Prérequis : Linux x86_64, Python 3.11 à 3.14, `venv`, pip système avec `--python`
et un accès aux dépôts PyPI/PyTorch. Le mode normal exige un pilote NVIDIA
accessible à `nvidia-smi -L`, puis valide la compatibilité réelle par des calculs.
Le pilote, Siril et StarNet ne sont pas installés par ce script.
Sur Apple Silicon, utiliser l'[installateur Mac MPS/CoreML](../install_macos/README.md).
Les téléchargements peuvent représenter plusieurs gigaoctets selon l'état du venv.

## 2. Commandes et options

```bash
# Installer et vérifier tous les moteurs CUDA dans le venv habituel.
./bin/install_dependances.sh

# Vérifier seulement ; aucun paquet n'est installé ou retiré.
./bin/install_dependances.sh --check-only

# Choisir explicitement un venv, également utilisable via VENV_DIR.
./bin/install_dependances.sh --venv /chemin/vers/venv

# Installer sans accès GPU ; le fonctionnement n'est pas certifié.
./bin/install_dependances.sh --install-only
```

Le choix est `--venv` > `VENV_DIR` > `.venv` du projet > ancien dossier `venv`.
Si aucun des deux dossiers par défaut n'existe, `.venv` est créé. Les lanceurs
retrouvent ce même environnement ; avec un chemin personnalisé, leur transmettre
également `VENV_DIR=/chemin/vers/venv`. L'environnement actif du terminal ne
remplace pas ce choix. `--help` affiche l'aide sans créer de venv.

| Option | Défaut | Effet |
|---|---|---|
| `--venv DOSSIER` | choix commun ci-dessus | Cible explicite |
| `--check-only` | faux | Vérification sans installation |
| `--install-only` | faux | Installation sans contrôle matériel ni calcul final |

Les deux modes spéciaux sont mutuellement exclusifs. Les mêmes options sont
acceptées par `python3 bin/install_dependances.py`.

## 3. Ensemble installé et compatibilité

L'installation utilise deux appels pip avec des index séparés : d'abord
`requirements-cosmic-clarity.txt` depuis l'index CUDA officiel PyTorch, puis
`requirements.txt` et `requirements-cuda.txt` depuis PyPI :

| Moteur | Paquet | Runtime ciblé |
|---|---|---|
| Cosmic Clarity | PyTorch `2.10.0+cu128` | CUDA 12.8, cuDNN fourni par les dépendances PyTorch |
| GraXpert | `onnxruntime-gpu==1.26.0` | CUDA 12.8, cuDNN 9 |
| AutoGHS | `cupy-cuda12x[ctk]==14.2.0` | Composants CUDA Python fournis par `cuda-toolkit==12.8.1` |

Le fichier Cosmic Clarity utilise exclusivement
`--index-url https://download.pytorch.org/whl/cu128`. Le fichier CUDA utilise
`--index-url https://pypi.org/simple` et conserve le même pin
`torch==2.10.0+cu128`, déjà satisfait par la première étape. Il n'inclut pas le
fichier PyTorch : les options d'index d'un fichier inclus s'appliqueraient à
toute la résolution. Aucun `--extra-index-url` ne mélange les candidats des
deux dépôts. Installer `requirements-cosmic-clarity.txt` avant
`requirements-cuda.txt` en cas d'installation manuelle.
L'extra CuPy `ctk` installe les composants CUDA dans l'environnement : aucun
Toolkit global n'est nécessaire. La version 12.8.1 du métapaquet fixe les
composants compatibles avec les dépendances de ce PyTorch.
Les versions GPU ONNX postérieures peuvent cibler CUDA 13 par défaut : elles
ne sont pas sélectionnées automatiquement par cet installateur.

Sources : [installation CuPy](https://docs.cupy.dev/en/stable/install.html) et
[compatibilité CUDA ONNX Runtime](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html).

Le script retire `onnxruntime` CPU et les variantes CuPy concurrentes du venv,
après réussite de la résolution préalable. Ces paquets partagent des modules
avec leurs variantes GPU ; les distributions retenues sont réinstallées pour
restaurer leurs fichiers. Ne pas réinstaller ensuite `onnxruntime` CPU ou une
autre variante de CuPy dans ce venv. Les lanceurs de traitement réutilisent
cet environnement sans changer ses dépendances.

La commande commune précharge par défaut les poids Cosmic Clarity et GraXpert,
après pip et avant les contrôles GPU. `--no-models` saute cette acquisition ;
`--assets-only` la relance sans pip. Voir [les ressources](../install_dependances/resources.md),
[Cosmic Clarity](../postProcess/cosmic-clarity.md) et [GraXpert](../postProcess/graxpert.md).
Les réglages CPU/GPU et les fichiers
JSON de traitement ne sont pas modifiés ; `--compute-backend auto` reste le
choix habituel. Les moteurs internes Siril/StarNet gardent leur propre environnement.

## 4. Vérifications, sorties et erreurs

Le mode normal vérifie d'abord le pilote, crée le venv sans pip embarqué et
utilise le pip système avec `--python` ciblant ce venv. Chaque phase GPU possède
sa résolution préalable `pip install --dry-run`. PyTorch est installé avant
la résolution PyPI ; celle-ci doit réussir avant les retraits des paquets en
conflit. Une phase PyPI séparée installe ensuite `requirements-power.txt` pour
PowerGuard. Elle autorise les sources de `dbus-python`, qui peuvent nécessiter
un compilateur et les en-têtes Python, D-Bus et GLib. Les paquets système restent
à installer séparément. Un échec de cette phase est propagé ; après installation,
`pip check` vérifie les dépendances.

Les vérifications finales sont exécutées dans un processus du venv cible :

- produit matriciel PyTorch sur CUDA ;
- transfert GHS, histogrammes et quantiles CuPy sur CUDA, avec retour des valeurs
  et contrôle numérique ;
- petite session ONNX MatMul, fournisseur CUDA exigé et repli CPU désactivé.

ONNX précharge les bibliothèques NVIDIA installées par pip, également lors de
l'inférence GraXpert normale. Les versions et résultats sont écrits dans la
console. Un import réussi ou un fournisseur simplement annoncé ne suffit pas
à annoncer que CUDA fonctionne. Le petit graphe ONNX ne requiert aucun modèle
astronomique supplémentaire.

Code de sortie : 0 pour la réussite du mode demandé ; 1 pour un échec matériel,
d'installation ou de contrôle ; 2 pour des options invalides. `--install-only`
annonce explicitement que le fonctionnement GPU n'est pas vérifié.
Un échec de résolution ne retire pas les paquets ONNX/CuPy en conflit ; si la
seconde résolution échoue, PyTorch et ses dépendances peuvent déjà avoir été
mis à jour. Une interruption pendant l'installation peut laisser le venv
incomplet. Corriger l'erreur puis relancer
le script. Aucun paquet Python système ni pilote n'est modifié.

## 5. Module et tests

`lib.cuda_environment.CudaEnvironmentInstaller` porte la sélection de venv,
les commandes pip, la gestion des conflits et les contrôles. Ses interfaces
sont `install(install_only=False)`, `verify()` et le contrôle interne
`verify_worker()`. Les imports GPU sont différés jusqu'au processus de contrôle.

`tests/test_cuda_environment.py` couvre les chemins, la séparation des index,
la cohérence des pins PyTorch, l'ordre des deux résolutions,
les conflits, les erreurs du pilote et de pip, l'aide sans effets de bord et la
validité du graphe de contrôle. Les tests simulés n'attestent pas de l'installation
des paquets ni de leur fonctionnement matériel : le mode normal ou `--check-only`
fournit cette vérification sur la machine cible.
