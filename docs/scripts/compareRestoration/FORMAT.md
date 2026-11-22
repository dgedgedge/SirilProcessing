# 3. Lecture et critères exportés

[Manuel](README.md)

## 3.1. Responsabilités du module

`lib.restoration_comparison` analyse des rapports, sans traiter d'image.
`discover` résout les entrées et retire les doublons ; `ReportReader` parcourt
le JSON ; `read_report` extrait les lignes compactes ; `export` écrit les deux
formats. `bin/compareRestoration.py` gère les arguments et les erreurs partielles.

Le décodeur JSON standard lit les champs de racine un par un et les tableaux
`variants` et `operations` objet par objet. L'indentation n'est pas imposée.
Les blocs de lecture grandissent de 1 à 16 Mio de caractères ; un objet entier
peut dépasser cette taille. Les clés répétées de racine, entrées non objets et
contenus après la racine sont refusés. Le lecteur compare taille et date du
fichier avant et après lecture pour détecter une modification concurrente.

## 3.2. Provenance des lignes

Les lignes `finale` viennent de `variants`. Le choix est celui du
`selected_chain` enregistré, sans recalcul de classement. L'entrée est le
`image_path` commun du rapport. Une variante exclue localement conserve ses
motifs et une évaluation finale absente reste vide.

Les lignes `locale` viennent de `operations`, une par calcul distinct. Les
`operation_id` des `steps` permettent de retrouver le préfixe effectivement
calculé ; le cache ne crée pas de lignes d'opération supplémentaires. L'entrée
est l'`input_image` enregistré. Le choix final n'est pas attribué aux étapes
intermédiaires.

## 3.3. Mesures et unités

Les ratios FWHM moyen et médian viennent de `score_details` lorsqu'ils sont
présents. Le ratio médian peut aussi venir de `comparison.paired_ratio.median`.
Le script ne reconstruit pas de ratio moyen absent de la décomposition du score.
Le gain de bruit utile vient de `score_details.useful_noise_gain`, sinon de
`artifacts.noise.proven_gain`. Le ratio de bruit et la fraction d'anneaux viennent
de `artifacts`. Les ratios sont après/avant ; les gains, seuils relatifs et
fractions sont sans unité. Les durées sont en secondes.

Les critères viennent de `criteria`, ou de `quality.criteria` si ce premier
champ est absent. Leurs champs scalaires sont conservés, ainsi que les
intervalles de deux éléments `interval` et `confidence_interval`. Les grandes
listes de mesures individuelles, régions, étoiles et résidus ne sont pas
recopiées. L'absence de drapeau `blocking` reste une absence : aucun caractère
bloquant n'est déduit du seul verdict. Il n'y a ni nouvelle pondération, ni
nouveau seuil, ni normalisation des scores entre objets.

Les comparaisons de qualité et leurs limites scientifiques sont décrites dans
[la restauration](../postProcess/restoration.md).

## 3.4. Rapports synthétiques v2

Le lecteur accepte aussi les rapports synthétiques de chaînes v2, avec les
mêmes colonnes de mesures. Les chemins d'entrée, de sortie et d'audit restent
des informations de lecture internes, sans colonnes dans le HTML ou le CSV.
Les populations détaillées ne sont pas rechargées pour remplir une valeur
agrégée absente. La détection d'une modification pendant la lecture contrôle aussi
l'identité du fichier, afin de détecter son remplacement atomique.

Voir [la séparation entre synthèses et audits](../postProcess/restoration-reports.md).

Le HTML rassemble tous ces critères dans une seule colonne « Critères de
qualité », une ligne nommée par critère présent. Le CSV conserve une colonne
par champ de critère pour les analyses dans un tableur.
