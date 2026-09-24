# Alignement et empilement final

[Documentation](../../../README.md) › [lightProcess](../README.md) › [Traitements](README.md)


## Entrées compatibles

Le stack final rassemble les FITS calibrés des sous-sessions d’une même session.
Le chemin CLI déduplique les chemins recueillis. Les fichiers disparus sont
écartés avant la préparation du stack.

Le groupe majoritaire de même structure FITS est conservé : nombre de canaux,
largeur, hauteur et `BITPIX`. Les structures différentes et les fichiers
illisibles sont comptés séparément. Si aucun fichier n’est lisible, ce contrôle
ne réduit pas les entrées ; la lecture ultérieure peut alors échouer.

Une seule entrée compatible est retournée directement, sans exécuter les
étapes de mesure, de filtrage ou d’empilement qui suivent.

## Répertoires et étapes

| Dossier sous `stacking/` | Traitement |
|---|---|
| `00_inputs/` | Liens vers les poses calibrées compatibles, copie de secours si le lien échoue |
| `01_registration/` | Conversion, détection des étoiles, astrométrie et alignement initial |
| `02_quality/` | Copie indépendante des séquences, sélection, contrôle des profils et pondérations |
| `03_capability/` | Vérification Siril si Drizzle est choisi, sinon `SKIPPED.txt` |
| `04_stacking/` | Application des transformations et empilement |

L’alignement initial est calculé sur toutes les entrées compatibles, avant
rejet de qualité et avant répétitions de pondération :

```text
convert <session>_ -out=<01_registration>
seqfindstar <session>_
seqplatesolve <session>_ -force -nocache -disto=ps_distortion
register <session>_ -2pass -transf=<transformation>
```

L’astrométrie dépend du réglage `enable_stack_platesolve` et la transformation
est `affine` par défaut. Les mesures et matrices du `.seq` sont lues par le
modèle objet de séquence ; elles ne sont pas recalculées par Python.

## Sélection et pondération

Dans `02_quality`, les contrôles géométriques, la validité des mesures, les
filtres FWHM, la rondeur et le nombre d’étoiles précèdent la mesure des profils
stellaires. Les seuils utilisent les poses indépendantes. Les répétitions
FWHM/rondeur sont calculées après cette sélection, mais les répétitions sont
créées seulement après le dernier rééchantillonnage, juste avant le stack.
Elles utilisent des liens vers les FITS alignés et conservent l’association aux
images sources : une pose n’est dématricée ou rééchantillonnée qu’une fois par étape.

Les valeurs par défaut, formules et bilans par critère sont centralisés dans
[la référence de sélection](../filter/stacking/IMAGE_SELECTION.md). Le contrôle
[R80 et allongement](../filter/stacking/STELLAR_PROFILE_FILTER.md) est appliqué
aux pixels des seules poses restantes.

## Choix Drizzle et branche standard

L’analyse du dithering, de la couverture, de l’échantillonnage et des ressources
porte sur les poses sélectionnées. Les modes `off`, `auto` et `force` sont
décrits dans [la référence Drizzle](../filter/stacking/DRIZZLE_SPECIFICATION.md).

Lorsque Drizzle est actif, les transformations initiales contrôlées sont
appliquées avec ses paramètres. Dans la branche standard, une source CFA est
dématricée puis réalignée dans `04_debayer_registration.sps`. Le `.seq` produit
est contrôlé avant d’appliquer cette transformation. La séquence de travail est
réduite aux poses indépendantes retenues avant le dématriçage ; les fichiers
rejetés ne sont donc pas traités, même si Siril ignore les exclusions lors de
`calibrate`. Le dématriçage utilise au plus quatre threads pour limiter les
allocations simultanées. Cette limite est locale au processus de cette étape.

Le réalignement robuste, actif si le réglage Python `robust_realign` n’est pas
désactivé, applique un premier alignement puis recalcule les transformations
dans `04_realign.sps`. Un nouveau contrôle géométrique précède le dernier
rééchantillonnage. Les seuils de qualité ne sont pas réappliqués à ces étapes.
Une entrée déjà exclue ne peut pas être réintroduite par un nouvel alignement.

## Commande d’empilement

`04_applyreg.sps` applique l’alignement avec `seqapplyreg -filter-included` aux
poses indépendantes. Python ajoute ensuite les répétitions de pondération par
liens symboliques, selon les poids initiaux (sans recalcul après les rejets
supplémentaires). `04_stacking.sps` empile les entrées incluses de cette séquence.
Le cadrage est `min`
pour toutes les méthodes, avec ou sans Drizzle : Siril recadre les poses
sélectionnées sur leur champ commun à partir des données d’alignement,
avant l’empilement. Ce cadrage s’applique aussi au premier rééchantillonnage
du réalignement robuste, pour éviter de conserver les bordures du champ étendu.

Le recadrage est donc assuré par Siril via
`seqapplyreg <séquence> -filter-included -framing=min`. Il n’y a pas d’étape
de crop Python après le stack. Les images rejetées avant chaque application
des transformations ne participent pas à son cadrage. Un rejet ultérieur ne
peut pas restituer une zone déjà retirée lors d’un cadrage précédent.
La mosaïque optionnelle conserve son cadrage `max`, adapté à l’assemblage
de champs différents.

| Choix | Commande finale construite |
|---|---|
| `--stack-method median` | `stack <alignée> median -output_norm -out=<sortie> -filter-included` |
| Méthode non médiane, rejet `none` | `stack <alignée> mean -output_norm -out=<sortie> -filter-included` |
| Méthode non médiane, autre rejet | `stack <alignée> rej <bas> <haut> -output_norm -out=<sortie> -filter-included` |

La CLI accepte `sum`, mais le constructeur du stack final n’a pas de branche
somme distincte : il suit le chemin non médian du tableau. De même, les noms
de rejet autres que `none` utilisent tous la forme `rej` avec les deux seuils
fournis. Cette page décrit les commandes construites, pas une différenciation
que le code n’effectue pas.

Le succès demande que Siril termine correctement et que le FITS final existe.
Le rapport est alors marqué `completed` ; les échecs conservent leurs raisons.
Voir [les sorties et la reprise](OUTPUTS.md).
