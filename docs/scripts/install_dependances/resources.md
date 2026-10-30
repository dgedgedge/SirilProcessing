# Ressources : poids de modèles et Gaia astrométrique

[Installateur](README.md) › Ressources

## 1. Responsabilités et interfaces

`lib.installation_assets.InstallationAssets` déclare les options de cache,
transmet les chemins au processus du venv et expose `install(root, args)`.
Le résultat associe les noms de ressources à leurs chemins vérifiés. La CLI
commune invoque ce module via son mode interne `--assets-worker`, sans pip.
Ce mode interne ne s'utilise pas directement : employer `--assets-only`.

Les imports scientifiques sont différés à cette étape : l'aide et l'orchestration
de l'installateur restent utilisables avec le Python système. Les commandes
sont transmises par listes d'arguments, sans interprétation shell.

Les réglages lus dans le JSON sont `cosmic_model_dir`, `graxpert_model_dir`,
`graxpert_model_version`, `graxpert_path` et `siril_mode`. Les surcharges CLI de
chemin priment. Un JSON absent utilise les défauts ; un JSON invalide échoue.
Aucune sauvegarde de configuration n'est effectuée. Le mode Siril par défaut
de cette détection est natif sur Mac, Flatpak sur Linux.

## 2. Poids des réseaux

Cosmic Clarity appelle `install_models` du module existant : les six fichiers
sont contrôlés avec les tailles, URL et SHA-256 de son manifeste versionné.
Un cache valide ne provoque aucun téléchargement. Une réception défectueuse
ne remplace pas un poids existant. Voir [Cosmic Clarity](../postProcess/cosmic-clarity.md).

GraXpert appelle `GraXpertModels.ensure_model`, conserve son pin 3.0.2 et son
SHA-256, puis réutilise/importe un cache ou acquiert le modèle par l'application
officielle. Les caches Mac Application Support sont aussi inspectés.
Voir [les prérequis et la licence GraXpert](../postProcess/graxpert.md).
La chaîne n'annonce pas une préparation complète si l'une des ressources échoue.
Les fichiers précédemment validés restent réutilisables à la relance.

## 3. Catalogue Gaia astrométrique

`lib.gaia_catalogue.GaiaCatalogueInstaller.install(directory, sources=())`
retourne le catalogue vérifié du cache commun ou installe le fichier
`siril_cat_healpix8_astro.dat`. La première recherche porte sur la destination,
puis sur les fichiers déjà configurés dans Siril. Un fichier externe conforme
est importé par lien dur, ou copié en cas de changement de disque, avant
vérification et remplacement atomique. `matches(path, size, digest)`
compare la taille en octets et le SHA-256, lu par blocs de 1 Mio.

L'extrait est épinglé sur la version 1.0.0 du
[dépôt Siril Zenodo 14692304](https://zenodo.org/records/14692304), sous CC-BY-4.0 :

| Fichier | Taille exacte, octets | SHA-256 |
|---|---:|---|
| Archive `.dat.bz2` | 1 142 276 749 | `846ad4b12c50865df0cb8c5b23453f22eec78bbe9969e17d669ae19eb49d421f` |
| Catalogue `.dat` | 1 521 132 640 | `2fa40c93fe115235d35c5050757f2ef60a326a6f3030f87be1598c016fcb2388` |

La somme de ces tailles doit être libre avant téléchargement. L'archive et le
fichier décompressé sont temporaires dans le dossier de destination, pour
permettre un remplacement final atomique sur le même système de fichiers.
Les tailles reçue et extraite sont bornées ; les deux SHA sont vérifiés. Toute
erreur conserve la destination précédente et nettoie les temporaires. L'archive
n'est pas conservée après réussite. Aucun fichier n'est ajouté au Git.

Le fichier n'est pas le catalogue spectral SPCC. Le cache est détecté
automatiquement par le traitement photométrique. Il sélectionne
`localgaia` avec une copie temporaire des préférences Siril où seul le chemin
Gaia est remplacé. Les autres paramètres SPCC sont conservés. Pour le mode
Flatpak, le dossier choisi doit également être accessible à Siril.

## 4. Vérification et limites

Les tests `test_gaia_catalogue.py` utilisent une vraie petite archive bzip2 pour
vérifier la décompression, les deux SHA, la réutilisation sans réseau et la
conservation d'un fichier existant en cas d'échec réseau/disque/intégrité.
`test_installation_assets.py` contrôle les défauts, les priorités et l'option
Gaia. Les tests d'orchestration contrôlent pip → ressources → GPU et le mode
ressources seules sans pip ni calcul GPU.

L'essai réel a vérifié les sept poids déjà en cache et le SHA-256 du catalogue
Gaia déjà installé, sans télécharger à nouveau ces fichiers volumineux. Une
installation réseau complète sur une machine vierge ou sur Mac n'est pas
certifiée par cet essai. Les téléchargements existants Cosmic/GraXpert conservent
leurs tests d'acquisition spécifiques.

Le plate solving Siril 1.4.4 a aussi été exécuté avec ce catalogue local et des
préférences isolées sur un extrait de test de M31 : résolution réussie avec
217 étoiles appariées. SPCC n'était pas inclus dans cet essai.

## 5. Cache commun et nettoyage

`lib.resource_cache.ResourceCache` résout `tmp/cache/models/` pour les modèles et
`tmp/cache/catalogues/gaia/` pour Gaia. `model_directory` normalise uniquement
les anciens défauts du dépôt ; les autres chemins restent explicites.
`register_gaia` conserve un chemin personnalisé dans `location.json`, écrit
atomiquement ; `find_gaia` vérifie taille et SHA-256 avant utilisation. Cette
vérification relit 1,52 Go par blocs, sans charger le catalogue en mémoire.
Un fichier de localisation corrompu ou un catalogue modifié n'est pas utilisé.

`reset_models` efface les arbres modèles dédiés courant et ancien du projet,
et seulement les poids connus dans les dossiers personnalisés. Les autres
fichiers de ces dossiers, Gaia et les caches des applications restent intacts.
Les liens de dossiers dans l'arbre projet et les cibles dangereuses sont
refusés. L'installateur appelle ce nettoyage lors de `--reset`, même avec
`--no-models` ; les poids ne sont alors pas préchargés de nouveau.

Les tests `test_resource_cache.py` couvrent ces propriétés et les tests
photométriques vérifient la sélection locale automatique et les préférences
temporaires. Les anciens modèles sont importés uniquement après vérification
des manifestes des moteurs, sans téléchargement réseau si leur cache est valide.
