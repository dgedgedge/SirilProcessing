# Séquence de post-traitements

[Guide utilisateur de postProcess](../../../postProcess/README.md)

`_PostProcessorSequence` orchestre une liste ordonnée de `processor`.
Par défaut, cette liste contient `GradientExtractor()`, `PhotometricColorCalibrator()`, `DeconvolutionProcessor()` puis `NoiseReductionProcessor()`. La séquence ne comporte
aucune branche dépendant de la classe concrète d'un traitement.

## Contrat commun

Un processeur hérite de `processor` et expose :

- `get_prefix()` : identifiant unique, utilisé pour les options et les rapports ;
- `add_arguments(parser)` : ajoute ses options sous la forme `--<prefixe>-<option>` ;
- `set_from_args(args)` : mémorise une copie indépendante des paramètres parsés ;
- `post_process(input_path, output_path, args=None)` : reçoit l'image courante,
  le **chemin de son rapport JSON** et les arguments déjà parsés ; retourne un
  dictionnaire sérialisable en JSON.

La séquence ajoute automatiquement `--enable-<prefixe>` et `--disable-<prefixe>`.
L’attribut `enabled_by_default` du processeur détermine son activation par défaut
(`True` pour les quatre traitements). Un appel Python sans `args` utilise
la copie mémorisée par `set_from_args`, puis les paramètres partagés de `Config`
si aucune copie locale n’a été fournie. Les défauts locaux du traitement
s’appliquent aux paramètres absents.
Passer `args` à `post_process` remplace cette configuration uniquement pour cet appel.
Un traitement ne doit pas reparcourir `sys.argv`.

```python
traitement.set_from_args(args)
rapport = traitement.post_process(image, chemin_rapport)
```

La séquence expose également `set_from_args(args)` et configure chacun de ses traitements.

## Résolution et liaison hiérarchique des paramètres

`ParameterizedComponent` (`lib.parameterized_component`) porte la déclaration
abstraite `add_arguments`, les enfants `parameter_children`, la liaison
`bind_parameters`, la persistance et les instantanés indépendants des instances.
Cette base n’exige aucun traitement d’image. `processor` en hérite et ajoute
`post_process` ; `Siril` en hérite directement pour utiliser les mêmes paramètres.
Les nouveaux services recevant des options peuvent donc hériter de cette base,
être enregistrés avec `Config.register`, puis configurés par
`Config.bind_parameters(args)` avant leur instanciation.

`Siril()` choisit les valeurs explicites de son constructeur, puis les valeurs
liées, puis ses défauts globaux, et valide l’exécutable. Une nouvelle liaison ne
modifie pas les instances déjà créées. `Siril.create_with_defaults()` utilise
explicitement les défauts globaux pour les appelants qui demandent ce comportement.

Les scripts créent directement `Siril()` après la liaison, ou
`Siril(siril_path=args.siril_path, siril_mode=args.siril_mode)` pour transmettre
les réglages d’un appel explicite. Ils exécutent ensuite le script par la méthode
`service.run_siril_script(contenu, dossier, script_name="traitement.sps")`.
Il n’existe plus de fonction de module pour exécuter un script ou créer le service.


Les méthodes `add_arguments` des traitements standard sont des méthodes de
classe. Le programme principal déclare les classes avant de créer les instances :

```python
import argparse
from lib.config import Config
from lib.postprocess import _PostProcessorSequence

config = Config("settings.json")
parser = argparse.ArgumentParser()
config.add_arguments(parser)
config.register(_PostProcessorSequence, parser)
args = config.parse_args(parser, ["--denoise-modulation", "0.25"])
config.bind_parameters(args)
sequence = _PostProcessorSequence()
```

`parse_args` applique la priorité CLI > fichier JSON > défaut local et conserve
les valeurs dans `Config`. `bind_parameters` interroge ensuite `args` pour
capturer les destinations propres de chaque classe et les paramètres communs
Siril et de travail. Les enfants sont déclarés par `parameter_children`, y
compris les moteurs Cosmic Clarity susceptibles d’être créés tardivement.
Leurs paramètres incluent les réglages neuronaux et les seuils de qualité des
deux moteurs. Les constructeurs des traitements ne sont pas exécutés pendant
cet enregistrement.

Chaque instance créée après la liaison reçoit une copie indépendante des valeurs
de sa classe. Modifier `args`, résoudre d’autres arguments ou refaire la liaison
ne change pas les instances existantes. Les traitements ne sont pas des
singletons. `ProcessorParameters` est une enveloppe dataclass figée et typée
avec `ConfigValue`, avec un instantané propre à chaque classe ; elle restitue des copies de
Namespace pour préserver les API existantes. Les valeurs ne sont pas encore des
champs d’une dataclass métier différente pour chaque algorithme.

Les surcharges restent prioritaires : `args` explicite pour un appel, puis
`set_from_args` sur l’instance, puis instantané de classe. Les instances créées
sans liaison conservent le repli historique vers `Config.arguments()`.
Les options du constructeur du gradient restent utilisées lorsque ces valeurs
ne sont pas remplacées par les paramètres résolus. L’enregistrement d’une
instance avec `config.register(instance, parser)` conserve ses défauts de
constructeur ; les séquences personnalisées restent déclarées par instance.

`parameter_persistence` associe les destinations argparse non rémanentes à
`False` ; les autres options sont rémanentes par défaut. La séquence rassemble
ces déclarations. `config.save_requested(args)` sauvegarde les réglages si
`--save-config` est présent. La liaison ne sauvegarde rien. Les choix
d’activation sont également rémanents.


La séquence écrit les rapports individuels dans `<rapport>_steps/<index>_<prefixe>.json`,
puis le rapport global `{ "prefixe": { ... }, ... }` au chemin demandé par le CLI.
Le processeur peut aussi écrire son propre rapport pour permettre l'usage autonome.

## Passage des images

`GradientExtractor.post_process()` produit un FITS flottant
`<préfixe><image>_gradient_corrected.fits` et le transmet via `output_image`.
La méthode est sélectionnée par `--gradient-method rbf|polynomial` (défaut :
`rbf`), ou `GradientExtractor(method="rbf")` en Python. `set_from_args()` accepte
le champ `gradient_method`.

Les valeurs par défaut reprennent la capture de configuration Siril :

| Paramètre | Option | Défaut |
| --- | --- | --- |
| Méthode | `--gradient-method` | `rbf` |
| Lissage | `--gradient-smoothing` | `0.50` |
| Échantillons par ligne | `--gradient-samples-per-line` | `20` |
| Tolérance de grille | `--gradient-grid-tolerance` | `2.00` |
| Conserver tous les échantillons | `--gradient-keep-all-samples` | désactivé |
| Correction | soustraction | fixe |
| Dithering | aucun | fixe |

RBF interpole les médianes locales avec le noyau `thin_plate_spline`.
Le nombre de lignes suit le rapport hauteur/largeur : une image carrée reçoit
400 cellules avant rejet. Une cellule est rejetée si sa médiane dépasse la
médiane globale plus la tolérance multipliée par une estimation robuste du sigma
(1,4826 × MAD). La conservation forcée désactive ce rejet des cellules brillantes.
Les pixels non finis restent exclus. Au moins quatre cellules valides réparties
en deux dimensions sont nécessaires. L'image de contrôle montre tous les points
retenus du premier canal, encadrés en vert.

L'implémentation utilise SciPy sur des coordonnées normalisées ; le lissage
est son coefficient de régularisation. Les valeurs de réglage correspondent
à la capture, mais la grille et le calcul ne reproduisent pas exactement le
moteur de Siril. Voir la [documentation Siril](https://siril.readthedocs.io/en/stable/processing/background.html).
Les options d'ordre, de fraction de pixels et `--gradient-max-samples` concernent
le mode polynomial.

En mode polynomial, le degré recommandé par l'analyse est ajusté séparément
sur chaque canal, avec rejet itératif des valeurs aberrantes. Les deux méthodes
soustraient la variation du fond en conservant son niveau au centre, sans
écrêtage des pixels. Les métadonnées WCS et les extensions FITS sont conservées.
Les méthodes `extract_gradient_from_fits()` et `extract_gradient_all_orders()`
restent des API d'analyse polynomiale seule.

Les nébulosités étendues peuvent influencer le fond estimé par les deux méthodes.
Les sources restent intactes pour permettre la comparaison avec les images corrigées.

Un traitement d'analyse laisse l'image courante inchangée. Un traitement produisant
une nouvelle image retourne `"output_image": "/chemin/image.fit"` dans son résultat.
Un chemin relatif est résolu depuis le dossier du rapport individuel. La séquence
vérifie que le fichier existe et le transmet au prochain traitement actif.
Le chemin d'un rapport JSON n'est jamais utilisé comme entrée image.

Une exception ou une clé `error` dans un résultat interrompt la séquence en
identifiant le traitement concerné. Les sorties déjà produites restent disponibles.

## Ajouter un traitement

```python
from pathlib import Path
from lib.processor import processor
from lib.postprocess import GradientExtractor, _PostProcessorSequence

class NouveauTraitement(processor):
    def get_prefix(self):
        return "nouveau"

    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument("--nouveau-intensite", type=float, default=1.0)

    def post_process(self, input_path, output_path, args=None):
        args = self._get_args(args)
        intensite = getattr(args, "nouveau_intensite", 1.0)
        # Exécuter ici le traitement et retourner des valeurs sérialisables.
        # Pour transmettre une image produite : ajouter output_image au résultat.
        return {"intensite": intensite, "image_path": str(input_path)}

sequence = _PostProcessorSequence([GradientExtractor(), NouveauTraitement()])
```

Pour intégrer ce traitement au CLI, ajouter son instance à la liste par défaut
construite dans `_PostProcessorSequence.__init__`. Ses options sont alors exposées
sans modification du script CLI ni de la boucle d'exécution.

Les candidats utilisent par défaut la [mesure stellaire aux positions initiales](../../../postProcess/reference-stars.md), avec un diagnostic distinct des catalogues de détection.

La séquence conserve les instances de ses étapes. `DeconvolutionProcessor` et
`NoiseReductionProcessor` déclarent leurs propres moteurs enfants et organisent
leur comparaison. Voir [les classes de restauration](../../../postProcess/restoration.md).
