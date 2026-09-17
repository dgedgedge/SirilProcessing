# Filtrage et contrôle qualité

[Documentation](../README.md) › Filtrage

Les contrôles dépendent du type d’image et de leur place dans le traitement.

## Stacking des lights

La rubrique [Stacking et sélection des poses](stacking/README.md) regroupe :

- [Les critères de sélection](stacking/IMAGE_SELECTION.md) : FWHM, rondeur, nombre d’étoiles, alignements et pondérations.
- [Les profils stellaires](stacking/STELLAR_PROFILE_FILTER.md) : étalement R80, allongement et défauts partagés par plusieurs étoiles.
- [Le Drizzle](stacking/DRIZZLE_SPECIFICATION.md) : diagnostic du dithering et choix du rééchantillonnage sur les poses retenues.

## Validation des darks

- [Statistiques robustes](../ROBUST_STATISTICS_UPDATE.md) : dispersion et valeurs aberrantes.
- [Validation conditionnelle](../VALIDATION_OPTIMIZATION.md) : déclenchement des contrôles.
- [Nombre minimal de darks](../MIN_DARKS_THRESHOLD_FEATURE.md) : conditions de mise à jour d’un master.
- [Configuration de la validation](../VALIDATION_CONFIG_GUIDE.md) : options et persistance.
