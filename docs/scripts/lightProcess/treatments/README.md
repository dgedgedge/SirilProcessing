# lightProcess — parcours de traitement

[Documentation](../../../README.md) › [lightProcess](../README.md)

Le parcours comporte une boucle de calibration sur les sous-sessions, une
boucle de stacking sur les sessions d’entrée, puis une mosaïque optionnelle.
Chaque calibration comporte elle-même une boucle sur les groupes de lights
et un sous-traitement des flats. Les schémas ci-dessous détaillent ces niveaux.

## Vocabulaire et périmètre du stacking

Une **session** est un répertoire fourni en paramètre à la commande.
Ses **sous-sessions** sont les répertoires d’acquisition contenant `light/`
ou `Light/`, avec éventuellement `flat/` ou `Flat/`, immédiatement en dessous.
Si la session contient directement `light/`, elle est traitée comme une unique
sous-session. Un **groupe de calibration** réunit des lights compatibles par
leurs métadonnées au sein d’une sous-session.

Les FITS calibrés de toutes les sous-sessions d’une session sont réunis pour
produire un seul résultat de stacking. Ce traitement est identique avec une
ou plusieurs sous-sessions. Avec `--mosaic`, les résultats de stacking des
sessions sont ensuite assemblés ; les poses calibrées ne sont pas ajoutées
aux entrées de la mosaïque.

## 1. Boucles principales et mosaïque finale

Le script découvre d’abord les sous-sessions de toutes les sessions, puis traite
leur liste. La boucle de stacking parcourt ensuite à nouveau les sessions.
Le mode simulation journalise les traitements prévus sans lancer Siril ; le
schéma montre le parcours d’exécution effectif.

```mermaid
flowchart TD
    START["Sessions fournies à la commande"] --> ROOTS{"Session suivante à découvrir ?"}
    ROOTS -->|Oui| DISCOVER["Découvrir les sous-sessions<br/>ou la session directement fournie"]
    DISCOVER --> MAP["Associer chaque sous-session à sa session"]
    MAP --> ROOTS
    ROOTS -->|Non| SESSIONS{"Sous-session suivante à calibrer ?"}

    subgraph CALIBRATION_LOOP["Boucle sur toutes les sous-sessions découvertes"]
        SESSIONS -->|Oui| CALIBRATE[["Calibrer les groupes de la sous-session<br/>Détail : schéma 2"]]
        CALIBRATE --> SESSION_RESULT["Conserver les FITS calibrés disponibles<br/>Enregistrer succès, échecs et statistiques"]
        SESSION_RESULT --> SESSIONS
    end

    SESSIONS -->|Non| TARGETS{"Session suivante à empiler ?"}
    subgraph STACKING_LOOP["Boucle de stacking : un résultat par session"]
        TARGETS -->|Oui| GATHER["Réunir les FITS calibrés de cette session<br/>Une ou plusieurs sous-sessions<br/>Relire le disque avec --force-stacking<br/>Dédupliquer les chemins"]
        GATHER --> INPUTS{"Des FITS calibrés sont disponibles ?"}
        INPUTS -->|Non| EMPTY["Journaliser l'absence de sortie combinée"]
        EMPTY --> TARGETS
        INPUTS -->|Oui| CACHE{"Stack existant réutilisable<br/>et reconstruction non forcée ?"}
        CACHE -->|Oui| REUSE["Réutiliser le résultat de cette session"]
        CACHE -->|Non| STACK[["Sélectionner, aligner et empiler cette session<br/>Détail : schéma 3"]]
        STACK --> TARGET_RESULT["Conserver le résultat disponible<br/>et le rapport de stacking"]
        REUSE --> TARGET_RESULT
        TARGET_RESULT --> TARGETS
    end

    TARGETS -->|Non| MOSAIC_REQUEST{"--mosaic et au moins deux<br/>résultats de stacking disponibles ?"}
    MOSAIC_REQUEST -->|Non| END["Bilan final"]
    subgraph MOSAIC["Sous-traitement final : mosaïque optionnelle"]
        MOSAIC_REQUEST -->|Oui| MOSAIC_INPUTS["Collecter un résultat de stacking par session"]
        MOSAIC_INPUTS --> MOSAIC_NAME["Choisir le nom et préparer les entrées"]
        MOSAIC_NAME --> MOSAIC_CONVERT["convert mosaic_"]
        MOSAIC_CONVERT --> MOSAIC_SOLVE["seqplatesolve mosaic_"]
        MOSAIC_SOLVE --> MOSAIC_REG["seqapplyreg mosaic_ -framing=max"]
        MOSAIC_REG --> MOSAIC_STACK["stack r_mosaic_<br/>Normalisation, recouvrement et raccords"]
        MOSAIC_STACK --> MOSAIC_RESULT["FITS mosaïque ou erreur journalisée<br/>Nettoyage du travail mosaïque"]
    end
    MOSAIC_RESULT --> END
```

La mosaïque nécessite au moins deux sessions fournies et deux résultats de
stacking disponibles. Voir [la mosaïque](../MOSAIC.md) pour ses traitements.

## 2. Sous-traitement d’une sous-session : groupes, flats et lights

Les groupes sont indépendants : l’échec d’un groupe est journalisé puis la
boucle poursuit avec le suivant. Les flats de la sous-session sont chargés
et filtrés pour chaque groupe de lights concerné. Le code conserve la durée
majoritaire des flats compatibles ; il ne produit pas un master pour chaque
durée de flat présente.

```mermaid
flowchart TD
    SESSION["Entrée : une sous-session"] --> LIGHTS["Lister les lights et lire leurs métadonnées"]
    LIGHTS --> GROUPS["Écarter les fichiers invalides<br/>Grouper caméra, température, exposition, gain et binning"]
    GROUPS --> NEXT{"Groupe de lights suivant ?"}
    NEXT -->|Non| SUMMARY["Résumé de la sous-session<br/>Retour des FITS calibrés produits ou réutilisés"]

    subgraph GROUP_LOOP["Boucle sur les groupes de calibration"]
        NEXT -->|Oui| DARK_MODE{"Darks activés ?"}
        DARK_MODE -->|Oui| DARK_LIGHT["Chercher le master dark adapté aux lights"]
        DARK_LIGHT --> DARK_FOUND{"Master dark trouvé ?"}
        DARK_FOUND -->|Non| FAILURE["Échec du groupe : journaliser la cause"]
        DARK_FOUND -->|Oui| FLAT_SELECTION
        DARK_MODE -->|Non| FLAT_SELECTION["Sélectionner les flats compatibles<br/>Caméra, gain et binning<br/>Garder la durée de pose majoritaire"]
        FLAT_SELECTION --> CALIBRATED_CACHE{"Calibrations exportées réutilisables<br/>sans --force et avec CFA adapté ?"}
        CALIBRATED_CACHE -->|Oui| REUSE_GROUP["Réutiliser les calibrations et leur séquence"]
        REUSE_GROUP --> NEXT
        CALIBRATED_CACHE -->|Non| PREPARE_LIGHTS["Préparer la séquence de lights"]
        PREPARE_LIGHTS --> HAS_FLATS{"Flats compatibles disponibles ?"}

        subgraph FLAT_PROCESS["Sous-traitement : master flat du groupe"]
            HAS_FLATS -->|Oui| FLAT_CACHE{"Master flat existant<br/>et calcul non forcé ?"}
            FLAT_CACHE -->|Oui| USE_FLAT["Utiliser le master flat"]
            FLAT_CACHE -->|Non| FLAT_DARK_MODE{"Darks activés ?"}
            FLAT_DARK_MODE -->|Oui| FIND_FLAT_DARK["Chercher le master dark adapté aux flats"]
            FIND_FLAT_DARK --> FLAT_DARK_FOUND{"Dark des flats trouvé ?"}
            FLAT_DARK_FOUND -->|Non| FAILURE
            FLAT_DARK_FOUND -->|Oui| PREPARE_FLATS["Préparer les flats et convertir dans flat_process"]
            FLAT_DARK_MODE -->|Non| PREPARE_FLATS
            PREPARE_FLATS --> CALIBRATE_FLATS["Calibrer les flats avec leur dark si activé<br/>Sinon conserver la séquence convertie"]
            CALIBRATE_FLATS --> STACK_FLATS["Empiler les flats en médiane<br/>avec normalisation multiplicative"]
            STACK_FLATS --> FLAT_OK{"Siril réussi et master flat présent ?"}
            FLAT_OK -->|Non| FAILURE
            FLAT_OK -->|Oui| USE_FLAT
        end

        HAS_FLATS -->|Non| CONVERT_LIGHTS["Nettoyer process et convertir les lights"]
        USE_FLAT --> CONVERT_LIGHTS
        CONVERT_LIGHTS --> CALIBRATE_LIGHTS["Calibrer les lights avec les masters disponibles<br/>Conserver le CFA en Drizzle auto ou force<br/>Dématricer en mode off"]
        CALIBRATE_LIGHTS --> LIGHT_OK{"Siril réussi et sorties calibrées présentes ?"}
        LIGHT_OK -->|Non| FAILURE
        LIGHT_OK -->|Oui| EXPORT["Copier les pp_light_*.fit(s)<br/>Exporter la séquence Siril associée"]
        EXPORT --> CLEANUP["Nettoyer les séquences temporaires<br/>sauf --keep-intermediate"]
        FAILURE --> CLEANUP
        CLEANUP --> NEXT
    end
```

Les échecs de préparation ou de lecture rejoignent aussi la sortie d’échec du
groupe. Le nettoyage concerne les séquences temporaires effectivement préparées.
Les règles de choix des masters figurent dans [Calibration](CALIBRATION.md).

## 3. Sous-traitement de stacking d’une session

Ce sous-traitement est appelé pour chaque session dans la boucle de
stacking. Les contrôles de qualité précèdent les pondérations. Les contrôles
supplémentaires après dématriçage ou réalignement vérifient les transformations,
sans recalculer les seuils FWHM, rondeur, comptage ou profils stellaires.

```mermaid
flowchart TD
    INPUT["FITS calibrés d'une seule session"] --> LAYOUT["Écarter les fichiers disparus<br/>Conserver la structure FITS majoritaire"]
    LAYOUT --> COUNT{"Nombre d'entrées compatibles ?"}
    COUNT -->|Aucune| FAIL["Échec du stack et rapport associé si disponible"]
    COUNT -->|Une| ONE["Retourner cette image directement<br/>Sans sélection ni empilement"]
    COUNT -->|Plusieurs| INPUT_LINKS["00_inputs : préparer les liens ou copies"]
    INPUT_LINKS --> REGISTER["01_registration : convertir, détecter les étoiles<br/>Astrométrie si activée puis alignement"]
    REGISTER --> GEOMETRY["02_quality : contrôler les transformations<br/>et lire les mesures des poses indépendantes"]
    GEOMETRY --> QUALITY["Appliquer les critères de sélection<br/>FWHM, rondeur, nombre d'étoiles"]
    QUALITY --> PROFILES["Mesurer et filtrer les profils stellaires<br/>R80 et allongement cohérent"]
    PROFILES --> RETAINED{"Des poses restent sélectionnées ?"}
    RETAINED -->|Non| FAIL
    RETAINED -->|Oui| WEIGHTS["Pondérer les poses retenues<br/>par répétitions FWHM et rondeur"]
    WEIGHTS --> DRIZZLE["Analyser le dithering, l'échantillonnage<br/>la couverture et les ressources"]
    DRIZZLE --> DRIZZLE_CHOICE{"Drizzle sélectionné ?"}
    DRIZZLE_CHOICE -->|Oui| CAPABILITY["03_capability : vérifier la compatibilité Siril"]
    CAPABILITY --> SUPPORTED{"Compatibilité vérifiée ?"}
    SUPPORTED -->|Oui| DRIZZLE_OPTIONS["Préparer les paramètres Drizzle<br/>Conserver les transformations initiales contrôlées"]
    SUPPORTED -->|Non, mode force| FAIL
    SUPPORTED -->|Non, mode auto| STANDARD
    DRIZZLE_CHOICE -->|Non| SKIP["03_capability : écrire SKIPPED.txt"]
    SKIP --> STANDARD{"Branche standard : images CFA ?"}
    STANDARD -->|Oui| DEBAYER["04_debayer_registration : dématricer et réaligner<br/>Contrôler la nouvelle séquence avant application"]
    STANDARD -->|Non| ROBUST
    DEBAYER --> ROBUST{"Réalignement robuste activé ?"}
    ROBUST -->|Oui| REALIGN["04_realign : appliquer l'alignement puis réaligner<br/>Contrôler les nouvelles transformations"]
    ROBUST -->|Non| APPLY
    REALIGN --> APPLY["04_stacking : appliquer l'alignement<br/>uniquement aux entrées incluses"]
    DRIZZLE_OPTIONS --> APPLY
    APPLY --> STACK["Empiler avec la méthode et les seuils demandés"]
    STACK --> RESULT["FITS final et rapport de stacking"]
    RESULT --> RETURN["Retour à la boucle des sessions"]
    ONE --> RETURN
```

Les échecs Siril, les séquences illisibles et l’absence de transformation
admissible interrompent le sous-traitement. Le cas d’une seule image renvoie
son chemin sans produire les rapports des étapes non exécutées. Le détail des
commandes et du cadrage est donné dans [Alignement et empilement](STACKING.md).

## Pages de référence

| Étape | Détail |
|---|---|
| Entrées et calibration | [Découverte, groupement, darks, flats et CFA](CALIBRATION.md) |
| Choix des poses | [Sélection des images](../filter/stacking/IMAGE_SELECTION.md) |
| Contrôle des pixels | [Profils stellaires R80 et allongement](../filter/stacking/STELLAR_PROFILE_FILTER.md) |
| Alignement et stacking | [Scripts exécutés et branche standard/Drizzle](STACKING.md) |
| Décision Drizzle | [Conditions, ressources et paramètres](../filter/stacking/DRIZZLE_SPECIFICATION.md) |
| Sorties et relance | [Fichiers, cache, journaux et options de reprise](OUTPUTS.md) |
| Assemblage de champs | [Mosaïque optionnelle](../MOSAIC.md) |
| Séquences | [Images, mesures, transformations et écritures](../reference/SIRIL_SEQUENCE.md) |
