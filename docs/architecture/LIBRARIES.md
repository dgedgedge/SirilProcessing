# Rôle général des bibliothèques

[Documentation](../README.md) › [Architecture](README.md) › Bibliothèques

| Module | Rôle général |
|---|---|
| [parameterized_component.py](../../lib/parameterized_component.py) | Partager la déclaration et la capture des paramètres entre services et traitements |
| [processor.py](../../lib/processor.py) | Ajouter le contrat de traitement d’image aux composants configurables |
| [postprocess.py](../../lib/postprocess.py) | Regrouper les moteurs, la séquence et le [pipeline complet de post-traitement](../scripts/postProcess/pipeline.md) commun à lightProcess et postProcess |
| [annotation_catalog.py](../../lib/annotation_catalog.py) | Charger OpenNGC hors ligne et projeter les positions sur le WCS final pour les [annotations](../scripts/postProcess/annotations.md) |
| [final_annotation.py](../../lib/final_annotation.py) | Produire la [copie annotée finale](../scripts/postProcess/annotations.md) et son encart de prise de vue |
| [stacking_annotation.py](../../lib/stacking_annotation.py) | Lire la provenance des poses et panneaux pour les [compteurs annotés](../scripts/postProcess/annotations.md) |
| [postprocess_resume.py](../../lib/postprocess_resume.py) | Conserver la provenance et retrouver l’entrée linéaire pour la [reprise du rendu final](../scripts/postProcess/README.md#relance-autoghs-seul) |
| [cuda_environment.py](../../lib/cuda_environment.py) | Installer les dépendances du venv commun et vérifier les trois moteurs [CUDA](../scripts/install_cuda/README.md) |
| [macos_environment.py](../../lib/macos_environment.py) | Installer les dépendances Apple Silicon et contrôler MPS/CoreML ; [guide Mac](../scripts/install_macos/README.md) |
| [gpu_environment.py](../../lib/gpu_environment.py) | Partager la sélection du venv, les appels pip et le graphe de contrôle entre les installateurs GPU |
| [dependency_installation.py](../../lib/dependency_installation.py) | Détecter la plateforme, orchestrer installation/test et nettoyer explicitement le venv ; [commande commune](../scripts/install_dependances/README.md) |
| [installation_assets.py](../../lib/installation_assets.py) | Précharger les modèles et résoudre leurs chemins ; [ressources de l'installateur](../scripts/install_dependances/resources.md) |
| resource_manager.py | Partager les accès aux catalogues et poids, leur découverte et leur acquisition entre installateur et traitements ; [interfaces et politiques](../scripts/install_dependances/resources.md#resource-manager) |
| [resource_cache.py](../../lib/resource_cache.py) | Centraliser modèles/Gaia, découvrir le catalogue local et nettoyer les modèles ; [cache et nettoyage](../scripts/install_dependances/resources.md) |
| [gaia_catalogue.py](../../lib/gaia_catalogue.py) | Acquérir et vérifier l'extrait Gaia astrométrique optionnel ; [format et intégrité](../scripts/install_dependances/resources.md) |
| gaia_spcc.py | Installer et vérifier les 48 régions spectrales SPCC ; [reprise et intégrité](../scripts/install_dependances/resources.md#31-catalogue-spectral-spcc-complet) |
| [ghs_final_stretch.py](../../lib/ghs_final_stretch.py) | Rechercher le renforcement final D/B/SP/HP sous [contraintes de hautes lumières](../scripts/postProcess/ghs-highlight-protection.md) |
| [ghs_log_histogram.py](../../lib/ghs_log_histogram.py) | Mesurer la décroissance semi-log robuste pour le [classement des essais GHS](../scripts/postProcess/ghs-highlight-protection.md#31-régression-de-la-décroissance-logarithmique) |
| [final_render.py](../../lib/final_render.py) | Orchestrer et publier le [rendu final AutoGHS](../scripts/postProcess/auto-ghs.md) après les traitements linéaires |
| [final_color.py](../../lib/final_color.py) | Renforcer la [couleur après GHS](../scripts/postProcess/final-color.md) et recomposer les étoiles, avec reprise indépendante |
| [auto_ghs.py](../../lib/auto_ghs.py) | Calculer les étirements et rechercher leurs paramètres sur histogrammes |
| [star_separation.py](../../lib/star_separation.py) | Adapter StarNet V2 via le service Siril existant |
| [star_rendering.py](../../lib/star_rendering.py) | Étendre modérément les étoiles et recomposer les branches |
| [compute_config.py](../../lib/compute_config.py) | Partager la [politique CPU/GPU](../scripts/postProcess/compute-backend.md) et les surcharges des moteurs |
| [config.py](../../lib/config.py) | Charger les réglages JSON, résoudre les déclarations des classes utilisatrices et sauvegarder explicitement |
| [fits_info.py](../../lib/fits_info.py) | Donner accès aux caractéristiques des FITS et aux informations nécessaires à leur regroupement et à leur validation |
| [noise_tiles.py](../../lib/noise_tiles.py) | Sélectionner spatialement les tuiles de bruit et calculer leurs statistiques par lots NumPy/PyTorch |
| [quality_processors.py](../../lib/quality_processors.py) | Séquencer les contrôles de qualité d'image sous forme de processeurs récursifs partageant les données |
| diffuse_regions.py | Sélectionner les régions de référence par tailles et positions ; [algorithme et audit](../scripts/postProcess/diffuse-regions.md) |
| torch_backend.py | Sélectionner CUDA/MPS et partager les filtres et statistiques PyTorch ; [contrats numériques](../scripts/postProcess/torch-compute.md) |
| diffuse_filters.py | Exécuter les convolutions diffuses sur SciPy CPU ou PyTorch CUDA/MPS avec contrôle initial et audit ; [moteur et limites](../scripts/postProcess/diffuse-regions.md#6-filtrages-multiscales-sur-gpu) |
| [quality_star_processors.py](../../lib/quality_star_processors.py) | Séparer la détection Siril, les mesures locales et la comparaison stellaire |
| [restoration_quality.py](../../lib/restoration_quality.py) | Évaluer les candidats sur des masques indépendants des moteurs et du gradient |
| [restoration_chains.py](../../lib/restoration_chains.py) | Organiser la comparaison des chaînes complètes, active par défaut |
| [restoration_summary.py](../../lib/restoration_summary.py) | Présenter les essais, contrôles et motifs de sélection dans les journaux et rapports |
| [quality_preview.py](../../lib/quality_preview.py) | Illustrer les exclusions de qualité et les tuiles de ciel retenues |
| [power_guard.py](../../lib/power_guard.py) | Fournir le contexte de performance et d’inhibition de veille D-Bus, activé uniquement par les scripts de lancement |
| [graxpert_inference.py](../../lib/graxpert_inference.py) | Exécuter le modèle ONNX avec préservation des FITS |
| [graxpert_models.py](../../lib/graxpert_models.py) | Acquérir et vérifier les poids à la demande |
| [graxpert.py](../../lib/graxpert.py) | Encapsuler le débruitage ONNX natif GraXpert |
| [siril_api.py](../../lib/siril_api.py) | Posséder les processus Siril, le transport API et les vues de modèles ; voir [sessions et API](../scripts/lightProcess/reference/SIRIL_API.md) |
| [siril.py](../../lib/siril.py) | Fournir des sessions Siril autonomes via sirilpy (Siril ≥ 1.4.4, erreurs fatales) et la lecture, la modification et l’écriture des séquences FITS |
| [darkprocess.py](../../lib/darkprocess.py) | Gérer la constitution et la mise à jour de la bibliothèque de master darks |
| [lightprocessor.py](../../lib/lightprocessor.py) | Découvrir les sessions de lights, grouper les images et orchestrer leur calibration |
| [light_calibration.py](../../lib/light_calibration.py) | Calibrer une séquence de lights et construire récursivement le master flat associé avec l'interface commune des traitements |
| [light_stacking.py](../../lib/light_stacking.py) | Empiler les FITS calibrés d'une session avec l'interface commune des traitements |
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

La configuration partagée est chargée au démarrage par le programme principal.
Les classes utilisatrices déclarent leurs options, défauts, enfants et règles de
persistance ; le programme les transmet à `Config.register`, résout les valeurs
avec la priorité CLI > JSON > défauts locaux, puis lie les instantanés aux classes.
`Config` n'importe aucun service ni traitement : il utilise leur contrat
déclaratif, sans imposer d'héritage. Les préfixes complémentaires sont déclarés
par les classes concernées, y compris Cosmic Clarity. Le programme principal
reste responsable de la sauvegarde explicite avec `-S` ; enregistrer, résoudre
ou lier des paramètres n'écrit pas le fichier.

Le module `ghs_linear_histogram.py` mesure la longueur continue semi-log et
propose les pivots aux ruptures de pente pour les
[étirements GHS successifs](../scripts/postProcess/ghs-quartiles.md).

`output_layout.py` centralise les étapes numérotées, l’identité stable des panneaux
et le relais physique `02_mosaic/image.fits` ; voir
[le plan des sorties](../scripts/lightProcess/output-layout.md).

`processing_html.py` publie les index hors ligne des résultats et leurs
aperçus, sans relancer les moteurs ni lire les pixels FITS. Voir les
[index de traitement](../scripts/lightProcess/html-reports.md).
