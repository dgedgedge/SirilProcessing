# Répertoires de travail et nettoyage

[Documentation](../../README.md) › [lightProcess](README.md)


Chaque sous-session dispose de son espace de travail. Les séquences d’entrée y
référencent les acquisitions, tandis que `flat_process/` et `process/`
contiennent les sorties intermédiaires de Siril.

Lors d’un calcul effectif, ces répertoires de conversion sont nettoyés avant
leur réutilisation. Un échec du nettoyage est journalisé. Les dossiers de
séquences temporaires des lights et des flats sont nettoyés après traitement,
sauf avec `--keep-intermediate`.

Les calibrations exportées sont des copies indépendantes dans l’arborescence
de sortie. Leur conservation ne dépend pas des liens temporaires. Les étapes
du stack final utilisent leurs [dossiers dédiés](treatments/STACKING.md).

Les différences entre nettoyage automatique, `--force`, `--force-stacking`
et `--purge-target` sont décrites dans [Sorties et reprise](treatments/OUTPUTS.md).
