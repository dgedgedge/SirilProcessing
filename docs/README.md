# Documentation Technique - SirilProcessing

Ce répertoire contient la documentation technique détaillée des scripts de traitement.

## 📚 Guide d'orientation

### 📖 Pour les utilisateurs
- **[`../GUIDE_COMPLET.md`](../GUIDE_COMPLET.md)** - Documentation principale et complète
- **[`../README.md`](../README.md)** - Introduction générale du projet
- **[`SOLAR_ECLIPSE_GIF.md`](SOLAR_ECLIPSE_GIF.md)** - GIF d'éclipse solaire et lecture des images de debug

### 🔧 Pour les développeurs et maintenance

#### Fonctionnalités principales
- **[`SOLAR_ECLIPSE_GIF.md`](SOLAR_ECLIPSE_GIF.md)** - Pipeline `solarEclipseGif.py`, modèle solaire et debug plein Soleil
- **[`VALIDATION_OPTIMIZATION.md`](VALIDATION_OPTIMIZATION.md)** - Validation conditionnelle optimisée
- **[`RAPPORT_OPTIMISE.md`](RAPPORT_OPTIMISE.md)** - Système de rapport intégré
- **[`LINK_CREATION_OPTIMIZATION.md`](LINK_CREATION_OPTIMIZATION.md)** - Optimisation création liens symboliques

#### Configuration et interface
- **[`VALIDATION_CONFIG_GUIDE.md`](VALIDATION_CONFIG_GUIDE.md)** - Configuration persistante
- **[`OPTIONS_COURTES_GUIDE.md`](OPTIONS_COURTES_GUIDE.md)** - Options courtes et variables dest

#### Améliorations techniques
- **[`ROBUST_STATISTICS_UPDATE.md`](ROBUST_STATISTICS_UPDATE.md)** - Statistiques robustes (MAD/percentiles)
- **[`INTERRUPTION_HANDLING.md`](INTERRUPTION_HANDLING.md)** - Gestion propre des interruptions

#### Métadocumentation
- **[`DOCUMENTATION_STATUS.md`](DOCUMENTATION_STATUS.md)** - État et obsolescence de la documentation

## 🏗️ Architecture des améliorations

```
Validation Intelligence
├── Validation conditionnelle → VALIDATION_OPTIMIZATION.md
├── Statistiques robustes → ROBUST_STATISTICS_UPDATE.md
└── Configuration persistante → VALIDATION_CONFIG_GUIDE.md

Optimisations Performance
├── Création liens optimisée → LINK_CREATION_OPTIMIZATION.md
├── Rapport intégré → RAPPORT_OPTIMISE.md
└── Gestion interruptions → INTERRUPTION_HANDLING.md

Interface Utilisateur
├── Options courtes → OPTIONS_COURTES_GUIDE.md
└── Guide complet → ../GUIDE_COMPLET.md
```

## 📋 Ordre de lecture recommandé

### Pour comprendre l'évolution
1. `ROBUST_STATISTICS_UPDATE.md` - Bases statistiques
2. `VALIDATION_OPTIMIZATION.md` - Logique de validation
3. `LINK_CREATION_OPTIMIZATION.md` - Optimisation traitement
4. `RAPPORT_OPTIMISE.md` - Système de rapport
5. `VALIDATION_CONFIG_GUIDE.md` - Configuration
6. `OPTIONS_COURTES_GUIDE.md` - Interface CLI

### Pour implementation/debugging
1. `INTERRUPTION_HANDLING.md` - Gestion erreurs
2. `VALIDATION_OPTIMIZATION.md` - Logique validation
3. `LINK_CREATION_OPTIMIZATION.md` - Ordre opérations
4. `RAPPORT_OPTIMISE.md` - Collecte données

## 🎯 Objectif de chaque document

| Document | Objectif | Audience |
|----------|----------|----------|
| `SOLAR_ECLIPSE_GIF.md` | GIF d'éclipse, pipeline de détection, images de debug | Utilisateurs avancés / Développeurs |
| `VALIDATION_OPTIMIZATION.md` | Validation conditionnelle vs systématique | Développeurs |
| `RAPPORT_OPTIMISE.md` | Rapport intégré vs séparé | Développeurs |
| `LINK_CREATION_OPTIMIZATION.md` | Ordre création liens/validation | Développeurs |
| `ROBUST_STATISTICS_UPDATE.md` | Statistiques MAD vs std | Développeurs |
| `VALIDATION_CONFIG_GUIDE.md` | Configuration persistante | Utilisateurs avancés |
| `OPTIONS_COURTES_GUIDE.md` | Référence CLI | Utilisateurs |
| `INTERRUPTION_HANDLING.md` | Gestion Ctrl+C | Développeurs |
| `DOCUMENTATION_STATUS.md` | État documentation | Mainteneurs |

---

**Note** : Cette documentation technique complète le guide utilisateur principal. Consultez d'abord [`GUIDE_COMPLET.md`](../GUIDE_COMPLET.md) pour l'usage général.
