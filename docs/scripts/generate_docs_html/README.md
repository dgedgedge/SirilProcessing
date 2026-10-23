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

Les exports utilisent une présentation A4 portrait par défaut. À l’impression,
les marges sont de 15 mm ; les diagrammes et images sont limités à la largeur
disponible et à 247 mm de hauteur, en conservant leurs proportions. Un diagramme
est déplacé sur la page suivante si nécessaire pour éviter de le couper.
Les diagrammes très étendus sont réduits pour tenir sur une page ; le HTML
autonome permet de les consulter avec le zoom du navigateur.

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

### 3.1. Téléchargements introuvables (erreur 404)

Si l’accueil s’affiche mais que les téléchargements HTML et PDF renvoient une
erreur 404, vérifier d’abord la source dans **Settings → Pages**. Elle doit être
**GitHub Actions**, et non **Deploy from a branch**. La publication depuis une
branche peut servir le README avec Jekyll à la place du site construit dans
`out/` ; les exports, absents des sources Git, ne sont alors pas disponibles.

Dans **Actions**, vérifier les jobs `build` et `deploy` de **Documentation
MkDocs** sur la branche par défaut. Un déploiement documentaire réussi ne
garantit pas qu’une publication concurrente ne l’a pas remplacé. La présence
d’un workflow automatique **pages build and deployment** et de la mention
`Jekyll` dans le code source de l’accueil permet de repérer cette situation.

Après avoir sélectionné **GitHub Actions**, relancer **Documentation MkDocs**
sur la branche par défaut. Vérifier ensuite l’accueil avec sa navigation
MkDocs et ouvrir les deux liens `downloads/documentation.html` et
`downloads/documentation.pdf`. Si un job échoue, consulter son journal avant
de relancer la publication.

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
ressources embarquées, exclusions, sommaires et protection des dossiers.
Un test supplémentaire vérifie le cadrage de figures larges et hautes en mode
impression avec Chromium, ainsi que l’interligne des libellés SVG entre écran et
impression ; il est ignoré si le navigateur ou Playwright manque.
Une construction complète vérifie en plus le rendu
Chromium et les vrais exports. Les scripts Mermaid des pages individuelles
s’exécutent dans le navigateur du lecteur ; les exports contiennent leur SVG.

### 4.1. Proportions des décisions dans les diagrammes

Le constructeur adapte les losanges du moteur flowchart par défaut de Mermaid
10.9.5 avant le calcul du placement. Leur largeur augmente de 20 % par rapport
au carré initial ; leur hauteur est calculée à partir du libellé, sans changer
la taille du texte. Cette géométrie sert aussi au raccordement des flèches.
Le site, le HTML autonome et le PDF utilisent la même adaptation.

Les schémas utilisent des retours à la ligne explicites `<br/>` dans les
libellés longs, aux limites des groupes de mots. Le texte et la taille des
caractères sont conservés ; un retour à la ligne peut augmenter la hauteur
d’un bloc, ce qui demande de vérifier le diagramme complet après modification.

Si `p` et `v` sont la largeur et la hauteur du libellé avec ses marges, la
largeur du losange est `W = 1,2 × (p + v)` et sa hauteur
`H = v / (1 − p / W)`. La relation `p/W + v/H = 1` place les coins de la boîte
du texte sur les côtés du losange. Les libellés sur plusieurs lignes restent
donc contenus dans la forme. Les dimensions sont exprimées dans les unités SVG.

Cette adaptation est spécifique au code de la version épinglée et au moteur
flowchart par défaut ; elle ne modifie pas les autres formes. Le cache original
est conservé, et une copie `mermaid-10.9.5-wide.min.js` est préparée à chaque
construction. Si la signature du moteur change, la construction échoue
explicitement : une mise à jour de Mermaid nécessite de revoir l’adaptation et
de vérifier les choix courts, longs et multilignes ainsi que les flèches.

### 4.2. Placement automatique et espacement

`DocumentationBuilder._diagram_renderer` fournit les mêmes réglages au site et
aux exports : `nodeSpacing = 24` entre symboles d’un même niveau et
`rankSpacing = 16` entre niveaux, en unités SVG. Ces valeurs sont des distances
minimales pour Dagre ; le moteur peut les augmenter pour placer les branches,
les libellés de flèches et les boucles sans superposer les symboles.
Une marge de 6 unités au-dessus et de 12 unités sous les titres des groupes
préserve leur lisibilité face au premier symbole.

Mermaid 10.9.5 impose 50 unités dans certains sous-graphes. L’adaptation du
constructeur remplace ces valeurs internes par les espacements du parent,
y compris pour les groupes imbriqués. Cette adaptation est contrôlée par une
signature de version, comme celle des losanges.

Aucune coordonnée ni translation corrective n’est ajoutée aux symboles ou aux
flèches. Le moteur recalcule tout le placement à chaque génération, selon les
textes et les relations présents. Les nouveaux diagrammes bénéficient des
réglages communs sans directive locale ; une directive Mermaid explicite peut
les surcharger. Le rendu Markdown directement sur GitHub utilise son propre
moteur et ne reçoit pas ces réglages du constructeur.

L’ajout d’un texte ou d’une branche peut modifier la disposition : le calcul
automatique ne garantit pas un dessin identique après évolution. Les tests
vérifient la compacité et l’absence de superposition après ajout d’une branche,
d’une boucle et d’un libellé multiligne dans des groupes imbriqués. Une mise à
jour de Mermaid exige également de reconstruire et contrôler les exports.
Le workflow lance les tests après la construction, pour que le cache Mermaid
soit disponible et que ces vérifications de rendu s’exécutent avant publication.

### 4.3. Conformité du séquencement

La [référence de vérification des diagrammes](DIAGRAMS.md) associe chacun des six
schémas à ses méthodes d’implémentation, ses tests et leurs limites. Les tests
de mise en page ne remplacent pas cette comparaison des flèches au code réel.

Références : [MkDocs](https://www.mkdocs.org/),
[GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages),
[Playwright](https://playwright.dev/python/docs/browsers).
