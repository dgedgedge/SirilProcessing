"""Carte colorée des populations de mesure GHS, sans modifier le starless.

Les deux méthodes sont présentées séparément car leurs masques peuvent se
recouvrir. Voir docs/scripts/postProcess/ghs-weak-structures.md.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from lib.auto_ghs import luminance
from lib.ghs_final_stretch import ProtectedGHSFinalizer
from lib.ghs_weak_structure import WeakStructureQuality
from lib.star_separation import validate_rgb
from lib.type_defs import JSONReport


class StarlessZoneMap:
    """Dessine les masques de référence de la branche starless, à leur résolution réelle."""

    COLORS = ('#080b12', '#3d9bff', '#777777', '#ffad32', '#ec67c8', '#51e397', '#b49bff')
    PREVIEW_ASINH = 20.
    GLOBAL_LABELS = ((0, 'Inactif'), (1, 'Fond ≤ P25'), (2, 'Autres niveaux'),
                     (3, 'Diffus global P75–P95'), (4, 'Lumineux > P95'),
                     (6, 'Fond et diffus : seuils confondus'))
    SPATIAL_LABELS = ((0, 'Inactif'), (1, 'Ciel retenu par détection'), (2, 'Non classé'),
                      (4, 'Graines > P95 grille et ≥ 6 σ'), (3, 'Signal connecté ≥ 3 σ, hors graines'),
                      (5, 'Faible émission connectée : 1,5–3 σ'))

    @classmethod
    def classify(cls, data: np.ndarray, color_mode: str = 'human') -> tuple[np.ndarray, np.ndarray, JSONReport]:
        """Classe la grille de l'entrée sans mélanger quantiles globaux et objets spatiaux.

        Les seuils globaux P25/P75/P95 sont ceux du champ complet. La grille
        et les masques faibles viennent du même moteur que le score GHS.
        Les identifiants sont 0=inactif, 1=ciel, 2=autre, 3=diffus/objet,
        4=lumineux global ou graine spatiale, 5=parties faibles, 6=fond et diffus simultanément
        lorsque les seuils sont confondus. Deux tableaux séparés préservent
        les recouvrements entre méthodes. Aucune carte n'est une segmentation
        astrophysique certifiée, ni un masque de transformation des pixels.
        """
        validate_rgb(data)
        if color_mode not in ('human', 'even'):
            raise ValueError('Mode de luminosité GHS inconnu')
        stride = max(1, math.ceil(max(data.shape[1:])/WeakStructureQuality.MAX_SIDE))
        light = luminance(data[:, ::stride, ::stride], color_mode)
        thresholds = ProtectedGHSFinalizer.thresholds(data, color_mode)
        active = light > 1e-6
        global_map = np.where(active, 2, 0).astype('uint8')
        if thresholds:
            sky = active & (light <= thresholds['sky'])
            diffuse = (active & (light >= thresholds['diffuse_low'])
                       & (light <= thresholds['diffuse_high']))
            global_map[sky] = 1
            global_map[diffuse] = 3
            global_map[sky & diffuse] = 6
            global_map[active & (light > thresholds['diffuse_high'])] = 4
        weak, masks = WeakStructureQuality.classify_grid(light)
        weak['stride'] = stride
        weak_map = np.where(active, 2, 0).astype('uint8')
        if masks:
            weak_map[masks['sky']] = 1
            weak_map[masks['objects']] = 3
            weak_map[masks['faint']] = 5
            weak_map[masks['seeds']] = 4
        return global_map, weak_map, dict(
            scientific_output=False, reference_scope='starless branch input',
            sampling='regular stride; no block averaging', stride=stride,
            grid_shape=list(light.shape), thresholds=thresholds, weak_structure=weak,
            global_available=bool(thresholds),
            color_mode=color_mode,
            legend={name: {str(i): dict(label=label, color=cls.COLORS[i]) for i, label in labels}
                    for name, labels in (('global', cls.GLOBAL_LABELS), ('spatial', cls.SPATIAL_LABELS))},
            global_counts={str(i): int(np.count_nonzero(global_map == i)) for i in range(7)},
            spatial_counts={str(i): int(np.count_nonzero(weak_map == i)) for i in range(7)},
        )

    @classmethod
    def write(
        cls, data: np.ndarray, destination: Path, *, color_mode: str = 'human',
        rendered: np.ndarray | None = None, title: str = 'Starless',
    ) -> JSONReport:
        """Écrit un PNG avec légendes, vue de contexte et deux cartes de référence.

        data est l'entrée linéaire RGB normalisée, préservée. rendered fournit
        facultativement le contexte étiré de même géométrie ; il ne sert jamais
        à redéfinir les classes. Sans rendu, seul l'aperçu est étiré par asinh.
        L'origine lower reproduit l'affichage natif Siril. La carte conserve
        les cellules de mesure, sans interpolation créant de fausses frontières.
        """
        import matplotlib.pyplot as plt
        from matplotlib.colors import ListedColormap
        from matplotlib.patches import Patch

        destination = Path(destination)
        if destination.suffix.lower() != '.png':
            raise ValueError('Une carte de zones PNG est requise')
        global_map, weak_map, report = cls.classify(data, color_mode)
        stride = report['stride']
        if rendered is not None:
            validate_rgb(rendered)
            if rendered.shape != data.shape:
                raise ValueError('Contexte starless de géométrie différente')
        context = luminance((data if rendered is None else rendered)[:, ::stride, ::stride], color_mode)
        if rendered is None:
            active = context[context > 1e-6]
            if active.size:
                low, high = np.quantile(active, [.01, .998])
                context = np.clip((context-low)/max(float(high-low), 1e-7), 0, 1)
                context = np.arcsinh(cls.PREVIEW_ASINH*context)/np.arcsinh(cls.PREVIEW_ASINH)
        fig, axes = plt.subplots(1, 3, figsize=(17, 6), facecolor='#121722')
        try:
            for axis in axes:
                axis.set_facecolor('#121722')
                axis.set_xticks([])
                axis.set_yticks([])
            axes[0].imshow(context, cmap='gray', vmin=0, vmax=1, origin='lower', interpolation='nearest')
            axes[0].set_title('Contexte : rendu retenu' if rendered is not None else 'Contexte : aperçu asinh', color='white')
            for axis, plane, label, legend in zip(
                axes[1:], (global_map, weak_map),
                ('Classes de luminosité', 'Détection spatiale'), (cls.GLOBAL_LABELS, cls.SPATIAL_LABELS),
            ):
                axis.imshow(plane, cmap=ListedColormap(cls.COLORS), vmin=0, vmax=6,
                            origin='lower', interpolation='nearest')
                axis.set_title(label, color='white')
                axis.legend(handles=[Patch(color=cls.COLORS[i], label=text) for i, text in legend],
                            loc='upper center', bbox_to_anchor=(.5, -.02), fontsize=9,
                            facecolor='#202837', labelcolor='white')
            weak = report['weak_structure']
            message = ('Masques fixes sur l’entrée ; classes superposables entre méthodes.'
                       if weak['available'] else
                       ('Zones détectées, évaluation indisponible : ' if weak.get('classification_available')
                        else 'Détection spatiale indisponible : ') + weak['reason'])
            fig.suptitle(title + ' — zones utilisées pour les mesures GHS', color='white', fontsize=15)
            fig.text(.5, .025, f"{message}\nGrille {global_map.shape[1]} × {global_map.shape[0]}, pas {stride} pixels ; aucune correction locale.",
                     ha='center', color='#d7deea', fontsize=10)
            fig.subplots_adjust(top=.88, bottom=.28, wspace=.08)
            destination.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(destination, dpi=150, facecolor=fig.get_facecolor())
        finally:
            plt.close(fig)
        report['output_png'] = str(destination)
        return report
