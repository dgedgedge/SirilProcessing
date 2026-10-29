# Couleur finale adaptative après GHS

[postProcess](README.md) › [AutoGHS](auto-ghs.md) › Couleur finale

## 1. Rôle et activation

`FinalColorProcessor` (`lib/final_color.py`) est un traitement `processor`
indépendant, exécuté par `PostProcessingProcessor` après AutoGHS et avant les
exports et l'annotation. Il renforce la couleur du **fond sans étoiles déjà
étiré**, puis le recompose avec la même couche stellaire rendue. Les étoiles
ne reçoivent pas de nouvelle passe de saturation.

L'étape est **désactivée par défaut**. Aucun réglage ne dépend du nom de la
cible. La force est modulée par le signal, la cohérence chromatique, la couleur
déjà présente, les hautes lumières et la marge disponible dans le gamut RGB.

La reprise seule utilise les dépendances Python communes (NumPy, SciPy,
Astropy, Pillow et Matplotlib pour l'annotation), sans lancer Siril ni StarNet.
CuPy est facultatif pour CUDA. Voir l'[installation commune](../../INSTALLATION.md)
et l'[installation du venv CUDA](../install_cuda/README.md).

| Option | Défaut | Rôle |
|---|---|---|
| `--enable-final-color` | désactivé | Activer la passe dans le traitement complet ou la reprise AutoGHS |
| `--disable-final-color` | — | Désactiver explicitement la passe |
| `--final-color-strength` | `3.0` | Excès de gain maximal dans `[0,3]` ; 1 autorise au plus ×2, 3 au plus ×4 ; zéro donne l'identité |
| `--color-only` | désactivé | Activer la couleur pour cet appel et reprendre directement les couches GHS existantes |

Les choix d'activation et la force suivent CLI > JSON > défaut et sont
mémorisables avec `-S`. Les options `only` indiquent le **point de reprise** :
`--autoghs-only` relance StarNet/GHS puis la saturation, les exports et l'annotation ;
`--color-only` reprend à la saturation puis poursuit les exports et l'annotation.
La reprise GHS active la saturation pour cet appel même si le JSON la désactive ;
ajouter `--disable-final-color` à la commande pour l'écarter explicitement.
Cette activation temporaire ne modifie pas les paramètres mémorisés.
Les deux modes de reprise sont non mémorisables et mutuellement exclusifs.

## 2. Commandes

Activer la passe dans un traitement complet :

```bash
bin/postProcess.sh image_RGB.fits resultats/ --enable-final-color -S
```

Relancer seulement la couleur avec la **même entrée initiale et le même dossier
de sortie** que lors du traitement précédent :

```bash
bin/postProcess.sh image_RGB.fits resultats/ --color-only
bin/postProcess.sh image_RGB.fits resultats/ --color-only --final-color-strength 1.5
```

StarNet, GHS, la restauration et les traitements linéaires ne sont pas relancés.
Les couches référencées par le rapport précédent sont réutilisées, pas l'image
déjà colorée. Des reprises identiques donnent donc le même résultat sans cumul.
Les intermédiaires linéaires préparés peuvent avoir été supprimés ; l'entrée
initiale, le rapport et les couches GHS/étoiles doivent rester disponibles.

Désactiver l'étape pour les prochains traitements :

```bash
bin/postProcess.sh image_RGB.fits resultats/ --disable-final-color -S
```

Pour réexporter le rendu GHS sans renforcement supplémentaire, sans recalculer
GHS, utiliser `--color-only --final-color-strength 0`. Le fond est alors conservé
et recomposé avec les étoiles. Pour refaire également GHS, employer
`--autoghs-only`.

Les mêmes options sont disponibles depuis `lightProcess.sh`. Ajouter
`--color-only` à la commande d'origine, avec les mêmes sessions et options de
mosaïque : les stacks finaux sont retrouvés dans leur manifeste, sans relancer
calibration, stacking ou mosaïque.

## 3. Entrées, provenance et sorties

La reprise utilise `autoghs.strategies.range.starless_image` et
`autoghs.stars.output_image` du rapport global. Les deux FITS doivent être RGB,
normalisés, finis, de même géométrie et WCS, marqués `SPNLIN=True` et avec les
rôles `STARLESS` / `STARS`. La reprise valide la provenance de l'entrée initiale
lorsqu'elle existe et les snapshots des couches enregistrés par la passe couleur.
Un historique absent, incomplet ou des couches modifiées provoquent une erreur
avant réinitialisation du dossier couleur. Les anciens rapports contenant les
couches explicites restent utilisables, sans pouvoir certifier rétroactivement
leur provenance avant la première passe couleur.

Le rapport `06_final_color.json` et `06_final_color_work/001/` contiennent le fond
coloré, le FITS recomposé et son JPEG. Les sources GHS et stellaires ne sont pas
modifiées. Lorsque la passe couleur est exécutée, les exports sont distincts :

- `<image>_postprocess_ghs.fits/.jpg` : rendu GHS avant saturation ;
- `<image>_postprocess_saturated.fits/.jpg` : résultat de la passe couleur ;
- `<image>_postprocess_saturated_annotated.png` : annotation du résultat final.

Le JPEG GHS est conservé lorsqu'il est disponible dans le rapport précédent.
Ces noms s'appliquent aussi à `--color-only` et à une force nulle. Sans passe
couleur, les noms historiques `<image>_postprocess.fits/.jpg` restent utilisés.
Les métadonnées et le WCS sont conservés. Aucun comparatif automatique n'est ajouté.

L'audit contient la force demandée et effectivement essayée, les gains maximal
et médian du diffus, le gain du ciel, les seuils, le backend, l'écart de luminosité,
les gardes de fusion et les snapshots des couches. Le journal INFO résume le
statut et les gains ; les détails restent dans le JSON.

## 4. Algorithme et limites

Le traitement conserve la luminosité liée au mode GHS (`human` par défaut,
`Y=0,2126R+0,7152G+0,0722B`, ou moyenne en mode `even`). Pour chaque pixel :

```text
RGB' = RGB + (g − 1) × (RGB − Y)
g = 1 + force × signal × cohérence × protection_hautes_lumières × vibrance
```

Le gain commun conserve Y et la direction RGB par rapport à cet axe gris. Ce
n'est pas une conservation perceptuelle calibrée de teinte/saturation : les
primaires du FITS ne sont pas supposées connues.

Une gaussienne, avec σ égal à la plus petite dimension / 768, borné à `[1,6]`
pixels, sert seulement à estimer les poids. Le gain s'applique aux pixels RGB
initiaux ; aucune image lissée ne remplace le signal final.

- Le ciel indicatif est le quartile inférieur des Y strictement positifs.
  Ses pixels sont inchangés. Le signal ailleurs suit un raccord progressif
  au-dessus du fond médian + trois fois le proxy de bruit. Ce proxy utilise
  la MAD normalisée des différences horizontales du ciel, divisée par √2.
- La cohérence utilise les écarts chromatiques RGB lissés, normalisés par Y,
  relativement à la couleur médiane du ciel. La dispersion du ciel est une
  MAD normalisée, avec un plancher de `10⁻⁵`. Le poids progresse entre un et
  trois fois ce proxy. Il ne constitue pas un SNR physique ni une preuve de
  couleur astrophysique ; les gradients et le bruit corrélé peuvent le biaiser.
- La vibrance vaut `1 / (1 + (C/0,025)²)`, avec
  `C=(max(RGB_lissé)−min(RGB_lissé))/sum(RGB_lissé)`. Les couleurs déjà fortes
  reçoivent donc moins de gain. Ce seuil est une heuristique de présentation.
- Les hautes lumières sont progressivement protégées de P98 à P99,9 ; les
  pixels au-dessus de P99,9 restent inchangés. Les masques sont automatiques.
- Le gain est limité analytiquement avant qu'un canal sorte de `[0,1]`, avec
  une marge float32, sans écrêtage. Une image grise reste grise. Les références
  constantes ou insuffisamment mesurables sont conservées.

La fusion screen utilise les étoiles, le mode de luminosité et la marge
enregistrés par GHS. Elle peut légèrement changer la luminosité finale malgré
la conservation de Y du fond ; l'écart maximal admis est de 0,01 unité `[0,1]`.
Les gardes de couleur et largeur stellaires communes sont également appliquées.
En cas de rejet, la force est divisée par deux, puis par quatre ; l'identité est
ensuite essayée. Si même la référence échoue, le traitement produit une erreur.

Lors d'un traitement complet ou d'une reprise AutoGHS avec la couleur activée,
le renforcement `--ghs-chroma` avant GHS est remplacé par cette étape pour éviter
le cumul. La configuration mémorisée n'est pas réécrite implicitement. En
reprise `--color-only`, un renforcement déjà présent dans les anciennes couches
ne peut pas être retiré : sa valeur figure dans le rapport global et le journal.
Pour le remplacer, relancer une fois `--autoghs-only`.

## 5. Ressources, interfaces et vérification

`render(data, strength, color_mode, backend)` expose le calcul sans fichiers.
`post_process(fond, rapport, args, stars_path=..., headroom=..., color_mode=...)`
expose le moteur autonome ; sans étoiles, il produit un fond coloré seulement.
Le backend commun NumPy/CuPy est sélectionné avant calcul, avec un budget estimé
à douze fois la taille RGB pour les temporaires et sans bascule après démarrage.
La fusion et ses diagnostics utilisent les fonctions communes sur CPU.

`tests/test_final_color.py` couvre luminosité, direction RGB, gamut, ciel,
cœur, gris, paramètres, ordre des étapes, activation/désactivation, absence de
cumul, restitution par force nulle, provenance, configuration et parité CPU/CUDA
lorsque le matériel est disponible. Les tests de sessions vérifient la reprise
depuis lightProcess, y compris en mosaïque. Les essais M31/M33 réutilisent les
couches réelles existantes, avec sorties distinctes ; ils ne relancent pas Siril
et ne constituent pas une qualification sur tous les types de champs.
