## Détection automatique du dithering et activation du Drizzle

L'outil doit analyser automatiquement les séquences d'images afin de déterminer :

1. si l'acquisition comporte du dithering ;
2. si les déplacements obtenus présentent une distribution sub-pixel suffisante ;
3. si l'échantillonnage de l'instrument rend le Drizzle potentiellement utile ;
4. si les conditions sont réunies pour activer automatiquement le Drizzle de Siril ;
5. dans le cas d'une caméra couleur CFA/Bayer, si le CFA Drizzle doit être privilégié.

### 1. Informations à récupérer

L'outil doit récupérer, lorsque ces informations sont disponibles dans les en-têtes FITS ou dans les données produites par Siril :

* focale de l'instrument ;
* taille des pixels du capteur ;
* dimensions du capteur ;
* motif CFA/Bayer (`RGGB`, `BGGR`, `GRBG`, `GBRG`, etc.) ;
* binning ;
* coordonnées et transformations calculées lors de l'alignement ;
* FWHM des étoiles de chaque image ;
* éventuellement toute information de dithering explicitement présente dans les FITS.

L'absence de focale ou de taille de pixel ne doit pas empêcher la détection du dithering si les transformations d'alignement sont disponibles.

### 2. Détection du dithering

La présence du dithering doit être déterminée principalement à partir des transformations géométriques obtenues lors de l'alignement des images.

Pour chaque image `i`, récupérer sa translation par rapport à l'image de référence :

```
dx[i], dy[i]
```

Calculer également le déplacement entre deux images successives :

```
ddx[i] = dx[i] - dx[i-1]
ddy[i] = dy[i] - dy[i-1]
```

et :

```
d[i] = sqrt(ddx[i]^2 + ddy[i]^2)
```

L'algorithme doit distinguer autant que possible :

* les petites variations dues au guidage ;
* une dérive lente de la monture ;
* les sauts volontaires correspondant au dithering ;
* les déplacements anormaux dus à une mauvaise image ou à un mauvais alignement.

La détection ne doit donc pas reposer uniquement sur un seuil fixe exprimé en pixels.

Une estimation robuste du bruit de déplacement doit être utilisée, par exemple à partir de la médiane et du MAD (Median Absolute Deviation).

Une série de déplacements significativement supérieurs au bruit normal, apparaissant régulièrement ou semi-régulièrement dans la séquence et dans plusieurs directions, doit être considérée comme une indication de dithering.

### 3. Caractérisation du dithering

Lorsque du dithering est détecté, l'outil doit calculer au minimum :

* nombre de dithers détectés ;
* fréquence moyenne du dithering en nombre de poses ;
* amplitude médiane ;
* amplitude minimale et maximale ;
* amplitude selon X et Y ;
* caractère mono-directionnel ou bidirectionnel ;
* éventuelle dérive globale de la séquence.

Le programme doit également analyser la distribution des positions sub-pixel :

```
fx[i] = dx[i] modulo 1
fy[i] = dy[i] modulo 1
```

Les couples `(fx, fy)` représentent les différentes phases d'échantillonnage du détecteur obtenues pendant l'acquisition.

Un indicateur de couverture sub-pixel doit être calculé afin d'estimer si les différentes images couvrent suffisamment uniformément l'espace `[0,1[ × [0,1[`.

Cet indicateur doit tenir compte du nombre d'images et éviter de considérer plusieurs positions pratiquement identiques comme des échantillons indépendants.

### 4. Cas particulier des capteurs CFA/Bayer

Lorsqu'un motif CFA est détecté, l'analyse doit tenir compte du fait que les échantillons R, G1, G2 et B occupent des positions physiques différentes sur le capteur.

Le programme doit déterminer si la distribution des déplacements fournit une couverture suffisante pour une reconstruction CFA Drizzle.

Les images ne doivent pas être dématricées avant l'étape CFA Drizzle lorsque le mode CFA Drizzle natif de Siril est utilisé.

La décision doit privilégier le CFA Drizzle par rapport à un Drizzle appliqué après dématriçage lorsque les données et la version de Siril utilisée permettent ce traitement.

### 5. Calcul de l'échantillonnage

Lorsque la focale et la taille des pixels sont connues, calculer l'échantillonnage natif :

```
sampling = 206.265 × pixel_size_um / focal_length_mm
```

Le résultat est exprimé en secondes d'arc par pixel.

Le binning doit être pris en compte.

Exemple :

```
pixel = 3.76 µm
focale = 550 mm
```

donne :

```
sampling ≈ 1.41 arcsec/pixel
```

### 6. Estimation de la résolution réellement enregistrée

La décision d'utiliser le Drizzle ne doit pas être fondée uniquement sur l'échantillonnage théorique.

L'outil doit utiliser de préférence la FWHM médiane mesurée sur les images retenues après contrôle qualité.

Calculer :

```
FWHM_pixels = médiane des FWHM des images retenues
```

Si les FWHM sont disponibles en secondes d'arc :

```
FWHM_pixels = FWHM_arcsec / sampling
```

Le Drizzle présente principalement un intérêt lorsque la PSF est insuffisamment échantillonnée.

À titre indicatif :

* FWHM < 2 pixels : séquence sous-échantillonnée, Drizzle potentiellement très intéressant ;
* FWHM entre 2 et 2.5 pixels : intérêt possible ;
* FWHM > 2.5–3 pixels : gain de résolution généralement faible.

Ces seuils doivent être configurables et ne doivent pas constituer à eux seuls une décision absolue.

### 7. Décision automatique

L'activation automatique du Drizzle doit être basée sur plusieurs critères indépendants :

* dithering effectivement détecté ;
* nombre suffisant d'images ;
* bonne diversité des positions sub-pixel ;
* absence d'une dérive excessive ;
* qualité suffisante de l'alignement ;
* FWHM indiquant un sous-échantillonnage ou un échantillonnage limite ;
* couverture CFA suffisante pour une caméra couleur ;
* ressources mémoire et disque compatibles avec l'augmentation de taille.

L'outil doit calculer un `drizzle_score` ou indicateur équivalent permettant d'expliquer la décision.

La décision ne doit pas être simplement :

```
dithering détecté => drizzle
```

mais plutôt :

```
dithering
+ couverture sub-pixel
+ sous-échantillonnage
+ nombre d'images
+ qualité d'alignement
=> pertinence du drizzle
```

### 8. Choix du facteur Drizzle

Le facteur ×2 doit être le facteur privilégié lorsqu'un Drizzle avec augmentation de résolution est justifié.

L'outil ne doit pas sélectionner automatiquement un facteur supérieur sans justification explicite.

Pour un Drizzle ×2, l'échantillonnage de sortie est :

```
sampling_out = sampling / 2
```

L'outil doit signaler que le nombre de pixels de l'image et les besoins approximatifs en mémoire et stockage sont multipliés par quatre.

### 9. Choix de `pixfrac`

Le choix de `pixfrac` doit dépendre de la densité et de l'uniformité de la couverture sub-pixel.

Une couverture importante et uniforme permet d'utiliser un `pixfrac` plus faible.

Une couverture faible ou irrégulière nécessite un `pixfrac` plus élevé afin d'éviter les zones insuffisamment couvertes et une augmentation excessive du bruit.

Le programme doit rester conservateur en mode automatique.

À défaut d'une estimation fiable, utiliser une valeur proche de 1 plutôt qu'une valeur agressivement faible.

### 10. Modes de fonctionnement

Prévoir au minimum trois modes :

```
drizzle = off
drizzle = auto
drizzle = force
```

`off` :
ne jamais utiliser Drizzle.

`force` :
utiliser Drizzle indépendamment de l'analyse automatique, tout en affichant les éventuels avertissements.

`auto` :
effectuer l'analyse complète et n'activer Drizzle que lorsque les critères configurés sont satisfaits.

Prévoir également, si nécessaire :

```
drizzle_scale
drizzle_pixfrac
drizzle_kernel
```

avec une valeur `auto` permettant au programme de choisir les paramètres.

### 11. Rapport de diagnostic

La décision doit être explicitement documentée dans le journal de traitement.

Exemple :

```
Analyse Drizzle
------------------------------
Images analysées       : 146
Images retenues        : 139
Dithering détecté      : oui
Dithers détectés       : 47
Fréquence estimée      : 1 / 3 poses
Amplitude médiane      : 5.1 px imageur
Couverture sub-pixel   : bonne
Capteur CFA            : RGGB
Échantillonnage        : 1.41 "/px
FWHM médiane           : 1.75 px
Sous-échantillonnage   : oui
CFA Drizzle possible   : oui
Drizzle score          : 0.87
Décision               : CFA Drizzle ×2
Pixfrac                : 0.8
Kernel                 : square
```

À l'inverse :

```
Dithering détecté      : oui
Couverture sub-pixel   : bonne
FWHM médiane           : 2.9 px
Sous-échantillonnage   : non
Drizzle score          : 0.31
Décision               : Drizzle désactivé
Raison                 : résolution principalement limitée par la PSF/seeing
```

### 12. Principe de prudence

En mode automatique, l'outil doit privilégier l'absence de Drizzle lorsqu'il n'existe pas suffisamment d'informations pour démontrer son intérêt.

Le Drizzle ne doit jamais être considéré comme améliorant systématiquement une image.

Lorsque la décision est incertaine, le programme doit conserver le traitement standard et indiquer dans le rapport :

```
Drizzle non activé automatiquement : données insuffisantes ou bénéfice incertain.
```

La détection du dithering et la décision d'utiliser Drizzle doivent rester deux décisions distinctes.

### 13. Conservation de l’analyse en JSON

**Conserver les résultats de cette analyse dans un fichier JSON à côté du résultat final.**
Le fichier `<cible>_combined.drizzle.json` doit permettre de comparer plusieurs nuits et
notamment de déterminer expérimentalement si les réglages Ekos (`Dither Pixels = 3`,
fréquence, etc.) donnent une bonne couverture sub-pixel avec le matériel utilisé.

Le rapport conserve une version de schéma, la date UTC, les paramètres du traitement,
les métadonnées instrumentales, les transformations et FWHM des poses retenues,
les dates et chemins des poses, les histogrammes de couverture, les statistiques
de dithering, les estimations de ressources, le score, les critères de décision et
le résultat de l’exécution (`pending`, `completed`, `failed`). Une analyse par dossier
source permet de distinguer les sous-sessions. Les valeurs inconnues sont `null`,
jamais des valeurs inventées. L’écriture est atomique ; le JSON subsiste après le
nettoyage des fichiers de travail. Le rapport est également produit lorsque le
Drizzle est désactivé ou refusé par l’analyse.

Les amplitudes sont exprimées en **pixels de l’imageur**. La valeur Ekos peut être
exprimée dans le repère du guidage : elle ne doit pas être comparée directement
sans conversion des échantillonnages respectifs. Les réglages Ekos ne sont pas
inférés des déplacements ; noter les réglages réellement utilisés avec chaque nuit.

## Utilisation et choix d’implémentation

```bash
bin/lightProcess.sh --drizzle auto <arguments habituels>
bin/lightProcess.sh --drizzle force --drizzle-scale 2 --drizzle-pixfrac 0.8 <arguments habituels>
bin/lightProcess.sh --drizzle off <arguments habituels>
```

Le mode par défaut est `auto`. Paramètres persistants et options CLI :

| Option | Défaut | Signification |
| --- | --- | --- |
| `--drizzle` | `auto` | `off`, `auto`, `force` |
| `--drizzle-scale` | `auto` | ×2 automatiquement ; valeur explicite entre 1 et 3 |
| `--drizzle-pixfrac` | `auto` | 1 ; 0.8 si couverture dense et uniforme |
| `--drizzle-kernel` | `auto` | `square` automatiquement ; `gaussian`, `turbo`, `point` explicites |
| `--drizzle-min-frames` | 30 | Minimum de poses indépendantes |
| `--drizzle-min-coverage` | 0.75 | Fraction des 16 cellules sub-pixel occupées |
| `--drizzle-fwhm-limit` | 2.5 | FWHM limite en pixels natifs |
| `--drizzle-max-drift` | 10 | Dérive lente maximale estimée en pixels |

Le vrai Drizzle nécessite Siril ≥ 1.4. Une vérification `requires 1.4` précède
l’activation ; une incompatibilité impose le traitement standard en `auto` et un
échec explicite en `force`. Les séquences Siril de versions 4 à 7 sont lues.
Un format inconnu empêche une activation automatique.

La nouvelle calibration en `auto/force` conserve le CFA. Les calibrations RGB déjà
présentes sont recalculées si les brutes Bayer sont disponibles pendant le traitement
des sessions. Avec `--force-stacking` seul, les RGB existants restent utilisables
pour du Drizzle RGB ; utiliser `--force` pour imposer une recalibration complète. La désactivation sur des entrées CFA
entraîne un dématriçage avant application des transformations. En Drizzle, on
applique une seule fois les transformations aux pixels natifs : la deuxième passe
avec rééchantillonnage du mode standard n’est pas appliquée aux images drizzlées.

La sélection de qualité du parcours Drizzle est partagée entre diagnostic et
stacking via les indicateurs d’inclusion de la séquence. Les filtres `k` utilisent
médiane ± k × 1.4826 MAD ; les pourcentages retiennent la proportion demandée.
Les copies de pondération FWHM restent dans le stack mais ne comptent qu’une fois
pour l’analyse. La couverture utilise une grille 4 × 4, son uniformité et un
regroupement des phases à moins de 0.05 pixel sur le tore (0 et 1 sont voisins).
Pour Bayer, la couverture est aussi évaluée modulo 2 sur les sites R/G1/G2/B,
avec un minimum conservateur de 60 poses pour ×2. Les rotations ou déformations
sensibles rendent l’estimation par translation insuffisante et bloquent `auto`.

Le score est la proportion des critères satisfaits, **pas une probabilité de gain**.
Tous les critères doivent être satisfaits en automatique. Le détecteur recherche
au moins trois sauts dans plusieurs directions, après soustraction de la dérive
médiane et estimation robuste du bruit ; les excursions immédiatement suivies
d’un retour sont ignorées. Une séquence entièrement composée de grands déplacements
ou une acquisition mal horodatée peut rester indéterminée. L’analyse des déplacements
ne prouve pas l’intention de dithering ; les seuils sont des heuristiques à confronter
aux acquisitions réelles.

L’échantillonnage utilise `FOCALLEN`, `XPIXSZ` et `XBINNING` (taille du pixel supposée
non binnée). Vérifier cette convention si le pilote écrit une taille déjà binnée.
La FWHM du diagnostic provient de Siril et est exprimée en pixels. Les besoins en
ressources sont des estimations prudentes, pas une garantie de consommation maximale.

Références : [Drizzle Siril](https://siril.readthedocs.io/en/stable/preprocessing/drizzle.html),
[commandes Siril](https://siril.readthedocs.io/en/stable/Commands.html).

### Validation

Tests automatisés : dérive et guidage sans dithering, dithering périodique,
FWHM défavorable, répétition des phases, excursion isolée, frontières de sessions,
ressources, couverture CFA, cache, sélection de qualité et scripts des trois modes.
Un essai réel avec `siril-cli` 1.4.4 sur douze images Bayer synthétiques de 256 × 256
pixels a produit un FITS RGB de 504 × 502 en Drizzle ×2 et un FITS RGB de 253 × 251
avec repli standard (dimensions après recadrage commun). Cette validation porte
sur le fonctionnement ; les gains scientifiques restent à évaluer sur les nuits réelles.

### Ordre des contrôles de qualité et pondération

Le parcours commun aux modes `off`, `auto` et `force` respecte cet ordre :

1. Préfiltrage FWHM et pondération FWHM existants.
2. Calcul des transformations et mesures des étoiles sur les poses natives.
3. Filtrage FWHM, rondeur et nombre d’étoiles sur les poses indépendantes.
4. Détermination de la pondération de rondeur sur les poses retenues.
5. Analyse du dithering et choix du Drizzle sur les poses indépendantes retenues.
6. Application des transformations, avec Drizzle si activé, puis empilement pondéré.

Le filtre `--nbstars-filter` utilise par défaut `1.8k`. Une valeur numérique fixe
un minimum d’étoiles ; `80%` retient les poses dans les 80 % supérieurs (les ex æquo
au seuil sont conservés) ; `1.8k` utilise médiane − 1.8 × 1.4826 MAD. `none` le
désactive. Les seuils de sélection sont calculés sans compter plusieurs fois
les poses répétées par la pondération FWHM.

La pondération de rondeur est active par défaut. Elle suit la convention de
pondération discrète déjà utilisée pour la FWHM : les meilleures poses ont
jusqu’à `--roundness-weight-max-extra 1` répétition supplémentaire. Pour une
rondeur `r`, le nombre de répétitions est
`1 + arrondi((r - r_min) / (r_max - r_min) × max_extra)` parmi les poses retenues.
À rondeur identique, les poids sont égaux. `--no-roundness-weighted` désactive
cette pondération. Les multiplicités FWHM et rondeur se multiplient : ce sont
des poids approximatifs, pas des poses ou du temps d’intégration supplémentaires.
Les répétitions peuvent aussi influencer le rejet statistique et la médiane.

Les matrices de transformation des poses pondérées sont conservées ; leur
rondeur n’est pas remesurée sur les images drizzlées. Les répétitions n’augmentent
ni le nombre de poses indépendantes ni la couverture sub-pixel du diagnostic.
Le JSON contient `roundness`, `nbstars`, `roundness_multiplicity` et
`effective_stack_entries` pour chaque pose retenue, ainsi que le bilan
`quality_selection`. La version du parcours de qualité invalide les anciens
résultats en cache. Si la sélection qualité échoue ou ne retient aucune pose,
l’empilement s’arrête explicitement, y compris en mode `force`.

### Raisons précises dans le journal

Chaque critère refusé est affiché sur sa propre ligne avec les valeurs mesurées
et les seuils effectivement appliqués. Une donnée absente est distinguée d’un
seuil dépassé. La mémoire RAM, le disque de travail et le disque de sortie sont
vérifiés et affichés séparément, avec les besoins estimés et les disponibilités
en Gio et en octets. Les critères satisfaits ne figurent pas parmi les raisons.
Pour les transformations, le diagnostic indique le nombre de poses hors tolérance
et la pose présentant le dépassement relatif maximal, avec le coefficient de
matrice concerné, sa valeur attendue et sa tolérance. Il n’en déduit pas une cause
physique que cette seule comparaison ne permet pas de déterminer.

Le JSON conserve les codes `reasons` pour la compatibilité et ajoute
`reason_details` (critère et message précis), également dans les analyses par
sous-session. Le pourcentage de poses retenues regroupe les rejets qualité et
alignement : le journal ne les attribue pas tous à un mauvais alignement.

### Répertoires fixes et numéros d’étapes

Le stacking utilise des chemins déterministes dans `work/<cible>/stacking/` :

| Étape | Répertoire | Script / résultat |
| --- | --- | --- |
| 00 | `00_inputs/` | Liens vers les entrées calibrées |
| 01 | `01_registration/` | `01_registration.sps`, FITS natifs et transformations |
| 02 | `02_quality/` | Copie de la séquence, filtres, pondération et analyse Python |
| 03 | `03_capability/` | `03_capability.sps` si Drizzle sélectionné, sinon `SKIPPED.txt` |
| 04 | `04_stacking/` | `04_stacking.sps`, rééchantillonnage et empilement |

Chaque script est stocké et exécuté dans le répertoire de son étape. Aucun nom
aléatoire ni répertoire `run_*` n’est créé. Chaque reconstruction nettoie ces
étapes puis les recrée aux mêmes chemins ; elles représentent le dernier traitement.
Les anciens dossiers `run_*` ne sont pas réutilisés ; `--force-stacking` conserve
son rôle de nettoyage complet de l’arborescence de stacking.

Les métadonnées modifiables sont copiées de 01 vers 02 puis de 02 vers 04 ; les
FITS sont référencés par liens absolus. Ainsi, les séquences de l’alignement initial
et de la sélection restent consultables après l’empilement. Les scripts et logs
ne sont pas recopiés. Le contrôle 03 ne produisant aucune donnée image, son saut
est signalé par un fichier explicatif, sans lien de répertoire supplémentaire.
Le JSON indique les chemins dans `stages` et l’exécution effective du contrôle dans
`stages.capability_executed`. Le résultat final reste dans son dossier habituel.

Le [graphique du dataflow](LIGHT_PROCESSOR_GUIDE.md#schéma-du-processus) indique
les répertoires, scripts, transferts de données et branches optionnelles.
