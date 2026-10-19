# Options de guideLogAnalyze

[Documentation des scripts](../README.md) › [guideLogAnalyze](README.md)

<a id="options-de-ligne-de-commande"></a>

## 1. Options de ligne de commande

| Argument ou option | Rôle |
|---|---|
| `inputs` | Fichiers `.txt`, dossiers ou motifs glob ; répertoire KStars par défaut si omis |
| `--gui` | Interface native, mode par défaut |
| `--html` | Rapport HTML sans ouvrir l’interface native |
| `-o`, `--output` | Choisir le fichier HTML de sortie |
| `--relative` | Temps relatif au début de chaque séquence |
| `--unit arcsec`, `--unit px` | Unité des erreurs de guidage |
| `-h`, `--help` | Afficher l’aide complète |

`--gui` et `--html` sont mutuellement exclusifs. Le lanceur shell transmet ces
options au script Python ; les commandes du manuel illustrent les deux modes.
