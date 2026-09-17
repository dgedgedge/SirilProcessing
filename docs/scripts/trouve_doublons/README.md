# trouve_doublons — comparaison de répertoires

[Documentation](../../README.md)


`trouve_doublons.py` cherche dans B les fichiers dont le contenu existe déjà
dans A. Il compare la taille puis l’empreinte SHA-256, et génère un script shell
avec les commandes de suppression des doublons de B. Le programme Python
n’exécute pas ces suppressions.

```bash
python3 bin/trouve_doublons.py /archives/reference /imports/a_examiner --output /tmp/nettoyage.sh
```

`--check-basename` exige également l’égalité des noms de fichiers, après la
comparaison du contenu. Sans `--output`, le fichier produit est
`supprime_doublons.sh`. Examiner ce fichier avant de l’exécuter.

Les deux dossiers doivent exister et être distincts. Les liens symboliques,
les comparaisons d’un fichier avec lui-même et le script de sortie sont exclus.
Les statistiques distinguent les paires refusées à cause du nom et les fichiers
B pour lesquels cette restriction empêche toute correspondance.
