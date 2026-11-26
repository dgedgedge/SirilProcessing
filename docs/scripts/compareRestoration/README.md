# 1. Comparer les restaurations

[Manuels des scripts](../README.md)

## 1.1. Objectif et prérequis

`bin/compareRestoration.py` produit un tableau comparatif des traitements seuls,
des chaînes et de leurs critères de qualité à partir des `restoration.json`
existants. Il fonctionne avec Python 3.10 ou plus et la bibliothèque standard,
sans installation supplémentaire, sans Siril et sans GPU. Depuis la racine du
dépôt, utiliser le Python de l'environnement existant ou `python3`.
Il ne modifie aucun rapport source ni aucune image et ne relance aucun calcul.

## 1.2. Utilisation

Comparer plusieurs objets en choisissant un préfixe de sortie :

```bash
.venv/bin/python bin/compareRestoration.py \
  /siril/sirilHome/Luc_M45 /siril/sirilHome/M_33 \
  /siril/sirilHome/NGC7331 /siril/sirilHome/NGC_7380 \
  -o /tmp/restoration_comparison
```

Pour comparer les rapports directement accessibles dans une collection :

```bash
.venv/bin/python bin/compareRestoration.py /siril/sirilHome \
  -o /tmp/restoration_comparison_all
```

Les entrées peuvent être un fichier JSON, un dossier `05_restoration`, un
dossier d'objet ou une collection d'objets. Les chemins sont dédoublonnés.
L'option `--recursive` recherche aussi dans tous les sous-répertoires.

## 1.3. Résultats

Le script écrit `<préfixe>.html` et `<préfixe>.csv` ; par défaut le préfixe est
`restoration_comparison` dans le dossier de lancement. Relancer avec le même
préfixe remplace ces deux exports. Leurs chemins sont affichés sur stdout ;
la progression et les erreurs sont écrites sur stderr.

Le HTML est autonome, sans connexion réseau. Il propose une recherche textuelle,
un filtre de portée et le tri par clic sur un titre de colonne. Les critères
de qualité occupent une seule colonne large : chaque critère a sa propre ligne,
avec son nom, son verdict et les valeurs disponibles, seuils et drapeaux
bloquants. Les verdicts PASS, FAIL et UNKNOWN ont des couleurs distinctes
et restent écrits en toutes lettres. Les champs sans valeur sont masqués ;
les valeurs zéro et False restent affichées. Le CSV utilise
UTF-8 avec BOM et le point-virgule, avec une colonne par champ de critère, pour
les tableurs. Les champs numériques gardent leur précision enregistrée dans
le CSV ; les nombres du HTML sont présentés avec sept chiffres significatifs.
Les chaînes textuelles pouvant être interprétées comme des formules de tableur
sont préfixées d'une apostrophe dans le CSV.

Les variantes finales et les opérations locales ont des lignes distinctes :
les premières comparent la chaîne à l'entrée originale de restauration ; les
secondes comparent une opération à son entrée effective, éventuellement déjà
traitée. Les tentatives internes d'un moteur ne sont pas des chaînes
supplémentaires et ne sont pas développées dans ces tableaux.

Les colonnes exposent le statut, le choix retenu, le score, les ratios de FWHM,
le gain de bruit utile, le ratio maximal de bruit, la fraction maximale d'anneaux,
la durée enregistrée, les motifs et l'étape de rejet, ainsi que le statut du
rapport et sa date de modification en UTC. Les chemins d'entrée, de sortie et
de rapports ne sont pas affichés dans le comparatif.
Tous les critères présents sont conservés sous forme synthétique, avec leurs
seuils, verdicts et drapeaux bloquants lorsqu'ils sont enregistrés.

## 1.4. Erreurs et limites

Une entrée absente, un dossier sans rapport, un JSON tronqué ou modifié pendant
la lecture, ou un ancien rapport sans variantes est signalé explicitement.
Les rapports valides restants sont exportés, mais le code de retour vaut 1
si une erreur est survenue, même partielle ; il vaut 0 sinon.

Attendre la fin du traitement pour un bilan complet. Un rapport stable pendant
la lecture peut néanmoins ne contenir qu'une partie des essais d'un calcul en
cours. Le script n'affirme pas que ce calcul est terminé.

Une cellule vide indique une mesure absente, jamais zéro. Un contrôle `FAIL`
non bloquant peut coexister avec une chaîne acceptée. `UNKNOWN` ne prouve pas la
conservation du signal. Le score sert au classement des essais d'une même
image ; comparer ses valeurs entre objets ne mesure pas leur qualité relative.
Les durées locales et finales correspondent à des périmètres différents et ne
constituent pas automatiquement le coût total d'une chaîne avec cache.

La lecture progressive décode un objet de variante ou d'opération à la fois :
elle réduit la mémoire nécessaire par rapport au chargement du rapport entier,
mais une opération contenant beaucoup de tentatives peut encore être volumineuse.
Les détails bruts restent dans les rapports sources.

[Options](OPTIONS.md) · [Format et extraction](FORMAT.md) ·
[Chaînes de restauration](../postProcess/restoration.md)

Les rapports issus d'un arrêt anticipé de restauration sont signalés dans le
HTML, avec le nombre de variantes évaluées et prévues. Seuls les essais
réellement exécutés ont des lignes : les autres ne sont pas des rejets.
