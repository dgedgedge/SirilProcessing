#!/usr/bin/env python3
"""
Script de démonstration pour tester l'option --force-recalc
"""

import os
import sys
import tempfile
import shutil
import logging
sys.path.append('lib')

from darkprocess import DarkLib
from config import Config

def create_demo():
    """Crée une démonstration de l'option --force-recalc"""
    
    print("=== Démonstration de l'option --force-recalc ===\n")
    
    # Configuration avec force_recalc à False
    config = Config()
    config.set('temperature_precision', 0.2)
    config.set('force_recalc', False)
    
    print("1. Configuration initiale:")
    print(f"   - Précision température: {config.get('temperature_precision')}°C")
    print(f"   - Force recalcul: {config.get('force_recalc')}")
    
    # Créer une instance DarkLib
    darklib = DarkLib(config, force_recalc=False)
    
    print(f"\n2. Instance DarkLib créée:")
    print(f"   - Répertoire de bibliothèque: {darklib.dark_library_path}")
    print(f"   - Force recalcul: {darklib.force_recalc}")
    
    # Maintenant avec force_recalc à True
    config.set('force_recalc', True)
    darklib_force = DarkLib(config, force_recalc=True)
    
    print(f"\n3. Instance DarkLib avec force_recalc=True:")
    print(f"   - Répertoire de bibliothèque: {darklib_force.dark_library_path}")
    print(f"   - Force recalcul: {darklib_force.force_recalc}")
    
    print(f"\n4. Différence comportementale:")
    print(f"   - Sans force_recalc: Les master darks existants plus récents que les darks sources sont conservés")
    print(f"   - Avec force_recalc: Tous les master darks sont recréés indépendamment de leur âge")
    
    print(f"\n5. Cas d'usage de --force-recalc:")
    print(f"   - Changement de précision de température (ex: 0.2°C → 1.0°C)")
    print(f"   - Test de nouveaux paramètres de groupement")
    print(f"   - Validation après modification du code de traitement")
    print(f"   - Réparation de master darks corrompus")
    
    print(f"\n=== Fin de la démonstration ===")

if __name__ == "__main__":
    create_demo()