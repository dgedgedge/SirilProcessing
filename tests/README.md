# Tests et non-régression

[Documentation](../docs/README.md)

Tous les tests du projet se trouvent dans `tests/`. Le fichier `pytest.ini`
définit ce répertoire comme point de découverte par défaut et rend les imports
du projet accessibles aux tests.

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
| Qualité astrophotographique, chaînes et GraXpert | `test_restoration_quality.py`, `test_restoration_chains.py`, `test_restoration_summary.py`, `test_graxpert.py`, `test_restoration.py` |
| Mosaïque : noms, entrées, préparation et script généré | `test_mosaic.py`, `test_mosaic_validation.py`, `test_integrated_script.py`, `test_simplified_mosaic.py` |
| Configuration, lancement et journaux Siril | `test_siril_validation.py` |
| Génération et navigation HTML | `test_docs_html.py` |
| Synthèse de non-régression | `test_regression_summary.py` |

## Isolation et automatisation

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
le dossier des poids ; sinon le dossier ignoré `tmp/models/cosmicclarity` est utilisé.

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
Une inférence CPU réelle conditionnelle sur un extrait de M33 est
exécutée lorsque ONNX Runtime, les poids et l’image exemple sont disponibles.
Les nouveaux contrôles de flux sont diagnostiques par défaut : ces tests ne
remplacent pas une calibration sur plusieurs références astrophotographiques.
Voir [le contrat et les limites](../docs/scripts/postProcess/quality.md).

`test_docs_html.py` vérifie la navigation, l’exclusion des commentaires de code
du sommaire, les diagrammes et la copie des fichiers
Python, shell et JSON liés dans l’export HTML. Les poids binaires et les cibles
hors dépôt restent exclus ; la génération n’exécute pas les sources copiées.
`test_documentation_site.py` couvre les ancres distinctes des chapitres, leurs
liens internes, les ressources embarquées du monofichier et la protection des
dossiers. Une construction MkDocs complète rend les diagrammes dans Chromium
et génère le PDF avant de publier les téléchargements sur le site.
