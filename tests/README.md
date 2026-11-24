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

`test_stellar_appearance.py` vérifie l'objectif non bloquant de 40 % d'étoiles
visibles : 40 % atteint le seuil, tandis que 20 %, 25 % et 30 % échouent au diagnostic.

Les tests synthétiques utilisent un ciel/bruit et des structures connus, y compris
une galaxie et un filament effacés, une nébulosité dominante, du signal rouge et
du bruit corrélé. Les décisions UNKNOWN sont vérifiées sans faux bonus de bruit.
Le contrôle diffus distingue une perte corroborée (FAIL) d'une augmentation de
flux (variation positive conservée dans le rapport, sans FAIL). Les valeurs
suivent `(après - avant) / avant`, avec un seuil signé négatif et des résidus
et intervalles négatifs pour les pertes confirmées.
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

## AutoGHS, branches et diagnostics

Les tests automatisés utilisent des images synthétiques et, selon le scénario,
un adaptateur StarNet simulé. Ils ne constituent pas une validation d'une
nouvelle séparation réelle dans Siril. Les références spécialisées décrivent
les algorithmes, leurs unités et les procédures sur des images d'observation.

| Sujet vérifié | Tests | Référence |
|---|---|---|
| Transferts RGB, bornes, options et égalisation | `test_auto_ghs.py`, `test_auto_ghs_integration.py` | [AutoGHS](../docs/scripts/postProcess/auto-ghs.md) |
| Épaulement, signe et ratios des pentes, ex aequo de profils | `test_ghs_histogram_shape.py`, `test_ghs_shape_ratios.py` | [Forme des histogrammes](../docs/scripts/postProcess/ghs-histogram-shape.md) |
| Classement de qualité, poids du niveau à 0,8 et bonus à 0,875 | `test_ghs_render_quality.py` | [Qualité du rendu](../docs/scripts/postProcess/ghs-highlight-protection.md) |
| Recherches GHS, budgets et rectitude logarithmique | `test_ghs_quartiles.py`, `test_ghs_log_render.py`, `test_ghs_final_stretch.py` | [Quartiles et critères](../docs/scripts/postProcess/ghs-quartiles.md) |
| Conservation de la somme, résidus signés et refus d'entrées invalides | `test_star_separation.py`, `test_final_render.py` | [Séparation stellaire](../docs/scripts/postProcess/star-rendering.md) |
| Branches autonomes, données préservées et paramètres indépendants | `test_stretch_branches.py`, `test_final_render.py` | [Classes des branches](../docs/scripts/postProcess/stretch-branches.md) |
| Recettes cumulées, géométrie native et reproduction des candidats | `test_stretch_transfer.py`, `test_final_render.py` | [Transformations cumulées](../docs/scripts/postProcess/stretch-transfer.md) |
| PNG, galeries, JPEG et traçabilité des essais | `test_ghs_diagnostics.py`, `test_final_render.py` | [Exports](../docs/scripts/postProcess/render-exports.md) |
| Nettoyage limité aux produits et préservation des sources | `test_postprocess_paths.py`, `test_output_layout.py` | [Organisation des sorties](../docs/scripts/lightProcess/output-layout.md) |

Les tests du pipeline contrôlent aussi que changer l'étirement des étoiles ne
modifie pas les pixels ou diagnostics du fond, et réciproquement. Les deux
rapports de branche doivent être disponibles avant la recomposition, même si
celle-ci échoue. Les collecteurs JPEG sont fermés après succès comme après erreur.

La cible à 0,8 est exprimée en log10 : les tests contrôlent notamment 1→10,
2→100 et 1,5→31,62, la désactivation, les surcharges CLI/JSON et la sauvegarde.
Le bonus du niveau à 0,8 reste distinct du bonus de hauteur à 0,875 ; son poids
ne contourne pas les gardes de forme ou de contraste. Les ratios après
l'épaulement autorisent un aplatissement homogène mais signalent les inversions,
les ruptures isolées et les déplacements de l'épaulement.

### Calage stellaire à 25 %

`test_star_rendering.py` vérifie la courbe ponctuelle calée par le quantile des
25 %, ses rapports RGB, le bruit résiduel, ses bornes et les profils à 50 % et
10 % du pic. Les options suivent CLI > JSON > défaut, avec instantanés
indépendants par instance et sauvegarde explicite. Les tests de recette
contrôlent l’absence de coordonnées, la restriction neutre et la reproduction
RGB exacte sur une image complète ou recadrée. `test_stellar_selection.py`
conserve la compatibilité des anciennes recettes spatiales.

Les [essais sur quatre couches réelles](../docs/scripts/postProcess/stellar-selection.md)
réutilisent les séparations et fonds déjà produits. Ils ne relancent ni
StarNet ni Siril et ne remplacent pas la validation de la chaîne complète.

### Signal sonore de fin

`test_completion_sound.py` vérifie le WAV mono PCM 16 bits, sa durée, son
amplitude et sa décroissance, les lecteurs système simulés, les erreurs,
les délais et le nettoyage des fichiers temporaires. `test_postprocessing.py`
et `test_session_pipeline.py` contrôlent un seul appel après succès et aucun
en cas d'échec ou de simulation. La configuration reste persistante et
prioritaire en CLI. Aucun test ne joue de son réel ; la validation audible
sur chaque système reste distincte, comme indiqué dans
[la référence du signal sonore](../docs/scripts/postProcess/completion-sound.md).

Pour lancer les contrôles ciblés de ces ajouts :

```bash
.venv/bin/python -m pytest -q tests/test_ghs_shape_ratios.py tests/test_completion_sound.py tests/test_stretch_branches.py tests/test_stretch_transfer.py
```

La sélection des darks locaux est vérifiée dans
`test_lightprocessor_calibration.py` : dossiers `dark` et `Dark`, priorité du
FITS unique compatible, utilisation sans bibliothèque, conservation du fichier
source, repli sur la bibliothèque (incompatibilité, fichier invalide, dossier
vide ou plusieurs fichiers) et désactivation par `--no-dark`.

La compatibilité des darks de calibration accepte un écart de gain arrondi de
1, dans les deux sens, et rejette un écart de 2. Les tests vérifient aussi que
la comparaison stricte par défaut et les métadonnées sources sont préservées.

`test_stellar_profiles.py` conserve la couverture de compatibilité des anciennes
recettes à profils : halos brillants, raccords, champs denses, profils tronqués
et recettes invalides. Le mode global `pointwise` à 25 % utilise la courbe ponctuelle
`histogram_quantile` : les tests vérifient son calage à 0,08, l’absence de
coordonnées, les gardes de largeur et sa reproduction exacte, y compris après
recadrage. Les comparatifs réutilisent les séparations existantes sans relancer
Siril ni StarNet. Les tests du raccord `monotone_cap_v1` vérifient une
rampe dense, la conservation de la base brillante et un profil gaussien radial
sans inversion créée au seuil de protection. Ils contrôlent aussi la
compatibilité des recettes sans politique et le rejet des politiques inconnues.
`test_stretch_transfer.py` vérifie en plus la luminance `human`, RGBBlend près
du plafond, la différence avec le mode `even`, l'enregistrement du mode couleur
et la reproduction bit à bit d'une recette stellaire. Les tests de branches
contrôlent que `--ghs-color-mode` est partagé par le fond et les étoiles.

`test_aberration.py` couvre Aberration Remover avec une session ONNX simulée :
conservation médiane/MAD, recouvrements, petites images, protections, entrées
invalides, FITS uint16 et WCS, activation et énumération des chaînes, sélection du modèle livré depuis un
autre dossier de lancement et désactivation par chemin vide. Ces tests
ne remplacent pas la comparaison sur modèle réel avec Siril.

`test_restoration_chains.py` vérifie aussi les séquences dans les bandeaux et
les durées des calculs : moteur seul, débruitage puis netteté, et ordre inverse
activé explicitement. Le titre ne doit pas annoncer une étape future.

Les chaînes avec Aberration Remover sont aussi exécutées avec des moteurs
simulés : correction uniquement sur la référence et en première position,
limite de deux étapes sans répétition de type, cache commun, classement final
et ordre inverse. Pour trois débruiteurs et deux autres moteurs de netteté,
ces scénarios couvrent 16/22 variantes et 15/21 calculs distincts lorsque tous
les contrôles acceptent. Les combinaisons de deux nettetés sont interdites.

`test_final_render.py` couvre aussi la préparation affine des entrées RGB
finies hors de [0,1] : données signées, dépassement supérieur, identité,
inversion et absence de modification de l'entrée. Le scénario de séparation
contrôle le FITS préparé, les extensions, l'audit et la conservation de la
source ; NaN/Inf et géométries invalides restent refusés.

Les régressions GraXpert vérifient les bornes réelles du tenseur ONNX, la
protection des valeurs brillantes sur le tenseur non borné et le bornage après
mélange, en float et uint16, avec audit des extrema/effectifs. Les tests de
chaînes rejouent les signatures et empreintes de toutes les étapes, vérifient
les pixels de chaque référence locale et la référence initiale du contrôle
final. Les traitements restent indépendants des cibles astronomiques.

Les tests `test_cosmic_inference.py` vérifient le mélange Cosmic Clarity après
inversion du préétirement : identité à 0 sans réseau, résultat complet à 1,
interpolation à 0,25/0,5, modes mono/luminance/full/separate, flottants faibles,
flottants déjà lumineux, valeurs signées et uint16. Ils vérifient aussi le
résidu de couleur indépendant, la source intacte, les HDU/WCS et le domaine
`linear_input_units` dans l'audit. Les réseaux simulés sont non identitaires
pour détecter un déplacement accidentel du mélange avant l'inversion MTF.

`test_restoration_preview.py` contrôle les JPEG AutoStretch de restauration :
fond cible de 0,25, courbe monotone commune, RGB lié, mono/constantes/valeurs
signées, orientation, source intacte, candidats rejetés, désactivation et erreurs
auxiliaires. `test_restoration_chains.py` vérifie les JPEG publiés à chaque
opération et le partage effectif des paramètres avec l'entrée et la sortie finale.

Les tests GHS de modes sombres (`test_ghs_histogram_shape.py` et
`test_ghs_render_quality.py`) couvrent une seconde bosse après un creux
profond, le changement de pic dominant, l’invariance au nombre de pixels,
les épaules monotones, l’exclusion de la queue brillante, les populations
insuffisantes et les entrées invalides. Ils vérifient la tolérance de 10 %
par rapport à la référence fixe, la compatibilité des anciens rapports, le
rejet avant score malgré des bonus élevés et son affichage dans les encarts.
La comparaison directe de forme, utilisée par la CDF et la présentation,
applique aussi cette garde.

`test_stellar_contrast.py` couvre le rendu global courant : conservation des
sources, valeurs RGB identiques transformées identiquement, recette sans
coordonnées et rejeu exact après translation ou recadrage. Les anciennes
options locales ne peuvent pas réactiver un traitement spatial. Les
utilitaires de profils sont vérifiés séparément pour la lecture des archives :
piédestaux, voisins brillants, recouvrements et recettes invalides. Les mesures
de largeur couvrent aussi les pics sous l'ancien seuil fixe de 0,005.

`test_star_separation.py` couvre également la préparation MTF de StarNet :
cible médiane, inversion hors écrêtage, audit des valeurs écrêtées, canaux
constants et cas dégénérés, validation des paramètres, absence de double
pré-étirement, orientation, conservation de la source et priorité CLI/JSON.
L'adaptation est vérifiée sur des sources synthétiques petites, étendues ou
allongées, avec centres fixes, essais non mesurables et limite inaccessible.
Les essais de géométrie ne déclenchent aucune inférence ; une séparation
Siril/StarNet réelle reste un niveau de validation distinct.

`test_restoration_comparison.py` vérifie le décodage progressif aux frontières
arbitraires des blocs, les JSON invalides et tronqués, la découverte et le
dédoublonnage des chemins, la séparation des contrôles locaux et finaux, les
valeurs absentes, zéro et False, la conservation des alertes non bloquantes,
l'échappement HTML et la neutralisation des formules CSV, la précision numérique,
les écritures concurrentes et le code de retour en cas d'export partiel.
Les essais réels lisent des rapports existants et n'exécutent aucun moteur IA.

`test_restoration_report_storage.py` vérifie le format synthétique v2, la
conservation des mesures détaillées, l'absence de mutation du cache, les
copies supprimées dans les critères et tentatives, l'identité des décisions
et colonnes comparatives entre anciens et nouveaux rapports, et les erreurs
de publication atomique. Les tests des chaînes vérifient aussi le branchement
réel des synthèses globales vers les audits locaux et finaux, avec des
populations de mesures présentes uniquement dans les détails.

Le comparateur vérifie également le regroupement HTML des critères en une
seule colonne, avec des lignes nommées et des verdicts textuels colorés, tout
en conservant les colonnes analytiques du CSV.

`test_restoration_early_stop.py` couvre les seuils stricts de 30 % et 35 %,
l'arrondi flottant à la frontière de FWHM, les mesures manquantes/non finies,
les FAIL non bloquants, UNKNOWN et critères futurs ou absents, la validation
des paramètres et la priorité CLI/JSON avec instantanés indépendants. Les
parcours intégrés sur FITS synthétiques vérifient la chaîne prioritaire,
l'arrêt effectif, les comptes rendus texte/HTML, la poursuite exhaustive sans
recalcul des préfixes, le rejet local et la désactivation explicite.

La branche stellaire doit rester une transformation globale de l'histogramme,
conformément aux consignes communes. Les tests vérifient l'égalité de sortie
pour des RGB identiques dans des voisinages différents, le rejeu exact après
permutation, translation ou découpe, l'absence de profils spatiaux dans les
nouvelles recettes et l'impossibilité de réactiver le débruitage ou le rendu
local avec une ancienne configuration `profiles` / `star_noise_sigma=8`.
Les tests des utilitaires de profils locaux concernent le rejeu des archives,
pas leur utilisation dans les nouveaux rendus.

`test_stellar_appearance.py` vérifie les indicateurs purement diagnostiques :
objectif exact de 25 %, populations inconnues aux bords, absence de candidat,
rayons et gradient gaussiens analytiques, comparaison d'une transition élargie,
jupe Moffat plus étendue et conservation des pixels. Les objectifs sont non
bloquants ; aucun seuil de netteté perceptuelle n'est inventé.

Le contrôle d'ordre des pentes est testé par différences signées directes :
pentes négatives, positives ou nulles, quasi-égalités, invariance par
multiplication positive et exception de la queue terminale. Un test du PNG
vérifie les cinq segments de contrôle effectivement présents dans la légende,
distincts du découpage adaptatif.

`test_ghs_weak_structure.py` vérifie les populations fixes indépendantes du
candidat, leur signature, la préférence d'un ciel sobre avec préservation du
contraste et du CNR, la protection contre une solution artificiellement noire,
le budget de visibilité initiale des images linéaires et le repli en absence
de segmentation fiable. Il contrôle aussi le maintien de la garde de nouvelle
bosse sombre malgré un bonus de signal faible et la limitation des écarts de
courbure aux pentes descendantes. Les essais réels sur grille et les recherches
GHS complètes sont distingués dans les comptes rendus.

`test_aberration_stage.py` vérifie le déclenchement optique avant restauration,
les gains appariés de rondeur/FWHM au centre et aux bords, le refus des
régressions et des pertes de correspondances, et le maintien exact de la source.
Les tests de séquence vérifient que AR précède la restauration sans y être rejoué.
`test_output_layout.py` vérifie aussi la numérotation photométrie 03, AR 04,
restauration 05, AutoGHS 06 et couleur 07, ainsi que la lecture explicite des
anciens rapports numérotés. Les reprises et comparaisons couvrent les chemins
compatibles sans renommer les produits existants.
L'audit de flux d'AR est vérifié sur une structure étendue synthétique atténuée
ou amplifiée et sur un champ sans mesure. `test_final_annotation.py` vérifie
que la synthèse d'AR (rondeur initiale, largeur relative et décision) apparaît
dans le PNG, même si les mesures détaillées
et seuils sont présents dans le rapport. `test_aberration.py` vérifie la
protection du fond par défaut et sa désactivation explicite.

Les tests de comparaison stellaire couvrent la règle « 1 000 paires OU 70 % »,
avec les bornes 999/1 000 et 69,9/70 % sur de vrais catalogues synthétiques.
Les tests AR vérifient que le seuil relatif local ne réintroduit pas un refus
quand la cohorte globale atteint 1 000 paires.

`test_starless_zones.py` vérifie les couleurs par identité des masques avec
les mesures GHS, les quantiles globaux et leurs recouvrements, le pas régulier,
la disponibilité, le PNG et la conservation des sources. Le test autonome
de branche starless vérifie également la publication de la carte et de son audit.

`test_stellar_visibility_selection.py` vérifie que l'objectif de visibilité
modifie réellement le rendu global : les cibles 20 % et 70 % produisent des
pixels et taux différents. Les tests couvrent la limite de profils et la jupe
du groupe brillant, un objectif inaccessible, les valeurs invalides, les deux
triplets identiques sous des voisinages différents et le rejeu exact de la recette.
