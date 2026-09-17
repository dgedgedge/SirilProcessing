# Filtrage par les profils stellaires

[Documentation](../../../../README.md) › [lightProcess](../../README.md) › [Filtrage](../README.md) › [Stacking](README.md)


Le traitement mesure l’étalement et la forme des étoiles dans les pixels des
poses encore retenues. Il complète les mesures Siril sans exiger un nombre
d’étoiles élevé. Il s’applique dans `02_quality`, avant pondération et
rééchantillonnage, avec ou sans Drizzle.

## Choix de la méthode et littérature

La méthode associe un rayon de flux R80 à des moments d’ordre deux fenêtrés.
Ce choix vise les profils larges, allongés ou à plusieurs composantes.
Il ne prétend pas identifier la cause physique du défaut.

- Les moments fenêtrés permettent de mesurer la taille et l’orientation des
  sources tout en limitant l’influence du bruit extérieur. Les formules de
  covariance et d’axes sont décrites dans la documentation primaire de
  [SExtractor, *Windowed positional parameters*](https://sextractor.readthedocs.io/en/latest/PositionWin.html).
- Une courbe de croissance mesure le flux dans des ouvertures concentriques,
  après soustraction du fond. Elle permet de définir un rayon contenant une
  fraction donnée du flux. Voir
  [Photutils, *CurveOfGrowth*](https://photutils.readthedocs.io/en/stable/api/photutils.profiles.CurveOfGrowth.html).
- [Gillis et al. (2020), *Validation of PSF models for HST and other space-based observations*](https://academic.oup.com/mnras/article/496/4/5017/5861948)
  montrent notamment qu’une mesure concentrée sur le cœur peut manquer des
  défauts dans les ailes. Leur étude confronte aussi les mesures aux effets
  d’étoiles binaires, de fond et de guidage. Elle motive ici l’emploi de mesures
  complémentaires et de nombreuses étoiles ; son modèle optique HST n’est pas
  utilisé dans le code.

L’association R80/moments et les seuils ci-dessous sont un choix d’ingénierie
pour ce traitement. Les sources ne démontrent pas qu’elle est universellement
la plus performante et ne prescrivent pas ces seuils.

| Approche | Utilité et limite dans ce traitement |
|---|---|
| Nombre d’étoiles | Indice indirect ; un défaut peut garder un comptage ordinaire |
| Plafond FWHM fixe | Simple, mais dépend de l’échantillonnage et de la limite choisie |
| Ajustement d’un profil simple contre un profil double | Suppose une forme et une séparation adaptées ; peut manquer des traînées ou plusieurs composantes |
| R80 et moments sur plusieurs étoiles | Mesure directement l’étalement et l’allongement, sans imposer deux pics ni ajuster un modèle double |

## Mesures sur les pixels

`measure_stellar_profiles()` dans [lib/stellar_quality.py](../../../../../lib/stellar_quality.py)
reçoit un `SequenceImage` et lit son `processing_path`. Les FITS sont ouverts
en lecture seule. Les images RGB sont moyennées sur leurs canaux ; les CFA
sont moyennées par cellules 2 × 2 pour supprimer la modulation de la mosaïque.
Les rayons et coordonnées rapportés sont ramenés aux pixels natifs.

Le traitement sélectionne jusqu’à huit étoiles brillantes par secteur d’une
grille 3 × 3, soit 72 au maximum. Les pics proches partagent une même zone de
mesure : les composantes d’un dédoublement ne sont pas séparées artificiellement.
Les pixels non finis et les zones saturées sont écartés. Le seuil de saturation
provient de `SATURATE`, sinon du maximum du type entier ; pour les flottants
normalisés dont le maximum vaut exactement 1, cette valeur est utilisée.

Le fond local est un plan ajusté dans une couronne extérieure, avec trois
itérations de rejet à trois dispersions robustes. Il faut un pic à dix fois
le bruit du fond et un flux d’ouverture à cinquante fois le bruit intégré
approximatif. Les flux négatifs après soustraction du fond ne sont pas ramenés
à zéro, pour limiter le biais positif du bruit.

Une fenêtre gaussienne large sert au centrage et aux moments. Son sigma vaut
la médiane des FWHM Siril des poses restantes, avec un minimum de trois pixels
natifs. Contrairement à la fenêtre adaptative de SExtractor, cette largeur est
commune à la sélection pour conserver des mesures comparables et inclure les
composantes voisines. L’ouverture de flux s’étend à trois sigmas.

Pour chaque étoile sont conservés :

- `r80` : rayon contenant 80 % du flux mesuré dans cette ouverture finie,
  avec une intégration par sous-pixels 4 × 4 ;
- `size` : racine de la trace de la covariance fenêtrée, pour diagnostic ;
- `elongation` : rapport grand axe/petit axe, obtenu par les valeurs propres
  de cette covariance ;
- `e1`, `e2` : composantes d’ellipticité, utilisées pour vérifier l’orientation ;
- les coordonnées natives et le secteur du champ.

Une mesure qui se recentre trop loin, produit une covariance invalide ou dont
le R80 atteint 80 % du rayon d’ouverture n’est pas retenue. Il faut au moins
12 étoiles mesurables dans au moins trois secteurs pour caractériser une pose.

## Décision de rejet

`filter_stellar_profiles()` et `classify_stellar_profiles()` dans
[lib/quality_filter.py](../../../../../lib/quality_filter.py) appliquent les décisions.
Ils analysent uniquement les poses indépendantes encore retenues, sans les
répétitions de pondération. Les médianes par image servent à établir la référence.

Les groupes de comparaison ont les mêmes dimensions, organisation des canaux,
mosaïque, binning, taille de pixel et filtre. Une variation de focale de 2 %
autour de la première focale du groupe est admise pour les raffinements
astrométriques. Les métadonnées absentes doivent être absentes des deux côtés.
Il faut au moins huit poses mesurées dans le groupe.

Les deux seuils sont calculés une seule fois, avant les rejets de profils :

```text
dispersion = 1,4826 × médiane des écarts absolus à la médiane
seuil R80 = max(médiane R80 + k × dispersion R80, 1,25 × médiane R80)
seuil allongement = max(médiane allongement + k × dispersion allongement, 1,5)
```

Le coefficient `k` vaut 3 par défaut. Une pose est rejetée si au moins 60 %
de ses étoiles, réparties dans au moins trois secteurs, dépassent strictement
le seuil R80. Le critère d’allongement demande en plus que ces étoiles soient
orientées à moins de 30° de la direction collective, modulo 180°.
Cette direction provient des médianes de `e1` et `e2`.

Les marges de 25 % et le rapport minimal de 1,5 empêchent une dispersion quasi
nulle de rendre la sélection excessivement sensible. Une étoile double isolée
ne suffit pas à rejeter une pose. Un même défaut peut satisfaire les deux
critères ; les logs comptent le retrait une seule fois, R80 d’abord.

| Option | Défaut | Effet |
|---|---|---|
| `--stellar-profile-filter` | Activé | Analyse et filtre les profils |
| `--no-stellar-profile-filter` | — | Désactive la mesure et le rejet |
| `--stellar-profile-sigma` | `3` | Coefficient robuste, fini et strictement positif |

Une erreur de lecture, un manque d’étoiles ou de poses de référence est
**non concluant** : ce contrôle ne rejette pas la pose sur cette seule base.
Le bilan indique le nombre de résultats non concluants, qui restent détaillés
dans le rapport.

## Rapports et validation

`02_quality/stellar_profiles.json` conserve les réglages de fenêtre, les
références, les mesures par étoile, les seuils par pose, les fractions concordantes,
les statuts et les motifs. Le champ `stellar_profiles` du rapport général
`<sortie>.drizzle.json` pointe vers ce fichier et fournit les comptes.
Les logs INFO indiquent l’avancement, les fichiers rejetés et les retraits
additionnels de chaque critère.

Les tests [test_stellar_quality.py](../../../../../tests/test_stellar_quality.py) couvrent
des profils simples, flous, allongés, dédoublés et une binaire isolée, en
monochrome et CFA, ainsi que le fond incliné, le bruit, la saturation,
les erreurs de lecture et l’exclusion effective avant stacking.

L’essai en lecture seule sur M33 du 17 septembre 2026 a analysé les 172 poses
retenues par les mesures Siril en environ 57 secondes sur la machine de test.
Le seuil R80 était de 8,240 pixels et celui d’allongement de 1,5. Les deux poses
signalées par l’utilisateur avaient des R80 de 11,450 et 9,858 pixels : elles
sont toutes deux exclues. Quatre poses dépassent le critère R80 et six le
critère d’allongement, avec deux communes : huit retraits au total, 164 restantes.
Ce constat ne mesure pas un taux de faux positifs : seules les deux premières
poses disposaient d’une confirmation utilisateur du défaut.

## Limites

La référence suppose une majorité de poses acceptables à échantillonnage
comparable. Une série uniformément mauvaise peut passer ; le plafond
`--max-fwhm` reste disponible pour imposer une limite externe. Les champs denses,
nébulosités, ailes très faibles, saturation non identifiée et profils dépassant
l’ouverture peuvent rendre les mesures non concluantes ou les biaiser.
Le R80 peut manquer une composante secondaire peu lumineuse ; les moments
peuvent être affectés par des aberrations optiques cohérentes. La réduction CFA
limite la sensibilité aux déplacements inférieurs à quelques pixels natifs.
La validation synthétique et l’essai M33 ne constituent pas un banc d’essai
représentatif de tous les instruments et types de bougé.
