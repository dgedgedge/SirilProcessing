# Séquence de post-traitements

[Guide utilisateur de postProcess](../../../postProcess/README.md)

`_PostProcessorSequence` orchestre une liste ordonnée de `processor`.
Par défaut, cette liste contient `GradientExtractor()`, `PhotometricColorCalibrator()`, `NoiseReductionProcessor()` puis `DeconvolutionProcessor()`. La séquence ne comporte
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

Le programme principal enregistre la séquence avec
`config.register(sequence, parser)`, puis appelle `config.parse_args(parser)`.
Les déclarations restent dans `add_arguments` de chaque traitement.
`parameter_persistence` associe les destinations argparse non rémanentes à
`False` ; les autres options sont rémanentes par défaut. La séquence rassemble
ces déclarations. `config.save_requested(args)` sauvegarde les réglages si
`--save-config` est présent. Les choix d’activation sont également rémanents.


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

    def add_arguments(self, parser):
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
