# Stacking et sélection des poses

[Documentation](../../../../README.md) › [lightProcess](../../README.md) › [Filtrage](../README.md)


Cette rubrique décrit la sélection des lights, leur pondération et le choix
du rééchantillonnage avant l’empilement final.

## Critères de sélection

Commencer par [Sélection des images pour le stacking](IMAGE_SELECTION.md).
Cette page décrit l’ordre des contrôles, leurs valeurs par défaut et les logs :
validité des alignements, FWHM, rondeur, nombre d’étoiles et pondérations.

Le [filtrage par les profils stellaires](STELLAR_PROFILE_FILTER.md) détaille le
contrôle complémentaire des pixels : rayon R80, allongement cohérent, seuils,
rapports, validation et références scientifiques.

## Drizzle

Le [diagnostic et le rééchantillonnage Drizzle](DRIZZLE_SPECIFICATION.md)
utilisent les poses retenues pour évaluer le dithering, la couverture des
positions sous-pixel, l’échantillonnage et les ressources disponibles.
Cette décision choisit le mode d’empilement ; elle est distincte des critères
qui excluent une pose.

## Guides associés

- [Traitement des lights](../../GUIDE.md) : commandes et déroulement complet.
- [Séquences Siril](../../reference/SIRIL_SEQUENCE.md) : représentation des images, mesures et transformations.
- [Mosaïques](../../MOSAIC.md) : assemblage de champs après leur traitement.
