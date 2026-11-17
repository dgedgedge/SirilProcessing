# Signal sonore de fin de traitement

[postProcess](README.md) › Signal sonore

## 1. Utilisation

`postProcess.py` et `lightProcess.py`, ainsi que leurs lanceurs, jouent un
**dong après une fin réussie**, par défaut. Le signal intervient après la sortie
du contexte de protection énergétique. Il est émis une seule fois pour la
commande complète, y compris lorsque lightProcess enchaîne plusieurs sessions
et le post-traitement. Une erreur, une interruption ou `--dry-run` reste silencieux.

`--no-completion-sound` désactive le signal ; `--completion-sound` le réactive.
La priorité est **CLI > JSON > défaut**, avec mémorisation uniquement sur
`--save-config`. La clé JSON est `completion_sound` (booléen).

## 2. Classe et synthèse

[`CompletionSound`](../../../lib/completion_sound.py) hérite de
`ParameterizedComponent` : il s’agit d’un service configurable, pas d’un
traitement d’image. Les scripts principaux l’enregistrent dans `Config` et
appellent directement `CompletionSound().play(args)` après succès. Les moteurs
et orchestrateurs d’image ne déclenchent aucun son.

`CompletionSound.write_dong(path)` écrit un WAV mono PCM signé 16 bits,
22 050 Hz, de 1,2 seconde. Il combine trois partiels amortis à 392, 588 et
784 Hz, une attaque de 10 ms et une extinction de 50 ms. L’amplitude est bornée
à 50 % de la pleine échelle ; le volume entendu dépend du volume système.
La bibliothèque standard Python suffit à la synthèse.

`play(args=None)` retourne `True` si un lecteur signale une lecture réussie,
`False` si le signal est désactivé, simulé ou indisponible. Chaque appel crée
son WAV dans un dossier temporaire, puis le supprime après la lecture.
La génération et la lecture n’utilisent aucun fichier d’observation.

## 3. Compatibilité et limites

| Système | Lecture prévue |
| --- | --- |
| Linux | `paplay`, puis `pw-play`, puis `aplay`, avec repli sur `ffplay` |
| macOS | `afplay`, avec repli sur `ffplay` |
| Windows | `winsound.PlaySound`, bibliothèque standard Python |

Les exécutables sont recherchés dans `PATH`, sans shell. Chaque tentative de
lecteur externe est limitée à cinq secondes. L’absence de sortie audio ou un
échec de lecture produit un avertissement et conserve le succès du traitement.
Aucun lecteur n’est installé automatiquement.

Les tests contrôlent le format, l’amplitude et la décroissance du WAV, les
appels Linux/macOS/Windows simulés, les erreurs, les délais dépassés et le
nettoyage. Les tests CLI contrôlent un seul appel après succès et aucun après
échec ou simulation. La lecture réellement audible, notamment sur macOS et
Windows, doit être vérifiée sur les machines concernées ; les tests simulés
ne constituent pas cette validation.
