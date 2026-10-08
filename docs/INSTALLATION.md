# Installation et démarrage

[Documentation](README.md)

Les lanceurs `.sh` utilisent Python et pip **du système** pour créer et mettre
à jour leur environnement virtuel, puis exécutent les scripts avec le Python
de cet environnement. Ils ne nécessitent pas pip dans le venv.

Depuis la racine du projet :

```bash
./bin/lightProcess.sh /chemin/vers/la/session
```

Le module partagé `bin/venv_helpers.sh` sélectionne `VENV_DIR` s’il est défini,
sinon `.venv`, puis `venv` lorsqu’il existe déjà. Sans environnement existant,
il crée `.venv`. Il utilise `python3` pour retrouver l’interpréteur système,
même lorsqu’un venv est activé dans le terminal, puis effectue l’équivalent de :

```bash
python3 -m venv --without-pip .venv
python3 -m pip --python .venv/bin/python install --upgrade -r requirements.txt
```

Ces commandes manuelles supposent un terminal sans venv activé. Le Python
système doit fournir les modules `venv` et `pip` avec prise en charge de
`--python` (pip 22.3 ou plus récent). Un échec de création ou d’installation
interrompt le lanceur. Les paquets système ne sont pas modifiés.

Chaque lancement vérifie les mises à jour et peut nécessiter Internet, y compris
avec `--help`. Pour exécuter sans mise à jour, appeler directement le script :

```bash
.venv/bin/python bin/postProcess.py --help
.venv/bin/python -m pytest -q tests/test_deconvolution.py tests/test_denoising.py
```

`requirements.txt` inclut les traitements, les visualiseurs, la documentation
HTML/PDF et les tests. Les lanceurs KStars et guide logs utilisent seulement
`requirements-analyze.txt`. Le fichier `requirements-docs.txt` permet une
installation HTML seule. Tkinter reste une dépendance système (`python3-tk`
sous Debian/Ubuntu).

Les traitements astronomiques utilisent une installation de Siril accessible
selon le mode configuré (`native`, `flatpak` ou `appimage`). Les wrappers `.sh`
préparent l’environnement Python avant d’appeler le script correspondant.

Consulter ensuite la [documentation du script](scripts/README.md) concerné
pour ses paramètres et les structures de répertoires attendues.

La génération HTML seule ne nécessite ni Siril ni les bibliothèques de traitement
d’image. Ses dépendances et commandes figurent dans le
[guide du générateur](scripts/generate_docs_html/README.md).

Les dépendances CUDA sont optionnelles et séparées de `requirements.txt` :
`bin/postProcess.sh --install-cosmic-clarity` installe PyTorch CUDA et les poids
nécessaires dans le projet. Voir [Cosmic Clarity](scripts/postProcess/cosmic-clarity.md)
pour les prérequis GPU, les options et les vérifications.
