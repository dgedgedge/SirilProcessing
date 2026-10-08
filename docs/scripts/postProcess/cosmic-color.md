# Modes couleur Cosmic Clarity

[Post-traitement](README.md) · [Cosmic Clarity](cosmic-clarity.md)

## Référence et périmètre

Les modes sont adaptés des scripts MIT de Franklin Marek à la révision
`423acc442724872ff6d81011962a8cbf5744a7f7` :
[netteté AI3.5](https://github.com/setiastro/cosmicclarity/blob/423acc442724872ff6d81011962a8cbf5744a7f7/SetiAstroCosmicClarity.py)
et [débruitage AI3.6](https://github.com/setiastro/cosmicclarity/blob/423acc442724872ff6d81011962a8cbf5744a7f7/SetiAstroCosmicClarity_denoise.py).
Les valeurs par défaut des modes correspondent à leur usage en ligne de
commande, pas à toutes les variantes des interfaces graphiques ou de Suite Pro.

`lib/cosmic_clarity/color.py` fournit les conversions et le filtre de
chrominance. `inference.py` applique le prétraitement, les réseaux et la
recomposition ; `processors.py` expose les réglages et conserve la comparaison
Siril/Clarity. Les poids, leurs empreintes et l’architecture ne changent pas.

## Modes et réglages

| Opération | Option | Défaut | Autres modes |
|---|---|---|---|
| Netteté | `--cosmic-sharpen-mode` | `luminance` | `separate` |
| Débruitage | `--cosmic-denoise-mode` | `luminance` | `full`, `separate` |
| Chrominance en `full` | `--cosmic-color-denoise-amount` | valeur de `--cosmic-denoise-amount` | nombre de 0 à 1 |

Ces paramètres sont mémorisables avec `-S`, avec priorité CLI > configuration
JSON > défauts. Une intensité couleur explicite égale à zéro conserve Cb/Cr en
mode `full` ; elle n’empêche pas le traitement de Y. Inversement, une intensité
neuronale nulle laisse disponible le lissage de chrominance en mode `full`.

- **Luminance** : extrait Y, traite uniquement Y par réseau, puis recompose
  avec les Cb/Cr du RGB préparé. Les deux opérations l’utilisent par défaut.
- **Separate** : traite chaque canal R/V/B individuellement. Cela correspond
  à l’option de canaux séparés de l’auteur.
- **Full** : débruite Y par réseau, puis lisse Cb et Cr par un filtre guidé
  avec Y avant débruitage comme guide. Ce mode concerne uniquement le débruitage.
  Il ne transmet pas un RGB couleur au réseau.

Pour une image mono, tous les modes débruitent le seul plan ; aucun canal de
chrominance n’est créé. Les réseaux reçoivent chaque plan dupliqué en trois
canaux et l’inférence récupère le premier canal de sortie, comme dans les
scripts de l’auteur. La netteté reste séquentielle : stellaire, puis non
stellaire, avec mélange entrée/prédiction et interpolation du rayon.

## Prétraitement et recomposition

Les FITS sont convertis en float32 dans une échelle normalisée, comme décrit
sur la [page Cosmic Clarity](cosmic-clarity.md#modules-et-algorithme).
Sur l’image normalisée `I`, le minimum `m0` est commun à tous les canaux.
La décision d’étirement est globale : `median(I - m0) < 0,08` pour la netteté,
`< 0,05` pour le débruitage. Si elle est vraie, chaque canal est décalé de
`m0` et sa médiane `m` est déplacée vers `t = 0,25` par :

`T(x; m, t) = t (m - 1) x / [m (t + x - 1) - t x]`.

Les médianes dégénérées (hors de `]0,1[`) ne subissent pas cette transformation.
Après traitement et recomposition, la médiane courante de chaque canal est
ramenée à sa médiane initiale, puis le minimum commun est réintroduit avec
écrêtage dans `[0,1]`. La translation et l’échelle de normalisation communes
sont ensuite inversées pour les entrées flottantes.

Les conversions emploient directement les coefficients YCbCr BT.601 de
l’auteur sur les pixels préparés, sans correction gamma supplémentaire :

```text
Y  =  0,299 R + 0,587 G + 0,114 B
Cb = -0,168736 R - 0,331264 G + 0,5 B + 0,5
Cr =  0,5 R - 0,418688 G - 0,081312 B + 0,5
```

Pour la recomposition, Cb et Cr sont décalés de `−0,5` puis :

```text
R = Y + 1,402 Cr
G = Y - 0,344136 Cb - 0,714136 Cr
B = Y + 1,772 Cb
```

Y et les Cb/Cr décalés sont écrêtés dans `[0,1]` avant conversion, puis le RGB
est écrêté dans cette même plage, conformément aux fonctions de l’auteur.
Ces coefficients arrondis introduisent une petite erreur de recomposition.

## Filtre de chrominance

Pour l’intensité `s`, l’intensité effective est `e = min(2 s, 1)`.
Le rayon de la fenêtre carrée est `r = 2 + round(10 e)` pixels et
`epsilon = (0,001 + 0,05 e)²`. Avec le guide Y et un plan de chrominance C :

```text
a = (mean(Y C) - mean(Y) mean(C)) / (mean(Y²) - mean(Y)² + epsilon)
b = mean(C) - a mean(Y)
C_filtré = mean(a) Y + mean(b)
C_sortie = (1 - e) C + e C_filtré
```

Chaque moyenne porte sur une fenêtre de `(2r + 1)²` pixels avec bords réfléchis.
L’implémentation utilise `scipy.ndimage.uniform_filter(..., mode='reflect')`
à la place de `cv2.boxFilter(..., BORDER_REFLECT)` ; aucune nouvelle dépendance
OpenCV n’est nécessaire. À intensité nulle, le plan est conservé.

## Interfaces, sorties et limites

`extract_luminance(rgb)` accepte un tableau `3×H×W` et retourne trois plans
`H×W`. `merge_luminance(y, cb, cr)` retourne le RGB `3×H×W`.
`guided_chroma(guide, chroma, strength)` retourne un plan lissé de même forme.
Les entrées doivent être non vides, finies, normalisées et de formes compatibles ;
les entrées invalides lèvent `ValueError`.

`CosmicClarityEngine.process(...)` accepte `sharpen_mode`, `denoise_mode` et
`color_denoise_amount`, écrit un FITS distinct, conserve les dimensions/HDU et
retourne un audit. `inference.channels` indique `luminance`, `full`, `separate`
ou `mono` ; `color_mode`, `color_space`, `color_denoise_amount` et
`temporary_stretch` précisent les réglages appliqués. Le journal indique le mode
au moment de la production du candidat.

En mode luminance, Cb/Cr sont conservés **dans l’espace préparé**, avant
recomposition et éventuel retour à l’échelle linéaire. Cela ne garantit pas la
conservation exacte des teintes, de la saturation ni des flux photométriques
initiaux : les étirements par canal et les écrêtages peuvent les modifier.
Le mode `full` modifie volontairement la chrominance. Les contrôles du pipeline
portent sur les étoiles, le bruit et les anneaux, sans mesure colorimétrique.

La gestion du padding, les statistiques aux bords, la normalisation des
flottants hors `[0,1]`, la précision float32 et le rayon fixe restent des choix
de cette adaptation. L’exécutable de l’auteur n’est donc pas reproduit pixel
à pixel ; l’auto-PSF locale, les interfaces et les modèles AI4 ne sont pas inclus.

Le mode luminance envoie un plan au réseau contre trois en mode séparé : il
réduit le nombre de passages réseau. Le mode `full` ajoute des moyennes locales
et des tableaux de chrominance en RAM, sans charger d’autres poids.

## Vérification

Les tests `test_cosmic_color.py` et `test_cosmic_inference.py` vérifient les
primaires BT.601, la recomposition, les limites d’écrêtage, le filtre guidé aux
bords, le routage neuronal des cinq modes, les entrées mono/RGB/entières, les
métadonnées et les sources. `test_cosmic_clarity.py` vérifie la transmission et
la persistance des réglages. Les tests déterministes emploient des prédictions
contrôlées ; ils ne valident pas la qualité visuelle sur une observation réelle.

La validation GPU reste à effectuer sur un FITS astronomique, avec inspection
comparative des étoiles, nébulosités et couleurs. Un test CUDA optionnel existe
dans `test_cosmic_inference.py` et s’active après installation des poids avec
`COSMIC_CUDA_TEST=1`.
