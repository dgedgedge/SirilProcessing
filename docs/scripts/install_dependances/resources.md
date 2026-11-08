# Ressources : modèles, Gaia astrométrique et spectres SPCC

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

<a id="resource-manager"></a>

## 1.1. Bibliothèque commune aux traitements et à l'installation

`lib.resource_manager.ResourceManager(args=None, root=None)` est le point
commun d'accès aux poids et catalogues. Il capture un instantané des options
déjà résolues et utilise `ResourceCache` pour les emplacements. L'installateur
conserve la lecture CLI/JSON et les options de désactivation ; il délègue les
acquisitions au gestionnaire. Les traitements Cosmic Clarity, GraXpert et la
photométrie utilisent ce même gestionnaire.

| Méthode | Responsabilité |
| --- | --- |
| `cosmic_model(name, directory=None)` | Préparer le poids nécessaire à l'inférence |
| `cosmic_models(directory=None)` | Préparer les six poids pour l'installation |
| `graxpert_model(provider=None)` | Préparer le modèle vérifié, éventuellement avec le fournisseur déjà configuré du calcul |
| `find_graxpert()` | Détecter un modèle local pour l'activation automatique, sans téléchargement |
| `gaia_astrometry(directory=None, acquire=False)` | Trouver le catalogue vérifié ou autoriser son installation |
| `gaia_spcc(directory=None, acquire=False, coverage=None)` | Trouver les régions vérifiées ou installer la couverture choisie |
| `file_checksum` / `matches` | Contrôler empreinte, taille en octets et présence par blocs de 1 Mio |

Les adaptateurs `cosmic_clarity.models`, `GraXpertModels`,
`GaiaCatalogueInstaller` et `GaiaSPCCInstaller` conservent les protocoles propres
à leurs ressources. Leurs vérifications utilisent les mêmes fonctions
d'intégrité du gestionnaire. Leurs API historiques restent utilisables.
Les imports des adaptateurs sont différés : importer le gestionnaire ne charge
pas les moteurs PyTorch/ONNX ni n'effectue d'acquisition.

Un fichier local conforme est réutilisé avant tout accès réseau, qu'il soit
fourni avec le dépôt Git, dans un cache ou dans un dossier explicitement choisi.
Il n'est ni retéléchargé ni remplacé pour rechercher une mise à jour. La simple
présence d'un fichier ne suffit pas : les tailles et empreintes restent celles
des manifestes distribués avec cette version du code. Le gestionnaire n'a pas
besoin d'exécuter Git pour vérifier les données.

Les politiques des appelants sont conservées : l'installation autorise
`acquire=True`, la photométrie recherche les catalogues locaux sans acquisition
implicite et conserve son comportement distant en l'absence de couverture locale vérifiée.
Les poids manquants peuvent être acquis à la demande par les moteurs. La
détection automatique GraXpert reste sans acquisition. Une destination
personnalisée explicite prime sur les pointeurs de cache lors de l'installation ;
sans destination explicite, un cache personnalisé déjà enregistré est réutilisé.
Les préférences et fichiers Siril sources restent intacts.

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

<a id="31-catalogue-spectral-spcc-complet"></a>

## 3.1. Catalogue spectral SPCC par hémisphère

Les deux catalogues sont préparés par défaut ; SPCC se limite au nord céleste. Pour compléter seulement
les spectres dans un venv existant :

```bash
./bin/install_dependances.sh --assets-only --disable-models --disable-gaia-astrometry
```

`--gaia-spcc-dir DOSSIER` remplace le dossier spectral par défaut
`tmp/cache/catalogues/gaia/spcc/`. `--gaia-dir` reste le dossier astrométrique.
`--disable-gaia` désactive les deux catalogues et prime sur leurs options
individuelles ; `--disable-gaia-astrometry` et `--disable-gaia-spcc` permettent
de les désactiver séparément. `--disable-models` (alias `--no-models`) désactive
les modèles. Les anciennes options `--with-gaia` et `--with-gaia-spcc` restent
acceptées pour activer chaque catalogue. Les désactivations sont transmises
explicitement au worker du venv. `--test` ne lance aucune acquisition.

L'option `--gaia-spcc-coverage` est transmise au worker et au gestionnaire commun :

| Sélection | Régions | Archives | Installé |
| --- | ---: | ---: | ---: |
| `north` (défaut) | 28 | 5,28 Go | 10,49 Go |
| `south` | 28 | 6,56 Go | 12,90 Go |
| `remaining` (complément du nord) | 20 | 5,27 Go | 10,38 Go |
| `all` | 48 | 10,55 Go | 20,87 Go |

Les régions sont des zones célestes HEALpix NESTED d'ordre 1 (NSIDE=2),
indexées de 0 à 47 dans le repère équatorial Gaia. Chaque fichier contient les
étoiles et spectres de cette zone. `north` couvre les déclinaisons positives,
`south` les déclinaisons négatives ; les huit régions traversant l'équateur
appartiennent aux deux sélections. Ce n'est pas le ciel visible depuis une
latitude terrestre : depuis la France, certains objets observables sont au
sud céleste et nécessitent d'étendre les données. Voir la
[géométrie HEALpix](https://healpix.sourceforge.io/doc/html/intro_Geometric_Algebraic_Propert.htm).

```bash
# Compléter le nord déjà installé, sans supprimer les fichiers existants.
./bin/install_dependances.sh --assets-only --disable-models --gaia-spcc-coverage remaining
# Ou préparer directement le ciel entier.
./bin/install_dependances.sh --assets-only --disable-models --gaia-spcc-coverage all
```

Les intersections nord/sud ont été vérifiées indépendamment avec les centres
et contours de `astropy_healpix` installé dans l'environnement Siril. Aucune
nouvelle dépendance n'est nécessaire à l'installateur pour ces ensembles fixes.

`lib.gaia_spcc.GaiaSPCCInstaller` utilise le manifeste versionné
`lib/gaia_spcc_manifest.json` : 48 fichiers Siril Gaia DR3 XP sampled 1.0.1,
couvrant le ciel entier. Ils représentent **10 551 868 571 octets compressés**
et **20 867 715 872 octets installés**. Les tailles et MD5 des archives viennent
du [dépôt officiel compressé](https://zenodo.org/records/14738271), les tailles
extraites du [dépôt officiel non compressé](https://zenodo.org/records/17988559),
et les SHA-256 de `uncompressed_files.sha256sum` du premier dépôt, sous CC-BY-4.0.
Le manifeste est livré avec le programme : aucune vérification distante de
mise à jour n'est nécessaire pour réutiliser les fichiers valides.

Le téléchargement est séquentiel, par blocs de 1 Mio. Chaque fichier valide
est conservé. Les fichiers déjà configurés dans Siril et son dossier de données
standard sont proposés à l'import : lien dur si possible, copie entre disques.
Les `.bz2.part` existants sont copiés sans modifier leur source, puis repris.
Une archive complète `.bz2` externe peut aussi être réutilisée. Le serveur doit
confirmer le décalage et la taille totale d'une réponse HTTP 206 ; s'il ignore
Range et répond 200, le transfert repart proprement de zéro. Trois essais sont
permis ; un échec conserve les préfixes reçus et les régions validées.

La taille et le MD5 de l'archive sont contrôlés avant décompression. Le fichier
extrait est borné à la taille attendue et vérifié en SHA-256 avant remplacement
atomique du `.dat`. Une archive complète corrompue est réacquise ; un `.dat`
précédent n'est pas remplacé par une réception incomplète. Les temporaires de
décompression sont supprimés après erreur ; l'archive est supprimée après succès.
L'espace libre est vérifié pour chaque acquisition : le catalogue déjà installé
reste présent et il faut en plus l'archive de la région courante et son fichier
extrait pendant sa publication. Une erreur sur une région est affichée, puis les régions suivantes sont
tentées. Le bilan final donne les fichiers en échec et les régions vérifiées ;
`SPCCInstallationError` fait échouer la commande si la sélection n'est pas prête.
Les archives et fichiers déjà validés sont conservés ; relancer reprend les
régions manquantes. Une sélection incomplète n'est pas annoncée comme prête.

Après succès, un éventuel dossier personnalisé est enregistré atomiquement dans
`spcc-location.json`, avec la couverture installée. `ResourceCache.find_spcc`
contrôle toutes les régions de cette couverture avant de proposer le catalogue
local, sans requête HTTP. Cette vérification relit les fichiers par blocs
(environ 10,49 Go pour le nord, 20,87 Go pour all) ; sa durée dépend du stockage. Le pointeur seul
ne vaut pas certificat et une sélection incomplète ou altérée n'est pas choisie.
Le catalogue local n'est utilisable que pour les zones effectivement installées ;
Siril peut refuser un champ nécessitant une région absente.

La photométrie sélectionne alors automatiquement `spcc -catalog=localgaia` et
transmet `core.catalogue_gaia_photo` dans une copie temporaire des préférences,
indépendamment du catalogue astrométrique. Le fichier utilisateur reste intact.
Le cache d'index HTTP de Siril n'est pas assimilé aux 48 fichiers de spectres.
Sans couverture locale vérifiée dans le cache du projet, le comportement précédent est
conservé. Les recherches de noms et les profils instrumentaux Siril restent
des ressources distinctes ; installer les catalogues ne garantit pas à lui seul
qu'un pipeline entier fonctionne sans réseau.

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

Les tests `test_gaia_spcc.py` vérifient sur de vraies archives miniatures la
réutilisation sans réseau, les importations, la reprise HTTP, le redémarrage
si Range est ignoré, les réponses incohérentes, les empreintes et le manque
d'espace. Les tests photométriques vérifient la sélection SPCC locale et les
préférences isolées. Un fichier réel déjà installé (région 0) a été comparé avec
succès au SHA-256 du manifeste. Le téléchargement complet des 48 régions et
une calibration SPCC réelle hors ligne ne sont pas validés par ces seuls essais.

`test_resource_manager.py` vérifie le même point d'accès pour l'installation
et le calcul, la réutilisation d'un poids fourni localement sans réseau,
les acquisitions miniatures retrouvées par un second gestionnaire, les
instantanés indépendants et les contrôles d'intégrité communs aux adaptateurs.

Le manifeste est maintenu dans Git, sans mise à jour réseau automatique. La
région 5 a une taille vérifiée de **111 253 377 octets** : sa décompression
complète a donné le SHA-256 officiel déjà épinglé. La valeur initiale de
28 794 880 octets issue des métadonnées ne correspondait pas à cette archive.
La correction n'a modifié ni l'archive ni le SHA-256 attendu. Les tests vérifient
la poursuite après échec, le bilan agrégé, les ensembles hémisphériques et la
transmission de la sélection au worker.
