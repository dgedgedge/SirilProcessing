# Classes de restauration et orchestration

[postProcess](README.md) · [Cosmic Clarity](cosmic-clarity.md)

La séquence connaît les étapes gradient, photométrie, déconvolution et débruitage.
Elle conserve leurs instances et ne sélectionne pas leurs moteurs internes.
Par défaut, elle remplace les deux dernières étapes par
`RestorationChainProcessor`. Chaque orchestrateur organise ses propres enfants.
`--no-restoration-compare-chains` retrouve les comparaisons par étape.
Le service `RestorationSummary` produit une
[synthèse des essais et des éliminations](quality.md#synthèse-des-essais-et-des-éliminations)
à partir des audits, sans recalculer les verdicts.

| Module | Classe | Responsabilité |
|---|---|---|
| `lib/denoising.py` | `NoiseReductionProcessor` | Organiser le débruitage et sélectionner un candidat |
| `lib/denoising.py` | `SirilNoiseReductionProcessor` | Produire et contrôler uniquement le candidat Siril |
| `lib/deconvolution.py` | `DeconvolutionProcessor` | Organiser la déconvolution et sélectionner un candidat |
| `lib/deconvolution.py` | `SirilDeconvolutionProcessor` | Produire et contrôler uniquement les essais Siril |
| `lib/cosmic_clarity/processors.py` | `CosmicClarityDenoiseProcessor` | Produire et contrôler uniquement le candidat neuronal de débruitage |
| `lib/cosmic_clarity/processors.py` | `CosmicClaritySharpenProcessor` | Produire et contrôler uniquement le candidat neuronal de netteté |
| `lib/graxpert.py` | `GraXpertDenoiseProcessor` | Débruitage ONNX natif, automatique si des poids locaux valides sont présents |
| `lib/restoration_chains.py` | `RestorationChainProcessor` | Comparer les chaînes finales sur une référence commune |
| `lib/restoration.py` | `RestorationComparisonProcessor` | Mutualiser l’orchestration, les scores, les journaux et la publication |

Toutes ces classes héritent de `processor`. Les orchestrateurs concrets déclarent
leurs moteurs avec `parameter_children`, sans instanciation au moment de déclarer
les arguments. Les options neuronales communes sont déclarées une seule fois,
même quand les deux étapes sont enregistrées. Après parsing, la liaison des
paramètres prépare les classes des moteurs qui seront créés lors du traitement.

## Contrat et déroulement

`post_process(input_path, output_path, args=None)` reçoit l’image source et le
chemin de rapport JSON ; les surcharges suivent le contrat de `processor`.
Les moteurs actifs reçoivent la même image et sont exécutés séquentiellement :
Cosmic Clarity, Siril, puis GraXpert pour le débruitage lorsqu’il est activé.
Chaque moteur
retourne son statut, son candidat, les mesures et les motifs de qualité.
L’orchestrateur retient seulement les candidats acceptés, calcule leurs scores,
copie le gagnant vers la sortie de l’étape et écrit les évaluations dans
le rapport de l’étape. En mode fiable, le gagnant doit avoir un score strictement
positif pour battre l’identité. Les scores sont décrits dans
[Cosmic Clarity](cosmic-clarity.md#score-de-sélection-utilisé), et les masques,
incertitudes et états UNKNOWN dans [Qualité astrophotographique](quality.md).

Sans Clarity ni GraXpert, l’orchestrateur utilise uniquement le moteur Siril.
Le rapport historique direct reste disponible en mode `legacy`. Aucun moteur neuronal n’est créé. Un refus de déconvolution
Siril permet d’évaluer l’autre candidat. En mode fiable, un échec technique d’un
moteur est audité puis les autres moteurs sont essayés ; le mode `legacy`
conserve le contrat historique des erreurs.
Sans candidat accepté, l’image d’entrée est conservée, avec `rejected` pour le
débruitage ou `no_safe_improvement` pour la déconvolution comparative.

Les moteurs Cosmic Clarity ne lancent plus le traitement Siril concurrent.
Siril reste cependant requis par leur propre contrôle des catalogues stellaires.
Les paramètres sont déclarés par les méthodes de classe des moteurs Siril.
Les moteurs créent directement un objet d’exécution par appel. Les modules
`denoising` et `deconvolution` n’exposent pas de fonctions globales
`add_arguments` ou `post_process` : utiliser les méthodes des classes. Les imports des orchestrateurs depuis `lib.postprocess` restent disponibles,
mais leurs définitions appartiennent désormais aux modules des traitements.

```python
from lib.denoising import NoiseReductionProcessor
from lib.deconvolution import DeconvolutionProcessor, SirilDeconvolutionProcessor
from lib.denoising import SirilNoiseReductionProcessor

# Déclarer les paramètres du moteur Siril sans créer d’instance :
SirilNoiseReductionProcessor.add_arguments(parser)

# Exécuter uniquement le moteur Siril avec les arguments déjà résolus :
result = SirilNoiseReductionProcessor().post_process(image, report, args)
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

<a id="siril-execution-phases"></a>

## État et phases d’une exécution Siril

`_DenoisingExecution` et `_DeconvolutionExecution` sont des objets internes à
usage unique, créés par les moteurs `processor`. Ils portent les options copiées,
les chemins, les rapports et les étapes de l’appel ; ils ne sont pas des moteurs
publics supplémentaires et ne sont pas des singletons.

Le débruitage sépare construction du script, exécution Siril, évaluation de
qualité, publication et sauvegarde de l’audit. La déconvolution sépare détection,
construction de PSF, exécution et évaluation d’un essai, essai initial, recherche
adaptative, publication et audit. Ces méthodes remplacent les fonctions imbriquées
qui partageaient implicitement les variables de leur fonction englobante.

L’élargissement du recadrage utilise une fraction de dimension distincte de la
fraction minimale d’appariement. Élargir une zone ne change donc pas le seuil
configuré de correspondances. Le test de régression vérifie ce contrat.

Deux appels sur la même instance de moteur créent des états différents. Les
chemins de rapports doivent toutefois être distincts pour des exécutions
concurrentes : leur dossier de travail est déterminé par le rapport et recréé
au début de chaque appel. Les erreurs d’exécution restent propagées et auditées.

Les nouveaux masques, états UNKNOWN, diagnostics photométriques, options GraXpert
et règles des chaînes sont décrits dans [Qualité astrophotographique](quality.md).

Le moteur [GraXpert natif](graxpert.md) déclare ses services d’inférence et de
modèles dans la même hiérarchie de paramètres.
