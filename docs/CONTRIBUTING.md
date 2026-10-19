# Organisation et maintenance de la documentation

[Documentation](README.md)

## Répartition des contenus

- `scripts/<script>/` : utilisation, options, traitements, formats et sorties propres au script.
- `scripts/lightProcess/treatments/` : parcours détaillé des lights, de la découverte aux résultats.
- `scripts/lightProcess/filter/stacking/` : sélection des poses, profils stellaires et Drizzle.
- `architecture/` : rôle général des bibliothèques et relations entre responsabilités, sans algorithmes ni référence d’API détaillée.

Chaque ensemble possède un `README.md` qui sert de sommaire. Un wrapper shell
est documenté avec le script Python qu’il lance. Une information détaillée
possède une page de référence ; les autres pages y renvoient.

## Pages maîtresses communes aux deux formats

Le `README.md` du projet contient une description synthétique et des liens
vers les sommaires maîtres. `docs/README.md` présente les ensembles,
`docs/scripts/README.md` mène aux scripts et `docs/architecture/README.md`
présente l’architecture. Les détails d’installation sont dans
[Installation](INSTALLATION.md).

L’accueil MkDocs est généré à partir du README du projet ; chaque autre Markdown
conserve son emplacement. MkDocs convertit `README.md` en `index.html` dans
chaque dossier, et les autres pages en `.html`. Ajouter une page demande
donc de la relier depuis le README de son ensemble, pour qu’elle soit accessible
dans les deux formats. La rubrique `nav` de `mkdocs.yml` définit le plan commun :
installation, manuels regroupés par script, architecture, puis maintenance et
tests. Chaque script propose des accès au manuel, aux options et aux traitements
détaillés. Les options sont regroupées dans une page de référence `OPTIONS.md`
(ou le guide d’options existant). Les anciens emplacements du manuel gardent
leurs ancres et renvoient à cette référence. Les entrées de `nav` visent les
pages Markdown, sans fragment ; les accès à un paragraphe sont des liens dans
le contenu du manuel, car MkDocs ne les traite pas comme des pages de navigation.
Le constructeur reprend cet ordre et cette hiérarchie pour le PDF et le HTML
autonome ; une page citée plusieurs fois n’est exportée qu’une fois. Les sources
complémentaires hors navigation sont placées en annexes des exports.

Les manuels et sommaires utilisent des paragraphes numérotés (`1.`, `1.1.`, etc.).
Lorsqu’un titre est numéroté ou déplacé, conserver ses anciennes ancres avec un
élément `<a id="ancienne-ancre"></a>` pour les liens existants. Vérifier les
ancres utilisées aussi bien par MkDocs que par le moteur du monofichier,
notamment pour les titres accentués. Les numéros d’étapes scientifiques peuvent
être précisés dans le titre sans servir de numérotation de paragraphe.

## Mise à jour

Décrire le comportement présent du code. Les changements passés sont consultables
dans Git ; ils ne constituent pas le plan de la documentation utilisateur.
Lorsqu’un traitement change, mettre à jour sa page de référence, les exemples,
les valeurs par défaut et les liens concernés.

Déplacer les fichiers suivis avec `git mv` et supprimer ceux qui sont devenus
inutiles avec `git rm`. Après une réorganisation, vérifier les liens relatifs
et reconstruire le [site HTML](scripts/generate_docs_html/README.md).
