# SirilProcessing

Scripts de traitement d’images astronomiques avec Siril : bibliothèque de darks,
calibration des lights, sélection des images, stacking, mosaïques et animations
d’éclipse solaire.

[**Documentation en ligne**](https://dgedgedge.github.io/SirilProcessing/) ·
[PDF complet](https://dgedgedge.github.io/SirilProcessing/downloads/documentation.pdf) ·
[HTML autonome](https://dgedgedge.github.io/SirilProcessing/downloads/documentation.html)

## Documentation

Le site MkDocs publié sur GitHub Pages donne accès aux guides suivants.
Les mêmes pages restent consultables en Markdown dans le dépôt.

| Guide sur le site publié | Source dans le dépôt |
|---|---|
| [Accueil de la documentation](https://dgedgedge.github.io/SirilProcessing/docs/index.html) | [Markdown](docs/README.md) |
| [Installation](https://dgedgedge.github.io/SirilProcessing/docs/INSTALLATION.html) | [Markdown](docs/INSTALLATION.md) |
| [Traitement des lights : calibration, stacking et mosaïques](https://dgedgedge.github.io/SirilProcessing/docs/scripts/lightProcess/index.html) | [Markdown](docs/scripts/lightProcess/README.md) |
| [Post-traitement et rendu AutoGHS](https://dgedgedge.github.io/SirilProcessing/docs/scripts/postProcess/index.html) | [Markdown](docs/scripts/postProcess/README.md) |
| [Bibliothèque de darks](https://dgedgedge.github.io/SirilProcessing/docs/scripts/darkLibUpdate/index.html) | [Markdown](docs/scripts/darkLibUpdate/README.md) |
| [Animations d’éclipse solaire](https://dgedgedge.github.io/SirilProcessing/docs/scripts/solarEclipseGif/index.html) | [Markdown](docs/scripts/solarEclipseGif/README.md) |
| [Visualiseur des analyses KStars / Ekos](https://dgedgedge.github.io/SirilProcessing/docs/scripts/kstarsAnalyze/index.html) | [Markdown](docs/scripts/kstarsAnalyze/README.md) |
| [Visualiseur des guide logs KStars / Ekos](https://dgedgedge.github.io/SirilProcessing/docs/scripts/guideLogAnalyze/index.html) | [Markdown](docs/scripts/guideLogAnalyze/README.md) |
| [Tous les scripts](https://dgedgedge.github.io/SirilProcessing/docs/scripts/index.html) | [Markdown](docs/scripts/README.md) |
| [Architecture des bibliothèques](https://dgedgedge.github.io/SirilProcessing/docs/architecture/index.html) | [Markdown](docs/architecture/README.md) |

La publication est actualisée par GitHub Actions après intégration des changements
sur la branche par défaut. Voir [la construction et la publication](docs/scripts/generate_docs_html/README.md).

## Ressources de calcul

Tous les scripts Siril utilisent par défaut le nombre de processeurs logiques
du PC d’exécution moins un (minimum : un). La classe commune
`lib.cpu_config.CpuConfig` permet de changer cette limite pour tous les lancements
du processus : `CpuConfig.configure(limit=8)`. Utiliser
`CpuConfig.configure()` pour rétablir le calcul automatique. Une limite explicite
est plafonnée au nombre de processeurs du PC.
