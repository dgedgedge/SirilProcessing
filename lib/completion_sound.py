"""Signal sonore de fin de commande, synthétisé sans dépendance Python externe.

La lecture utilise un lecteur système disponible et reste facultative : une
panne audio ne transforme jamais un traitement réussi en échec.
"""

from __future__ import annotations

import argparse
from array import array
import logging
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import wave

from lib.parameterized_component import ParameterizedComponent

LOGGER = logging.getLogger(__name__)
SAMPLE_RATE = 22050  # Hz, mono PCM signé 16 bits.
DURATION = 1.2  # Secondes ; décroissance courte de type cloche.
PLAYBACK_TIMEOUT = 5.0  # Secondes, pour ne pas retenir une commande terminée.


class CompletionSound(ParameterizedComponent):
    """Produit un dong après succès, indépendamment des moteurs d'image."""

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Expose le signal de succès, mémorisable avec --save-config."""
        parser.add_argument(
            '--completion-sound', action=argparse.BooleanOptionalAction, default=True,
            help='Jouer un dong à la fin réussie du traitement (activé par défaut)',
        )

    @staticmethod
    def write_dong(path: Path) -> None:
        """Écrit un WAV mono PCM 16 bits de 1,2 seconde à 22 050 Hz.

        Le signal combine trois partiels amortis à 392, 588 et 784 Hz,
        avec une attaque de 10 ms et une extinction de 50 ms. Son amplitude
        reste sous 50 % de la pleine échelle. Les erreurs d'écriture remontent.
        """
        samples = array('h')
        count = round(SAMPLE_RATE * DURATION)
        for index in range(count):
            time = index / SAMPLE_RATE
            envelope = min(1., time / .01, (DURATION - time) / .05)
            tone = sum(weight * math.sin(2 * math.pi * frequency * time)
                       * math.exp(-decay * time)
                       for frequency, weight, decay in (
                           (392., .6, 3.), (588., .25, 4.5), (784., .15, 6.),
                       ))
            samples.append(round(16383 * envelope * tone))
        if sys.byteorder != 'little':
            samples.byteswap()
        with wave.open(str(path), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(SAMPLE_RATE)
            stream.writeframes(samples.tobytes())

    def play(self, args: argparse.Namespace | None = None) -> bool:
        """Joue une fois après succès ; retourne False si désactivé ou indisponible.

        L'appelant décide du succès et appelle ce service au niveau de la
        commande complète, jamais pour chaque sous-traitement. dry_run reste
        silencieux. La lecture Linux/macOS utilise un lecteur système trouvé
        dans PATH, avec un délai de cinq secondes par tentative ; Windows
        utilise winsound. Les fichiers temporaires sont supprimés après lecture.
        Les erreurs audio sont journalisées sans modifier le résultat du calcul.
        """
        args = self._get_args(args)
        if not getattr(args, 'completion_sound', True) or getattr(args, 'dry_run', False):
            return False
        try:
            with tempfile.TemporaryDirectory(prefix='siril-completion-') as directory:
                path = Path(directory) / 'dong.wav'
                self.write_dong(path)
                if sys.platform == 'win32':
                    import winsound

                    try:
                        winsound.PlaySound(str(path), winsound.SND_FILENAME)
                        return True
                    except RuntimeError as error:
                        LOGGER.warning('Signal sonore indisponible : %s', error)
                        return False
                players = ([('afplay', [])] if sys.platform == 'darwin' else [
                    ('paplay', []), ('pw-play', []), ('aplay', ['-q']),
                ])
                players.append(('ffplay', ['-nodisp', '-autoexit', '-loglevel', 'error']))
                for name, options in players:
                    player = shutil.which(name)
                    if player is None:
                        continue
                    try:
                        result = subprocess.run(
                            [player, *options, str(path)], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=PLAYBACK_TIMEOUT, check=False,
                        )
                        if result.returncode == 0:
                            return True
                    except (OSError, subprocess.TimeoutExpired):
                        continue
            LOGGER.warning('Signal sonore indisponible : aucun lecteur audio utilisable')
        except OSError as error:
            LOGGER.warning('Signal sonore indisponible : %s', error)
        return False
