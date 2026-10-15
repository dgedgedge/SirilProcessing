# Rôle général des bibliothèques

[Documentation](../README.md) › [Architecture](README.md) › Bibliothèques

| Module | Rôle général |
|---|---|
| [parameterized_component.py](../../lib/parameterized_component.py) | Partager la déclaration et la capture des paramètres entre services et traitements |
| [processor.py](../../lib/processor.py) | Ajouter le contrat de traitement d’image aux composants configurables |
| [config.py](../../lib/config.py) | Centraliser les réglages, leurs valeurs par défaut et leur persistance |
| [fits_info.py](../../lib/fits_info.py) | Donner accès aux caractéristiques des FITS et aux informations nécessaires à leur regroupement et à leur validation |
| [restoration_quality.py](../../lib/restoration_quality.py) | Évaluer les candidats sur des masques indépendants des moteurs et du gradient |
| [restoration_chains.py](../../lib/restoration_chains.py) | Organiser la comparaison des chaînes complètes, active par défaut |
| [restoration_summary.py](../../lib/restoration_summary.py) | Présenter les essais, contrôles et motifs de sélection dans les journaux et rapports |
| [quality_preview.py](../../lib/quality_preview.py) | Illustrer les exclusions de qualité et les tuiles de ciel retenues |
| [power_guard.py](../../lib/power_guard.py) | Détenir temporairement des demandes de performance et d’inhibition de veille via D-Bus |
| [graxpert_inference.py](../../lib/graxpert_inference.py) | Exécuter le modèle ONNX avec préservation des FITS |
| [graxpert_models.py](../../lib/graxpert_models.py) | Acquérir et vérifier les poids à la demande |
| [graxpert.py](../../lib/graxpert.py) | Encapsuler le débruitage ONNX natif GraXpert |
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
documentation de [lightProcess](../scripts/lightProcess/README.md), de
[postProcess](../scripts/postProcess/README.md) ou de
[darkLibUpdate](../scripts/darkLibUpdate/README.md).
