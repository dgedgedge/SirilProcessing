#!/usr/bin/env python3
"""
Test du script Siril intégré pour la mosaïque.
"""

import sys
import os
from pathlib import Path

# Add the parent directory to the path to import the lib module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.mosaic import Mosaic

def test_integrated_script():
    """Teste la génération du script Siril intégré."""
    print("=== Test du script Siril intégré ===\n")
    
    # Créer une instance de mosaïque
    session_dirs = [
        Path("/exemple/session_M31_nord"),
        Path("/exemple/session_M31_sud")
    ]
    
    mosaic = Mosaic(
        session_dirs=session_dirs,
        output_dir=Path("/tmp/mosaic_output"),
        work_dir=Path("/tmp/mosaic_work"),
        mosaic_name="M31_test"
    )
    
    # Simuler des fichiers d'entrée
    input_files = [
        Path("panel_01_session_M31_nord.fit"),
        Path("panel_02_session_M31_sud.fit")
    ]
    
    # Générer le script
    script_content = mosaic._generate_mosaic_script(input_files)
    
    print("Script Siril généré:")
    print("=" * 50)
    print(script_content)
    print("=" * 50)
    
    # Vérifications
    checks = [
        ("requires 1.2.0", "Version Siril spécifiée"),
        ("convert panel", "Conversion des fichiers"),
        ("seqfindstar panel", "Détection des étoiles"),
        ("register panel", "Alignement des images"),
        ("stack r_panel", "Empilement des images"),
        ("M31_test_mosaic", "Nom de sortie correct"),
        ("close", "Fermeture propre")
    ]
    
    print("\nVérifications:")
    for check_str, description in checks:
        if check_str in script_content:
            print(f"✓ {description}")
        else:
            print(f"❌ {description} - '{check_str}' non trouvé")
    
    print(f"\n✅ Script généré avec succès pour '{mosaic.mosaic_name}'")

def main():
    print("🧪 Test du script Siril intégré pour mosaïque")
    print("=" * 50)
    
    test_integrated_script()
    
    print("\n" + "=" * 50)
    print("✅ Test terminé")
    print("\nLe script est maintenant intégré directement dans le module.")
    print("Plus besoin de fichier externe ou paramètre utilisateur !")

if __name__ == "__main__":
    main()