# Extraction de Gradient — Analyse du fond de ciel

[Documentation](../../../README.md) › [lightProcess](../../README.md) › [Traitements](README.md)

## Vue d'ensemble

L'extraction automatique du gradient est une **étape optionnelle** du pipeline lightProcess qui permet d'analyser les gradients d'illumination présents dans les images astronomiques calibrées. Cette fonctionnalité utilise une régression polynomiale à différents ordres pour modéliser et quantifier les variations de fond de ciel.

## Activation

L'extraction de gradient est désactivée par défaut. Pour l'activer, utiliser l'option `--extract-gradient` :

```bash
python lightProcess.py /path/to/session_M31 --extract-gradient
```

## Fonctionnement

### Algorithme

Pour chaque image calibrée (sortie du prétraitement), le système :

1. **Charge les données** depuis le fichier FITS calibré
2. **Échantillonne l'image** pour accélérer le calcul (30% des pixels par défaut, max 10000 points)
3. **Ajuste des surfaces polynomiales** d'ordres croissants (par défaut: 1 à 3)
4. **Calcule des statistiques** pour chaque ordre :
   - Coefficients du polynôme
   - Erreur quadratique moyenne (MSE) et racine carrée (RMSE)
   - Coefficient de détermination R²
   - Nombre de coefficients utilisés
5. **Réévalue et recommande** le meilleur ordre selon plusieurs critères
6. **Génère une image des points de mesure** pour visualisation

### Critères de recommandation

Trois critères sont utilisés pour déterminer l'ordre polynomial optimal :

| Critère | Description | Priorité |
|---------|-------------|----------|
| `best_r_squared` | Ordre avec le meilleur R² absolu | Qualités métriques |
| `best_compromise` | Meilleur rapport R² / nombre de coefficients | **Défaut** |
| `simplest_good_fit` | Ordre minimal avec R² ≥ 0.95 | Simplicité |

Le critère `best_compromise` est utilisé par défaut car il équilibre qualité d'ajustement et complexité du modèle.

### Points de mesure

L'option `--gradient-points` (défaut: 100) permet de définir le nombre de points de mesure aléatoires à afficher sur l'image générée. Ces points sont matérialisés par des croix rouges sur une version normalisée de l'image calibrée.

## Options de configuration

| Option | Type | Défaut | Description |
|--------|------|--------|-------------|
| `--extract-gradient` | booléen | False | Active l'extraction de gradient |
| `--gradient-min-order` | entier | 1 | Ordre polynomial minimum (1-3) |
| `--gradient-max-order` | entier | 3 | Ordre polynomial maximum (1-5) |
| `--gradient-points` | entier | 100 | Nombre de points de mesure à afficher |
| `--gradient-output` | chemin | None | Répertoire de sortie spécifique (défaut: `<work_dir>/<target>/gradient_analysis`) |

## Sorties générées

### Structure des répertoires

```
<output_dir>/
└── <target>/
    └── sessions/
        └── <session>/
            └── gradient_analysis/
                ├── <session>_gradient_report.json  # Rapport complet
                ├── pp_<sequence>_0001_measurement_points.png  # Image point 1
                ├── pp_<sequence>_0002_measurement_points.png  # Image point 2
                └── ...
```

### Rapport JSON

Le fichier `*_gradient_report.json` contient pour chaque image calibrée :

```json
{
  "image_path": "/path/to/calibrated/file.fit",
  "session_dir": "/path/to/session",
  "calibrated_file": "/path/to/pp_light_group1_0001.fit",
  "height": 4000,
  "width": 3000,
  "final_recommendation": 2,
  "gradient_info": {
    "mean_gradient": 0.0015,
    "max_gradient": 0.0042,
    "std_gradient": 0.0008,
    "recommended_order": 2
  },
  "results": {
    "order_1": {
      "order": 1,
      "coefficients": [0.5, -0.001, 1000.0],
      "rmse": 15.2,
      "mse": 231.04,
      "r_squared": 0.8523,
      "n_samples": 10000,
      "n_coefficients": 3,
      "success": true
    },
    "order_2": {
      "order": 2,
      "coefficients": [0.5, -0.001, 0.0001, 0.0005, -0.0002, 1000.0],
      "rmse": 8.1,
      "mse": 65.61,
      "r_squared": 0.9642,
      "n_samples": 10000,
      "n_coefficients": 6,
      "success": true
    }
  },
  "recommendations": {
    "best_r_squared": 3,
    "best_compromise": 2,
    "simplest_good_fit": 2
  },
  "measurement_points_count": 100,
  "measurement_points": [
    {"x": 1500, "y": 2000},
    {"x": 500, "y": 500},
    ...
  ],
  "measurement_image_path": "/path/to/image_measurement_points.png"
}
```

## Interprétation des résultats

### Ordre polynomial recommandé

| Ordre | Interprétation | Action suggérée |
|-------|---------------|-----------------|
| 1 | Gradient linéaire simple | Vérifier l'équilibrage du fond de ciel |
| 2 | Gradient quadratique | Correction de vignettage nécessaire |
| 3 | Gradient complexe | Correction multi-paramètres requise |
| ≥4 | Structure très complexe | Analyse manuelle recommandée |

### Statistiques R²

| R² | Qualité de l'ajustement | Signification |
|----|----------------------|---------------|
| ≥ 0.95 | Excellente | Le gradient explique presque toute la variation |
| 0.80 - 0.95 | Bonne | Gradient significatif présent |
| 0.50 - 0.80 | Moyenne | Autres sources de variation présentes |
| < 0.50 | Faible | Peu ou pas de gradient détectable |

### Magnitude du gradient

La valeur `gradient_magnitude` (dans `gradient_info`) représente l'intensité moyenne du gradient en ADU/pixel. Des valeurs élevées indiquent une forte variation d'illumination.

## Cas d'usage

### 1. Détection de vignettage

```bash
python lightProcess.py /path/to/session --extract-gradient \
  --gradient-max-order 2 \
  --gradient-points 200
```

Le vignettage apparaît généralement comme un gradient radial (ordre 2) avec une symétrie circulaire.

### 2. Analyse de qualité de calibration

```bash
python lightProcess.py /path/to/session --extract-gradient \
  --gradient-min-order 1 \
  --gradient-max-order 3 \
  --gradient-output /tmp/gradient_results
```

Un R² élevé (>0.95) avec un ordre 1 peut indiquer un résidu de gradient après calibration (dark incomplet).

### 3. Validation de flat

Si les flats sont correctement appliqués, le gradient résiduel devrait être minimal (R² < 0.5 pour tous les ordres).

## Limites et précautions

- **Échantillonnage** : Par défaut, 30% des pixels sont utilisés pour accélérer le calcul. Pour une analyse plus précise, réduire `sample_fraction` dans le code.
- **Mémoire** : L'analyse peut être mémoire-intensive pour les grandes images. Le paramètre `max_samples` (10000 par défaut) limite cet impact.
- **Interprétation** : Un R² élevé n'indique pas toujours un problème. Certains gradients sont normaux (vignettage optique, variation naturelle du fond de ciel).
- **Compatibilité** : Cette fonctionnalité nécessite les bibliothèques `numpy`, `astropy`, et `PIL` (Pillow).

## Exemple de sortie console

```
INFO: Extraction du gradient pour la sous-session: /path/to/session_M31/session_01
INFO: Extraction de gradient sur 50 fichiers calibrés
INFO: Gradient extrait pour pp_light_group1_0001.fit: ordre recommandé=2, R² max=0.9642
INFO: Gradient extrait pour pp_light_group1_0002.fit: ordre recommandé=2, R² max=0.9589
...
INFO: Rapport de gradient sauvegardé: /path/to/work/M31/session_01/gradient_analysis/session_01_gradient_report.json
INFO: Résumé gradient pour session_01: fichiers=50, ordre recommandé moyen=2.00, R² moyen=0.9423
```

## Références

- [Traitement principal lightProcess](../README.md)
- [Pipeline complet](README.md)
- [Calibration et stacking](CALIBRATION.md)
