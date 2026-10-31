# Rendu final AutoGHS

[postProcess](README.md) › AutoGHS

Une sortie StarNet brute légèrement supérieure à 1 est conservée avec son
résidu signé jusqu'à la [compensation des couches](star-rendering.md#politique-des-residus-negatifs).
L'entrée et les couches corrigées doivent rester dans `[0,1]` ; aucun écrêtage
indépendant ni normalisation globale de secours n'est appliqué.

La phase AutoGHS, activée par défaut, termine le pipeline existant, après la
restauration, sur son résultat **RGB linéaire avec étoiles**. Elle effectue une
séparation StarNet, un étirement dédié aux étoiles, puis une optimisation RANGE du
fond sans étoiles et une recomposition. Les étapes amont gardent leur ordre.
Chaque fond passe aussi par une recherche finale D/B/SP/HP avec protection des
hautes lumières, après l’optimisation et la chroma éventuelle, avant la fusion.

La [couleur finale adaptative](final-color.md), activée par défaut, renforce ensuite
le fond sans étoiles. Sauf `--disable-final-color`, elle remplace la
chroma préalable et publie un rendu recomposé distinct. `--color-only` reprend
directement les couches rendues sans recalculer StarNet ou GHS.
La phase est activée par défaut ; `--disable-autoghs` la désactive même si
elle a été mémorisée dans la configuration.

## Prérequis et entrée

- Dépendances habituelles de postProcess : NumPy, Astropy, SciPy et Matplotlib.
- **Siril 1.4.x et StarNet++ V2** pour l'adaptateur historique `starnet -stretch`.
  Validation locale : Siril Flatpak 1.4.4, StarNet++ 2.0.2. Le script installé
  `StarNet.py` cible StarNet 2.5 et n'est pas utilisé avec le binaire V2.
- FITS RGB à trois plans `(3, hauteur, largeur)`, linéaire, dématricé, étoilé.
  Les entiers sont normalisés selon le mécanisme existant `normalized_planes` ;
  les flottants doivent déjà être dans `[0,1]`. NaN, Inf, valeurs hors domaine,
  monochrome, CFA et entrée déclarée starless sont refusés.
- Les images marquées `SPNLIN=True`, ou `SPROLE=STARLESS/STARS`, sont refusées. L'absence de cette carte
  **ne prouve pas la linéarité** : celle-ci relève du contrat du pipeline ou de
  l'utilisateur pour un fichier externe. Aucun diagnostic d'histogramme ne
  permet de certifier qu'une image a conservé sa réponse linéaire.

L'installation de StarNet et de son modèle reste externe au dépôt. Les
bibliothèques et le modèle doivent être accessibles depuis le mode Siril
choisi. L'adaptateur échoue explicitement si StarNet ne peut pas fonctionner.
Sur l'installation V2 testée, un essai 256 × 256 a échoué ; utiliser au moins
512 × 512 pour la validation réelle.

## Lancement et chemin StarNet sauvegardé

Avec le venv existant, pour ajouter le rendu à toute la chaîne :

```bash
.venv/bin/python bin/postProcess.py image_RGB_lineaire.fits rapports/ \
  --enable-autoghs --compute-backend cpu \
  --starnet-path /chemin/StarNetv2CLI_linux/starnet++ --save-config
```

Le lanceur `bin/postProcess.sh` accepte les mêmes arguments et utilise le venv installé
comme décrit dans [Installation](../../INSTALLATION.md). Il ne possède pas de
mécanisme AutoGHS distinct. Aucun nouveau lanceur n'est nécessaire.

`--starnet-path` suit la priorité **CLI > configuration JSON > découverte dans
les préférences Siril**. Sans chemin explicite, la découverte lit
`core.starnet_exe` dans `config.1.4.ini`, puis `config.ini`, du mode choisi :

- Flatpak : `~/.var/app/org.siril.Siril/config/siril/` ;
- natif/AppImage : `$XDG_CONFIG_HOME/siril/`, ou `~/.config/siril/`.

Avec AutoGHS actif et `-S/--save-config`, le point d'entrée résout aussi le chemin
découvert et le sauvegarde sous la clé **`starnet_path`**, en chemin absolu. Le
fichier par défaut reste `~/.siril_darklib_config.json` ; `--config fichier.json`
en sélectionne un autre. Sans `-S`, aucune configuration n'est sauvegardée.
Les préférences Siril sont copiées dans le travail de l'étape et passées avec
`-i` : la séparation ne remplace pas les préférences personnelles de Siril.

Pour ne faire que le rendu d'un résultat linéaire déjà restauré :

```bash
.venv/bin/python bin/postProcess.py image_RGB_lineaire.fits rapports/ \
  --disable-gradient --disable-photometry \
  --disable-deconvolution --disable-denoise \
  --enable-autoghs --compute-backend cpu
```

Cette forme a été exécutée sur un extrait réel de M33 dans les tests
[d'intégration](../../../tests/README.md). La validation de la phase n'impose pas
de réexécuter la calibration, la photométrie ou la restauration.

## Sorties et publication

Le dossier d'étapes habituel contient `05_autoghs.json` et le dossier visible
`05_autoghs_work/`, sans suffixe aléatoire ni dossier temporaire. Le numéro 05
reste fixe quand la restauration regroupe les étapes 03/04. Les transformations
sont ordonnées dans les sous-dossiers suivants :

```text
05_autoghs_work/
  001/separation_work/  # entrée, fond et étoiles linéaires, script et journal Siril
  002_stars/           # couche d’étoiles rendue commune
  003_ghs/             # fond étiré RANGE
  004_recomposition/   # résultat final avec étoiles, FITS et JPEG
  005_diagnostics/     # graphiques et rapport détaillé
```

Les fichiers sont répartis dans ces sous-dossiers :

| Fichier | Contenu |
|---|---|
| `<image>_render_range.fits` | **Résultat principal étoilé**, stratégie range |
| `<image>_render_range.jpg` | Export JPEG RGB pleine résolution du résultat final |
| `<image>_starless_ghs_range.fits` | Fond étiré RANGE utilisé pour la recomposition |
| `<image>_stars_render.fits` | Couche d'étoiles étirée |
| `<image>_stars_render.jpg` | Couche d'étoiles entière en JPEG, dans `002_stars/` |
| `<image>_stars_diagnostic.png` | Étoiles seules : extraits avant/après, histogrammes log et bilan d'adaptation dans `002_stars/` |
| `<image>_ghs_report.json` | Rapport complet, paramètres, histogrammes et métriques |
| `<image>_ghs_histograms.png` | Sans étoiles : entrée, étape intermédiaire et rendu final ; axes Y linéaire et logarithmique |
| `<image>_ghs_histograms_with_stars.png` | Avec étoiles : rendu final recomposé seul ; axes Y linéaire et logarithmique |
| `separation_work/` | FITS linéaires, script, préférences isolées et journal StarNet |

**RANGE est le seul traitement exécuté.** Son rendu fournit `output_image`,
`output_jpeg`, le FITS final de postProcess et l'image annotée. Le JPEG est
également copié sous `<image>_postprocess.jpg` à côté du FITS final.
Les cartes `strategies`, `output_images` et `output_jpegs` restent compatibles
avec les lecteurs existants, avec une seule entrée `range`.

L'ancienne option `--ghs-strategy` est acceptée pour compatibilité, sans être
proposée dans l'aide : `range`, `both` et `peak` sont tous normalisés vers
`range`, y compris depuis une ancienne configuration JSON. Cela ne réactive
aucun traitement PEAK. Les autres paramètres suivent leur priorité habituelle.
Les comparatifs AutoGHS et les fichiers PEAK ne sont plus générés ; les anciens
produits du dossier de travail sont supprimés par sa réinitialisation à la relance.

Les métadonnées et extensions du FITS source sont conservées, ainsi que WCS et
`ROWORDER`. Les anciennes cartes entières `BZERO/BSCALE/BLANK` sont retirées,
les bornes recalculées et `BUNIT` devient dimensionless (`1`). `SPROLE` identifie chaque branche (`STARLESS`, `STARS`, `WITHSTARS`). Les rendus sont float32 et marqués **`SPNLIN=True`** avec HISTORY :
ce sont des images de présentation non linéaires, impropres à des mesures de
flux linéaires. La source reste intacte. Les JPEG sont quantifiés sur 8 bits, qualité 95, sans
étirement ni gamma supplémentaire. Les histogrammes
sont des figures de présentation et de diagnostic. Le rapport fournit
leurs chemins dans `histogram_paths` (`starless` et `with_stars`) ;
`histogram_path` conserve le chemin de la figure sans étoiles.

Les produits sont écrits directement dans cette arborescence visible. Au
lancement suivant, le dossier de travail de l’étape est réinitialisé, comme
celui des autres traitements ; copier ailleurs les produits à conserver avant
une relance. Une entrée située dans ce dossier est refusée pour la préserver.
Le rapport de réussite n’est écrit qu’après validation du rendu et de ses exports. En cas
d’erreur, le rapport indique `ERROR` et les produits partiels et journaux restent
disponibles pour diagnostic ; leur présence ne signifie pas que l’étape a réussi.
Les rapports JSON utilisent un fichier visible `.pending` en suffixe avant
remplacement, sans création de répertoire temporaire.

## Paramètres

Tous les réglages ci-dessous sont persistants avec `-S`. Les défauts sont
expérimentaux et configurables ; ils ne constituent pas des normes de qualité
astronomique.

| Option | Défaut | Rôle |
|---|---|---|
| `--compute-backend` | `auto` | Politique commune de postProcess ; NumPy/CuPy pour le GHS |
| `--ghs-max-iterations` | `8` | Limite de 1 à 32 recherches GHS |
| `--ghs-final-strength` | `1.25` | D maximal en mode auto, force imposée en mode fixed ; 0 désactive |
| `--ghs-final-mode` | `auto` | Recherche protégée ou courbe comparative `fixed` |
| `--ghs-highlight-retention` | `0.9` | Conservation des indicateurs de contraste brillant, dans `]0,1]` |
| `--ghs-log-rms-weight` | `2.0` | Poids de la réduction relative du RMS logarithmique ; `[0,10]`, zéro désactive ce terme |
| `--ghs-dynamic-target` | `1.0` | Étendue RGB minimale du fond après GHS, dans `]0,1]` ; par exemple `0.8` |
| `--ghs-dynamic-max-iterations` | `32` | Limite de 1 à 64 passages d’expansion linéaire |
| `--ghs-histogram-strength` | `0.7` | Égalisation globale partielle, de 0 (désactivée) à 1 |
| `--ghs-histogram-clip` | `4.0` | Plafond d’effectifs relatif à la moyenne, de 1 à 16 |
| `--ghs-bins` | `4096` | Histogramme, de 64 à 65536 classes |
| `--ghs-q-low`, `--ghs-q-high` | `0.1`, `99.9` | Percentiles en pourcentage |
| `--ghs-min-gain` | `0.01` | Amélioration relative minimale |
| `--ghs-plateau-fraction` | `0.2` | Fraction du meilleur gain marginal définissant un plateau |
| `--ghs-max-clip` | `0.001` | Fraction maximale de nouveaux pixels écrêtés par contrôle |
| `--ghs-max-noise-gain` | `30` | Limite cumulée des proxys bruit et IQR |
| `--ghs-color-mode` | `human` | Luminosité pondérée human ou moyenne `even` |
| `--ghs-chroma` | `off` | `off`, `balanced`, `enhanced` ; RGB expérimental |
| `--star-render` | `conservative` | Rendu asinh commun des étoiles |
| `--star-strength` | `2.0` | Force asinh, dans `]0,20]` |
| `--star-faint-boost` | `5.0` | Renforcement demandé des étoiles faibles, de 1 à 10 ; réduit par pas de 1 jusqu'au premier rendu respectant la garde de largeur |
| `--star-histogram-strength` | `0.8` | Répartition des amplitudes des pics en logarithme, de 0 (gain fixe) à 1 ; réduite si la garde FWHM le demande |
| `--star-ceiling` | `0.85` | Plafond du maximum RGB des étoiles seules |
| `--star-max-width-ratio` | `2.0` | Rapport FWHM médian maximal, si mesurable |
| `--star-max-color-drift` | `0.08` | Dérive moyenne maximale de chromaticité RGB après fusion |
| `--star-max-chroma-excess` | `0.10` | Hausse moyenne maximale de chroma RGB après fusion |
| `--ghs-negative-tolerance` | `0.005` | Seuil d’alerte des résidus négatifs, dans `[0,0.05]` ; compensation conservant la somme linéaire |
| `--ghs-headroom` | `0.95` | Facteur commun du fond avant fusion screen |

Les plages `[0,1]` s'expriment en unités Siril normalisées, les fractions ne
sont pas des pourcentages. Les mesures de couleur ne prétendent pas décrire
une colorimétrie perceptuelle calibrée.

## Renforcement final du fond sans étoiles

La [protection automatique des hautes lumières](ghs-highlight-protection.md)
recherche D, B, SP et HP à partir du contenu de chaque image, après les traitements
du fond et avant recomposition. Le mode par défaut `--ghs-final-mode auto`
conserve le contraste relatif des régions brillantes et contrôle aussi la fusion
avec les étoiles. Aucun réglage n'est spécifique à M31.
Le classement combine la visibilité des tons moyens et une régression robuste
de la queue d'histogramme logarithmique entre le pic et Y=0,875 lorsque cette
extension est suffisamment peuplée, sinon Y=0,75. La réduction relative du RMS
est pondérée par `--ghs-log-rms-weight 2`, contre 0,15 pour le terme de pente.
Les trois finalistes sont comparés sur tous les pixels avant sélection.
Ce critère respecte les gardes et ne vise aucune pente universelle. SP peut se déplacer
du pic vers le signal diffus ; P98 sombre n'est plus assimilé à une haute lumière.

`--ghs-final-strength 1.25` borne D au lieu d'imposer cette valeur ; zéro
désactive cette dernière passe. `--ghs-highlight-retention 0.9` exige de conserver
au moins 90 % des indicateurs de contraste brillant mesurables. Le moteur garde
le fond précédent si aucune amélioration admissible n'est trouvée.
Les paramètres retenus et les rejets figurent dans `presentation_stretch`.
Les options sont rémanentes avec `-S`, avec priorité CLI > JSON > défaut.

Pour renforcer encore le rôle de la régularité lors d'une reprise du rendu :

```bash
bin/postProcess.sh image_RGB.fit --autoghs-only --ghs-log-rms-weight 3
```

Ajouter `-S` mémorise ce poids ; zéro désactive seulement le terme RMS du score.
Une référence déjà rectiligne peut être conservée si aucun essai n'améliore le compromis.

`--ghs-final-mode fixed` permet une comparaison avec l'ancienne courbe
B=SP=LP=0, HP=1, D imposé par `--ghs-final-strength`, sans les gardes automatiques.
Les deux modes laissent la couche d'étoiles inchangée.

## Erreurs, résultats et limites de validation

Les dépassements négatifs StarNet corrigeables sont compensés avant étirement,
avec avertissement et mesures dans `residual_compensation`. Ils n’interrompent
pas le rendu. Les couches corrigées doivent rester finies et bornées ; voir
la [politique de compensation](star-rendering.md#politique-des-residus-negatifs).

Une panne StarNet, CUDA en cours de calcul, FITS, disque ou publication est
**fatale**, sans reprise CPU ou substitution de rendu. La CLI retourne `1`.
Le rapport d'étape porte `ERROR` si son écriture est possible et conserve le
chemin du dossier de travail pour diagnostic. Un arrêt de recherche sans gain est
normal : `NO_IMPROVEMENT`, avec recomposition malgré tout des branches valides.
Le statut global `SUCCESS` signifie que RANGE a appliqué une transformation,
pas qu'une expertise visuelle certifie la qualité astronomique.

Le choix hérite de la [politique générale de calcul](README.md#choix-du-calcul-cpugpu).
CPU seul a été exécuté sans CuPy. Le backend `auto` teste CUDA avant la
séparation, avec une estimation des temporaires et une marge de VRAM de 20 %.
`cuda` exige un moteur fonctionnel. Le backend StarNet reste indépendant.
La parité numérique CPU/CUDA est vérifiée sur le matériel local ; le test
correspondant est ignoré lorsqu'un moteur CUDA fonctionnel manque.

Les validations numériques Siril et une exécution réelle complète sur un
extrait de M33 sont disponibles. La qualification visuelle sur nébuleuse,
IFN, bande étroite, fond fortement bruité et étoiles très brillantes reste à
réaliser. Les heuristiques ne peuvent pas distinguer universellement un détail
diffus d'une amplification de bruit. Un noyau déjà saturé n'est pas restauré.

Le rendu RANGE seul et sa reprise `--autoghs-only` ont également été exécutés
sur un extrait de M31, avec Siril, StarNet et CUDA : un seul FITS/JPEG étoilé,
aucun produit PEAK ni comparatif, et production finale de l'image annotée.

## Références spécialisées

- [Moteur GHS, histogrammes, optimisation et backends](auto-ghs-algorithm.md).
- [StarNet, rendu stellaire et recomposition](star-rendering.md).
- [Tests et procédure de validation réelle](../../../tests/README.md).

Un encart rappelle l'objectif RANGE et résume la boucle GHS : recherches
ayant évalué des candidats / limite configurée, GHS acceptés, recherches sans
application, nombre de candidats examinés et corrections du point noir appliquées.
Il indique la raison d’arrêt et la largeur initiale/finale du critère, avec son
gain relatif si la largeur initiale est positive. Ces mesures concernent le fond
avant fusion, y compris sur la figure avec étoiles. Un arrêt avant toute recherche
(distribution constante) compte zéro recherche ; la chroma ne compte
pas comme une itération GHS. Les données absentes ne sont pas remplacées par zéro.

La seconde boucle complète les GHS si `max(RGB)-min(RGB)` n’atteint pas la cible.
Chaque passage recale le point noir sur la première intensité RGB strictement
positive et applique un gain commun borné. Le point noir est également recalé
avant chaque recherche GHS. L’ancienne option `--ghs-black-margin` n’est plus
utilisée. Les encarts et le rapport `dynamic_range` indiquent les passages,
les étendues et l’atteinte de la cible ; une image constante reste inchangée.
La cible porte sur le fond sans étoiles avant la marge de recomposition.
Voir les [règles de la seconde boucle](auto-ghs-algorithm.md#boucle-complémentaire-dexpansion-de-la-dynamique).

Pour viser une étendue de 0,8 et enregistrer ce choix dans la configuration :

```bash
bin/postProcess.sh image_RGB.fit --ghs-dynamic-target 0.8 --save-config
```

La valeur est résolue selon CLI > configuration > défaut (1,0). Une ancienne
valeur sauvegardée reste prioritaire ; `--ghs-dynamic-target 1 --save-config`
permet de la remplacer explicitement.

Pour relever davantage les structures diffuses et les étoiles faibles, les réglages
`--ghs-histogram-strength` (défaut 0,7) et `--star-faint-boost` (défaut 5)
sont indépendants et persistants avec `--save-config`. Le premier égalise
partiellement les intensités du fond, sous plafond de contraste ; le second
renforce uniquement la couche stellaire extraite. Les valeurs 0 et 1,
respectivement, désactivent ces deux ajouts. L'adaptation stellaire est pilotée
séparément par `--star-histogram-strength` (défaut 0,8 ; 0 la désactive).
Le diagnostic dédié et les exports des étoiles se trouvent dans `002_stars/`.
Voir les pages
[algorithme](auto-ghs-algorithm.md) et [étoiles](star-rendering.md).

Le rapport fournit `output_jpegs` et `output_jpeg` (JPEG principal), ainsi que
`output_jpeg` dans la branche `range`. Aucun ancien rendu n'est chargé pour
produire un comparatif et aucune clé `comparison_paths` ou `comparison_previous`
n'est écrite.
Voir [exports JPEG](render-exports.md) pour l’orientation, la conversion
RGB et les limites de ces formats.
