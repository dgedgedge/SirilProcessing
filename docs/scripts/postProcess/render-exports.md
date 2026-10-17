# Exports JPEG et comparatifs AutoGHS

[postProcess](README.md) › [AutoGHS](auto-ghs.md) › Exports

## Responsabilités et interfaces

[`lib/render_exports.py`](../../../lib/render_exports.py) présente des rendus
déjà non linéaires, sans modifier leur traitement scientifique.
`AutoGHSFinalRenderer` appelle ses utilitaires après les contrôles de fusion.

- `read_render_image(source)` lit un FITS RGB `SPNLIN=True` en unités `[0,1]`.
- `write_render_jpeg(source, destination)` exporte le rendu entier.
- `load_previous_previews(report_path)` capture les rendus précédents en mémoire,
  avant que l’orchestrateur réinitialise son dossier de travail.
- `write_render_comparisons(reports, directory, stem, previous)` produit les
  comparatifs séparés sans étoiles et avec étoiles.

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

## Emplacement et comparaison entre exécutions

Les JPEG portent le même nom que les FITS finaux dans `004_recomposition/`.
Le CLI copie le JPEG principal sous `<image>_postprocess.jpg`, près de l’export
FITS principal, lorsque la phase AutoGHS a fourni ce JPEG.

`003_ghs/` contient deux PNG : `<image>_ghs_comparison_starless.png` et
`<image>_ghs_comparison_with_stars.png`. Chaque colonne représente une stratégie.
Une ligne précédente est ajoutée si cette catégorie possède un ancien rendu
exploitable ; une image manquante est explicitement indiquée. Le premier
lancement montre seulement les variantes actuelles.

Les aperçus sont limités à 1024 pixels de côté avec un redimensionnement Lanczos,
en conservant leurs proportions. Le même affichage direct est utilisé pour
toutes les images, afin d’éviter qu’un auto-étirement masque les différences.
Les comparatifs entre exécutions reflètent aussi les changements des étapes
amont ; ils ne prouvent pas l’effet isolé d’un paramètre particulier.

## Erreurs et vérification

Les pixels NaN/Inf, hors `[0,1]`, un format autre que trois plans RGB ou un
ROWORDER inconnu sont refusés. Une erreur d’export courant fait échouer la phase
et produit son rapport `ERROR`. Un ancien rendu absent ou illisible est signalé
dans le journal et ne bloque pas la nouvelle exécution.

Les tests de `tests/test_render_exports.py` et `tests/test_final_render.py`
vérifient les conversions, les orientations, les fichiers finaux, le respect
des catégories et la capture de l’historique avant réinitialisation.
