"""Audit structuré de la registration : API sirilpy et séquence persistante Siril.

Les mesures et l'inclusion sont des faits Siril. L'API RegData ne publie pas
le motif individuel de rejet ni les seuils internes implicites. Aucun journal
textuel n'est interprété et aucune nouvelle sélection n'est appliquée ici.
Voir docs/scripts/lightProcess/filter/stacking/IMAGE_SELECTION.md.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path

from lib.siril_api import SirilApi, SirilCommand, SirilRecord
from lib.type_defs import JSONReport


class SirilRegistrationAudit:
    """Conserve chaque registration d'un lot avant close ou un nouvel alignement."""

    def __init__(self, path: Path, api: SirilApi) -> None:
        """Associe la destination interne à l'API de la session possédée."""
        self.path = path
        self.api = api
        self.events: list[JSONReport] = []
        self._load_error: str | None = None

    def prepare(self, operation: SirilCommand) -> None:
        """Charge la séquence dans l'état public avant son alignement.

        Les commandes de séquence CLI peuvent travailler sur une séquence
        temporaire inaccessible à get_seq(). Lorsqu'il est autorisé, load_seq
        la rend publique et conserve les compteurs en mémoire. Un refus, comme
        sur Siril 1.4.4 en console, déclenche le repli .seq lors de la capture.
        Les pixels et les critères de registration ne sont pas modifiés.
        """
        self._load_error = None
        try:
            if operation.arguments and operation.arguments[0] != '.':
                self.api.cmd('load_seq', operation.arguments[0])
        except (RuntimeError, OSError, ValueError) as error:
            self._load_error = str(error)
            logging.info('Chargement API indisponible ; audit de la séquence Siril : %s', error)

    @staticmethod
    def _finite(value: object) -> int | float | None:
        """Conserve les nombres finis ; une mesure absente ne devient jamais zéro."""
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        return value

    @staticmethod
    def parameters(operation: SirilCommand) -> JSONReport:
        """Conserve les options explicites et signale les seuils internes inconnus.

        Les valeurs absentes ne sont pas remplacées par des défauts supposés
        d'une version de Siril. Ces réglages ne suffisent pas à attribuer une
        cause à un rejet : l'API ne publie pas le verdict de chaque sous-contrôle.
        """
        options = dict(argument[1:].split('=', 1) for argument in operation.arguments
                       if argument.startswith('-') and '=' in argument)
        return dict(
            transformation=options.get('transf'), minpairs=options.get('minpairs'),
            maxstars=options.get('maxstars'), layer=options.get('layer'),
            two_pass='-2pass' in operation.arguments,
            selected_only='-selected' in operation.arguments,
            star_detection='Siril : paramètres courants de détection',
            implicit_thresholds='non exposés par l’API',
        )

    def _capture_frames(self, sequence: SirilRecord, channel: int) -> list[JSONReport]:
        """Extrait les données officielles, y compris les compteurs d'appariement.

        Les indices API sont basés sur zéro. filenum garde le numéro réel du
        fichier ; la référence peut ne pas avoir de compteurs d'appariement.
        """
        directory = Path(self.api.get_siril_wd())
        frames: list[JSONReport] = []
        for index in range(sequence.number):
            image = sequence.imgparam[index]
            if image is None:
                raise ValueError(f'Métadonnées absentes pour la pose {index}')
            registration = sequence.regparam[channel][index]
            filename = self.api.get_seq_frame_filename(index)
            source = None
            if filename:
                path = Path(filename)
                source = str((path if path.is_absolute() else directory / path).resolve())
            measurements: JSONReport = {}
            homography = None
            if registration is not None:
                measurements = {
                    key: self._finite(getattr(registration, field, None))
                    for key, field in (
                        ('fwhm', 'fwhm'), ('weighted_fwhm', 'weighted_fwhm'),
                        ('roundness', 'roundness'), ('quality', 'quality'),
                        ('background', 'background_lvl'), ('nbstars', 'number_of_stars'),
                    )
                }
                matrix = registration.H
                measurements['pair_matched'] = self._finite(getattr(matrix, 'pair_matched', None))
                measurements['inliers'] = self._finite(getattr(matrix, 'Inliers', None))
                homography = [[self._finite(getattr(matrix, f'h{row}{column}', None))
                               for column in range(3)] for row in range(3)]
            frames.append(dict(
                sequence_index=index, number=int(image.filenum), source=source,
                included=bool(image.incl), is_reference=index == sequence.reference_image,
                status='included' if image.incl else 'excluded',
                registration_available=registration is not None,
                measurements=measurements, homography=homography,
                rejection_reason=None,
            ))
        return frames

    def _capture_sequence(self, operation: SirilCommand) -> JSONReport:
        """Lit le résultat Siril persistant lorsque son état API est inaccessible.

        Siril 1.4.4 interdit load_seq en mode console. Son .seq conserve
        l'inclusion, les mesures et les matrices, mais aucun compteur de paires
        ou d'inliers. Ces données restent None. Ce repli ne parse aucun log et
        n'attribue pas de motif physique à un échec.
        """
        from lib.siril import SirilSequence

        directory = Path(self.api.get_siril_wd())
        if not operation.arguments or operation.arguments[0] == '.':
            raise ValueError('Nom de séquence explicite nécessaire pour le repli .seq')
        requested = Path(operation.arguments[0])
        if not requested.is_absolute():
            requested = directory / requested
        if requested.suffix != '.seq':
            path = Path(str(requested) + '.seq')
            alternative = Path(str(requested) + '_.seq')
            if not path.is_file() and alternative.is_file():
                path = alternative
        else:
            path = requested
        sequence = SirilSequence.read(path)
        explicit = self.parameters(operation)['layer']
        # En couleur, Siril utilise le vert ; des mesures R0 antérieures peuvent subsister.
        layer = f'R{explicit}' if explicit is not None else ('R1' if 'R1' in sequence.registration_layers else 'R0')
        frames: list[JSONReport] = []
        reference_index = int(sequence.header[6])
        for index, image in enumerate(sequence.images):
            registration = image.registrations.get(layer)
            measurements: JSONReport = {}
            homography = None
            if registration is not None:
                measurements = {
                    key: self._finite(value) for key, value in zip(
                        ('fwhm', 'weighted_fwhm', 'roundness', 'quality', 'background', 'nbstars'),
                        registration.values,
                    )
                }
                homography = [[self._finite(float(value)) for value in row]
                              for row in registration.homography]
            measurements.update(pair_matched=None, inliers=None)
            frames.append(dict(
                sequence_index=index, number=int(image.number),
                source=str(image.filename.resolve()), included=bool(image.included),
                is_reference=index == reference_index,
                status='included' if image.included else 'excluded',
                registration_available=registration is not None,
                measurements=measurements, homography=homography, rejection_reason=None,
            ))
        included = sum(frame['included'] for frame in frames)
        return dict(
            sequence=sequence.path.stem, channel=int(layer[1:]), reference_index=reference_index,
            input_count=len(frames), included_count=included, excluded_count=len(frames) - included,
            frames=frames, data_source='siril_sequence', api_data_available=False, complete=True,
        )

    def capture(self, operation: SirilCommand, *, command_error: str | None = None) -> None:
        """Publie un instantané atomique, ou une indisponibilité explicite.

        Une panne de lecture de l'API déclenche le repli .seq et reste enregistrée,
        sans transformer une commande échouée en succès ni modifier les poses.
        Les erreurs d'écriture restent fatales pour éviter un audit silencieusement
        perdu. Un instantané partiel conserve les événements précédents du lot.
        """
        event: JSONReport = dict(
            command=operation.name, arguments=list(operation.arguments),
            requested_sequence=operation.arguments[0] if operation.arguments else None,
            parameters=self.parameters(operation),
            command_succeeded=command_error is None, command_error=command_error,
            reason_available=False, frames=[], complete=False,
        )
        try:
            if self._load_error is not None:
                raise ValueError(self._load_error)
            sequence = self.api.get_seq()
            if sequence is None:
                raise ValueError('Aucune séquence exposée après registration')
            explicit = event['parameters']['layer']
            channel = int(explicit) if explicit is not None else (1 if sequence.nb_layers > 1 else 0)
            if not 0 <= channel < sequence.nb_layers:
                raise ValueError(f'Canal de registration hors séquence : {channel}')
            frames = self._capture_frames(sequence, channel)
            retained = sum(frame['included'] for frame in frames)
            event.update(
                sequence=sequence.seqname, channel=channel,
                reference_index=sequence.reference_image, input_count=len(frames),
                included_count=retained, excluded_count=len(frames) - retained,
                frames=frames, data_source='sirilpy', api_data_available=True, complete=True,
            )
        except (RuntimeError, OSError, ValueError, IndexError, AttributeError, TypeError) as error:
            event['api_error'] = str(error)
            try:
                event.update(self._capture_sequence(operation))
            except (RuntimeError, OSError, ValueError, IndexError, AttributeError, TypeError) as fallback_error:
                event['error'] = str(fallback_error)
                logging.warning('Audit de registration incomplet : %s', fallback_error)
        self.events.append(event)
        temporary = self.path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(dict(
            schema_version=1, source='siril_registration',
            complete=all(event['complete'] for event in self.events), events=self.events,
        ), indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
        temporary.replace(self.path)
