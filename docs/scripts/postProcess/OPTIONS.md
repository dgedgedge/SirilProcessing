# Options de postProcess

[Documentation des scripts](../README.md) › [postProcess](README.md)

<a id="configuration-et-m-morisation"></a>
<a id="configuration-et-mémorisation"></a>

## 1. Configuration et mémorisation

Au démarrage, `Config` interroge les classes de traitement, charge leurs options
et résout les valeurs selon la priorité **ligne de commande → fichier JSON →
défauts locaux des classes**. Les défauts décrits dans les références des
traitements peuvent donc être remplacés par une configuration sauvegardée.

| Option commune | Rôle |
|---|---|
| `--config FICHIER` | Fichier partagé ; défaut `~/.siril_darklib_config.json` |
| `-S`, `--save-config` | Mémoriser les paramètres rémanents |
| `-m`, `--siril-mode` | `flatpak` par défaut, ou `native`, `appimage` |
| `-s`, `--siril-path` | `siril` par défaut ; chemin de l’exécutable en mode native/appimage |
| `-l`, `--log-level` | `INFO` par défaut ; aussi `DEBUG`, `WARNING`, `ERROR`, `CRITICAL` |

`-S` mémorise les paramètres Siril, les activations/désactivations et les réglages
rémanents des traitements, y compris ceux désactivés. Les clés déjà
présentes concernant d’autres traitements sont conservées. Sans `-S`, aucune
écriture du fichier de configuration n’est effectuée.

Restent propres à l’exécution : les chemins d’entrée et de rapport, le niveau
de journalisation, `--gradient-output`, `--photometry-object`,
`--photometry-coordinates`, `--photometry-force`, `--photometry-output` et
`--deconvolution-output`, `--autoghs-only` et `--color-only`. Ces paramètres ne sont pas mémorisés par `-S`.
Les anciens noms `--photometry-siril-path` et `--photometry-siril-mode` sont des
alias des réglages Siril communs.

```bash
# Mémoriser les réglages, puis traiter l’image.
bin/postProcess.sh image_RGB.fit rapports/resultat.json \
  --config config/postprocess.json -m native -s siril-cli \
  --photometry-object M20 --denoise-modulation 0.4 -S

# Réutiliser ces réglages sur une autre image ; le nom d’objet est fourni à nouveau.
bin/postProcess.sh autre_image_RGB.fit rapports/autre.json \
  --config config/postprocess.json --photometry-object M31
```

La sauvegarde intervient avant les traitements, après vérification de la présence
de l’image d’entrée. Les réglages peuvent donc être mémorisés même si une étape
échoue ensuite. Un échec de sauvegarde arrête le programme.
Les réglages de qualité et des chaînes sont également rémanents. Pour GraXpert,
`enable_graxpert: null` conserve l’activation automatique ; `false` reste une
désactivation explicite, même avec des poids présents.

<a id="choix-du-calcul-cpu-gpu"></a>
<a id="choix-du-calcul-cpugpu"></a>

## 2. Choix du calcul CPU/GPU

`--compute-backend auto|cpu` s'applique aux moteurs de postProcess et se
sauvegarde avec `-S`. Le défaut `auto` choisit l'accélérateur disponible pour
chaque moteur, CPU sinon avec un WARNING indiquant la cause, avant calcul.
L'ancien choix global `cuda` est converti en `auto` avec avertissement.
Les options propres aux moteurs restent
des surcharges explicites, y compris dans les anciens JSON. Voir la
[politique commune, ses priorités et ses limites](compute-backend.md).
Sur Apple Silicon, `auto` sélectionne MPS pour Cosmic Clarity et CoreML pour
GraXpert lorsqu'ils sont utilisables. `--cosmic-device mps` force ce seul moteur ;
La couleur finale et les diagnostics bruit/diffus utilisent aussi PyTorch MPS ;
AutoGHS reste sur CPU. Voir [le guide Mac](../install_macos/README.md).

<a id="options-des-traitements"></a>

## 3. Options des traitements

Les tableaux des sections suivantes décrivent les options de chaque étape. Les références spécialisées complètent celles de [GraXpert](graxpert.md#paramètres), [AutoGHS](auto-ghs.md#paramètres) et de la [politique CPU/GPU](compute-backend.md).

AutoGHS recherche par défaut un renforcement final protégé
(`--ghs-final-mode auto`), avec D borné par `--ghs-final-strength 1.25`,
avant recomposition. La recherche de droite logarithmique vise également
10 pixels par classe du graphique à l'intensité 0,8
(`--ghs-regression-min-count 10` ; 0 désactive ce seuil), sous les gardes
cumulatives. `--ghs-quartile-validations` fixe le budget de candidats
diversifiés validés sur toute l’image par passe (12 par défaut, de 1 à 64).
En mode `piecewise`, `--ghs-shape-branches` conserve jusqu'à trois chemins
de familles et positions du pic différentes (de 1 à 8). Le budget de
validations reste partagé par profondeur, et les forces sont affinées entre
positions de pic trop espacées. Aucun ancien candidat n'est réutilisé.
`--ghs-quartile-block-pixels` limite les temporaires GHS float64 à des blocs
de 262144 pixels par défaut (de 1 à 4194304), sur CPU comme sur CUDA.
En rectitude, la protection du contraste brillant s'adapte entre 45 % et
la conservation normale selon la fraction du fond actif au-dessus de 0,8.
`--ghs-sparse-tail-fraction 0` désactive cette adaptation ; son défaut est
0,001 (0,1 % du signal actif). Le plancher est réglable avec
`--ghs-sparse-highlight-retention` (0,45 par défaut). Le cœur garde sa
protection normale.
Voir [le critère de régression](ghs-quartiles.md#seuil-de-la-droite-à-08).
`--ghs-histogram-model piecewise` sélectionne le profil par morceaux par défaut ;
`line` conserve le mode historique sur demande explicite. Les réglages `--ghs-shape-smoothing`
(0,0001), `--ghs-shape-retention` (0,65), `--ghs-shape-local-retention` (0,1),
`--ghs-shape-distance` (0,15), `--ghs-shape-peak-shift` (0,25) et
`--ghs-shape-stiffness` (1,25) sont décrits dans
[la conservation de forme](ghs-histogram-shape.md). `--ghs-linearity-tolerance`
s'applique alors à chaque morceau. Le poids global `--ghs-log-rms-weight`
concerne seulement le mode `line`.
Le classement `piecewise` plafonne le bonus contraste/bruit à 100 %, pénalise
progressivement les déformations, les bosses intermédiaires et la compression
brillante, puis départage par le déplacement du pic. `--ghs-quality-cnr-floor`
(0,9 ; ]0,1]) règle le début de pénalité de perte contraste/bruit ;
`--ghs-quality-tail-bonus` (0,1 ; [0,1]) borne le bonus total des niveaux à
0,8 et 0,875. Ces niveaux ne priment pas sur la qualité. Les seuils de forme
et de contraste brillant sont des cibles progressives dans cette recherche
finale ; écrêtage, cœur, couleurs et bruit restent des gardes obligatoires.
`--ghs-post-blackpoint rise` recale le BP après chaque courbe, avant conformité,
en mode `piecewise` ; `off` désactive ce recalage. Voir
[le pied de montée et son budget cumulatif](ghs-blackpoint.md).
Les graphiques sont uniquement logarithmiques et la série est exposée par
`histogram_gallery`, `histogram_series_manifest` et `histogram_series`.
`--ghs-save-trial-jpegs` ajoute les JPEG des essais de cette galerie, avec
les mêmes numéros que les histogrammes et des aperçus avec étoiles.
Le défaut est désactivé ; `--no-ghs-save-trial-jpegs` permet de désactiver
une valeur mémorisée. Voir [les images de chaque essai](ghs-histogram-shape.md#151-images-jpeg-de-chaque-essai).
`--ghs-final-strength 0` désactive le renforcement et cette recherche.
Cette option est rémanente avec `-S` et commune à postProcess et à lightProcess.

## Annotation finale

| Option | Défaut | Rôle |
|---|---|---|
| `--final-annotation` / `--no-final-annotation` | activé | [Copie PNG avec encart de prise de vue](annotations.md), après le rendu final |

Les options `--annotation-objects` / `--no-annotation-objects` (actif),
`--annotation-magnitude` (14, entre 0 et 25) et `--annotation-max-objects`
(40, entre 0 et 200) règlent les noms astronomiques. Elles sont mémorisables
avec `-S` ; les détails figurent dans [Annotation finale](annotations.md).

## Relance AutoGHS et protection des hautes lumières

| Option | Défaut | Rôle |
|---|---|---|
| `--autoghs-only` | désactivé | Reprendre à StarNet/AutoGHS, puis saturation, exports et annotation ; saturation activée pour cet appel sauf `--disable-final-color` explicite ; non mémorisable |
| `--color-only` | désactivé | Reprendre seulement la couleur, la fusion et les exports sur les couches GHS existantes ; non mémorisable |
| `--enable-final-color` / `--disable-final-color` | activé | Activer/désactiver la [couleur finale adaptative](final-color.md) après GHS ; mémorisable avec `-S` |
| `--final-color-backend` | non définie | Surcharge PyTorch `auto`, `cpu`, `cuda` ou `mps` ; sinon politique commune |
| `--final-color-strength` | `3.0` | Excès de gain chromatique maximal, dans `[0,3]` ; 0 redonne le rendu GHS sans renforcement ; mémorisable |
| `--ghs-final-mode` | `auto` | Recherche D/B/SP/HP protégée ; `fixed` pour comparer l'ancienne courbe |
| `--ghs-highlight-retention` | `0.9` | Conservation minimale des indicateurs de contraste brillant, dans `]0,1]` |
| `--ghs-log-rms-weight` | `2.0` | Poids du gain relatif de régularité de la queue log ; `[0,10]`, zéro désactive ce terme ; mémorisable avec `-S` |

Voir [le mode de relance](README.md#relance-autoghs-seul) et
[la méthode de protection](ghs-highlight-protection.md).

### Étirements GHS successifs

`--ghs-quartile-iterations` (12, entre 0 et 64),
`--ghs-quartile-max-strength` (10, dans ]0,10]),
`--ghs-linearity-tolerance` (0,05 dex, entre 0,005 et 0,5) et
`--ghs-blackpoint-loss` (0,001, entre 0 et 0,01) contrôlent la
[recherche de la droite logarithmique la plus longue](ghs-quartiles.md), après la passe finale protégée.
`--ghs-tail-height` (0,25, de 0 à 1) ajoute une cible de hauteur logarithmique
vers 0,875, par rapport au pic de `--ghs-tail-channel` (`red` par défaut,
`green` ou `blue` possibles). Zéro conserve le seul objectif de longueur.
Le mode fixe et `--ghs-final-strength 0` désactivent cette recherche.

### Régions de qualité diffuse

`--quality-max-diffuse-regions` limite à 256 régions par défaut, avec
répartition spatiale et classes de tailles ; 0 conserve toutes les régions.
Voir [la sélection et son audit](diffuse-regions.md).

`--quality-noise-backend auto|cpu|cuda|mps` surcharge la politique commune
pour le [bruit sur tuiles](noise-tiles.md), sans changer les autres diagnostics.

`--quality-diffuse-backend cuda` impose PyTorch CUDA aux filtrages multiscales diffus ;
`mps` impose PyTorch Metal sur Mac et `auto` choisit CUDA, MPS ou CPU ;
sans surcharge, ils suivent `--compute-backend auto` ou `cpu`. Les fenêtres
retenues, mesures de flux et seuils gardent les mêmes définitions.
