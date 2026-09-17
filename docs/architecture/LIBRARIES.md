# Rôle général des bibliothèques

[Documentation](../README.md) › [Architecture](README.md) › Bibliothèques

| Module | Rôle général |
|---|---|
| [config.py](../../lib/config.py) | Centraliser les réglages, leurs valeurs par défaut et leur persistance |
| [fits_info.py](../../lib/fits_info.py) | Donner accès aux caractéristiques des FITS et aux informations nécessaires à leur regroupement et à leur validation |
| [siril_utils.py](../../lib/siril_utils.py) | Encapsuler la configuration et l’exécution de Siril |
| [siril_sequence.py](../../lib/siril_sequence.py) | Représenter les séquences Siril et leurs images, en conservant l’association entre fichiers, mesures et transformations |
| [darkprocess.py](../../lib/darkprocess.py) | Gérer la constitution et la mise à jour de la bibliothèque de master darks |
| [lightprocessor.py](../../lib/lightprocessor.py) | Organiser les sessions de lights, leur calibration et la préparation des entrées du stack final |
| [quality_filter.py](../../lib/quality_filter.py) | Centraliser les décisions de sélection, les contrôles d’alignement et les pondérations des poses |
| [stellar_quality.py](../../lib/stellar_quality.py) | Mesurer la forme et l’étalement des étoiles dans les pixels des images |
| [drizzle.py](../../lib/drizzle.py) | Évaluer l’intérêt du Drizzle et orchestrer les étapes du stacking |
| [mosaic.py](../../lib/mosaic.py) | Organiser l’assemblage des champs en mosaïque |

Les bibliothèques de représentation alimentent les traitements de calibration
et de stacking. La mesure des profils stellaires fournit des observations au
module de sélection ; l’orchestration du stack consomme ensuite cette sélection.
L’accès à Siril est partagé par les traitements qui exécutent ses commandes.

Pour les règles de traitement et les formats détaillés, consulter la
documentation de [lightProcess](../scripts/lightProcess/README.md) ou de
[darkLibUpdate](../scripts/darkLibUpdate/README.md).
