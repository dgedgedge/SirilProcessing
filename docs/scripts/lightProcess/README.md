# lightProcess — traitement des lights

[Documentation](../../README.md)


`lightProcess.py` et son wrapper `lightProcess.sh` traitent une ou plusieurs sessions, chacune
composée d’une ou plusieurs sous-sessions. Cet ensemble décrit leur utilisation
et le détail des traitements effectués.

## Utilisation

- [Guide d’utilisation](GUIDE.md) : commandes, options et vue d’ensemble du parcours.
- [Sessions et sous-sessions](SESSIONS.md) : organisation des données d’entrée.

## Traitements détaillés

Le [parcours de traitement](treatments/README.md) relie les étapes dans leur
ordre d’exécution :

1. [Découverte, regroupement et calibration](treatments/CALIBRATION.md).
2. [Filtrage et stacking](filter/stacking/README.md), dont la
   [sélection des images](filter/stacking/IMAGE_SELECTION.md), les
   [profils stellaires](filter/stacking/STELLAR_PROFILE_FILTER.md) et le
   [Drizzle](filter/stacking/DRIZZLE_SPECIFICATION.md).
3. [Alignement et empilement final](treatments/STACKING.md).
4. [Sorties, reprise et journaux](treatments/OUTPUTS.md).
5. [Mosaïque optionnelle](MOSAIC.md).

## Références

- [Séquences Siril et images associées](reference/SIRIL_SEQUENCE.md).
- [Répertoires de travail et nettoyage](WORKSPACE.md).
- [Rôle général des bibliothèques](../../architecture/LIBRARIES.md).
