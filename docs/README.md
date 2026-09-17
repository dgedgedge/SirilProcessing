# Documentation SirilProcessing

[Projet](../README.md)

## Documentation des scripts

Chaque script possède son dossier, avec ses commandes, ses traitements et ses
sorties. Les wrappers shell sont documentés avec le script Python qu’ils lancent.

| Ensemble | Contenu |
|---|---|
| [lightProcess](scripts/lightProcess/README.md) | Calibration des lights, traitements détaillés, filtrage, stacking, Drizzle et mosaïques |
| [darkLibUpdate](scripts/darkLibUpdate/README.md) | Constitution, validation et mise à jour de la bibliothèque de darks |
| [solarEclipseGif](scripts/solarEclipseGif/README.md) | Préparation et production des animations d’éclipse |
| [pyecho](scripts/pyecho/README.md) | Messages dans les scripts Siril |
| [pydir](scripts/pydir/README.md) | Affichage du contenu d’un répertoire |
| [trouve_doublons](scripts/trouve_doublons/README.md) | Comparaison de répertoires et préparation d’un nettoyage |
| [generate_docs_html et build_docs](scripts/generate_docs_html/README.md) | Site HTML et export PDF |

## Architecture

L’[ensemble d’architecture](architecture/README.md) décrit uniquement les
responsabilités générales des bibliothèques et leurs relations. Les algorithmes,
seuils, options et formats détaillés sont documentés dans l’ensemble du script
qui les utilise.

## Maintenance

- [Règles d’organisation et de mise à jour](CONTRIBUTING.md).
- [Tests du projet](../tests/README.md).
