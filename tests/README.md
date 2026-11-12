# Tests et non-régression

[Documentation](../docs/README.md)

Les tests de séparation et de rendu final couvrent un fond StarNet brut
supérieur à 1, sa conservation sur disque et la compensation du résidu signé
sans perte de la somme. Une somme hors domaine, les valeurs négatives du fond
et les NaN/Inf restent refusés.

Les tests de couleur finale vérifient aussi son activation implicite dans un
traitement complet, l'ordre GHS → couleur, les exports `_ghs`/`_saturated`, les
surcharges JSON/CLI et le parcours sans GHS. Les défauts communs
`winsorizedsigma`/`average` et le nom CLI long sont vérifiés pour darks et lights.

Les tests d'installation vérifient l'ajout Linux de `requirements-power.txt`
depuis PyPI sans contrainte de roue binaire, la propagation d'un échec de cette
phase et son absence sur le parcours macOS.

`test_postprocess_resume.py` couvre aussi la reprise d'un ancien échec AutoGHS
sans rapport global, les chaînes incohérentes et fichiers modifiés, et la
persistance du point de reprise avant un nouvel échec du rendu.

Tous les tests du projet se trouvent dans `tests/`. Le fichier `pytest.ini`
définit ce répertoire comme point de découverte par défaut et rend les imports
du projet accessibles aux tests.

`test_darkprocess.py` vérifie la validation des darks activée par défaut,
la priorité CLI > JSON > défauts, l'absence d'écriture implicite du JSON et
la transmission du contrôle lors d'un appel direct au traitement des groupes.
Il vérifie aussi le défaut commun de température à 0,5 °C dans les darks et
les lights et les surcharges JSON/CLI. `test_fits_info.py` contrôle l'effet de
ce pas sur le regroupement, la correspondance et l'en-tête du master.

## Exécution

Depuis la racine du projet, avec les dépendances de `requirements.txt` installées :

```bash
# Suite complète, y compris le contrôle de synthèse de non-régression
.venv/bin/python -m pytest -q

# Non-régression avec vérification du nombre de tests collectés et réussis
.venv/bin/python -m pytest -q tests/test_regression_summary.py

# Tests de mosaïque et d’accès à Siril
.venv/bin/python -m pytest -q tests/test_mosaic.py tests/test_mosaic_validation.py tests/test_integrated_script.py tests/test_simplified_mosaic.py tests/test_siril_validation.py

# Post-traitements FITS (Siril classique + backend Cosmic Clarity)
.venv/bin/python -m pytest -q tests/test_postprocess.py tests/test_deconvolution.py tests/test_denoising.py tests/test_cosmic_clarity.py tests/test_cosmic_inference.py

# Couverture des bibliothèques
.venv/bin/python -m pytest --cov=lib --cov-report=html
```

`test_regression_summary.py` découvre récursivement les `test_*.py` dans
`tests/`, s’exclut lui-même pour éviter une récursion, puis exécute ces tests
dans un sous-processus. Il vérifie le succès de l’exécution et compare le
nombre de tests réussis au nombre collecté par pytest. Le compte rendu est
écrit dans `regression_summary.txt` dans le répertoire temporaire du test.
Un nouveau fichier `test_*.py` est ainsi inclus automatiquement.

La suite complète exécute aussi cette synthèse, donc répète les autres tests.
Pour une exécution simple sans ce contrôle supplémentaire :

```bash
.venv/bin/python -m pytest -q --ignore=tests/test_regression_summary.py
```

## Domaines couverts

| Domaine | Fichiers de tests |
|---|---|
| Métadonnées FITS | `test_fits_info.py` |
| Calibration et reprises | `test_lightprocessor_calibration.py`, `test_force_recalc.py` |
| Sessions, stacking et orchestration mosaïque | `test_session_pipeline.py` |
| Sélection et profils stellaires | `test_quality_filter.py`, `test_stellar_quality.py` |
| Drizzle et transformations | `test_drizzle.py`, `test_registration_geometry.py` |
| Séquences Siril | `test_siril_sequence.py` |
| Post-traitements Siril et backend Cosmic Clarity | `test_postprocessor.py`, `test_postprocess.py`, `test_deconvolution.py`, `test_denoising.py`, `test_deconvolution_quality.py`, `test_photometric_color.py`, `test_cosmic_clarity.py`, `test_cosmic_inference.py` |
| Pipeline de post-traitement partagé | `test_postprocessing.py`, `test_session_pipeline.py` |
| Boucle solaire : centrage, corrections et sélection vide | `test_solar_eclipse_sequence.py` |
| Qualité astrophotographique, chaînes et GraXpert | `test_restoration_quality.py`, `test_restoration_chains.py`, `test_restoration_summary.py`, `test_graxpert.py`, `test_restoration.py` |
| Mosaïque : noms, entrées, préparation et script généré | `test_mosaic.py`, `test_mosaic_validation.py`, `test_integrated_script.py`, `test_simplified_mosaic.py` |
| Configuration, lancement et journaux Siril | `test_siril_validation.py` |
| Génération et navigation HTML, titres uniques et ancres du monofichier | `test_docs_html.py` |
| Synthèse de non-régression | `test_regression_summary.py` |

## Isolation et automatisation

`test_solar_eclipse_sequence.py` vérifie l’ordre réel des corrections sur des
images constantes synthétiques : recadrage et rotation de l’image et du masque,
normalisation puis filtrage du fond. Il impose seulement les détections du
disque et le déplacement de corrélation, pour tester les départs cohérents ou
incohérents, la mémorisation du centre et l’absence d’images film. Les sorties
doivent garder la taille prévue, une intensité de 100 et un facteur unité.
Un essai complet sur cinq FITS synthétiques exécute aussi le modèle solaire,
les corrections, les slots et les écritures : GIF de 64 × 64 pixels durant
une seconde, avec trois PNG de debug. Les FITS de cet essai renseignent
l’exposition et le gain. Voir la [correspondance des six diagrammes avec le
code](../docs/scripts/generate_docs_html/DIAGRAMS.md).

`test_calibration_logging.py` vérifie les titres INFO et leur ordre pour les
quatre combinaisons dark/flat, la reprise sans nouvel appel Siril, la simulation
et l’arrêt après échec du master flat. Les détails de préparation restent en
DEBUG. Les scénarios utilisent de petits FITS et simulent les produits Siril.

`test_drizzle.py` vérifie l’ordre des bandeaux INFO du stacking sur les branches
CFA standard et Drizzle forcé, ainsi que l’absence de titres pour les étapes
non exécutées après un échec. `test_session_pipeline.py` vérifie les bandeaux
de réutilisation dans les journaux de stacking, sans annonces de recalcul.

`test_postprocessing.py` compare les appels scientifiques, les paramètres et
les exports FITS/JPEG entre l’appel du processeur complet et le CLI autonome.
Il vérifie aussi les priorités de configuration, les instantanés indépendants,
les déclarations uniques partagées avec la mosaïque, les chemins protégés et
la fermeture du journal sur erreur ou interruption sans activation énergétique
dans la bibliothèque. Le CLI autonome détient et libère son propre PowerGuard.
Les bandeaux INFO du début de post-traitement sont vérifiés dans la console
capturée et les fichiers des deux modes d’appel. `test_postprocessor.py`
vérifie les titres et numéros des étapes actives, avec restauration groupée,
restauration par étape ou photométrie désactivée.
`test_restoration_chains.py` vérifie les sous-titres en tirets pour les calculs
et contrôles réellement exécutés, sans nouveau bandeau pour un calcul réutilisé.
`test_restoration.py` vérifie aussi l’ordre des titres pour les moteurs évalués
séparément et le classement final.
`test_session_pipeline.py` couvre l’option `--postprocess`, les stacks repris,
la mosaïque finale seule, la simulation et les échecs sans perte des stacks.
Deux scénarios vérifient l’enchaînement activé par défaut, sans option, sur un
stack et une mosaïque ; les tests limités aux étapes amont le désactivent
explicitement. Les désactivations JSON/CLI restent testées séparément.
Les moteurs simulés isolent l’enchaînement de la validation astrophotographique.
Les scénarios PowerGuard vérifient un contexte unique dans lightProcess pour
la calibration, les stacks, la mosaïque et les post-traitements, même lorsque
ces derniers sont désactivés. La simulation ne demande pas de protection ;
une erreur ou une interruption libère celle détenue par le script.

```bash
PIP_NO_INDEX=1 .venv/bin/python -m pytest -q tests/test_postprocessing.py tests/test_session_pipeline.py tests/test_render_exports.py
```

Les données de test sont créées dans des répertoires temporaires, notamment
avec la fixture `tmp_path`. `conftest.py` fournit les fixtures partagées, les
FITS simulés et l’environnement du wrapper `lightProcess.sh`.
Les tests Siril utilisent des commandes simulées ou de faux exécutables ;
ils ne constituent pas une validation d’un stacking réel sur les acquisitions.

Le workflow GitHub **Documentation MkDocs** exécute les tests des générateurs
avec `unittest`. La suite de non-régression complète s’exécute avec les
commandes pytest ci-dessus ; aucun workflow GitHub dédié à cette suite
n’est actuellement configuré.

## Refactoring des moteurs Siril

`test_deconvolution.py` et `test_denoising.py` couvrent les objets d’exécution
indépendants, les copies des options et l’audit des essais. Le repli adaptatif
vérifie aussi que le recadrage ne change pas la fraction minimale d’appariement.
Les tests appellent directement les méthodes des moteurs de type `processor`.
Les doubles de calcul sont injectés dans ces moteurs ; `test_restoration.py` contrôle leur
orchestration sur la même entrée.

Pour mesurer la couverture des deux modules sans créer d’artefact dans le dépôt :

```bash
PIP_NO_INDEX=1 COVERAGE_FILE=/tmp/siril-refactoring.coverage \
  .venv/bin/python -m pytest -q \
  --cov=lib.deconvolution --cov=lib.denoising \
  tests/test_deconvolution.py tests/test_denoising.py \
  tests/test_restoration.py tests/test_cosmic_clarity.py \
  tests/test_processor_args.py tests/test_processor_parameters.py
```

Ce contrôle porte sur ces modules et utilise des exécutions Siril simulées.
Il ne mesure pas la couverture complète du dépôt ni la validité d’un traitement
réel sur GPU.

Le test `test_cosmic_inference.py::test_real_weights_cuda` appartient à la
non-régression et s’exécute automatiquement si PyTorch et CUDA sont disponibles.
Sans CUDA, pytest affiche explicitement la raison du test ignoré (`-rs`). Il
charge les six vrais modèles et peut télécharger les poids manquants ; les
erreurs de téléchargement, de chargement et de calcul font échouer le test.
Aucune variable d’activation n’est nécessaire. `COSMIC_MODEL_DIR` peut choisir
le dossier des poids ; sinon le dossier ignoré `tmp/cache/models/cosmicclarity` est utilisé.

## Qualité astrophotographique

Les tests synthétiques utilisent un ciel/bruit et des structures connus, y compris
une galaxie et un filament effacés, une nébulosité dominante, du signal rouge et
du bruit corrélé. Les décisions UNKNOWN sont vérifiées sans faux bonus de bruit.
Les champs stellaires denses vérifient que le lissage ne transforme pas les
étoiles en faux signal diffus. Une image majoritairement couverte de signal
vérifie que le ciel connu dans les quatre coins reste exploitable, tandis
qu’un champ sans ciel identifiable conserve UNKNOWN.
Les tests des chaînes évaluent 12/18 variantes et 11/17 opérations élémentaires
avec cache par entrée effective ; le choix final ne somme pas les scores locaux.
Les synthèses distinguent rejets locaux et globaux, erreurs, indisponibilités,
UNKNOWN et candidats acceptés mais moins bien classés ; elles conservent les
audits écrits avant une erreur et les références des calculs réutilisés.
La conclusion explicative est vérifiée sur une chaîne retenue et une entrée
conservée, avec traduction des régions UNKNOWN et des limites du contrôle diffus.
`test_quality_preview.py` vérifie les couleurs des masques, les cadres verts,
l’orientation de l’aperçu, le cas sans ciel et la conservation des données.
`test_power_guard.py` vérifie les services D-Bus facultatifs, les ressources
acquises et leur libération même après une erreur de traitement.
La conservation du type du cookie est contrôlée systématiquement ; si
`dbus-python` est disponible, sa sérialisation réelle en `UInt32` est également
vérifiée sans connexion à un service système.
Les tests de paramètres vérifient l’activation des chaînes par défaut et la
priorité CLI > configuration > défauts.
GraXpert est testé par des sessions ONNX et une acquisition de poids simulées,
sans réseau. L’activation automatique est vérifiée sur les caches locaux valides,
absents, corrompus ou inaccessibles, ainsi que ses priorités CLI/configuration,
sa sauvegarde et son intégration aux comparaisons et aux chaînes.
Les scénarios à deux moteurs isolent les caches utilisateur.
Les tests de transmission entre déconvolution et débruitage désactivent
explicitement AutoGHS, dont le rendu est couvert par ses propres tests.
Une inférence CPU réelle conditionnelle sur un extrait de M33 est
exécutée lorsque ONNX Runtime, les poids et l’image exemple sont disponibles.
Les nouveaux contrôles de flux sont diagnostiques par défaut : ces tests ne
remplacent pas une calibration sur plusieurs références astrophotographiques.
Voir [le contrat et les limites](../docs/scripts/postProcess/quality.md).

`test_docs_html.py` vérifie la navigation, l’exclusion des commentaires de code
du sommaire, les diagrammes et la copie des JSON liés dans l’export HTML.
Les liens Python et shell pointent vers GitHub sans copie du code. Les poids
binaires et les cibles hors dépôt restent exclus.
`test_documentation_site.py` couvre les ancres distinctes des chapitres, leurs
liens internes, les ressources embarquées du monofichier et la protection des
dossiers. Une construction MkDocs complète rend les diagrammes dans Chromium
et génère le PDF avant de publier les téléchargements sur le site.
Le test de cadrage utilise Chromium en mode impression sur des figures larges
et hautes : elles doivent tenir dans la largeur disponible et sous 247 mm de
hauteur, avec évitement des coupures. Ce test est ignoré si Playwright ou son
navigateur ne sont pas installés ; les autres erreurs du navigateur sont bloquantes.
Il compare aussi la hauteur et l’espacement des libellés HTML intégrés aux SVG
entre écran et impression pour détecter les textes superposés dans le PDF.
Avec le cache Mermaid disponible, un test vérifie aussi les losanges élargis
sur des choix courts, longs et multilignes, en disposition verticale et
horizontale : largeur augmentée, hauteur diminuée, libellés inchangés et
contenus dans la forme. Ce test est ignoré si le cache manque ; construire la
documentation avant de le lancer. Une version Mermaid incompatible est refusée.
Le même scénario vérifie le placement automatique de groupes imbriqués avant
et après ajout d’une branche, d’une boucle et d’un libellé multiligne : hauteur
réduite avec les réglages communs, textes conservés et aucun symbole superposé.
Le diagramme réel de calibration est aussi rendu : la sortie « Oui » du choix
de reprise doit partir à droite du losange et la sortie « Non » à gauche,
sans coordonnées fixes pour les symboles.
Les pages contenant des blocs Mermaid sont également converties avec les
extensions et formateurs réels de `mkdocs.yml`, puis rendues dans Chromium.
Le test exige un SVG par bloc et aucun pictogramme d'erreur, pour les flux et
les séquences. Il couvre notamment l'enveloppe HTML des blocs produite par
SuperFences, qui n'est pas testée par un rendu des seules sources Mermaid.
Tous les diagrammes réels sous `docs/` sont également rendus et contrôlés avec
`fixtures/documentation_crossings.js` : les intersections transverses des
flèches sont recherchées dans un repère SVG commun. Des cas synthétiques
vérifient le détecteur, les jonctions et les transformations des groupes.
L’échantillonnage des courbes exclut les extrémités proches et les segments
quasi colinéaires ; une inspection visuelle reste nécessaire pour les contacts,
recouvrements et libellés. Le [relevé des croisements et convergences](../docs/scripts/generate_docs_html/DIAGRAMS.md#croisements-et-convergences)
explique les convergences conservées et les croisements supprimés.
Le workflow documentaire construit les exports avant les tests pour préparer
le cache Mermaid nécessaire à ces vérifications avant publication.
Les tests vérifient aussi l’ordre commun des rubriques, le sommaire imbriqué,
les accès directs aux options sans duplication des chapitres, le regroupement
des annexes et les ancres conservées après numérotation des manuels. Une cible
absente ou hors des sources de navigation est refusée explicitement.

## AutoGHS, StarNet et rendu final

Le GHS final sans étoiles est vérifié à D=1,25, avec désactivation à 0,
distribution constante, forces invalides et entrées non finies. RANGE,
avec et sans chroma, doit reproduire exactement l’application
de la courbe validée au fond déjà rendu, sans modifier les recherches amont.
Le contrat de publication vérifie son application avant la fusion stellaire
et sa présence dans les rapports ; la configuration vérifie CLI > JSON >
défaut, la sauvegarde explicite et les instantanés indépendants par instance.

Les tests de compensation vérifient les dépassements locaux StarNet de type
M31, même supérieurs au seuil d’alerte : branches positives, somme linéaire
conservée, données d’entrée inchangées et diagnostic explicite. Un scénario
complet produit le rendu RANGE et son JPEG après compensation. Les seuils
invalides et les branches corrigées hors domaine restent refusés.

Les tests `test_star_separation.py`, `test_star_rendering.py`, `test_auto_ghs.py`
et `test_final_render.py` couvrent respectivement l'adaptateur externe, le rendu
stellaire/fusion, les mathématiques/recherches et le contrat du pipeline :

Les tests de rendu stellaire vérifient aussi la réduction du renforcement de
base jusqu'au premier essai admissible, l'arrêt immédiat lorsque la demande
convient, les facteurs fractionnaires, l'épuisement borné à 1 et les largeurs
non mesurables. Les couleurs, le support et les données sources sont préservés.
Le rendu stellaire adaptatif est vérifié sur un champ synthétique de 36 pics
colorés : davantage de pics franchissent le contraste indicatif 0,03, avec
conservation des zéros, rapports RGB, plafond et garde de largeur. Les cas
constants, plateaux et forces invalides sont couverts. Les exports étoiles
FITS/JPEG et le diagnostic PNG sont vérifiés dans le pipeline. La cible de
dynamique 1,0 est testée pour RANGE ; la configuration préserve les
surcharges et la nouvelle force stellaire sauvegardée.

```bash
.venv/bin/python -m pytest -q tests/test_star_separation.py tests/test_star_rendering.py tests/test_auto_ghs.py tests/test_final_render.py tests/test_postprocessor.py
```

Le contrat exige une séparation, un rendu stellaire, un seul traitement RANGE
et une fusion avec la couche d'étoiles ; les valeurs du FITS final sont vérifiées,
ainsi que les métadonnées, extensions et la source
inchangée. Les pannes d'extraction, calcul, fusion et publication sont injectées
sans autoriser de reprise. La configuration vérifie priorité, sauvegarde
explicite et instantanés d'instance. Les histogrammes constants, gaussiens,
points noirs ambigus/sûrs, limites des courbes, convergence et déterminisme sont
vérifiés ; la chroma RGB optionnelle conserve la direction au gris et le gamut.

`tests/data/auto_ghs_siril_1_4_4.json` contient des références **réellement
calculées par Siril 1.4.4**, et non par une deuxième copie de l'implémentation
Python. L'écart maximal GHS observé d'environ `1.02e-6` motive la tolérance absolue `2e-6`.
Le point noir de référence donne un écart nul. Les branches B négatives passent
les tests mathématiques, mais la CLI Siril locale les refuse.

La validation réelle est volontairement opt-in : elle nécessite Siril Flatpak,
StarNet V2, ses poids, et un dossier temporaire accessible depuis Flatpak. La
commande suivante crée ses FITS et rapports **dans `tmp/autoghs-validation/`**,
qui est réinitialisé par pytest et ne doit contenir que ces données de test :

```bash
SIRIL_AUTOGHS_INTEGRATION=1 .venv/bin/python -m pytest -q tests/test_auto_ghs_integration.py --basetemp=tmp/autoghs-validation
```

Le test CLI utilise un extrait RGB 512 × 512 de `imagesExample/M_33_combined.fits`.
Le chemin StarNet vient des préférences Siril ; `STARNET_PATH` permet une
surcharge pour cette validation. Siril est en Flatpak par défaut ; pour une
installation native ou AppImage (macOS, Windows), définir `SIRIL_MODE=native` et
`SIRIL_PATH=/chemin/siril-cli`, utilisés par les deux tests. La CLI désactive les traitements amont pour
ne pas les réexécuter et conserve le chemin réellement résolu dans un JSON
isolé avec `-S`. Elle vérifie les deux sorties **avec étoiles**, les deux fonds,
le rendu stellaire, les histogrammes, la reconstruction linéaire, l'absence
d'écrêtage, les indicateurs non linéaires et les données originales intactes.
Un second test recalcule les oracles mathématiques dans le Siril installé et
compare la recomposition screen à la commande PixelMath réelle ; l’écart screen
mesuré est de `5.96e-8` environ.

Le test CuPy est ignoré sans CUDA/CuPy fonctionnel. Des doubles couvrent
l'absence initiale de CUDA et une panne après initialisation ; ils ne valident
pas une exécution matérielle GPU. Une inspection astrophotographique sur
nébuleuse, IFN, bande étroite, ciel bruité et étoiles très brillantes reste
nécessaire pour qualifier les heuristiques de rendu. Voir
[AutoGHS](../docs/scripts/postProcess/auto-ghs.md), son
[algorithme](../docs/scripts/postProcess/auto-ghs-algorithm.md) et le
[traitement des étoiles](../docs/scripts/postProcess/star-rendering.md).

La politique générale de calcul est vérifiée par `test_compute_config.py` :
déclaration unique, défaut auto, transmission aux moteurs créés tardivement,
sauvegarde, surcharges historiques et choix CPU/CUDA selon la disponibilité.
Les doubles de disponibilité ne constituent pas une validation matérielle GPU.

```bash
.venv/bin/python -m pytest -q tests/test_compute_config.py
```

Les tests de `test_final_render.py` vérifient aussi la publication de deux PNG
distincts (sans étoiles et avec étoiles recomposées), l’absence de mélange de
leurs courbes et les axes logarithmiques, y compris avec des classes vides.

Les encarts AutoGHS sont vérifiés sur des recherches acceptées, refusées et
un arrêt avant recherche, avec distinction des candidats et corrections BP.

Les tests AutoGHS vérifient les dossiers visibles et ordonnés, le numéro fixe 05
même après fusion des étapes de restauration, la conservation des produits
partiels accompagnés d’un rapport ERROR et la réinitialisation sans produits
périmés lors d’une relance. Une source dans le dossier réinitialisé est protégée.

Le test CLI du nom de sortie vérifie le dossier, le rapport et le FITS
`<image>_postprocess`, avec Clarity activé puis désactivé.

La boucle d’expansion AutoGHS est vérifiée pour RANGE avec les cibles
0,8/0,9, le gain borné, les images déjà étendues ou constantes, la limite de
passages et les paramètres invalides. Les tests vérifient BP sur le premier RGB
strictement positif, la conservation des valeurs supérieures et la sérialisation
du rapport complet, avec les deux boucles distinguées dans les figures.

L’égalisation partielle AutoGHS est vérifiée sur un histogramme concentré :
diminution de la dispersion des effectifs, étalement des intensités, monotonie,
respect des bornes, conservation des ratios RGB, désactivation et cas constant.
Le renforcement stellaire vérifie le gain des faibles intensités, le plafond,
l’absence de nouveaux pixels stellaires et la reproduction du rendu antérieur
avec un facteur 1. Les gardes de largeur et de couleur restent actives.

Les tests des chaînes de restauration vérifient que masques et opérations sont
placés directement dans `<rapport>_work/`, sans niveau `001`, y compris après
une relance sur une ancienne arborescence. Une entrée située dans le dossier
réinitialisé est refusée avant toute suppression.

Le défaut stellaire `star_faint_boost=5` est vérifié contre le facteur 3 : gain
des très faibles intensités proche de 5/3, même support positif, ratios RGB et
plafond conservés. Les profils gaussiens sont contrôlés aux deux réglages.

`test_render_exports.py` vérifie les exports JPEG RGB pleine résolution, les
intensités directes, l’orientation ROWORDER, l’intégrité du FITS, les entrées
invalides et la copie du JPEG principal par le CLI. Le contrat AutoGHS vérifie
un seul appel RANGE, une seule fusion, l’absence des produits PEAK et des
comparatifs, puis leur nettoyage lors d’une relance sur un ancien dossier. Les
anciens choix de stratégie CLI/JSON sont normalisés vers RANGE ; le moteur
PEAK n’est plus exécutable. Les histogrammes du fond et de l’image étoilée
restent distincts.

`test_final_annotation.py` contrôle l’encart final de prise de vue : provenance
des cartes FITS, placement automatique, conservation des pixels hors panneau,
absence de totalisation supposée des mosaïques et options de configuration.

`test_annotation_catalog.py` vérifie OpenNGC, la projection WCS avec rotation
et passage RA=0, les orientations et les exclusions automatiques d’étiquettes.

`test_ghs_final_stretch.py` vérifie la recherche automatique D/B/SP/HP sur trois
distributions spatiales, la conservation du contraste brillant et du petit cœur,
les fusions à étoiles fixes et le retour à l'identité. `test_postprocessing.py`
vérifie que `--autoghs-only` saute tous les traitements linéaires et leurs aperçus,
garde les exports et ne persiste pas le mode de relance.

`test_ghs_log_histogram.py` confronte la régression semi-log à une pente
exponentielle connue, vérifie sa résistance à une bosse secondaire, les
classes vides, les bordures, la borne haute conditionnelle 0,75/0,875 et le
classement renforcé du RMS relatif face à la pente. Les tests du moteur final
contrôlent aussi SP variable, la visibilité des tons moyens, la distinction
entre P98 sombre et hautes lumières, et la parité CPU/CUDA lorsqu'elle est disponible.
Ils vérifient aussi les bornes hautes figées, le meilleur score sur tout le champ
parmi trois finalistes, et la conservation d'un champ déjà rectiligne. Les tests
de configuration couvrent le poids RMS, ses limites et sa priorité CLI/JSON.

`test_postprocess_resume.py` vérifie la reprise avec la même entrée initiale,
la conservation des réglages et rapports amont, les reprises répétées, les
intermédiaires périmés/manquants/non linéaires et les anciens rapports.
Les tests de sessions vérifient que la reprise lightProcess saute calibration,
stacking et mosaïque et garde les mêmes réglages.

`test_cuda_environment.py` contrôle aussi la séparation des index PyTorch/PyPI,
les pins torch identiques et les échecs de chacune des deux résolutions avant
toute suppression des paquets en conflit.
Il contrôle l’installation CUDA dans le seul venv cible,
la résolution avant retrait des conflits, les échecs et la validité du graphe
ONNX. Les contrôles matériels sont lancés séparément avec `install_dependances.sh`.

`test_macos_environment.py` couvre les plateformes Apple Silicon simulées,
Rosetta et versions refusées avant mutation, l'ordre de résolution/retrait,
les pins Mac sans CUDA et le graphe Add ONNX exécuté sur CPU. Le test matériel
MPS/CoreML ne s'exécute que sur Darwin arm64 ; il est ignoré sous Linux.
`test_compute_config.py` contrôle auto CUDA/MPS/CPU et la surcharge MPS
persistante. `test_graxpert.py` vérifie la configuration CoreML sans préchargement
NVIDIA. `test_venv_helpers.py` vérifie la sélection du venv existant, les erreurs
sans création implicite et les six lanceurs de traitement/visualisation avec
des outils réseau/pip interdits. Les arguments contenant des espaces sont
transmis intacts. Le lanceur documentaire est vérifié séparément : préparation
du venv, installation des dépendances documentaires et de Chromium, arrêt
sur échec de pip avant la construction.
Les choix publics `auto`/`cpu`, la migration CLI/JSON de l'ancien `cuda` global,
les WARNING de choix CPU automatique et leur absence en CPU forcé sont couverts
par les tests de configuration et des moteurs. Le Mac NumPy/CuPy signale
l'absence de moteur Metal sans tenter d'importer CUDA.
Sur un Mac réel, utiliser `bin/install_dependances.sh --check-only` avant traitement.

`test_dependency_installation.py` vérifie l'installateur unique : sélection
Linux/Mac, refus des plateformes non couvertes, nettoyage limité au venv,
précontrôle NVIDIA avant suppression, réinstallation suivie du test et mode
`--test` sans mutation. Les combinaisons `--reset --test` sont refusées.
Les suppressions réelles des tests restent dans leurs dossiers temporaires.

`test_installation_assets.py` vérifie le préchargement par défaut des modèles,
les priorités de chemins, Gaia optionnel et l'absence d'écriture du JSON.
`test_gaia_catalogue.py` utilise une archive bzip2 miniature pour vérifier les
deux SHA, la décompression, la réutilisation sans réseau et les erreurs qui
doivent conserver le fichier précédent. Les essais réels ont vérifié les
poids et le catalogue Gaia déjà en cache, sans nouveau téléchargement massif.
`test_resource_cache.py` vérifie l'arborescence commune, les anciens chemins,
la vérification du pointeur Gaia et le nettoyage des modèles qui préserve Gaia
et les fichiers étrangers. Les tests photométriques vérifient le passage
automatique à `localgaia` avec préférences temporaires sans écriture utilisateur.

Les contours d'annotation sont contrôlés par leurs dimensions angulaires et
leur angle de position, après projection WCS avec rotation et inversion des
lignes. Les tailles distinctes de M31/M32 et le repli vers un marqueur de centre
lorsque les dimensions sont absentes sont également vérifiés.

Les noms usuels d'annotation sont vérifiés pour M31/M33/M42, avec priorité au
français disponible, conservation des identifiants et repli vers le nom original
ou les seuls identifiants lorsque le catalogue ne fournit pas de nom usuel.

Les tests GraXpert vérifient aussi la configuration des sessions mixtes GPU/CPU,
le refus d’une session sans fournisseur GPU lorsque celui-ci est demandé, et
la désactivation du redémarrage implicite ONNX. Ces tests simulent les
fournisseurs ; ils ne valident pas la répartition réelle des opérations sur GPU.

`test_final_color.py` vérifie aussi que `--autoghs-only` reprend à GHS puis
exécute la saturation malgré un JSON désactivé, sauf `--disable-final-color`
explicite, sans modifier les paramètres mémorisés.
Il vérifie la passe couleur après GHS : conservation de Y et
la direction des écarts RGB, ciel et cœur inchangés, couleurs fortes moins
renforcées, gamut, force nulle et entrées invalides. Les tests de reprise
interdisent les appels amont, exécutent deux reprises identiques, réexportent la
référence avec la force zéro et rejettent les couches modifiées. La configuration
contrôle CLI > JSON, l'activation facultative et le mode de reprise non rémanent.
La parité CPU/CUDA est vérifiée quand CUDA est disponible. Les tests de sessions
couvrent `--color-only` avec et sans mosaïque, sans calibration ni stacking.

`test_stacking_annotation.py` vérifie les comptes indépendants des poids, les
panneaux de mosaïque, les métadonnées absentes/incohérentes et les rapports
périmés. L’intégration de l’encart utilise explicitement le stack d’origine.

Les tests de `test_session_pipeline.py` appellent aussi directement
`LightProcessor.process_target`, sans lancer le CLI : session directe, une ou
plusieurs sous-sessions, image unique après rassemblement des calibrations,
session vide et poursuite après un échec retourné ou une exception. Ils
contrôlent les entrées exactes de l'unique empilement et l'exclusion des
calibrations échouées. Un test vérifie que la découverte CLI délègue à la classe.
La calibration et Siril sont simulés dans ces scénarios d'orchestration.

La classe unique `Mosaic` est testée dans les deux contextes : les stacks de
sessions fournis par `lightProcess`, et les panneaux CLI autonomes exposés par
`add_arguments(parser, include_inputs=True)`. Les tests vérifient aussi
l'enregistrement de la classe dans `Config`, les paramètres de son gradient
enfant, les instantanés indépendants et la sauvegarde sélective.

Le calcul du nom automatique de mosaïque appartient au script `lightProcess.py` ;
ses cas de préfixe, mot commun, liste vide et session unique sont vérifiés
dans `test_session_pipeline.py`.

Le test de préparation du site exécute `DocumentationBuilder.build` sur un
mini-guide lié à un JSON et à un fichier Python : seul le JSON est copié.
Le rendu navigateur et MkDocs y sont simulés ; la construction réelle des
exports et du site se vérifie avec `.venv/bin/python bin/build_documentation.py`.

`test_config.py` contrôle également l'import de `Config` sans autre module
applicatif et la déclaration parent/enfant sans héritage applicatif : alias,
paramètres non rémanents, préfixes, copies indépendantes et sauvegarde explicite.

Les tests de calibration vérifient `--no-flat` avec des flats réellement présents
et Siril simulé : aucune sélection de flats ni création de master, aucune option
`-flat` dans le script, darks conservés sauf `--no-dark`. Ils contrôlent aussi
`--flat`, les priorités CLI/JSON, la sauvegarde explicite, les instantanés et la
fabrique `from_config`. Une alternance avec/sans flats vérifie le recalcul des
sorties en cache puis la reprise lorsque le réglage reste identique.

Les tests `test_logging_utils.py` et `test_processing_timings.py` utilisent une
horloge déterministe sans attente pour vérifier les cumuls en secondes, les
rapports enfant/global, les étapes désactivées et la propagation des échecs ou
interruptions. Les tests des chaînes vérifient un chronométrage par calcul et
contrôle final réellement exécutés, les hits de cache et les cumuls par moteur.
Les tests des moteurs utilisent Siril et les inférences simulés ; ils ne mesurent
pas les performances matérielles réelles.

Les tests de couleur finale couvrent les résidus float32 aux deux bornes du
gamut : reprise du pixel RGB GHS entier, entrée intacte et audit explicite.
Un dépassement significatif ou étendu reste refusé ; la tolérance ne s'applique
pas aux entrées GHS invalides. L'essai réel NGC7331 reproduit un unique canal
à environ -2,79e-9 avant correction, avec des couches sources valides.

`test_quality_processors.py` vérifie les processeurs de mesure indépendants et
l'orchestrateur récursif : masques avant tuiles, branche stellaire, branche des
contrôles sur pixels, durées distinctes, tableaux partagés, conservation des
sources, UNKNOWN sans arrêt des autres diagnostics et propagation des échecs
avec audit partiel. Le catalogue de référence est réutilisé au second appel.
Les tests stellaires contrôlent aussi l'arbre de comparaison/mesure locale.

`test_noise_tiles.py` compare les calculs par lots aux moindres carrés et
corrélations scalaires, teste les tuiles de bord, les masques incomplets et la
sélection spatiale déterministe. Trois scénarios de parité CUDA/NumPy utilisent
un GPU réel s'il est disponible. Les tests des processeurs vérifient plafond,
quadrants, canaux RGB, mode exhaustif, options rémanentes et propagation d'une
panne GPU sans reprise CPU. Les gains de temps sont mesurés séparément des
assertions : aucun seuil de performance dépendant du matériel n'est imposé.

Les tests de `test_quality_processors.py` vérifient aussi la propriété locale des
options, leur collecte récursive, les branches partagées sans doublon et
l’indépendance des listes de valeurs par défaut entre parseurs.

`test_ghs_linear_histogram.py` et `test_ghs_quartiles.py` vérifient la priorité
à la longueur continue rectiligne, la liberté de pente, les trous, les bosses
et les SP aux ruptures de pente (dont 0,5), la hauteur log relative à 0,875,
les maxima RGB indépendants, l'échelle de population, la validation finale de
la grille et de la recomposition, le plafonnement et la désactivation du bonus,
le support incomplet, le pic sombre isolé et son budget, les paramètres
récursifs CLI/JSON, le déterminisme et la validation complète des passes.

`test_gaia_spcc.py` vérifie les acquisitions spectrales miniatures, les reprises
HTTP et les interruptions, les empreintes, les importations et la découverte
sans réseau. Les tests d’installation et de photométrie couvrent les options
Gaia complètes et la sélection automatique de SPCC local.

Les tests d’installation contrôlent aussi l’activation par défaut de tous les
modèles et catalogues, leurs désactivations indépendantes et globales, la
transmission au worker et le mode `--test` sans acquisition.

`test_resource_manager.py` couvre les points d’entrée partagés entre installateur
et traitements, les contrôles communs et la réutilisation de ressources locales
sans réseau, avec conservation des protocoles propres à chaque adaptateur.

Les tests SPCC couvrent aussi les sélections nord/sud/complément/all, les
intersections équatoriales, la continuation après échec d’une région et le
bilan final qui empêche de certifier une sélection incomplète.

`test_diffuse_regions.py` vérifie la sélection spatiale par octaves de taille,
les grandes régions rares, le mode exhaustif et l’identité des mesures retenues
avec celles du calcul exhaustif.

`test_diffuse_filters.py` confronte les filtres PyTorch CUDA/MPS à SciPy
lorsque le matériel est disponible. Les convolutions PyTorch sont aussi testées
sur CPU (bords, petits axes, types flottants et médianes paires/NaN). Il
vérifie les formes de fenêtres, l’absence de structure, la surcharge locale,
le choix CPU pendant le contrôle initial et la propagation des pannes GPU.


`test_torch_backend.py` vérifie sans GPU les opérations PyTorch partagées :
médianes paires/NaN, percentiles interpolés, couleur finale pour human/even et
forces 0/1/3, bruit double et hybride MPS sur fond élevé et fort gradient.
Les tenseurs sont volontairement sur CPU pour ces vérifications numériques.
Les options locales sont résolues récursivement par Config ; les tests des
moteurs conservent les contrôles d'erreur et les essais matériels conditionnels.
Les tests MPS ignorés sous Linux restent à exécuter sur un Mac compatible.


`test_output_layout.py` vérifie les six étapes numérotées, les copies physiques
et les en-têtes, l'identité stable des panneaux malgré leur réordonnancement,
les synthèses JSON/TXT et les reprises d'un historique interrompu ou de la
couleur seule. Les tests CLI imposent une seule entrée hors mosaïque et
contrôlent les relais, chemins génériques et étapes communes sans Siril réel.
Les tests d'annotation retrouvent les comptes du stack initial et des panneaux
à travers le relais, sans inventer de poses ni modifier les sources.

Les tests de calibration vérifient aussi les étapes `02_registration` à
`05_stacking` directement sous `01_calibration`, et la conservation de
`01_session` lors d’un empilement forcé.

La préparation directe de séquence couvre les FITS mono/RGB, les liens et
leur remplacement par des copies, avec comparaison binaire des sources.
Ces tests ne valident pas une exécution réelle de Siril.

La photométrie vérifie le dossier direct sans `001`, le nom `02_photometrie`
dans le plan numéroté, la remise à zéro lors des relances et la protection
d’une entrée placée dans ce dossier. Les appels Siril sont simulés.

Les dossiers de restauration sont vérifiés dans `test_postprocess_paths.py`
(noms numérotés, isolation des opérations, nettoyage et sources protégées).
Les tests des moteurs contrôlent les artefacts directs sans `001` ; ceux des
chaînes vérifient aussi le regroupement `04_restoration`.

Les tests du rendu GHS vérifient les étapes nommées `01_separation` à
`05_diagnostics` directement à côté du rapport, sans enveloppe `autoghs_work`.
Ils couvrent le nettoyage des anciens produits et enveloppes lors des relances,
la conservation des fichiers voisins, le retrait des liens sans suivre leur
cible et le refus d'une source dans chaque dossier nettoyé avant toute suppression. La couleur finale vérifie des produits directs
dans son dossier de travail, sans niveau `001`, avec des FITS synthétiques.

La régression de transmission du stack (`.fit`/`.fits`) est couverte dans
`test_lightprocessor_calibration.py` : le faux Siril réécrit les images
astrométriques selon `setext`, puis les étapes réelles lisent la séquence
transférée. Tous les scripts de stack fixent `setext fits` et les sources
sont comparées avant/après. Un essai Siril 1.4.4 sur deux copies M31 a aussi
vérifié la lecture après transfert et l’absence de doublon, sans relancer
les traitements complets M31/M33.

Le générateur commun Siril impose `setext fits` dans les scripts sauvegardés
(native, AppImage et Flatpak). Les tests de validation vérifient cet en-tête ;
ceux de calibration et des post-traitements couvrent les chemins `.fits`,
tout en conservant la lecture des entrées historiques `.fit`.

Les essais de restauration regroupent leurs rapports, masques FITS/PNG et
résultats dans leur dossier. `test_restoration_chains.py` vérifie les chemins
et la réutilisation du cache ; `test_postprocess_paths.py` vérifie que les
moteurs Siril préservent les masques du dossier préparé et refusent une
source qui serait écrasée. Les calculs moteurs de ces scénarios sont simulés.

Les noms des dossiers d’essais décrivent désormais le parcours complet,
par exemple `denoise_graxpert_then_sharpen_siril`. Les tests de chaînes
vérifient les noms et numéros selon l’ordre normal ou inverse, ainsi que
la conservation du chemin de la première exécution lors des réutilisations.

Les tests de chaînes vérifient aussi le rattachement des masques à l’image
d’entrée : un jeu partagé sous `00_mask_origine`, puis les masques des
intermédiaires dans le dossier producteur. Les audits référencent FITS et PNG ;
les scénarios dans les deux ordres vérifient l’absence de copies par consommateur.

Les essais de chaînes conservent un FITS effectif auprès de leur rapport. Les
tests contrôlent la sortie acceptée, la copie exacte de l’entrée en cas de
rejet ou sans amélioration sûre, et l’absence de résultat artificiel en échec.
Les audits distinguent `accepted_output` et `retained_input`.

Les tests Drizzle vérifient que SKIPPED.txt décrit les décisions, mesures et
seuils du rapport JSON. Les contrôles finaux sont rangés auprès du traitement
évalué, avec textes et JSON, et restent liés par la synthèse globale.

`test_processing_html.py` contrôle les index HTML hiérarchiques, aperçus
JPEG/PNG et liens FITS, audits incomplets, protection des index étrangers,
conservation des sources et reprise des stacks malgré la génération HTML.
La préparation commune Siril est vérifiée dans `test_siril_validation.py`.
Les transferts scientifiques omettent les pages HTML, même imbriquées.

Dans le plan numéroté, les essais de restauration et `00_mask_origine` sont
directement sous `04_restoration`, sans `01_chains` ni `diagnostics`. Les tests
des chemins vérifient le nettoyage ciblé des produits et la conservation des
rapports et fichiers voisins. Les tests de pipeline vérifient l’absence de
nouvelle génération de diagnostics dans ce parcours.

Les tests de restauration vérifient la décomposition du score existant
(termes FWHM, gain utile, pénalités bruit/anneaux), les égalités et écarts du
classement des chaînes acceptées, la conservation de la référence à score zéro
et les mesures absentes. Les tests HTML vérifient le tableau de classement
et l’extraction visible des statuts, paramètres, valeurs, seuils et motifs JSON.

Les tests de sélection qualité couvrent aussi la trace par pose : seuils
effectifs, rangs et égalités, contrôles désactivés ou non évalués après rejet,
mesures non finies, profils et sélection finale. Les tests Drizzle vérifient
la persistance dans les deux rapports JSON ; les tests HTML vérifient leur
présentation et la distinction des audits anciens sans décisions détaillées.

Les seuils de profils sont testés avec plusieurs groupes comparables : stockage
unique dans les références, identifiant par pose, groupe insuffisant sans seuil,
lecture des anciens `limits` et affichage des limites avant les tableaux.

`test_siril_api.py` couvre la factory avec un véritable transport local et le
worker sirilpy : fermeture et erreurs, authentification, isolation des sessions,
modèles et tableaux. Les tests des traitements utilisent des `SirilCommand`
et vérifient les mêmes opérations et critères scientifiques. L’installateur
vérifie aussi le module fourni par le moteur Siril choisi.

`SIRIL_API_INTEGRATION=1 .venv/bin/python -m pytest -q tests/test_siril_api_integration.py`
valide avec Siril réel un champ synthétique irrégulier : quatre poses décalées,
registration, audit qualité et empilement. Les sources et les préférences sont
préservées. Les produits sont temporaires sous `tmp/`, accessible au Flatpak.
Le test est désactivé par défaut et accepte `SIRIL_API_MODE` / `SIRIL_API_PATH`.


Les tests `test_registration_audit.py` vérifient l’audit structuré de chaque
registration : compteurs d’appariement, référence, seuils explicites ou
inconnus, collecte incomplète et capture avant fermeture. Les tests HTML
vérifient les liens de définition et l’absence de téléchargement JSON.
L’essai opt-in `SIRIL_API_INTEGRATION=1` couvre aussi une pose synthétique sans
étoiles rejetée par Siril parmi quatre poses étoilées retenues ; il contrôle
les effectifs, l’inclusion enregistrée, l’index et la conservation des sources.


Les tableaux HTML de restauration sont vérifiés avec les mêmes conventions
que la qualité : titres verticaux, groupes de critères, deux décimales,
symboles et légendes. Les tests distinguent une chaîne rejetée d’une chaîne
acceptée non retenue, conservent les drapeaux non bloquants, vérifient les
contrôles locaux et laissent les autres étapes dans leur disposition actuelle.
Les valeurs JSON restent inchangées.

### Seuil de régression AutoGHS à 0,8

`test_ghs_linear_histogram.py` vérifie la conversion de densité vers les
classes du graphique, le seuil 10¹, la correction de population échantillonnée,
la priorité du seuil, la progression intermédiaire, la désactivation et les
entrées invalides. `test_ghs_quartiles.py` contrôle l'audit pleine résolution et
les protections cumulatives. Ces tests synthétiques ne valident pas le rendu
visuel de M31 ou M33 ; relancer AutoGHS sur ces images reste nécessaire.

### Diversification de la recherche de rectitude AutoGHS

`tests/test_ghs_quartiles.py` couvre les forces faibles, les raccords HP
précoces, SP <= HP, l'absence de doublons de grille, l'alternance des familles,
la poursuite après trois rejets complets simulés et l'acceptation d'un candidat
suivant. Les tests contrôlent le budget configurable et l'audit des candidats
restant à examiner. Le sondage ciblé est vérifié sur des régions brillantes
absentes de la grille régulière, avec leurs voisins et une population bornée ;
un rejet par ce sondage doit précéder toute validation complète. Les protections cumulatives restent vérifiées sur le fond
et sa recomposition. Une validation sur un champ réel est distincte de ces
tests synthétiques ; les résultats M31 doivent préciser le seuil atteint ou
non, les rejets et le nombre de validations effectivement réalisées.

### Contraste brillant et queue rare AutoGHS

Les tests de `test_ghs_quartiles.py` vérifient le comptage du champ actif,
l'exclusion des bordures nulles, l'interpolation de 50 % à 90 %, le retour à
la protection normale à 0,1 %, la désactivation, les populations vides et
les limites invalides. Le sondage ciblé doit encore rejeter une perte de
contraste du cœur à Y >= 0,95 : l'adaptation brillante ne relâche pas cette
garde. Les contraintes de bruit, couleur et écrêtage sont conservées.

L'essai réel M31 de la phase de rectitude est décrit dans la section
[Vérification](../docs/scripts/postProcess/ghs-quartiles.md#6-vérification).
Il utilise les couches rendues existantes et ne remplace pas les tests
Siril/StarNet ; les FITS, JPEG et rapports d'essai ne sont pas versionnés.

### Mémoire de la rectitude AutoGHS

`test_ghs_quartiles.py` compare exactement les transformations par blocs au
calcul plein champ float64, pour deux modes de luminosité, avec/sans point
noir et plusieurs tailles de bloc. Une vue non contiguë plus large que le
budget vérifie la borne réelle des entrées du noyau. Des références faibles
vérifient la destruction de chaque candidat avant le suivant, puis le
recalcul exact du meilleur avec ses seuls paramètres. Les tailles invalides
sont rejetées avant allocation. `test_auto_ghs.py` vérifie que le nettoyage du
pool libre CUDA reste sans effet sur CPU et propage les erreurs de pilote.
Ces tests CPU ne remplacent pas une mesure de mémoire sur un GPU réel.

Les tests CUDA `test_cuda_quartile_parity_when_available` et
`test_cuda_block_transform_limits_full_size_allocations` confrontent le
résultat au CPU et observent les tailles réellement demandées au pool. Ils
sont ignorés si CUDA est indisponible. Exécution ciblée utilisée :

```bash
.venv/bin/python -m pytest -q tests/test_ghs_quartiles.py -k 'cuda' --basetemp=/tmp/autoghs-cuda-pytest
```

### Prévision VRAM et reprise CPU AutoGHS

`test_auto_ghs.py` vérifie le budget depuis la géométrie RGB finale, le choix
CPU préventif avec une VRAM simulée insuffisante, puis une reprise CPU unique
sur la seule erreur CUDA OutOfMemoryError en auto. Les tests contrôlent la
libération des cadres interrompus, les entrées intactes, la provenance du
backend et l'absence de reprise sur erreur de calcul, RAM hôte ou CUDA forcé.
Une reprise CPU qui échoue reste fatale. Le test matériel suivant limite
volontairement le pool CuPy à 4096 octets sur un petit tableau, restaure sa
limite dans finally et confronte la reprise au résultat CPU direct :

```bash
.venv/bin/python -m pytest -q tests/test_auto_ghs.py -k cuda_pool_limit --basetemp=/tmp/autoghs-cuda-fallback-pytest
```

La validation réelle du GHS complet de M31 sur CUDA, avec mesure du pool,
est décrite dans [la gestion mémoire](../docs/scripts/postProcess/ghs-quartiles.md#33-mémoire-cpu-et-cuda).
Elle réutilise des couches StarNet existantes et ne valide pas à nouveau cette
séparation. Les données et rapports de cet essai ne sont pas versionnés.

### Régression de qualité M33 et sélection des moteurs GHS

`test_ghs_linear_histogram.py` vérifie qu'une extrapolation haute depuis un
segment qui s'arrête avant 0,8 ne valide pas le seuil, et qu'un segment peuplé
couvrant 0,8 est recherché. `test_ghs_quartiles.py` reproduit la perte diffuse
mesurée sur M33 et le cas de sept pixels brillants répétés dans des paires :
le sondage ne doit pas inventer une région exploitable absente du champ entier.
`test_ghs_log_render.py` contrôle les sorties indépendantes des moteurs de
longueur et de niveau, le classement commun, le rejet d'une sortie assombrie,
la préférence pour les objectifs réellement atteints et la conservation de
l'entrée quand la sélection est désactivée. Les encarts doivent afficher une
queue non atteinte, l'extrapolation éventuelle et les segments effectivement
mesurés. Les essais matériels sur M33 et M31 restent distincts de ces tests.

Les essais réels sont décrits dans
[la vérification des recherches](../docs/scripts/postProcess/ghs-quartiles.md#6-vérification).
Les jeux réels ne sont pas distribués avec les tests.

### Rectitude depuis la sortie du pic et populations rares

`test_ghs_linear_histogram.py` vérifie le bord droit du pic à 2 % des effectifs
lissés, l'interdiction de contourner une bosse ou un trou et l'impossibilité
de valider le niveau à 0,8 sur une droite isolée en fin de queue.
`test_ghs_quartiles.py` vérifie la somme des poids de population, la conservation
des classes rares et des pixels inactifs, la correspondance des coordonnées
RGB et la fidélité de la prédiction pondérée pour une transformation connue.
`test_ghs_log_render.py` exécute le moteur stratifié et vérifie que ses mesures
finales sont recomptées sur tous les pixels, sans marque d'estimation.
`test_final_render.py` vérifie la propagation du statut `TARGETS_NOT_REACHED`
et de la liste des objectifs incomplets jusqu'au rapport exporté.

### Profil GHS par morceaux et série logarithmique

Le classement par objectifs puis déplacement du pic est testé avec des mesures
synthétiques et une recherche complète à deux finalistes : un candidat au score
secondaire plus fort ne doit pas remplacer un candidat conforme au pic plus
proche. Les tests couvrent aussi la progression d'un rendu incomplet, le refus
de forme, le déplacement inconnu et le maintien d'un seuil strict explicite.
La recherche par branches couvre un cas où le premier meilleur parent est
terminal mais où un parent initialement second atteint ensuite les objectifs.
Les tests vérifient le budget partagé par profondeur, les gardes cumulées,
les chemins composés sans tableaux d'image dans les rapports, la couverture
de pics à gauche, au milieu et à droite, et la bissection des écarts de pic.
La galerie conserve les alternatives admissibles et leur filiation sans les
confondre avec le chemin finalement choisi.

`piecewise` est le modèle par défaut ; les tests de configuration couvrent
le maintien d'un choix explicite `line`. `test_ghs_blackpoint.py` vérifie
le recalage affine après courbe, les poids, les minima liés et le budget
cumulatif de canaux positifs, y compris les recalages préalables et l'expansion.
Les essais M33 17/19 sont rejoués sur copies hors dépôt et proposés au sélecteur
général après correction ; aucun numéro ni nom d'objet n'intervient dans le code.
Les tests synthétiques conservent des cas d'aplatissement rejeté et acceptent
un raccord terminal descendant. Le bruit est contrôlé par rapport au gain
de contraste utile en mode par morceaux, et absolument en mode historique.

Le contrôle local couvre aussi le plateau intermédiaire conservant pourtant
la chute globale, la déformation relative des pentes voisines et une variation
homogène acceptée. `test_auto_ghs.py` exécute la réduction de force de la CDF
contre une référence fixe sur une population exponentielle et vérifie le
cas sans référence mesurable.

`test_ghs_histogram_shape.py` couvre trois pentes analytiques, les variations
significatives après lissage d'une queue bruitée, la protection cumulative de
la décroissance, la géométrie du pic et la vérification finale du sélecteur.
Un candidat atteignant le niveau à 0,8 est rejeté s'il aplatit la forme.
Les niveaux sont exprimés dans les mêmes classes que le graphique, sans
extrapolation à travers une région absente. Le mode `piecewise` est le défaut ;
`line` reste disponible explicitement pour les comparaisons historiques.

`test_ghs_diagnostics.py` exécute l'export d'une série, inspecte les axes
Matplotlib logarithmiques et leurs limites communes, et vérifie les verdicts,
la galerie, le manifeste et la coexistence avec l'index HTML commun.
Les dernières figures identifient l'essai retenu par son numéro, y compris
lorsqu'une branche ultérieure a été explorée, quand un autre moteur est choisi
ou lorsque l'entrée est conservée. Les histogrammes égaux ne servent pas à
deviner la provenance ; un rapport incomplet garde une valeur non renseignée.
Les encarts de série et les liens vers l'essai sont contrôlés dans les PNG et
la galerie, avec et sans JPEG.
`test_final_render.py` vérifie les synthèses sans panneau d'effectifs linéaires.
Les fonds noirs, les encarts et textes contrastés, la visibilité de l'enveloppe
et du repère à 0,8 sont contrôlés dans les figures et les PNG écrits. Le thème
reste local à la figure, même si la configuration Matplotlib impose un export
blanc. `test_star_diagnostics.py` vérifie le même thème et conserve les niveaux
RGB et l'orientation des extraits stellaires ; la galerie HTML reste sombre.
Les encarts de déplacement du pic sont vérifiés sur les essais estimés, les
recomptages complets, les finalistes de présentation et la synthèse ; une mesure
absente ou non finie ne doit pas devenir un déplacement nul.
L'option `--ghs-save-trial-jpegs` est exercée par le pipeline complet dans
les modes `line` et `piecewise`, avec une séparation StarNet simulée : les
images gardent leur résolution et le JPEG final est identique à l'export
principal. Les tests vérifient les correspondances de numéros, les refus,
les estimations, les aperçus recomposés, l'orientation, la fermeture du cache
après erreur d'écriture et la priorité CLI sur la configuration mémorisée.
Les essais réels réutilisent les couches StarNet de M31, M33 et NGC7331 ;
leurs PNG, FITS et rapports restent hors dépôt. Les calculs de comparaison
utilisent `/tmp` ; les galeries peuvent être conservées dans les diagnostics
des objets, distinctement des produits de traitement existants.

`test_ghs_render_quality.py` vérifie le plafonnement du contraste/bruit et de
la visibilité, les bonus lumineux bornés, les pénalités continues de forme,
de bosse et de compression (fond et fusion), le départage par déplacement du
pic, les mesures absentes et la priorité CLI > JSON > défauts des deux options.
La mesure rapide est confrontée aux définitions spatiales du finaliseur sur
les mêmes masques. `test_ghs_histogram_shape.py` exerce la sélection complète et
les branches avec ce score ; `test_final_render.py` vérifie ses encarts.
Ces tests synthétiques sont distincts des essais CUDA sur les entrées de phase
figées de M31, M33 et NGC7331, dont les résultats restent dans `/tmp`.

Les cas de régression du classement couvrent aussi le changement de pic dominant
qui masque un creux à gauche, les bosses juste après le pic et une compression
extrême du bruit du ciel par une courbe GHS. Les mesures natives de creux sont
comparées à la référence, pas déclarées nulles quand elles manquent.
Les sondages de cœur et de couleur sont exercés dans les moteurs `line` et
`piecewise` : une pénalité brillante ne permet pas de contourner ces gardes.

Le contrôle local du pic est testé indépendamment du lissage global : un
second mode doit produire une pénalité même si la spline du corps décroît.
Une bosse native reste admise et une référence absente n'est pas déclarée nulle.
`test_ghs_blackpoint.py` vérifie le quantile affiné dans un bin contenant des
pertes antérieures, les poids et la borne de tri à 65 536 valeurs. Le test
`test_candidate_histograms_are_evaluated_after_the_actual_blackpoint` confronte
les histogrammes évalués aux pixels réellement retournés après recalage, y
compris pendant la validation complète. L'encart distingue luminosité Y,
minimum RGB protégé, budget consommé et budget restant.

Les tests du BP répété contrôlent la marge d'intensité de type machine en
float32 et float64 : les canaux encore positifs ne doivent pas être érodés
jusqu'aux valeurs sous-normales. La préparation CDF doit rejeter un dépassement
de budget noir, même lorsque sa forme est admissible. Ces cas couvrent le
blocage de tous les finalistes par une perte numérique d'un pixel.
