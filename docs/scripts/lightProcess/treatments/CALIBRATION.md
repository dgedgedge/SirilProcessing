# Découverte, regroupement et calibration

[Documentation](../../../README.md) › [lightProcess](../README.md) › [Traitements](README.md)


## Découverte des acquisitions

Chaque chemin fourni est une session. S’il contient directement `light/` ou
`Light/`, il constitue son unique sous-session. Sinon, le script cherche les
sous-sessions dans ses sous-répertoires immédiats contenant ce dossier. La
recherche n’est pas récursive au-delà de ce niveau.

Dans chaque sous-session, les acquisitions sont les fichiers `.fit`, `.fits`,
`.FIT` et `.FITS` directement présents dans `light/` ou `Light/`. Les flats
sont recherchés de même dans `flat/` ou `Flat/`. Les listes sont triées.

## Métadonnées et groupes

Un light doit posséder des métadonnées exploitables : date d’observation,
exposition, température, gain, type d’image, caméra et binning sur les deux
axes. Les fichiers identifiés comme darks ou bias sont ignorés dans les lights.
Les rejets sont comptés dans les statistiques de sous-session.

La clé de groupe rassemble caméra, température arrondie selon la précision
configurée (pas de **0,5 °C** par défaut), exposition, gain et binning.
`--temperature-precision` règle ce pas et la tolérance de recherche des masters
darks ; cette option est déclarée par `SequenceCalibrationProcessor`.
Chaque groupe est calibré séparément.
Le premier light du groupe sert de référence pour chercher les masters.

## Recherche du master dark

`--dark-lib` indique la bibliothèque parcourue pour trouver les masters ; son
chemin est un paramètre de `SequenceCalibrationProcessor`.
Avec les darks activés, le script parcourt récursivement la bibliothèque à la
recherche d’un FITS valide identifié comme dark et compatible avec la clé du
light. Si les deux en-têtes portent une commande `STACKCMD`, leur compatibilité
est aussi vérifiée. Le premier fichier compatible rencontré est utilisé ;
aucun classement par date ou nombre de darks n’est appliqué à cette recherche.

Sans correspondance, le groupe ne peut pas être calibré avec dark et est
signalé en échec. `--no-dark` désactive cette recherche et cette soustraction,
pour les lights comme pour les flats. Les masters sont produits par
[darkLibUpdate](../../darkLibUpdate/README.md).
Cette option est déclarée par `SequenceCalibrationProcessor` ; celui-ci résout
le mode `use_dark` et le transmet au moteur de commandes de calibration.
`LightProcessor` orchestre la recherche des masters sans recevoir ce réglage à
son instanciation.

## Sélection et préparation des flats

Les flats doivent avoir des métadonnées valides et un type contenant `flat`.
La compatibilité avec les lights porte sur la caméra, le gain arrondi et le
binning. Leur température et leur durée d’exposition n’ont pas à être celles
des lights.

Si plusieurs durées de flats existent, le sous-groupe majoritaire est conservé,
avec regroupement de l’exposition arrondie à six décimales. Un seul master dark
adapté à ces flats est recherché lorsque les darks sont actifs. Une absence de
ce dark empêche de terminer le groupe ; l’absence de flats compatibles permet
de poursuivre sans correction par flat.

Les opérations Siril sont :

```text
convert <flats> -out=<travail>/flat_process
calibrate <flats> -dark=<dark_flat> -cc=dark -cfa
stack pp_<flats> median -norm=mul -out=<master_flat>
```

Avec `--no-dark`, la commande `calibrate` des flats est omise et `stack` utilise
la séquence convertie directement. Si les lights calibrées du groupe sont
réutilisables, cette étape n’est pas exécutée. Lorsqu’une nouvelle calibration
est nécessaire, le master flat est recréé à partir des flats sélectionnés ; sa
seule présence sur disque ne déclenche pas une réutilisation indépendante.

## Calibration des lights

Les entrées du groupe sont préparées dans une séquence de travail, puis
converties dans `process/`. La commande dépend des masters disponibles :

| Calibrations | Arguments de `calibrate <lights>` avant choix du dématriçage |
|---|---|
| Dark et flat | `-dark=<dark> -flat=<flat> -cc=dark -cfa -equalize_cfa` |
| Dark seul | `-dark=<dark> -cc=dark -cfa` |
| Flat seul | `-flat=<flat> -cfa -equalize_cfa` |
| Sans dark ni flat | `-cfa` |

En mode `--drizzle off`, la commande contient aussi `-debayer`. En mode `auto`
ou `force`, cet argument est omis pour conserver le CFA natif jusqu’à la
décision de stacking. Une calibration RGB en cache peut donc nécessiter une
reconstruction si les sources sont CFA et que le mode demande le CFA natif.

## Export

Python copie les sorties `pp_light_*.fit(s)` de `process/` dans le dossier
`<sous-session>_<groupe>_calibrated`. Il exporte également leur séquence, via les
objets [SirilSequence et SequenceImage](../reference/SIRIL_SEQUENCE.md).
Cette conservation relève de
[`SequenceCalibrationProcessor.save_calibrated_sequence`](../../../../lib/light_calibration.py) :
elle copie la sélection Siril si elle existe, ou la reconstruit avec les indices
réels et le nombre de plans des FITS exportés. Les pixels sources ne sont pas
modifiés par cet export. Les copies finales restent utilisables après nettoyage
des liens de travail.

Le groupe produit des images calibrées, sans alignement ni empilement
intermédiaire. Leur sélection pour le stack est décrite dans
[la phase suivante](STACKING.md).

## Orchestration des étapes

La découverte des groupes et la sélection de leurs masters restent orchestrées
par [`LightProcessor`](../../../../lib/lightprocessor.py). L'exécution d'un
groupe est déléguée à
[`SequenceCalibrationProcessor`](../../../../lib/light_calibration.py), un
traitement fondé sur l'interface commune `processor`. Lorsqu'il reçoit des flats
compatibles, il appelle récursivement
[`MasterFlatProcessor`](../../../../lib/light_calibration.py) avant de calibrer
les lights. La préparation des séquences, l'appel Siril, l'export et le nettoyage
font partie de ces étapes.

Chaque script est soumis au service
[`Siril.run_siril_script`](../../../../lib/siril.py), qui ajoute
automatiquement `requires 1.4` et `setcpu` dans son en-tête. Cette version
minimale s’applique aussi aux scripts de calibration et de création des masters.
Un code de sortie Siril non nul déclenche une erreur fatale avec le chemin du
journal associé ; la calibration du groupe ne peut pas se poursuivre.

Les paramètres propres au parcours CLI restent déclarés par `lightProcess` ;
les classes de calibration reçoivent les réglages résolus et les données du
groupe au moment de leur exécution. Cette séparation permet d'ajouter des
comportements transverses aux traitements sans déplacer la sélection des
masters. La mesure générique de durée d'exécution n'est pas encore activée.

## Étapes dans le journal

Chaque groupe nécessitant un calcul annonce en INFO, dans cet ordre :

```text
=== Création du master flat : 100 flats — groupe <groupe> ===
Master dark des flats : <chemin>
Master flat créé : <chemin>
=== Calibration des lights avec master dark + master flat : 61 images — groupe <groupe> ===
Master dark des lights : <chemin>
Master flat des lights : <chemin>
Calibration terminée : 61 FITS calibrés dans <dossier>
```

Le titre de calibration nomme uniquement les masters effectivement utilisés.
Sans flat compatible, le journal l’indique et n’annonce pas de création de
master flat. Une reprise affiche `=== Réutilisation des lights déjà calibrées :
groupe <groupe> ===`, sans annoncer de nouveau traitement des flats ou lights.
En simulation, les titres sont précédés de `[DRY-RUN]`.

Les caractéristiques caméra, température, exposition et gain, les répertoires
d’entrée, la préparation des séquences et les détails de leur export sont
disponibles avec `--log-level DEBUG`. Les paramètres d’empilement ne sont pas
affichés comme paramètres de calibration : l’empilement intervient ensuite.
Voir [les journaux et rapports](OUTPUTS.md#lecture-des-journaux-et-rapports).
