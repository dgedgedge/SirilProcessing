# Mosaïque optionnelle

[Documentation](../../README.md) › [lightProcess](README.md)

`--mosaic` demande une mosaïque à la suite des traitements des sessions.
Il faut au moins deux sessions fournies en paramètre et deux résultats de
stacking disponibles pour lancer cette étape. Plusieurs sous-sessions d’une
seule session ne constituent pas plusieurs entrées de mosaïque.

```bash
./bin/lightProcess.sh /chemin/champ_nord /chemin/champ_sud --mosaic --mosaic-name champ
```

## Entrées et nom

Le script collecte **un résultat de stacking par session**, créé pendant
l’exécution ou réutilisé depuis le cache. Aucune pose calibrée individuelle
n’est ajoutée. Une session dont le stacking échoue ne contribue pas à la
mosaïque ; les autres résultats restent utilisables s’ils sont au moins deux.
Un échec de stacking ou de mosaïque est signalé par un code de retour non nul.

`--mosaic-name` fixe le nom. Sinon, le script cherche un préfixe commun aux
sessions ayant un résultat de stacking, puis un mot commun suffisamment long. Un nom automatique de moins
de trois caractères n’est pas accepté.

## Traitement Siril

Les fichiers existants sont préparés par liens symboliques ou copies dans le
dossier de travail de la mosaïque. Les commandes principales sont :

```text
convert mosaic_ -out=<travail_mosaique>
seqplatesolve mosaic_ -force -nocache -disto=ps_distortion
seqapplyreg mosaic_ -framing=max
stack r_mosaic_ rej 3 3 -norm=addscale -output_norm -rgb_equal -maximize -overlap_norm -feather=5 -out=<nom>_mosaic
```

Ce parcours est propre à la mosaïque. Les commandes et critères du
[stack final de session](treatments/STACKING.md) sont documentés séparément.
Le mode simulation ne lance pas la création de mosaïque. L’exécution effective
journalise les fichiers d’entrée, le résultat et les erreurs rencontrées.
