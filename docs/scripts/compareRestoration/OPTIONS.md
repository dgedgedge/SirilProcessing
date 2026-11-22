# 2. Options du comparatif

[Manuel](README.md)

| Argument | Défaut | Rôle |
|---|---|---|
| `inputs [inputs ...]` | obligatoire | Rapports JSON ou répertoires à comparer |
| `-o`, `--output` | `restoration_comparison` | Préfixe des fichiers `.html` et `.csv` |
| `--recursive` | désactivé | Recherche récursive des `restoration.json` |
| `-h`, `--help` | — | Aide et sortie sans analyser de rapport |

Les chemins relatifs sont résolus depuis le dossier de lancement. Le préfixe
est utilisé littéralement : `-o bilan.html` produit `bilan.html.html` et
`bilan.html.csv`. Les options ne sont pas enregistrées dans la configuration
des traitements : ce script lit uniquement des résultats existants.
