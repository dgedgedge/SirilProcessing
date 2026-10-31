# Moteur GHS, histogrammes et optimisation

[postProcess](README.md) › [AutoGHS](auto-ghs.md) › Algorithme

## Responsabilités et interfaces

[`lib/auto_ghs.py`](../../../lib/auto_ghs.py) sépare le moteur mathématique de
StarNet et de la publication. `ghs_transfer` traite une intensité scalaire,
`ghs_rgb` réalise le mode lié human/even avec RGBBlend, et
`linear_blackpoint` réalise le point noir indépendant de Siril.
`HistogramAnalyzer` produit des effectifs et métriques compacts.
`GHSOptimizer(processor)` recherche une variante sans étoiles.
`ComputeBackend` sélectionne NumPy/CuPy avant le traitement.

`optimize(data, strategy, args, backend)` reçoit un tableau RGB CPU float32,
normalisé, sans étoiles ; retourne le tableau étiré et son rapport. `strategy`
vaut toujours `range`, seule recherche disponible. Le moteur
ne relance ni StarNet ni le rendu stellaire. Les paramètres sont déclarés par
`add_arguments`, puis résolus par `Config` ; le point d'entrée reste postProcess.

## Transformation conforme à Siril

Le paramètre CLI `D` est dans `[0,10]` ; le noyau utilise `d = exp(D)-1`.
Avec `t = abs(x-SP)`, la primitive positive `A(t)` vaut :

| B | A(t) |
|---|---|
| `B > 0` | `1 - (1+d*B*t)^(-1/B)` |
| `B = 0` | `1 - exp(-d*t)` |
| `B = -1` | `log(1+d*t)` |
| `B < 0`, `beta=-B`, `beta != 1` | `((1+d*beta*t)^((beta-1)/beta)-1)/(beta-1)` |

La primitive signée vaut `-A(SP-x)` à gauche de SP et `A(x-SP)` à droite.
À l'extérieur de `[LP,HP]`, ses tangentes aux limites assurent le raccord.
La transformation finale est `(F(x)-F(0))/(F(1)-F(0))`, avec `0→0`, `1→1` et
`D=0` identité. `log1p/expm1` réduisent les annulations près de zéro. Les
contraintes sont `-5≤B≤15` et `0≤LP≤SP≤HP≤1` ; toutes les branches ont des
tests de continuité et monotonie.

Les formules ont été confrontées au [source officiel Siril GHS](https://gitlab.com/free-astro/siril/-/blob/1.4.0/src/filters/ght.c)
et aux exécutions de Siril 1.4.4. L'interface CLI locale refuse les valeurs de
B négatives, malgré les branches présentes dans le moteur : les références
exécutées et l'optimiseur utilisent donc **B positif ou nul**. Les branches
négatives Python ont des tests mathématiques, sans comparaison CLI locale.

Pour RGB, `human` utilise `Y=.2126R+.7152G+.0722B`, comme Siril ; `even` la
moyenne des canaux. Ces coefficients constituent un **proxy de luminosité**
lorsque les primaires FITS sont inconnues, pas une calibration Rec.709.
Le gain lié `GHS(Y)/Y` s'applique aux trois canaux, avec gain nul pour Y=0.
RGBBlend combine ce résultat avec les trois étirements indépendants : le
poids commun maximal garde chaque canal sous 1. Il ne s'agit pas de trois
optimisations séparées ni d'un écrêtage RGB final.

Le point noir reproduit `max(0,(x-BP)/(1-BP))` **par canal**, conformément au
`linstretch -BP=... -clipmode=rgbblend` effectivement observé. Il n'est pas
appliqué comme un changement de luminance liée.

Les vecteurs [`auto_ghs_siril_1_4_4.json`](../../../tests/data/auto_ghs_siril_1_4_4.json)
proviennent de cinq exécutions GHS avec SP, LP, HP, D et B variés, plus le point
noir. La première comparaison mesurait un écart GHS maximal de `9.55e-7`,
et un écart nul pour BP. La tolérance de régression est `2e-6` absolu ; elle
ne vaut pas qualification de toutes les combinaisons de paramètres possibles.
Le test d'intégration recalcule les oracles avec le Siril réellement installé ;
la version float32 finale a mesuré un écart maximal de `1.02e-6` environ.

## Analyse des histogrammes

L'axe horizontal est toujours l'intensité. L'histogramme compte les pixels dans
`ghs-bins` classes uniformes de `[0,1]`. La courbe d'analyse est
`log1p(effectifs)`, lissée avec `[1,2,3,2,1]/9`. Les effectifs bruts restent
utilisés pour la CDF, les prédictions et les diagnostics d'écrêtage.

Le pic principal est le maximum de cette courbe. Sa largeur est celle de la
composante contiguë au-dessus de la **demi-hauteur logarithmique** autour de ce
maximum. Un pic touchant une limite, vide ou supporté par moins de trois classes
est ambigu ; cette indication reste un diagnostic de la forme du pic.
Les bosses significatives sont les pics d'une proéminence d'au moins 12 % du
maximum log, espacés d'au moins `bins//128` classes, avec un minimum de deux.
La rugosité de la pente droite est conservée comme diagnostic.

`range` utilise `percentile(Y,q_high)-percentile(Y,q_low)` ; les vrais quantiles
sont calculés sur les finalistes. Les histogrammes R/G/B, fractions aux bornes
par canal et Y, médiane et IQR sont également conservés.

Le proxy de bruit utilise les différences horizontales dont les deux pixels
appartiennent au quartile inférieur de Y. Il vaut
`1.4826 * MAD(différences) / sqrt(2)` à partir de 32 paires ; sinon la mesure est
absente. La corrélation des pixels et la sélection dans la queue de distribution
biaisent ce proxy. Il est complété par un garde d'amplification de l'IQR ;
aucune de ces grandeurs ne mesure à elle seule la qualité des détails diffus.

## Recherche et convergence

Chaque passage place `SP` 1,5 classe à gauche du pic, avec LP=0, HP=1. La
concentration B suit 15, 5, 2 puis 0 aux passages suivants. La recherche explore
D de 0,25 à 6 par pas de 0,25 : ce maillage constitue une heuristique bornée,
pas une optimisation continue de tous les paramètres du GHS.

La prédiction applique chaque courbe aux centres des classes et redistribue
leurs effectifs. Le plateau est le premier gain marginal inférieur à
`ghs-plateau-fraction` fois le meilleur gain marginal antérieur. Les voisins
du plateau, un candidat faible et les meilleurs candidats prédits respectant
les gardes de luminosité et IQR sont ensuite confrontés aux **vrais pixels
RGB**. Les classements sont recalculés après ces transformations ; le rapport
mesure l'écart entre largeur prédite et largeur observée. La prédiction
scalaire n'est pas assimilée au résultat human/RGBBlend.

Sont rejetés les candidats qui dépassent les budgets d'écrêtage, une médiane
Y de 0,45, l'amplification maximale des proxys bruit/IQR, ou qui créent plus
d'une bosse significative supplémentaire (avec un seuil minimal de deux).
Une compression de plus de 20 % de la largeur robuste près de la borne haute
est également rejetée.
Le meilleur finaliste doit améliorer la largeur retenue de plus de
`ghs-min-gain` relativement au meilleur état examiné.

Avant chaque recherche GHS, BP est remonté jusqu’à la **plus petite valeur RGB
strictement positive**, tous canaux confondus. Les zéros existants sont ignorés.
La transformation commune est `max(0,(x-BP)/(1-BP))` : les valeurs égales à BP
rejoignent zéro, les valeurs supérieures restent positives. Il n’y a plus de
marge de classes ni de condition de gain supplémentaire pour appliquer BP.
Un support positif constant n’est pas annulé. La correction est recalculée
sur les pixels courants à chaque passage, indépendamment du pic détecté.

Les arrêts normaux sont : distribution constante, absence de
finaliste admissible ou gain marginal insuffisant, et limite d'itérations.
Une exception de calcul ne devient jamais un arrêt de convergence.

## Égalisation partielle de l’histogramme

Après les recherches GHS et avant l’expansion finale de dynamique, une courbe
CDF globale redistribue les intensités pour réduire leur concentration. L’option
`--ghs-histogram-strength` vaut 0,7 par défaut : 0 désactive l’opération, 1 applique
toute la courbe construite. Ce réglage intervient après la recherche RANGE.

Les effectifs bruts de luminance sont plafonnés à
`ghs-histogram-clip * effectif_moyen` (facteur 4 par défaut, de 1 à 16).
L’excès est réparti uniformément entre les classes. La CDF normalisée qui en
résulte est interpolée entre 0 et 1, puis mélangée à l’identité :

```text
T(Y) = (1-strength)*Y + strength*CDF_plafonnée(Y)
```

Le plafonnement borne la pente de la courbe, afin d’éviter l’amplification
arbitraire d’un pic de fond très étroit. Il ne s’agit ni d’une égalisation locale
par tuiles ni d’une garantie d’histogramme parfaitement plat. Le gain `T(Y)/Y`
est commun aux RGB et limité pour qu’aucun canal ne dépasse 1 ; les rapports
RGB restent conservés. Les zéros restent nuls et une luminance constante est
préservée. Cette étape peut aussi amplifier le bruit ; le rapport
`histogram_balance` conserve les métriques avant/après, le plafond, la force et
la fraction de pixels dont le gain a été limité par le gamut. L’histogramme
intermédiaire est enregistré et les encarts rappellent les deux réglages.
La boucle de dynamique, la chroma optionnelle et le GHS final de présentation
interviennent ensuite, avant la recomposition stellaire.

## Boucle complémentaire d’expansion de la dynamique

Après la recherche RANGE, une seconde boucle vérifie l’étendue réelle
`max(RGB)-min(RGB)` sur tous les pixels et canaux, en unités `[0,1]`. La cible
`--ghs-dynamic-target` vaut `1.0` par défaut ; `0.8` est également possible.
Ce critère est distinct de la largeur robuste de RANGE.
Il mesure les bornes occupées, sans imposer une répartition uniforme ; un pixel
extrême peut donc suffire à étendre cette mesure.

Tant que l’étendue est inférieure à la cible, chaque passage :

1. Recalcule et applique BP sur le premier RGB strictement positif.
2. Mesure l’étendue et le maximum après cette correction.
3. Multiplie tous les canaux par le même gain, au plus 2, limité par le rapport
   cible/étendue et par `1/maximum` pour rester dans le gamut.
4. Recalcule l’histogramme et enregistre BP, gain et étendues avant/après.

La boucle s’arrête à la cible (tolérance absolue de huit epsilon float32), sur
absence de progression ou distribution constante, ou à la limite configurée
par `--ghs-dynamic-max-iterations` (32 par défaut, de 1 à 64). Une image constante
reste intacte ; le rapport indique explicitement si la cible n’a pas été atteinte.
Il s’agit d’une expansion **linéaire commune** après les GHS, sans nouvelle
recherche D/B/SP et sans écrêtage supérieur volontaire. Les gardes d’amplification
de bruit de la recherche GHS ne bloquent pas cette phase : elle amplifie aussi
le bruit présent. Les métriques finales et celles de `post_ghs` permettent de
comparer ces effets. La chroma éventuelle, le GHS final et la fusion viennent ensuite ; la
marge de fusion `ghs-headroom` peut réduire l’étendue du fond dans le rendu étoilé.

Le rapport conserve `iterations` pour les recherches GHS, `blackpoint_iterations`
pour leurs corrections BP et `dynamic_range` pour la seconde boucle. Les
histogrammes intermédiaires incluent ces transformations et les encarts des
figures distinguent les deux boucles.

## Chroma optionnelle

`off` conserve le rendu de référence Siril. `balanced/enhanced` renforcent la
distance au gris moyen RGB d'au plus 10 % / 25 %, avec rampe entre 2 % et 10 %
d'intensité et atténuation des hautes lumières. Un gain commun borné par le
gamut `[0,1]` préserve la direction des écarts RGB au gris et la moyenne RGB.
C'est une saturation **dans les coordonnées RGB du FITS**, expérimentale,
non une conversion OKLab, sRGB ou une restitution perceptuelle calibrée.
Les tests vérifient gris, direction, gamut et diagnostic de chromaticité ;
le défaut reste `off` en attente de qualification visuelle plus étendue.

<a id="ghs-final-de-presentation"></a>

## GHS final de présentation

Après la recherche principale, l'égalisation, l'expansion et la chroma,
`GHSOptimizer.final_stretch` délègue au moteur `ProtectedGHSFinalizer`.
La [recherche finale D/B/SP/HP protégée](ghs-highlight-protection.md) utilise les
pixels RGB, des régions fixes et une validation complète du fond et de sa fusion.
Le classement combine contraste diffus, visibilité par rapport au fond et aux
hautes lumières, bruit et régression robuste de la queue logarithmique.
Le RMS relatif est pondéré par `--ghs-log-rms-weight 2`,
la pente par 0,15, et la fenêtre peut s'étendre à 0,875 si le support le permet.
Le meilleur score admissible est choisi après réévaluation des trois finalistes
sur tous les pixels. SP est exploré jusqu'à P90 ; LP reste à zéro.
La page spécialisée définit les régions,
le score borné d'histogramme et ses cas non mesurables.
Elle remplace l'application systématique de la courbe finale fixe.
`--ghs-final-strength` borne D dans `[0,10]` (défaut 1,25 ; zéro désactive).
Une largeur robuste inférieure ou égale à `1e-7` conserve la distribution.

Le mode comparatif `--ghs-final-mode fixed` utilise B=SP=LP=0 et HP=1.
Pour le transfert scalaire, avec `a = exp(D) − 1`, sa courbe vaut
`T(x) = (1 − exp(−a × x))/(1 − exp(−a))` pour D>0, et l'identité pour D=0.
RGBBlend adapte le résultat lié ; cette formule n'est pas trois transferts RGB
indépendants. Ce mode fixe n'est pas soumis aux gardes de présentation et peut
amplifier davantage le bruit et compresser les hautes lumières.

`presentation_stretch` consigne les paramètres et mesures, ainsi que le statut
`applied`, `disabled`, `constant distribution` ou `no admissible improvement`.
`chroma_diagnostics` reste mesuré avant cette dernière passe. Celle-ci n'ajoute
pas d'acceptation à `iterations` : les figures indiquent séparément D/B/SP/HP.
Les histogrammes et `final` portent sur le fond retenu après cette étape.

## CPU, CUDA et ressources

Le choix du backend et la vérification VRAM ont lieu avant StarNet. Le budget
est estimé à 16 fois la taille du RGB float32 pour les temporaires ; 20 % de la
VRAM libre reste disponible. CuPy est facultatif et n'est pas ajouté aux
dépendances CPU. Son installation doit correspondre au CUDA de la machine,
selon la [documentation CuPy](https://docs.cupy.dev/en/stable/install.html).

Le moteur conserve ses états image sur GPU, transfère les histogrammes et
résumés compacts pour la recherche, puis récupère le résultat accepté. Les
calculs de séparation, de rendu stellaire, les E/S et les figures restent CPU
ou dépendent de StarNet. `--compute-backend` ne force pas le moteur StarNet.
`auto` choisit CPU seulement si le précontrôle CUDA échoue ; une panne après
initialisation se propage sans reprise. CUDA forcé indisponible est une erreur.

La mémoire CPU peut dépasser plusieurs copies du FITS et de ses extensions.
Les mesures des finalistes impliquent des parcours complets de l'image ; le
nombre de candidats pleinement évalués reste limité. La parité matérielle
CuPy n'est pas validée dans un environnement sans CuPy/CUDA fonctionnel.

Références : [étirements Siril](https://siril.readthedocs.io/en/stable/processing/stretching.html),
[tutoriel GHS](https://siril.org/fr/tutorials/ghs/),
[guide de rendu final](auto-ghs.md), [étoiles et fusion](star-rendering.md).

Les figures de diagnostic affichent les mêmes effectifs bruts sur deux axes Y :
linéaire et logarithmique (graduations en puissances de 10). Les classes vides
sont masquées dans la vue log, sans ajouter de pseudo-effectif ; X reste linéaire.
Cette présentation est distincte du `log1p` interne utilisé pour analyser les pics.

Les deux panneaux RANGE sont numérotés : 1 = échelle linéaire à gauche,
2 = échelle logarithmique à droite. Aucun panneau PEAK n’est calculé.
Chaque titre indique son objectif et son échelle. Les populations sont séparées
entre deux PNG : la figure sans étoiles montre l’entrée linéaire, une étape
intermédiaire et le rendu final (au plus trois courbes par panneau) ; la figure
avec étoiles montre uniquement le rendu final recomposé (une courbe par panneau).
Chaque figure possède sa légende commune. Ces panneaux ne représentent pas les
étoiles seules.

Un encart rappelle l’objectif RANGE et résume la boucle GHS : recherches
ayant évalué des candidats / limite configurée, GHS acceptés, recherches sans
application, nombre de candidats examinés et corrections du point noir appliquées.
Il indique la raison d’arrêt et la largeur initiale/finale du critère, avec son
gain relatif si la largeur initiale est positive. Ces mesures concernent le fond
avant fusion, y compris sur la figure avec étoiles. Un arrêt avant toute recherche
(distribution constante) compte zéro recherche ; la chroma ne compte
pas comme une itération GHS. Les données absentes ne sont pas remplacées par zéro.
