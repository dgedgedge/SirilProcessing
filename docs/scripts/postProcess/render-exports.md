# Exports JPEG AutoGHS

[postProcess](README.md) › [AutoGHS](auto-ghs.md) › Exports

## Responsabilités et interfaces

[`lib/render_exports.py`](../../../lib/render_exports.py) présente des rendus
déjà non linéaires, sans modifier leur traitement scientifique.
`AutoGHSFinalRenderer` appelle ses utilitaires après les contrôles de fusion.

- `read_render_image(source)` lit un FITS RGB `SPNLIN=True` en unités `[0,1]`.
- `write_render_jpeg(source, destination)` exporte le rendu entier.

## Conversion et orientation

Chaque intensité est arrondie sur 8 bits par `round(255*x)`. Aucun étirement,
gamma, balance de couleurs ou normalisation par les extrema n’est ajouté.
Il s’agit de valeurs RGB de présentation, sans calibration sRGB revendiquée.
`ROWORDER=TOP-DOWN` conserve l’ordre des lignes ; `BOTTOM-UP`, valeur Siril par
défaut si la carte manque, inverse les lignes pour l’affichage écran.

Les JPEG utilisent une qualité de 95, sans sous-échantillonnage des couleurs,
avec les dimensions complètes du FITS. Ils sont compressés avec pertes et ne
conservent pas les données float32, extensions et métadonnées scientifiques du
FITS. Les FITS originaux restent disponibles et ne sont pas modifiés.

<a id="emplacement-et-comparaison-entre-exécutions"></a>

## Emplacement des exports

Les JPEG portent le même nom que les FITS finaux dans `004_recomposition/`.
Le CLI copie le JPEG principal sous `<image>_postprocess.jpg`, près de l’export
FITS principal, lorsque la phase AutoGHS a fourni ce JPEG.

Un seul rendu RANGE est exporté. `003_ghs/` conserve son fond sans étoiles ;
`004_recomposition/` contient le FITS et le JPEG étoilés. Aucun comparatif
AutoGHS n'est généré et aucun ancien rendu n'est chargé en mémoire à cette fin.
Les histogrammes du fond et de l'image étoilée restent dans `005_diagnostics/` ;
ils sont distincts du diagnostic des étoiles seules dans `002_stars/`.

## Erreurs et vérification

Les pixels NaN/Inf, hors `[0,1]`, un format autre que trois plans RGB ou un
ROWORDER inconnu sont refusés. Une erreur d’export courant fait échouer la phase
et produit son rapport `ERROR`.

Les tests de `tests/test_render_exports.py` et `tests/test_final_render.py`
vérifient les conversions, les orientations, les fichiers finaux et la présence
du seul rendu RANGE. Une relance vérifie aussi le nettoyage des anciens produits
PEAK et comparatifs du dossier de travail.

Après ces exports, l’[annotation finale](annotations.md) ajoute un encart de
prise de vue à une copie PNG du rendu principal.
