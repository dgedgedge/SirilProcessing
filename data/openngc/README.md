# Catalogue d’annotation OpenNGC

Données : **OpenNGC**, Mattia Verga et contributeurs.
Source : https://github.com/mattiaverga/OpenNGC/tree/be3cd6c5498806a2fbb2f7e0452ef802dbb02be0
Révision : `be3cd6c5498806a2fbb2f7e0452ef802dbb02be0` ; extraction des fichiers `database_files/NGC.csv`
et `database_files/addendum.csv`.

Cet extrait est distribué sous **CC BY-SA 4.0** :
https://creativecommons.org/licenses/by-sa/4.0/
La licence GPL du code ne remplace pas la licence de ces données.
Le texte CC BY-SA 4.0 (`LICENSE-CC-BY-SA-4.0.txt`) accompagne les données pour
conserver la notice dans l'archive source. L'[inventaire des composants tiers](../../docs/THIRD_PARTY.md)
décrit leur place dans la distribution du projet.

Modifications : sélection des objets du ciel profond, exclusion des doublons,
objets inexistants, étoiles simples/doubles, novae et entrées indéterminées ;
conservation des identifiants et types, conversion RA/Dec en degrés, magnitude
V sinon B, alias Messier. Les entrées sans coordonnées sont omises.
12159 entrées ; aucune magnitude absente n’est remplacée par zéro.
Les positions sont celles du catalogue, pas des détections sur l’image.

Les sources astronomiques originales et crédits sont conservés dans
[UPSTREAM_README.md](UPSTREAM_README.md). Les données générées d’annotation
citent cette attribution dans leur audit JSON.

L'extrait conserve également `MajAx`, `MinAx` (diamètres en minutes d'arc) et
`PosAng` (angle du nord vers l'est, en degrés) sous les colonnes
`major_arcmin`, `minor_arcmin`, `position_angle_deg`. Les valeurs absentes
restent vides. Ces dimensions servent aux contours WCS des annotations.

`common_names` conserve les alias de la colonne `Common names` d'OpenNGC.
`common_name_fr` ajoute les traductions françaises éditoriales de M31, M33 et
M42 ; les autres valeurs restent vides. Sans traduction, le nom original est
utilisé. Les identifiants astronomiques sont toujours conservés dans l'étiquette.
