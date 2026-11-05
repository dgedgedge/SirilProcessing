# Calculs PyTorch pour les diagnostics et la couleur

[postProcess](README.md) › [Politique CPU/GPU](compute-backend.md)

## 1. Responsabilités

`TorchComputeBackend` (`lib.torch_backend`) partage la sélection CUDA/MPS,
le précontrôle mémoire, les convolutions et les statistiques entre la
[couleur finale](final-color.md), le [bruit sur tuiles](noise-tiles.md) et les
[filtres diffus](diffuse-regions.md). Il ne charge aucun modèle et ne modifie
pas les images sources. PyTorch est déjà une dépendance de la restauration.
AutoGHS conserve son moteur NumPy/CuPy distinct.

## 2. Choix et erreurs

La politique globale est `--compute-backend auto|cpu`. Chaque traitement possède
une surcharge locale facultative `auto|cpu|cuda|mps`, déclarée par sa classe :
`--final-color-backend`, `--quality-noise-backend`, `--quality-diffuse-backend`.
Leur priorité est CLI > JSON > défaut non défini (héritage de la politique).
Une surcharge locale reste prioritaire même si la politique générale est CPU.
La sauvegarde reste explicite avec `-S`.

En auto, CUDA est préféré à MPS. L'absence de GPU utilisable, l'échec du
précontrôle ou un budget supérieur à 80 % de la mémoire disponible sélectionne
CPU avant calcul, avec avertissement. CUDA/MPS imposé échoue dans ce cas.
La mémoire CUDA libre ou le budget MPS recommandé moins la mémoire pilote
allouée est consulté. Ce contrôle est une estimation, pas une réservation.
Les pannes après initialisation se propagent sans recommencer sur CPU.

## 3. Contrats numériques

- Les convolutions sont séparables, tronquées à quatre sigma, avec indices
  repliés comme `scipy.ndimage` en mode `reflect` (pixels de bord inclus).
  Les plans ne sont pas mélangés. TF32 est désactivé pendant les convolutions
  CUDA dans un contexte restauré ensuite.
- Les médianes paires moyennent les deux centres. Les médianes masquées
  excluent les NaN ; une série sans valeur retourne NaN. La médiane ordinaire
  propage un NaN. Les tris et les réductions restent sur le périphérique actif.
- Les percentiles interpolent les statistiques d'ordre, sans utiliser
  `torch.quantile` et sa limite de taille. Seuls les rangs sont préparés sur CPU.
- La couleur finale travaille en float32. Les gardes du gamut, du ciel,
  des hautes lumières et de la luminosité restent actives.
- CUDA conserve le float64 du bruit. MPS n'accepte pas float64 : le bruit
  utilise des plans retirés en double sur CPU, puis des résidus normalisés
  convertis en float32 pour les statistiques GPU. Les flux diffus conservent
  leur règle distincte : une image float64 choisit CPU en auto, sans conversion.

Les sorties d'image et petites matrices de statistiques sont rapatriées
explicitement vers NumPy. `TorchArrayAPI` expose uniquement les opérations
requises par les formules partagées du bruit et de la couleur, sans redirection
implicite vers NumPy pour une opération manquante.

## 4. Vérifications et limites

Les tests comparent les calculs tensoriels sur CPU à NumPy/SciPy, y compris
les tuiles minces, le bruit faible sur gradient fort, les masques, les médianes
et la conservation des couleurs et de la luminosité. Ils ne mesurent pas
l'accélération. Les tests matériels CUDA/MPS sont conditionnels et indiquent
explicitement leur absence. L'exécution MPS reste à vérifier sur Mac lorsque
les validations sont faites sur Linux.

Aucun benchmark lourd n'a été ajouté. Sur de petits lots, les transferts et
tris peuvent coûter plus que NumPy ; le bruit garde son choix CPU automatique
sous 32 tuiles. Sur MPS, le retrait des plans CPU limite le gain maximal.

Références : [backend MPS](https://docs.pytorch.org/docs/main/notes/mps.html),
[médiane PyTorch](https://docs.pytorch.org/docs/stable/generated/torch.median.html)
et [gaussienne SciPy](https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.gaussian_filter.html).
