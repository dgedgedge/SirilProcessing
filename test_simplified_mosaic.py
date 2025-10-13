#!/usr/bin/env python3
"""
Test de la classe Mosaic simplifiée sans session_dirs.
"""

import tempfile
import logging
from pathlib import Path
from lib.mosaic import Mosaic

def test_simplified_mosaic():
    """Test la nouvelle API simplifiée de Mosaic."""
    
    logging.basicConfig(level=logging.INFO)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Créer quelques fichiers de test
        input_files = []
        for i in range(3):
            test_file = temp_path / f"session_M31_panel_{i}_stacked.fits"
            test_file.write_text(f"FAKE FITS DATA FOR SESSION {i}")
            input_files.append(test_file)
        
        # Répertoires de sortie et de travail
        output_dir = temp_path / "output"
        work_dir = temp_path / "work"
        
        try:
            print("=== Test de la nouvelle API Mosaic simplifiée ===")
            
            # Créer la mosaïque avec la nouvelle API
            mosaic = Mosaic(
                output_dir=output_dir,
                work_dir=work_dir,
                mosaic_name="M31_complete",
                input_files=input_files
            )
            
            print(f"✅ Mosaïque créée: {mosaic.mosaic_name}")
            print(f"✅ Fichiers d'entrée: {len(mosaic.input_files)}")
            
            # Préparer les fichiers
            prepared_files = mosaic.prepare_input_files()
            print(f"✅ Fichiers préparés: {len(prepared_files)}")
            
            for prepared_file in prepared_files:
                assert prepared_file.exists(), f"Fichier manquant: {prepared_file}"
                print(f"  - {prepared_file.name}")
            
            # Vérifier le script généré
            script_content = mosaic._generate_mosaic_script(prepared_files)
            assert "M31_complete" in script_content, "Le nom de mosaïque devrait être dans le script"
            print(f"✅ Script généré ({len(script_content.splitlines())} lignes)")
            
            print("✅ Test de l'API simplifiée réussi")
            return True
            
        except Exception as e:
            print(f"❌ Erreur: {e}")
            import traceback
            traceback.print_exc()
            return False

if __name__ == "__main__":
    print("Test de la classe Mosaic simplifiée...")
    success = test_simplified_mosaic()
    if success:
        print("\n🌟 La nouvelle API Mosaic simplifiée fonctionne parfaitement !")
        print("Plus besoin de session_dirs, seulement les fichiers explicites.")
    else:
        print("\n💥 La nouvelle API a échoué")
        exit(1)