# Découverte, regroupement et calibration

[Documentation](../../../README.md) › [lightProcess](../README.md) › [Traitements](README.md)


## Découverte des acquisitions

Chaque chemin fourni est une session. S’il contient directement `light/` ou
`Light/`, il constitue son unique sous-session. Sinon, le script cherche les
sous-sessions dans ses sous-répertoires immédiats contenant ce dossier. La
recherche n’est pas récursive au-delà de ce niveau.

Dans chaque sous-session, les acquisitions sont les fichiers `.fit`, `.fits`,
`.FIT` et `.FITS` directement présents dans `light/` ou `Light/`. Les flats
sont recherchés de même dans `flat/` ou `Flat/`. Les listes sont triées.

## Métadonnées et groupes

Un light doit posséder des métadonnées exploitables : date d’observation,
exposition, température, gain, type d’image, caméra et binning sur les deux
axes. Les fichiers identifiés comme darks ou bias sont ignorés dans les lights.
Les rejets sont comptés dans les statistiques de sous-session.

La clé de groupe rassemble caméra, température arrondie selon la précision
configurée, exposition, gain et binning. Chaque groupe est calibré séparément.
Le premier light du groupe sert de référence pour chercher les masters.

## Recherche du master dark

Avec les darks activés, le script parcourt récursivement la bibliothèque à la
recherche d’un FITS valide identifié comme dark et compatible avec la clé du
light. Si les deux en-têtes portent une commande `STACKCMD`, leur compatibilité
est aussi vérifiée. Le premier fichier compatible rencontré est utilisé ;
aucun classement par date ou nombre de darks n’est appliqué à cette recherche.

Sans correspondance, le groupe ne peut pas être calibré avec dark et est
signalé en échec. `--no-dark` désactive cette recherche et cette soustraction,
pour les lights comme pour les flats. Les masters sont produits par
[darkLibUpdate](../../darkLibUpdate/README.md).

## Sélection et préparation des flats

Les flats doivent avoir des métadonnées valides et un type contenant `flat`.
La compatibilité avec les lights porte sur la caméra, le gain arrondi et le
binning. Leur température et leur durée d’exposition n’ont pas à être celles
des lights.

Si plusieurs durées de flats existent, le sous-groupe majoritaire est conservé,
avec regroupement de l’exposition arrondie à six décimales. Un seul master dark
adapté à ces flats est recherché lorsque les darks sont actifs. Une absence de
ce dark empêche de terminer le groupe ; l’absence de flats compatibles permet
de poursuivre sans correction par flat.

Les opérations Siril sont :

```text
convert <flats> -out=<travail>/flat_process
calibrate <flats> -dark=<dark_flat> -cc=dark -cfa
stack pp_<flats> median -norm=mul -out=<master_flat>
```

Avec `--no-dark`, la commande `calibrate` des flats est omise et `stack` utilise
la séquence convertie directement. Un master flat existant peut être réutilisé
si le retraitement n’est pas forcé.

## Calibration des lights

Les entrées du groupe sont préparées dans une séquence de travail, puis
converties dans `process/`. La commande dépend des masters disponibles :

| Calibrations | Arguments de `calibrate <lights>` avant choix du dématriçage |
|---|---|
| Dark et flat | `-dark=<dark> -flat=<flat> -cc=dark -cfa -equalize_cfa` |
| Dark seul | `-dark=<dark> -cc=dark -cfa` |
| Flat seul | `-flat=<flat> -cfa -equalize_cfa` |
| Sans dark ni flat | `-cfa` |

En mode `--drizzle off`, la commande contient aussi `-debayer`. En mode `auto`
ou `force`, cet argument est omis pour conserver le CFA natif jusqu’à la
décision de stacking. Une calibration RGB en cache peut donc nécessiter une
reconstruction si les sources sont CFA et que le mode demande le CFA natif.

## Export

Python copie les sorties `pp_light_*.fit(s)` de `process/` dans le dossier
`<sous-session>_<groupe>_calibrated`. Il exporte également leur séquence, via les
objets [SirilSequence et SequenceImage](../reference/SIRIL_SEQUENCE.md).
Les pixels sources ne sont pas modifiés par cet export. Les copies finales
restent utilisables après nettoyage des liens de travail.

Le groupe produit des images calibrées, sans alignement ni empilement
intermédiaire. Leur sélection pour le stack est décrite dans
[la phase suivante](STACKING.md).
