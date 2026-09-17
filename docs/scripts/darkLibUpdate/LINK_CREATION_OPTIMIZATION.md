# Optimisation de la Création de Liens Symboliques

[Documentation](../../README.md) › [darkLibUpdate](README.md)

## Problème résolu

### Comportement précédent (problématique)
```
1. Créer des liens symboliques pour TOUS les fichiers du groupe
2. Valider les fichiers darks 
3. Filtrer les fichiers invalides
4. Lancer Siril sur le répertoire contenant TOUS les liens (valides + invalides)
```

**Problème** : Siril traite automatiquement tous les fichiers présents dans le répertoire, y compris ceux qui ont été marqués comme invalides par la validation.

### Nouveau comportement (correct)
```
1. Valider les fichiers darks (si nécessaire)
2. Filtrer pour ne garder que les fichiers valides
3. Créer des liens symboliques SEULEMENT pour les fichiers validés
4. Lancer Siril sur le répertoire contenant uniquement les liens valides
```

**Avantage** : Siril ne traite que les fichiers qui ont passé la validation.

## Impact technique

### Cohérence de validation
- **Avant** : Validation effectuée mais fichiers invalides quand même traités par Siril
- **Maintenant** : Validation respectée, seuls les darks valides sont empilés

### Qualité des master darks
- **Amélioration** : Les master darks ne contiennent plus de darks suspects (capot ouvert, lumière parasite)
- **Fiabilité** : Garantie que tous les darks empilés sont conformes aux critères de validation

### Performance
- **Siril** : Traite moins de fichiers si certains sont rejetés par la validation
- **I/O** : Moins de liens symboliques créés si validation activée
- **Logs** : Nombre de fichiers dans l'en-tête FITS correspond aux fichiers réellement utilisés

## Workflow détaillé

### Cas 1: Validation désactivée (`--no-validate-darks`)
```
1. Vérifier si master dark doit être mis à jour
2. Si oui: créer liens pour tous les fichiers → Siril traite tous
3. Sinon: ignorer le groupe
```

### Cas 2: Validation activée (`--validate-darks`)
```
1. Vérifier si master dark doit être mis à jour
2. Si oui:
   a. Valider chaque fichier dark du groupe
   b. Filtrer pour ne garder que les valides
   c. Vérifier qu'il reste au moins 2 fichiers valides
   d. Créer liens SEULEMENT pour les fichiers valides → Siril traite seulement les valides
3. Sinon: ignorer le groupe (pas de validation ni création de liens)
```

## Messages de log améliorés

### Validation avec rejets
```
INFO: Validating dark files for group Temp15.0_Exp300.0_Gain100.0_CAM1 before stacking...
WARNING: Invalid dark rejected: /path/dark_001.fit - Test 2 failed: too many hot pixels
WARNING: Invalid dark rejected: /path/dark_007.fit - Test 1 failed: median too high  
INFO: Group Temp15.0_Exp300.0_Gain100.0_CAM1: 2 invalid dark(s) rejected, 8 valid dark(s) remaining.
INFO: Header updated with group metadata, stack command, and number of frames (8).
```

### Validation avec trop peu de fichiers valides
```
INFO: Validating dark files for group Temp15.0_Exp300.0_Gain100.0_CAM1 before stacking...
WARNING: Invalid dark rejected: /path/dark_001.fit - Test 3 failed: excessive noise
WARNING: Group Temp15.0_Exp300.0_Gain100.0_CAM1 contains only 1 valid file(s) after validation. Stacking ignored (Siril requires at least 2).
```

### Master dark à jour (pas de validation ni création de liens)
```
INFO: Master dark already exists and is newer or same date (2025-10-03). Update ignored.
```

## Code technique

### Modification dans `process_all_groups()`
```python
# AVANT: Créer les liens avant validation
linked_infos = []
for i, info in enumerate(files):
    newInfo = info.create_symlink(link_dir, index=i)
    linked_infos.append(newInfo)
self.stack_and_save_master_dark(group_key, linked_infos, ...)

# MAINTENANT: Passer les fichiers originaux
self.stack_and_save_master_dark(group_key, files, ...)
```

### Modification dans `stack_and_save_master_dark()`
```python
# Validation conditionnelle
if validate_darks:
    valid_files = [info for info in fitsinfo_list if info.is_valid_dark()[0]]
    fitsinfo_list = valid_files

# Création des liens APRÈS validation
linked_infos = []
for i, info in enumerate(fitsinfo_list):
    newInfo = info.create_symlink(link_dir, index=i)
    linked_infos.append(newInfo)

# Siril ne voit que les fichiers valides
```

## Bénéfices pour l'utilisateur

### 1. Qualité garantie
- **Master darks purs** : Plus de contamination par des darks suspects
- **Validation efficace** : La validation a un impact réel sur le résultat final

### 2. Traçabilité améliorée
- **En-tête FITS précis** : Le nombre de darks correspond aux fichiers réellement utilisés
- **Logs détaillés** : Suivi exact de ce qui est rejeté et pourquoi

### 3. Performance optimisée
- **Siril efficient** : Traite seulement les fichiers pertinents
- **Moins d'I/O** : Création de liens seulement pour les fichiers nécessaires

## Cas d'usage typiques

### Validation strict d'une session d'observation
```bash
# Tous les darks doivent être parfaits
python bin/darkLibUpdate.py -i /session_darks -v --rejection-method winsorizedsigma -t 0.1
```

### Traitement en lot avec validation
```bash
# Traitement automatisé avec qualité garantie
python bin/darkLibUpdate.py -i /multiple/sessions/* -v -R -S
```

### Re-traitement après amélioration des seuils
```bash
# Force la re-validation avec de nouveaux critères
python bin/darkLibUpdate.py -i /darks -v -f
```

Cette optimisation garantit que la validation des darks a un impact direct et mesurable sur la qualité des master darks produits ! 🎯
