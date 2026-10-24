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
`--deconvolution-output` et `--autoghs-only`. Ces paramètres ne sont pas mémorisés par `-S`.
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

`--compute-backend auto|cpu|cuda` s'applique aux moteurs de postProcess et se
sauvegarde avec `-S`. Le défaut `auto` choisit l'accélérateur disponible pour
chaque moteur, CPU sinon, avant calcul. Les options propres aux moteurs restent
des surcharges explicites, y compris dans les anciens JSON. Voir la
[politique commune, ses priorités et ses limites](compute-backend.md).

<a id="options-des-traitements"></a>

## 3. Options des traitements

Les tableaux des sections suivantes décrivent les options de chaque étape. Les références spécialisées complètent celles de [GraXpert](graxpert.md#paramètres), [AutoGHS](auto-ghs.md#paramètres) et de la [politique CPU/GPU](compute-backend.md).

AutoGHS recherche par défaut un renforcement final protégé
(`--ghs-final-mode auto`), avec D borné par `--ghs-final-strength 1.25`,
avant recomposition. `--ghs-final-strength 0` le désactive.
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
| `--autoghs-only` | désactivé | Reprendre la même commande à partir du résultat linéaire précédent ; StarNet, AutoGHS et exports seulement ; non mémorisable |
| `--ghs-final-mode` | `auto` | Recherche D/B/SP/HP protégée ; `fixed` pour comparer l'ancienne courbe |
| `--ghs-highlight-retention` | `0.9` | Conservation minimale des indicateurs de contraste brillant, dans `]0,1]` |
| `--ghs-log-rms-weight` | `2.0` | Poids du gain relatif de régularité de la queue log ; `[0,10]`, zéro désactive ce terme ; mémorisable avec `-S` |

Voir [le mode de relance](README.md#relance-autoghs-seul) et
[la méthode de protection](ghs-highlight-protection.md).
