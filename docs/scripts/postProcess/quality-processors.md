# Processeurs des contrôles de restauration

[Documentation](../../README.md) › [postProcess](README.md) › Processeurs de qualité

## 1. Responsabilités et séquence

Les analyses indépendantes sont des classes dérivées de `processor`, avec
`post_process`, `add_arguments` et `parameter_children`. Les algorithmes des
mesures sont décrits dans la [référence scientifique](quality.md) ; cette page
explique leur exécution et leurs interfaces.

`RestorationEvaluationProcessor` est l'orchestrateur du contrôle final. Il
appelle récursivement les sous-processeurs ; chaque feuille effectue uniquement
son analyse. L'arbre d'exécution est le suivant :

```text
RestorationEvaluationProcessor
├── ReferencePreparationProcessor       si la référence n'est pas déjà préparée
│   ├── MaskPreparationProcessor        cartes et masques fixes
│   └── SkyTileProcessor                tuiles admissibles selon ces masques
├── StellarAssessmentProcessor
│   ├── SirilStarDetectionProcessor      catalogues avant/après
│   └── StarComparisonProcessor          appariement et représentativité
│       └── ReferenceStarMeasurementProcessor
│                                       ajustements locaux aux positions initiales
└── ArtifactQualityProcessor
    ├── LegacyNoiseProcessor             diagnostic historique des hautes fréquences
    ├── StellarRingProcessor             profils radiaux et nouveaux anneaux
    └── ReferenceEvaluationProcessor
        ├── GeometryQualityProcessor    dimensions, finitude et métadonnées
        ├── SkyNoiseProcessor           dispersion et incertitude sur tuiles fixes
        ├── DiffuseSignalProcessor      flux et résidus des régions diffuses
        ├── StellarFluxProcessor        photométrie d'ouverture des étoiles
        └── SaturationQualityProcessor  nouveaux pixels à saturation normalisée
```

Les masques précèdent les tuiles, qui dépendent de leurs exclusions. Les mesures
stellaires utilisent le catalogue initial ; les anneaux et flux stellaires
utilisent les positions appariées. Les diagnostics de bruit et de signal diffus
ne dépendent pas de ces mesures stellaires. Ils restent exécutés par
l'orchestrateur complet même si la cohorte stellaire est insuffisante : les
mesures stellaires manquantes sont alors inconnues. Une erreur technique ou
une géométrie invalide reste bloquante.

Le mode `legacy` n'exécute pas les diagnostics fiables de référence : il conserve
ses anciennes règles de bruit et d'anneaux. La comparaison de chaînes utilise
le mode `reliable`. Les critères d'acceptation et le classement restent dans les
moteurs et l'orchestrateur de restauration ; aucun processeur de mesure ne
choisit une image gagnante.

## 2. Interfaces et données partagées

Les classes d'analyse d'image sont regroupées dans `lib.quality_processors` ;
les classes stellaires sont dans `lib.quality_star_processors`.
`QualityContext` porte les chemins source/candidat, leurs plans normalisés,
la référence préparée et les paires stellaires. Les sous-processeurs partagent
ce contexte : ils ne relisent pas les grands plans pour chaque diagnostic.
Les ajustements stellaires lisent le canal nécessaire aux fenêtres de mesure.

Les tableaux de la référence sont scellés en lecture seule après préparation.
Les contrôles n'écrivent pas dans les images candidates. La détection écrit
les catalogues TSV explicitement demandés ; les masques et aperçus continuent
d'être publiés par les appels existants. Un catalogue de référence commun peut
être réutilisé entre contrôles finaux du même lancement.

Chaque sous-processeur accepte :

- `input_path` : candidat du contexte, ou source pour la préparation ;
- `output_path` : rapport JSON, ou `None` pour un sous-appel en mémoire ;
- `args` : options scientifiques résolues pour l'appel.

Les constructeurs reçoivent le contexte et, selon l'étape, les catalogues,
le canal, le rayon de mesure ou le dossier de travail. Les options communes
restent déclarées une seule fois, et le graphe `parameter_children` permet à
`Config` de recenser les enfants sans les instancier.

Les fonctions historiques `artifact_metrics`, `compare_star_catalogs` et
`measure_reference_catalog`, ainsi que les méthodes `QualityReference.noise`,
`signal`, `stellar_flux` et `evaluate`, délèguent à ces processeurs. Les champs
scientifiques et les unités restent identiques : FWHM et distances en pixels,
rapports sans unité, flux en somme de pixels normalisés.

Les options et leurs défauts sont déclarés par leurs propriétaires :

| Classe | Paramètres |
| --- | --- |
| `MaskPreparationProcessor` | seuil des sources, échelles, croissance des masques |
| `SkyTileProcessor` | taille des tuiles |
| `DiffuseSignalProcessor` | plafond des régions, stratifié par tailles et positions ; backend des filtres multiscales |
| `SkyNoiseProcessor` | effectif minimal, gain, significativité, plafond et lots |
| `FluxQualityProcessor`, base des flux diffus et stellaires | perte de flux, contrôle du signal diffus |
| `ArtifactQualityProcessor` | mode de qualité |
| `SirilStarDetectionProcessor` | canal de détection |
| `StarComparisonProcessor` | rayon, effectif, fraction et appariement manuel |
| `ReferenceStarMeasurementProcessor` | activation des mesures aux positions initiales |

Les orchestrateurs appellent récursivement `add_arguments` sur leurs
`parameter_children`. Les branches partagées évitent les doublons.
`RestorationQuality` reste une façade qui collecte les déclarations et défauts
des processeurs d'image. Les noms CLI et JSON historiques sont conservés,
ainsi que la priorité CLI > JSON > défauts locaux, la sauvegarde explicite
et les instantanés indépendants.

## 3. Rapports et chronométrage récursif

Chaque processeur ajoute un champ `processing` à son résultat :

| Champ | Signification |
|---|---|
| `processor` | Classe réellement exécutée |
| `stage` | Identifiant du contrôle |
| `duration_seconds` | Temps mural du calcul et de ses sous-appels, hors écriture finale du JSON propre à ce processeur |
| `execution_status` | `completed` ou `failed`, indépendamment du verdict scientifique |
| `children` | Audits de durée des enfants réellement exécutés |

Une feuille a une liste `children` vide. Un parent inclut le coût de ses enfants :
**ne pas additionner les durées de plusieurs niveaux**. Les journaux INFO
annoncent chaque durée à la fin de l'appel, y compris en cas d'échec.
Un rapport demandé conserve l'erreur et les durées déjà disponibles, puis
l'exception d'origine est propagée. Les enfants suivants ne sont pas exécutés
après une erreur technique ; un simple verdict scientifique `UNKNOWN` n'a pas
cet effet sur les diagnostics indépendants.

La préparation partagée reste accessible dans
`quality_reference.preparation_processing`, ou dans
`artifacts.reference.preparation_processing`. Le contrôle final d'une variante
contient son arbre `processing` ; les mesures locales le conservent dans
`comparison.processing` et `artifacts.processing`. Les rapports moteurs
contiennent aussi la durée de leur détection Siril, y compris pour les essais
de déconvolution. Ces détails complètent les [temps par moteur et par chaîne](timings.md).

La détection est séparée du calcul de débruitage ou de déconvolution Siril pour
mesurer son propre coût. Cela implique des appels Siril distincts, dont les
temps de démarrage et de lecture sont inclus. La séparation ne garantit donc
pas une accélération. Les calculs restent séquentiels ; aucun parallélisme
implicite ni nouveau cache entre lancements n'est introduit.

## 4. Vérifications

`tests/test_quality_processors.py` vérifie les feuilles autonomes, le gain connu
de bruit, l'ordre récursif, le partage des plans, les diagnostics inconnus,
les rapports et les échecs. Une horloge déterministe vérifie une durée distincte
par feuille et une durée englobante par parent, sans attente artificielle.
Les tests stellaires vérifient les mesures réelles sur étoiles synthétiques ;
Siril est simulé dans les tests d'orchestration. Les contrôles existants de
qualité, choix de chaînes et conservation des sources restent applicables.

Le [processeur de bruit sur tuiles](noise-tiles.md) limite par défaut son
échantillon à 256 tuiles réparties sur le champ. Ses opérations par lots
s'exécutent sur NumPy ou PyTorch, sans lancer simultanément d'autres moteurs de
restauration. Les masques et la liste complète des tuiles préparées restent
partagés et inchangés.

La sélection des régions de `DiffuseSignalProcessor` est décrite dans
[Échantillonnage des régions diffuses](diffuse-regions.md). Les filtrages
multiscales utilisent PyTorch sur CUDA ou MPS en mode auto, avec référence
SciPy CPU ; les mesures de flux et le bootstrap restent sur CPU.
