# solarEclipseGif — animation d’éclipse solaire

[Documentation](../../README.md)


`solarEclipseGif.py` et `solarEclipseGif.sh` produisent une animation à partir
d’images FITS solaires monochromes.

<a id="manuel-d-utilisation"></a>
<a id="manuel-dutilisation"></a>

## 1. Manuel d’utilisation

Le [guide du script](GUIDE.md) décrit les entrées, la calibration du fond,
le centrage, les modèles de disque solaire, les options d’accélération,
les images de diagnostic et la création du GIF.

<a id="options-et-r-sultats"></a>
<a id="options-et-résultats"></a>

## 2. Options et résultats

- [Paramètres de traitement](OPTIONS.md).
- [Commandes de lancement](GUIDE.md#commandes).
- [Fichiers générés](GUIDE.md#fichiers-generes).

<a id="installation"></a>

## 3. Installation

Le lanceur utilise le Python du venv existant sans installer ni mettre à jour
les dépendances, y compris avec `--help`. La sélection suit `VENV_DIR`, `.venv`,
puis `venv`. Si l’environnement manque, il indique la commande d’installation.
Voir [Installation](../../INSTALLATION.md) pour préparer les prérequis.
