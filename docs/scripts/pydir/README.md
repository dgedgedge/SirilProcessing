# pydir — contenu d’un répertoire

[Documentation](../../README.md)


`pydir.py` affiche les répertoires puis les fichiers, avec tri alphabétique
insensible à la casse, taille des fichiers et totaux. Il ne parcourt pas
récursivement les sous-répertoires.

```bash
python3 bin/pydir.py /chemin/vers/un/repertoire
```

Sans argument, il utilise le répertoire courant. Dans Siril, le script peut
être appelé par `pyscript /chemin/vers/pydir.py` pour inspecter le dossier de
travail. Une absence de répertoire, un chemin qui n’est pas un dossier ou une
erreur d’accès produit un message et un code de sortie 1.
