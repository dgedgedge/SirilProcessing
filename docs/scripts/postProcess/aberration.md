# Correction des aberrations avec Aberration Remover

[postProcess](README.md) · [Restauration](restoration.md)

## 1. Utilisation

Le moteur `AberrationRemoverProcessor` ajoute un candidat de netteté aux
comparaisons de restauration. Selon la
[recommandation de l’auteur du modèle 2.5.0](https://github.com/riccardoalberghi/abberation_models/releases/tag/2.5.0),
il doit intervenir sur l’image linéaire après correction du fond et des couleurs,
**avant tout débruitage ou autre déconvolution**.

En comparaison des chaînes, Aberration Remover est un moteur de netteté à
part entière. Il est essayé seul ou suivi d'un débruitage. Il ne peut pas être
associé à une déconvolution Siril ou à la netteté Cosmic Clarity : cela
répéterait une étape de netteté. Toutes les chaînes comportent au plus deux
traitements, avec au plus une netteté et un débruitage.
`--restoration-reverse-order` ne déplace pas Aberration Remover après le
débruitage ; cette option concerne seulement les autres moteurs de netteté.
Les contrôles locaux et finaux ainsi que la comparaison avec l’identité sont
conservés.
Sans comparaison des chaînes, la séquence standard évalue les candidats de
netteté avant le débruitage.
Il utilise par défaut le modèle livré dans
`data/AberrationRemover/model_v2_5_0.onnx`, avec un chemin absolu calculé depuis
l'emplacement du module, indépendant du dossier de lancement. Il est donc
inclus par défaut dans la comparaison lorsque la déconvolution est activée.
`--aberration-model /chemin/modele.onnx` sélectionne un autre modèle ;
`--aberration-model ""` désactive ce moteur.
Les seuils de contrôle stellaire et d'artefacts sont ceux de la déconvolution ;
un résultat refusé ne remplace pas l'entrée.

Installer ONNX Runtime dans l'environnement Python utilisé, comme pour
GraXpert, et fournir un modèle compatible issu des
[modèles de Riccardo Alberghi](https://github.com/riccardoalberghi/abberation_models/releases).
Le programme ne télécharge aucun modèle automatiquement.

Le modèle **2.5.0** est conservé sans modification dans
`data/AberrationRemover/model_v2_5_0.onnx`. Sa
[notice de provenance et de licence](../../../data/AberrationRemover/README.md)
contient l'empreinte SHA-256 et la commande de vérification.
Il est dû à **Riccardo Alberghi** et distribué sous **CC BY-NC-SA 4.0**
(usage non commercial), distincte de la GPL du code ; voir les
[licences et remerciements](../../THIRD_PARTY.md).
Une valeur sauvegardée dans la configuration ou fournie en CLI reste prioritaire.

Options persistantes (CLI prioritaire sur configuration JSON puis défauts) :

| Option | Défaut | Effet |
| --- | --- | --- |
| `--aberration-model` | chemin absolu vers `data/AberrationRemover/model_v2_5_0.onnx` | Modèle livré ; chemin vide pour désactiver |
| `--aberration-strength` | `1.0` | Mélange entre entrée et correction, entre 0 et 1 |
| `--aberration-dark-ringing` | faux | Réduction des anneaux sombres |
| `--aberration-protect-background` | faux | Protection des pixels sous le seuil du fond |

La politique `--compute-backend cpu` impose CPU. Avec `auto`, CUDA est choisi
s'il est disponible dans ONNX Runtime, sinon CPU. CoreML est exclu.
Les pannes de calcul sont auditées sans reprise silencieuse sur CPU.

Depuis la racine du dépôt, ajouter ces arguments à une commande postProcess existante :

```text
--aberration-model data/AberrationRemover/model_v2_5_0.onnx --aberration-strength 0.5 --aberration-protect-background
```

Le candidat FITS, les catalogues stellaires et le rapport JSON suivent les
[conventions des moteurs de restauration](restoration.md). La source est
préservée. Le rapport d'inférence indique le chemin du modèle, les fournisseurs
ONNX actifs et l'intensité. Le modèle est inclus dans l'empreinte du cache des
chaînes. Un modèle absent, une forme ONNX incompatible ou des pixels non finis
font échouer ce candidat et sont enregistrés par l'orchestrateur.

## 2. Module et algorithme

`lib/aberration.py` sépare `AberrationEngine` (calcul indépendant) et
`AberrationRemoverProcessor` (candidat avec contrôles communs). Le moteur expose
`process(source, candidate)` pour les FITS et `restore(data, session, input_name,
...)` pour des tableaux CHW normalisés. L'adaptateur réutilise le cycle de
contrôle du processeur neuronal existant via sa méthode `_infer`.

La référence est le
[script Siril AberrationRemover](https://gitlab.com/free-astro/siril-scripts/-/blob/main/processing/AberrationRemover.py),
version 1.1.4 consultée. L'implémentation reproduit ses opérations numériques :

1. Lire un FITS linéaire flottant ou uint16, mono ou RGB avec canaux en premier.
   Les uint16 sont divisés par 65535 avant calcul et remis dans leurs unités
   d'origine dans une sortie flottante.
2. Traiter chaque canal séparément par tuiles de 512 × 512 pixels avec
   recouvrement de 64 pixels et dernière tuile alignée sur le bord.
3. Exécuter le modèle avec entrée et sortie `[1, 1, 512, 512]`.
   Pour chaque tuile, ramener la prédiction à la médiane et à la MAD de l'entrée :
   `sortie = (prédiction − médiane_prédiction) / MAD_prédiction × MAD_entrée + médiane_entrée`.
   Les MAD sont bornées inférieurement à `1e-8` (unités normalisées).
4. Assembler par fenêtre de Hann, bornée à `1e-3`, et diviser par les poids.
5. Option anneaux sombres : remplacer par l'entrée les prédictions sous le
   percentile local 2 %, fenêtre de 15 pixels ; à partir de 100 pixels sur
   chaque axe, sous-échantillonner par 2, filtrer sur 7 pixels et interpoler.
6. Option fond : conserver les pixels sous `médiane + 1.4826 × MAD`, estimées
   sur un pixel sur quatre dans chaque direction, séparément par canal.
7. Mélanger selon l'intensité puis borner à `[0, 1]` avant restitution des unités.

Contrairement au script, les images de moins de 512 pixels sont complétées
par réflexion, puis recadrées ; les prédictions non finies sont rejetées.
Une intensité nulle restitue exactement les pixels d'entrée, sans inférence
par tuiles. Le modèle est néanmoins chargé par le traitement FITS.
La méthode vise les aberrations optiques sur données linéaires ; elle ne
récupère pas une information absente ou saturée et ne garantit pas une
amélioration de chaque champ. Les seuils de netteté actuels peuvent refuser
une amélioration de forme sans gain de FWHM.

Le traitement est séquentiel par canal et par tuile. Les tableaux d'entrée,
de sortie et de pondération occupent une mémoire proportionnelle au nombre
de pixels ; la mémoire du modèle s'y ajoute.

## 3. Vérifications et limites

`tests/test_aberration.py` vérifie le calcul avec des sessions ONNX simulées,
les statistiques locales, les bordures, les protections, les erreurs, les
unités et métadonnées FITS, ainsi que l'ajout aux chaînes. Exécuter :

```bash
.venv/bin/python -m pytest -q tests/test_aberration.py
```

Ces tests ne valident pas la qualité astronomique du modèle réel. Avant un
usage scientifique, comparer sur un FITS linéaire avec le même modèle dans
Siril, examiner les jointures et les étoiles aux bords, puis vérifier les
verdicts du rapport de restauration. Cette validation réelle n'a pas été
réalisée lors de l'intégration.

Pour CUDA, le moteur précharge les bibliothèques NVIDIA présentes dans le venv
avec `onnxruntime.preload_dlls(directory='')` lorsque cette API est disponible,
avant de créer la session, comme GraXpert. Le chargement ne dépend donc pas de
l'ordre des moteurs. Cela ne remplace pas l'installation des dépendances CUDA
et cuDNN si celles-ci sont absentes du venv.
