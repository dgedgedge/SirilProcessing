# Documentation SirilProcessing

La documentation est organisée par étape de traitement et par sujet technique.
Le [guide complet](../GUIDE_COMPLET.md) présente l’utilisation générale et le
[README du projet](../README.md) les commandes de démarrage.

## Traitement des images

- [Traitement des lights](LIGHT_PROCESSOR_GUIDE.md) : calibration, alignement et stack final multi-sessions.
- [Filtrage et contrôle qualité](filter/README.md) : choix des images et validation des darks.
- [Stacking et sélection des poses](filter/stacking/README.md) : critères, profils stellaires et Drizzle.
- [Mosaïques](MOSAIC_GUIDE.md) : assemblage de plusieurs champs.
- [GIF d’éclipse solaire](SOLAR_ECLIPSE_GIF.md) : traitement et visualisation des images solaires.

## Configuration et utilisation

- [Configuration persistante](VALIDATION_CONFIG_GUIDE.md).
- [Options courtes](OPTIONS_COURTES_GUIDE.md).
- [Gestion de plusieurs sessions](MULTIPLE_SESSIONS_FEATURE.md).
- [Gestion des chemins absolus](ABSOLUTE_PATHS_FEATURE.md).

## Formats et fonctionnement interne

- [Séquences Siril](SIRIL_SEQUENCE.md) : lecture, modification et écriture des fichiers `.seq`.
- [Bibliothèque Python](../lib/README.md) : organisation des modules.
- [Rapports de traitement](RAPPORT_OPTIMISE.md).
- [Création des liens symboliques](LINK_CREATION_OPTIMIZATION.md).
- [Gestion des interruptions](INTERRUPTION_HANDLING.md).
- [Nettoyage des processus](PROCESS_CLEANUP_FEATURE.md).
- [Commande pyecho](PYECHO_GUIDE.md).

## Maintenance de la documentation

- [État de la documentation](DOCUMENTATION_STATUS.md).
- [Tests du projet](../tests/README.md).

Pour générer le site HTML depuis la racine du projet :

```bash
.venv/bin/python bin/generate_docs_html.py
```

Ouvrir `out/index.html`. L’index suit les dossiers de documentation ; chaque
page de `filter/stacking/` permet de revenir aux rubriques parentes.
