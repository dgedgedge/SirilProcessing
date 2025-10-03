# Gestion Propre des Interruptions Utilisateur

## Problème résolu

### Comportement précédent (problématique)
```
$ python bin/darkLibUpdate.py -i /path/darks
[... traitement en cours ...]
^C
Traceback (most recent call last):
  File "bin/darkLibUpdate.py", line 295, in main
    darklib.process_all_groups(dark_groups, validate_darks=args.validate_darks)
  File "lib/darkprocess.py", line 367, in process_all_groups
    self.stack_and_save_master_dark(group_key, files, process_dir, link_dir, validate_darks)
KeyboardInterrupt
```

**Problèmes** :
- ❌ Affichage d'une exception technique peu conviviale
- ❌ Pas d'information sur l'état du traitement
- ❌ Pas de nettoyage explicite mentionné

### Nouveau comportement (amélioré)
```
$ python bin/darkLibUpdate.py -i /path/darks
[... traitement en cours ...]
^C
⚠️  Traitement interrompu par l'utilisateur.
   Les fichiers temporaires peuvent être conservés dans le répertoire de travail.
```

**Avantages** :
- ✅ Message clair et convivial
- ✅ Information sur les fichiers temporaires
- ✅ Nettoyage automatique des ressources
- ✅ Log informatif sur l'état du traitement

## Implémentation technique

### Gestion au niveau principal (`darkLibUpdate.py`)
```python
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\\n⚠️  Traitement interrompu par l'utilisateur.")
        print("   Les fichiers temporaires peuvent être conservés dans le répertoire de travail.")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Erreur inattendue: {e}")
        sys.exit(1)
```

### Gestion dans le traitement (`darkprocess.py`)
```python
def process_all_groups(self, dark_groups, validate_darks: bool = False):
    total_groups = len(dark_groups)
    processed_groups = 0
    
    try:
        for group_key, files in dark_groups.items():
            processed_groups += 1
            logging.info(f"Processing group {processed_groups}/{total_groups}: {group_key}")
            # ... traitement ...
            
    except KeyboardInterrupt:
        logging.warning(f"Traitement interrompu après {processed_groups}/{total_groups} groupes.")
        # Nettoyage automatique
        process_dir = os.path.join(self.work_dir, "processs")
        if os.path.exists(process_dir):
            shutil.rmtree(process_dir, ignore_errors=True)
        raise  # Re-lancer pour gestion niveau supérieur
```

## Fonctionnalités d'interruption

### 1. Message utilisateur convivial
- **Format** : Emoji + message clair
- **Information** : Statut des fichiers temporaires
- **Sortie propre** : Code d'erreur 1

### 2. Nettoyage automatique
- **Répertoires temporaires** : Suppression automatique du répertoire `processs`
- **Liens symboliques** : Nettoyage des liens créés
- **État cohérent** : Pas de fichiers partiels laissés

### 3. Information de progression
- **Logs informatifs** : "Traitement interrompu après X/Y groupes"
- **Compteur de progression** : "Processing group 3/7: ..."
- **État du traitement** : Information sur ce qui a été complété

### 4. Gestion multi-niveaux
- **Niveau principal** : Message utilisateur final
- **Niveau traitement** : Nettoyage et logs techniques
- **Propagation** : Exception re-lancée pour cohérence

## Scenarios d'utilisation

### 1. Interruption pendant groupement
```bash
$ python bin/darkLibUpdate.py -i /large_dataset
Scanning directory: /large_dataset
^C
⚠️  Traitement interrompu par l'utilisateur.
   Les fichiers temporaires peuvent être conservés dans le répertoire de travail.
```

### 2. Interruption pendant stacking Siril
```bash
$ python bin/darkLibUpdate.py -i /darks -v
Processing group 2/5: Temp15.0_Exp300.0_Gain100.0_CAM1
Validating dark files for group...
^C
WARNING: Traitement interrompu après 2/5 groupes.
⚠️  Traitement interrompu par l'utilisateur.
   Les fichiers temporaires peuvent être conservés dans le répertoire de travail.
```

### 3. Interruption pendant validation
```bash
$ python bin/darkLibUpdate.py -i /darks -v -R
Processing group 1/3: Temp20.0_Exp120.0_Gain200.0_CAM1
Validating dark files for group...
^C
WARNING: Traitement interrompu après 1/3 groupes.
⚠️  Traitement interrompu par l'utilisateur.
   Les fichiers temporaires peuvent être conservés dans le répertoire de travail.
```

## Avantages pour l'utilisateur

### 1. Expérience utilisateur améliorée
- **Pas de stack trace** technique effrayant
- **Message informatif** sur l'état du système
- **Sortie propre** du programme

### 2. Transparence du processus
- **Progression visible** : X/Y groupes traités
- **Information sur l'état** : Ce qui a été fait vs ce qui reste
- **Logs structurés** : Suivi technique dans les logs

### 3. Nettoyage automatique
- **Espace disque** : Pas de fichiers temporaires oubliés
- **État cohérent** : Pas de répertoires de travail corrompus
- **Redémarrage propre** : Possibilité de relancer sans conflit

### 4. Flexibilité opérationnelle
- **Arrêt en cours** : Possibilité d'interrompre les longs traitements
- **Reprise possible** : Les masters créés sont préservés
- **Diagnostic simple** : Logs clairs sur ce qui s'est passé

## Cas d'usage typiques

### Traitement long interrompu
```bash
# Début traitement de 50 groupes
python bin/darkLibUpdate.py -i /massive_dataset -v

# Interruption après 20 groupes (Ctrl+C)
# Les 20 premiers masters sont créés et conservés
# Relance pour traiter les 30 restants
python bin/darkLibUpdate.py -i /massive_dataset -v
```

### Test avec interruption volontaire
```bash
# Test rapide avec interruption volontaire
python bin/darkLibUpdate.py -i /test_data -v &
sleep 5 && kill -INT $!
# Sortie propre avec message clair
```

### Mode dummy interrompu
```bash
# Analyse longue interrompue
python bin/darkLibUpdate.py -i /large_set -D -v
^C
# Pas de problème, aucun fichier modifié
```

Cette amélioration rend l'outil beaucoup plus convivial et professionnel dans sa gestion des interruptions utilisateur ! 🎯