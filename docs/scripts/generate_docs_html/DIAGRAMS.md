# Vérification du séquencement des diagrammes

[Génération documentaire](README.md)

## 1. Correspondance avec l’implémentation

Les six diagrammes Mermaid décrivent le cheminement des traitements. Les
symboles sont positionnés automatiquement ; leurs flèches restent une
description à maintenir lors d’un changement du code. Un rendu correct ne
prouve pas à lui seul la conformité du séquencement.

L’ordre des déclarations Mermaid participe au choix entre dispositions
équivalentes. Dans le choix de reprise des calibrations, la branche de
recalcul est déclarée avant celle de réutilisation : le moteur place ainsi
la sortie « Oui » à droite et la sortie « Non » à gauche. Un test de rendu du
diagramme réel vérifie ces côtés pour signaler une régression lors d’une
évolution du contenu ou du moteur. Aucune coordonnée n’est imposée.

| Diagramme de référence | Implémentation à comparer | Contrôles d’exécution |
|---|---|---|
| [lightProcess : boucles et mosaïque](../lightProcess/treatments/README.md#diagramme-boucles-lightprocess) | [lightProcess.py](../../../bin/lightProcess.py), `_process_sessions` ; [mosaic.py](../../../lib/mosaic.py), `Mosaic.post_process` et `create_mosaic` | `test_session_pipeline.py`, `test_mosaic.py`, `test_mosaic_crop.py` |
| [lightProcess : calibration](../lightProcess/treatments/README.md#diagramme-calibration-lightprocess) | [lightprocessor.py](../../../lib/lightprocessor.py), `process_session`, `process_light_group` et scripts de calibration | `test_lightprocessor_calibration.py`, `test_calibration_logging.py` |
| [lightProcess : stacking](../lightProcess/treatments/README.md#diagramme-stacking-lightprocess) | [lightprocessor.py](../../../lib/lightprocessor.py), `stack_session_outputs` ; [drizzle.py](../../../lib/drizzle.py), `run_stack` | `test_lightprocessor_calibration.py`, `test_drizzle.py` |
| [postProcess : séquence complète](../postProcess/README.md#ordre-et-circulation-des-images) | [postprocess.py](../../../lib/postprocess.py), `_PostProcessorSequence` ; [postprocessing.py](../../../lib/postprocessing.py), exports communs | `test_postprocessor.py`, `test_postprocessing.py`, `test_restoration_chains.py` |
| [Restauration : comparaison par opération](../postProcess/cosmic-clarity.md) | [restoration.py](../../../lib/restoration.py), `engines` et `post_process` | `test_restoration.py` |
| [solarEclipseGif : boucle des images et GIF](../solarEclipseGif/GUIDE.md#2-workflow-global) | [solarEclipseGif.py](../../../bin/solarEclipseGif.py), `main` et `build_movie_frames` | `test_solar_eclipse_sequence.py` |

## 2. Points à préserver

- La calibration parcourt les sous-sessions ; le stacking produit un résultat
  par session fournie. Les étapes désactivées et les reprises ne sont pas
  exécutées à nouveau.
- La mosaïque reçoit exclusivement les stacks des sessions. Elle peut corriger
  conjointement leur gradient avant l’assemblage et recadrer la sortie ensuite.
  Une mosaïque demandée mais indisponible ne provoque pas de post-traitement
  de ses panneaux en remplacement.
- La sélection et les profils stellaires précèdent les poids. Les répétitions
  de pondération sont matérialisées après l’application de l’alignement.
  Le diagnostic Drizzle précède l’arrêt dû à une sélection vide ou invalide.
- La comparaison de chaînes suit la photométrie et précède AutoGHS. Dans le
  mode par opérations isolées, la netteté précède le débruitage ; les moteurs
  d’une opération reçoivent une entrée commune.
- Les corrections solaires sont exécutées pour chaque image film avant les
  slots temporels. Les images de debug du GIF sont écrites avant le GIF final.

## 3. Vérifications reproductibles et limites

Depuis la racine du dépôt :

```bash
.venv/bin/python -m pytest -q \
  tests/test_postprocessor.py tests/test_postprocessing.py \
  tests/test_restoration.py tests/test_restoration_chains.py \
  tests/test_session_pipeline.py tests/test_lightprocessor_calibration.py \
  tests/test_calibration_logging.py tests/test_drizzle.py \
  tests/test_mosaic.py tests/test_mosaic_crop.py \
  tests/test_solar_eclipse_sequence.py
.venv/bin/python bin/build_documentation.py
```

Ces tests vérifient l’orchestration avec des données synthétiques et des doubles
pour Siril et les moteurs externes. Le test solaire complet calcule réellement
le modèle et les corrections sur cinq FITS synthétiques : un Soleil plein,
un dark et trois poses film. Il attend un GIF de 64 × 64 pixels d’une seconde
et trois PNG de debug, avec exposition et gain renseignés. Les autres scénarios
solaires imposent les détections et les déplacements pour contrôler les replis
de centrage, tout en exécutant les corrections d’image réelles.

Ces contrôles ne constituent pas une validation scientifique sur observations
réelles ni une exécution avec Siril installé. Les échecs techniques de lecture,
d’écriture ou de moteur ne sont pas tous dessinés ; leurs traitements restent
à contrôler dans les méthodes indiquées. Une limite de l’export solaire de
debug lorsque le gain manque est décrite dans le [guide](../solarEclipseGif/GUIDE.md).

<a id="croisements-et-convergences"></a>

## 4. Croisements de flèches et convergences

Un croisement de deux chemins indépendants ne crée pas de relation dans le
traitement. Une convergence vers le même symbole indique en revanche une
suite commune et peut être légitime, notamment après une alternative ou à la
fin d’une itération. Un retour de boucle ne signifie pas que ses chemins sont
exécutés simultanément.

| Diagramme | Vérification et disposition retenue |
|---|---|
| Boucles lightProcess et mosaïque | Deux croisements entre la branche sans mosaïque et les chemins de résultat/échec de mosaïque ont été supprimés. La décision de succès reste dans le groupe mosaïque ; les entrées du post-traitement sont déclarées ensemble après ce groupe. |
| Groupes de calibration | Aucun croisement transverse détecté. Les retours vers le groupe suivant sont des convergences légitimes ; le choix de reprise conserve « Oui » à droite et « Non » à gauche. |
| Détail du stacking | Les deux retours anticipés, avant et après le filtrage des structures FITS, ont chacun leur symbole de retour. Leur convergence commune n’occasionne plus le croisement des deux flèches d’entrée. |
| postProcess complet | Aucun croisement transverse détecté. Les chemins de restauration convergent légitimement vers le choix AutoGHS. |
| Comparaison des moteurs | Aucun croisement transverse détecté ; les candidats retenus et le maintien de l’entrée sont des issues alternatives. |
| solarEclipseGif | Aucun croisement transverse détecté. Les choix de centrage convergent légitimement vers le recadrage, puis la boucle poursuit avec l’image suivante. |

Dans la boucle de stacking, la reprise d’un résultat existant et un nouveau
stack réussi rejoignent le même symbole de résultat. Les chemins d’échec et
d’absence d’entrées rejoignent le choix de session suivante : ces convergences
reflètent le code et restent présentes. Aucun ordre de traitement n’a été
modifié pour améliorer le dessin et aucune coordonnée n’a été imposée.

Le test de rendu parcourt tous les blocs Mermaid sous `docs/`, puis contrôle
leurs flèches SVG dans un repère commun, y compris celles de groupes transformés.
Il recherche les intersections transverses en échantillonnant les courbes tous
les 5 points SVG, dans la limite de 250 intervalles par flèche. Il ignore les
jonctions à moins d’une unité des extrémités et les segments quasi colinéaires.
Les cas synthétiques couvrent un croisement, une convergence, un recouvrement
colinéaire et une translation qui sépare les chemins.

Le contrôle passe sans croisement transverse sur les six diagrammes actuels.
Cette approximation ne constitue pas une preuve géométrique exhaustive : les
recouvrements, contacts tangents, libellés et croisements très proches d’une
extrémité demandent aussi une inspection visuelle. Une évolution du contenu ou
du moteur peut changer les dispositions ; le test signale les nouveaux
croisements détectés pour qu’ils soient examinés avant publication.
