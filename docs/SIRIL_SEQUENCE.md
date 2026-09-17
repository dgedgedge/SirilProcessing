# Manipulation des séquences Siril

Le module [lib/siril_sequence.py](../lib/siril_sequence.py) centralise la lecture,
l’écriture et la modification des fichiers `.seq`. Toute manipulation d’une
image appartenant à une séquence passe par un objet `SequenceImage` obtenu depuis
`SirilSequence`.

## Objets disponibles

| Objet | Responsabilité |
|---|---|
| `SirilSequence` | Chemin du `.seq`, en-tête, ordre des images, lecture, écriture atomique et duplication |
| `SequenceImage` | Numéro de fichier, inclusion, fichiers associés, données d’alignement par canal et statistiques |
| `RegistrationData` | FWHM, FWHM pondérée, rondeur, qualité, fond de ciel, nombre d’étoiles et matrice d’alignement |

Le périmètre est celui des séquences **FITS en fichiers individuels**, formats
`.seq` 4 à 7. Les conteneurs SER, vidéo et FITSEQ (`TS`, `TA`, `TF`) sont refusés
explicitement. Une séquence calibrée peut ne contenir aucune mesure d’alignement :
le module n’en invente pas.

## Lecture et fichiers transmis aux traitements

```python
from lib.siril_sequence import SirilSequence

sequence = SirilSequence.read('/chemin/01_registration/light_.seq')
image = sequence.images[0]

print(image.number)                 # numéro du FITS, distinct de son indice dans la liste
print(image.included)               # booléen de sélection
print(image.filename)               # fichier FITS de cette entrée de séquence
print(image.processing_path)        # fichier à passer au traitement sur les pixels natifs

registration = image.registration() # premier canal d’alignement présent
print(registration.fwhm)
print(registration.weighted_fwhm)
print(registration.number_of_stars)
print(registration.homography)      # tableau NumPy 3 × 3
```

`image.registration('R1')` choisit explicitement un canal ; `R*` est également
pris en charge. Une mesure manquante provoque une `ValueError`.

Le nom FITS est reconstruit à partir du préfixe, du numéro et de la largeur de
numérotation de l’en-tête. Le module cherche `.fit`, `.fits`, `.fts`, `.fit.fz`
et `.fits.fz`. Plusieurs correspondances provoquent une erreur d’ambiguïté.
Si aucun fichier n’existe, le chemin attendu est fourni avec l’extension `.fit` ;
la lecture des métadonnées reste possible, mais un traitement de pixels doit
évidemment disposer du FITS. Le module ne renomme pas les fichiers.

Lorsque la séquence contient les sorties de conversion Siril, on peut lui
associer les fichiers calibrés d’origine, **dans le même ordre** :

```python
sequence = SirilSequence.read(sequence_path, source_files=calibrated_paths)
# Ou, après lecture :
sequence.bind_sources(calibrated_paths)

for image in sequence.images:
    traiter(image.processing_path)
```

`filename` reste le FITS de la séquence ; `source_path` désigne le fichier source
fourni ; `processing_path` renvoie `source_path` s’il est défini, sinon `filename`.
Le nombre de fichiers sources doit correspondre au nombre d’entrées. L’appelant
est responsable de leur correspondance par ordre.

## Modification et écriture

```python
image = sequence.images[0]
image.included = False
image.registration('R0').roundness = 0.85
sequence.write()
```

`write()` met à jour les nombres total et inclus dans l’en-tête `S`, écrit les
lignes `I`, les mesures `R` et les statistiques `M`. Les lignes d’alignement non
modifiées conservent leur représentation numérique d’origine. Les commentaires,
les cartes de distorsion et les autres lignes non interprétées sont préservés.
Les statistiques d’une même couche sont regroupées à l’écriture.

Les statistiques sont accessibles par canal, sous forme de charge utile Siril :
`image.statistics['M0']`. Elles restent liées à l’image et leur indice dans le
fichier est calculé à l’écriture.

L’écriture utilise un fichier temporaire dans le même dossier, puis un
remplacement atomique. Un échec de remplacement laisse le `.seq` initial intact.
`sequence.write(other_path)` écrit une copie de ses métadonnées : cela ne copie
pas les FITS et ne déplace pas l’objet en mémoire vers ce nouveau dossier.
Pour travailler sur cette copie, préparer ses FITS puis relire le nouveau `.seq`.

La collection `sequence.images` expose un ordre stable et ne permet pas de
retirer ou de réordonner directement les entrées. Pour exclure une image,
modifier `included`. Cela préserve les indices de référence, les informations
d’astrométrie et les statistiques de recouvrement conservées dans le fichier.

## Duplication pour la pondération

```python
image = sequence.images[0]
copy = sequence.duplicate(image)
sequence.write()
```

`duplicate()` attribue un nouveau numéro, crée un lien symbolique vers le FITS
et ajoute une entrée en fin de séquence. La copie possède ses propres objets de
mesures pour **tous les canaux**, sa matrice et ses statistiques ; les modifier
ne change pas l’original. Le fichier source associé est conservé. Un fichier
existant au nom de destination n’est jamais écrasé. La duplication hérite de
l’état d’inclusion de l’image.

Le lien est créé immédiatement ; `write()` enregistre ensuite les métadonnées.
Si cette écriture échoue, le lien peut rester présent, mais le `.seq` initial
n’est pas partiellement écrasé. Aucune donnée pixel n’est dupliquée ou modifiée.

## Création d’une séquence calibrée

```python
sequence = SirilSequence.from_files(
    output_dir / 'pp_light_.seq',
    prefix='pp_light_',
    files=calibrated_paths,
    layers=3,
)
sequence.write()
```

Les fichiers doivent partager le préfixe et la largeur de numérotation. Leurs
numéros réels sont conservés, même s’ils ne sont pas consécutifs. L’en-tête et les
entrées incluses sont créés sans statistiques ni alignements artificiels.

## Utilisation dans le traitement

- `save_calibrated_sequence()` lit/écrit ou crée les séquences via `SirilSequence`.
- `run_stack()` ouvre la séquence et fournit des `SequenceImage` à la sélection,
  aux contrôles géométriques et aux pondérations.
- `quality_mask()` extrait les mesures des objets pour les calculs NumPy.
- `filter_stellar_profiles()` transmet les objets à `measure_stellar_profiles()`,
  qui lit les pixels natifs depuis `processing_path` pour mesurer R80 et allongement.
- `apply_quality_weights()` modifie `included`, appelle `duplicate()` puis `write()`.

`read_registration()` dans `drizzle.py` reste un adaptateur de compatibilité
pour les anciens consommateurs : il délègue toute lecture à `SirilSequence`.
Le traitement principal n’utilise plus ses listes de lignes brutes. Les tests
purement numériques de `quality_mask()` peuvent toujours fournir des tableaux
de mesures sans fichiers de séquence.

Les tests du module se lancent avec :

```bash
.venv/bin/python -m pytest tests/test_siril_sequence.py -v
```
