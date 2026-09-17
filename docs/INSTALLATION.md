# Installation et démarrage

[Documentation](README.md)

Depuis la racine du projet :

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./bin/lightProcess.sh /chemin/vers/la/session
```

Les traitements astronomiques utilisent une installation de Siril accessible
selon le mode configuré (`native`, `flatpak` ou `appimage`). Les wrappers `.sh`
activent l’environnement Python avant d’appeler le script correspondant.

Consulter ensuite la [documentation du script](scripts/README.md) concerné
pour ses paramètres et les structures de répertoires attendues.

La génération HTML seule ne nécessite ni Siril ni les bibliothèques de traitement
d’image. Ses dépendances et commandes figurent dans le
[guide du générateur](scripts/generate_docs_html/README.md).
