"""Audits de registration : faits API, seuils explicites et absence de cause inventée."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.registration_audit import SirilRegistrationAudit
from lib.siril import Siril
from lib.siril_api import SirilRecord, command


class AuditApi:
    """API simulée avec une référence et une pose rejetée, sans motif détaillé."""

    def __init__(self, directory: Path) -> None:
        """Construit les modèles imbriqués comme le transport officiel."""
        self.directory = directory
        self.closed = False
        matrix = SirilRecord('Homography', {
            **{f'h{row}{column}': int(row == column) for row in range(3) for column in range(3)},
            'pair_matched': 0, 'Inliers': 0,
        })
        registration = SirilRecord('RegData', dict(
            fwhm=2.5, weighted_fwhm=2.7, roundness=.9, quality=float('nan'),
            background_lvl=.02, number_of_stars=48, H=matrix,
        ))
        self.sequence = SirilRecord('Sequence', dict(
            seqname='lights_', number=2, nb_layers=1, reference_image=0,
            imgparam=[SirilRecord('ImgData', dict(filenum=5, incl=True)),
                      SirilRecord('ImgData', dict(filenum=9, incl=False))],
            regparam=[[registration, None]],
        ))

    def cmd(self, name: str, *arguments: str) -> None:
        """Enregistre close pour vérifier l'ordre capture puis fermeture."""
        self.closed = name == 'close'

    def get_seq(self) -> SirilRecord:
        """Refuse une lecture trop tardive après la fermeture de la séquence."""
        assert not self.closed
        return self.sequence

    def get_siril_wd(self) -> str:
        """Retourne le répertoire courant distant."""
        return str(self.directory)

    def get_seq_frame_filename(self, frame: int) -> str:
        """Retourne des noms sans rapport arithmétique avec l'indice API."""
        return ['light 00005.fits', 'light 00009.fits'][frame]


def test_capture_preserves_siril_facts_and_unknown_rejection_reason(tmp_path: Path) -> None:
    """Référence, valeurs nulles et exclusion sans motif restent distinctes."""
    api = AuditApi(tmp_path)
    audit = SirilRegistrationAudit(tmp_path/'audit.registration.json', api)
    audit.capture(command('register', 'lights_', '-2pass', '-transf=affine', '-minpairs=12'))
    report = json.loads(audit.path.read_text())
    event = report['events'][0]
    assert report['complete'] and event['complete']
    assert event['input_count'] == 2 and event['included_count'] == event['excluded_count'] == 1
    assert event['parameters']['minpairs'] == '12'
    assert event['parameters']['maxstars'] is None
    assert event['reason_available'] is False
    first, failed = event['frames']
    assert first['number'] == 5 and first['is_reference']
    assert first['status'] == 'included' and first['measurements']['pair_matched'] == 0
    assert first['measurements']['quality'] is None
    assert failed['number'] == 9 and failed['sequence_index'] == 1
    assert failed['source'] == str(tmp_path/'light 00009.fits')
    assert failed['included'] is False and failed['rejection_reason'] is None
    assert not failed['registration_available'] and failed['measurements'] == {}


def test_channel_selection_and_multiple_registrations(tmp_path: Path) -> None:
    """Le canal vert par défaut et une surcharge explicite sont enregistrés."""
    api = AuditApi(tmp_path)
    api.sequence.fields['nb_layers'] = 3
    api.sequence.fields['regparam'] *= 3
    audit = SirilRegistrationAudit(tmp_path/'audit.registration.json', api)
    audit.capture(command('register', 'lights_', '-2pass'))
    audit.capture(command('register', 'lights_', '-2pass', '-layer=2', '-selected'),
                  command_error='registration échouée')
    report = json.loads(audit.path.read_text())
    assert [event['channel'] for event in report['events']] == [1, 2]
    assert report['events'][1]['parameters']['selected_only']
    assert report['events'][1]['command_succeeded'] is False
    assert report['events'][1]['command_error'] == 'registration échouée'


def test_unavailable_capture_is_explicit_and_keeps_previous_events(tmp_path: Path) -> None:
    """Une panne API ne publie pas de faux zéro ni de succès inventé."""
    api = AuditApi(tmp_path)
    audit = SirilRegistrationAudit(tmp_path/'audit.registration.json', api)
    audit.capture(command('register', 'lights_', '-2pass'))
    api.sequence = None
    audit.capture(command('register', 'lights_', '-2pass'))
    report = json.loads(audit.path.read_text())
    assert not report['complete'] and len(report['events']) == 2
    unavailable = report['events'][-1]
    assert unavailable['frames'] == [] and 'input_count' not in unavailable
    assert 'Aucune séquence' in unavailable['api_error']
    assert unavailable['error']


def test_run_commands_captures_each_registration_before_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le service collecte les nouveaux alignements sans perdre une étape précédente."""
    api = AuditApi(tmp_path)
    service = Siril('/bin/true', 'native')

    class Session:
        """Session contrôlée qui ferme l'API à la sortie."""

        def __init__(self) -> None:
            """Expose le contrat minimal de session au service."""
            self.api = api
            self.exit_code = 0
            self.log_path = tmp_path/'pipeline.log'

        def __enter__(self) -> Session:
            """Ouvre la session simulée."""
            return self

        def __exit__(self, *exception: object) -> None:
            """Marque la fermeture après les captures."""
            api.closed = True

    monkeypatch.setattr(service, 'session', lambda *args, **kwargs: Session())
    service.run_commands([command('register', 'lights_', '-2pass'),
                          command('register', 'lights_', '-2pass', '-transf=affine'),
                          command('close')], tmp_path, 'pipeline')
    events = json.loads((tmp_path/'pipeline.registration.json').read_text())['events']
    assert len(events) == 2 and all(event['complete'] for event in events)
    assert api.closed


@pytest.mark.parametrize('layers', [1, 3])
def test_console_fallback_preserves_inclusion_and_never_invents_pair_counts(tmp_path: Path, layers: int) -> None:
    """Le .seq garde les décisions Siril quand load_seq n'est pas autorisé."""
    import numpy as np
    from astropy.io import fits
    from lib.siril import RegistrationData, SirilSequence

    paths = [tmp_path/f'lights_{index:03d}.fits' for index in (1, 2)]
    for path in paths:
        shape = (8, 8) if layers == 1 else (3, 8, 8)
        fits.writeto(path, np.ones(shape, dtype='float32'))
    sequence = SirilSequence.from_files(tmp_path/'lights_.seq', 'lights_', paths, layers=layers)
    for image in sequence.images:
        image.registrations['R0'] = RegistrationData(2, 2.5, .9, 0, .02, 30, np.eye(3))
        if layers == 3:
            image.registrations['R1'] = RegistrationData(3, 3.5, .8, 0, .03, 40, np.eye(3))
    sequence.images[-1].included = False
    sequence.write()
    api = AuditApi(tmp_path)
    api.sequence = None
    audit = SirilRegistrationAudit(tmp_path/'audit.registration.json', api)
    audit.capture(command('register', 'lights_', '-2pass'))
    event = json.loads(audit.path.read_text())['events'][0]
    assert event['complete'] and event['data_source'] == 'siril_sequence'
    assert event['api_data_available'] is False and 'Aucune séquence' in event['api_error']
    assert event['input_count'] == 2 and event['included_count'] == 1
    assert [frame['included'] for frame in event['frames']] == [True, False]
    assert [frame['source'] for frame in event['frames']] == list(map(str, paths))
    assert event['channel'] == (1 if layers == 3 else 0)
    assert all(frame['measurements']['nbstars'] == (40 if layers == 3 else 30) for frame in event['frames'])
    assert all(frame['measurements']['pair_matched'] is None for frame in event['frames'])
    assert all(frame['measurements']['inliers'] is None for frame in event['frames'])
    assert all(frame['rejection_reason'] is None for frame in event['frames'])
