# SirilProcessing

Scripts de traitement d’images astronomiques avec Siril : bibliothèque de darks,
calibration des lights, sélection des images, stacking, mosaïques et animations
d’éclipse solaire.

Tous les scripts Siril utilisent par défaut le nombre de processeurs logiques
du PC d’exécution moins un (minimum : un). La classe commune
`lib.cpu_config.CpuConfig` permet de changer cette limite pour tous les lancements
du processus : `CpuConfig.configure(limit=8)`. Utiliser
`CpuConfig.configure()` pour rétablir le calcul automatique. Une limite explicite
est plafonnée au nombre de processeurs du PC.

- [Documentation — accueil et installation](docs/README.md)
- [Documentation des scripts](docs/scripts/README.md)
- [Architecture des bibliothèques](docs/architecture/README.md)
