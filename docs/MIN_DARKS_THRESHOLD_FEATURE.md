# Critère de Mise à Jour par Nombre de Darks

## Description de la fonctionnalité

Le script `darkLibUpdate.py` inclut maintenant un nouveau critère pour déterminer si un master dark existant doit être remplacé : le **critère du nombre de darks**.

## Paramètre CLI

```bash
-n, --min-darks-threshold MIN_DARKS_THRESHOLD
```

**Valeur par défaut :** `0` (désactivé)

**Description :** Seuil minimum de darks pour mettre à jour un master dark existant. Un master dark sera remplacé si le nombre de darks disponibles dépasse ce seuil OU s'il dépasse le nombre de darks utilisés dans le master dark précédent.

## Logique de mise à jour

Le script évalue **trois critères** pour décider s'il faut mettre à jour un master dark existant :

### 1. Critère de commande de stacking différente
- **Condition :** La commande de stacking est différente de celle du master dark existant
- **Résultat :** Mise à jour **obligatoire**

### 2. Critères combinés de date ET nombre de darks
- **Critère obligatoire :** `date_darks_disponibles > date_master_existant`
- **Critère A :** `nombre_darks_disponibles >= min_darks_threshold`
- **Critère B :** `nombre_darks_disponibles > nombre_darks_dans_master_existant`
- **Résultat :** Mise à jour si date plus récente **ET** au moins un critère de nombre satisfait

### Logique finale
Un master dark sera mis à jour si :
- Commande de stacking différente (priorité absolue)
- **OU** (Date plus récente **ET** (Seuil de darks atteint **OU** Plus de darks que dans le master existant))

## Exemples d'utilisation

### Exemple 1 : Seuil absolu avec date récente
```bash
python3 bin/darkLibUpdate.py --min-darks-threshold 20 --input-dirs /path/to/darks
```
- Les master darks seront mis à jour si au moins 20 darks sont disponibles **ET** si la date est plus récente
- Même si le master dark existant a été créé avec 25 darks, il sera mis à jour avec 20+ darks plus récents

### Exemple 2 : Amélioration progressive avec date récente
```bash
python3 bin/darkLibUpdate.py --min-darks-threshold 0 --input-dirs /path/to/darks
```
- Les master darks seront mis à jour seulement s'il y a plus de darks disponibles que dans le master existant **ET** si la date est plus récente
- Permet d'améliorer progressivement la qualité en ajoutant des darks plus récents

### Exemple 3 : Stratégie hybride avec date récente
```bash
python3 bin/darkLibUpdate.py --min-darks-threshold 10 --input-dirs /path/to/darks
```
- Mise à jour si date plus récente ET (10+ darks disponibles OU plus de darks que dans le master existant)
- Garantit une qualité minimum tout en permettant l'amélioration progressive

## Messages de log

Le script affiche des messages détaillés pour expliquer les décisions :

### Mise à jour refusée (date non plus récente)
```
Master dark for GROUP_KEY kept unchanged: current darks=8, existing darks=12, threshold=10, existing date=2025-09-27, latest date=2025-09-25. Date not newer.
```

### Mise à jour refusée (date plus récente mais critères de nombre non satisfaits)
```
Master dark for GROUP_KEY kept unchanged: current darks=8, existing darks=12, threshold=15, existing date=2025-09-25, latest date=2025-09-27. Date is newer but no dark count criteria met.
```

### Mise à jour acceptée
```
Updating master dark for GROUP_KEY: newer date (2025-09-27 > 2025-09-25) and meets threshold (15 >= 10), more darks than existing (15 > 12).
```

## Cas d'usage

### 1. **Seuil de qualité minimum avec fraîcheur**
Utilisez `--min-darks-threshold 20` pour garantir que tous les master darks sont créés avec au moins 20 darks ET que les darks sont plus récents que le master existant.

### 2. **Amélioration continue avec fraîcheur**
Utilisez `--min-darks-threshold 0` pour mettre à jour uniquement quand vous avez plus de darks que précédemment ET que ces darks sont plus récents.

### 3. **Stratégie conservatrice**
Utilisez `--min-darks-threshold 50` pour ne remplacer les master darks que si vous avez un nombre très élevé de darks récents, évitant les remplacements inutiles.

### 4. **Protection contre les anciens darks**
Cette logique empêche de remplacer un master dark récent avec des darks plus anciens, même s'ils sont plus nombreux.

## Configuration persistante

Le paramètre peut être sauvegardé dans la configuration :

```bash
python3 bin/darkLibUpdate.py --min-darks-threshold 15 --save-config
```

La valeur sera réutilisée automatiquement lors des prochains lancements.

## Stockage dans l'en-tête FITS

Le nombre de darks utilisés est stocké dans l'en-tête FITS du master dark :
- **Champ :** `NDARKS`
- **Description :** Nombre entier indiquant combien de darks ont été empilés
- **Utilisation :** Permet la comparaison pour les futures mises à jour

## Compatibilité

- ✅ **Rétrocompatible :** Les master darks existants sans champ `NDARKS` sont traités comme ayant 0 darks
- ✅ **Valeur par défaut :** `--min-darks-threshold 0` préserve le comportement historique
- ✅ **Combinaison :** Fonctionne avec tous les autres paramètres (validation, rapport, etc.)

## Notes techniques

- La lecture du nombre de darks se fait via la méthode `ndarks()` de la classe `FitsInfo`
- La logique de décision est implémentée dans `darkprocess.py:stack_and_save_master_dark()`
- Les messages de log utilisent le niveau `INFO` pour la traçabilité des décisions