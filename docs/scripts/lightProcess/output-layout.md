# Arborescence numérotée des sorties

[lightProcess](README.md) › [Sorties et reprise](treatments/OUTPUTS.md)

## 1. Racine d'un objet

Une exécution produit un objet : une seule entrée hors mosaïque, ou au moins
deux entrées avec `--mosaic`. Une entrée peut toujours contenir plusieurs
sous-sessions d'acquisition. Le nom de l'objet est celui du répertoire fourni,
ou `--mosaic-name` / le nom commun calculé en mosaïque.

La racine est `<output>/<objet>/`. Si `--output` désigne déjà un dossier portant
le nom de l'objet, le nom n'est pas ajouté une seconde fois. Les six dossiers
de premier niveau sont fixes, sans préfixe d'objet répété :

```text
M31/
  index.html                      # accueil des résultats, images finales et exports
  01_calibration/
    calibration.json
    calibration.txt
    part_01/
      source.json                 # répertoire d'entrée de ce panneau
      calibration.json
      01_session/<sous-session>/..._calibrated/pp_*.fits
      stack_combined.fits
      stack_combined.drizzle.json
    part_02/
      ...
  02_mosaic/
    image.fits                    # relais linéaire commun
    image_uncropped.fits          # mosaïque avant recadrage
    mosaic.json
    mosaic.txt
    lightprocess_final_images.json
  03_photometrie/
    01_gradient/gradient.json
    photometry.json
    02_photometrie/                # scripts et journaux, sans niveau 001
  04_restoration/
    restoration.json
    restoration_summary.txt       # toutes les vérifications et le classement
    00_mask_origine/
      common_masks.fits         # entrée des premiers traitements
      common_masks.png
    01_denoise_siril/            # exemple ; ordre selon les moteurs actifs
      01_denoise_siril.json
      01_denoise_siril.fits
      01_denoise_siril_masks.fits # si ce résultat sert d’entrée ensuite
      01_denoise_siril_masks.png
      verifications_locales.txt
      verification_finale/
        verifications.txt
        verification.json
        ...                     # catalogues, scripts et journaux finaux
      ...                       # scripts, candidats et résultat de cet essai
    09_denoise_graxpert_then_sharpen_siril/  # exemple d’essai enchaîné
  05_autoghs/
    autoghs.json
    01_separation/
    02_stars/
    03_ghs/
    04_recomposition/
    05_diagnostics/
    render.fits                   # rendu GHS conservé avec la passe couleur
  06_color/
    color.json
    annotation.json
    postprocess.json              # synthèse globale des post-traitements
    postprocess_resume.json
    postprocess.log
  M31.fits                        # résultat final publié
  M31.jpg                         # si un rendu JPEG a été produit
  M31_annotated.png               # si l'annotation a pu être effectuée
```

Les rapports d’étapes et la synthèse globale ont une synthèse `.txt` voisine ;
les audits techniques internes peuvent rester uniquement en JSON. Les moteurs créent
leurs sous-répertoires de travail dans l'étape correspondante. Les cinq
sous-étapes AutoGHS sont directement dans `05_autoghs/`, sans enveloppe
`autoghs_work/`. Les dossiers des étapes désactivées
peuvent rester vides. Le JPEG et le PNG dépendent des étapes actives et de
leurs prérequis ; ils ne sont pas inventés lorsqu'aucun rendu n'est disponible.

## 2. Panneaux et sous-sessions

Lors du premier traitement, les entrées reçoivent `part_01`, `part_02`, etc.
Leur identité est conservée dans `source.json`, créé avant la calibration.
Une nouvelle commande qui réordonne les mêmes entrées retrouve les mêmes
numéros, pour éviter de réutiliser la calibration d'un autre panneau.
Une nouvelle entrée reçoit un numéro libre. Un ancien panneau absent de la
commande reste sur disque mais ne contribue pas à la nouvelle mosaïque.
Le rapport global `01_calibration/calibration.json` référence uniquement les
entrées de cette exécution, leurs sous-sessions, sorties et statistiques WCS.

Chaque panneau produit au plus un stack. Les acquisitions calibrées restent
sous son dossier `01_session/`. Une entrée avec une seule pose peut être
transmise sans empilement supplémentaire ; elle est néanmoins copiée dans
le relais commun lorsqu'il s'agit du résultat final hors mosaïque.

## 3. Un champ sans mosaïque

Pour M33, les calibrations et le stack sont directement sous
`M33/01_calibration/`, sans `part_XX`. Le dossier `02_mosaic` existe aussi :
`image.fits` est une **copie indépendante** du produit calibré/empilé, avec
ses pixels, en-tête et date conservés. `mosaic.json` indique `assembled=false`
et référence explicitement la source. Aucun assemblage n'est exécuté.

Le passage vers la photométrie est ainsi toujours `02_mosaic/image.fits`.
L'annotation retrouve les comptes de poses dans le rapport du stack d'origine ;
la copie ne transforme pas des répétitions de pondération en nouvelles poses.
Une pose unique sans rapport de stack garde des comptes inconnus.

```bash
./bin/lightProcess.sh /donnees/M33 --output /resultats --work-dir /travail
```

Pour une mosaïque :

```bash
./bin/lightProcess.sh /donnees/M31_nord /donnees/M31_sud \
  --mosaic --mosaic-name M31 --output /resultats --work-dir /travail
```

Ces commandes requièrent les [acquisitions](SESSIONS.md) et dépendances réelles.
Les parcours et les chemins sont vérifiés par les tests avec moteurs simulés.

## 4. Travail, reprises et compatibilité

Les temporaires de calibration et de stacking suivent le même regroupement
sous `<work-dir>/<objet>/01_calibration/`, avec les mêmes panneaux. Les acquisitions sont sous `01_session/`. Les étapes du stack sont directement
au même niveau : `02_registration/`, `03_quality/`,
`04_drizzle/` et `05_stacking/`, avec `stacking.log` à leur racine. Les calculs de mosaïque,
leurs gradients et leurs fichiers Siril sont sous le dossier de sortie
`02_mosaic`, conformément au relais visible. `--output` est donc respecté aussi
en mosaïque. `--keep-intermediate` conserve ses règles de nettoyage.

La photométrie inclut la correction initiale de gradient dans son sous-dossier
`01_gradient/`. Si la comparaison de chaînes de restauration est désactivée,
les moteurs sont rangés sous `04_restoration/01_deconvolution/` et
`04_restoration/02_denoise/`. La comparaison complète utilise
`04_restoration/`, avec un dossier direct par opération, sans
suffixe `_work` ni niveau `001` ; voir les [chaînes de restauration](../postProcess/quality.md).

`--autoghs-only` et `--color-only` retrouvent le relais avec le manifeste situé
sous `02_mosaic`, puis les rapports et snapshots sous `06_color`. La reprise
couleur conserve les couches GHS sous `05_autoghs`. La mesure, l'ordre et les
paramètres scientifiques des traitements ne changent pas avec cette organisation.
Un appel à `postProcess` sur ce relais reconnaît l'arborescence ; il en est de
même si son répertoire de sortie explicite désigne cette racine numérotée.
Les appels autonomes avec d'autres entrées/sorties gardent leur convention
historique, ainsi que les appels directs à l'API sans contexte numéroté.

Les anciennes sorties ne sont ni déplacées ni réécrites automatiquement.
Les manifestes et rapports historiques explicites restent reconnus en reprise.
Un nouveau traitement complet écrit la nouvelle arborescence et ne réutilise
pas implicitement une calibration trouvée dans une ancienne organisation.
Les reprises vérifient toujours les mêmes sources et le même mode d'assemblage.
Changer la source d'un champ simple sous la même racine est refusé tant que
l'identité précédente subsiste ; une purge explicite permet un nouveau départ.

`--purge-target` porte sur les racines de cet objet, sortie et travail, et refuse
une racine qui recouvre les données d'entrée. En `--dry-run`, aucune purge ni
étape de calcul n'est exécutée ; des journaux de simulation peuvent être créés.

## 5. Bibliothèques et vérifications

`OutputLayout` (`lib.output_layout`) calcule les chemins, conserve les identités
de panneaux et réalise la copie du relais ; il ne lance aucun moteur d'image.
`LightProcessor` reçoit explicitement les répertoires de calibration et
`PostProcessingProcessor` dirige ses moteurs vers leurs rapports d'étapes.
`PostProcessResume` lit les manifestes et `StackingAnnotation` suit la provenance
du relais pour conserver les comptes de poses et de panneaux.

Les tests vérifient copies physiques et en-têtes, absence de mutation des
sources, panneaux réordonnés, reprises GHS/couleur, étapes alternatives,
synthèses localisées, noms génériques et rejet de plusieurs entrées sans
mosaïque. Ces tests ne remplacent pas une nouvelle exécution avec Siril sur
une mosaïque réelle ; les données M31/M33 existantes n'ont pas été déplacées.

Chaque niveau dispose d’un `index.html` avec audits et aperçus JPEG/PNG.
Voir les [index HTML et leur régénération](html-reports.md).
