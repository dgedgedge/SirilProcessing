# Index HTML des résultats de traitement

[lightProcess](README.md) › [Arborescence des sorties](output-layout.md)

## 1. Objectif et consultation

Chaque répertoire réel de résultats reçoit un `index.html`, avec navigation
vers le niveau supérieur, l’accueil et les sous-traitements. Les pages
fonctionnent hors ligne dans un navigateur, sans serveur, JavaScript ni
ressource distante. Ouvrir l’`index.html` de la racine de l’objet.

Chaque page présente :

- le rôle du traitement et les étapes scientifiques qu’il organise ;
- les sous-traitements, avec les verdicts explicitement enregistrés dans leurs rapports ;
- les images **JPEG et PNG intégrées**, cliquables pour voir l’original ;
- une synthèse visible des rapports JSON : statuts, descriptions, moteurs, résultats,
  paramètres, mesures des critères, seuils, motifs et conditions de décision ;
- un rappel des critères pour chaque combinaison de restauration, avec les contrôles
  finaux et les audits locaux reliés aux étapes, y compris les calculs réutilisés ;
- un tableau comparatif des chaînes acceptées, avec rang, mesures, score, écart et choix ;
- les vérifications texte, les synthèses des audits et les commandes Siril ;
- les liens vers les FITS, scripts, journaux, catalogues et autres résultats locaux hors JSON.

L’accueil présente les images et exports présents à la racine, dont les FITS,
JPEG finaux et PNG annotés. Le statut final reste celui des rapports : une
sortie partielle peut contenir des images intermédiaires. Aucune image n’est
convertie ou inventée pour remplir une galerie.

```text
M31/
  index.html                      # accueil, exports et navigation
  M31.fits
  M31.jpg
  M31_annotated.png
  04_restoration/
    index.html                    # restauration et synthèse de ses contrôles
    restoration_summary.txt
    00_mask_origine/
      index.html                # aperçu PNG et masque FITS
    01_denoise_siril/
      index.html                # paramètres, résultat et vérifications locales
      01_denoise_siril.fits
      verifications_locales.txt
      verification_finale/
        index.html              # résultats des tests finaux et leurs seuils
        verifications.txt
        verification.json
```

Les synthèses sont extraites des champs JSON et des rapports de traitements
imbriqués. Les gros tableaux de poses restent dans les données détaillées.
Les valeurs manquantes sont indiquées UNKNOWN ; aucun seuil n’est inventé.
Les pages reprennent les valeurs des rapports, sans relancer les mesures ni
modifier un verdict. Pour un ancien audit sans classement préconstruit, les
chaînes acceptées sont affichées par score enregistré ; les ratios FWHM
présentés peuvent être obtenus à partir des moyennes avant/après enregistrées.
Le score et le choix scientifique ne sont jamais recalculés par l’indexeur. `PASS`, `FAIL`, `UNKNOWN`, les erreurs et les traitements
non réalisés restent distincts. Les descriptions expliquent le rôle d’une
étape ; elles ne constituent pas une preuve que tous ses moteurs ont été exécutés.

## 2. Génération automatique

`lightProcess` publie les index à la fin du parcours normal, y compris lorsque
son bilan est incomplet, dans les racines de sortie et de travail existantes.
La simulation `--dry-run` ne génère pas ces pages. La reprise GHS/couleur passe
par la publication du post-traitement.

`postProcess` publie aussi les index en fin de traitement et lors d’une erreur
survenue après initialisation du journal, à partir des fichiers disponibles.
Avec le plan numéroté, la racine est celle de l’objet ; en mode autonome, c’est
le parent du rapport global, qui contient les exports finaux.
La comparaison autonome des chaînes publie les index dans le parent de son
rapport après écriture du bilan.

Une interruption avant ces points ne garantit pas la publication des pages.
La commande ci-dessous permet de les régénérer sans relancer les moteurs.
Une erreur de publication automatique est signalée dans les journaux sans
masquer le résultat scientifique du traitement.

## 3. Script de régénération : indexProcessing.py

### 3.1. Prérequis et installation

Le script utilise uniquement la bibliothèque standard du Python du projet.
Depuis la racine du dépôt, utiliser son environnement Python existant. Siril,
les modèles neuronaux et les bibliothèques de lecture FITS ne sont pas requis
pour indexer des résultats déjà présents. Aucun lanceur shell distinct n’est nécessaire.

### 3.2. Entrées, sorties et options

| Argument | Rôle | Défaut |
| --- | --- | --- |
| `directory` | Racine existante des résultats à indexer | Obligatoire |
| `-h`, `--help` | Afficher l’aide puis quitter | — |

Le script écrit seulement les `index.html` dans cette racine et ses
sous-répertoires réels non cachés. Il affiche le chemin de l’accueil sur la
sortie standard. Les acquisitions, FITS, images, rapports et journaux sont
préservés, ainsi que les dates de modification des dossiers pour ne pas
invalider la reprise des calibrations. Les index de présentation sont ignorés
dans le calcul d’ancienneté des données calibrées. Les liens relatifs entre pages et fichiers restent valides si
l’arborescence complète est déplacée. Dans les tableaux de poses, les fichiers
sont affichés par leur basename avec un lien local lorsqu’ils sont retrouvés.

### 3.3. Exemples

```bash
.venv/bin/python bin/indexProcessing.py --help
.venv/bin/python bin/indexProcessing.py tmp/html-report-example
```

Le second exemple a été exécuté sur un dossier synthétique contenant un FITS,
un JPEG final, un PNG annoté, un aperçu de masque et des audits qualité. Il
produit `tmp/html-report-example/index.html` et les pages des traitements.
Ce dossier d’essai local n’est pas livré comme donnée d’observation.
Pour des résultats réels, remplacer l’argument par la racine de l’objet.

### 3.4. Erreurs et limites

Le script renvoie `0` en cas de réussite et `1` si l’indexation échoue, par
exemple si la racine n’existe pas, n’est pas un dossier, ou n’est pas accessible.
Il refuse de remplacer un `index.html` qui ne porte pas la marque du générateur,
ou un index symbolique ; toutes les destinations sont vérifiées avant écriture.
Les erreurs d’arguments sont gérées par `argparse`.

Les dossiers symboliques ne sont pas parcourus ; les répertoires cachés sont
ignorés. Un lien FITS peut être listé sans que ses pixels soient lus. Les
rapports symboliques ne sont pas intégrés dans la page. Une erreur de lecture
ou un JSON invalide apparaît dans la page avec un lien vers le fichier.
Les textes sont échappés et les chemins encodés, y compris les accents et espaces.

Le compte rendu de restauration est généré directement depuis `restoration.json`,
y compris au-delà de 32 Mio, sans utiliser la synthèse texte.
Il présente le choix enregistré, le classement avec scores
et écarts, et les décisions de chaque combinaison. Les mesures détaillées
par région et les paramètres techniques ne sont pas affichés dans cette synthèse.

Pour borner les pages, un aperçu texte est limité à 64 000 caractères et un
JSON de plus de 32 Mio est omis de la présentation. Chaque
JSON est présenté dans la limite de 500 éléments structurés, de 50 éléments
par liste et de sept niveaux imbriqués ; les JSON complets restent internes.
La synthèse visible parcourt au plus 25 sections, quatre niveaux de rapports
imbriqués et 40 paramètres ou critères par section. Les mesures complémentaires
restent dans les audits internes.
Les index HTML ne sont pas recopiés avec les données entre étapes ; ils
sont régénérés dans leur contexte de destination.
Les FITS ne sont jamais convertis en images : les aperçus reposent sur les
JPEG/PNG effectivement présents. Les grands dossiers de poses produisent
une longue liste de liens, mais aucun chargement des pixels FITS.

## 4. Module et vérification

`lib.processing_html.ProcessingHtmlReport` sépare la publication HTML des
traitements d’image. `generate()` effectue une prévalidation puis écrit
atomiquement chaque index. `publish()` journalise les erreurs de publication
automatique ; la commande utilise `generate()` pour signaler un échec par son
code de retour. `add_arguments()` déclare l’unique argument de la commande.

`tests/test_processing_html.py` vérifie les liens locaux à tous les niveaux,
les aperçus JPEG/PNG et FITS finaux à l’accueil, l’échappement HTML, les audits
incomplets, les limites d’affichage, les relances, la préservation des sources
et le refus des pages étrangères. Les tests des pipelines vérifient également
les parcours concernés. La validation est structurelle ; elle ne constitue
pas une nouvelle validation scientifique des traitements ni une validation
visuelle dans tous les navigateurs.

### Présentation des décisions de restauration

Le tableau des sous-traitements utilise aussi cette présentation et conserve
les liens vers chaque dossier. Les noms de chaînes longs reviennent à la ligne.

Les JSON sont des sources internes : les pages ne proposent ni téléchargement
ni affichage brut de ces fichiers. La page de restauration présente la conclusion,
la règle de sélection, le classement avec les scores et écarts, puis la décision
pour chaque combinaison. Les audits techniques restent sur disque.

Les tableaux de classement, de décision et des contrôles locaux reprennent la
présentation des tableaux de qualité : titres verticaux à 90°, grille visible,
colonnes de libellés compactes, flottants à deux décimales et « ? » pour les
valeurs absentes. La légende des verdicts est affichée auprès des tableaux
concernés, sous forme de symboles colorés.
**✓ vert** indique une chaîne retenue/admissible ou un contrôle satisfait ;
**✗ rouge**, un rejet ; un **tiret blanc**, un contrôle ignoré/désactivé, une
référence ou une chaîne acceptée dont le score n’a pas été retenu. Le survol
et les libellés accessibles précisent chaque cas. Le choix et l’admissibilité
restent distincts : une chaîne acceptée mais non retenue n’est pas présentée
comme rejetée pour sa qualité.

Dans le bilan par combinaison, chaque critère possède ses colonnes Valeur,
Seuil et Verdict, plus Bloquant lorsque ce drapeau est enregistré. Les critères
non bloquants restent visibles même en cas d’échec et ne sont pas assimilés
à un rejet global. Les contrôles locaux/finals propres à une image conservent
un critère par ligne, avec la même présentation. Les scores, seuils et décisions
sont ceux de l’audit : seuls leur format d’affichage et leur disposition changent.

L’introduction du tableau des scores contient uniquement la formule enregistrée
du calcul du score. La lecture du JSON de restauration volumineux se fait avec
le parseur standard et nécessite de la mémoire proportionnelle à sa taille.

Les définitions des critères sont regroupées dans `quality-criteria.html`,
à la racine des résultats. Les noms de critères affichés dans les rapports
sont des liens relatifs vers leur définition précise (dont `diffuse`).
Les pages de calibration et les autres pages sans critère n’affichent pas ce
guide. Ce fichier commun est régénéré et protégé contre le remplacement
d’un fichier étranger, comme les index.

### Sélection des poses après registration

La page `02_registration/index.html` présente la sortie de l’alignement initial
Siril à partir de `registration.json`, avant tout contrôle Python. La page
`03_quality/index.html` présente la sélection qui suit cet alignement : contrôles
géométriques, filtres de mesures et profils stellaires. Le bilan global du
rapport `*.drizzle.json` reste présenté au niveau de la calibration, avec les
poses indépendantes et les entrées pondérées distinguées. Un motif individuel
absent du JSON n’est pas reconstruit. Les nouveaux traitements enregistrent
également l’inclusion par pose, la référence, la FWHM, la rondeur et les étoiles
détectées. Les paires appariées et inliers sont conservés lorsque l’API peut
lire l’état en mémoire. Avec Siril 1.4.4 en console, l’audit utilise le `.seq`
produit par Siril et marque ces deux compteurs inconnus. La source des données
est indiquée. Les paramètres explicites sont
présentés une fois avant le tableau ; les seuils internes non exposés restent
inconnus. Les noms de critères renvoient au glossaire commun. Les réalignements
ont leur propre audit dans leur dossier d’exécution. Voir les
[critères et limites de l’audit de registration](filter/stacking/IMAGE_SELECTION.md#audit-de-la-registration-avec-sirilpy).
Une régénération HTML seule ne récupère pas les compteurs absents des anciens
rapports ; leur collecte demande de relancer le traitement.

Les pages contenant `stellar_profiles.json`, notamment `03_quality`, présentent
les décisions par pose, motifs, R80, allongement, limites et fractions hors seuil.
Ces rejets constituent une partie du bilan global et ne s’y ajoutent pas.
Les contrôles non concluants restent distincts des rejets ; les données absentes
sont indiquées par « ? ». Les critères renvoient à leur définition commune.

Les six réglages de sélection et de pondération sont aussi reliés à leurs
définitions : plafond FWHM, pourcentage de rejet FWHM, contrôle des profils
stellaires, coefficient de leurs seuils, pondération par rondeur et par FWHM.
Les définitions distinguent rejet et pondération, expliquent la valeur zéro,
la dispersion robuste et le cumul des participations à l’empilement.

Les nouveaux traitements publient aussi les **décisions Python pour chaque
image** : contrôles effectifs, mesures, seuils, rang de sélection proportionnelle,
profils, pondération et sélection finale. Chaque critère occupe un groupe de
colonnes : mesures utilisées, rang éventuel et verdict. La validité initiale
est synthétisée dans une seule colonne **Validité**, avec les mêmes symboles
que les autres verdicts : **✓ vert** pour PASS, **✗ rouge** pour FAIL, un tiret
blanc pour un contrôle non exécuté ou désactivé et **?** pour un résultat absent. Les quatre conditions liées (mesures finies, FWHM positive, inclusion
et transformation valide) sont expliquées dans le guide commun ; leurs entrées
restent conservées dans l’audit interne. Les valeurs flottantes des tableaux
de qualité et leurs seuils sont affichés avec exactement deux décimales ; les
entiers restent entiers. Les données manquantes s’affichent « ? ». Les calculs
conservent la précision des valeurs enregistrées ; l’indexeur ne les recalcule
pas. Les contrôles géométriques successifs gardent des colonnes distinctes par
étape.
Les descriptions de règles et les motifs textuels ne sont pas répétés dans
les lignes : les en-têtes pointent vers leur calcul dans le guide commun.
Les seuils identiques sont affichés une fois avant le tableau ; s’ils varient
selon la pose, ils disposent de colonnes propres. Une barre de défilement
horizontale permet de lire les critères sans compresser les valeurs. Les titres
de colonnes sont orientés à 90° pour une lecture verticale, y compris « Pose »
et les noms des groupes de critères. Les liens de définition restent
accessibles dans les titres verticaux. La grille reste visible dans les deux
niveaux d’en-têtes ; des séparations plus marquées délimitent chaque groupe de
critères et se prolongent dans les lignes de valeurs. La colonne des fichiers
est limitée à 10 rem, avec retour à la ligne du nom complet. Les sélections
qualité, finales et des profils sont représentées par **✓ vert** (retenue) ou
**✗ rouge** (écartée). Le libellé reste disponible au survol et pour les lecteurs
d’écran ; une sélection non enregistrée reste « ? ». Les verdicts de critères
utilisent les mêmes symboles : **✓ vert** pour PASS et **✗ rouge** pour FAIL.
Un contrôle SKIPPED ou DISABLED affiche un **tiret blanc** ; le libellé au
survol distingue un contrôle non exécuté d’un contrôle désactivé. La colonne
Validité utilise également ces symboles. Une légende textuelle illustrée par
ces mêmes symboles colorés est placée immédiatement avant le tableau, après
les seuils communs.
Ces données sont lues depuis
`selection_decisions` du rapport de stacking et depuis l’audit local
`selection_decisions.json`. Les anciens rapports restent signalés comme dépourvus
de cette traçabilité ; l’indexeur ne recalcule pas les rejets.

Les seuils R80 et d’allongement sont affichés une seule fois par groupe avant
les tableaux de poses. Chaque pose indique son groupe de seuils. Les nouveaux
JSON utilisent `reference_id` et des références communes ; les anciens rapports
avec limites répétées restent lisibles, sans recalcul des seuils.

Dans les tableaux de sélection, le nom seul de chaque FITS est affiché, avec
un lien relatif vers la pose lorsqu’elle existe. Le chemin de session est
conservé pour distinguer les noms identiques de sessions différentes. Les
anciens chemins absolus sont recalés sur `01_calibration/01_session` dans
l’arborescence courante ; un fichier absent reste affiché sans lien.

La page `03_quality/index.html` indique le nombre de poses indépendantes en
entrée de l’étape qualité. Le contrôle des profils indique séparément son
effectif d’entrée, après les filtres précédents. Les nouveaux JSON conservent
ces effectifs dans `input_count` ; pour les anciens profils activés, l’effectif
est lu dans la liste des poses auditées.

### Effectifs distincts de registration et de qualité

`02_registration` présente exclusivement l’alignement initial Siril : inventaire
d’entrée, poses alignées et échecs. `03_quality` utilise cette sortie : contrôles
géométriques Python, filtres de qualité successifs puis contrôle des profils.
Le nombre de lignes conservées dans une séquence est un inventaire, pas le
nombre de poses encore admissibles.

Les nouveaux rapports publient `registration.json` et `selection_flow` pour
afficher les entrées, exclusions et sorties de chaque étape. Les sous-étapes
sont affichées sans additionner à nouveau leur bilan parent. Pour les anciens
rapports, seuls les agrégats certains sont repris des JSON : le détail des
filtres antérieurs aux profils peut manquer. La page de registration n’affiche
plus le résultat final de la sélection qualité comme sa propre sortie.
