# Guide d'utilisation de lightProcessor.py

## Description

Le script `lightProcessor.py` permet de traiter automatiquement des images light en effectuant :

1. **Détection automatique** des images light dans un répertoire de session
2. **Groupement** des images par caractéristiques communes (température, exposition, gain, caméra, binning)
3. **Recherche automatique** du master dark correspondant dans la librairie
4. **Prétraitement** avec soustraction du dark
5. **Stacking** automatique des images prétraitées

## Structure des répertoires attendue

```
session_directory/
├── light/                  # OBLIGATOIRE - Contient les images light
│   ├── image_001.fit
│   ├── image_002.fit
│   └── ...
└── flat/                   # OPTIONNEL - Sera ignoré pour l'instant
    ├── flat_001.fit
    └── ...
```

## Utilisation basique

```bash
# Traitement d'une session avec configuration par défaut
python3 bin/lightProcessor.py /path/to/session_M31

# Avec spécification de la librairie de darks
python3 bin/lightProcessor.py /path/to/session_M31 --dark-lib /path/to/dark_library
```

## Options principales

### Répertoires
- `--dark-lib` : Chemin vers la librairie de master darks
- `--output` : Répertoire de sortie (défaut: `session_dir/processed`)
- `--work-dir` : Répertoire de travail temporaire (défaut: `session_dir/work`)

### Traitement
- `--temp-precision` : Précision de correspondance des températures en °C (défaut: 0.2)
- `--force` : Force le retraitement même si les fichiers de sortie existent
- `--dry-run` : Simule le traitement sans l'exécuter

### Siril
- `--siril-path` : Chemin vers l'exécutable Siril (défaut: siril)
- `--siril-mode` : Mode d'exécution (`native`, `flatpak`, `appimage`)

### Stacking
- `--stack-method` : Méthode de stacking (`average`, `median`, `sum`)
- `--rejection` : Méthode de rejet (`none`, `sigma`, `linear`, `winsor`, `percentile`)
- `--rejection-low` : Seuil bas de rejet (défaut: 3.0)
- `--rejection-high` : Seuil haut de rejet (défaut: 3.0)

## Exemples d'utilisation

### Exemple 1 : Traitement simple
```bash
python3 bin/lightProcessor.py /home/user/astrophoto/session_M31
```

### Exemple 2 : Avec personnalisation
```bash
python3 bin/lightProcessor.py /home/user/astrophoto/session_M31 \
    --dark-lib /home/user/dark_library \
    --output /home/user/results \
    --stack-method median \
    --rejection sigma \
    --temp-precision 0.5
```

### Exemple 3 : Mode dry-run pour tester
```bash
python3 bin/lightProcessor.py /home/user/astrophoto/session_M31 \
    --dry-run \
    --log-level DEBUG
```

## Correspondance des master darks

Le script recherche automatiquement le master dark correspondant selon ces critères :

- **Température** : À ±0.2°C près (configurable avec `--temp-precision`)
- **Temps d'exposition** : Exact
- **Gain** : Exact  
- **Caméra** : Exact
- **Binning** : Exact

## Structure de sortie

```
session_directory/
├── processed/                         # Répertoire de sortie
│   ├── Group1_T-10.0_E300_G100_B1x1/
│   │   └── light_Group1_T-10.0_E300_G100_B1x1_stacked.fit
│   └── Group2_T-10.2_E120_G100_B1x1/
│       └── light_Group2_T-10.2_E120_G100_B1x1_stacked.fit
└── work/                              # Répertoire de travail (temporaire)
    └── ...
```

## Gestion des erreurs

### Erreurs communes

1. **"Le répertoire 'light' n'existe pas"**
   - Vérifiez que votre session contient un sous-répertoire `light/`

2. **"Aucun master dark correspondant trouvé"**
   - Vérifiez que votre librairie de darks contient des masters avec les bonnes caractéristiques
   - Ajustez `--temp-precision` si nécessaire

3. **"Aucun fichier light trouvé"**
   - Vérifiez que le répertoire `light/` contient des fichiers `.fit` ou `.fits`

### Mode debug

Pour obtenir plus d'informations en cas de problème :
```bash
python3 bin/lightProcessor.py /path/to/session --log-level DEBUG
```

## Configuration

Le script utilise le fichier de configuration `~/.siril_darklib_config.json` pour les paramètres par défaut (notamment le chemin vers la librairie de darks).

Vous pouvez utiliser un fichier de configuration personnalisé avec :
```bash
python3 bin/lightProcessor.py /path/to/session --config /path/to/custom_config.json
```

## Workflow complet

1. **Préparer la session** : Organiser les images light dans `session_dir/light/`
2. **Vérifier la librairie** : S'assurer que les master darks correspondants existent
3. **Tester** : Utiliser `--dry-run` pour vérifier la détection
4. **Traiter** : Lancer le traitement complet
5. **Vérifier** : Contrôler les résultats dans `session_dir/processed/`

## Intégration avec darkLibUpdate.py

Pour un workflow complet :

1. **Créer les master darks** :
   ```bash
   python3 bin/darkLibUpdate.py /path/to/darks --report
   ```

2. **Traiter les lights** :
   ```bash
   python3 bin/lightProcessor.py /path/to/session
   ```

Cette approche garantit que vous avez les master darks nécessaires avant de traiter vos images light.