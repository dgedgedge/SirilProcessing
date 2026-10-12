# Mesures aux positions stellaires initiales

[postProcess](README.md) · [Cosmic Clarity](cosmic-clarity.md)

## Objectif et activation

Par défaut, la comparaison des candidats de déconvolution et de débruitage
Siril et Cosmic Clarity mesure les mêmes étoiles dans les images avant et après.
L’option `--no-deconvolution-reference-measurements` rétablit l’appariement des
catalogues de détection indépendants. Ce réglage est rémanent avec `-S`, comme
les autres options du traitement. Il concerne aussi le débruitage dans la
séquence postProcess, malgré son préfixe historique `deconvolution`.

La détection Siril avant traitement fournit les positions initiales ; les étoiles
saturées ou aux mesures invalides sont déjà exclues du catalogue. La détection
indépendante après reste dans son TSV et son effectif est conservé comme
diagnostic, mais n’est pas nécessaire pour retrouver une étoile dans la mesure
locale. Les commandes de détection continuent d’être exécutées et doivent réussir.

## Module et étapes

`lib/reference_stars.py` fournit `image_plane`, `measure_star` et
`measure_reference_catalog`. `compare_star_catalogs` reçoit les chemins des deux
FITS via `reference_images`, puis produit ses statistiques et contrôles habituels
à partir des mesures locales. Les dimensions des deux FITS doivent être identiques.
La gestion de `ROWORDER` convertit les tableaux FITS dans les coordonnées Siril
(depuis le bas, centres à .5 pixel). Les valeurs entières sont normalisées.

Pour chaque référence, la fenêtre couvre au moins six pixels de rayon, sinon
un rayon de trois FWHM initiales. Les références proches du bord, non finies ou
proches d’une autre détection à moins de trois FWHM sont exclues. Un plateau de
saturation de trois pixels ou plus au niveau maximal ≥ 1 est également rejeté.

Le même modèle est ajusté avant et après : gaussienne elliptique tournée,
amplitude positive et fond plan. Le centre reste libre dans le rayon
d’appariement ; un déplacement atteignant 99 % du rayon est refusé. Les sigmas
sont compris entre 0.5 pixel et la moitié du rayon de fenêtre. L’ajustement utilise
SciPy `least_squares`, borné à 150 évaluations. Une non-convergence, une largeur
sur une borne ou un Jacobien singulier des cinq paramètres principaux est refusé.

Les positions de la référence remesurée guident l’ajustement candidat. La FWHM
est calculée par `2 sqrt(2 ln 2) × sigma`. Les deux FWHM sont donc issues de la
même méthode de mesure, au lieu de mélanger ajustement Siril et ajustement Python.

## Signal/bruit et incertitudes

Le fond initial est la médiane des pixels périphériques ; le bruit est
`1.4826 × MAD` dans cette zone. Une ouverture circulaire de rayon
`2.5 sqrt(sigma_x sigma_y)` fournit le flux net après soustraction du fond plan.
Si N est son nombre de pixels et M le nombre de pixels périphériques :

```text
incertitude ≈ bruit × sqrt(N + N²/M)
SNR ≈ flux_net / incertitude
```

Le SNR minimal est 10, appliqué séparément à l’entrée et au candidat. Une
référence trop faible n’entre pas dans l’échantillon évalué ; une référence
fiable dont le candidat échoue reste dans le dénominateur, donc ne disparaît
pas silencieusement de l’évaluation.

Ce SNR est une estimation empirique provisoire : bruit corrélé, bruit de photons,
incertitude du fond plan et covariance complète ne sont pas modélisés. Il ne
s’agit pas d’une photométrie calibrée. Les résidus RMS normalisés et une
incertitude conditionnelle de largeur sont enregistrés, sans seuil de rejet
supplémentaire non calibré. La covariance conditionnelle fixe angle et fond ;
elle ne remplace pas une incertitude marginale complète.

## Rapports, performances et limites

`comparison.reference_measurements` expose les effectifs initial, fiable et
candidat, les identités éligibles, `failure_summary` et les mesures individuelles
avec leur étape et motif. Chaque mesure réussie expose flux net, SNR, bruit,
fond, déplacement, résidus et incertitude de largeur. Le journal résume les
références et les motifs sans imprimer une ligne par étoile.

Les contrôles de représentativité en flux et position utilisent uniquement les
références fiables. Les seuils minimaux d’appariement et les contrôles de gain,
rondeur, bruit et anneaux restent actifs. Le nombre minimal automatique conserve
sa définition depuis le catalogue initial ; un nombre trop faible de références
fiables peut donc toujours empêcher la validation.

Deux plans flottants sont chargés en mémoire, et deux ajustements locaux sont
nécessaires par référence fiable, pour chaque essai. Le temps augmente avec le
nombre d’étoiles et d’essais adaptatifs. Les profils non gaussiens, nébulosités,
voisins non détectés et structures introduites par le réseau peuvent encore
biaiser les ajustements. L’isolation est déterminée depuis les détections initiales.
L’inspection des candidats demeure nécessaire.

## Vérification reproductible

```bash
.venv/bin/python -m pytest -q tests/test_reference_stars.py
```

Les tests synthétiques vérifient une étoile absente du catalogue candidat,
un léger déplacement et une réduction connue de largeur, un signal candidat
perdu, les références faibles ou proches du bord et les dimensions incompatibles.
Les essais sur M33 lisent des images existantes et ne remplacent pas les sorties.
