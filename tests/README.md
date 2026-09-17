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

Le workflow GitHub **Documentation HTML** exécute les tests du générateur
avec `unittest`. La suite de non-régression complète s’exécute avec les
commandes pytest ci-dessus ; aucun workflow GitHub dédié à cette suite
n’est actuellement configuré.
