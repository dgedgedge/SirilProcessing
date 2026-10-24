# Politique commune de calcul

[postProcess](README.md) › Calcul CPU/GPU

[`ComputeConfig`](../../../lib/compute_config.py) déclare la politique commune
`--compute-backend auto|cpu|cuda`, avec **auto par défaut**. Cette classe dérive
de `ParameterizedComponent`, n'importe aucune bibliothèque GPU et ne charge
aucun modèle. Sa méthode `add_arguments` évite les déclarations multiples dans
les parseurs de la séquence et de ses moteurs.

## Choix du périphérique

| Moteur | auto | cpu | cuda |
|---|---|---|---|
| Cosmic Clarity | CUDA si `torch.cuda.is_available()`, CPU sinon | PyTorch CPU | CUDA requis |
| GraXpert | Fournisseur ONNX GPU fonctionnel, CPU sinon | ONNX CPU | `CUDAExecutionProvider` requis |
| AutoGHS | CuPy/CUDA après contrôle et budget VRAM, NumPy sinon | NumPy | CuPy/CUDA requis |

Chaque moteur vérifie ses propres dépendances : un CUDA disponible dans PyTorch
n'installe ni CuPy ni un fournisseur ONNX GPU. Le choix du calcul ne désactive
pas les moteurs et ne remplace pas leurs dépendances manquantes. Les commandes
Siril et le moteur interne StarNet conservent leurs propres réglages.

La résolution automatique intervient **avant le calcul**. Pour ONNX, un
fournisseur annoncé mais non initialisable peut conduire au choix CPU pendant
le chargement de la session, avant le premier appel d'inférence. Les erreurs de
contrat de modèle ne sont pas traitées comme une absence d'accélérateur.

Après démarrage du calcul, une panne ne déclenche pas de reprise automatique.
GraXpert conserve son option historique **explicite** `--graxpert-cpu-fallback` :
seule son activation autorise à recommencer l'image sur CPU après une panne GPU.

## Configuration, surcharges et transmission

La valeur générale suit CLI > JSON > défaut auto et se sauvegarde avec `-S` :

```bash
.venv/bin/python bin/postProcess.py image_RGB_lineaire.fits rapports/ \
  --compute-backend auto -S
```

Les surcharges propres aux moteurs restent prioritaires :

- `--cosmic-device auto|cpu|cuda` ; défaut `null`, donc héritage du choix général ;
- `--graxpert-gpu` / `--no-graxpert-gpu` ; défaut `null`, donc héritage également.

Les anciennes valeurs JSON `cosmic_device: "cuda"` ou `graxpert_gpu: false`
restent des choix explicites. Elles ne sont pas écrasées par le réglage général.
Retirer ces clés ou les mettre à `null` permet de retrouver l'héritage. Le choix
`cosmic_device: "auto"` constitue une surcharge automatique de ce seul moteur,
même si la politique générale est CPU ou CUDA.

`ComputeConfig.requested(args, override)` résout une surcharge non nulle puis
`compute_backend`, sinon auto. GraXpert adapte son ancienne surcharge booléenne
au choix GPU/CPU. `Config.bind_parameters` transmet `compute_backend` comme un
paramètre commun à toutes les classes enregistrées ; les instances et moteurs
créés tardivement conservent leurs instantanés indépendants. Les moteurs
résolvent leurs périphériques au moment prévu par leur propre cycle de vie.

La CLI journalise la politique commune. Les rapports Cosmic Clarity conservent
le périphérique PyTorch effectif, ceux de GraXpert les fournisseurs ONNX et la
politique demandée, ceux d'AutoGHS le backend choisi et le motif du précontrôle.

## Vérifications

`tests/test_compute_config.py` vérifie la déclaration unique, le défaut auto,
la sauvegarde et les priorités, la transmission aux moteurs tardifs et les
surcharges historiques. Les disponibilités CUDA simulées vérifient la sélection
CPU/CUDA ; un fournisseur ONNX non initialisable est testé séparément d'une
inférence déjà commencée. Ces doubles ne valident pas une exécution matérielle
GPU. Les tests des moteurs conservent leurs contrôles de panne et de reprise
explicitement autorisée.

Voir les détails de [Cosmic Clarity](cosmic-clarity.md),
[GraXpert](graxpert.md), et du [backend AutoGHS](auto-ghs-algorithm.md#cpu-cuda-et-ressources).

## Installation commune

`bin/install_cuda.sh` prépare et vérifie les trois dépendances CUDA dans le
même venv. Voir [Installation CUDA](../install_cuda/README.md). L’option
`--compute-backend` choisit le moteur et ne réalise pas cette installation.
