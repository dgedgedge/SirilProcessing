# Architecture

[Documentation](../README.md) › Architecture

Les scripts de `bin/` sont les points d’entrée. Ils reçoivent les options,
organisent une exécution et rendent ses résultats accessibles à l’utilisateur.
Les bibliothèques de `lib/` portent les responsabilités communes et les
traitements propres aux images astronomiques.

## Organisation générale

| Ensemble de bibliothèques | Responsabilité |
|---|---|
| Configuration et accès à Siril | Adapter l’environnement d’exécution et lancer les traitements externes |
| Métadonnées et séquences | Représenter les fichiers, leurs caractéristiques et leurs relations |
| Traitement des darks et des lights | Organiser la calibration et la production des résultats |
| Qualité et stacking | Mesurer la qualité, choisir les poses et piloter leur empilement |
| Mosaïques | Assembler les résultats de plusieurs champs |

Le [catalogue des bibliothèques](LIBRARIES.md) précise le rôle général de chaque
module. Les pages d’architecture ne détaillent ni les algorithmes, ni les
signatures de méthodes, ni les seuils de sélection.

## Documentation des traitements

- [lightProcess : traitements détaillés](../scripts/lightProcess/treatments/README.md).
- [darkLibUpdate : bibliothèque de darks](../scripts/darkLibUpdate/README.md).
- [Autres scripts](../scripts/README.md).
