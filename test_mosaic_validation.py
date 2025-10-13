#!/usr/bin/env python3
"""
Test que la validation du nom de mosaïque fonctionne toujours après la simplification.
"""

import tempfile
import logging
from pathlib import Path
from lib.mosaic import Mosaic

def test_mosaic_name_validation():
    """Test la validation du nom de mosaïque dans le constructeur."""
    
    logging.basicConfig(level=logging.INFO)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Test 1: Sessions avec noms courts (devrait échouer)
        print("=== Test 1: Sessions avec noms courts ===")
        short_session_dirs = [
            temp_path / "a",
            temp_path / "b"
        ]
        
        for session_dir in short_session_dirs:
            session_dir.mkdir()
        
        try:
            mosaic = Mosaic(
                session_dirs=short_session_dirs,
                output_dir=temp_path / "output",
                work_dir=temp_path / "work",
                mosaic_name=None  # Devrait calculer automatiquement et échouer
            )
            print("❌ Erreur: Devrait avoir échoué avec des noms courts")
            return False
        except ValueError as e:
            print(f"✅ Validation réussie: {e}")
        
        # Test 2: Sessions avec nom explicite (devrait réussir)
        print("\n=== Test 2: Nom explicite fourni ===")
        try:
            mosaic = Mosaic(
                session_dirs=short_session_dirs,
                output_dir=temp_path / "output",
                work_dir=temp_path / "work",
                mosaic_name="test_explicite"  # Nom explicite
            )
            print(f"✅ Nom explicite accepté: {mosaic.mosaic_name}")
        except Exception as e:
            print(f"❌ Erreur inattendue avec nom explicite: {e}")
            return False
        
        # Test 3: Sessions avec noms longs (devrait réussir)
        print("\n=== Test 3: Sessions avec noms longs ===")
        long_session_dirs = [
            temp_path / "session_M31_nord",
            temp_path / "session_M31_sud"
        ]
        
        for session_dir in long_session_dirs:
            session_dir.mkdir()
        
        try:
            mosaic = Mosaic(
                session_dirs=long_session_dirs,
                output_dir=temp_path / "output",
                work_dir=temp_path / "work",
                mosaic_name=None  # Devrait calculer "session_M31"
            )
            print(f"✅ Nom automatique calculé: {mosaic.mosaic_name}")
            assert "M31" in mosaic.mosaic_name, f"Le nom devrait contenir M31: {mosaic.mosaic_name}"
        except Exception as e:
            print(f"❌ Erreur inattendue avec noms longs: {e}")
            return False
    
    return True

if __name__ == "__main__":
    print("Test de la validation du nom de mosaïque...")
    success = test_mosaic_name_validation()
    if success:
        print("\n🌟 La validation fonctionne toujours correctement dans la classe Mosaic !")
    else:
        print("\n💥 La validation a échoué")
        exit(1)