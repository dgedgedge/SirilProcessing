# Cosmic Clarity : moteur neuronal optionnel

[postProcess](README.md) · [Installation](../../INSTALLATION.md)

## Installation et lancement

Le moteur Siril reste le défaut. Cosmic Clarity remplace seulement les étapes
03 (netteté) et 04 (débruitage), en conservant les étapes gradient et photométrie,
les options d'activation, les rapports et leurs contrôles de qualité.

```bash
# Installation seule : pip système cible le venv choisi par le lanceur.
bin/postProcess.sh --install-cosmic-clarity

# Traitement avec les réseaux, après installation.
bin/postProcess.sh image_RGB.fit rapports/resultat.json \
  --postprocess-backend cosmic-clarity --photometry-object M20

# Installation et traitement dans la même commande.
bin/postProcess.sh --install-cosmic-clarity image_RGB.fit \
  --postprocess-backend cosmic-clarity

# Revenir aux traitements classiques, même si le moteur neuronal est mémorisé.
bin/postProcess.sh image_RGB.fit --postprocess-backend siril
```

`--install-cosmic-clarity` est consommé par le `.sh` : il n'est ni transmis au
script Python ni enregistré dans la configuration. Il installe
`requirements-cosmic-clarity.txt` (PyTorch 2.10.0 CUDA 12.8) et six poids, environ
95 Mo au total. Les bibliothèques CUDA/PyTorch occupent plusieurs Go supplémentaires.
Le téléchargement des modèles ne se produit jamais pendant l'inférence.
Une nouvelle installation vérifie les poids existants et ne retélécharge que
les fichiers manquants ou corrompus. Une erreur interrompt le lancement.

Le dossier par défaut est `models/cosmicclarity/` à la racine du projet, ignoré
par Git. `--cosmic-model-dir /chemin` change le dossier pour l'installation et
le traitement ; ce chemin doit aussi être fourni à l'installation si une
configuration de traitement utilise un dossier personnalisé. Le lanceur ne lit
pas le JSON de configuration pour installer les poids.

Un GPU NVIDIA avec pilote compatible CUDA 12.8 est requis par défaut. Le module
`torch` vérifie CUDA au lancement ; il n'y a pas de repli silencieux vers le CPU.
`--cosmic-device cpu` autorise explicitement une exécution CPU. Siril 1.4 reste
nécessaire pour les mesures stellaires, même avec les réseaux. Le traitement
reste local, sans service distant ni entraînement.

## Options

| Option | Défaut | Rôle |
|---|---|---|
| `--postprocess-backend` | `siril` | `siril` ou `cosmic-clarity` |
| `--cosmic-model-dir` | `<projet>/models/cosmicclarity` | Poids locaux vérifiés |
| `--cosmic-device` | `cuda` | `cuda` ou `cpu` |
| `--cosmic-stellar-amount` | `0.5` | Mélange stellaire, entre 0 et 1 |
| `--cosmic-nonstellar-amount` | `0.5` | Mélange des structures diffuses, entre 0 et 1 |
| `--cosmic-nonstellar-radius` | `3.0` | Rayon en pixels, entre 1 et 8 |
| `--cosmic-denoise-amount` | `0.5` | Mélange du débruitage, entre 0 et 1 |
| `--deconvolution-force-manual-matching` | `false` | Force les seuils manuels d’appariement au lieu du mode auto |

Les options Python respectent CLI > configuration JSON > défauts et ne sont
sauvegardées qu'avec `-S`. `--disable-deconvolution` et `--disable-denoise`
s'appliquent aussi aux réseaux. Les seuils `--deconvolution-min-gain`,
`--deconvolution-max-noise`, `--deconvolution-max-rings`, les réglages
d'appariement, `--deconvolution-layer`, `--deconvolution-output` et
`--denoise-max-blur` restent actifs. Les paramètres du solveur Siril (`iterations`,
`psf-size`, `psf-method`, `alpha`, `step`, `adaptive`, etc.) et
`--denoise-modulation` n'agissent pas sur les réseaux.

En netteté (`03_deconvolution`), les seuils d’appariement stellaires sont
automatiques par défaut : ils sont estimés depuis le catalogue `before.tsv`
(rayon en fonction de la FWHM médiane, volume minimal et fraction minimale
selon la densité d’étoiles valides). Pour conserver des seuils fixes, fournir
les valeurs manuelles et ajouter `--deconvolution-force-manual-matching`.
Sans cette option de forçage, les valeurs manuelles d’appariement ne
remplacent pas le mode auto.

Une intensité nulle désactive le calcul correspondant, mais ne dispense pas des
contrôles d'acceptation : une netteté inchangée ne peut pas satisfaire le gain
minimal demandé. Le débruitage sans amélioration rend l'image précédente.

## Modules et algorithme

- `lib/cosmic_clarity/models.py` installe les fichiers décrits dans `models.json`
  via HTTPS, dans un fichier temporaire, puis vérifie taille et SHA-256 avant
  remplacement atomique. L'inférence vérifie aussi chaque fichier chargé.
- `network.py` contient les architectures compatibles avec les poids amont,
  avec connexions résiduelles et latérales. Les paramètres sont chargés
  strictement par PyTorch avec `weights_only=True`.
- `inference.py` fournit `CosmicClarityEngine.process(source, destination,
  operation, ...)`, écrit un candidat FITS distinct et retourne ses paramètres.
- `processors.py` fournit `CosmicClaritySharpenProcessor` et
  `CosmicClarityDenoiseProcessor`. Ces classes produisent le candidat, lancent
  les mesures Siril et décident si l'image peut être transmise à l'étape suivante.

Les données FITS mono ou RGB sont calculées en float32. Les entiers
sont divisés par leur maximum représentable, comme dans les contrôles Siril,
et la sortie float32 conserve cette échelle normalisée. Pour les autres données, si
nécessaire, une translation et une échelle communes placent les pixels dans
[0,1] ; elles sont inversées en sortie pour conserver l’échelle des flottants. Les fichiers
CFA, les dimensions incompatibles, les pixels non finis et les images constantes
sont refusés. Les autres HDU et les métadonnées sont conservés.

Chaque canal est traité indépendamment. Si sa médiane après soustraction du
minimum est positive et inférieure à 0,08 (netteté) ou 0,05 (débruitage), une
transformation temporaire déplace cette médiane à 0,25 :

`T(x; m, t) = t (m - 1) x / [m (t + x - 1) - t x]`.

Après inférence, la transformation rétablit la médiane initiale en utilisant la
médiane du résultat, puis réintroduit le minimum. Les médianes dégénérées sont
laissées inchangées pour éviter les divisions par zéro.

Le réseau travaille sur des tuiles de 256 × 256 pixels, avec recouvrement de
64 pixels. Une marge de 16 pixels est écartée sur chaque prédiction et les
recouvrements valides sont moyennés. Une bordure ajoutée à l'image assure la
couverture de tous ses pixels ; le dernier morceau est complété avant inférence.
Un seul lot est envoyé au GPU, et les réseaux sont chargés successivement.
La RAM contient néanmoins plusieurs tableaux à la taille de l'image.

La netteté applique d'abord le modèle stellaire, puis les modèles non stellaires
encadrant le rayon choisi parmi 1, 2, 4 et 8. Leurs sorties sont interpolées
linéairement. Chaque intensité `a` mélange l'entrée et la prédiction :
`sortie = (1 - a) entrée + a prédiction`. Le débruitage utilise le poids AI3.6.

Cette adaptation utilise des canaux indépendants, un rayon fixe et float32.
Elle ne reproduit pas les modes luminance/chrominance, l'auto-PSF locale, les
interfaces graphiques ni toutes les variantes de l'application Cosmic Clarity.
Elle ne charge pas les modèles AI4 de Suite Pro.

## Contrôles et limites

La netteté exige les mêmes mesures que la déconvolution Siril : étoiles appariées,
gain de finesse, bruit et anneaux acceptables. Un rejet Cosmic Clarity déclenche
automatiquement une tentative de repli avec la déconvolution Siril. Si ce repli
échoue aussi faute de gain validé (`no_safe_improvement`), l'image d'entrée est
conservée et la séquence continue vers le débruitage. Le débruitage utilise la
fonction commune `assess_denoising` ; un rejet conserve l'image reçue. Un incident
technique ou des mesures impossibles font échouer l'étape.

Les rapports `03_deconvolution.json` et `04_denoise.json` indiquent le moteur,
le périphérique, la version de PyTorch, les empreintes des poids utilisés,
les réglages, les mesures et les motifs de rejet. Chaque dossier de travail
conserve le candidat, le script et les catalogues de contrôle. Une ancienne
sortie acceptée n'est pas remplacée par un candidat rejeté.

Ces contrôles ne prouvent pas la conservation de structures diffuses ou des flux
photométriques. La validation sur des FITS astronomiques avec Siril et inspection
visuelle reste nécessaire avant de préférer ce moteur au traitement classique.

## Vérification reproductible

Tests sans téléchargement des poids :

```bash
.venv/bin/python -m pytest -q tests/test_cosmic_clarity.py tests/test_cosmic_inference.py
```

Les tests d'inférence sont ignorés si PyTorch n'est pas installé. Après
installation, ce test charge les six vrais poids sur CUDA puis traite un FITS
synthétique avec les deux opérations :

```bash
COSMIC_CUDA_TEST=1 .venv/bin/python -m pytest -q tests/test_cosmic_inference.py
```

`COSMIC_MODEL_DIR` peut désigner un autre dossier pour ce test. Celui-ci vérifie
la compatibilité des checkpoints, les dimensions et la finitude des sorties,
ainsi que la préservation de l'entrée ; il ne constitue pas un test de qualité
astronomique ni une validation de bout en bout avec Siril.

## Provenance

Architectures et pré/post-traitements adaptés du dépôt
[setiastro/cosmicclarity](https://github.com/setiastro/cosmicclarity/tree/423acc442724872ff6d81011962a8cbf5744a7f7),
commit `423acc442724872ff6d81011962a8cbf5744a7f7`. La licence MIT de Franklin
Marek est conservée dans `lib/cosmic_clarity/LICENSE`. Les poids proviennent de
la [publication Linux officielle](https://github.com/setiastro/cosmicclarity/releases/tag/Linux),
avec des empreintes figées dans le manifeste du projet. Les scripts d'interface
Siril, sous une autre licence, ne sont pas incorporés.
