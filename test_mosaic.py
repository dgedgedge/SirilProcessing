#!/usr/bin/env python3
"""
Script de test pour la fonctionnalité de mosaïque.
Teste principalement le calcul des noms de base communs.
"""

import sys
import os
from pathlib import Path

# Add the parent directory to the path to import the lib module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.mosaic import calculate_common_basename, Mosaic

def test_calculate_common_basename():
    """Teste la fonction de calcul du nom de base commun."""
    print("=== Tests de calcul du nom de base commun ===\n")
    
    test_cases = [
        # Format: (noms_répertoires, résultat_attendu)
        (["session_M31_nord", "session_M31_sud"], "session_M31"),
        (["NGC7000_panel1", "NGC7000_panel2", "NGC7000_panel3"], "NGC7000"),
        (["M42_rouge", "M42_vert", "M42_bleu"], "M42"),
        (["IC1396_A", "IC1396_B"], "IC1396"),
        (["session1", "session2"], "session"),
        (["A", "B"], ""),  # Trop court
        (["completely_different", "names_here"], ""),  # Aucun préfixe commun
        (["single_session"], "single_session"),  # Une seule session
        (["M31_mosaic_part_1", "M31_mosaic_part_2"], "M31_mosaic_part"),
        (["2023_10_15_M31", "2023_10_15_M42"], "2023_10_15"),
    ]
    
    for i, (session_names, expected) in enumerate(test_cases, 1):
        session_dirs = [Path(name) for name in session_names]
        result = calculate_common_basename(session_dirs)
        
        status = "✓" if result == expected else "❌"
        print(f"{i:2d}. {status} Sessions: {session_names}")
        print(f"    Résultat: '{result}' (attendu: '{expected}')")
        
        if len(result) < 3 and result:
            print(f"    ⚠️  Nom trop court ({len(result)} < 3 caractères)")
        
        print()

def test_mosaic_name_validation():
    """Teste la validation des noms de mosaïque."""
    print("=== Tests de validation des noms de mosaïque ===\n")
    
    test_cases = [
        # Format: (sessions, nom_custom, devrait_réussir)
        (["session_M31_nord", "session_M31_sud"], None, True),  # Nom auto suffisant
        (["A", "B"], None, False),  # Nom auto trop court
        (["A", "B"], "CustomName", True),  # Nom custom fourni
        (["completely_different", "names"], "ValidName", True),  # Nom custom valide
    ]
    
    for i, (session_names, custom_name, should_succeed) in enumerate(test_cases, 1):
        session_dirs = [Path(name) for name in session_names]
        
        try:
            mosaic = Mosaic(
                session_dirs=session_dirs,
                output_dir=Path("/tmp/test_output"),
                work_dir=Path("/tmp/test_work"),
                mosaic_name=custom_name
            )
            
            if should_succeed:
                print(f"{i}. ✓ Mosaïque créée: nom='{mosaic.mosaic_name}'")
            else:
                print(f"{i}. ❌ Mosaïque créée alors qu'elle devrait échouer: nom='{mosaic.mosaic_name}'")
                
        except ValueError as e:
            if not should_succeed:
                print(f"{i}. ✓ Validation échouée comme attendu: {e}")
            else:
                print(f"{i}. ❌ Validation échouée alors qu'elle devrait réussir: {e}")
        
        print()

def test_mosaic_workflow():
    """Teste le workflow complet de mosaïque (sans exécution Siril)."""
    print("=== Test du workflow de mosaïque ===\n")
    
    # Simuler des répertoires de session
    session_dirs = [
        Path("/exemple/session_M31_nord"),
        Path("/exemple/session_M31_sud")
    ]
    
    try:
        mosaic = Mosaic(
            session_dirs=session_dirs,
            output_dir=Path("/tmp/mosaic_output"),
            work_dir=Path("/tmp/mosaic_work"),
            mosaic_name="M31_complete"
        )
        
        print(f"✓ Mosaïque initialisée:")
        print(f"  - Nom: {mosaic.mosaic_name}")
        print(f"  - Sessions: {[d.name for d in mosaic.session_dirs]}")
        print(f"  - Répertoire de travail: {mosaic.mosaic_work_dir}")
        print(f"  - Répertoire d'entrée: {mosaic.mosaic_input_dir}")
        
        # Tester la préparation (sans fichiers réels)
        print(f"\n✓ Structure de mosaïque validée")
        
    except Exception as e:
        print(f"❌ Erreur lors du test de workflow: {e}")

def main():
    print("🧪 Tests du module Mosaïque")
    print("=" * 50)
    
    test_calculate_common_basename()
    test_mosaic_name_validation()
    test_mosaic_workflow()
    
    print("=" * 50)
    print("✅ Tests terminés")
    print("\nNote: Ces tests valident la logique de nommage.")
    print("Pour tester complètement, il faut:")
    print("1. Des sessions réellement traitées avec fichiers de sortie")
    print("2. Un script Siril personnalisé pour la mosaïque")
    print("3. Siril installé et configuré")

if __name__ == "__main__":
    main()