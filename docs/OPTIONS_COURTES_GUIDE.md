# Guide des Options Courtes - darkLibUpdate.py

## Vue d'ensemble

Toutes les options du script ont maintenant des équivalents courts pour une utilisation plus rapide en ligne de commande.

## Liste complète des options courtes

| Option courte | Option longue | Description | Dest |
|---------------|---------------|-------------|------|
| `-h` | `--help` | Affiche l'aide | - |
| `-i` | `--input-dirs` | Répertoires d'entrée des darks | `input_dirs` |
| `-d` | `--dark-library-path` | Chemin de la bibliothèque de darks | `dark_library_path` |
| `-w` | `--work-dir` | Répertoire de travail temporaire | `work_dir` |
| `-s` | `--siril-path` | Chemin vers Siril | `siril_path` |
| `-m` | `--siril-mode` | Mode d'exécution de Siril | `siril_mode` |
| `-S` | `--save-config` | Sauvegarde la configuration | `save_config` |
| `-D` | `--dummy` | Mode test (sans exécuter Siril) | `dummy` |
| `-l` | `--log-level` | Niveau de journalisation | `log_level` |
| `-L` | `--list-darks` | Liste les master darks | `list_darks` |
| `-a` | `--max-age` | Âge maximum des darks (jours) | `max_age` |
| `-c` | `--cfa` | Images couleur (CFA) | `cfa` |
| `-o` | `--output-norm` | Méthode de normalisation | `output_norm` |
| `-r` | `--rejection-method` | Méthode de rejet | `rejection_method` |
| `-t` | `--temperature-precision` | Précision température (°C) | `temperature_precision` |
| `-f` | `--force-recalc` | Force le recalcul | `force_recalc` |
| `-v` | `--validate-darks` | Active la validation | `validate_darks` |
| `-R` | `--report` | Génère un rapport de traitement | `report` |

## Options sans équivalent court

| Option longue | Description | Dest |
|---------------|-------------|------|
| `--log-skipped` | Log les fichiers ignorés | `log_skipped` |
| `--rejection-param1` | Premier paramètre de rejet | `rejection_param1` |
| `--rejection-param2` | Second paramètre de rejet | `rejection_param2` |
| `--stack-method` | Méthode d'empilement | `stack_method` |
| `--no-validate-darks` | Désactive la validation | `validate_darks` |
| `--no-report` | Désactive le rapport | `report` |

## Exemples d'utilisation avec options courtes

### Utilisation basique
```bash
# Traitement simple avec validation
python bin/darkLibUpdate.py -i /path/darks -v -R

# Mode test avec sauvegarde de config
python bin/darkLibUpdate.py -i /path/darks -v -D -S

# Lister les darks existants
python bin/darkLibUpdate.py -L
```

### Configuration avancée
```bash
# Configuration complète avec options courtes
python bin/darkLibUpdate.py \
    -i /observatory/darks \
    -d ~/myDarkLib \
    -w ~/tmp/work \
    -m flatpak \
    -v -R \
    -c \
    -t 0.1 \
    -f \
    -S
```

### Workflow de débogage
```bash
# Mode verbose avec test
python bin/darkLibUpdate.py -i /path/darks -l DEBUG -D -v -R

# Force le recalcul avec validation
python bin/darkLibUpdate.py -i /path/darks -f -v -l INFO
```

## Combinaisons recommandées

### Session de validation
```bash
# Analyser et valider sans traiter
python bin/darkLibUpdate.py -i /new/darks -v -R -D

# Traiter après validation
python bin/darkLibUpdate.py -i /new/darks -v
```

### Configuration initiale
```bash
# Setup complet en une commande
python bin/darkLibUpdate.py -i /darks -d ~/darkLib -v -R -t 0.1 -S
```

### Maintenance
```bash
# Recalcul forcé avec logs
python bin/darkLibUpdate.py -f -l INFO

# Liste des darks avec leurs caractéristiques
python bin/darkLibUpdate.py -L
```

## Variables dest explicites

Toutes les options ont maintenant des variables `dest` explicites qui correspondent au nom de l'attribut dans `args` :

- `args.input_dirs` ← `-i/--input-dirs`
- `args.validate_darks` ← `-v/--validate-darks`
- `args.validation_report` ← `-R/--validation-report`
- etc.

Cette explicité améliore la lisibilité du code et évite les erreurs de nommage automatique d'argparse.