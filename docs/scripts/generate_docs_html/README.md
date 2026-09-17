# Génération de la documentation HTML et PDF

[Documentation](../../README.md)


`generate_docs_html.py` transforme les Markdown du dépôt en pages HTML.
L’accueil HTML (`index.html`) reprend le `README.md` du projet. Les liens
conduisent ensuite aux mêmes pages maîtresses et aux mêmes sous-ensembles
qu’en Markdown, en conservant les chemins relatifs. Aucune seconde hiérarchie
n’est construite pour le site. Sans README racine, le générateur utilise un
catalogue automatique des documents comme accueil.
`build_docs.sh` active le venv du projet et lance le générateur avec export PDF.

## Utilisation locale

Pour installer uniquement les dépendances HTML :

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-docs.txt
```


```bash
.venv/bin/python bin/generate_docs_html.py
./bin/build_docs.sh
```

Les sorties sont `out/index.html`, les pages individuelles et
`out/all_docs.html`. Avec `--pdf`, le générateur tente aussi de produire
`out/documentation.pdf` via WeasyPrint.

| Option | Effet |
|---|---|
| `--source-root` | Racine des sources Markdown ; répertoire courant par défaut |
| `--output-dir` | Dossier généré ; `out` par défaut |
| `--title` | Titre du site |
| `--pdf` | Demande l’export PDF |
| `--pdf-output` | Nom du PDF dans le dossier généré |

Le dossier de sortie est recréé à chaque génération : il est réservé aux
fichiers générés. Les liens Markdown sont convertis vers les pages HTML et
les ancres de section sont normalisées. Les blocs Mermaid sont conservés pour
leur rendu dans le navigateur.

Le wrapper choisit `VENV_DIR` s’il est défini, sinon `.venv` puis `venv` dans
le projet. Les arguments supplémentaires sont transmis au générateur.

## Génération sur GitHub

Le workflow **Documentation HTML** (`.github/workflows/documentation.yml`)
génère le site lorsqu’un push ou une pull request modifie les Markdown, le
générateur, ses tests ou ses dépendances. Il utilise Python et les dépendances
HTML seules, sans exécuter Siril.

Pour une génération manuelle :

1. Ouvrir l’onglet **Actions** du dépôt.
2. Choisir **Documentation HTML**, puis **Run workflow**.
3. À la fin de l’exécution, télécharger l’artefact **documentation-html**.
4. Extraire l’archive entière et ouvrir `index.html` dans le navigateur.

Le bouton manuel est disponible une fois le workflow présent sur la branche
par défaut, avec les droits nécessaires pour lancer une exécution.
L’archive est conservée pendant 30 jours. Le workflow génère les pages
individuelles et `all_docs.html`, sans PDF. Les diagrammes Mermaid nécessitent
un accès réseau au CDN lors de leur affichage.

Cette automatisation fournit une archive téléchargeable ; elle ne publie pas
un site GitHub Pages. Les fichiers HTML restent générés et ne sont pas ajoutés
au dépôt Git.

Références GitHub : [lancement manuel d’un workflow](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)
et [conservation des artefacts](https://docs.github.com/en/actions/tutorials/store-and-share-data).
