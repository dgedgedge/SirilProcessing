#!/usr/bin/env python3
"""
Module de mosaïque pour assembler plusieurs sessions de light en utilisant Siril.

Ce module fournit des fonctionnalités pour :
- Calculer un nom de base commun à partir de plusieurs répertoires de session
- Assembler les fichiers light traités en préparation pour Siril
- Exécuter le script Siril de mosaïque
"""

import os
import argparse
import json
from copy import copy
import shutil
import logging
from pathlib import Path
from typing import List, Optional, Tuple
from lib.siril_utils import Siril, create_siril_from_args
from lib.processor import processor
from lib.postprocess import GradientExtractor
from lib.mosaic_crop import crop_mosaic


def calculate_common_basename(session_dirs: List[Path]) -> str:
    """
    Calcule le nom de base commun à partir d'une liste de répertoires de session.
    
    Args:
        session_dirs: Liste des répertoires de session
        
    Returns:
        Nom de base commun (peut être vide si moins de 3 caractères)
        
    Examples:
        >>> calculate_common_basename([Path("session_M31_nord"), Path("session_M31_sud")])
        'session_M31'
        >>> calculate_common_basename([Path("NGC7000_panel1"), Path("NGC7000_panel2")])
        'NGC7000'
    """
    if not session_dirs:
        return ""
    
    # Extraire les noms de base (basename) de chaque répertoire
    basenames = [session_dir.name for session_dir in session_dirs]
    
    if len(basenames) == 1:
        return basenames[0]
    
    # Trouver le préfixe commun
    common_prefix = ""
    min_length = min(len(name) for name in basenames)
    
    for i in range(min_length):
        char = basenames[0][i]
        if all(name[i] == char for name in basenames):
            common_prefix += char
        else:
            break
    
    # Nettoyer le préfixe commun (enlever les caractères de fin non alphanumériques)
    common_prefix = common_prefix.rstrip('_-. ')
    
    # Essayer aussi de trouver un suffixe commun et l'enlever du préfixe
    if len(common_prefix) >= 3:
        return common_prefix
    
    # Si le préfixe est trop court, essayer une approche par mots
    words_sets = [set(name.replace('_', ' ').replace('-', ' ').split()) for name in basenames]
    common_words = set.intersection(*words_sets) if words_sets else set()
    
    if common_words:
        # Prendre le mot le plus long
        longest_word = max(common_words, key=len)
        if len(longest_word) >= 3:
            return longest_word
    
    return common_prefix


class Mosaic(processor):
    """
    Classe pour gérer la création de mosaïques à partir de plusieurs sessions light.
    """
    parameter_persistence = dict.fromkeys(
        ('mosaic_inputs', 'mosaic_name', 'gradient_output_dir', 'mosaic_gradient_masks'), False)
    
    def __init__(self, output_dir: Path = Path('.'), work_dir: Path = Path('.'),
                 mosaic_name: str = 'mosaic', input_files: Optional[List[Path]] = None):
        """
        Initialise la mosaïque.
        
        Args:
            output_dir: Répertoire de sortie pour la mosaïque
            work_dir: Répertoire de travail temporaire
            mosaic_name: Nom de la mosaïque
            input_files: Liste des fichiers d'entrée pour la mosaïque
        """
        self.output_dir = Path(output_dir)
        self.work_dir = Path(work_dir)
        self.mosaic_name = mosaic_name
        self.input_files = list(input_files or [])
        self.gradient_processor = GradientExtractor()
        
        # Répertoires de travail
        self.mosaic_work_dir = self.work_dir / f"mosaic_{self.mosaic_name}"
        self.mosaic_input_dir = self.mosaic_work_dir / "input"
        self.mosaic_output_dir = self.mosaic_work_dir / "output"
    
    def add_arguments(self, parser: argparse.ArgumentParser, include_inputs: bool = True) -> None:
        self.gradient_processor.add_arguments(parser)
        parser.add_argument(
            '--mosaic-gradient', action=argparse.BooleanOptionalAction, default=True,
            help='Corriger le gradient de chaque panneau avant assemblage (activé par défaut)',
        )
        parser.add_argument('--mosaic-crop', action=argparse.BooleanOptionalAction, default=True,
                            help='Recadrer la mosaïque sur un rectangle sans remplissage ; conserver aussi le FITS complet')
        parser.add_argument('--mosaic-gradient-mode', choices=['background', 'relative'], default='background',
                            help='Fond de chaque panneau et raccords (défaut), ou ancien ajustement relatif seul')
        parser.add_argument('--mosaic-gradient-border', type=float, default=.02,
                            help='Marge exclue du fond, fraction du petit côté (défaut : 0.02)')
        parser.add_argument('--mosaic-gradient-mask-growth', type=int, choices=range(6), default=1,
                            help='Dilatation du masque des objets en cellules de grille (défaut : 1)')
        parser.add_argument('--mosaic-gradient-masks', nargs='+', type=Path, metavar='FITS',
                            help='Un masque 2D par panneau, même grille et ordre ; pixels non nuls exclus')
        parser.add_argument(
            '--mosaic-name', type=str,
            help="Nom de la mosaïque (obligatoire si le nom automatique fait moins de 3 caractères)",
        )
        if include_inputs:
            parser.add_argument(
                '--mosaic-inputs', nargs='+', type=Path, metavar='FITS',
                help="Panneaux à assembler avec l’image courante (sinon input_files du constructeur)",
            )

    def set_from_args(self, args: argparse.Namespace) -> None:
        """Configure la mosaïque et son traitement de gradient implicite."""
        super().set_from_args(args)
        self.gradient_processor.set_from_args(args)

    def post_process(self, input_path: Path, output_path: Path, args=None) -> dict:
        """Assemble l'image courante et les panneaux configurés ; écrit un rapport JSON.

        Les panneaux --mosaic-inputs remplacent ceux du constructeur pour cet
        appel. L'image courante est toujours incluse, sans répétition de fichiers.
        create_mosaic() reste l'API directe pour assembler seulement input_files.
        """
        args = self._get_args(args)
        panels = getattr(args, 'mosaic_inputs', None)
        if panels is None:
            panels = self.input_files
        input_files = list(dict.fromkeys(Path(path).resolve() for path in [input_path, *panels]))
        if len(input_files) < 2:
            raise ValueError('Une mosaïque nécessite au moins deux images distinctes')
        for path in input_files:
            if not path.is_file():
                raise FileNotFoundError(f'Panneau de mosaïque introuvable : {path}')
        output_path = Path(output_path).resolve()
        operation = copy(self)
        operation.input_files = input_files
        operation.output_dir = Path(getattr(args, 'output_dir', None) or self.output_dir).resolve()
        operation.work_dir = Path(getattr(args, 'work_dir', None) or self.work_dir).resolve()
        operation.mosaic_name = getattr(args, 'mosaic_name', None) or self.mosaic_name
        if (operation.mosaic_name in ('.', '..') or
                any(character in operation.mosaic_name for character in '/\\\n\r\x00"')):
            raise ValueError('Nom de mosaïque incompatible avec un nom de fichier')
        operation.mosaic_work_dir = Path(getattr(args, 'mosaic_directory', None) or
                                        operation.work_dir / f'mosaic_{operation.mosaic_name}').resolve()
        operation.mosaic_input_dir = operation.mosaic_work_dir / 'input'
        operation.mosaic_output_dir = operation.mosaic_work_dir / 'output'
        final_image = operation.mosaic_work_dir / f'{operation.mosaic_name}_mosaic.fits'
        uncropped_image = operation.mosaic_work_dir / f'{operation.mosaic_name}_mosaic_uncropped.fits'
        if (output_path in input_files or output_path in (final_image, uncropped_image)
                or final_image in input_files or uncropped_image in input_files):
            raise ValueError('Les panneaux, le rapport JSON et la mosaïque doivent avoir des chemins distincts')
        # Keep the configured panel list reusable across calls in a sequence.
        gradient_results = []
        gradient_enabled = getattr(args, 'mosaic_gradient', True)
        try:
            operation.mosaic_work_dir.mkdir(parents=True, exist_ok=True)
            stacked_files = input_files
            processed_files = stacked_files
            if gradient_enabled:
                logging.info('Ajustement conjoint du gradient de %d panneaux', len(stacked_files))
                gradient_results = self.gradient_processor.process_mosaic(
                    stacked_files, operation.mosaic_work_dir, args=args)
                processed_files = [Path(result['output_image']).resolve() for result in gradient_results]
                if len(processed_files) != len(stacked_files) or not all(p.is_file() for p in processed_files):
                    raise RuntimeError('Le gradient conjoint doit produire une image par panneau')
            operation.input_files = processed_files
            output_image = operation.create_mosaic(args=args)
            if output_image is None or not Path(output_image).is_file():
                raise RuntimeError('Échec de la création de la mosaïque')
        finally:
            if not getattr(args, 'keep_intermediate', False):
                operation.cleanup()
        result = {
            'image_path': str(Path(input_path).resolve()),
            'input_files': [str(path) for path in input_files],
            'stacked_files': [str(path) for path in stacked_files],
            'gradient_results': gradient_results,
            'gradient_enabled': gradient_enabled,
            'output_image': str(Path(output_image).resolve()),
            'uncropped_image': getattr(operation, '_crop_result', {}).get('uncropped_image', str(Path(output_image).resolve())),
            'crop': getattr(operation, '_crop_result', None),
            'mosaic_name': operation.mosaic_name,
            'report_saved_to': str(output_path),
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
        return result
    
    def prepare_input_files(self) -> List[Path]:
        """
        Prépare les fichiers d'entrée pour la mosaïque en les copiant/liant 
        dans le répertoire de travail.
        
        Returns:
            Liste des fichiers préparés pour la mosaïque
        """
        # Créer les répertoires de travail
        self.mosaic_input_dir.mkdir(parents=True, exist_ok=True)
        self.mosaic_output_dir.mkdir(parents=True, exist_ok=True)

        prepared_files = []
        
        logging.info(f"Préparation de {len(self.input_files)} fichiers pour la mosaïque")
        
        for i, source_file in enumerate(self.input_files):
            if not source_file.exists():
                logging.warning(f"Fichier d'entrée non trouvé: {source_file}")
                continue
            
            # Nom du fichier de destination dans le répertoire d'entrée
            session_name = source_file.stem.split('_')[0]  # Extraire le nom de session du fichier
            dest_filename = f"panel_{i+1:02d}_{session_name}.fit"
            dest_path = self.mosaic_input_dir / dest_filename
            
            # Créer un lien symbolique vers le fichier source
            if dest_path.exists():
                dest_path.unlink()
            
            try:
                dest_path.symlink_to(source_file.absolute())
                prepared_files.append(dest_path)
                logging.info(f"Fichier préparé: {dest_filename} -> {source_file}")
            except OSError as e:
                # Fallback vers copie si les liens symboliques ne fonctionnent pas
                shutil.copy2(source_file, dest_path)
                prepared_files.append(dest_path)
                logging.info(f"Fichier copié: {dest_filename} <- {source_file}")
        
        if not prepared_files:
            raise ValueError("Aucun fichier d'entrée trouvé pour la mosaïque")
        
        logging.info(f"Préparation terminée: {len(prepared_files)} fichiers prêts pour la mosaïque")
        return prepared_files
    
    def _generate_mosaic_script(self, input_files: List[Path]) -> str:
        """
        Génère le script Siril pour créer la mosaïque.
        
        Args:
            input_files: Liste des fichiers d'entrée préparés
            
        Returns:
            Contenu du script Siril pour la mosaïque
        """
        input_dir = str(self.mosaic_input_dir)

        # Chemin vers les scripts Python
        script_dir = Path(__file__).parent.parent / "bin"
        pyecho_path = script_dir / "pyecho.py"
        pydir_path = script_dir / "pydir.py"

        # Script Siril pour la mosaïque
        script_content = f"""requires 1.2.0
# Script de mosaïque automatique pour {self.mosaic_name}
# Généré automatiquement par SirilProcessing

cd "{input_dir}"
pyscript {pyecho_path} "====================================================================="
pyscript {pyecho_path} "============Convert Light Frames to .fit files"
pyscript {pyecho_path} "============Convert files to sequence."
pyscript {pyecho_path} "cmd:========> convert mosaic_ -out={self.mosaic_output_dir}"
convert mosaic_ -out={self.mosaic_output_dir}

cd "{self.mosaic_output_dir}"
pyscript {pyecho_path} "====================================================================="
pyscript {pyecho_path} "============Résolution astrométrique (platesolve) de la séquence..."
seqplatesolve mosaic_ -force  -nocache -disto=ps_distortion
pyscript {pyecho_path} "====================================================================="
seqapplyreg mosaic_ -framing=max 
pyscript {pydir_path}

pyscript {pyecho_path} "====================================================================="
pyscript {pyecho_path} "============Empilement (mosaïque finale)..."
pyscript {pyecho_path} "cmd:========> stack r_mosaic_ rej 3 3 -norm=addscale -output_norm -rgb_equal -maximize -overlap_norm -feather=5 -out={self.mosaic_name}_mosaic "
stack r_mosaic_ rej 3 3 -norm=addscale -output_norm -rgb_equal -maximize -overlap_norm -feather=5 -out={self.mosaic_name}_mosaic 

pyscript {pyecho_path} "====================================================================="


pyscript {pyecho_path} "============Sauvegarde du résultat final"
pyscript {pydir_path}

close"""
        
        return script_content
    
    def create_mosaic(self, args=None) -> Optional[Path]:
        """
        Crée la mosaïque en utilisant le script Siril intégré.
        
        Returns:
            Chemin vers le fichier de mosaïque créé ou None en cas d'échec
        """
        # Préparer les fichiers d'entrée
        input_files = self.prepare_input_files()
        
        if not input_files:
            logging.error("Impossible de créer la mosaïque : aucun fichier d'entrée")
            return None
        
        # Générer le script Siril
        script_content = self._generate_mosaic_script(input_files)
        
        logging.info(f"Script Siril généré pour la mosaïque {self.mosaic_name}")
        for i, line in enumerate(script_content.split('\n'), 1):
            if line.strip():
                logging.debug(f"  {i:2d}: {line}")
        
        # Exécuter le script Siril
        siril = create_siril_from_args(args) if args is not None else Siril.create_with_defaults()
        success = siril.run_siril_script(script_content, str(self.mosaic_work_dir))
        
        if not success:
            logging.error("Échec de l'exécution du script Siril pour la mosaïque")
            return None
        
        # Chercher le fichier de sortie de la mosaïque
        potential_outputs = [
            self.mosaic_output_dir / f"{self.mosaic_name}_mosaic.fit",
            self.mosaic_output_dir / f"{self.mosaic_name}_mosaic.fits",
        ]
        
        output_file = None
        for potential_output in potential_outputs:
            if potential_output.exists():
                output_file = potential_output
                break
        
        if not output_file:
            logging.error("Fichier de mosaïque non trouvé après exécution du script Siril")
            logging.error(f"Emplacements recherchés: {[str(p) for p in potential_outputs]}")
            return None
        
        # Preserve the full mosaic outside the directories removed by cleanup,
        # before attempting any crop. A failed crop leaves this file available.
        uncropped = self.mosaic_work_dir / f"{self.mosaic_name}_mosaic_uncropped.fits"
        final_output = self.mosaic_work_dir / f"{self.mosaic_name}_mosaic.fits"
        protected = {Path(path).resolve() for path in self.input_files}
        if uncropped.resolve() in protected or final_output.resolve() in protected:
            raise ValueError('La mosaïque ne peut pas remplacer un panneau source')
        uncropped.parent.mkdir(parents=True, exist_ok=True)
        output_file.replace(uncropped)
        logging.info('Mosaïque complète conservée : %s', uncropped)
        if getattr(args, 'mosaic_crop', True):
            panels = sorted(path for path in self.mosaic_output_dir.glob('r_mosaic_*')
                            if path.suffix.lower() in ('.fit', '.fits', '.fts'))
            if len(panels) != len(self.input_files):
                panels = self.input_files
            self._crop_result = crop_mosaic(uncropped, final_output, panel_paths=panels)
            logging.info('Sortie mosaïque : %s ; rectangle %s ; %.1f %% de la surface conservée',
                         self._crop_result['output_image'], self._crop_result['bounds'], 100*self._crop_result['retained_fraction'])
            if self._crop_result.get('reason'):
                logging.warning('Recadrage écarté, mosaïque complète utilisée : %s (%s)',
                                self._crop_result['reason'], self._crop_result.get('detail') or '')
        else:
            self._crop_result = dict(enabled=False, applied=False,
                                     uncropped_image=str(uncropped.resolve()), output_image=str(uncropped.resolve()))
        return Path(self._crop_result['output_image'])

    def cleanup(self):
        """Nettoie les fichiers temporaires de la mosaïque."""
        for directory in (self.mosaic_input_dir, self.mosaic_output_dir):
            if directory.exists():
                shutil.rmtree(directory)
                logging.info("Répertoire temporaire nettoyé: %s", directory)
