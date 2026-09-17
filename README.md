# SirilProcessing

Scripts de préparation et de traitement d’images astronomiques avec Siril :
bibliothèque de darks, calibration des lights, sélection des poses, stacking,
mosaïques et animations d’éclipse solaire.

## Installation et démarrage

Depuis la racine du projet :

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./bin/lightProcess.sh /chemin/vers/la/cible
```

Les traitements astronomiques utilisent une installation de Siril accessible
selon le mode configuré (`native`, `flatpak` ou `appimage`). Les wrappers `.sh`
activent l’environnement Python avant d’appeler le script correspondant.

## Documentation par script

| Script | Documentation |
|---|---|
| `lightProcess.py` / `.sh` | [Utilisation et traitements détaillés](docs/scripts/lightProcess/README.md) |
| `darkLibUpdate.py` / `.sh` | [Bibliothèque de master darks](docs/scripts/darkLibUpdate/README.md) |
| `solarEclipseGif.py` / `.sh` | [Animations d’éclipse solaire](docs/scripts/solarEclipseGif/README.md) |
| `pyecho.py` | [Affichage de messages](docs/scripts/pyecho/README.md) |
| `pydir.py` | [Liste des fichiers d’un répertoire](docs/scripts/pydir/README.md) |
| `trouve_doublons.py` | [Recherche de doublons](docs/scripts/trouve_doublons/README.md) |
| `generate_docs_html.py` / `build_docs.sh` | [Génération de la documentation](docs/scripts/generate_docs_html/README.md) |

## Architecture et maintenance

- [Accueil de la documentation](docs/README.md).
- [Architecture et rôle des bibliothèques](docs/architecture/README.md).
- [Organisation et maintenance des documents](docs/CONTRIBUTING.md).
- [Tests](tests/README.md).

Pour générer le site HTML :

```bash
.venv/bin/python bin/generate_docs_html.py
```

Ouvrir `out/index.html`. Les pages HTML suivent l’arborescence des documents.
