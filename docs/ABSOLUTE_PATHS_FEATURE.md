# Gestion des Chemins Absolus

## Description

Le système de configuration a été amélioré pour automatiquement convertir tous les chemins de répertoires en chemins absolus lors de leur sauvegarde. Cette fonctionnalité garantit une configuration robuste et portable.

## Fonctionnalités

### Conversion Automatique

Tous les paramètres de chemins de répertoires sont automatiquement convertis en chemins absolus :

- `dark_library_path` : Chemin vers la bibliothèque de master darks
- `bias_library_path` : Chemin vers la bibliothèque de master bias  
- `work_dir` : Répertoire de travail temporaire
- `input_dirs` : Liste des répertoires d'entrée contenant les fichiers dark

### Valeurs par Défaut

Les valeurs par défaut définies dans `DEFAULTS` sont également converties en chemins absolus :

```python
DEFAULTS = {
    "dark_library_path": "/home/user/darkLib",      # Au lieu de "~/darkLib"
    "bias_library_path": "/home/user/biasLib",      # Au lieu de "~/biasLib"
    "work_dir": "/home/user/tmp/sirilWorkDir",      # Au lieu de "~/tmp/sirilWorkDir"
    # ...
}
```

## Avantages

### 1. Robustesse
- Fonctionne indépendamment du répertoire de travail actuel
- Évite les erreurs dues aux chemins relatifs

### 2. Portabilité de Configuration
- La configuration peut être utilisée depuis n'importe quel répertoire
- Pas de dépendance sur le contexte d'exécution

### 3. Clarté
- Les chemins sauvegardés sont explicites et sans ambiguïté
- Facilite le débogage et la maintenance

## Exemples d'Utilisation

### Sauvegarde avec Chemins Relatifs

```bash
# Les chemins relatifs sont automatiquement convertis
python3 bin/darkLibUpdate.py -i ./darks ../more_darks -w ./work -d ./library -S
```

### Configuration Résultante

```json
{
  "input_dirs": [
    "/absolute/path/to/darks",
    "/absolute/path/to/more_darks"
  ],
  "work_dir": "/absolute/path/to/work",
  "dark_library_path": "/absolute/path/to/library"
}
```

### Utilisation Ultérieure

```bash
# Fonctionne depuis n'importe quel répertoire
cd /any/directory
python3 /path/to/darkLibUpdate.py  # Utilise la config sauvegardée
```

## Implémentation Technique

### Fonction `set_from_args()`

La conversion est effectuée dans `lib/config.py` :

```python
def set_from_args(self, args):
    """Met à jour la configuration à partir des arguments.
    Convertit automatiquement tous les chemins en absolus."""
    
    updates = {
        "work_dir": os.path.abspath(args.work_dir),  # Conversion automatique
        # ...
    }
    
    if hasattr(args, 'input_dirs') and args.input_dirs is not None:
        # Conversion de chaque répertoire d'entrée
        updates["input_dirs"] = [os.path.abspath(d) for d in args.input_dirs]
    
    if hasattr(args, 'dark_library_path'):
        updates["dark_library_path"] = os.path.abspath(args.dark_library_path)
```

### Gestion des Cas Spéciaux

- **Chemins avec `~`** : Expansion automatique via `os.path.expanduser()`
- **Chemins relatifs** : Conversion basée sur le répertoire de travail actuel
- **Chemins déjà absolus** : Préservés tels quels

## Compatibilité

### Rétrocompatibilité
- Les configurations existantes sont automatiquement converties lors du premier usage
- Aucune action manuelle requise

### Migration
Les configurations avec chemins relatifs seront automatiquement mises à jour lors de la prochaine sauvegarde.

## Tests

### Test de Conversion

```python
# Test unitaire disponible dans le code
args.input_dirs = ["./relative", "../parent", "/absolute"]
config.set_from_args(args)

# Résultat : tous les chemins sont absolus
assert all(os.path.isabs(path) for path in config.get('input_dirs'))
```

### Validation

La conversion peut être vérifiée en inspectant le fichier `~/.siril_darklib_config.json` après sauvegarde.