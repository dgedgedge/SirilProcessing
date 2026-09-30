# Post-traitement d’une image FITS

[Documentation](../../../../README.md) › [postProcess](../../../postProcess/README.md)

La documentation utilisateur est disponible dans l’ensemble dédié à
[postProcess : traitements, options, configuration et sorties](../../../postProcess/README.md).

La séquence comporte quatre étapes : correction du gradient, étalonnage
photométrique, réduction du bruit puis déconvolution contrôlée.

Pour l’API Python et l’ajout d’un traitement, voir
[le contrat de la séquence](SEQUENCE_PROCESSOR.md).

## Configuration Siril partagée avec lightProcess

Les paramètres Siril et les réglages rémanents des traitements sont gérés par
`Config`. Voir [la configuration et la mémorisation](../../../postProcess/README.md#configuration-et-mémorisation).
