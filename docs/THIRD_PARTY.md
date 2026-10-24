# Outils, données tierces et licences

[Documentation](README.md) › Composants tiers

## 1. Périmètre et licence du projet

Le code propre à SirilProcessing est distribué sous [GNU GPLv3](https://github.com/dgedgedge/SirilProcessing/blob/main/LICENSE).
Les données, adaptations et logiciels tiers gardent leurs licences respectives.
Cette page recense les composants directs utilisés par le projet ; les paquets
installés peuvent inclure leurs propres dépendances et notices supplémentaires.
Les versions et options d'installation sont définies par les fichiers
`requirements*.txt` à la racine et le [guide d'installation](INSTALLATION.md).

## 2. Composants conservés dans le dépôt et l'archive source

| Composant | Emplacement et utilisation | Source et licence |
|---|---|---|
| Extrait OpenNGC, Mattia Verga et contributeurs | `data/openngc/objects.csv` : positions, dimensions, magnitudes et noms des objets pour les annotations hors ligne | [Révision amont be3cd6c](https://github.com/mattiaverga/OpenNGC/tree/be3cd6c5498806a2fbb2f7e0452ef802dbb02be0), **CC BY-SA 4.0** ; [provenance et modifications](../data/openngc/README.md), [crédits amont](../data/openngc/UPSTREAM_README.md), [texte CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/legalcode.fr), également livré sous `data/openngc/LICENSE-CC-BY-SA-4.0.txt` |
| Code adapté de Cosmic Clarity, Franklin Marek | `lib/cosmic_clarity/network.py` : architecture des réseaux de restauration ; intégration et adaptations locales dans le même ensemble | [Révision amont 423acc4](https://github.com/setiastro/cosmicclarity/tree/423acc442724872ff6d81011962a8cbf5744a7f7), **MIT** ; [notice conservée dans le dépôt](https://github.com/dgedgedge/SirilProcessing/blob/main/lib/cosmic_clarity/LICENSE) |

Le répertoire `data/openngc/` représente moins de 1 Mio, notices comprises. Il
est conservé avec les sources pour que les annotations n'exigent aucun
téléchargement de catalogue à l'exécution. Les archives ZIP/tar d'une révision
Git contiennent ces fichiers dès qu'ils sont enregistrés dans cette révision.

L'extrait OpenNGC transforme les coordonnées en degrés, sélectionne les objets
utiles et conserve les valeurs absentes. Les noms français de M31/M33/M42 sont
des ajouts éditoriaux. Les modifications et l'attribution sont décrites dans son
README ; la licence des données demeure CC BY-SA 4.0, distincte de la GPL du code.
Le texte livré est reproduit depuis le [répertoire SPDX des licences](https://github.com/spdx/license-list-data/blob/main/text/CC-BY-SA-4.0.txt).

Les poids neuronaux, exécutables externes, environnements virtuels et images
personnelles ne sont pas inclus dans ces données embarquées. Les modèles
Cosmic Clarity et GraXpert sont installés séparément dans `tmp/models/`, ignoré
par Git. Le manifeste `lib/cosmic_clarity/models.json` conserve les URL, tailles
et empreintes des poids Cosmic Clarity ; il ne contient pas les poids eux-mêmes.

## 3. Outils et moteurs installés séparément

| Outil | Utilisation | Licence et référence |
|---|---|---|
| Python | Exécution des scripts | [PSF et notices associées](https://docs.python.org/3/license.html) |
| Siril | Calibration, alignement, stacking, photométrie et adaptation StarNet | [GNU GPLv3](https://gitlab.com/free-astro/siril/-/blob/master/LICENSE.md) |
| StarNet++ V2 historique | Séparation des étoiles via Siril | Licence spécifique, **non assimilable à la GPL ou à MIT** ; restrictions propres à l'archive V2, notamment au domaine d'utilisation. Lire le fichier `LICENSE` de l'[archive officielle installée](https://starnetastro.com/cli-tools/legacy/) ; binaire et modèle absents de Git |
| Application GraXpert | Traitements externes facultatifs | [GNU GPLv3](https://github.com/Steffenhir/GraXpert/blob/main/License.md) pour l'application ; les modèles téléchargés doivent être examinés selon leur distribution |
| Modèles Cosmic Clarity | Restauration native PyTorch | [Dépôt et licence MIT amont](https://github.com/setiastro/cosmicclarity/blob/main/LICENSE) ; conserver les notices accompagnant les fichiers de la version téléchargée |
| NVIDIA CUDA, cuDNN et composants NVIDIA installés par pip | Calcul GPU facultatif | [Conditions NVIDIA CUDA](https://docs.nvidia.com/cuda/eula/index.html) et licences des composants ; ces bibliothèques ne deviennent pas MIT du fait de leur utilisation par CuPy ou PyTorch |
| Chromium | Rendu des diagrammes et export PDF par Playwright | [Licence Chromium et notices tierces](https://chromium.googlesource.com/chromium/src/+/HEAD/LICENSE) ; téléchargé séparément |

KStars/Ekos fournissent les journaux lus par les visualiseurs ; leur installation
n'est pas nécessaire à la lecture d'un journal déjà disponible. Ils font partie
de [KStars, sous GPL-2.0-or-later](https://invent.kde.org/education/kstars).
Les versions de StarNet et les licences de leurs distributions successives ne
doivent pas être confondues : le projet utilise l'adaptateur V2 historique.

## 4. Bibliothèques Python directes

Ces paquets sont installés dans le venv, et ne sont pas copiés dans le dépôt.
Les notices des versions réellement installées restent la référence, notamment
pour les bibliothèques natives incluses dans les roues binaires.

| Paquet | Fonction | Licence principale / notice amont |
|---|---|---|
| NumPy | Tableaux et calcul CPU | [BSD-3-Clause et notices des composants inclus](https://github.com/numpy/numpy/blob/main/LICENSE.txt) |
| SciPy | Statistiques, optimisation et filtres | [BSD-3-Clause](https://github.com/scipy/scipy/blob/main/LICENSE.txt) |
| Astropy | FITS, WCS et coordonnées | [BSD-3-Clause](https://github.com/astropy/astropy/blob/main/LICENSE.rst) |
| Pillow | Images, texte et exports | [MIT-CMU et notices tierces](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| scikit-image | Traitements d'images | [BSD-3-Clause et notices par fichier](https://github.com/scikit-image/scikit-image/blob/main/LICENSE.txt) |
| Matplotlib | Figures et graphiques | [Licence Matplotlib basée sur PSF](https://matplotlib.org/stable/project/license.html) ; polices incluses sous leurs notices propres |
| Plotly.py | Graphiques interactifs des journaux | [MIT](https://github.com/plotly/plotly.py/blob/main/LICENSE.txt) |
| PyTorch | Inférence Cosmic Clarity | [BSD-3-Clause et notices tierces](https://github.com/pytorch/pytorch/blob/main/LICENSE) |
| ONNX Runtime / ONNX Runtime GPU | Inférence GraXpert | [MIT et notices tierces](https://github.com/microsoft/onnxruntime/blob/main/LICENSE) |
| CuPy | Calcul AutoGHS CUDA | [MIT](https://github.com/cupy/cupy/blob/main/LICENSE) ; les composants CUDA installés en complément ont leurs propres licences |
| dbus-python | Gestion énergétique Linux facultative | [MIT et notices des fichiers sous GPL/AFL](https://gitlab.freedesktop.org/dbus/dbus-python/-/blob/main/COPYING) |

## 5. Documentation et vérification

| Composant | Utilisation | Licence / référence |
|---|---|---|
| Python-Markdown | Conversion Markdown | [BSD-3-Clause](https://github.com/Python-Markdown/markdown/blob/master/LICENSE.md) |
| MkDocs | Site documentaire | [BSD-2-Clause](https://github.com/mkdocs/mkdocs/blob/master/LICENSE) |
| Pymdown Extensions | Extensions Markdown | [MIT](https://github.com/facelessuser/pymdown-extensions/blob/main/LICENSE.md) |
| Pygments | Coloration du code | [BSD-2-Clause](https://github.com/pygments/pygments/blob/master/LICENSE) |
| Playwright | Pilotage de Chromium | [Apache-2.0](https://github.com/microsoft/playwright/blob/main/LICENSE) |
| WeasyPrint | Dépendance d'export PDF | [BSD-3-Clause](https://github.com/Kozea/WeasyPrint/blob/main/LICENSE) |
| Mermaid 10.9.5 | Diagrammes automatiques | [MIT](https://github.com/mermaid-js/mermaid/blob/v10.9.5/LICENSE) ; téléchargé dans le cache et adapté par le constructeur, puis copié dans les exports HTML, sans bibliothèque JS versionnée dans le dépôt source |
| pytest / pytest-cov | Tests et couverture | [MIT — pytest](https://github.com/pytest-dev/pytest/blob/main/LICENSE), [MIT — pytest-cov](https://github.com/pytest-dev/pytest-cov/blob/master/LICENSE) |

## 6. Maintenance de cet inventaire

Lors de l'ajout d'un composant copié dans Git, conserver sa source, sa révision,
son auteur, sa licence et les modifications apportées dans une notice voisine,
puis compléter cette page. Pour une dépendance installée, mettre à jour les
requirements et le tableau correspondant. Une licence de code ne doit pas
être attribuée automatiquement à un modèle ou à un jeu de données distinct.
