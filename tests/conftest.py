"""
Configuration pytest et fixtures communes pour les tests SirilProcessing
"""
import pytest
import tempfile
import os
import json
import numpy as np
from pathlib import Path
from astropy.io import fits
from astropy.time import Time
import sys

# Ajouter le répertoire lib au path pour les imports
sys.path.insert(0, str(Path(__file__).parent.parent / 'lib'))

from config import Config
from fits_info import FitsInfo


@pytest.fixture
def temp_dir():
    """Répertoire temporaire pour les tests"""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture
def sample_config():
    """Configuration de test avec valeurs par défaut"""
    return {
        "dark_library_path": "/tmp/test_darklib",
        "work_dir": "/tmp/test_work", 
        "siril_path": "siril",
        "siril_mode": "flatpak",
        "temperature_precision": 0.2,
        "max_age_days": 182,
        "rejection_param1": 3.0,
        "rejection_param2": 3.0
    }


@pytest.fixture
def valid_dark_fits(temp_dir):
    """Crée un fichier FITS dark valide pour les tests"""
    filepath = temp_dir / "valid_dark.fit"
    
    # Créer données d'image simulées (dark valide)
    data = np.random.normal(100, 15, (100, 100)).astype(np.uint16)
    data = np.clip(data, 0, 65535)
    
    # Créer en-tête FITS
    header = fits.Header()
    header['IMAGETYP'] = 'dark'
    header['EXPTIME'] = 300.0
    header['CCD-TEMP'] = -15.0
    header['GAIN'] = 125.0
    header['INSTRUME'] = 'TestCamera'
    header['XBINNING'] = 1
    header['YBINNING'] = 1
    header['DATE-OBS'] = '2023-10-27T20:00:00'
    
    # Sauvegarder le fichier FITS
    hdu = fits.PrimaryHDU(data=data, header=header)
    hdu.writeto(filepath, overwrite=True)
    
    return str(filepath)


@pytest.fixture  
def invalid_dark_fits(temp_dir):
    """Crée un fichier FITS dark invalide (capot ouvert) pour les tests"""
    filepath = temp_dir / "invalid_dark.fit"
    
    # Créer données avec lumière parasite (valeurs élevées)
    data = np.random.normal(300, 100, (100, 100)).astype(np.uint16)
    data = np.clip(data, 0, 65535)
    
    # Ajouter quelques "étoiles" (pixels très brillants)
    data[25:27, 25:27] = 2000
    data[75:77, 75:77] = 1800
    
    # Créer en-tête FITS  
    header = fits.Header()
    header['IMAGETYP'] = 'dark'
    header['EXPTIME'] = 300.0
    header['CCD-TEMP'] = -15.0
    header['GAIN'] = 125.0
    header['INSTRUME'] = 'TestCamera'
    header['XBINNING'] = 1
    header['YBINNING'] = 1
    header['DATE-OBS'] = '2023-10-27T20:00:00'
    
    # Sauvegarder le fichier FITS
    hdu = fits.PrimaryHDU(data=data, header=header)
    hdu.writeto(filepath, overwrite=True)
    
    return str(filepath)


@pytest.fixture
def bias_fits(temp_dir):
    """Crée un fichier FITS bias pour les tests"""
    filepath = temp_dir / "bias.fit"
    
    # Créer données bias (très faibles valeurs)
    data = np.random.normal(50, 5, (100, 100)).astype(np.uint16)
    data = np.clip(data, 0, 65535)
    
    # Créer en-tête FITS
    header = fits.Header()
    header['IMAGETYP'] = 'bias'
    header['EXPTIME'] = 0.0
    header['CCD-TEMP'] = -15.0
    header['GAIN'] = 125.0
    header['INSTRUME'] = 'TestCamera'
    header['XBINNING'] = 1
    header['YBINNING'] = 1
    header['DATE-OBS'] = '2023-10-27T20:00:00'
    
    # Sauvegarder le fichier FITS
    hdu = fits.PrimaryHDU(data=data, header=header)
    hdu.writeto(filepath, overwrite=True)
    
    return str(filepath)


@pytest.fixture
def config_instance(temp_dir):
    """Instance Config pour les tests avec fichier temporaire"""
    config_file = temp_dir / "test_config.json"
    config = Config(str(config_file))
    return config


@pytest.fixture
def sample_dark_group(temp_dir):
    """Groupe de fichiers dark similaires pour tests de groupement"""
    files = []
    
    for i in range(3):
        filepath = temp_dir / f"dark_{i:02d}.fit"
        
        # Données similaires mais légèrement différentes
        data = np.random.normal(100 + i*2, 15, (50, 50)).astype(np.uint16)
        data = np.clip(data, 0, 65535)
        
        # En-têtes identiques (même groupe)
        header = fits.Header()
        header['IMAGETYP'] = 'dark'
        header['EXPTIME'] = 300.0
        header['CCD-TEMP'] = -15.0 + i*0.1  # Variation minime de température
        header['GAIN'] = 125.0
        header['INSTRUME'] = 'TestCamera'
        header['XBINNING'] = 1
        header['YBINNING'] = 1
        header['DATE-OBS'] = f'2023-10-27T20:{i:02d}:00'
        
        hdu = fits.PrimaryHDU(data=data, header=header)
        hdu.writeto(filepath, overwrite=True)
        files.append(str(filepath))
    
    return files