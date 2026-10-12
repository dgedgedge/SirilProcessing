# Classes de restauration et orchestration

[postProcess](README.md) · [Cosmic Clarity](cosmic-clarity.md)

La séquence connaît les étapes gradient, photométrie, déconvolution et débruitage.
Elle conserve leurs instances et ne sélectionne pas leurs moteurs internes.
Chaque étape de restauration déclare et organise ses propres enfants.

| Module | Classe | Responsabilité |
|---|---|---|
| `lib/denoising.py` | `NoiseReductionProcessor` | Organiser le débruitage et sélectionner un candidat |
| `lib/denoising.py` | `SirilNoiseReductionProcessor` | Produire et contrôler uniquement le candidat Siril |
| `lib/deconvolution.py` | `DeconvolutionProcessor` | Organiser la déconvolution et sélectionner un candidat |
| `lib/deconvolution.py` | `SirilDeconvolutionProcessor` | Produire et contrôler uniquement les essais Siril |
| `lib/cosmic_clarity/processors.py` | `CosmicClarityDenoiseProcessor` | Produire et contrôler uniquement le candidat neuronal de débruitage |
| `lib/cosmic_clarity/processors.py` | `CosmicClaritySharpenProcessor` | Produire et contrôler uniquement le candidat neuronal de netteté |
| `lib/restoration.py` | `RestorationComparisonProcessor` | Mutualiser l’orchestration, les scores, les journaux et la publication |

Toutes ces classes héritent de `processor`. Les orchestrateurs concrets déclarent
leurs moteurs avec `parameter_children`, sans instanciation au moment de déclarer
les arguments. Les options neuronales communes sont déclarées une seule fois,
même quand les deux étapes sont enregistrées. Après parsing, la liaison des
paramètres prépare les classes des moteurs qui seront créés lors du traitement.

## Contrat et déroulement

`post_process(input_path, output_path, args=None)` reçoit l’image source et le
chemin de rapport JSON ; les surcharges suivent le contrat de `processor`.
Avec Clarity actif, les deux enfants reçoivent la même image. Chaque moteur
retourne son statut, son candidat, les mesures et les motifs de qualité.
L’orchestrateur retient seulement les candidats acceptés, calcule leurs scores,
copie le gagnant vers la sortie de l’étape et écrit les deux évaluations dans
le rapport global de l’étape. Les scores et leurs limites restent décrits dans
[Cosmic Clarity](cosmic-clarity.md).

Sans Clarity, l’orchestrateur utilise uniquement le moteur Siril et conserve son
rapport historique. Aucun moteur neuronal n’est créé. Un refus de déconvolution
Siril permet d’évaluer l’autre candidat ; un échec technique reste bloquant.
Sans candidat accepté, l’image d’entrée est conservée, avec `rejected` pour le
débruitage ou `no_safe_improvement` pour la déconvolution comparative.

Les moteurs Cosmic Clarity ne lancent plus le traitement Siril concurrent.
Siril reste cependant requis par leur propre contrôle des catalogues stellaires.
Les fonctions historiques `post_process` dans `denoising` et `deconvolution`
restent les points d’entrée algorithmiques compatibles, appelés par les moteurs
Siril. Les imports des orchestrateurs depuis `lib.postprocess` restent disponibles,
mais leurs définitions appartiennent désormais aux modules des traitements.

```python
from lib.denoising import NoiseReductionProcessor
from lib.deconvolution import DeconvolutionProcessor
```

## Vérification

```bash
PIP_NO_INDEX=1 .venv/bin/python -m pytest -q \
  tests/test_restoration.py tests/test_cosmic_clarity.py \
  tests/test_deconvolution.py tests/test_denoising.py
```

Les tests contrôlent les deux candidats sur la même entrée, leur sélection,
la conservation de l’original, les rejets et le mode Siril seul sans moteur
neuronal. Les scénarios comparatifs utilisent des moteurs simulés ; ils ne
constituent pas une validation supplémentaire avec Siril ou CUDA.
