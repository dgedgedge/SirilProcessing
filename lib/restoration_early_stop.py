"""Arrêt anticipé conditionnel après Aberration Remover puis GraXpert.

La politique lit les critères finaux existants, sans modifier leurs verdicts.
Un critère diagnostique FAIL ou UNKNOWN interdit l'arrêt même s'il ne rejette
pas le candidat. Voir docs/scripts/postProcess/restoration.md.
"""
from __future__ import annotations

import argparse
import math

from lib.parameterized_component import ParameterizedComponent
from lib.type_defs import JSONReport, JSONValue


class RestorationEarlyStop(ParameterizedComponent):
    """Déclare les paramètres et vérifie la suffisance du premier candidat.

    Le gain de FWHM est ``1 - ratio médian après/avant``. Le gain de bruit est
    le ``useful_noise_gain`` déjà mesuré, pas un gain déduit du score global.
    Tous les critères finaux actuels doivent être explicitement PASS, y compris
    les diagnostics non bloquants. Les mesures absentes ne valent jamais zéro.
    """

    PREFERRED_CHAIN = ('sharpen:aberration_remover', 'denoise:graxpert')
    DEFAULT_FWHM_GAIN = 0.30
    DEFAULT_NOISE_GAIN = 0.35
    REQUIRED_CRITERIA = frozenset({
        'noise', 'rings', 'diffuse', 'fwhm_mean', 'fwhm_median',
        'roundness', 'fwhm_significance', 'stars', 'stellar_flux',
        'useful_noise_gain', 'global_noise_reduction',
    })

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Expose activation et seuils relatifs, résolus par Config comme les moteurs."""
        if any(action.dest == 'restoration_early_stop' for action in parser._actions):
            return
        parser.add_argument(
            '--restoration-early-stop', action=argparse.BooleanOptionalAction,
            default=True,
            help='Essayer AR puis GraXpert en priorité et arrêter si tous les critères passent',
        )
        parser.add_argument(
            '--restoration-early-stop-fwhm-gain', type=float,
            default=cls.DEFAULT_FWHM_GAIN,
            help='Gain médian de FWHM strictement requis pour arrêter (fraction, défaut 0.30)',
        )
        parser.add_argument(
            '--restoration-early-stop-noise-gain', type=float,
            default=cls.DEFAULT_NOISE_GAIN,
            help='Gain de bruit utile strictement requis pour arrêter (fraction, défaut 0.35)',
        )

    @classmethod
    def thresholds(cls, args: argparse.Namespace) -> tuple[float, float]:
        """Valide et retourne les deux seuils finis dans [0, 1[ avant tout calcul."""
        values = []
        for name, default in (
            ('restoration_early_stop_fwhm_gain', cls.DEFAULT_FWHM_GAIN),
            ('restoration_early_stop_noise_gain', cls.DEFAULT_NOISE_GAIN),
        ):
            value = getattr(args, name, default)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not 0 <= value < 1):
                raise ValueError(f'{name} doit être un nombre fini dans [0, 1[')
            values.append(float(value))
        return values[0], values[1]

    @staticmethod
    def _number(value: JSONValue) -> float | None:
        """Lit une mesure numérique finie ; booléens et absences restent invalides."""
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value)):
            return None
        return float(value)

    @classmethod
    def assess(cls, audit: JSONReport, args: argparse.Namespace) -> JSONReport:
        """Enregistre les conditions d'arrêt sur le contrôle final de la chaîne.

        Les seuils sont stricts : 30 % ou 35 % exactement ne suffisent pas.
        Le ratio de FWHM doit être positif. Une donnée absente, un critère
        inconnu ou un échec entraîne la poursuite de la comparaison complète.
        Les critères futurs sont également contrôlés, sans liste d'exemption.
        """
        min_fwhm, min_noise = cls.thresholds(args)
        details = audit.get('score_details')
        details = details if isinstance(details, dict) else {}
        ratio = cls._number(details.get('fwhm_median_ratio'))
        gain = 1 - ratio if ratio is not None and ratio > 0 else None
        noise = cls._number(details.get('useful_noise_gain'))
        criteria = audit.get('criteria')
        criteria = criteria if isinstance(criteria, dict) else {}
        missing = sorted(cls.REQUIRED_CRITERIA.difference(criteria))
        non_pass = sorted(
            name for name, check in criteria.items()
            if not isinstance(check, dict) or check.get('status') != 'PASS'
        )
        conditions = dict(
            accepted=audit.get('status') == 'accepted',
            all_criteria_pass=not missing and not non_pass,
            # Comparer les ratios évite que 1 - 0.7 dépasse 0.30 par arrondi.
            fwhm_gain=ratio is not None and 0 < ratio < 1 - min_fwhm,
            useful_noise_gain=noise is not None and noise > min_noise,
        )
        return dict(
            eligible=all(conditions.values()), conditions=conditions,
            fwhm_gain=gain, useful_noise_gain=noise,
            minimum_fwhm_gain=min_fwhm, minimum_noise_gain=min_noise,
            missing_criteria=missing, non_pass_criteria=non_pass,
        )

    @classmethod
    def continuation_messages(cls, audit: JSONReport, assessment: JSONReport) -> list[str]:
        """Nomme chaque condition empêchant l'arrêt, sans changer son verdict.

        Les critères diagnostiques restent distingués des critères bloquants
        pour l'acceptation. Les mesures absentes ne sont jamais affichées à zéro.
        """
        messages = []
        conditions = assessment['conditions']
        if not conditions['accepted']:
            reasons = audit.get('rejection_reasons') or audit.get('reason') or audit.get('error')
            messages.append(f"candidat non accepté : statut={audit.get('status', 'UNKNOWN')} ; "
                            f"motif={reasons or 'non renseigné'}")
        criteria = audit.get('criteria', {})
        for name in assessment['missing_criteria']:
            messages.append(f'critère {name} : absent, validation impossible')
        for name in assessment['non_pass_criteria']:
            check = criteria.get(name)
            check = check if isinstance(check, dict) else {}
            role = ('diagnostique non bloquant pour l’acceptation'
                    if check.get('blocking') is False else
                    'bloquant pour l’acceptation' if check.get('blocking') is True
                    else 'rôle bloquant non renseigné')
            messages.append(
                f"critère {name} : {check.get('status') or 'UNKNOWN'} ; {role} ; "
                f"motif={check.get('reason') or 'non renseigné'} ; "
                "PASS requis pour l’arrêt anticipé"
            )
        for condition, key, threshold, label in (
            ('fwhm_gain', 'fwhm_gain', 'minimum_fwhm_gain', 'gain FWHM médiane'),
            ('useful_noise_gain', 'useful_noise_gain', 'minimum_noise_gain', 'gain bruit utile'),
        ):
            if not conditions[condition]:
                value = assessment[key]
                text = 'non mesuré' if value is None else f'{100*value:.6g} %'
                messages.append(f'{label} : {text} ; strictement supérieur à '
                                f'{100*assessment[threshold]:.6g} % requis')
        return messages
