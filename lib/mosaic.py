#!/usr/bin/env python3
"""
Module de mosaïque pour assembler plusieurs sessions de light en utilisant Siril.

Ce module fournit des fonctionnalités pour :
- Calculer un nom de base commun à partir de plusieurs répertoires de session
- Assembler les fichiers light traités en préparation pour Siril
- Exécuter le script Siril de mosaïque
"""

import os
import shutil
import logging
from pathlib import Path
from typing import List, Optional, Tuple
from lib.siril_utils import Siril


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


class Mosaic:
    """
    Classe pour gérer la création de mosaïques à partir de plusieurs sessions light.
    """
    
    def __init__(self, output_dir: Path, work_dir: Path, 
                 mosaic_name: str, input_files: List[Path]):
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
        self.input_files = input_files
        
        # Répertoires de travail
        self.mosaic_work_dir = self.work_dir / f"mosaic_{self.mosaic_name}"
        self.mosaic_input_dir = self.mosaic_work_dir / "input"
        self.mosaic_output_dir = self.mosaic_work_dir / "output"
    
        logging.info(f"Mosaïque initialisée: {self.mosaic_name}")
        logging.info(f"Fichiers d'entrée: {len(input_files)} fichiers")
        for file in input_files:
            logging.info(f"  - {file}")
    
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
pyscript {pyecho_path} "============Sauvegarde du résultat final"
pyscript {pydir_path}

close"""
        
        return script_content
    
    def create_mosaic(self) -> Optional[Path]:
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
        siril = Siril.create_with_defaults()
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
        
        # Déplacer le fichier vers le répertoire de sortie final
        final_output = self.output_dir / f"{self.mosaic_name}_mosaic.fits"
        final_output.parent.mkdir(parents=True, exist_ok=True)
        
        if final_output.exists():
            final_output.unlink()
        
        shutil.move(str(output_file), str(final_output))
        logging.info(f"Mosaïque créée avec succès: {final_output}")
        
        return final_output
    
    def cleanup(self):
        """Nettoie les fichiers temporaires de la mosaïque."""
        if self.mosaic_work_dir.exists():
            shutil.rmtree(self.mosaic_work_dir)
            logging.info(f"Répertoire de travail nettoyé: {self.mosaic_work_dir}")