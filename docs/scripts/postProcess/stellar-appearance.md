# 1. Apparence des étoiles et sélection de la courbe globale

[Rendu stellaire](star-rendering.md) · [Courbe globale](stellar-selection.md)

## 1.1. Périmètre

`lib.stellar_appearance.StellarAppearanceQuality` mesure le résultat de la
courbe globale sur la couche étoiles. Il ne modifie aucun pixel, ne produit
aucun masque ou gain spatial et ne choisit lui-même aucun essai. Le moteur
stellaire utilise sa mesure de visibilité pour choisir une courbe globale,
sous les gardes de profils décrites ci-dessous.
Ses indicateurs figurent dans `stars.json`, sous `appearance_quality`, et dans
le PNG de diagnostic stellaire. La restauration et le fond restent indépendants.

La visibilité réelle dépend du fond après fusion, de l'écran, du gamma,
de l'échelle d'affichage et de l'observateur. Les mesures suivantes sont des
proxys d'affichage normalisé, pas un modèle calibré de l'œil humain.

## 1.2. Objectif de visibilité : au moins 40 %

La population est celle des pics candidats détectés dans l'entrée stellaire
linéaire par le détecteur existant. Elle reste fixe avant/après ; il ne s'agit
pas d'un catalogue certifié ni d'un décompte de toutes les étoiles physiques.

Le contraste est la luminosité `Y = 0,2126 R + 0,7152 G + 0,0722 B` au pic
moins la médiane du bord d'un carré
centré de rayon 32 pixels. Un contraste **au moins égal à 0,03** est considéré
visible. Ce seuil diffère du compteur historique en max RGB brut, qui est
également conservé dans les diagnostics. Un fond élevé ne suffit donc pas à
rendre une étoile visible selon le nouvel indicateur.

Les candidats trop proches des bords pour mesurer leur fond sont inconnus.
Avec N candidats, V visibles mesurés et U inconnus, la fraction de visibilité
se situe dans `[V/N, (V+U)/N]`. L'objectif indicatif est `PASS` si la borne basse
est au moins 0,40, `FAIL` si la borne haute est inférieure à 0,40 et `UNKNOWN`
sinon. Sans candidat, les fractions sont absentes, pas nulles. Tous les verdicts
portent `blocking: false` : un objectif inaccessible ne fait pas échouer le
rendu. Il influence cependant réellement les paramètres de la courbe retenue.

`--star-visible-fraction` fixe cet objectif entre 0 et 1 (défaut `0.40`).
Si la courbe initiale l'atteint déjà, elle est conservée. Sinon, jusqu'à sept
courbes globales supplémentaires sont évaluées : quantiles de calibration
élargis vers la cible puis vers 1, et mélanges du calage complet à 0,125, 0,25,
0,5 et 1 fois l'intensité demandée. Cela permet d'augmenter progressivement la
visibilité sans passer directement à une adaptation complète trop forte.

Toutes utilisent les mêmes positions candidates initiales. La sélection
favorise d'abord la fraction visible mesurée, avec un bénéfice saturé à la
cible. Une fois la cible atteinte, elle préfère les profils les plus proches
de ceux de l'entrée, puis le plus petit changement du quantile demandé.
Elle ne cherche donc ni le maximum d'étoiles à tout prix, ni les étoiles les
plus fines. La fraction obtenue peut dépasser la cible : ce n'est pas un quota.

Les candidats supplémentaires doivent respecter la limite de largeur
`--star-stretch-max-width-ratio` (ou la limite commune héritée), à 50 %, 10 %
et 5 % du pic, avec un plancher de ratio de 0,90. Les médianes sont contrôlées
par groupes de luminosité, y compris le groupe brillant, pour surveiller à la
fois finesse et jupe. Une référence de profil devenue non mesurable ne peut
pas disparaître du contrôle pour rendre le candidat admissible. Ce sont des
profils axiaux sur 15 pixels, pas des PSF ajustées ni une garantie perceptuelle.
Les ailes plus larges que la fenêtre restent une limite de cette mesure.

Sans amélioration admissible, la courbe initiale sûre reste utilisée et
l'objectif non atteint est enregistré. Avec une visibilité entièrement
inconnue, aucune recherche supplémentaire n'est faite. Une cible nulle,
`--star-histogram-strength 0` ou `--star-boost-fraction 0` désactive également
cette recherche. Les anciens réglages explicites restent donc respectés.
`visibility_selection` conserve les essais, gardes, objectif et paramètres
retenus ; les galeries enregistrent les recettes globales rejouables.

## 1.3. Taille apparente, transition et jupe

Au plus 64 candidats visibles sont répartis en amplitude pour les profils.
Les mêmes positions sont mesurées avant/après. Seize rayons sont échantillonnés
par interpolation bilinéaire tous les 0,5 pixel jusqu'à 32 pixels. Les traversées
sont interpolées ; chaque rayon doit fournir une mesure pour que sa médiane
angulaire soit publiée. Les absences de traversée restent inconnues.

Le rayon r80 est la première traversée descendante de 80 % du contraste du
pic au-dessus du fond local ; r50, r20 et r5 correspondent à 50, 20 et 5 %.
Les indicateurs sont :

| Indicateur | Définition et interprétation |
|---|---|
| Diamètre visible | Deux fois le rayon au contraste absolu 0,03, en pixels |
| Largeur de transition | `r20 - r80`, en pixels ; une largeur faible caractérise une chute plus abrupte |
| Gradient de transition | `0,6 × contraste_du_pic / (r20 - r80)`, unités normalisées par pixel |
| Gradient relatif | `0,6 / (r20 - r80)`, par pixel ; distingue la pente de l'amplitude du pic |
| Étendue relative de la jupe | `r5 / r50`, sans unité ; une grande valeur signale des ailes faibles étendues |

Le rapport publie médianes, effectifs et comparaisons avant/après sur des paires
mesurables. Une mesure après seule n'est pas comparée à un zéro fictif avant.
Les pixels des profils ne sont jamais modifiés.

La netteté et la jupe restent `UNKNOWN / NO_CALIBRATED_SHAPE_TARGET` au niveau
de l'objectif : leurs valeurs sont disponibles, mais aucun seuil de réussite
perceptuelle n'est encore fixé. Un profil mesuré n'est pas un objectif validé.
Les profils confondus avec des voisins, non monotones, tronqués ou saturés
peuvent biaiser les premières traversées. La médiane du bord peut être biaisée
par la nébulosité ou un champ dense. Ce ne sont ni des PSF ajustées ni des
mesures photométriques. Les ailes qui dépassent la fenêtre restent inconnues.

## 1.4. Vérifications

Les tests couvrent le seuil exact de 40 %, les bords et champs vides, la
conservation des pixels, les rayons et gradients gaussiens analytiques, la
transition plus large d'une étoile diffuse et les ailes plus étendues d'un
profil Moffat. Le rendu global et les recettes gardent leurs tests d'invariance
spatiale. `test_stellar_visibility_selection.py` vérifie aussi que deux objectifs
de visibilité produisent réellement des pixels différents, que les gardes de
finesse/ailes priment sur la cible et que la recette reproduit exactement la sortie.
