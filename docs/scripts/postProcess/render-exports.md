# Exports JPEG AutoGHS

[postProcess](README.md) › [AutoGHS](auto-ghs.md) › Exports

## Responsabilités et interfaces

[`lib/render_exports.py`](../../../lib/render_exports.py) présente des rendus
déjà non linéaires, sans modifier leur traitement scientifique.
`AutoGHSFinalRenderer` appelle ses utilitaires après les contrôles de fusion.

- `read_render_image(source)` lit un FITS RGB `SPNLIN=True` en unités `[0,1]`.
- `write_render_jpeg(source, destination)` exporte le rendu entier.
- `write_array_jpeg(data, destination, row_order)` capture les mêmes plans
  natifs pendant les essais, avec la même orientation que l'export final.
- `siril_display_planes(data)` fournit la vue RGB dans le sens de l'affichage
  Siril, sans changer les intensités ni les données FITS.

## Conversion et orientation

Chaque intensité est arrondie sur 8 bits par `round(255*x)`. Aucun étirement,
gamma, balance de couleurs ou normalisation par les extrema n’est ajouté.
Il s’agit de valeurs RGB de présentation, sans calibration sRGB revendiquée.
L'orientation de référence est **l'affichage natif dans Siril** : la première
ligne du FITS se trouve en bas de l'image. La conversion vers JPEG inverse donc
les lignes pour `TOP-DOWN`, pour `BOTTOM-UP` et lorsque `ROWORDER` manque.
`ROWORDER` décrit le stockage et intervient notamment dans le dématriçage ; il
ne commande pas à Siril de retourner automatiquement l'affichage RGB.
Voir la [documentation officielle Siril](https://siril.readthedocs.io/en/stable/file-formats/FITS.html#orientation-of-fits-images).

Les JPEG finaux, les captures d'essais, les extraits stellaires et les PNG
annotés utilisent ce même sens haut/bas. Les positions et contours WCS des
annotations sont convertis vers cette vue après projection. Le WCS et les
pixels des FITS ne sont pas modifiés, et aucune rotation « nord en haut »
n'est ajoutée.

Les JPEG utilisent une qualité de 95, sans sous-échantillonnage des couleurs,
avec les dimensions complètes du FITS. Ils sont compressés avec pertes et ne
conservent pas les données float32, extensions et métadonnées scientifiques du
FITS. Les FITS originaux restent disponibles et ne sont pas modifiés.

<a id="emplacement-et-comparaison-entre-exécutions"></a>

## Emplacement des exports

Les JPEG portent le même nom que les FITS finaux dans `04_recomposition/`.
Le CLI copie le JPEG principal sous `<image>_postprocess.jpg`, près de l’export
FITS principal, lorsque la phase AutoGHS a fourni ce JPEG.

Un seul rendu RANGE est exporté. `03_ghs/` conserve son fond sans étoiles ;
`04_recomposition/` contient le FITS et le JPEG étoilés. Aucun comparatif
AutoGHS n'est généré et aucun ancien rendu n'est chargé en mémoire à cette fin.
Les histogrammes du fond et de l'image étoilée restent dans `05_diagnostics/` ;
ils sont distincts du diagnostic des étoiles seules dans `02_stars/`.

## Erreurs et vérification

Les pixels NaN/Inf, hors `[0,1]`, un format autre que trois plans RGB ou un
ROWORDER inconnu sont refusés. Une erreur d’export courant fait échouer la phase
et produit son rapport `ERROR`.

Les tests de `tests/test_render_exports.py` et `tests/test_final_render.py`
vérifient les conversions, les orientations, les fichiers finaux et la présence
du seul rendu RANGE. Des repères colorés asymétriques contrôlent les coins
pour les deux ROWORDER, la concordance des captures avec les exports finaux
et le placement WCS sur les pixels réellement affichés. La convention a aussi
été confrontée à `savepng` dans une session Siril indépendante. Une relance vérifie aussi le nettoyage des anciens produits
PEAK et comparatifs du dossier de travail.

Après ces exports, l’[annotation finale](annotations.md) ajoute un encart de
prise de vue à une copie PNG du rendu principal.
