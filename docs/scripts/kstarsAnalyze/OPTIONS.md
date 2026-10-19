# Options de kstarsAnalyze

[Documentation des scripts](../README.md) › [kstarsAnalyze](README.md)

<a id="options-de-ligne-de-commande"></a>

## 1. Options de ligne de commande

| Argument ou option | Rôle |
|---|---|
| `inputs` | Fichiers `.analyze`, dossiers ou motifs glob |
| `--gui` | Ouvrir l’interface Python native |
| `--html` | Générer le rapport HTML |
| `-o`, `--output` | Choisir le chemin du rapport HTML |
| `--open` | Ouvrir le rapport HTML dans le navigateur |
| `--relative` | Comparer les sessions depuis leur début |
| `--with-captures` | Garder les sessions avec acquisition démarrée, terminée ou abandonnée |
| `-h`, `--help` | Afficher l’aide complète |

Le lanceur shell transmet ces options au script Python. Les exemples du manuel
précisent le choix entre l’interface native et l’export HTML.
