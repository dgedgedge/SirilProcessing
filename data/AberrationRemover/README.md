# Modèle Aberration Remover 2.5.0

[Inventaire des composants tiers](../../docs/THIRD_PARTY.md) ·
[Guide de restauration](../../docs/scripts/postProcess/aberration.md)

## 1. Provenance et intégrité

Auteur : **Riccardo Alberghi** (`riccardoalberghi`).
Modèle : **Aberration Remover**, version **2.5.0**.

- [Dépôt amont](https://github.com/riccardoalberghi/abberation_models).
- [Version archivée](https://github.com/riccardoalberghi/abberation_models/releases/tag/2.5.0).
- [Fichier original](https://github.com/riccardoalberghi/abberation_models/releases/download/2.5.0/model_v2_5_0.onnx).
- Fichier local : `model_v2_5_0.onnx` ; **24 700 202 octets** (environ 23,56 Mio).
- SHA-256 : `6fd8f488b7522f6afdf75545632ef9b8b7df1765b8fa40bca13beae250ec2233`.

Le fichier est conservé sans modification. Sa taille et son empreinte ont été
comparées aux métadonnées de la publication GitHub le 17 novembre 2026.
Le fichier [SHA256SUMS](SHA256SUMS) permet de contrôler la copie locale :

```bash
cd data/AberrationRemover
sha256sum -c SHA256SUMS
```

Ce dossier est destiné à être conservé dans Git et dans les archives sources,
avec le modèle, son empreinte, cette attribution et sa licence. Les archives
d'une révision Git ne le contiennent qu'après son enregistrement dans cette
révision. Il n'est pas nécessaire de télécharger à nouveau le modèle pour
utiliser une copie complète de cette distribution.

## 2. Licence et attribution

Le modèle est distribué par son auteur sous **Creative Commons
Attribution–NonCommercial–ShareAlike 4.0 International (CC BY-NC-SA 4.0)**,
comme indiqué dans la [publication amont](https://github.com/riccardoalberghi/abberation_models/releases/tag/2.5.0).

Le texte intégral de la licence, `LICENSE-CC-BY-NC-SA-4.0.txt`, accompagne le
modèle ; il est reproduit depuis le
[répertoire SPDX des licences](https://github.com/spdx/license-list-data/blob/main/text/CC-BY-NC-SA-4.0.txt).
La [page officielle Creative Commons](https://creativecommons.org/licenses/by-nc-sa/4.0/deed.fr)
présente ses conditions en français.

L'attribution à Riccardo Alberghi, les liens de provenance et la licence doivent
accompagner les redistributions. Le modèle est réservé aux usages non
commerciaux ; les adaptations doivent être signalées et partagées sous la même
licence. La GPLv3 du code SirilProcessing ne remplace pas la licence du modèle.
La présente copie ne modifie ni les poids ni le format ONNX.

## 3. Utilisation et remerciements

Le moteur utilise ce modèle par défaut, indépendamment du dossier de lancement.
Selon la [publication 2.5.0](https://github.com/riccardoalberghi/abberation_models/releases/tag/2.5.0),
l’appliquer sur l’image linéaire après correction du fond et des couleurs,
avant tout débruitage ou autre déconvolution. Les chaînes de restauration
respectent cet ordre : Aberration Remover seul ou suivi d'un débruitage. Il
compte comme une étape de netteté et ne peut pas être associé à une autre
netteté/déconvolution ; toutes les chaînes sont limitées à deux traitements.
Pour le sélectionner explicitement depuis la racine du dépôt :

```text
--aberration-model data/AberrationRemover/model_v2_5_0.onnx
```

Un chemin relatif fourni explicitement est résolu depuis le dossier de
lancement. Le chemin par défaut est absolu, calculé depuis le module.
`--aberration-model ""` désactive le moteur. ONNX Runtime reste une dépendance
installée séparément. Voir le [guide Aberration Remover](../../docs/scripts/postProcess/aberration.md)
pour les options et les limites de validation.

Merci à **Riccardo Alberghi** pour le développement et la mise à disposition
d'Aberration Remover et de ses modèles, ainsi qu'à l'**équipe Siril** et aux
contributeurs du script de référence pour leur travail d'intégration.
L'intégration dans SirilProcessing est maintenue par ce projet ; elle
n'implique aucune approbation par les auteurs amont.
