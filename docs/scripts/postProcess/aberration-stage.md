# 1. Étape optique Aberration Remover avant restauration

[postProcess](README.md) · [Moteur ONNX](aberration.md) · [Restauration](restoration.md)

## 1.1. Position et sorties

La séquence standard est **gradient → couleurs → Aberration Remover →
restauration → AutoGHS**. `AberrationCorrectionStage` sélectionne le résultat
optique ; `AberrationEngine` calcule uniquement le candidat. La restauration
reçoit exactement `output_image` de cette étape, puis compare Siril, Cosmic
Clarity et GraXpert. Aberration Remover n'est plus un moteur de ces comparaisons.
Il ne dépend pas de l'activation de la déconvolution ou du débruitage.

L'étape est active par défaut, désactivable par `--disable-aberration`.
`--aberration-model ""` la saute également. Le modèle fourni dans `data/` et
les options de calcul restent décrits sur la [page du moteur](aberration.md).
Le calcul n'applique aucun renforcement local supplémentaire aux étoiles.

Le plan lightProcess place cette étape dans `04_aberration_remover/aberration.json`, avec ses
catalogues, scripts, journaux et candidat dans `aberration_work/`.
La restauration suit dans `05_restoration/`, puis AutoGHS dans `06_autoghs/`
et la couleur et publication dans `07_color/`. Les anciens dossiers ne sont
pas renommés automatiquement, en particulier pendant un calcul en cours.
En autonome, les rapports sont
`03_aberration.json`, `04_restoration.json` et `06_autoghs.json` ; sans fusion
de restauration : `04_deconvolution.json`, `05_denoise.json`.
`--autoghs-only` reprend l'entrée enregistrée du rendu et ne relance pas cette étape.

## 1.2. Mesures et déclenchement

La détection Siril utilise le profil gaussien sur le vert d'une image RGB,
ou l'unique canal mono. Les étoiles saturées, contaminées par une voisine,
mal ajustées ou de SNR inférieur à 10 sont exclues par les mesures communes.
Chaque étoile est mesurée aux mêmes positions initiales avant/après.

Deux critères sont enregistrés :

- rondeur `b/a = min(FWHMx, FWHMy) / max(FWHMx, FWHMy)`, entre 0 et 1 ;
- finesse `sqrt(FWHMx × FWHMy)`, en pixels.

Les médianes sont séparées entre champ complet, bords et centre. Une étoile
appartient aux bords si sa position est dans une bande de 20 % de la largeur
ou hauteur, sur l'un des quatre côtés ; les coins appartiennent aussi à ce groupe.
Les positions sont celles d'avant traitement, jamais une nouvelle sélection
des seules étoiles devenues faciles à détecter.

Un essai est déclenché si la rondeur médiane du champ ou des bords est sous
0,85, ou si la FWHM médiane des bords dépasse 1,25 fois celle du centre.
Le champ exige au moins 10 références fiables et chaque sous-groupe au moins
5 pour participer à la décision. Un seuil absolu facultatif de FWHM peut aussi
déclencher l'essai. Sans défaut mesuré, aucun modèle n'est chargé. Une couverture
insuffisante ne signifie pas que les bords sont sains : elle est enregistrée
explicitement, et ne déclenche pas seule un essai. L'entrée reste utilisée.

Ces seuils sont des choix heuristiques génériques, pas des constantes optiques.
Le seuil absolu de rondeur est sans unité. **Il n'existe pas de FWHM idéale
universelle en pixels** : elle dépend de la focale, des pixels, du seeing et
de l'échantillonnage. `--aberration-max-fwhm-px` est donc absent par défaut.
Un excès bords/centre peut aussi provenir d'autres causes que les aberrations
que le modèle sait corriger. Les médianes ne garantissent pas chaque coin.

## 1.3. Sélection après calcul

Les paires doivent satisfaire les contrôles communs de correspondance et de
représentativité stellaire. Pour chaque groupe évalué, il faut au moins 70 %
de rétention des références initiales, 10 paires globales et 5 par sous-groupe.
Les étoiles perdues ne peuvent donc pas simplement améliorer la médiane.

Le gain utile est soit la médiane des différences de rondeur ≥ 0,03 point,
soit `1 − médiane(FWHM_après / FWHM_avant)` ≥ 5 %. Si le défaut ayant motivé
l'essai concerne les bords, ce groupe doit lui-même montrer un gain utile.
Un essai forcé doit au moins montrer un gain global.

Dans les groupes fiables, une perte médiane de rondeur supérieure à 0,01 point
ou une augmentation médiane de FWHM supérieure à 3 % refuse le candidat.
La géométrie, les métadonnées et les pixels finis sont également contrôlés.
Cette sélection stellaire ne constitue pas une validation photométrique du
signal diffus ni une preuve de correction physique d'une aberration donnée.

Chaque essai calculé mesure également le [flux diffus local](quality.md),
avec les ouvertures fixes, fonds locaux et contrôles multi-échelles communs.
`aberration_work/diffuse_flux.json` garde le détail ; `diffuse_flux` dans le
rapport d'AR contient le statut, les effectifs et `median_relative_change`,
médiane des valeurs région/canal disponibles (y compris UNKNOWN avec valeur).
La formule est `(flux_après − flux_avant) / flux_avant`, négative en cas de perte.
Une absence de mesure donne `null`, jamais zéro. Ce diagnostic ne change pas
la sélection stellaire ; il n'est pas un flux global intégré de l'objet.
Son calcul ajoute un audit de régions diffuses à chaque essai effectivement
exécuté, y compris un candidat ensuite refusé, sans lancer les autres moteurs.
L'[image annotée](annotations.md) affiche uniquement le statut d'utilisation
d'AR. Les gains, seuils et variations de flux restent disponibles dans les rapports.

Si le gain est trop faible ou les correspondances insuffisantes, `output_image`
désigne **le fichier d'entrée, inchangé**, avec `status: retained_input`.
Sinon le candidat est transmis avec `status: accepted`. Une étape non déclenchée
porte `status: skipped`. Les pannes de Siril ou du modèle restent des erreurs,
auditées et propagées, plutôt qu'une prétendue amélioration absente.

## 1.4. Options et vérifications

| Option | Défaut | Rôle |
| --- | --- | --- |
| `--enable-aberration` / `--disable-aberration` | activé | Activer l'étape indépendante |
| `--aberration-roundness-target` | `0.85` | Cible de rondeur initiale |
| `--aberration-edge-fwhm-ratio` | `1.25` | Excès initial FWHM bords/centre |
| `--aberration-max-fwhm-px` | non défini | Limite absolue optionnelle, en pixels |
| `--aberration-edge-fraction` | `0.2` | Largeur des bandes, dans `]0, 0.5[` |
| `--aberration-min-roundness-gain` | `0.03` | Gain minimal, en points de rondeur |
| `--aberration-min-fwhm-gain` | `0.05` | Réduction relative minimale de FWHM |
| `--aberration-force` | faux | Forcer l'essai, sans supprimer ses contrôles après calcul |

Les options suivent la priorité CLI > configuration > défaut local et sont
mémorisables avec `-S`. Les valeurs non finies ou hors domaine sont refusées.
Le JSON garde les paramètres, mesures initiales, raisons du déclenchement,
paires, gains par groupe, décision et chemin transmis. Les décisions sont
également journalisées. Les sources ne sont jamais réécrites.

`tests/test_aberration_stage.py` couvre les déclencheurs, les gains séparés,
les régressions, la perte d'étoiles sur les bords, l'absence de chargement du
modèle pour un champ sain et le maintien de la source. Les tests de séquence
contrôlent l'ordre, la transmission et les reprises avec les moteurs simulés.
La calibration des seuils sur des images complètes et une exécution réelle
Siril/ONNX restent distinctes de ces vérifications synthétiques.
