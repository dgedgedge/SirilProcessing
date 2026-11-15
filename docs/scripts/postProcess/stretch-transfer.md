# 1. Recette RGB d’étirement stellaire

[postProcess](README.md) · [Sélection stellaire](stellar-selection.md)

`lib/stretch_transfer.py` fournit `stellar_operation(report, balance)` et
`CumulativeStretchTransform(domain, operations)` pour figer et rejouer les
paramètres stellaires, la table des quantiles, le seuil de bruit et la sélection.
L’application `apply_rgb` ne redétecte pas de pics. Une sélection spatiale active
impose la géométrie native ; une incohérence lève ValueError.

`export(destination)` écrit une recette JSON et des échantillons NPZ float64.
Pour une sélection spatiale, la courbe est une enveloppe R=G=B à poids 1,
identifiée par `scalar_role: selected_star_neutral_envelope`, et non une LUT
universelle de l’image. Le gain F(x)/x est indéfini à zéro (NaN). La recette
RGB complète, avec les coordonnées, reproduit tous les pixels float32.

La courbe est raffinée au seuil d’erreur observée 1e-7 sur F(x), avec au plus
262145 points et 24 raffinements ; une limite atteinte est signalée. Les sondes
numériques ne constituent pas une borne analytique de l’erreur. Les valeurs
neutres intermédiaires sont bornées à [0,1] ; les pixels colorés peuvent avoir
une restriction différente. Voir les tests `test_stretch_transfer.py` pour
l’export, la réapplication et les seuils extrêmes.
