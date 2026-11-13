# 1. Recalage du point noir après chaque essai GHS

[postProcess](README.md) · [AutoGHS](auto-ghs.md) · [Profil par morceaux](ghs-histogram-shape.md)

## 1.1. Ordre des opérations

En mode `piecewise`, sélectionné par défaut, les moteurs suivent cet ordre :

1. produire le candidat GHS ou CDF ;
2. mesurer le pied sombre et appliquer un étirement affine commun aux RGB ;
3. mesurer l'histogramme, le bruit, le contraste, la couleur et l'écrêtage ;
4. vérifier la conformité puis comparer le score au rendu courant.

Ce recalage retire le décalage sombre créé par la courbe elle-même. Un BP
appliqué uniquement avant le GHS ne corrigeait pas ce décalage. Le mode
`--ghs-post-blackpoint off` désactive ce recalage supplémentaire. Le mode
historique `line` et le témoin `--ghs-final-mode fixed` conservent leurs règles.

Les essais complets, les graphiques et les JPEG enregistrés correspondent
aux **pixels après recalage**. Les rapports distinguent `BP`, appliqué avant
la courbe lorsqu'il existe, et `post_BP`, appliqué après. L'audit
`post_blackpoint` indique la population, le budget, la perte estimée ou exacte
et le caractère échantillonné du calcul.

## 1.2. Définition du pied de montée

Le pied de la première montée est approché par un quantile sombre conservateur,
sans chercher le minimum isolé d'un pixel ni le maximum du pic principal.
Pour chaque pixel actif, le calcul prend le minimum de ses canaux initialement
positifs. Le quantile dépend du budget `--ghs-blackpoint-loss`, égal à **0,001**
par défaut, soit au plus 0,1 % de pixels perdant au moins un canal positif.

Le module `lib.ghs_blackpoint.HistogramBlackpointNormalizer` compte ces minima
dans 4096 classes entre zéro et leur maximum. Il affine ensuite le quantile
en triant uniquement le préfixe de valeurs **positives** situé au plus au bord
droit de la classe repérée, avec une limite de **65 536 valeurs**. Les pertes
antérieures sont déduites du budget avant de choisir le rang ; les poids sont
respectés. Le seuil reste sous la valeur limite avec une **marge d'un epsilon
sur l'échelle normalisée [0,1]**, puis un arrondi `nextafter` vers zéro.
Cette marge vaut environ 1,19×10⁻⁷ en float32. Elle évite de pousser les
derniers canaux positifs vers des valeurs sous-normales, qu'une opération
ultérieure pourrait arrondir à zéro et compter comme une perte supplémentaire.
Un décalage inférieur à cette précision reste nul ; la limite de perte noire
n'est jamais relevée. Les valeurs liées ne sont pas coupées arbitrairement. Cela évite qu'un
premier bin contenant les pixels déjà perdus ramène inutilement le BP à zéro.
Si le préfixe dépasse cette limite de taille, la borne gauche conservative de
la classe est conservée : le calcul ne trie jamais une mosaïque entière. Une distribution constante reste inchangée. Avec un budget nul,
seul un déplacement sans perte de canal positif est autorisé.

L'histogramme diagnostique représente **Y**, tandis que le budget BP porte
sur le **minimum RGB positif initial**. Un pixel encore situé à droite de
l'histogramme Y peut avoir un canal proche de zéro ; la soustraction commune
peut annuler ce canal. Une zone presque vide à gauche de Y ne permet donc pas
de déduire le nombre de pixels menacés par le BP RGB. Les pertes antérieures
consomment aussi le budget : 0,0993 % déjà perdus ne laissent qu'environ
0,0007 % de marge sur un budget total de 0,1 %.

Un BP positif de quelques 10⁻⁵ peut être correct et pratiquement invisible
sur un graphique en intensité [0,1]. Le recalage précède néanmoins les mesures ;
il ne garantit pas que le pied apparent de Y soit exactement à zéro.
Les rapports donnent `threshold_basis`, `loss_fraction_before`,
`remaining_loss_budget`, `precision`, `precision_prefix_pixels`,
`quantile_limit` et `numerical_margin`.
L'encart distingue le BP effectivement appliqué, la perte antérieure et le
budget encore disponible. Un BP nul peut porter `limit_reason` : résolution
insuffisante du bin conservateur, seuil inférieur à la précision utile du
type ou absence de décalage positif sûr.

Ce pied est un **repère statistique de rendu**, pas une mesure physique du
fond de ciel. Une aile sombre étendue peut empêcher de déplacer tout le pied
apparent sans dépasser le budget. Le contrôle ne supprime pas cette protection
pour satisfaire une préférence visuelle.

Le même BP est appliqué aux trois canaux, avec le blanc fixé à un :

```text
RGB_corrigé = max(0, (RGB_candidat - post_BP) / (1 - post_BP))
```

Les rapports RGB peuvent changer par cette soustraction commune ; les gardes
de couleur sur les régions brillantes restent donc nécessaires après le BP.
Aucun filtrage spatial ni traitement des étoiles n'est ajouté.

## 1.3. Budget cumulatif et échantillonnage

Le traitement complet conserve un masque immuable des canaux positifs de
l'entrée linéaire. Les pixels déjà perdus consomment le budget des candidats
suivants. Un candidat dépassant ce budget est rejeté, même si son propre
recalage n'ajoute aucune perte. Les recalages préalables sur le minimum et les
expansions utilisent la même marge d’intensité sous le premier flottant positif,
pour éviter qu'un groupe de minima liés consomme un budget supplémentaire.

Le sondage stratifié utilise ses poids de population pour estimer le quantile.
Ce seuil estimé s'applique aussi au sondage spatial et aux sondes brillantes.
Le BP et la perte sont ensuite recalculés sur le champ entier avant les tests
finaux. Une perte de 0,1 % n'est pas certifiée depuis le seul sondage spatial.
Le candidat retenu est reconstruit avec le **BP complet enregistré**, sans
réestimer une nouvelle correction après le choix.

Le calcul fonctionne sur NumPy ou CuPy. Il ajoute un histogramme de minima et
un recalage affine ; il ne conserve pas de collection d'images GPU. Les JPEG
optionnels suivent le [mécanisme de captures sur disque](ghs-histogram-shape.md#151-images-jpeg-de-chaque-essai).

## 1.4. Interfaces et vérifications

`safe_threshold(limit, dtype)` fournit la borne avec sa marge numérique sur
[0,1] ; le recalage du minimum, l'expansion et le quantile après courbe
partagent ce calcul. Les limites non finies ou hors [0,1] et les types non
flottants sont rejetés.

`measure(data, budget, xp, weights=..., reference=...)` fournit le seuil et son
audit. `apply(...)` renvoie les pixels corrigés et cet audit. Une référence
absente utilise les canaux positifs de l'entrée de cet appel ; l'orchestrateur
fournit sa référence globale aux moteurs du traitement complet.
`post_process` écrit un FITS distinct et un rapport JSON, en conservant le
caractère linéaire ou non linéaire déclaré par le FITS source.

Les données RGB doivent être finies dans [0,1], les poids strictement positifs
et de même géométrie spatiale, et le budget dans [0,0,01]. Les entrées invalides
lèvent `ValueError` ; les erreurs de lecture et d'écriture sont propagées.

```bash
.venv/bin/python -m pytest -q tests/test_ghs_blackpoint.py \
  tests/test_ghs_histogram_shape.py tests/test_ghs_quartiles.py
```

Les tests couvrent le pied décalé, le blanc fixe, les minima liés, le budget
cumulatif, les poids de population, les distributions vides et constantes,
les entrées invalides et les artefacts du processeur autonome. Ils vérifient aussi le raffinement
avec des pertes antérieures dans le premier bin, sa version pondérée et la
borne mémoire du tri local. Les recalages répétés doivent conserver les
canaux positifs et ne pas épuiser le budget par érosion numérique. Un test du moteur compare les histogrammes reçus
par l'évaluation aux pixels effectivement renvoyés après BP, sur les sondages
et les validations complètes. Les essais
M33 17 et 19 sont également rejoués sur copies, recalés puis proposés au
sélecteur général : leur conformité est vérifiée sans exception par objet
ou par numéro d'essai.
