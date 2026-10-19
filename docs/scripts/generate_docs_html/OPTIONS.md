# Options du constructeur documentaire

[Documentation des scripts](../README.md) › [generate_docs_html](README.md)

<a id="options-du-constructeur"></a>

## 1. Options du constructeur

| Option du constructeur | Effet |
|---|---|
| `--source-root` | Racine du dépôt ; défaut : projet contenant le script |
| `--output-dir` | Dossier généré ; défaut : `out/` dans ce projet |
| `--no-pdf` | Construire le site et le HTML autonome sans PDF |

Chromium reste nécessaire au HTML autonome pour rendre les diagrammes.
Le premier lancement récupère Mermaid 10.9.5 via HTTPS dans
`tmp/documentation/` ; les suivants réutilisent ce cache. Les caches et `out/`
sont ignorés par Git. Le dossier de sortie est nettoyé par MkDocs et doit être
réservé à la génération ; les dossiers des sources et du cache sont refusés.
Une copie temporaire permet d’ajouter téléchargements et ressources sans modifier
les guides. Un échec de téléchargement, de navigateur, de rendu ou de construction
MkDocs bloque la CI ; elle ne publie pas un site privé des exports demandés.
