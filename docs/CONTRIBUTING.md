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

## Mise à jour

Décrire le comportement présent du code. Les changements passés sont consultables
dans Git ; ils ne constituent pas le plan de la documentation utilisateur.
Lorsqu’un traitement change, mettre à jour sa page de référence, les exemples,
les valeurs par défaut et les liens concernés.

Déplacer les fichiers suivis avec `git mv` et supprimer ceux qui sont devenus
inutiles avec `git rm`. Après une réorganisation, vérifier les liens relatifs
et reconstruire le [site HTML](scripts/generate_docs_html/README.md).
