# Guide Complet - darkLibUpdate.py

## Vue d'ensemble

`darkLibUpdate.py` est un outil complet pour créer et maintenir une bibliothèque de master darks pour Siril. Il automatise le regroupement, la validation, l'empilement et la gestion des fichiers dark frames avec des fonctionnalités avancées de validation et de rapport.

## Utilisation de base

```bash
python bin/darkLibUpdate.py [OPTIONS]
```

## Options principales

### Répertoires et fichiers

#### `-i, --input-dirs` (Répertoires d'entrée) ⭐⭐⭐
```bash
-i /path/darks1 /path/darks2 /path/darks3
```
- **Description** : Liste des répertoires contenant les fichiers dark à traiter
- **Défaut** : Configuration sauvegardée ou `['../sirilHome_FichiersOri/Dark/']`
- **Usage** : Spécifie où chercher les fichiers FITS dark à traiter

#### `-d, --dark-library-path` (Bibliothèque de sortie)
```bash
-d /path/to/library
```
- **Description** : Répertoire où sont stockés les master darks
- **Défaut** : `/home/denis/sirilHome_FichiersOri/MasterDark`
- **Usage** : Destination des master darks créés

#### `-w, --work-dir` (Répertoire de travail)
```bash
-w /tmp/siril_work
```
- **Description** : Répertoire de travail temporaire
- **Défaut** : `/home/denis/tmp/sirilWorkDir`
- **Usage** : Espace temporaire pour les opérations Siril

### Configuration Siril

#### `-s, --siril-path` (Exécutable Siril)
```bash
-s /path/to/siril
```
- **Description** : Chemin vers l'exécutable Siril
- **Défaut** : `siril`
- **Usage** : Localisation de Siril si non dans PATH

#### `-m, --siril-mode` (Mode d'exécution)
```bash
-m flatpak    # Par défaut
-m native     # Installation native
-m appimage   # Version AppImage
```
- **Description** : Mode d'exécution de Siril
- **Défaut** : `flatpak`
- **Usage** : Adapte la commande selon l'installation

### Validation et qualité ⭐⭐⭐

#### `-v, --validate-darks` (Validation intelligente)
```bash
-v                    # Active la validation
--no-validate-darks   # Désactive la validation
```
- **Description** : Valide les fichiers darks pour détecter les capots ouverts
- **Défaut** : `True` (activé par configuration)
- **Fonctionnement** : 
  - Analyse statistique (médiane, MAD, percentiles)
  - Détection pixels chauds
  - Rejet automatique des darks suspects
  - **Optimisé** : Validation seulement si master dark à mettre à jour

#### `-R, --report` (Rapport de traitement)
```bash
-R              # Génère un rapport détaillé
--no-report     # Pas de rapport
```
- **Description** : Génère un rapport détaillé du traitement effectué
- **Défaut** : `True` (activé par configuration)
- **Contenu** :
  - Master darks mis à jour
  - Fichiers rejetés avec raisons détaillées
  - Statistiques globales
  - État de la bibliothèque complète

### Paramètres d'empilement

#### `-r, --rejection-method` (Méthode de rejet)
```bash
-r winsorizedsigma    # Par défaut - Robuste
-r sigma              # Sigma classique
-r minmax             # Min/Max
-r percentile         # Percentiles
-r none               # Aucun rejet
```
- **Description** : Méthode de rejet pour Siril
- **Défaut** : `winsorizedsigma`
- **Usage** : Contrôle la robustesse de l'empilement

#### `--rejection-param1, --rejection-param2` (Paramètres de rejet)
```bash
--rejection-param1 3.0    # Premier seuil
--rejection-param2 3.0    # Second seuil
```
- **Description** : Paramètres numériques pour la méthode de rejet
- **Défaut** : `3.0` pour les deux
- **Usage** : Ajuste la sensibilité du rejet

#### `--stack-method` (Méthode d'empilement)
```bash
--stack-method average    # Par défaut - Moyenne avec rejet
--stack-method median     # Médiane (plus robuste)
```
- **Description** : Algorithme d'empilement
- **Défaut** : `average`
- **Usage** : `median` pour données très bruitées

#### `-o, --output-norm` (Normalisation)
```bash
-o noscale      # Par défaut - Aucune normalisation
-o addscale     # Normalisation additive
-o rejection    # Normalisation par rejet
```
- **Description** : Méthode de normalisation pour Siril
- **Défaut** : `noscale`
- **Usage** : Généralement `noscale` pour les darks

### Paramètres de regroupement

#### `-t, --temperature-precision` (Précision température) ⭐⭐
```bash
-t 0.1    # Précision de 0.1°C
-t 0.5    # Par défaut
-t 1.0    # Moins strict
```
- **Description** : Précision d'arrondi pour la température
- **Défaut** : `0.5°C`
- **Usage** : Plus petit = groupes plus fins, plus grand = groupes plus larges

#### `-a, --max-age` (Âge maximum)
```bash
-a 182    # Par défaut - 6 mois
-a 30     # 1 mois seulement
-a 365    # 1 an
```
- **Description** : Écart maximum en jours entre darks d'un groupe
- **Défaut** : `182` jours (6 mois)
- **Usage** : Filtre les darks trop anciens

#### `-c, --cfa` (Images couleur)
```bash
-c    # Images couleur (CFA)
```
- **Description** : Indique des images couleur (Color Filter Array)
- **Défaut** : `False` (monochrome)
- **Usage** : Nécessaire pour appareils couleur non défiltés

### Modes d'opération

#### `-L, --list-darks` (Lister la bibliothèque)
```bash
-L
```
- **Description** : Liste tous les master darks disponibles
- **Usage** : Inspection de la bibliothèque existante
- **Sortie** : Tableau détaillé avec caractéristiques

#### `-D, --dummy` (Mode test)
```bash
-D
```
- **Description** : Analyse sans exécuter Siril
- **Usage** : Test de configuration, validation seulement
- **Sécurité** : Aucune modification des fichiers

#### `-f, --force-recalc` (Force recalcul)
```bash
-f
```
- **Description** : Force la recréation de tous les master darks
- **Usage** : Test de nouveaux paramètres, mise à jour forcée
- **Attention** : Ignore les dates de modification

### Configuration et logs

#### `-S, --save-config` (Sauvegarde configuration) ⭐⭐
```bash
-S
```
- **Description** : Sauvegarde la configuration pour usage futur
- **Fichier** : `~/.siril_darklib_config.json`
- **Contenu** : Tous les paramètres courants
- **Usage** : Personnalisation permanente

#### `-l, --log-level` (Niveau de journalisation)
```bash
-l DEBUG      # Maximum de détails
-l INFO       # Informations importantes
-l WARNING    # Par défaut - Avertissements
-l ERROR      # Erreurs seulement
-l CRITICAL   # Critique seulement
```
- **Description** : Contrôle la verbosité des logs
- **Défaut** : `WARNING`
- **Usage** : `DEBUG` pour diagnostique, `INFO` pour suivi détaillé

#### `--log-skipped` (Log fichiers ignorés)
```bash
--log-skipped
```
- **Description** : Log les fichiers ignorés (non-DARK, invalides)
- **Usage** : Diagnostique des problèmes de fichiers

## Exemples d'utilisation

### Configuration initiale
```bash
# Setup complet avec validation et rapport
python bin/darkLibUpdate.py \
    -i /observatory/darks \
    -d ~/darkLibrary \
    -v -R \
    -t 0.1 \
    -S
```

### Utilisation quotidienne
```bash
# Avec configuration sauvegardée
python bin/darkLibUpdate.py -i /new/session/darks

# Ou utilisation des répertoires par défaut
python bin/darkLibUpdate.py
```

### Mode diagnostic
```bash
# Test avec logs détaillés
python bin/darkLibUpdate.py \
    -i /problem/darks \
    -D -v -R \
    -l DEBUG \
    --log-skipped
```

### Traitement en lot
```bash
# Plusieurs sessions
python bin/darkLibUpdate.py \
    -i /session1/darks /session2/darks /session3/darks \
    -v -R
```

### Mise à jour forcée
```bash
# Nouveaux paramètres d'empilement
python bin/darkLibUpdate.py \
    -f \
    -r sigma \
    --rejection-param1 2.5 \
    -v -R
```

## Workflow recommandé

### 1. Configuration initiale (une fois)
```bash
python bin/darkLibUpdate.py \
    -d ~/MasterDarks \
    -v -R \
    -t 0.2 \
    -S
```

### 2. Usage régulier
```bash
# Traitement automatique
python bin/darkLibUpdate.py -i /new/darks

# Avec rapport détaillé
python bin/darkLibUpdate.py -i /new/darks -R
```

### 3. Maintenance
```bash
# Inspection bibliothèque
python bin/darkLibUpdate.py -L

# Nettoyage/mise à jour
python bin/darkLibUpdate.py -f -R
```

## Fonctionnalités avancées

### Validation intelligente ⭐⭐⭐
- **Détection automatique** des darks pris capot ouvert
- **Analyse statistique** : MAD, percentiles, pixels chauds
- **Optimisation** : Validation seulement si nécessaire
- **Traçabilité** : Raisons détaillées des rejets

### Rapport consolidé ⭐⭐⭐
- **Masters mis à jour** avec statistiques
- **Fichiers rejetés** avec détails techniques
- **État bibliothèque** complète
- **Statistiques globales** de réussite

### Configuration persistante ⭐⭐
- **Sauvegarde automatique** des préférences
- **Restauration** au démarrage
- **Options courtes** pour usage fréquent
- **Flexibilité** : Override ponctuel possible

### Gestion d'interruption ⭐
- **Interruption propre** (Ctrl+C)
- **Nettoyage automatique** des fichiers temporaires
- **Message informatif** sans stack trace
- **Reprise possible** après interruption

## Options courtes (raccourcis)

| Courte | Longue | Description |
|--------|--------|-------------|
| `-i` | `--input-dirs` | Répertoires d'entrée |
| `-d` | `--dark-library-path` | Bibliothèque de sortie |
| `-w` | `--work-dir` | Répertoire de travail |
| `-s` | `--siril-path` | Exécutable Siril |
| `-m` | `--siril-mode` | Mode Siril |
| `-S` | `--save-config` | Sauvegarde config |
| `-D` | `--dummy` | Mode test |
| `-l` | `--log-level` | Niveau de log |
| `-L` | `--list-darks` | Liste bibliothèque |
| `-a` | `--max-age` | Âge maximum |
| `-c` | `--cfa` | Images couleur |
| `-o` | `--output-norm` | Normalisation |
| `-r` | `--rejection-method` | Méthode de rejet |
| `-t` | `--temperature-precision` | Précision température |
| `-f` | `--force-recalc` | Force recalcul |
| `-v` | `--validate-darks` | Validation |
| `-R` | `--report` | Rapport |

## Configuration par défaut

Le fichier `~/.siril_darklib_config.json` contient :
```json
{
  "dark_library_path": "/home/user/MasterDarks",
  "work_dir": "/tmp/sirilWorkDir",
  "validate_darks": true,
  "report": true,
  "temperature_precision": 0.5,
  "rejection_method": "winsorizedsigma",
  "stack_method": "average",
  "input_dirs": ["/path/to/darks"]
}
```

## Codes de sortie

- **0** : Succès
- **1** : Interruption utilisateur (Ctrl+C)
- **1** : Erreur d'exécution

Cette documentation couvre toutes les fonctionnalités avancées ajoutées au système de gestion des master darks ! 🎯