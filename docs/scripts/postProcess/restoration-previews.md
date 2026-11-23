# Aperçus JPEG AutoStretch de la restauration

[postProcess](README.md) · [Restauration](restoration.md) · [Options](OPTIONS.md)

## 1. Utilisation et sorties

Les orchestrateurs de restauration produisent par défaut des JPEG de
visualisation rapide, sans lancer AutoGHS, StarNet ou un moteur IA supplémentaire.
Ils permettent d'examiner une image linéaire qui paraîtrait presque noire sans
étirement. Les FITS scientifiques et les scores de sélection restent inchangés.

| Option | Défaut | Effet |
| --- | --- | --- |
| `--restoration-previews` / `--no-restoration-previews` | vrai | Activer ou désactiver les JPEG AutoStretch |

L'option respecte CLI > configuration JSON > défaut local et la sauvegarde
explicite `-S`. Elle concerne les comparaisons de chaînes et les comparaisons
séparées de débruitage/déconvolution. Les appels directs aux seuls moteurs de
calcul ne produisent pas automatiquement ces aperçus.

Dans une restauration numérotée :

```text
05_restoration/
  restoration_input_autostretch.jpg
  restoration_autostretch.jpg
  01_denoise_cosmic_clarity/
    01_denoise_cosmic_clarity_autostretch.jpg
```

Le nom exact et le numéro des opérations dépendent des moteurs actifs. Le JPEG
`<rapport>_autostretch.jpg` montre la sortie effective. En cas de rejet, c'est
l'image conservée ; si le candidat réel est disponible, un second JPEG
`<rapport>_candidate_autostretch.jpg` permet d'examiner ce candidat sans le
présenter comme retenu. Les aperçus d'opérations mises en cache ne sont pas
recalculés. Les appels non numérotés utilisent les mêmes suffixes auprès de
leurs rapports existants.

Les rapports contiennent `input_preview`, `result_preview` et, si applicable,
`candidate_preview`, avec statut, chemin JPEG, FITS présenté, référence commune
et paramètres d'étirement. Les JPEG apparaissent dans les galeries des index
HTML existants. Ils sont entiers, à la résolution du FITS, en RGB 8 bits,
qualité JPEG 95, sans sous-échantillonnage couleur. Le mono est dupliqué en RGB.
L'orientation suit l'[affichage natif Siril](render-exports.md).

L'export d'aperçu est auxiliaire : une entrée illisible, non finie ou une erreur
d'écriture produit un avertissement et `status=failed` pour cet aperçu ; elle ne
transforme pas un calcul scientifique réussi en échec. L'ancien JPEG à ce chemin
est retiré avant la tentative ou lors d'une désactivation afin d'éviter sa
confusion avec un nouvel export. Les erreurs du moteur scientifique continuent
à suivre leurs règles habituelles.

## 2. Courbe commune et algorithme

`lib/restoration_preview.py` expose `RestorationPreviewProcessor`, qui hérite de
`processor`. Une instance conserve la référence et estime sa courbe au premier
export. `post_process(input_path, output_path, args)` écrit un JPEG distinct ;
`optional` audite les erreurs auxiliaires et `attach` enrichit un rapport de
traitement. Les méthodes sans état `fit` et `apply` séparent estimation et rendu.

L'approche est du type médiane/MAD + transfert des tons moyens, apparentée à
l'[AutoStretch Siril](https://siril.readthedocs.io/en/stable/processing/stretching.html).
Il ne s'agit pas d'une reproduction pixel à pixel de son affichage : la
stratégie d'échantillonnage et la comparaison à courbe fixe sont explicites ici.

1. Lire des plans mono/RGB finis dans les unités normalisées Siril ; les entiers
   sont divisés par leur maximum représentable. Pour une référence flottante
   hors de `[0,1]`, utiliser `offset=min(0,minimum)` et
   `scale=max(1,maximum-offset)` ; ces paramètres sont communs aux candidats.
2. Échantillonner régulièrement la référence, avec un pas
   `ceil(sqrt(hauteur × largeur / 262144))`, au minimum 1, puis augmenté si
   nécessaire pour ne pas dépasser 262144 pixels par canal, y compris pour
   une image très allongée. Calculer médiane et
   `sigma=1,4826 × MAD` par canal, puis leurs moyennes entre canaux.
3. Définir le point noir `c0=max(0,médiane − 2,8 × sigma)`. Une MAD nulle conserve
   `c0=0`. Le point blanc est 1. Le centre normalisé est
   `x0=(médiane − c0)/(1 − c0)`.
4. Choisir le paramètre de tons moyens `m=MTF(0,25,x0)` pour amener le fond vers
   0,25. Les paramètres dégénérés sont protégés par une marge de `1e-12`.
5. Pour chaque candidat, utiliser **les mêmes paramètres**, normaliser avec
   `offset/scale`, borner `x=(pixel − c0)/(1 − c0)` à `[0,1]`, puis appliquer :

```text
MTF(m,x) = (m − 1) × x / ((2m − 1) × x − m)
```

Les canaux RGB sont liés : aucun rééquilibrage indépendant des couleurs n'est
ajouté. Une courbe fixe facilite l'examen du bruit, du fond et des étoiles entre
l'entrée et les candidats ; un nouveau réglage automatique pour chaque candidat
pourrait masquer leurs différences. Le point noir et les dépassements sont
écrêtés uniquement dans la présentation JPEG.

Les images constantes noires et saturées restent finies ; un champ constant
non nul peut être présenté à un niveau intermédiaire. Une image vide, NaN/Inf
ou une géométrie autre que mono/RGB provoque une erreur explicite d'aperçu.
Le calcul est indépendant du GPU et de Siril, avec NumPy, Astropy et Pillow,
déjà présents dans le projet. Les temporaires float64 sont limités à des bandes
de 256 lignes ; le tableau rendu et le JPEG restent à pleine résolution.

## 3. Limites et vérifications

Ces JPEG facilitent le diagnostic ; ils ne remplacent pas un rendu final, une
mesure de flux ou un examen du FITS. Le transfert non linéaire, l'écrêtage et la
quantification JPEG peuvent modifier l'apparence des couleurs et du bruit.
Les pixels hors des bornes de la référence peuvent perdre leurs détails dans
le JPEG. Les contrôles scientifiques restent calculés sur les FITS linéaires.

Les tests couvrent le fond cible, la monotonie, les images constantes, les
valeurs signées, le RGB lié, l'orientation, la conservation de la source, les
courbes partagées, les candidats rejetés, les erreurs et la priorité des options :

```bash
.venv/bin/python -m pytest -q tests/test_restoration_preview.py tests/test_restoration_chains.py
```

L'intégration aux chaînes est vérifiée avec des moteurs simulés sur des FITS
réels de test. Ces vérifications ne constituent pas une validation scientifique
de la qualité d'un moteur de restauration ni une égalité de rendu avec Siril.
