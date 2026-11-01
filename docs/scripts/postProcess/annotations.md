# Annotation finale et informations de prise de vue

[postProcess](README.md) › Annotation finale

## 1. Utilisation et sorties

Après les traitements et la publication du rendu final, `postProcess` et
l'enchaînement depuis `lightProcess` produisent par défaut une copie PNG avec un
encart en bas à gauche et les noms des objets du ciel profond. Le FITS et le JPEG sans annotation restent disponibles.
Aucun étirement supplémentaire n'est appliqué. Le PNG garde les dimensions et
l'orientation du rendu ; seuls les pixels des annotations changent.

L'option `--final-annotation` est active par défaut.
`--no-final-annotation` désactive cette dernière étape. Le choix suit la priorité
CLI > configuration JSON > défaut et peut être mémorisé avec `-S`.

Pour un rapport `image_postprocess.json`, les produits supplémentaires sont :

- `image_postprocess_annotated.png` : image de présentation ;
- `image_postprocess_annotation.json` : statut, textes affichés, rectangle du
  panneau en pixels écran et provenance des métadonnées ;
- la clé `annotation` du rapport global reprend cet audit.

Le résultat retourné par `PostProcessingProcessor` expose aussi
`output_annotated_image` lorsque le PNG a été produit. `output_image` continue
de désigner le FITS scientifique.

## 2. Informations affichées et limites

Le panneau utilise les cartes présentes dans l'en-tête du FITS final :
`DATE-OBS`, `TELESCOP`, `INSTRUME`, `FILTER`, `EXPTIME` (secondes), `GAIN`,
`OFFSET` et `CCD-TEMP` (degrés Celsius). La date est reproduite sans inventer de
fuseau horaire. Les champs absents, vides et les nombres non finis sont omis.
Un en-tête sans ces informations affiche « Informations non renseignées ».
La ligne « Objet » est omise : les noms des objets sont déjà placés sur l'image.
La carte FITS `OBJECT` reste inchangée.

L'encart commence directement par les métadonnées disponibles, sans ligne de
titre. En particulier, une mosaïque peut
hériter des valeurs d'un seul panneau. `EXPTIME` est donc présenté comme
« Pose de référence » ; `STACKCNT` et `LIVETIME` ne sont pas utilisés pour
prétendre connaître le nombre total de poses ou l'intégration de la mosaïque.

Les comptes d'empilement sont affichés séparément à partir des rapports du
stack ou de la mosaïque **d'origine**, et non de cet en-tête de référence :

```text
Images empilées (retenues/total) : 164/313
```

Pour une mosaïque :

```text
Mosaïque : 3 panneaux
Images empilées par panneau (retenues/total) :
P1 · M_31-Part_1 : 8/10
P2 · M_31-Part_2 : 8/10
P3 · M_31-Part_3 : 5/10
```

`xx/yy` signifie **poses indépendantes effectivement retenues / poses
indépendantes proposées au moteur de stacking**, après les préparations de
calibration et de compatibilité. Ce n'est pas nécessairement le nombre de
fichiers bruts acquis. Les répétitions introduites par la pondération ne
comptent pas comme des poses supplémentaires. Le nombre de panneaux vient
de la liste des images de la mosaïque, dans son ordre de construction.
Ces nombres ne sont pas convertis en durée totale ni en couverture commune.

`lib.stacking_annotation.StackingAnnotation` consulte le rapport voisin
`<stack>.drizzle.json` terminé : `metadata.unique_inputs` fournit le dénominateur
et `final_selection.independent_retained` le numérateur. Pour une mosaïque,
`<mosaïque>.json` doit référencer son `output_image` et ses `input_files` ;
chaque panneau fournit son propre rapport d'empilement. Les chemins relatifs
sont résolus par rapport au rapport qui les contient, les liens symboliques
sont résolus et l'identité du produit est contrôlée.

Un rapport étranger, incomplet, non terminé, supérieur à 32 Mio ou antérieur à
une modification du FITS n'est pas utilisé. Un panneau réempilé après le rapport
de mosaïque ne fournit pas non plus de compte à cette ancienne mosaïque.
Ces contrôles de chemins et dates ne prouvent pas une intégrité cryptographique.
Sans provenance exploitable, aucun compte de stack n'est inventé ; pour un
panneau manquant, la ligne indique `inconnu`. L'annotation reste produite.
Le sous-rapport `stacking` conserve les comptes, les rapports sources, les
motifs d'indisponibilité et le statut `AVAILABLE`, `PARTIAL`, `UNAVAILABLE` ou
`INVALID`. Les rapports de stacking/mosaïque doivent donc rester disponibles.

Les noms sont issus du catalogue local [OpenNGC](https://github.com/mattiaverga/OpenNGC)
(Mattia Verga et contributeurs, CC BY-SA 4.0). L'encart affiche :
« Catalogue des objets : OpenNGC — Mattia Verga et contributeurs (CC BY-SA 4.0) ».
L'extrait livré comprend 12 159
entrées NGC/IC et complémentaires : galaxies, amas et nébuleuses ; il exclut les
doublons, objets inexistants, étoiles simples/doubles, novae et entrées
indéterminées. Sa [provenance et sa licence](../../../data/openngc/README.md)
sont distinctes de la GPL du code. Aucun téléchargement n'est fait à l'exécution.

Le WCS céleste du FITS final, y compris les distorsions SIP et son référentiel,
projette les coordonnées sur l'image. L'ordre des lignes est ensuite appliqué
comme pour le JPEG. Seuls les objets dont le centre est dans le champ sont
éligibles. Les noms Messier sont prioritaires, puis les objets les plus brillants.
Il s'agit de positions cataloguées, sans détection ni affirmation de visibilité.

| Option | Défaut | Effet |
|---|---|---|
| `--annotation-objects` / `--no-annotation-objects` | actif | Afficher les noms, indépendamment de l'encart |
| `--annotation-magnitude` | `14` | Magnitude V sinon B maximale, entre 0 et 25 ; les Messier restent éligibles |
| `--annotation-max-objects` | `40` | Plafond de noms, entre 0 et 200 |

Une magnitude inconnue exclut l'objet, sauf s'il possède un alias Messier.
Huit positions d'étiquette sont essayées autour de chaque centre. Les textes
évitent l'encart, les autres textes, les centres des objets et les rectangles
des segments de rappel. Les noms sans place sont omis et audités. La disposition
reste automatique après toute évolution de l'image ou du champ.

Sans WCS exploitable, l'encart est produit et le sous-rapport `objects` indique
`UNAVAILABLE` et sa raison. Aucune position n'est déduite du seul nom de cible.
L'audit conserve les noms placés, leurs coordonnées célestes et pixels écran,
les rectangles des textes, les omissions et les crédits du catalogue.

## 3. Module et présentation

`lib.final_annotation.FinalImageAnnotator` implémente le contrat `processor`.
`post_process(fits_final, rapport_json, args, acquisition_source=stack_initial)` est appelé en dernier par
`PostProcessingProcessor`, après les exports scientifiques. `acquisition_lines`
isole la lecture des métadonnées ; `draw_panel` et `draw_objects` réalisent la
composition Pillow. `lib.annotation_catalog.AnnotationCatalog` charge les
coordonnées et les projette avec Astropy, indépendamment de la présentation.
Un préfiltre sphérique autour du centre du champ réduit les projections WCS ;
les centres hors de l’image sont ensuite exclus en pixels.
`StackingAnnotation.describe` isole la lecture de provenance des comptes.
Le paramètre `acquisition_source` est transmis par l'orchestrateur, également
en reprise GHS/couleur. Sans ce paramètre, l'annotateur autonome consulte les
rapports associés au FITS qu'il reçoit ; il ne recherche pas un fichier par
supposition à partir du nom d'un export final.


La taille de police dépend de la plus petite dimension de l'image. Le texte
revient automatiquement à la ligne et la police diminue si nécessaire pour
maintenir l'encart dans le champ. Le fond sombre translucide améliore la lecture.
DejaVu Sans est fourni par Matplotlib, déjà requis ; aucune fonte système ni
connexion réseau n'est nécessaire. Le PNG est sans pertes, mais contient le
même RGB de présentation quantifié sur 8 bits que l'export JPEG.

Une image encore linéaire (`SPNLIN` absent ou faux) ou de moins de 96 pixels
sur un côté produit un statut `SKIPPED`, avec la raison, sans inventer de rendu.
La désactivation produit `DISABLED`. Un rendu invalide ou une erreur de lecture
ou d'écriture fait échouer le post-traitement ; les fichiers déjà écrits restent
sur disque. Les sources ne peuvent pas être remplacées par les sorties.

## 4. Vérification

`tests/test_final_annotation.py` vérifie le placement, les retours à la ligne,
l'orientation, la conservation exacte des pixels extérieurs au panneau et du
FITS source, les informations absentes, les poses de référence, les cas ignorés
et les priorités de configuration. `tests/test_postprocessing.py` couvre
l'intégration commune aux deux points d'entrée.
Il vérifie que les compteurs viennent du stack initial même lorsque le FITS
rendu ne porte pas ces informations. `test_stacking_annotation.py` couvre les
comptes indépendants des pondérations, l'ordre des panneaux, les rapports
absents/incohérents/périmés et les listes répétées ou trop volumineuses.
Les exemples 164/313 et 8/10, 8/10, 5/10 ont été vérifiés dans les rapports
existants M33 et M31 ; aucun nom d'objet n'intervient dans le calcul.


`tests/test_annotation_catalog.py` vérifie les identifiants de M31/M32/M110,
les rotations WCS, le passage RA=0, les orientations, les seuils de magnitude,
le placement sans chevauchement et le catalogue invalide.

Les contours des objets utilisent les diamètres majeur et mineur d'OpenNGC
(en minutes d'arc) et leur angle de position, du nord vers l'est. Les demi-axes
sont projetés par le WCS céleste en 96 segments : rotation, échelle, distorsion
et orientation des lignes sont respectées. Leur taille ne dépend pas de la
police. Sans dimensions ou orientation suffisantes, une croix marque seulement
le centre ; aucune taille n'est inventée. Les dimensions cataloguées décrivent
une emprise indicative, dont le contour visible dépend aussi du traitement et
de la profondeur de l'image. Les portions hors champ sont coupées à l'affichage.
L'audit conserve les dimensions, l'angle et les points du contour projeté.

Les étiquettes affichent le nom usuel OpenNGC lorsqu'il existe, suivi des
identifiants Messier/NGC sur une seconde ligne. Les traductions françaises
livrées couvrent actuellement la Galaxie d’Andromède (M31), la Galaxie du Triangle
(M33) et la Nébuleuse d’Orion (M42). Pour les autres objets, le premier nom usuel
original est conservé ; à défaut de nom usuel, seuls les identifiants apparaissent.
Les alias originaux et le nom français éventuel sont conservés dans l'audit.

Le journal INFO indique seulement le chemin de l'image annotée, ou le statut
si aucune image n'est produite. Le rapport détaillé (métadonnées, objets et
points des contours) est journalisé en DEBUG et conservé dans le rapport JSON.
