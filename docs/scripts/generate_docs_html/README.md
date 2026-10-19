# MkDocs, PDF et HTML autonome

[Documentation](../../README.md)

`bin/build_documentation.py` construit le site MkDocs et ses téléchargements
à partir des mêmes Markdown. L’accueil reprend le README du projet ; les pages
maîtresses des scripts et de l’architecture restent leurs points d’entrée.
MkDocs fournit navigation et recherche, avec le thème Read the Docs en français.
`mkdocs.yml` configure le thème, le rendu Markdown et l’adresse du site.

La navigation suit un plan numéroté : installation, manuels des scripts,
architecture, puis maintenance et tests. Chaque rubrique de script regroupe son
manuel d’utilisation, ses options et ses traitements détaillés. La rubrique
`nav` de `mkdocs.yml` est commune au menu du site et au sommaire des exports.
Les paragraphes numérotés des manuels conservent leurs anciennes ancres.

L’accueil propose deux téléchargements :

- `downloads/documentation.html` : toute la documentation dans un seul HTML,
  avec styles intégrés, ancres uniques et diagrammes SVG, consultable hors ligne ;
- `downloads/documentation.pdf` : la même documentation en PDF A4, avec diagrammes
  et signets.

Le monofichier conserve les liens internes entre chapitres et embarque les
sources Python/shell/JSON et images locales explicitement liées. Les liens vers
des sites externes restent externes. Les fichiers non liés, les poids `.onnx`/
`.pth`, les FITS et les caches ne sont pas embarqués. Le site propose aussi les
pages HTML individuelles et leurs sources liées.

Le PDF et le HTML autonome suivent l’ordre des rubriques et proposent un
sommaire imbriqué. Les références d’options sont regroupées avec le manuel de
chaque script, sans répéter leurs tableaux dans plusieurs pages. Les sources
complémentaires absentes du menu sont
regroupées dans les annexes des exports. Une cible de navigation absente des
sources arrête la construction au lieu de produire un lien incomplet.

<a id="construire-localement"></a>

## 1. Construire localement

Depuis la racine du projet :

```bash
bin/build_docs.sh
```

Le lanceur prépare le venv avec `requirements-docs.txt`, installe Chromium dans
`tmp/docs-browsers/`, puis construit les trois formats dans `out/`. Il ne nécessite
ni Siril ni les bibliothèques astronomiques. Avec les prérequis déjà installés :

```bash
.venv/bin/python bin/build_documentation.py
.venv/bin/python -m http.server --bind 127.0.0.1 --directory out 8000
```

Ouvrir `http://127.0.0.1:8000/`. Pour préparer manuellement les prérequis :

```bash
python3 -m pip --python .venv/bin/python install -r requirements-docs.txt
PLAYWRIGHT_BROWSERS_PATH="$PWD/tmp/docs-browsers" \
  .venv/bin/python -m playwright install chromium
```

Sous Linux, des bibliothèques système peuvent être nécessaires ; la commande
`python -m playwright install --with-deps chromium` les installe avec les droits
administrateur. Le workflow l’utilise sur son runner GitHub.


<a id="options-du-constructeur"></a>

## 2. Options du constructeur

Consulter la [référence des options de generate_docs_html](OPTIONS.md). Les tableaux et réglages y sont regroupés.


<a id="publication-github-pages"></a>

## 3. Publication GitHub Pages

Le workflow **Documentation MkDocs** (`.github/workflows/documentation.yml`)
construit et teste les trois formats après une modification documentaire,
de configuration ou du générateur. L’archive **documentation** reste téléchargeable
pendant 30 jours dans les artefacts GitHub Actions. Les pull requests et branches
de travail construisent cette archive sans remplacer le site publié.

Pour activer la publication :

1. Ouvrir **Settings → Pages** dans le dépôt GitHub.
2. Sélectionner **GitHub Actions** dans **Build and deployment → Source**.
3. Intégrer le workflow sur la branche par défaut, puis lancer
   **Actions → Documentation MkDocs → Run workflow**, ou pousser une modification
   documentaire sur cette branche.

Le job `deploy` utilise `actions/deploy-pages` et les permissions `pages: write`
et `id-token: write`. Seuls les lancements sur la branche par défaut publient.
L’environnement `github-pages` affiche l’adresse du site. L’adresse prévue est
[dgedgedge.github.io/SirilProcessing](https://dgedgedge.github.io/SirilProcessing/) ;
elle devient disponible après le premier déploiement réussi.

GitHub Pages reçoit uniquement `out/`, sans modèles, FITS d’exemple ni caches
Chromium. Aucun HTML généré n’est commité dans `docs/`, qui conserve les sources
Markdown. Une construction locale ne publie pas le site distant.

<a id="classes-et-v-rification"></a>
<a id="classes-et-vérification"></a>

## 4. Classes et vérification

`DocumentationBuilder` prépare les sources, lit la navigation MkDocs et produit
les exports et le site. `DocumentationSection` représente les rubriques, pages
et accès directs à leurs sections.
`SingleFileLinks` convertit les liens et embarque les ressources autorisées.
Playwright/Chromium rend Mermaid en SVG et imprime le même document en PDF.
Le HTML autonome sauvegardé ne dépend d’aucun script ou fichier de style externe.
Le site utilise une copie locale du script Mermaid pour afficher ses diagrammes.

`generate_docs_html.py` reste le moteur de rendu partagé des exports et conserve
son ancien point d’entrée. Ses options `--pdf` et `--pdf-output` utilisent
WeasyPrint ; elles ne sont pas celles du nouveau constructeur MkDocs.
Le fichier `all_docs.html` conserve une seule fois le titre principal de chaque
source, avec son ancre et la mention de provenance. Un titre est ajouté à partir
du nom du document uniquement si le contenu ne possède pas de titre principal.

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_doc*.py'
```

Les tests utilisent des documents synthétiques et vérifient ancres, liens,
ressources embarquées, exclusions, sommaires et protection des dossiers, sans
installer de navigateur. Une construction complète vérifie en plus le rendu
Chromium et les vrais exports. Les scripts Mermaid des pages individuelles
s’exécutent dans le navigateur du lecteur ; les exports contiennent leur SVG.

Références : [MkDocs](https://www.mkdocs.org/),
[GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages),
[Playwright](https://playwright.dev/python/docs/browsers).
