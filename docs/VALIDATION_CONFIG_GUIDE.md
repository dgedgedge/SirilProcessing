# Guide de Configuration des Options de Validation

## Vue d'ensemble

Les options de validation peuvent maintenant être sauvegardées et restaurées automatiquement grâce au système de configuration `--save-config`.

## Options de validation disponibles

- `--validate-darks` : Active la validation des fichiers darks
- `--no-validate-darks` : Désactive la validation des fichiers darks
- `--validation-report` : Génère un rapport détaillé de validation
- `--no-validation-report` : Désactive la génération du rapport de validation
- `--input-dirs DIR1 DIR2 ...` : Spécifie les répertoires d'entrée

## Utilisation

### 1. Configurer et sauvegarder les options de validation

```bash
# Activer la validation et le rapport, spécifier des répertoires
python bin/darkLibUpdate.py --validate-darks --validation-report --input-dirs /path/to/darks1 /path/to/darks2 --save-config

# Désactiver la validation
python bin/darkLibUpdate.py --no-validate-darks --no-validation-report --save-config
```

### 2. Utiliser la configuration sauvegardée

Une fois sauvegardées, les options sont automatiquement appliquées :

```bash
# Lance le script avec les options sauvegardées
python bin/darkLibUpdate.py

# Ou override temporairement une option sans sauvegarder
python bin/darkLibUpdate.py --no-validate-darks
```

### 3. Vérifier la configuration actuelle

```bash
# Voir les valeurs par défaut chargées
python bin/darkLibUpdate.py --help
```

## Exemples d'utilisation

### Workflow type pour validation automatique

```bash
# Configuration initiale (une seule fois)
python bin/darkLibUpdate.py \
    --validate-darks \
    --validation-report \
    --input-dirs /observatory/darks/session1 /observatory/darks/session2 \
    --save-config

# Utilisation quotidienne (plus besoin de spécifier les options)
python bin/darkLibUpdate.py

# Ajout d'un nouveau répertoire temporairement
python bin/darkLibUpdate.py --input-dirs /observatory/darks/session3
```

### Workflow pour traitement sans validation

```bash
# Désactiver la validation pour des traitements rapides
python bin/darkLibUpdate.py --no-validate-darks --no-validation-report --save-config

# Utilisation sans validation
python bin/darkLibUpdate.py --input-dirs /path/to/darks
```

## Fichier de configuration

Les options sont sauvegardées dans `~/.siril_darklib_config.json` :

```json
{
  "validate_darks": true,
  "validation_report": true,
  "input_dirs": ["/path/to/darks1", "/path/to/darks2"],
  "...": "autres options..."
}
```

## Notes importantes

- Les options `--input-dirs` sauvegardées servent de défaut, mais peuvent être overridées
- Les options `--no-*` permettent de désactiver explicitement les options même si elles sont sauvegardées comme `true`
- La configuration persiste entre les sessions
- Utiliser `--save-config` pour mettre à jour la configuration