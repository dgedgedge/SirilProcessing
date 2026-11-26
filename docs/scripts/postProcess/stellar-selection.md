<a id="1-contraste-stellaire-a-profils-conserves"></a>
<a id="1-contraste-stellaire-à-profils-conservés"></a>
<a id="11-objectif-et-modes"></a>
<a id="11-objectif-et-parametre"></a>
<a id="12-base-et-mesure-des-profils"></a>
<a id="12-construction-de-la-courbe"></a>
<a id="13-gardes-de-largeur-et-de-bruit"></a>
<a id="13-renforcement-du-contraste-sans-voile"></a>
<a id="14-rapport-et-recette"></a>
<a id="14-rapport-reproduction-et-limites"></a>
<a id="15-limites-et-verifications"></a>
<a id="2-courbe-globale-ponctuelle-mode-optionnel"></a>
<a id="21-objectif-et-parametre"></a>
<a id="21-objectif-et-paramètre"></a>
<a id="22-construction-de-la-courbe"></a>
<a id="23-gardes-de-largeur-et-de-bruit"></a>
<a id="24-rapport-et-recette"></a>
<a id="25-limites-et-verifications"></a>
<a id="25-limites-et-vérifications"></a>

# 1. Courbe globale de l'histogramme stellaire

[Manuel](README.md) · [Rendu stellaire](star-rendering.md)

## 1.1. Contrat global

À la demande explicite de l'utilisateur, l'étirement de la couche étoiles
applique une transformation identique à tous les triplets RGB de l'image.
Il n'emploie aucun masque, gain par étoile, correction du fond local ou
suppression de bruit. Deux pixels RGB identiques doivent être transformés
identiquement, même avec des voisins et positions différents.

## 1.2. Construction de la transformation

La base est un asinh lié à la luminosité, complété par une fonction rationnelle
des faibles niveaux, avec protection RGBBlend du plafond. Une distribution de
pics peut calibrer une courbe monotone d'intensité ; après calibration, les
paramètres sont fixes pour toute l'image. Aucune coordonnée stellaire n'est
utilisée pour appliquer la transformation.

`--star-boost-fraction 0.25` choisit le quantile initial de calibration. Zéro conserve
la base ; un utilise l'adaptation globale complète. Pour une fraction entre
zéro et un, les quantiles fixent les raccords entre niveaux d'intensité.
`--star-histogram-strength`, dans [0,1], mélange cette courbe avec la base.
Le plafonnement monotone `monotone_cap_v1` évite une inversion au raccord des
noyaux brillants. La courbe agit sur toutes les intensités correspondantes,
y compris celles qui ne sont pas des étoiles détectées.

## 1.3. Choix des paramètres et contrôles

Les profils mesurés, leurs largeurs et les histogrammes servent à accepter ou
réduire les paramètres de la courbe globale. Les gardes ne créent aucun
traitement local. La force retenue reste identique pour toute l'image.
Une garde non mesurable ne constitue pas une validation physique.

L'[objectif de visibilité](stellar-appearance.md) `--star-visible-fraction 0.30`
peut élargir le quantile et ajuster le mélange de la courbe pour révéler davantage
d'étoiles. Il sature le bénéfice à la cible et contrôle les profils à 50 %, 10 %
et 5 % du pic, par luminosité. S'il n'est pas atteignable sans dégradation,
le rendu reste sûr et signale l'objectif non atteint. Zéro désactive cette
recherche supplémentaire ; aucun traitement local n'est introduit.

`--star-contrast-mode pointwise` est le défaut. Une ancienne configuration
`profiles` produit également cette courbe globale. `--star-noise-sigma` vaut
zéro ; ses anciennes valeurs valides restent acceptées sans effet. Les
contrôles de domaine et de finitude restent appliqués aux anciens paramètres.

## 1.4. Audits et reproduction

Le rapport indique `histogram_only: true`, `spatially_varying: false`,
`contrast_mode: pointwise` et `noise_suppression.threshold: 0`.
`boost_selection.mode` est `histogram_quantile`, ou `all` pour la courbe
complète. Malgré le nom historique du champ, il ne décrit pas de masque
spatial ; les coordonnées, rayons et gains par étoile n'y figurent pas.

La recette fixe se rejoue sur une découpe ou une image déplacée sans exiger
les dimensions natives : elle reste la même fonction des valeurs RGB.
Sa courbe scalaire porte `scalar_role: neutral_rgb_restriction` : elle est
la restriction aux gris R=G=B, pas une LUT exacte de tous les pixels colorés.

Les anciennes recettes `spatial`, `profiles` et `contrast_profiles` demeurent
lisibles uniquement pour reproduire les archives. Les utilitaires de rejeu
historique ne sont pas appelés pour créer les nouveaux rendus stellaires.
Pour remplacer un ancien résultat local, relancer StarNet et les étirements
avec `--autoghs-only` ; `--color-only` conserve les couches déjà étirées.

## 1.5. Vérifications et limites

Les tests vérifient l'égalité des sorties pour des triplets identiques dans
des voisinages différents, le rejeu exact, la permutation et la découpe des
pixels après calibration, les paramètres historiques sans effet local et
l'absence de seuil de bruit actif. Les valeurs FITS sources restent préservées.
Une courbe globale peut amplifier le bruit résiduel ou élargir des profils ;
ces effets ne doivent pas être compensés par une correction locale dans cette
branche. La restauration et StarNet restent des étapes distinctes.
