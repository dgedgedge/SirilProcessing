# Options de solarEclipseGif

[Documentation des scripts](../README.md) › [solarEclipseGif](GUIDE.md)

<a id="parametres"></a>

## 1. Paramètres

Les parametres sont :

- `--input-dir` : repertoire des images sources.
- `--dark-calib-frames` : liste d'index d'images.
- `--manual-seuil-fond-du-ciel` : nombre.
- `--full-sun-frames` : liste d'index d'images.
- `--first-nearly-full-sun-frames` : parametre par defaut `10`.
- `--exclude-frames`.
- `--debug-dir` : repertoire des images de debug. Par defaut, sous-repertoire
  de `--input-dir` nomme `debug`.
- `--debug-full-sun`.
- `--debug-shifts` : cree et alimente le repertoire de debug du calcul des
  decalages.
- `--debug-gif-frames` : cree et alimente un repertoire contenant les images
  effectivement incluses dans le GIF final.
- `--debug-watershed` : calcule et affiche le watershed dans les images de
  debug de `MasqueSolairePrincipal`. Par defaut, le watershed n'est pas calcule.
- `--debug-luminosity` : cree et alimente le repertoire de debug de la
  normalisation de luminosite avant/apres correction.
- `--background-outside-mask-scale` : facteur applique au signal hors masque
  solaire dilate avant la courbe en S. `1` conserve le fond pour laisser la
  courbe en S travailler, `0` ramene d'abord le fond au niveau du ciel.
- `--background-mask-dilate-fraction` : dilatation relative du masque solaire
  courant avant attenuation du fond hors masque.
- `--background-s-curve-sigma` : nombre d'ecarts-types du fond utilise pour
  definir le seuil bas de la courbe en S du filtre de fond.
- `--background-s-curve-target-fraction` : niveau relatif vise pour le fond
  apres courbe en S, en fraction du contraste solaire courant.
- `--enable-background-filter` : active explicitement le filtrage selectif du
  fond hors masque solaire. Par defaut, ce filtrage est desactive.
- `--disable-background-filter` : desactive completement le filtrage selectif du
  fond hors masque solaire.
- `--rotate-clockwise-deg`.
- `--target-duration`.
- `--output`.
- `--log-level` : niveau de log technique.

Les selections d'images sont 1-based et acceptent les plages :

```text
1-10,15,20-25
```
