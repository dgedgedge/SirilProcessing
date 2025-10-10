#!/usr/bin/env python3
"""
Script de test pour valider le comportement de validation de la classe Siril.
"""

import sys
import os
import logging

# Add the parent directory to the path to import the lib module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.siril_utils import Siril

def test_configure_defaults_validation():
    """Test de la validation lors de configure_defaults."""
    print("=== Test configure_defaults avec validation ===")
    
    # Test avec une configuration valide (flatpak)
    print("\n1. Test configuration valide (flatpak):")
    try:
        Siril.configure_defaults(siril_mode="flatpak")
        print("   ✓ Configuration flatpak acceptée")
    except ValueError as e:
        print(f"   ❌ Configuration flatpak rejetée: {e}")
    
    # Test avec un mode invalide
    print("\n2. Test configuration invalide (mode):")
    try:
        Siril.configure_defaults(siril_mode="invalid_mode")
        print("   ❌ Configuration invalide acceptée (ne devrait pas arriver)")
    except ValueError as e:
        print(f"   ✓ Configuration invalide rejetée: {e}")
    
    # Test avec un chemin invalide
    print("\n3. Test configuration invalide (chemin):")
    try:
        Siril.configure_defaults(siril_path="/chemin/inexistant/siril", siril_mode="native")
        print("   ❌ Chemin invalide accepté (ne devrait pas arriver)")
    except ValueError as e:
        print(f"   ✓ Chemin invalide rejeté: {e}")

def test_instance_creation_validation():
    """Test de la validation lors de la création d'instances."""
    print("\n=== Test création d'instances avec validation ===")
    
    # Remettre une configuration valide
    try:
        Siril.configure_defaults(siril_mode="flatpak")
    except ValueError:
        pass  # Peut échouer si flatpak n'est pas disponible
    
    # Test création instance avec configuration par défaut
    print("\n1. Test création avec configuration par défaut:")
    try:
        siril = Siril.create_with_defaults()
        print(f"   ✓ Instance créée: validée={siril.is_validated}")
    except ValueError as e:
        print(f"   ❌ Création échouée: {e}")
    
    # Test création instance avec override invalide
    print("\n2. Test création avec override invalide:")
    try:
        siril = Siril(siril_mode="mode_inexistant")
        print("   ❌ Instance avec mode invalide créée (ne devrait pas arriver)")
    except ValueError as e:
        print(f"   ✓ Instance avec mode invalide rejetée: {e}")
    
    # Test modification propriété invalide
    print("\n3. Test modification propriété avec valeur invalide:")
    try:
        siril = Siril.create_with_defaults()
        siril.siril_mode = "mode_invalide"
        print("   ❌ Modification invalide acceptée (ne devrait pas arriver)")
    except ValueError as e:
        print(f"   ✓ Modification invalide rejetée: {e}")
        print(f"   Mode restauré: {siril.siril_mode}")

def test_validation_details():
    """Test des détails de validation pour différents modes."""
    print("\n=== Test détails de validation ===")
    
    # Supprimer les logs pour ce test
    logging.disable(logging.CRITICAL)
    
    modes_to_test = ["flatpak", "native", "appimage"]
    
    for mode in modes_to_test:
        print(f"\n{mode.capitalize()}:")
        try:
            Siril.configure_defaults(siril_mode=mode)
            print(f"   ✓ Mode {mode} validé")
            
            # Test création instance
            siril = Siril.create_with_defaults()
            print(f"   ✓ Instance {mode} créée et validée")
            
        except ValueError as e:
            print(f"   ❌ Mode {mode} invalide: {e}")
    
    # Réactiver les logs
    logging.disable(logging.NOTSET)

def main():
    print("🧪 Tests de validation de la classe Siril")
    print("=" * 50)
    
    # Configuration du logging pour réduire le bruit
    logging.basicConfig(level=logging.WARNING)
    
    try:
        test_configure_defaults_validation()
        test_instance_creation_validation()
        test_validation_details()
        
        print("\n" + "=" * 50)
        print("✅ Tests de validation terminés")
        print("\nNote: Certains tests peuvent échouer selon l'environnement:")
        print("- Flatpak doit être installé pour valider le mode 'flatpak'")
        print("- Siril doit être dans le PATH pour valider le mode 'native'")
        print("- Les chemins d'AppImage doivent exister pour le mode 'appimage'")
        
    except Exception as e:
        print(f"\n❌ Erreur inattendue lors des tests: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()