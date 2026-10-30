# Qualité astrophotographique et comparaison des chaînes

[postProcess](README.md) · [Classes de restauration](restoration.md)

Les contrôles distinguent acceptation, classement et absence de mesure. Le mode
`--quality-mode reliable`, par défaut, remplace le score de bruit sur les 40 %
de pixels sombres par une mesure sur des masques fixes. `--quality-mode legacy`
retrouve les métriques historiques, les règles d’appariement Clarity et le
contrat historique des erreurs. L’ordre historique gradient → photométrie →
netteté → débruitage reste inchangé hors compétition des chaînes.

## Responsabilités

- `lib/restoration_quality.py` : `RestorationQuality` déclare les paramètres via
  `ParameterizedComponent` ; `QualityReference` construit les cartes et mesure
  bruit, signal diffus et flux d’ouverture. Aucun appel à `GradientExtractor`.
- `lib/deconvolution_quality.py` : conserve les anneaux et les critères
  FWHM/rondeur, expose les diagnostics historiques et les nouveaux états.
- `lib/restoration.py` : moteurs séquentiels sur la même entrée et les mêmes
  masques ; écarte un moteur absent ou en erreur et audite la raison.
- `lib/graxpert.py` : `GraXpertDenoiseProcessor`, uniquement débruitage.
- `lib/restoration_chains.py` : `RestorationChainProcessor`, compétition finale
  des chaînes, active par défaut, avec conservation de l’identité et cache par appel.

Le gradient interne reste RBF par défaut, avec ses options historiques ; le
polynomial conserve son critère R² / nombre de coefficients. Il n’y a pas de
nouvelle extraction de gradient ni de compétition avec GraXpert Background
Extraction. Cette évolution reste une phase distincte.

## Masques indépendants des moteurs

La référence est l’entrée commune de l’étape, après les étapes amont déjà
choisies. En compétition des chaînes, les contrôles finaux utilisent une seule
référence post-gradient/photométrie ; chaque opération intermédiaire est aussi
contrôlée contre son entrée effective. Un score local obtenu sur une entrée
différente n’est pas utilisé pour départager les chaînes finales.

Les cartes locales sont des diagnostics, jamais une correction appliquée au
FITS. Les tuiles fournissent une médiane après trois itérations de sigma-clipping.
Pour la segmentation, la dispersion des différences entre pixels adjacents
réduit le biais des pentes lentes ; sa normalisation suppose un bruit peu corrélé
et ne sert pas directement au score de bruit.

Les maxima stellaires de tous les canaux sont exclus avec une marge **avant**
le lissage des résidus au fond local. À chaque échelle, une convolution
normalisée utilise uniquement les pixels hors étoiles et hors pixels invalides :
`G_s(résidu × masque) / G_s(masque)`. Cela empêche la lumière des étoiles de
devenir un faux signal diffus dans les zones de ciel voisines. Les pixels dont
le support du filtre est inférieur à 50 % restent incertains.

Le seuil de détection est `source_sigma × max(σ_local / D(s), σ_observé(s)) / √support`,
avec `D(s) = max(1, min(2√π × s, 8))`. `σ_observé(s)` est la dispersion MAD du
résidu lissé hors étoiles, mesurée sur un échantillon spatial régulier limité
à environ 262 144 pixels. Ce plancher empirique tient compte des corrélations
et des résidus locaux que la formule de bruit blanc peut sous-estimer.
Il peut inclure du signal et reste une heuristique conservative pour la
segmentation ; il ne sert pas à calculer le score de débruitage.

Les composantes positives d’au moins `max(12, s²)` pixels sont dilatées.
Une détection à grande échelle indépendante du fond local exclut également
les zones ambiguës. Les exclusions spatiales sont l’union de tous les canaux,
pour protéger notamment les objets rouges. La couverture totale des exclusions
ne déclenche plus de veto global : les zones périphériques de ciel restent
éligibles même si l’objet occupe plus de 65 % du champ. Leur exploitation exige
toujours la couverture minimale des tuiles et leur répartition spatiale.
Une seule réestimation du fond/bruit hors exclusions est effectuée, sans réduire
les masques. Ces choix sont conservateurs et à calibrer.

Le rapport `metrics_version=3` conserve la dispersion de segmentation observée
à chaque échelle et par canal. Le journal INFO indique le pourcentage de ciel,
le nombre de tuiles retenues et de quadrants couverts avant l’exécution des moteurs.

Les masques `sky`, `star`, `object`, `uncertain`, `invalid` sont figés avant les
moteurs. Le fichier `<rapport>_masks.fits` encode les bits 1, 2, 4, 8 et 16 ; en
compétition, `common_masks.fits` appartient au dossier des chaînes. Les rapports
référencent aussi `quality_preview_path` : l’aperçu PNG est produit à côté du
masque FITS (`common_masks.png` ou `<rapport>_masks.png`). Il affiche les zones
stellaires en rouge, le signal diffus en orange, les zones incertaines en bleu
et les tuiles de ciel retenues encadrées en vert. Les voiles et contours suivent
les masques exacts, sans rectangles englobants autour des exclusions. Une grille
grise montre les tuiles possibles de `quality_tile_size` pixels de côté
(64 par défaut), dans les coordonnées natives avant réduction de l’aperçu.
La grille n’indique pas une sélection. Dans une tuile verte,
seuls les pixels classés ciel servent à la mesure. Même sans ciel admissible,
le PNG est produit avec zéro tuile verte. L’étirement est uniquement visuel,
et l’orientation suit `ROWORDER` ; les FITS sources et les critères restent identiques.
Chaque exécution achevée de `postProcess` produit cet intermédiaire. Si aucun
aperçu de restauration n’existe (mode historique ou restauration désactivée),
le script écrit `quality_masks.png`, `quality_masks.fits` et `quality_masks.json`
dans le dossier des étapes, sur l’image après gradient/photométrie. Ce diagnostic
visuel ne remplace pas les métriques historiques. Le chemin est indiqué dans
la section RÉSULTATS du journal.
Si les échantillons RBF sont enregistrés, cet aperçu montre aussi leurs positions
en cyan et une grande croix blanche sur chaque tuile sans échantillon du premier
canal. Les contours bleus restent visibles. Ces annotations comparent les deux
sélections sans changer les masques ni les critères. Les retournements ou
permutations des axes sont déduits des orientations WCS et appliqués aux points
avant l’orientation d’affichage. Le raffinement astrométrique de CRPIX/CRVAL ne
déplace pas les points. Sans orientation déterminable, le journal signale
l’absence de superposition ; aucune croix trompeuse n’est dessinée.
Un second aperçu, `quality_gradient_points.png`, montre sur le FITS original
les points enregistrés par le gradient (échantillons du premier canal pour RBF ;
points d’illustration aléatoires pour le polynomial), avec croix
rouges et cadres verts de repérage. Ces cadres ne sont pas des tuiles de bruit.
Il est produit même si l’aperçu habituel du gradient est désactivé, dès lors
que les points sont enregistrés. Aucun point n’est inventé si le gradient est
désactivé. Son chemin figure dans `gradient.quality_gradient_points_path`
du rapport global et dans le journal. La photométrie pouvant réorienter l’image,
les deux PNG ne doivent pas être superposés directement sans vérifier la géométrie.
Les rapports
conservent couverture, nombre de tuiles/quadrants, paramètres et empreintes SHA256
séparées de la référence et des masques. Un masque n’est jamais réutilisé sur
une image réorientée ou de dimensions différentes.

## Bruit et incertitude

Une tuile doit fournir au moins 128 pixels de ciel et 50 % de couverture.
Au moins quatre tuiles réparties sur trois quadrants sont nécessaires par défaut.
Dans chaque tuile, une tendance plane est retirée pour le diagnostic ; le bruit
est la MAD multipliée par 1,4826 du résidu. Le ratio après/avant est résumé par
sa médiane. Un bootstrap déterministe de 256 répétitions, par tuiles et non par
pixels, fournit un intervalle percentile à 95 %. La corrélation à un pixel est
également enregistrée. Cette incertitude n’inclut pas tous les effets de drizzle,
les corrélations entre tuiles, les erreurs systématiques ni le bruit des photons.

Le pire canal fournit le ratio global. Si le ciel manque ou si la dispersion
initiale n’est pas mesurable, le résultat vaut `UNKNOWN`, le ratio vaut `null`
et le score ne reçoit aucun bonus de bruit. Le débruitage automatique conserve
alors l’entrée. Pour une déconvolution, un gain stellaire peut rester mesurable
avec un bruit `UNKNOWN` : le rapport ne prétend pas que ce bruit est validé.

Le classement du débruitage utilise la borne conservative du gain de bruit,
avec pénalités de flou et d’anneaux. La MAD du résidu au filtre médian 3×3 et le
masque des 40 % sombres restent des diagnostics historiques explicites
(`high_frequency_residual`, `legacy_noise_ratio`), sans être le fondement du
score fiable. Le gain utile initial de 5 % est un point de calibration : son
contrôle strict est activé avec `--quality-enforce-significance`. Sans cette
option, les résultats sous ce seuil sont observés ; un gain numérique seul dont
la borne conservative n’est pas positive ne bat pas l’identité en débruitage.

## Préservation du signal

Les régions diffuses détectées ont des ouvertures fixes, avec marges permettant
la redistribution de lumière. Le fond de chaque ouverture est estimé sur une
couronne commune admissible, séparément avant/après. Les flux intégrés sont en
somme de pixels normalisés ; un faible SNR ou l’absence de couronne donne
`UNKNOWN`, sans division instable. Les contributions de blocs spatiaux et les
résidus lissés à plusieurs échelles corroborent une variation de flux. La simple
baisse des hautes fréquences ne constitue pas un rejet.

La photométrie stellaire utilise des ouvertures de rayon au moins trois FWHM
avec fond local, aux positions initiales ; les médianes de changements de flux
par blocs spatiaux sont rééchantillonnées. Les pertes ou créations de flux
significatives peuvent être signalées séparément des changements de FWHM.
Le diagnostic conserve aussi les nouveaux pixels à saturation normalisée ;
leur seule présence ne prouve pas un écrêtage artificiel.

Ces nouveaux contrôles photométriques sont **en observation par défaut**.
`--quality-enforce-diffuse` rend bloquante une perte/création de flux diffus
corroborée ou un changement stellaire significatif au-delà du seuil configuré.
Ils demandent une calibration sur plusieurs références. Un `PASS` signifie
« aucune perte confirmée par ce contrôle », jamais « fidélité prouvée ».
Aucun contrôle ne peut garantir l’existence d’un ciel vide ou retrouver une
nébulosité indistinguable du fond sur une seule image.

Les étoiles ont par défaut les mêmes seuils entre Siril et Clarity : 10 mesures
comparables, 70 % de survie et rayon de 1 pixel, avec mesure aux positions initiales
et SNR initial ≥ 10. Les groupes spatiaux et de flux d’au moins dix étoiles doivent
conserver au moins la moitié du maximum entre taux global et fraction demandée.
Les règles Clarity auto historiques restent disponibles en mode `legacy`.
Le gain FWHM de 2 %, la rondeur à 0,01, le bruit amplifié de 15 % et les anneaux
sur au plus 10 % des étoiles restent des critères heuristiques. L’incertitude
FWHM par blocs d’étoiles de 128 pixels est diagnostique, sans nouveau rejet strict.

## Paramètres

Les options suivent CLI > JSON sauvegardé > défaut, avec sauvegarde explicite `-S`.

| Option | Défaut | Sens |
|---|---|---|
| `--quality-mode` | `reliable` | `legacy` conserve les métriques historiques |
| `--quality-tile-size` | 64 | Taille des tuiles, pixels ; minimum 16 |
| `--quality-min-tiles` | 4 | Minimum de tuiles ; minimum configurable 2 |
| `--quality-source-sigma` | 4 | Seuil de segmentation, minimum 2 |
| `--quality-scales` | 2 4 8 16 32 | Échelles de lissage, pixels, entre 1 et 128 |
| `--quality-mask-growth` | 5 | Marge des objets ; les étoiles ont une marge plus grande |
| `--quality-min-noise-gain` | 0,05 | Gain utile relatif, à calibrer |
| `--quality-enforce-significance` | faux | Exiger le gain utile avec significativité |
| `--quality-max-flux-loss` | 0,05 | Tolérance relative de variation de flux, à calibrer |
| `--quality-enforce-diffuse` | faux | Rendre les contrôles photométriques bloquants |
| `--restoration-compare-chains` | vrai | Comparer les chaînes complètes ; `--no-restoration-compare-chains` revient aux étapes séparées |
| `--restoration-reverse-order` | faux | Inclure aussi netteté puis débruitage |
| `--restoration-siril` | vrai | Ajouter le moteur Siril aux opérations concurrentes |

Les options booléennes ci-dessus acceptent leur variante `--no-…`.

## Chaînes complètes

La comparaison est active par défaut, sauf choix contraire mémorisé dans la
configuration. La ligne de commande reste prioritaire :
`--restoration-compare-chains` force ce mode et
`--no-restoration-compare-chains` retrouve déconvolution puis débruitage avec
comparaisons indépendantes. L’ordre inverse reste expérimental et optionnel.

La comparaison des chaînes exige le mode `reliable`. Pour utiliser les
métriques historiques, passer ensemble `--quality-mode legacy` et
`--no-restoration-compare-chains` ; sinon une erreur explicite explique ce choix.

```bash
bin/postProcess.sh imagesExample/M_33_combined.fits /tmp/m33-quality \
  --restoration-compare-chains --quality-enforce-significance
```

Ce lancement conserve une seule correction de gradient et une seule photométrie
amont. Il nécessite les moteurs configurés ; l’exemple est une commande de
lancement, pas une annonce de validation scientifique de M33.

Les produits de comparaison des chaînes sont rangés directement dans
`03_restoration_work/` : masques communs, opérations numérotées de débruitage et
netteté, ainsi que dossiers `final_<numéro>` pour les contrôles finaux. Il n’y a
pas de sous-niveau `001` autour de cet ensemble : la comparaison n’est pas une
boucle de restauration successive. La réinitialisation à chaque lancement
remplace les produits précédents ; les éventuels essais internes d’un moteur
restent dans le dossier propre à ce moteur.

Avec trois débruiteurs et deux moteurs de netteté, la liste contient 12 chaînes :
identité, trois débruitages seuls, deux nettetés seules et six associations.
`--restoration-reverse-order` ajoute les six associations inversées, soit 18.
Les activations `--disable-denoise` / `--disable-deconvolution` sont respectées.
Il n’existe pas de plafond de candidats ni de classement préalable top-N.

Chaque opération passe ses contrôles locaux. Chaque chaîne admissible est
ensuite réévaluée contre l’image commune : les gains stellaires et de bruit ne
sont comptés qu’une fois. Un résultat nul ne bat pas l’identité. Les opérations
identiques sont partagées selon l’empreinte de l’entrée effective, l’opération,
la classe, les paramètres et les empreintes des dépendances présentes au début de cet appel. Sans échec ou recherche interne,
11/17 exécutions de moteurs suffisent pour les 12/18 variantes. Le cache n’est
pas persistant entre lancements ; modèles et exécutables doivent rester fixes
pendant un appel. Versions et empreintes disponibles sont conservées dans les
rapports enfants. Les opérations GPU ne sont jamais lancées en parallèle.

### Synthèse des essais et des éliminations

Le début du journal indique le mode réellement utilisé. En comparaison des
chaînes, le journal INFO et `03_restoration_summary.txt` présentent chaque
variante, ses contrôles locaux, son contrôle final sur la référence commune,
son score et le motif de son élimination ou de sa sélection. Le JSON contient
la même synthèse dans `summary.rows` et son chemin dans `summary_path`.

- **REJET LOCAL** : une opération n’a pas passé ses propres contrôles ; la suite
  de cette chaîne n’est pas exécutée.
- **REJET GLOBAL** : les opérations ont passé leurs contrôles locaux, mais la
  chaîne complète échoue face à la référence commune.
- **INDISPONIBLE**, **ERREUR TECHNIQUE**, **CONTRÔLE IMPOSSIBLE** : le candidat
  ne peut pas être évalué correctement ; la cause enregistrée est affichée.
- **NE BAT PAS L’IMAGE INCHANGÉE**, **SCORE INFÉRIEUR AU MEILLEUR**, **ÉGALITÉ** :
  le candidat a passé les contrôles, mais ne gagne pas le classement. Une
  égalité est départagée par l’ordre d’évaluation.
- **RETENUE** : chaîne sélectionnée, ou image inchangée si aucune chaîne sûre
  ne présente un score positif.

Pour chaque indicateur publié, la synthèse affiche PASS, FAIL ou UNKNOWN,
la valeur et le seuil effectifs, ainsi que les incertitudes, effectifs et
intervalles disponibles. Les détails par canal ou région sont conservés ; les
grands tableaux par tuile restent dans les audits scientifiques détaillés.
Dans le journal, les lignes `diffuse.région[...]` sont au niveau DEBUG
(`--log-level DEBUG`). Le verdict global `diffuse` reste au niveau INFO.
Le fichier de synthèse texte et le JSON conservent les détails par région
quel que soit le niveau de journalisation.
Les paramètres configurés apparaissent en tête. Les critères indiqués comme
« observation » ne sont pas bloquants : les motifs de rejet enregistrés
expliquent la décision effective. UNKNOWN reste une mesure indisponible,
jamais une valeur nulle. Les variations relatives sont en fractions : 0,02
signifie 2 %.

Les références `operation_id`, le chemin de l’audit et l’indication
« calculé/réutilisé » permettent de suivre les calculs partagés. Les essais
adaptatifs internes de Siril sont présentés séparément et ne comptent pas
comme des chaînes supplémentaires. Un audit écrit avant une erreur technique
reste disponible dans le rapport de l’opération.

Avec `--no-restoration-compare-chains`, les synthèses sont
`03_deconvolution_summary.txt` et `04_denoise_summary.txt`. Elles portent la
mention **ÉTAPE ISOLÉE** et précisent l’absence de classement global des chaînes.
Les chemins des synthèses produites figurent aussi dans les résultats du journal.

Une **CONCLUSION EXPLICATIVE** termine chaque synthèse au niveau INFO et figure
aussi dans `summary.explanation`. Elle traduit le traitement retenu, compte les
rejets et les candidats admissibles non retenus, et rappelle que le score n’est
pas un pourcentage de qualité. Conserver l’entrée de la restauration conserve
les traitements de gradient et de photométrie déjà appliqués.

Pour le signal diffus, elle regroupe les états des régions du contrôle final
retenu ou, si l’entrée est conservée, les mesures des audits locaux disponibles.
Ces dernières ne représentent pas nécessairement des zones distinctes.
`NO_LOCAL_SKY` signifie qu’aucun ciel local fiable ne permet d’estimer le fond
autour de la structure ; `LOW_DIFFUSE_SNR` indique un signal trop faible pour
conclure. UNKNOWN ne prouve ni une dégradation ni la conservation du signal.
La conclusion précise si ce contrôle reste diagnostique.

## GraXpert Denoising

Le moteur utilise l’inférence Python native ONNX. Il est activé par défaut si
des poids locaux valides sont présents, sauf choix explicite contraire ;
`--enable-graxpert` permet aussi d’acquérir les poids manquants.
Les poids 3.0.2 sont importés ou acquis automatiquement dans
`tmp/cache/models/graxpert/`, avec taille/SHA256 épinglés. Le CLI GraXpert sert seulement
à acquérir des poids absents, selon le mécanisme officiel ; il ne traite plus
les images utilisateur. Le repli CPU est contrôlé explicitement.

Voir [GraXpert natif : installation, options et algorithme](graxpert.md) pour
l’installeur commun, les modèles, la normalisation, le tuilage, les fournisseurs
et les résultats des validations réelles et simulées.

## Vérification et limites

```bash
PIP_NO_INDEX=1 .venv/bin/python -m pytest -q \
  tests/test_restoration_quality.py tests/test_restoration_chains.py \
  tests/test_graxpert.py tests/test_restoration.py
```

Les synthèses testent un bruit connu, une galaxie/filament effacé, une structure
rouge, un champ sans ciel fiable, les corrélations, les métadonnées et l’isolation
du gradient. Les faux moteurs vérifient 12/18 variantes et 11/17 opérations,
un gagnant final différent des meilleurs scores locaux, les erreurs et l’identité.
Les tests GraXpert vérifient le cache, l’acquisition officielle simulée,
le tuilage natif, les FITS et le repli CPU configuré. Une inférence CPU réelle
conditionnelle utilise les poids déjà disponibles et un extrait de M33. Ces tests ne calibrent pas les seuils sur
une population d’objets ni sur des références à fort SNR.

Une validation réelle avec Siril 1.4.4 sur un champ synthétique de 16 étoiles a
conservé toutes les références et mesuré environ 49,5 % de baisse de dispersion.
La comparaison de quatre variantes Siril a sélectionné le débruitage seul, avec
trois calculs distincts ; la netteté était sous le seuil de 2 %. Le diagnostic
diffus restait UNKNOWN sur cet essai. Cela valide l’intégration, pas les seuils
sur l’ensemble des objets ni une calibration astrophysique générale du débruitage GraXpert natif.

La segmentation version 3 a aussi été vérifiée sur les images après photométrie
de M31 et M33 et les candidats de débruitage déjà enregistrés. Elle retient
4 265 tuiles sur M31 (63,8 % de pixels de ciel) et 1 324 sur M33 (68,1 %),
dans les quatre quadrants. Sur M31, les baisses de dispersion du pire canal
sont d’environ 12,4 % pour Clarity, 21,1 % pour Siril et 41,9 % pour GraXpert.
Sur M33, elles sont d’environ 10,2 %, 35,3 % et 44,0 %. Les contrôles locaux
publiés précédemment deviennent admissibles avec ces mesures de bruit.
Cette vérification remesure les candidats existants ; elle valide la correction
de sélection du ciel, avec les cas synthétiques de protection du signal diffus.
La comparaison complète des nouvelles associations reste à exécuter lors du
prochain traitement des images réelles ; la préservation de tous les halos
faibles exige une calibration astrophysique plus large.
