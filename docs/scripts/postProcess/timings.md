# Audit des temps de traitement

[Documentation](../../README.md) › [postProcess](README.md) › Temps de traitement

## 1. Consulter les durées

Le chronométrage est actif automatiquement dans `postProcess` et dans le
post-traitement de `lightProcess`. Le niveau INFO affiche une ligne `Durée — …`
à la fin de chaque phase mesurée. Les appels en échec ajoutent la mention
`interrompu ou en échec` ; leur exception conserve son comportement habituel.
Les étapes désactivées ne sont pas chronométrées.

Le rapport global contient `processing_duration_seconds` dans le résultat de
chaque étape active : gradient, photométrie, restauration ou comparaisons
séparées, AutoGHS. La même valeur est écrite dans son rapport enfant sous
`<rapport>_steps/`. Elle mesure l'appel au processeur, y compris son propre
travail de publication, avant la validation de sortie et la réécriture du rapport
par la séquence. Les anciennes durées internes du moteur sont conservées.

## 2. Décomposer la restauration

Le rapport `03_restoration.json` et la clé `restoration` du rapport global
contiennent les mesures suivantes en comparaison de chaînes :

| Champ | Périmètre |
|---|---|
| `duration_seconds` | Total de la comparaison, depuis la préparation du travail jusqu'à la publication ; dernière écriture JSON exclue |
| `timings.preparation_seconds` | Référence commune, masques et manifestes des paramètres et dépendances |
| `timings.operation_XX_preparation_seconds` | Référence locale et masques nécessaires au calcul distinct XX |
| `timings.operation_XX_processing_seconds` | Construction du moteur et appel complet, incluant calcul, contrôles locaux et produits d'audit |
| `timings.final_XX_seconds` | Contrôle final réellement exécuté pour la variante XX contre la référence commune |
| `timings.publication_seconds` | Copie de la sortie retenue et production de la synthèse scientifique |
| `engine_duration_seconds` | Cumul des appels distincts par opération et moteur, par exemple `denoise:siril` |
| `cache_hits.operations` | Nombre de réutilisations d'un calcul moteur |
| `cache_hits.final_evaluations` | Nombre de réutilisations d'un contrôle final |

Les lignes `Durée cumulée — Restauration …` classent les moteurs par temps
cumulé décroissant. Elles incluent les calculs refusés ou échoués, pas seulement
ceux de la chaîne retenue. Les préparations locales restent comptées séparément.

Un calcul partagé par plusieurs variantes n'est chronométré qu'une fois. Sa
durée peut être référencée par plusieurs variantes : ne pas additionner leurs
champs `duration_seconds`. Les listes `operations`, les cumuls par moteur et
`timings` correspondent aux calculs réellement effectués. Les écritures des
rapports intermédiaires, calculs d'empreintes et autres coûts d'orchestration
expliquent aussi une partie de l'écart entre le total et la somme des phases.
Le rapport est actualisé au fil des variantes ; une copie partielle représente
le temps mesuré jusque-là, sans garantir la présence des totaux de publication.

## 3. Décomposer un moteur

Chaque audit moteur peut contenir son propre dictionnaire `timings` :

| Moteur | Mesures |
|---|---|
| Déconvolution Siril | `siril_seconds` cumule les scripts (catalogues, PSF, essais) ; `quality_seconds` cumule l'appariement et les contrôles des essais |
| Débruitage Siril | `siril_seconds`, `quality_seconds`, `publication_seconds` |
| Cosmic Clarity | `inference_seconds` pour la construction du moteur et son inférence ; `quality_seconds` pour les catalogues Siril et les contrôles locaux |
| GraXpert | `inference_seconds` pour la construction du moteur et son inférence ; `quality_seconds` pour les catalogues, contrôles et publication |

Ces mesures sont enregistrées dans les rapports des calculs distincts, sous
`<rapport de restauration>_work/`, et dans leurs entrées `operations`.
En comparaison séparée (`--no-restoration-compare-chains`), les durées globales
par moteur restent dans `evaluations` et les détails dans chaque audit moteur.
Un moteur indisponible avant son lancement ne produit pas de durée d'inférence.

## 4. Unités et limites

Les valeurs sont des **secondes murales** mesurées avec une horloge monotone,
avec arrondi à trois décimales dans le journal et précision conservée dans le
JSON. Elles incluent les attentes et les entrées/sorties du périmètre mesuré.
Les rapports indiquent le temps de cette exécution ; une reprise garde les
anciens audits des étapes amont qu'elle ne relance pas.

Une durée de moteur est incluse dans celle de la restauration, elle-même incluse
dans celle de l'étape globale : ces niveaux se recouvrent et ne s'additionnent
pas. Les mesures ne séparent pas temps CPU, GPU et disque ; elles n'ajoutent
aucune synchronisation GPU artificielle et ne remplacent pas un profileur de
noyaux GPU. Les chargements ou acquisitions de modèles effectués pendant
l'inférence sont inclus. Un arrêt brutal du processus peut empêcher la dernière
écriture JSON ; le journal conserve les phases déjà terminées.

## 5. Détail des contrôles de qualité

Les rapports contiennent maintenant un arbre `processing` avec la classe,
le statut d'exécution, `duration_seconds` et les `children`. Il distingue
masques, tuiles, détection Siril, ajustements stellaires, appariement, bruit,
flux diffus/stellaire, anneaux, géométrie et saturation. Voir les
[processeurs récursifs](quality-processors.md) pour les emplacements et les
règles de réutilisation. Les durées parent/enfant se recouvrent.

L'audit du bruit précise également les tuiles disponibles/évaluées, leurs
coordonnées, le backend CPU/CUDA et la taille effective des lots. Le premier
appel CUDA inclut l'initialisation ; les appels suivants peuvent être plus
rapides. Voir l'[échantillonnage et les mesures par lots](noise-tiles.md).
