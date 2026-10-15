# Gestion temporaire de l’énergie

[postProcess](README.md)

`lib.power_guard.PowerGuard` est un gestionnaire de contexte réutilisable.
`postProcess` l’active automatiquement pendant le traitement, les diagnostics
et l’export du FITS. Il demande indépendamment :

- à `org.freedesktop.login1`, un inhibiteur `sleep:idle` en mode `block` ;
- à `power-profiles-daemon`, un maintien temporaire du profil `performance`.

L’inhibiteur reste actif tant que le descripteur transféré par D-Bus reste ouvert.
Le maintien de profil utilise `HoldProfile` et son cookie, puis `ReleaseProfile`.
Le cookie conserve son type D-Bus `UInt32` (signature `u`) jusqu’à la libération :
le convertir en entier Python ordinaire changerait la signature en entier signé
et pourrait provoquer une erreur `InvalidArgs` du service.
Les noms moderne `org.freedesktop.UPower.PowerProfiles` et historique
`net.hadess.PowerProfiles` sont essayés. Une connexion privée reste ouverte
pendant le contexte et se ferme à sa sortie. Les enfants Siril n’héritent pas du
descripteur ; leur exécution est couverte par la demande du processus parent.

## Dépendance facultative

Sous Linux, si les services existent et que leurs politiques autorisent l’appel :

```bash
.venv/bin/python -m pip install -r requirements-power.txt
```

`dbus-python` peut nécessiter les bibliothèques et en-têtes système D-Bus/GLib
pour sa compilation. Son installation n’est pas automatique ni obligatoire.
Une dépendance absente, un bus inaccessible, un refus de droits ou un profil
performance non disponible produit un avertissement et laisse le traitement
continuer. L’inhibition et le maintien de profil sont indépendants. Les appels
aux services ont un délai maximal de deux secondes chacun.

## Réutilisation

```python
from lib.power_guard import PowerGuard

with PowerGuard(app_id="SirilProcessing", reason="Traitement astrophotographique"):
    run_processing_pipeline()
```

`sleep_inhibited` et `performance_held` indiquent les demandes effectivement
acquises. Un profil demandé ne garantit pas une fréquence CPU ou GPU donnée.
Les ressources sont libérées même si le traitement lève une exception ou reçoit
KeyboardInterrupt ; l’exception du traitement reste propagée. `close()` est
idempotente et ne masque pas une erreur de traitement si une libération échoue.
Aucun réglage permanent du profil n’est écrit.

Il n’y a pas de surveillance périodique : le service maintient la demande durant
la connexion. Un choix manuel de profil peut annuler ce maintien, une demande
concurrente d’économie d’énergie est prioritaire et les protections thermiques
restent actives. L’inhibiteur ne garantit pas que tous les bureaux maintiennent
l’écran allumé ni qu’une suspension forcée sera empêchée.

Références : [API PowerProfiles](https://upower.pages.freedesktop.org/power-profiles-daemon/gdbus-org.freedesktop.UPower.PowerProfiles.html),
[API logind](https://github.com/systemd/systemd/blob/main/man/org.freedesktop.login1.xml).

## Vérification

```bash
PIP_NO_INDEX=1 .venv/bin/python -m pytest -q tests/test_power_guard.py
```

Les services D-Bus sont simulés ; les tests vérifient des descripteurs Linux réels,
les indisponibilités, les deux noms de service, la libération et la propagation
des erreurs du traitement. Ils ne valident pas une transition matérielle du
profil ou une mise en veille réelle sur la machine utilisateur.
Lorsque `dbus-python` est installé, des tests supplémentaires vérifient la
signature `u` réellement sérialisée, sans accès au bus système, y compris pour
le cookie zéro et les valeurs dépassant la plage des entiers signés 32 bits.
