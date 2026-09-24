# Post-traitement d'une image FITS

`bin/postProcess.sh` sélectionne le venv et transmet les arguments à
`bin/postProcess.py`. Les chemins relatifs restent relatifs au dossier de lancement.
Le script parse les arguments une seule fois et appelle `_PostProcessorSequence`.
Chaque `processor` déclare ses options et les applique dans `post_process`.

```bash
bin/postProcess.sh image.fit rapports/resultat.json \
  --disable-photometry --disable-deconvolution \
  --gradient-min-order 2 --gradient-max-order 4 \
  --gradient-sample-fraction 0.5 --gradient-max-samples 5000 \
  --gradient-points 200 --gradient-output apercus
```

Le second argument est le **rapport JSON global** ; par défaut, il s'appelle
`<image>_postProcess.json`, à côté de l'image source. Les rapports propres à chaque
traitement sont dans `<nom_du_rapport>_steps/<index>_<prefixe>.json`.

## Options du gradient

| Option | Défaut | Effet |
|---|---|---|
| `--enable-gradient` / `--disable-gradient` | activé | Active ou désactive ce traitement |
| `--gradient-min-order` | 1 | Ordre minimum, de 1 à 5 |
| `--gradient-max-order` | 3 | Ordre maximum, de 1 à 5, supérieur ou égal au minimum |
| `--gradient-sample-fraction` | 0.3 | Fraction de pixels ajustés, strictement positive et au plus 1 |
| `--gradient-max-samples` | 10000 | Limite positive du nombre de pixels ajustés |
| `--gradient-points` | 100 | Nombre positif de points aléatoires affichés sur l'aperçu |
| `--gradient-output` | dossier du rapport du traitement | Répertoire de l'aperçu PNG |
| `--gradient-measurement-image` / `--no-gradient-measurement-image` | activé | Génère ou supprime la génération de l'aperçu |

Les alias `--enable_gradient` et `--disable_gradient` sont aussi acceptés.
`--log-level` règle le niveau des logs console et fichier. `--help` liste les options de tous les traitements.
Les points affichés sont un échantillon de visualisation indépendant des pixels
utilisés pour l'ajustement polynomial.

Le traitement du gradient analyse l'image puis produit un FITS corrigé, un rapport
et, éventuellement, un PNG de mesure. Le fond RBF (par défaut) ou polynomial est soustrait par canal,
en conservant son niveau central et le FITS source. Une erreur de traitement entraîne
un code de sortie non nul et empêche l'écriture du rapport global de cette exécution.

## Utilisation Python

```python
from pathlib import Path
from lib.postprocess import GradientExtractor

extractor = GradientExtractor(max_polynomial_order=2, create_measurement_image=False)
results = extractor.post_process(Path("image.fit"), Path("gradient.json"))
```

Voir [le contrat de la séquence et l'ajout de traitements](SEQUENCE_PROCESSOR.md).

## Étalonnage des couleurs par astrométrie et photométrie

`PhotometricColorCalibrator` (`photometry`) reproduit la recherche de l'objet,
la résolution astrométrique, puis l'étalonnage photométrique PCC de Siril.
La photométrie, le gradient et la déconvolution sont activés par défaut.
Utiliser `--disable-photometry` ou `--disable-deconvolution` pour les désactiver.
Il faut **Siril 1.4 ou ultérieur**, une image FITS **RGB linéaire déjà dématricée**
et une connexion Internet pour la recherche d'objet et les catalogues distants.

```bash
bin/postProcess.sh image_RGB.fit rapport.json \
  --enable-photometry --photometry-object M20 \
  --photometry-output image_RGB_pcc.fit
```

La recherche du nom passe par le CDS via Astropy. Le centre trouvé est transmis à
`platesolve`, puis Siril exécute `pcc`. Sans objet ni coordonnées explicites, Siril
utilise les métadonnées de l'image et ses réglages. Une solution WCS existante est
réutilisée, sauf avec `--photometry-force` ou un centre explicitement fourni.

Si nécessaire, compléter les paramètres de prise de vue :

```bash
bin/postProcess.sh image_RGB.fit rapport.json \
  --disable-gradient --enable-photometry --photometry-object M20 \
  --photometry-focal 750 --photometry-pixelsize 3.76 \
  --photometry-catalog apass --photometry-output image_RGB_pcc.fit
```

Les valeurs 750 mm et 3,76 µm sont des exemples à remplacer par celles de votre
image. Après binning ou rééchantillonnage, renseigner une taille de pixel effective
cohérente avec l'échelle de l'image. Le centre du champ peut être différent du
centre de l'objet, notamment pour une mosaïque ; il peut être saisi directement.

| Option | Effet |
|---|---|
| `--enable-photometry` / `--disable-photometry` | Active ou désactive ce traitement |
| `--photometry-object M20` | Recherche les coordonnées de l'objet au CDS |
| `--photometry-coordinates RA DEC` | Centre en degrés ICRS/J2000, exclusif avec le nom d'objet |
| `--photometry-focal MM` | Focale, sinon métadonnées/réglages Siril |
| `--photometry-pixelsize UM` | Taille effective des pixels, sinon métadonnées/réglages Siril |
| `--photometry-force` | Recalcule une solution astrométrique existante |
| `--photometry-noflip` | Empêche Siril de retourner l'image pendant la résolution |
| `--photometry-downscale` | Sous-échantillonne pour accélérer la résolution |
| `--photometry-solve-catalog NOM` | Catalogue de résolution ; sinon choix automatique Siril |
| `--photometry-catalog NOM` | Catalogue PCC : `nomad`, `apass`, `localgaia` ou `gaia` |
| `--photometry-limitmag MAG` | Magnitude limite absolue des étoiles pour PCC |
| `--photometry-output FICHIER` | Image FITS étalonnée ; défaut : `<rapport>_steps/02_photometry.fit` |
| `-m`, `--siril-mode MODE` | `flatpak`, `native` ou `appimage` ; valeur chargée depuis Config |
| `-s`, `--siril-path CHEMIN` | Exécutable en mode native/appimage ; valeur chargée depuis Config |

L'image source est conservée. Le rapport `photometry` contient `output_image`, les
commandes exécutées, les coordonnées utilisées et les chemins du script et du log.
La séquence transmet automatiquement le FITS étalonné aux traitements suivants.
Les scripts, logs et le FITS de travail restent dans un sous-dossier unique
`<rapport>_steps/02_photometry_*`, utile au diagnostic. Une sortie existante est remplacée
seulement après réussite de Siril et validation des dimensions et du WCS du FITS.

Le gradient accepte aussi les FITS RGB et corrige chaque canal séparément.
Choisir `--gradient-method rbf` (défaut) ou `--gradient-method polynomial`.
En mode polynomial, la moyenne des canaux sert à choisir le degré.
Le FITS corrigé est transmis à la photométrie via `output_image`.

Références : [commandes Siril platesolve et pcc](https://siril.readthedocs.io/en/stable/Commands.html#platesolve),
[recherche d'objet Astropy](https://docs.astropy.org/en/stable/api/astropy.coordinates.SkyCoord.html#astropy.coordinates.SkyCoord.from_name).


## Logs

Comme `lightProcess`, `postProcess` utilise la configuration commune de
`lib/logging_utils.py` : console et fichier UTF-8, avec date, niveau, nom du logger
et message. Le niveau `--log-level` s'applique aux deux sorties.

Pour `bin/postProcess.sh image.fit rapports/resultat.json`, le log est écrit dans
`rapports/resultat_steps/00_image_postProcess.log`. Il est recréé à chaque lancement.
Sans chemin de rapport explicite, il se trouve dans
`<dossier_image>/<image>_postProcess_steps/00_<image>_postProcess.log`.

Les logs des traitements Python et les erreurs y sont enregistrés ; en mode
`--log-level DEBUG`, les exceptions incluent leur traceback. Le fichier est fermé
même en cas d'échec ou d'interruption. Les sorties brutes de Siril restent dans les
fichiers `.log` propres à ses scripts, dont les chemins figurent dans le log général.


## Configuration Siril partagée avec lightProcess

Les deux scripts déclarent leurs options Siril avec `add_siril_arguments` de
`lib/siril_utils.py`. Le traitement photométrique obtient son service avec
`create_siril_from_args` et utilise `Siril.run_siril_script` pour l'exécution et
les logs Siril. Les futurs traitements peuvent utiliser le même service et les
mêmes paramètres globaux.

La priorité est **options CLI > fichier JSON > Config.DEFAULTS**. Sans `--config`,
le fichier partagé est `~/.siril_darklib_config.json`. Les défauts communs sont
`siril_mode: "flatpak"` et `siril_path: "siril"`. Le chemin est ignoré en mode
Flatpak ; pour une installation native, indiquer le CLI, par exemple `-s siril-cli`.

```bash
# Charger un fichier et mémoriser les paramètres Siril pour les prochains lancements
bin/postProcess.sh image_RGB.fit rapport.json \
  --enable-photometry --photometry-object M20 \
  --config config/siril.json -m flatpak -s siril -S

# Réutiliser les paramètres sans les réécrire
bin/postProcess.sh image_RGB.fit rapport.json \
  --enable-photometry --photometry-object M20 --config config/siril.json

# Le même fichier est utilisable par lightProcess
bin/lightProcess.sh /chemin/session --config config/siril.json
```

`-S` / `--save-config` sauvegarde `siril_path` et `siril_mode` pour postProcess,
en conservant les autres clés déjà présentes. Les chemins des images, les options
du gradient et les réglages photométriques restent propres à l'invocation ; ils ne
sont pas sauvegardés par cette option. Sans `-S`, le fichier n'est pas modifié.
Les répertoires parents du fichier sont créés au besoin ; un échec de sauvegarde
entraîne un code de sortie non nul.

Les anciens noms `--photometry-siril-mode` et `--photometry-siril-path` restent des
alias des options globales. Une analyse de gradient seule ne lance ni ne valide
Siril ; le service est créé lorsqu'un traitement l'utilise.

## Déconvolution contrôlée : PSF fine, rondeur et artefacts

Le traitement `deconvolution` est activé par défaut et exécuté après la photométrie.
Il emploie le service Siril et les paramètres globaux `--siril-mode`, `--siril-path`
et `--config`. L'entrée doit être un FITS linéaire mono ou RGB dématricé.

```bash
bin/postProcess.sh image_RGB.fit rapport.json \
  --photometry-catalog localgaia --photometry-solve-catalog localgaia
```

### Premier essai

L'image d'entrée est sauvegardée sans modification. Une détection gaussienne Siril
fournit les positions, amplitudes et FWHM. La sélection PSF retient des étoiles :

- non saturées et de mesures finies ;
- de rondeur `min(FWHMx,FWHMy)/max(FWHMx,FWHMy) >= 0.8` ;
- dans la plage d'amplitude 0,01–0,7 ;
- parmi les 35 % les plus fines de cette population, avec FWHM >= 1,5 pixel ;
- isolées et assez éloignées des bords pour extraire leur profil.

Au moins 3 étoiles sont nécessaires, avec un maximum de 60. Leurs découpes sont
regroupées dans un FITS de sélection. Seule leur luminosité est normalisée, à un
pic de 0,3 pour satisfaire les critères internes de `makepsf stars` ; leur largeur
et leur forme ne sont pas changées. Siril génère la PSF sur cette planche, et non
sur l'image entière avec sa sélection automatique habituelle. Le coeur du noyau
ne doit pas dépasser 1,5 fois la largeur des étoiles sélectionnées. Un noyau
plus étroit reste soumis aux contrôles d’efficacité et d’artefacts des essais.
La dimension du fichier PSF (15 × 15 par défaut) est distincte de sa largeur stellaire.

Richardson–Lucy utilise maintenant la **descente de gradient**, avec un pas initial
de 0,0003 et 10 itérations. La PSF FITS est explicitement rechargée dans chaque essai.

### Comparaison et décision

Les catalogues gaussiens avant/après sont appariés par correspondances spatiales
uniques dans les deux sens, sur le même canal (vert par défaut en RGB). Les mesures
saturées, invalides ou ambiguës sont exclues. Au moins 10 paires et 70 % des étoiles
valides avant traitement doivent être retrouvées. Les statistiques avant/après
utilisent exactement ces mêmes paires : FWHM géométrique en pixels, rondeur,
moyenne, médiane, écart-type, MAD, quartiles et ratios appariés.

Une sortie n'est acceptée que si :

- la FWHM moyenne et le ratio médian apparié indiquent au moins 2 % de gain ;
- la rondeur moyenne ne perd pas plus de 0,01 ; une amélioration de rondeur
  n'est pas obligatoire si les étoiles deviennent plus fines ;
- le bruit ne dépasse pas 1,15 fois celui de l'original, mesuré sur le même masque
  de pixels de fond de ciel déterminé exclusivement sur l'image originale ;
- au plus 10 % des étoiles contrôlées présentent un anneau nouveau détecté.

Les contrôles de bruit et de halos portent sur tous les canaux RGB. Le détecteur
de halos mesure les creux sous le fond de ciel et les remontées du profil radial,
normalisés au pic stellaire ; un accroissement supérieur à 0,02 est compté comme
anneau potentiel. Ce sont des heuristiques, pas une garantie d'absence d'artefacts :
les images doivent rester inspectables visuellement, notamment dans les champs
très denses ou nébuleux. Chaque essai indique séparément efficacité et artefacts.

### Repli sur le tiers central et PSF Moffat

Si le premier essai n'est pas accepté ou si sa PSF n'est pas exploitable :

1. Repartir de l'original sauvegardé, et en extraire le tiers central de la largeur
   et de la hauteur (un neuvième de la surface). Ce recadrage est uniquement un
   fichier d'essai ; l'image entière conserve ses métadonnées et son WCS.
2. Détecter les étoiles avec le profil **Moffat**, plage d'amplitude **0,1–0,7**,
   puis filtrer finesse, rondeur et isolement comme ci-dessus ; générer et sauvegarder
   une nouvelle PSF à partir de ces étoiles.
3. Essayer les pas 0,0004, 0,0005, etc., jusqu'à 0,001 par défaut. Chaque essai
   recharge **le même recadrage original**, jamais une image déjà déconvoluée.
4. Au premier dépassement des garde-fous, arrêter la recherche. Conserver le dernier
   pas qui satisfaisait à la fois l'efficacité et les contrôles d'artefacts. Si aucun
   dépassement n'est rencontré, garder le dernier pas admissible dans la limite fixée.
5. Recharger l'image entière originale, appliquer cette PSF et ce pas, puis refaire
   tous les contrôles sur le champ entier. Un succès sur le centre ne suffit pas.

Si aucune sortie n'est acceptable, le processus signale une erreur et n'écrase pas
une sortie précédente. L'original, les candidats et les diagnostics restent disponibles.

### Options et audit

| Option | Défaut | Rôle |
|---|---|---|
| `--disable-deconvolution` | traitement actif | Désactiver la déconvolution |
| `--deconvolution-iterations` | 10 | Itérations Richardson–Lucy |
| `--deconvolution-psf-size` | 15 | Dimension impaire du noyau FITS |
| `--deconvolution-step` | 0.0003 | Pas de l'essai simple |
| `--deconvolution-max-step` | 0.001 | Limite de la recherche, incrément 0.0001 |
| `--deconvolution-fine-quantile` | 0.35 | Fraction fine admissible pour la PSF |
| `--deconvolution-psf-roundness` | 0.8 | Rondeur minimale des étoiles PSF |
| `--deconvolution-min-gain` | 0.02 | Gain relatif minimal de finesse |
| `--deconvolution-max-noise` | 1.15 | Amplification maximale du bruit |
| `--deconvolution-max-rings` | 0.1 | Fraction maximale d'anneaux potentiels nouveaux |
| `--deconvolution-layer` | 1 RGB, 0 mono | Canal des mesures stellaires |
| `--deconvolution-match-radius` | 1 | Rayon d'appariement en pixels |
| `--deconvolution-min-stars` | 10 | Nombre minimal de paires |
| `--deconvolution-min-match-fraction` | 0.7 | Fraction minimale retrouvée |
| `--deconvolution-output` | `<rapport>_steps/03_deconvolution.fit` | Image finale acceptée |

Les paramètres propres à la déconvolution restent propres à l'invocation ;
`--save-config` mémorise les paramètres d'exécution Siril, pas ceux-ci.

Chaque exécution crée un dossier unique `03_deconvolution_*`. Il contient l'original,
les sélections JSON avec identifiants et mesures, les planches des étoiles choisies,
les PSF FITS et PNG (linéaire et racine carrée), les catalogues et les images candidates,
les scripts et logs de chaque étape. Le rapport `03_deconvolution.json` conserve
chaque essai, son pas, les paires utilisées, les statistiques, les métriques de bruit
et de halos et les motifs de rejet. En cas de succès, `matched_stars.csv` contient
la cohorte du résultat final et `output_image` est transmis à la suite du pipeline.

Référence : [déconvolution Siril](https://siril.readthedocs.io/en/stable/processing/deconvolution.html).


## Ordre des fichiers générés

Pour `M_33_combined.fit`, sans rapport de sortie explicite :

```text
M_33_combined_postProcess.json
M_33_combined_postProcess_steps/
├── 00_M_33_combined_postProcess.log
├── 01_gradient.json
├── 01_gradient_M_33_combined_measurement_points.png
├── 02_photometry.json
├── 02_photometry.fit
├── 02_photometry_<identifiant>/
│   ├── 02_photometry.sps
│   ├── 02_photometry.log
│   └── 02_photometry_calibrated.fit
├── 03_deconvolution.json
├── 03_deconvolution.fit
└── 03_deconvolution_<identifiant>/
    ├── 03_deconvolution_original.fit
    ├── 03_deconvolution_simple_selection.json
    ├── 03_deconvolution_simple_selected_stars.fit
    ├── 03_deconvolution_simple_psf.fit
    ├── 03_deconvolution_simple_psf.png
    ├── 03_deconvolution_simple_trial.fit
    ├── 03_deconvolution_matched_stars.csv
    └── ... catalogues, scripts/logs, essais centraux et PSF Moffat si repli
```

L'index correspond à la position dans la séquence, y compris lorsqu'une étape
précédente est désactivée : la déconvolution conserve donc `03`. Le rapport global
conserve ses clés `gradient`, `photometry`, `deconvolution`. Les chemins explicitement
fournis par l'utilisateur pour le rapport ou une image de sortie sont respectés.
Les anciens fichiers ne sont pas déplacés ni renommés.

### Contrôle des imagettes PSF

La sélection vérifie aussi les pixels de chaque imagette, indépendamment de la
rondeur annoncée dans le catalogue. Le cœur et les ailes doivent être ronds
(moments aux niveaux 15 %, 35 % et 60 % du pic). Les compagnons non catalogués,
pics secondaires, profils décentrés et excès diffus entraînent le rejet de
l'imagette entière. Aucun profil artificiel ne remplace les étoiles rejetées.

Le fichier `*_selected_stars.json` conserve les mesures et motifs de rejet,
y compris lorsque moins de trois étoiles restent et que la PSF est refusée.
Le FITS de même nom contient la planche des étoiles acceptées. Ces contrôles
sont heuristiques ; les contrôles appariés après déconvolution et l'audit visuel
de la PSF restent nécessaires.

### Réglages prudents par défaut

La PSF par défaut est maintenant estimée par Siril avec
`makepsf blind -l0 -ks=15`, puis sauvegardée dans `03_deconvolution_blind_psf.fit`
avec son aperçu PNG. Richardson–Lucy utilise explicitement la régularisation
TV, alpha 3000, 10 itérations et un pas de 0,0003.

`--deconvolution-psf-method stars` réactive la sélection des imagettes décrite
plus haut. Le repli Moffat et la recherche de pas croissants nécessitent désormais
`--deconvolution-adaptive`. Sans cette option, un essai insuffisant ou dégradé
est refusé sans augmenter la force. `--deconvolution-alpha` règle la
régularisation TV (plus faible signifie davantage de régularisation).

### Réduction du bruit avant déconvolution (activée par défaut)

L'ordre est désormais gradient, photométrie, **03_denoise**, puis
**04_deconvolution**. Les noms de déconvolution indiqués plus haut avec 03
deviennent donc 04 dans la séquence par défaut.

La commande Siril est `denoise -vst -mod=0.5` : correction sel et poivre
active, algorithme secondaire VST d'Anscombe et modulation 0,5.
Les sorties restent en **32 bits flottants** ; aucun étirement d'histogramme
n'est appliqué. L'autoajustement et le zoom sont des réglages de visualisation,
pas des transformations nécessaires au traitement.

Options : `--disable-denoise`, `--denoise-modulation 0.5`,
`--denoise-max-blur 0.03`.

Le contrôle exige au moins 10 étoiles appariées, représentant au moins 70 %
des étoiles valides initiales. Il compare les mêmes étoiles et les mêmes
pixels de fond de ciel. Chaque canal doit présenter une réduction du bruit ;
la FWHM moyenne et le ratio médian ne doivent pas augmenter de plus de 3 %,
et la rondeur moyenne ne doit pas perdre plus de 0,01. Le contrôle des anneaux
est également appliqué. Ce sont des garde-fous heuristiques, pas une garantie
de conservation de tous les détails diffus.

Si les critères échouent, la réduction est annulée et la séquence reprend
l'image précédente. La candidate reste disponible pour audit, ainsi que les
catalogues, le script, le log et `03_denoise.json`. En cas de succès,
`03_denoise.fit` est transmis à la déconvolution. Les gains mesurés sont
affichés au niveau INFO.
