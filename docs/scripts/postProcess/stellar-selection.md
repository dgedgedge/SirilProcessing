# 1. Sélection d'une fraction des étoiles

[postProcess](README.md) · [Rendu stellaire](star-rendering.md) · [Recettes](stretch-transfer.md)

## 1.1. Objectif et paramètres

Le rendu asinh conserve une base commune à toutes les étoiles, puis réserve
son renforcement adaptatif supplémentaire à **25 % au maximum des pics
éligibles les plus marqués**. La règle utilise uniquement la couche d'étoiles
positive après compensation des résidus ; elle ne consulte ni fond, ni galaxie,
ni nom d'objet. Les mêmes options s'appliquent aux différentes cibles.

`--star-boost-fraction` vaut `0.25` par défaut, dans `[0,1]` :

- `0` conserve la base asinh sans supplément adaptatif ;
- `0.25` sélectionne au plus un quart des pics éligibles ;
- `1` retrouve le moteur adaptatif historique global ;
- `--star-histogram-strength 0` désactive également le supplément.

La fraction suit CLI > JSON > défaut et se mémorise avec `-S`. Une ancienne
configuration ne contenant pas cette clé adopte 0,25 ; une valeur sauvegardée
explicite reste prioritaire. La force du mélange reste contrôlée séparément
par `--star-histogram-strength`. Il ne s'agit pas de mettre 25 % de gain sur
toutes les étoiles, ni de garantir que 25 % seront perceptibles après fusion.

## 1.2. Étapes et formules

`ConservativeStarRenderer.render` de `lib/star_rendering.py` réalise le rendu ;
`lib/stellar_selection.py` fournit la sélection déterministe et la reconstruction
des poids spatiaux. Les fonctions `select_stellar_peaks` et
`stellar_selection_weights` utilisent des coordonnées natives FITS `(y,x)`.

1. Produire une base asinh et son renforcement fixe admissible. La garde de
   FWHM peut réduire le renforcement demandé ; le seuil de bruit retenu est
   ensuite fixé pour tous les essais de fraction.
2. Détecter les pics sur le maximum RGB, puis réserver le calage aux amplitudes
   strictement supérieures à `max(1e-5, médiane + 12*sigma)`. Le sigma robuste,
   en intensités normalisées, est celui décrit dans le rendu stellaire.
3. Pour n pics éligibles et une fraction f, autoriser `k = floor(f*n)` pics.
   La coupure est le plus grand pic exclu du budget ; les amplitudes égales à
   cette coupure sont toutes exclues. Ainsi les ex aequo ne conduisent pas à
   dépasser le budget et leur ordre de parcours ne décide pas de la sélection.
4. Calculer, pour chaque pixel, les distances euclidiennes en pixels aux pics
   sélectionnés et aux autres candidats. Le poids float32 est :

   ```text
   w = d_autres^4 / (d_sélection^4 + d_autres^4)
   ```

   Les ensembles sont distincts : w vaut exactement 1 aux sommets sélectionnés
   et 0 aux autres. Entre ces sommets, le supplément est réparti doucement.
5. Construire la table logarithmique des quantiles des pics comme dans le moteur
   historique. Avec `base` la courbe fixe et `adapté = max(base, table)`, essayer :

   ```text
   cible = (base + mélange * w * (adapté - base)) * porte_bruit
   gain = cible / max(R,G,B), ou zéro lorsque le maximum est nul
   sortie_RGB = entrée_RGB * gain
   ```

   Le gain est commun aux trois canaux. Le plafond reste 0,85 par défaut.
6. Essayer le mélange demandé puis ses moitiés, jusqu'à huit essais, avec un
   dernier essai nul. La FWHM médiane après/avant doit respecter la limite
   configurée ; les percentiles 10 des rapports des largeurs à 50 % et à 10 %
   doivent rester au moins à 0,9. Sans essai mesurable admissible, conserver la
   base et signaler le motif. Aucune mesure absente n'est remplacée par zéro.

Sans budget, sans assez de pics (huit et quatre amplitudes distinctes), ou si
les ex aequo éliminent toute la sélection, conserver la base. Une fraction non
finie ou hors domaine est refusée. Des coordonnées sauvegardées non entières,
hors image, partagées ou dupliquées sont également refusées à la réapplication.

## 1.3. Rapports, diagnostics et reproduction

`boost_selection` conserve la fraction demandée, l'effectif éligible, le budget,
la coupure, le nombre retenu, les coordonnées et `actually_boosted_count`.
Ce dernier compte les pics dont le maximum RGB dépasse la base de plus de
`1e-7`, pas le nombre d'étoiles physiques. Le mode historique global conserve
son effectif éligible mais ne recalcule pas ce dernier indicateur.
`fraction_adaptation` et `histogram_balance` décrivent les essais du supplément ;
`width_adaptation` garde l'historique indépendant du choix de la base.

Le diagnostic stellaire affiche la fraction et les effectifs. La recette RGB
exportée conserve la sélection : elle ne redétecte pas de pics et exige la
même géométrie native pour un mode spatial. La courbe scalaire représente
explicitement **l'enveloppe neutre des pics sélectionnés**, avec w fixé à 1.
Elle n'est pas une LUT universelle de l'image. Le JSON porte
`scalar_role: selected_star_neutral_envelope` ; la recette RGB complète reste
celle à utiliser pour reproduire tous les pixels, y compris les étoiles voisines.

## 1.4. Limites et ressources

Les pics candidats peuvent inclure des résidus compacts de séparation. Un budget
sur leurs amplitudes ne constitue ni un catalogue ni une classification physique.
Seul le niveau au sommet des pics non sélectionnés reste strictement identique
à la base ; leurs ailes peuvent recevoir une partie du supplément des voisines.
La pondération ne réalise pas de séparation des sources superposées. Les contrôles
locaux sur 64 profils au plus et des fenêtres de 15 pixels ne garantissent pas
la conservation de tous les halos ni de toutes les étoiles faibles.

Deux transformées de distance SciPy sont calculées pour une sélection spatiale.
Le coût et les tableaux temporaires croissent avec le nombre de pixels. Les
coordonnées enregistrées croissent avec le nombre de pics. Le mode 0 ou 1
évite ces transformées. Le fond GHS et la recomposition ne sont pas réoptimisés.

## 1.5. Vérifications

Les tests couvrent budgets, ex aequo, modes constants, coordonnées invalides,
poids exacts aux sommets, conservation des pics non sélectionnés, rapports RGB,
recettes spatiales et distinction entre enveloppe et reproduction exacte.
Les options sont vérifiées en CLI/JSON, sauvegarde et instantanés d'instances.

Les essais sur les couches existantes de M33, M31, NGC 7380 et NGC 7331 ont été
retenus à 25 %. Le contrôle de non-régression compare la recomposition obtenue
avec les fichiers de ces essais, à paramètres identiques. Cette vérification
réutilise les séparations et fonds déjà produits ; elle ne relance ni StarNet
ni Siril et ne valide pas une nouvelle chaîne scientifique complète.
