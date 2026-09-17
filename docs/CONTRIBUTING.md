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

L’accueil HTML est généré à partir du README du projet ; chaque autre Markdown
conserve son emplacement, avec l’extension `.html`. Ajouter une page demande
donc de la relier depuis le README de son ensemble, pour qu’elle soit accessible
dans les deux formats. La navigation HTML ne fournit pas de sommaire parallèle.

## Mise à jour

Décrire le comportement présent du code. Les changements passés sont consultables
dans Git ; ils ne constituent pas le plan de la documentation utilisateur.
Lorsqu’un traitement change, mettre à jour sa page de référence, les exemples,
les valeurs par défaut et les liens concernés.

Déplacer les fichiers suivis avec `git mv` et supprimer ceux qui sont devenus
inutiles avec `git rm`. Après une réorganisation, vérifier les liens relatifs
et reconstruire le [site HTML](scripts/generate_docs_html/README.md).
