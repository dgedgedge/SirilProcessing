# Instructions de travail — SirilProcessing

Ces consignes s'appliquent à l'ensemble du dépôt. Respecter en priorité les
instructions explicites de l'utilisateur et les règles de l'environnement.

## Communication et périmètre

- Répondre en français, sauf demande contraire. Conserver le français dans
  l'interface et la documentation existantes. Rédiger les propositions de
  messages de commit et de descriptions de pull request en anglais par défaut.
- Effectuer les modifications demandées et leurs vérifications pertinentes.
  Résoudre les choix courants en suivant les conventions existantes ; demander
  une précision lorsqu'une ambiguïté change réellement le résultat attendu.
- Lire le code concerné et sa documentation avant de modifier son comportement.
  Préserver les modifications locales de l'utilisateur et limiter les changements
  au périmètre demandé.
- Expliquer brièvement le résultat, les vérifications réellement effectuées et
  les limites restantes. Ne jamais annoncer un test ou une action non exécutés.

## Organisation du projet

- Le projet regroupe les traitements astronomiques Siril et des visualiseurs
  autonomes de journaux KStars/Ekos.
- `bin/` contient les points d'entrée Python, les interfaces et les lanceurs
  shell ; `lib/` les traitements partagés ; `tests/` les tests pytest ;
  `docs/` la documentation utilisateur et d'architecture.
- Réutiliser les mécanismes existants pour les paramètres, la configuration,
  la journalisation, les chemins et les appels Siril. Respecter la priorité
  ligne de commande > configuration JSON > défauts et la sauvegarde explicite.
- Garder les visualiseurs KStars utilisables sans Siril. Leurs dépendances
  sont déclarées dans `requirements-analyze.txt`.
- Conserver la cohérence entre interface native, ligne de commande et export
  HTML pour les mesures et les comportements communs.

## Qualité du code Python et documentation intégrée

- Appliquer PEP 8 pour le style et PEP 257 pour les docstrings, en respectant
  les outils et conventions déjà configurés dans le dépôt. Éviter les
  reformattages sans rapport avec la modification demandée.
- Toute fonction, méthode, classe ou module Python ajouté doit avoir une
  docstring utile. Lorsqu'un élément existant est modifié, compléter ou corriger
  sa documentation pour refléter son contrat réel.
- Décrire dans les docstrings le rôle et, lorsqu'ils s'appliquent, les paramètres,
  les unités, les valeurs retournées, les exceptions, les effets de bord et les
  contraintes. Pour les algorithmes, préciser les hypothèses, les limites
  numériques et le lien vers la documentation spécialisée. Garder un format
  cohérent dans chaque module ; ne pas simplement reformuler le nom de la fonction.
- Annoter les paramètres et les retours des fonctions ajoutées ou modifiées
  avec des types précis. Éviter les types vagues et les suppressions de contrôles
  sans justification. Les annotations ne remplacent pas la validation des entrées.
- Écrire des fonctions à responsabilité claire, des noms explicites et des
  interfaces simples. Séparer calculs, accès aux fichiers et présentation pour
  faciliter les tests. Mutualiser les comportements réellement communs sans
  ajouter d'abstractions inutiles.
- Remplacer les constantes numériques non évidentes par des noms explicites
  et documenter leur unité et leur justification. Commenter les choix et les
  contraintes, plutôt que paraphraser des instructions évidentes.
- Valider les entrées aux frontières du système ; traiter explicitement les
  données vides, invalides et non finies. Intercepter des exceptions précises,
  conserver le contexte des erreurs et ne pas masquer un échec par un résultat
  apparemment valide. Utiliser des gestionnaires de contexte pour les ressources.
- Éviter les états globaux mutables inutiles, les duplications, les imports
  inutilisés et le code mort. Préserver la compatibilité Python du projet et
  justifier les nouvelles dépendances.
- Exécuter les contrôles de style, d'analyse statique ou de typage déjà configurés
  lorsqu'ils concernent la modification. Ne pas affirmer qu'ils sont disponibles
  ou réussis sans vérification ; signaler les contrôles qui n'ont pas pu être faits.

## Données et calculs

- Préserver les fichiers sources FITS et journaux ; écrire les résultats dans
  des sorties distinctes. Ne pas ajouter au dépôt les données personnelles,
  les journaux d'observation ou les rapports générés sans demande explicite.
- Documenter les unités, conversions et définitions statistiques. Ne pas
  remplacer une donnée absente ou une mesure rejetée par zéro.
- Distinguer les faits enregistrés des interprétations : `StarMass` n'est pas
  un nombre d'étoiles ; un rejet de mesure n'établit pas seul sa cause physique.
- Respecter les frontières entre fichiers et séquences. Ne pas inventer des
  mesures, des horaires ou une continuité pendant les interruptions.
- Pour les guide logs, conserver la calibration précédente du même fichier
  avec les guidages suivants, sans doublonner ses trajectoires à l'affichage.

## Vérifications

- Utiliser l'environnement Python du projet lorsqu'il est disponible :
  `.venv/bin/python -m pytest -q tests/test_<module>.py` pour les tests ciblés.
  Adapter le nom du fichier aux changements ; ne pas supposer que le venv existe.
- Pour les deux visualiseurs KStars :
  `.venv/bin/python -m pytest -q tests/test_guide_log_analyze.py tests/test_kstars_analyze.py`.
- Ajouter des tests de régression lorsqu'un changement de calcul, de lecture
  ou de comportement le justifie. Une correction purement rédactionnelle ne
  nécessite pas de nouveaux tests automatisés ; une nouvelle affirmation sur
  une fonctionnalité doit en revanche être vérifiée selon les règles ci-dessous.
- Vérifier les lanceurs modifiés avec `bash -n` et les différences avec
  `git diff --check`. Tester les interactions graphiques concernées si un
  affichage est disponible ; sinon indiquer cette limite.
- Distinguer tests unitaires, essais sur exemples réels et validation avec Siril.
  Ne pas présenter les tests ciblés comme une validation de toute la suite.

## Documentation des scripts, modules et algorithmes

- Mettre à jour la documentation dans la même modification que le code concerné.
  La documentation intégrée au code et les guides utilisateur sont complémentaires.
- Fournir une page de description pour chaque script : objectif, prérequis,
  installation, entrées, sorties, options, valeurs par défaut, exemples de
  lancement, erreurs possibles et limites. Documenter le lanceur shell sur la
  même page et garder l'aide `--help` cohérente.
- Fournir des pages spécialisées distinctes pour les modules et algorithmes
  utilisés : responsabilités, interfaces, étapes du traitement, hypothèses,
  formules et unités, paramètres, cas limites, limites de validité et références
  pertinentes. Décrire les performances et les ressources lorsque cela aide
  à comprendre les choix ou à utiliser le traitement.
- Ranger les détails techniques dans l'ensemble documentaire du traitement
  concerné, conformément à `docs/CONTRIBUTING.md`. Garder `docs/architecture/`
  consacré aux responsabilités et aux relations générales entre modules.
- Relier les guides des scripts aux pages spécialisées et celles-ci à leurs
  sommaires. Conserver une page de référence par sujet pour éviter les copies
  divergentes. Vérifier les liens et exemples modifiés.

## Vérification des fonctionnalités documentées

- Toute nouvelle fonction ou fonctionnalité décrite dans la documentation doit
  être vérifiée dans l'implémentation et par une exécution pertinente avant
  d'être présentée comme disponible et validée. Cette exigence s'applique aussi
  lorsqu'une documentation est ajoutée à du code déjà existant.
- Vérifier les options, valeurs par défaut, unités, sorties et erreurs annoncées.
  Exécuter les nouveaux exemples sur des données de test adaptées ; confronter
  les résultats à des attentes explicites, pas seulement à l'absence d'exception.
- Utiliser selon le cas un test automatisé, un essai reproductible en ligne de
  commande, une vérification de l'interface ou une validation avec Siril. Pour
  un algorithme, couvrir un cas nominal et les cas limites pertinents, avec des
  valeurs de référence ou des propriétés vérifiables.
- Indiquer dans le compte rendu ou la pull request la fonctionnalité vérifiée,
  la commande ou le scénario utilisé et le résultat. Ne pas inventer de résultat.
- Si une dépendance, des données ou un affichage manquent, indiquer précisément
  ce qui reste non vérifié. Ne pas présenter la fonctionnalité comme validée ;
  documenter clairement cette limite et la procédure de vérification restante.

## Documentation et pull requests

- Suivre `docs/CONTRIBUTING.md` pour l'organisation documentaire. Mettre à jour
  la page de référence lorsque le comportement, les options ou les défauts
  changent. Relier les nouvelles pages depuis leur sommaire.
- Documenter un lanceur shell avec le script qu'il exécute. Garder l'aide
  `--help` cohérente avec les chemins et valeurs par défaut du programme.
- Utiliser `main` comme branche de référence, sauf indication contraire.
  Préparer les synthèses à partir de `git log main..HEAD` et
  `git diff main...HEAD`, en distinguant les modifications locales non commitées.
- Dans une pull request, décrire le problème résolu, le comportement final,
  les changements de valeurs par défaut et les vérifications effectuées.
