# Génération de la documentation HTML et PDF

[Documentation](../../README.md)


`generate_docs_html.py` transforme les Markdown du dépôt en pages HTML.
L’index reprend les dossiers et utilise leur `README.md` comme page d’entrée.
`build_docs.sh` active le venv du projet et lance le générateur avec export PDF.

## Utilisation

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
