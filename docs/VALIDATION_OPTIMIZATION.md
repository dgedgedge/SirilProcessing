# Validation Conditionnelle des Darks

## Vue d'ensemble

La validation des fichiers darks d'entrée ne se fait maintenant **que dans le cas où le master dark correspondant doit être mis à jour**. Cette optimisation améliore significativement les performances lors de traitements répétés.

## Comportement précédent vs nouveau

### Comportement précédent (moins efficace)
```
1. Lire tous les fichiers darks
2. Valider TOUS les fichiers darks (coûteux en temps)
3. Regrouper les darks validés
4. Pour chaque groupe, vérifier si le master dark doit être mis à jour
5. Si oui, effectuer le stacking
```

### Nouveau comportement (optimisé)
```
1. Lire tous les fichiers darks
2. Regrouper TOUS les darks (sans validation)
3. Pour chaque groupe, vérifier si le master dark doit être mis à jour
4. Si oui:
   a. Valider SEULEMENT les darks de ce groupe (si --validate-darks activé)
   b. Effectuer le stacking avec les darks validés
5. Si non, ignorer le groupe (pas de validation inutile)
```

## Avantages de la nouvelle approche

### 1. Performance améliorée
- **Validation conditionnelle** : Pas de validation pour les groupes déjà à jour
- **Temps de traitement réduit** : Seulement les groupes nécessaires sont validés
- **Économie de ressources** : Évite l'analyse statistique coûteuse des images inutiles

### 2. Logique plus cohérente
- **Validation ciblée** : Validation seulement avant stacking effectif
- **Cohérence temporelle** : Validation au moment du besoin réel
- **Traçabilité** : Les logs indiquent clairement quand la validation a lieu

### 3. Flexibilité préservée
- **Options inchangées** : `--validate-darks` et `--no-validate-darks` fonctionnent toujours
- **Rapport de validation** : `--validation-report` analyse toujours tous les fichiers
- **Configuration persistante** : Les préférences sont toujours sauvegardées

## Scenarios d'utilisation

### Scenario 1: Première création des master darks
```bash
# Tous les groupes nécessitent une création
python bin/darkLibUpdate.py -i /new/darks -v
# Résultat: Validation + stacking pour tous les groupes
```

### Scenario 2: Mise à jour avec darks existants
```bash
# Certains master darks sont déjà à jour
python bin/darkLibUpdate.py -i /mixed/darks -v
# Résultat: Validation + stacking seulement pour les groupes obsolètes
```

### Scenario 3: Re-exécution sans changements
```bash
# Tous les master darks sont à jour
python bin/darkLibUpdate.py -i /existing/darks -v
# Résultat: Aucune validation, aucun stacking
```

### Scenario 4: Force recalculation
```bash
# Force la recreation de tous les master darks
python bin/darkLibUpdate.py -i /darks -v -f
# Résultat: Validation + stacking pour tous les groupes (forcé)
```

## Messages de log

### Validation activée et master dark à mettre à jour
```
INFO: Validating dark files for group TempXX_ExpYY_GainZZ before stacking...
WARNING: Invalid dark rejected: /path/dark001.fit - Test 2 failed: too many hot pixels
INFO: Group TempXX_ExpYY_GainZZ: 1 invalid dark(s) rejected, 4 valid dark(s) remaining.
```

### Validation activée mais master dark à jour
```
INFO: Master dark already exists and is newer or same date (2025-10-03). Update ignored.
```
*Pas de message de validation = validation non effectuée*

### Validation désactivée
```
INFO: Processing group: TempXX_ExpYY_GainZZ
```
*Pas de validation même si mise à jour nécessaire*

## Impact sur les performances

### Estimation des gains
- **Grands jeux de données** : Gain de 50-80% du temps de traitement lors de re-exécutions
- **Validation coûteuse** : Évite l'analyse statistique (MAD, percentiles) des images inutiles
- **I/O réduits** : Moins de lectures FITS pour la validation

### Cas où le gain est maximal
- Bibliothèques de darks importantes déjà constituées
- Re-exécutions fréquentes du script
- Validation activée par défaut dans la configuration
- Groupes de darks nombreux avec peu de mises à jour

## Notes techniques

### Code affecté
- `group_dark_files()` : Ne fait plus de validation
- `stack_and_save_master_dark()` : Validation conditionnelle ajoutée
- `process_all_groups()` : Propage le paramètre de validation

### Compatibilité
- **API inchangée** : Les options CLI restent identiques
- **Configuration compatible** : Les fichiers config existants fonctionnent
- **Comportement préservé** : Le rapport de validation analyse toujours tous les fichiers