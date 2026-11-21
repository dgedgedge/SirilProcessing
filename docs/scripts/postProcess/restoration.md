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

Avec Clarity, GraXpert et Aberration Remover désactivés, l’orchestrateur utilise
uniquement le moteur Siril.
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

## Sous-étapes dans les journaux

À l’intérieur de l’étape principale `03`, les sous-étapes de restauration sont
annoncées en INFO par un titre entre deux lignes de `-`. Les lignes de `=`
restent réservées aux grandes étapes du pipeline.

```text
------------------------------------------------------------
Restauration — Préparation de la référence et des masques
------------------------------------------------------------
…
------------------------------------------------------------
Restauration — Calcul 01 : Débruitage avec Cosmic Clarity
------------------------------------------------------------
```

Dans la comparaison des chaînes, les titres signalent :

1. La préparation de la référence commune et des masques.
2. Chaque calcul distinct de débruitage ou de netteté, avec son numéro et son
   moteur : Siril, Cosmic Clarity ou GraXpert selon les moteurs actifs.
3. Le contrôle final de chaque variante admissible, identifié par le numéro
   de variante et sa chaîne. Le titre suivant, `Contrôle final : bruit, flux
   et artefacts`, annonce ces mesures après validation de la comparaison stellaire.
4. Le classement et la publication du résultat retenu, ou de l’image d’entrée
   lorsqu’aucune amélioration sûre n’a été retenue.

Une ligne INFO indique la variante courante et le nombre total de variantes.
Le bandeau de chaque nouveau calcul et ses lignes de durée indiquent la
séquence jusqu’au moteur courant, dans l’ordre d’application. Par exemple :

```text
Restauration — Calcul 06 : Netteté avec Aberration Remover
Restauration — Calcul 09 : Netteté avec Aberration Remover → Débruitage avec Cosmic Clarity
```

Ces calculs utilisent deux entrées différentes : l’image de référence pour le
premier, la sortie d’Aberration Remover pour le débruitage du second. La
correction des aberrations précède toujours le débruitage et ne peut pas
être associée à une autre netteté/déconvolution. Une étape future de la variante
n’est pas incluse dans le titre du calcul courant.
Une opération déjà en cache n’affiche pas de nouveau bandeau de calcul : sa
réutilisation est en DEBUG. Un contrôle final déjà en cache n’est pas annoncé
comme un nouveau contrôle. Les étapes non atteintes après rejet ne sont pas
annoncées.

Avec `--no-restoration-compare-chains`, chaque étape de déconvolution ou de
débruitage utilise aussi des sous-titres en `-` pour la préparation de référence,
les évaluations des moteurs et le classement. Le chemin historique Siril seul
en mode `legacy` annonce son évaluation puis la publication de son résultat.
Les mêmes messages apparaissent dans la console et le journal de post-traitement,
depuis postProcess comme depuis lightProcess.

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


Le moteur ONNX [Aberration Remover](aberration.md) complète par défaut les
comparaisons de netteté avec le modèle livré dans `data/`. `--aberration-model`
permet de changer le chemin (une chaîne vide désactive le moteur). Cette page décrit ses
options, son algorithme et ses limites de validation.

## Traçabilité des entrées et références d’évaluation

Dans `RestorationChainProcessor.post_process`, chaque variante commence sur
la référence commune. Après une étape acceptée, `current` devient le chemin
`output_image` renvoyé par ce moteur. L'appel suivant reçoit exactement ce
chemin, ainsi qu'une référence de qualité préparée sur cette entrée effective.
Un rejet ou un échec interrompt la variante ; aucune étape suivante n'est
exécutée sur la référence initiale pour contourner ce rejet.

Le cache d'opération inclut le SHA-256 du fichier d'entrée effectif, l'opération,
la classe du moteur, les paramètres et les dépendances. Une sortie déjà calculée
n'est réutilisée que pour cette clé. Chaque étape du rapport référence un
`operation_id` et son `input_sha256` ; l'opération détaille `input_image`,
`image_path`, `output_image` et ses masques. Ces champs permettent de contrôler
la continuité d'une chaîne même quand ses calculs sont partagés avec une autre.

Les contrôles locaux comparent l'entrée effective et le candidat du moteur.
`reference_for` refuse de réutiliser une référence attachée à un autre chemin.
Les contrôles finaux comparent volontairement le dernier candidat à la
**référence initiale commune**, pour mesurer le gain total de la chaîne sans
additionner les scores locaux. L'usage de cette référence par le contrôle final
ne signifie pas que les moteurs repartent de l'image d'origine.

La publication copie le candidat final gagnant vers `restoration.fits`.
La séquence de post-traitement affecte ensuite cette sortie à `current_path`
et la transmet à AutoGHS. Une éventuelle adaptation de plage pour le rendu
est auditée séparément et conserve le FITS scientifique.

Les tests de chaînes utilisent des signatures de pixels cumulatives pour
vérifier toutes les variantes : image effectivement consommée, référence
locale, empreinte d'entrée, sortie intermédiaire, réutilisation du cache et
référence finale. Ils vérifient la limite de deux traitements et l'absence de
répétition de type, avec et sans ordre inverse, indépendamment d'un champ
astronomique ou d'un modèle réel.

Les [aperçus JPEG AutoStretch de restauration](restoration-previews.md) sont
activés par défaut pour l'entrée, les résultats des moteurs et la sortie finale.
Ils partagent la courbe de l'entrée pour faciliter les comparaisons, sans
modifier les FITS. `--no-restoration-previews` désactive ces exports.

La limite de deux traitements s'applique à toutes les variantes de restauration :
identité, moteur seul, ou une netteté associée à un débruitage. Deux nettetés,
deux débruitages et les chaînes à trois étapes sont interdits. Aberration Remover
participe à la catégorie netteté, en concurrence avec Siril et Cosmic Clarity ;
il reste avant le débruitage lorsqu'il est utilisé.

## Comparer plusieurs répertoires de résultats

Le script [compareRestoration](../compareRestoration/README.md) extrait les
variantes et opérations de plusieurs `restoration.json` dans un tableau HTML
filtrable et un CSV. Il distingue les contrôles finaux des contrôles locaux,
conserve les alertes non bloquantes et ne relance aucun traitement.

Le rapport global des nouvelles comparaisons de chaînes est une synthèse v2.
Les populations scientifiques restent dans les audits locaux et finaux
référencés par `audit_path` : voir
[organisation et compatibilité des rapports](restoration-reports.md).

<a id="restoration-early-stop"></a>

## Arrêt anticipé après Aberration Remover → GraXpert

Avec `--restoration-early-stop` activé par défaut, le premier traitement essayé
est la chaîne **Aberration Remover → GraXpert**, si les deux moteurs sont actifs.
La référence inchangée est enregistrée au préalable sans exécuter de moteur.
La chaîne conserve les contrôles locaux habituels, puis son contrôle final
contre l'entrée originale de restauration.

Les autres essais ne sont pas lancés si, et seulement si :

- le candidat final est accepté ;
- tous les critères finaux actuels sont explicitement `PASS`, y compris les
  diagnostics non bloquants `diffuse` et `stellar_flux`, la significativité,
  l'appariement, la rondeur, les anneaux et les contrôles de bruit ;
- la réduction de FWHM **médiane** est strictement supérieure à **30 %** ;
- le gain de **bruit utile mesuré** est strictement supérieur à **35 %**.

Le gain de netteté est `1 - fwhm_median_ratio`, et le gain de bruit est le
`useful_noise_gain` enregistré dans la décomposition du score. Ces deux
quantités sont sans unité. Pour éviter un dépassement artificiel dû à
l'arrondi de `1 - 0.7`, le premier test compare directement le ratio à
`1 - seuil`. Une égalité exacte à un seuil ne suffit pas.

Un `FAIL`, même non bloquant pour l'acceptation, un `UNKNOWN`, un critère attendu
absent ou une mesure absente/non finie interdit l'arrêt anticipé. Un nouveau
critère final est également contrôlé. Les critères requis sont ceux de la
validation actuelle d'une chaîne de netteté avec débruitage, pas uniquement
les critères bloquants. Cette règle peut donc poursuivre tous les essais même
si les gains de netteté et de bruit dépassent les seuils.

Sinon l'orchestrateur poursuit toutes les autres variantes prévues, avec la
même référence et les mêmes règles de classement. Le cache réutilise les
opérations et contrôles finaux déjà effectués ; AR seul n'est pas recalculé.
Si AR est rejeté localement, GraXpert n'est pas appliqué au candidat refusé,
mais le débruitage seul reste essayé sur l'original. Si un moteur de la chaîne
prioritaire est désactivé, la recherche habituelle reste disponible.

Le service `lib.restoration_early_stop.RestorationEarlyStop`, dérivé de
`ParameterizedComponent`, possède les options et la décision de suffisance ;
il n'effectue aucun calcul d'image. Les paramètres se résolvent suivant
CLI > JSON > défaut et sont mémorisables avec `-S` :

| Option | Défaut | Effet |
|---|---|---|
| `--restoration-early-stop` / `--no-restoration-early-stop` | activé | Priorité à AR → GraXpert et arrêt conditionnel ; désactiver pour retrouver l'ordre et le parcours exhaustifs |
| `--restoration-early-stop-fwhm-gain` | `0.30` | Gain médian de FWHM à dépasser strictement, fraction finie dans `[0,1[` |
| `--restoration-early-stop-noise-gain` | `0.35` | Gain de bruit utile à dépasser strictement, fraction finie dans `[0,1[` |

Le champ `early_stop` du rapport indique l'activation, le déclenchement, les
conditions mesurées, les critères absents ou non PASS, et les nombres de
variantes prévues, évaluées et non exécutées. La synthèse texte et l'index HTML
signalent un arrêt anticipé. Les variantes non exécutées n'ont ni verdict ni
score : elles ne sont jamais présentées comme rejetées. Le résultat retenu
satisfait une condition de suffisance ; il n'est pas démontré meilleur que les
variantes non exécutées. Une reprise `--autoghs-only` ne relance pas cette
recherche de restauration.
