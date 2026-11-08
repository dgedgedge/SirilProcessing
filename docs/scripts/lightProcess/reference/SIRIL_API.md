# 1. Sessions Siril et API Python

[lightProcess](../README.md) › [Références](README.md)

## 1.1. Responsabilités et prérequis

`lib.siril.Siril` sélectionne le moteur, valide son installation et fournit des
sessions autonomes. `lib.siril_api.SirilSession` possède le processus Siril,
la connexion et le journal. Le worker `siril_api_worker.py`, lancé par Siril,
utilise le véritable `sirilpy.SirilInterface` fourni avec cette installation.
La composition conserve le contrôle du cycle de vie dans le projet, sans
hériter des détails internes du client officiel.

Siril **1.4.4 ou ultérieur** et son module **sirilpy >= 1.0.25** sont requis.
Les modes natif, AppImage et Flatpak restent sélectionnés par `--siril-mode`
et `--siril-path`, avec les priorités de configuration existantes. Sous
Flatpak, la session utilise `siril-cli` et une connexion locale sur loopback.
Aucune fenêtre Siril ni lancement manuel n’est nécessaire.

`sirilpy` appartient au Python de Siril. Il n’est pas ajouté comme dépendance
PyPI du venv du projet. Le programme hôte utilise la bibliothèque standard
et NumPy, déjà présent dans ses dépendances.

## 1.2. Lancement et fermeture

La factory `Siril.session(dossier, nom, initfile=...)` retourne un context
manager. Son entrée démarre Siril sans interface et attend la connexion du
worker. Son arrêt ferme l’API et le processus possédé. Les sessions sont
indépendantes ; elles ne partagent ni connexion ni état Siril global.

Siril exige une directive `requires` pour amorcer son mode script. Deux
instructions d’amorçage sont donc transmises sur l’entrée standard :
`requires 1.4.4`, puis `pyscript` pour démarrer le worker. Elles ne contiennent
aucun traitement d’image. Les traitements passent ensuite exclusivement par
les appels de l’API ; aucun fichier `.sps` ou `.ssf` de traitement n’est généré.

Les préférences Siril de référence sont copiées dans un dossier temporaire
privé, y compris lorsqu’un `initfile` explicite est fourni. Les modifications
de préférences ne touchent donc pas les fichiers d’origine. Le dossier de
travail, `setcpu` et `setext fits` sont configurés à l’ouverture.
Une erreur ou une interruption ferme la session ; le délai de connexion est
de 120 secondes, et l’arrêt accorde 10 secondes avant de tuer le processus.

## 1.3. Appels de traitement et lecture des résultats

Les traitements existants utilisent des lots de `SirilCommand` :

```python
from pathlib import Path
from lib.siril import Siril
from lib.siril_api import command

work = Path("tmp/siril-api-example").resolve()
service = Siril(siril_mode="flatpak")
service.run_commands([
    command("set32bits"),
    command("load", work / "source.fits"),
    command("save", work / "result.fits"),
    command("close"),
], work, "copy")
```

Les fichiers d’entrée doivent exister. Les arguments sont transmis séparément,
les chemins avec espaces sont protégés selon la syntaxe Siril. Un argument
multiligne est refusé. `SirilCommand.from_text()` accepte uniquement une
commande atomique de configuration existante ; il ne lit pas de script.

Pour garder une session ouverte et interroger son état :

```python
with service.session(work, "inspection") as session:
    session.api.cmd("load", work / "source.fits")
    pixels = session.api.get_image_pixeldata()
    metadata = session.api.get_image(with_pixels=False)
```

`session.api` expose les méthodes publiques de `SirilInterface` à distance,
notamment `cmd`, `get_seq_imgdata`, `get_seq_regdata` et les accès aux images.
Les tableaux NumPy conservent leurs dimensions, leur type et leurs valeurs.
Les modèles sont reçus sous forme de `SirilRecord` : `model` indique le nom
du modèle officiel et ses champs restent accessibles comme attributs,
par exemple `record.incl` ou `record.H`. Ce sont des vues de données, pas
des instances locales des classes sirilpy. Les méthodes privées et la gestion
directe de `connect` / `disconnect` sont réservées à la session.

Le transport JSON est authentifié par un jeton de session et limité à 512 Mio
par message. Il n’utilise pas pickle. Les appels d’une session sont sérialisés.
Les tableaux, modèles, scalaires non finis et données binaires sont transportés
explicitement ; un type non pris en charge produit une erreur.

## 1.4. Rapports et erreurs

Chaque lot publie `<nom>.commands.json`, qui décrit les appels `cmd`, et
`<nom>.log`, qui conserve la sortie de Siril et de son Python. Les anciens
champs `script_path` des traitements migrés sont remplacés par `command_path`.
Les étapes conservent leur nom, par exemple `01_registration` ou `04_stacking`.
Les index HTML gardent les diagnostics et les résultats ; les plans JSON
restent des données techniques internes.

Une erreur de commande, une perte de connexion, une version incompatible ou
un arrêt prématuré empêchent le lot de réussir. Le journal est indiqué dans
l’exception ; les diagnostics d’erreur et les interruptions par signal sont
conservés. Une sortie zéro du processus sans connexion API ne suffit pas à
valider une session. Les résultats scientifiques restent contrôlés par les
processeurs d’image, indépendamment du succès de la commande.

Chaque `register` exécuté dans `run_commands` produit aussi un événement dans
`<nom>.registration.json`, capturé avant la fermeture de la séquence. Le
module `lib.registration_audit.SirilRegistrationAudit` conserve les inclusions,
les mesures et les réglages explicites. Lorsque la séquence est accessible
via l’API, il conserve aussi `Homography.pair_matched` et `Homography.Inliers`.
Sur Siril 1.4.4 en console, `load_seq` est refusé ; l’audit lit alors le `.seq`
produit par Siril, signale cette source et laisse les compteurs absents inconnus. Les événements
successifs restent séparés et une collecte incomplète est signalée. L’index
HTML présente ces données avec des liens vers leurs définitions ; les plans
et audits JSON restent internes. Voir
[l’audit de registration](../filter/stacking/IMAGE_SELECTION.md#audit-de-la-registration-avec-sirilpy).

Les motifs détaillés de rejet par image ne deviennent pas disponibles parce
que le transport utilise sirilpy : les champs exposés restent ceux de l’API.
Les décisions propres à Python restent auditées dans les rapports qualité.

## 1.5. Installation et vérification

L’[installateur commun](../../install_dependances/README.md) vérifie une
connexion réelle au moteur et à son module intégré, après l’installation des
dépendances, y compris avec `--install-only`. `--test` vérifie aussi le moteur ;
`--assets-only` ne lance pas Siril. Les options `--siril-mode` et `--siril-path`
sélectionnent le moteur à vérifier. Siril reste une installation externe.

Les tests de transport utilisent un processus local et le véritable worker,
avec une API simulée : ils couvrent les appels, tableaux, modèles, erreurs,
préférences, sessions simultanées et délais de connexion. Les tests des
traitements vérifient leurs opérations et leurs contrôles scientifiques.
Un essai réel avec Siril Flatpak 1.4.4 / sirilpy 1.0.25 a vérifié la lecture et
la sauvegarde d’un FITS synthétique 64 × 64, les chemins avec espaces, la
récupération exacte des pixels et le modèle `FFit`. Cet essai ne valide pas
les moteurs externes StarNet, les catalogues en ligne ni les modes natif et
AppImage sur toutes les plateformes. Un second essai réel a validé le parcours
registration → contrôles qualité → application de l’alignement → empilement
sur quatre poses synthétiques décalées. Les quatre poses ont été retenues,
les sources sont restées identiques et aucun script de traitement n’a été créé.
Cet essai opt-in se lance avec :

```bash
SIRIL_API_INTEGRATION=1 .venv/bin/python -m pytest -q tests/test_siril_api_integration.py
```

## 1.6. Références officielles

- [API sirilpy](https://siril.readthedocs.io/en/stable/Python-API.html).
- [Commandes Siril](https://siril.readthedocs.io/en/stable/Commands.html).
