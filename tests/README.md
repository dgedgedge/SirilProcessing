# Répertoire des tests pour SirilProcessing

Ce répertoire contient tous les tests unitaires et d'intégration pour le projet SirilProcessing.

## Structure des tests

```
tests/
├── README.md                 # Ce fichier
├── conftest.py              # Configuration pytest et fixtures communes
├── test_config.py           # Tests pour lib/config.py
├── test_fits_info.py        # Tests pour lib/fits_info.py
├── test_darkprocess.py      # Tests pour lib/darkprocess.py
├── test_logging_config.py   # Tests pour lib/logging_config.py
├── test_siril_utils.py      # Tests pour lib/siril_utils.py
├── test_integration.py      # Tests d'intégration complets
├── fixtures/                # Données de test FITS simulées
│   ├── dark_valid.fit
│   ├── dark_invalid.fit
│   └── bias_sample.fit
└── mock_data/               # Données mockées pour les tests
    └── sample_configs.json
```

## Exécution des tests

```bash
# Installer pytest si nécessaire
pip install pytest pytest-cov

# Exécuter tous les tests
pytest

# Exécuter avec couverture
pytest --cov=lib --cov-report=html

# Exécuter des tests spécifiques
pytest tests/test_fits_info.py

# Exécuter en mode verbose
pytest -v
```

## Stratégies de test

### Tests unitaires
- **test_config.py** : Configuration, sauvegarde/chargement JSON
- **test_fits_info.py** : Lecture FITS, validation darks, statistiques
- **test_darkprocess.py** : Groupement, traitement, création master darks
- **test_logging_config.py** : Configuration centralisée des logs

### Tests d'intégration
- **test_integration.py** : Workflows complets end-to-end
- Simulation de traitement complet avec Siril
- Tests avec fichiers FITS réels

### Fixtures et mocks
- **conftest.py** : Fixtures communes (fichiers FITS simulés, configurations)
- **fixtures/** : Données FITS de test générées programmatiquement
- **mock_data/** : Configurations et métadonnées de test

## Conventions

1. **Nommage** : `test_<fonction>_<cas>()` (ex: `test_group_key_valid_inputs()`)
2. **Structure** : Arrange/Act/Assert pattern
3. **Couverture** : Viser >80% pour chaque module
4. **Isolation** : Chaque test doit être indépendant
5. **Performance** : Tests rapides (<1s par test unitaire)

## Cas de test prioritaires

### FitsInfo
- ✅ Lecture en-têtes FITS valides/invalides
- ✅ Validation statistique des darks
- ✅ Calcul des clés de groupement
- ✅ Détection lumière parasite (capot ouvert)

### DarkProcess  
- ✅ Groupement par température/exposition/gain
- ✅ Filtrage par âge des fichiers
- ✅ Validation automatique des darks
- ✅ Création master darks avec Siril

### Config
- ✅ Chargement/sauvegarde configuration JSON
- ✅ Valeurs par défaut et validation
- ✅ Gestion des chemins absolus/relatifs

## Intégration continue

Les tests sont exécutés automatiquement pour garantir la qualité du code :
- Tests unitaires à chaque commit
- Tests d'intégration sur les PR
- Couverture de code surveillée
- Performance benchmarkée