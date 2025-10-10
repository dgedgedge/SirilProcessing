#!/usr/bin/env python3
"""
Exemple d'utilisation de la nouvelle architecture Siril centralisée.
"""

import sys
import os
from pathlib import Path

# Add the parent directory to the path to import the lib module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.siril_utils import Siril
from lib.config import Config
from lib.darkprocess import DarkLib
from lib.lightprocessor import LightProcessor

def main():
    print("=== Exemple de configuration Siril centralisée ===\n")
    
    # 1. Configuration globale de Siril (une seule fois au début du programme)
    print("1. Configuration globale de Siril:")
    Siril.configure_defaults(siril_path="/usr/bin/siril", siril_mode="native")
    
    # Vérification de la configuration
    path, mode = Siril.get_default_config()
    print(f"   Configuration appliquée: path={path}, mode={mode}")
    
    # 2. Création d'instances sans paramètres Siril
    print("\n2. Création d'instances sans paramètres Siril:")
    
    config = Config()
    
    # DarkLib utilise automatiquement la configuration globale
    print("   - Création DarkLib (sans paramètres Siril)")
    darklib = DarkLib(config, force_recalc=False)
    print(f"   ✓ DarkLib créé avec Siril configuré")
    
    # LightProcessor utilise automatiquement la configuration globale  
    print("   - Création LightProcessor (sans paramètres Siril)")
    try:
        # Ces chemins sont des exemples - ils peuvent ne pas exister
        processor = LightProcessor(
            session_dir=Path("/tmp/example_session"),
            dark_library_path="/tmp/dark_lib",
            output_dir=Path("/tmp/output"),
            work_dir=Path("/tmp/work"),
            dry_run=True  # Mode simulation
        )
        print(f"   ✓ LightProcessor créé avec Siril configuré")
    except ValueError as e:
        print(f"   ⚠ LightProcessor: {e} (normal en mode exemple)")
    
    # 3. Création manuelle d'instance Siril
    print("\n3. Création manuelle d'instance Siril:")
    
    # Instance avec configuration par défaut
    siril_default = Siril.create_with_defaults()
    print(f"   - Instance par défaut: validée={siril_default.is_validated}")
    
    # Instance avec configuration spécifique (override)
    siril_custom = Siril(siril_path="custom_siril", siril_mode="flatpak")
    print(f"   - Instance personnalisée: validée={siril_custom.is_validated}")
    
    # 4. Changement de configuration globale
    print("\n4. Changement de configuration globale:")
    Siril.configure_defaults(siril_mode="flatpak")
    new_path, new_mode = Siril.get_default_config()
    print(f"   Nouvelle configuration: path={new_path}, mode={new_mode}")
    
    print("\n=== Avantages de cette approche ===")
    print("✓ Configuration centralisée dans les scripts principaux")
    print("✓ Pas de propagation de paramètres à travers les couches")
    print("✓ Simplification des signatures de constructeurs")
    print("✓ Validation automatique de la configuration Siril")
    print("✓ Possibilité d'override pour des cas spéciaux")
    print("✓ Compatibilité rétroactive maintenue")

if __name__ == "__main__":
    main()