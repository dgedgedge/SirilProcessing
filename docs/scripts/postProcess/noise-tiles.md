# Mesure du bruit sur tuiles : échantillonnage et GPU

[Documentation](../../README.md) › [postProcess](README.md) › Bruit sur tuiles

## 1. Objectif et options

`SkyNoiseProcessor` mesure la dispersion avant/après sur les mêmes tuiles de
ciel, avec les mêmes masques. Pour limiter le coût sur les grandes images,
il sélectionne par défaut au plus **256 tuiles** et calcule les statistiques
par lots. Ce réglage concerne le bruit `reliable`, pas les autres diagnostics
stellaires, diffus ou historiques.

| Option | Défaut | Effet |
|---|---|---|
| `--quality-max-noise-tiles` | 256 | Plafond des tuiles évaluées ; 0 conserve toutes les tuiles |
| `--quality-noise-batch-size` | 32 | Taille maximale d'un lot, entre 1 et 256 ; peut être réduite pour la mémoire |
| `--compute-backend` | auto | PyTorch CUDA puis MPS si disponibles et adaptés au lot ; `cpu` impose NumPy |
| `--quality-noise-backend` | non définie | Surcharge locale `auto`, `cpu`, `cuda` ou `mps` |

Ces options suivent CLI > JSON > défaut et sont enregistrées uniquement avec
`-S`. Un plafond non nul doit être au moins `max(3, quality-min-tiles)`.
Les critères existants restent applicables : couverture de ciel admissible,
nombre minimal de mesures, au moins trois quadrants, dispersion initiale
mesurable et incertitude du gain. Un réglage incohérent est refusé à la
préparation de la référence ou à l'appel du processeur.

Pour une vérification exhaustive, utiliser `--quality-max-noise-tiles 0`.
Pour augmenter la précision spatiale, relever le plafond. Un plafond plus bas
réduit les calculs mais fournit moins d'échantillons au bootstrap.

## 2. Sélection spatiale fixe

`SkyTileProcessor` prépare toujours la liste complète des tuiles admissibles.
Le plafond ne modifie ni cette liste, ni les masques, ni les aperçus de référence.
Seul le sous-ensemble évalué par le processeur de bruit est limité.

La sélection commence par une tuile proche du centre de chaque quadrant présent,
puis ajoute successivement la tuile la plus éloignée de celles déjà retenues.
Les distances utilisent les coordonnées relatives au champ. Les ex aequo sont
résolus par l'ordre initial, qui reste celui du résultat.

Les choix dépendent uniquement des coordonnées des tuiles de référence,
**jamais des valeurs du candidat**. Le même sous-ensemble est employé sur les
trois canaux RGB et avant/après. Il garantit une couverture des quadrants
présents, sans garantir à lui seul une représentativité statistique parfaite
d'un bruit spatialement variable. Il n'invente aucune tuile dans un quadrant
sans ciel admissible.

## 3. Calculs parallèles par lots

Les utilitaires de `lib.noise_tiles` effectuent les mêmes mesures :

1. l'ajustement d'une tendance plane par tuile, avec coordonnées centrées ;
2. la soustraction de cette tendance pour la mesure uniquement ;
3. la dispersion robuste `1,4826 × médiane(|résidu − médiane(résidu)|)` ;
4. la corrélation horizontale sur les paires de pixels toutes deux admissibles.

Les moindres carrés sont résolus par les covariances des coordonnées centrées.
Une pseudo-inverse de rang un traite les tuiles de bord à une seule ligne ou
colonne. Les pixels hors masque et le remplissage des lots sont exclus de tous
les calculs. Un pixel non fini dans le masque est une erreur.

NumPy vectorise ces opérations sur CPU ; PyTorch traite les lots sur CUDA ou MPS. Seuls les lots sélectionnés sont transférés : l'image complète
n'a pas à résider en VRAM pour cette mesure. Les petites statistiques sont
rapatriées sur CPU pour l'agrégation et le bootstrap déterministe à 256 tirages.
Le pire canal conserve le ratio global, comme auparavant. Une dispersion avant
inférieure ou égale à `1e-10` est ignorée ; un effectif insuffisant donne
`UNKNOWN`, sans inventer un gain.

Pour moins de 32 tuiles, `auto` utilise le CPU pour éviter le coût d'initialisation
et de transfert. À partir de 32 tuiles, le moteur PyTorch commun précontrôle CUDA puis MPS,
les opérations et la mémoire disponible. Une surcharge `cuda` ou `mps` impose
le périphérique, même pour un petit nombre de tuiles.
Un accélérateur absent ou insuffisant conduit au choix CPU **avant calcul**.
Une panne après le démarrage du backend est propagée, sans reprise CPU cachée.
Sur CUDA, les calculs restent en float64. Sur MPS, qui ne supporte pas ce type,
le retrait du plan reste sur CPU float64. Les résidus sont normalisés par leur
amplitude maximale par tuile/image, puis transférés en float32 pour les
médianes, MAD et corrélations sur GPU. La dispersion est remise dans ses unités
initiales sur CPU. La conversion intervient après retrait du fond et du gradient,
pour préserver un bruit faible sur un fond élevé. Les seuils ne sont pas modifiés.
Cette voie est hybride : son gain dépend de la part du temps consacrée au plan.
Voir le [moteur commun PyTorch](torch-compute.md).

La taille effective d'un lot est limitée par l'option, l'effectif disponible et
un budget cible de temporaires de 64 Mio, estimé à 24 plans float64 par tuile.
Une seule très grande tuile peut dépasser cette cible ; le précontrôle GPU
utilise l'estimation correspondante. Ce budget ne constitue pas une garantie
sur la mémoire totale de l'application ou les allocations internes de PyTorch.
Aucun moteur de restauration supplémentaire n'est lancé en parallèle.

## 4. Rapports et interprétation

Le diagnostic `noise` contient :

- `sampling` : tuiles disponibles/évaluées, plafond, quadrants, méthode et
  coordonnées des tuiles sélectionnées ;
- `backend` : backend choisi, raison, précision (float64 ou hybride MPS), version PyTorch,
  taille demandée/effective des lots,
  nombre de lots par canal et estimation mémoire ;
- `per_channel` : effectif réellement mesurable, dispersions avant/après,
  corrélations, ratio médian et intervalle de confiance ;
- `processing.duration_seconds` : durée englobante du processeur, incluant
  sélection, précontrôle/initialisation, transferts, calculs et bootstrap.

Si le ciel est insuffisant, le résultat reste `UNKNOWN` avec l'audit
`sampling`, sans initialiser de backend ni inventer de mesure.

Le journal INFO donne aussi `Bruit : évaluées/disponibles tuiles … backend …`.
Les durées restent intégrées à l'[audit récursif](quality-processors.md).

L'échantillonnage peut modifier légèrement le ratio et l'intervalle, donc un
verdict proche d'un seuil. Les seuils scientifiques ne sont pas relâchés.
Le bootstrap décrit la variabilité des tuiles retenues ; il ne couvre pas tous
les biais spatiaux, corrélations ou erreurs de fond. Une comparaison exhaustive
est conseillée pour examiner un résultat limite ou un bruit très hétérogène.
Le calcul float64 par lots est mathématiquement équivalent au retrait du plan
par moindres carrés, à la précision numérique près.

## 5. Vérification

Les tests comparent les lots CPU aux mesures scalaires par moindres carrés,
y compris les tuiles minces, le remplissage et les pixels exclus. Ils vérifient
la couverture spatiale, la reproductibilité, le mode exhaustif, les canaux RGB,
la sauvegarde et les erreurs de backend. Les tests CUDA conditionnels comparent
les mêmes données sur NumPy et sur GPU réel lorsqu'il est disponible.
Les chemins tensoriels CUDA double et MPS hybride sont également comparés sur
CPU, avec fort gradient, bruit faible, pixels exclus et tuiles minces. Cela
valide leurs formules, pas leur vitesse ni leur exécution sur un vrai Mac.
La comparaison matérielle MPS reste à effectuer sur Mac compatible.

Une mesure locale sur un ciel synthétique mono 2048 × 2048, avec 1 024 tuiles
admissibles et un ratio de bruit connu de 0,6, a comparé l'ancien parcours CPU
exhaustif au nouveau parcours plafonné. Ces essais vérifient cette charge ; ils
ne garantissent pas un facteur d'accélération pour un autre matériel ou la
restauration complète. Le premier appel CUDA inclut une initialisation qui peut
le rendre plus lent que CPU sur un échantillon court.

```bash
.venv/bin/python -m pytest -q tests/test_noise_tiles.py tests/test_quality_processors.py
```

Sur le candidat GraXpert déjà enregistré de NGC7331, un essai réel a comparé
1 902 tuiles exhaustives aux 256 tuiles plafonnées, sans recalculer le moteur
ni modifier les sources. Le ratio du pire canal passe de 0,55366 à 0,55256 ;
les intervalles à 95 % se recouvrent et la réduction de bruit reste démontrée.
Les calculs CPU/CUDA sur le même sous-ensemble concordent à la précision
numérique. Cet essai n'établit pas la représentativité pour tous les champs.
