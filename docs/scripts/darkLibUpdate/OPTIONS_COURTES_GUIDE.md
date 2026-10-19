# Guide des Options Courtes - darkLibUpdate.py

[Documentation](../../README.md) › [darkLibUpdate](README.md)

<a id="vue-d-ensemble"></a>
<a id="vue-densemble"></a>

## 1. Vue d'ensemble

Toutes les options du script ont maintenant des équivalents courts pour une utilisation plus rapide en ligne de commande.

<a id="liste-compl-te-des-options-courtes"></a>
<a id="liste-complète-des-options-courtes"></a>

## 2. Liste complète des options courtes

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

<a id="options-sans-quivalent-court"></a>
<a id="options-sans-équivalent-court"></a>

## 3. Options sans équivalent court

| Option longue | Description | Dest |
|---------------|-------------|------|
| `--log-skipped` | Log les fichiers ignorés | `log_skipped` |
| `--rejection-param1` | Premier paramètre de rejet | `rejection_param1` |
| `--rejection-param2` | Second paramètre de rejet | `rejection_param2` |
| `--stack-method` | Méthode d'empilement | `stack_method` |
| `--no-validate-darks` | Désactive la validation | `validate_darks` |
| `--no-report` | Désactive le rapport | `report` |

<a id="exemples-d-utilisation-avec-options-courtes"></a>
<a id="exemples-dutilisation-avec-options-courtes"></a>

## 4. Exemples d'utilisation avec options courtes

<a id="utilisation-basique"></a>

### 4.1. Utilisation basique
```bash
# Traitement simple avec validation
python bin/darkLibUpdate.py -i /path/darks -v -R

# Mode test avec sauvegarde de config
python bin/darkLibUpdate.py -i /path/darks -v -D -S

# Lister les darks existants
python bin/darkLibUpdate.py -L
```

<a id="configuration-avanc-e"></a>
<a id="configuration-avancée"></a>

### 4.2. Configuration avancée
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

<a id="workflow-de-d-bogage"></a>
<a id="workflow-de-débogage"></a>

### 4.3. Workflow de débogage
```bash
# Mode verbose avec test
python bin/darkLibUpdate.py -i /path/darks -l DEBUG -D -v -R

# Force le recalcul avec validation
python bin/darkLibUpdate.py -i /path/darks -f -v -l INFO
```

<a id="combinaisons-recommand-es"></a>
<a id="combinaisons-recommandées"></a>

## 5. Combinaisons recommandées

<a id="session-de-validation"></a>

### 5.1. Session de validation
```bash
# Analyser et valider sans traiter
python bin/darkLibUpdate.py -i /new/darks -v -R -D

# Traiter après validation
python bin/darkLibUpdate.py -i /new/darks -v
```

<a id="configuration-initiale"></a>

### 5.2. Configuration initiale
```bash
# Setup complet en une commande
python bin/darkLibUpdate.py -i /darks -d ~/darkLib -v -R -t 0.1 -S
```

<a id="maintenance"></a>

### 5.3. Maintenance
```bash
# Recalcul forcé avec logs
python bin/darkLibUpdate.py -f -l INFO

# Liste des darks avec leurs caractéristiques
python bin/darkLibUpdate.py -L
```

<a id="variables-dest-explicites"></a>

## 6. Variables dest explicites

Toutes les options ont maintenant des variables `dest` explicites qui correspondent au nom de l'attribut dans `args` :

- `args.input_dirs` ← `-i/--input-dirs`
- `args.validate_darks` ← `-v/--validate-darks`
- `args.validation_report` ← `-R/--validation-report`
- etc.

Cette explicité améliore la lisibilité du code et évite les erreurs de nommage automatique d'argparse.
