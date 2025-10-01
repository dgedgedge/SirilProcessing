"""
Tests unitaires pour le module fits_info.py
Tests la lecture FITS, validation des darks, et statistiques d'image.
"""
import pytest
import numpy as np
from pathlib import Path

from fits_info import FitsInfo


class TestFitsInfoBasic:
    """Tests de base pour la classe FitsInfo"""
    
    def test_valid_dark_detection(self, valid_dark_fits):
        """Test que les darks valides sont correctement détectés"""
        info = FitsInfo(valid_dark_fits)
        
        assert info.is_dark() is True
        assert info.validData() is True
        assert info.temperature() == -15.0
        assert info.exptime() == 300.0
        assert info.gain() == 125.0
        assert info.camera() == "TestCamera"
        assert info.binning() == "1x1"

    def test_invalid_dark_detection(self, invalid_dark_fits):
        """Test que les darks invalides sont correctement détectés"""
        info = FitsInfo(invalid_dark_fits)
        
        assert info.is_dark() is True
        assert info.validData() is True


class TestFitsInfoStatistics:
    """Tests pour l'analyse statistique des images"""
    
    def test_analyze_image_statistics_valid_dark(self, valid_dark_fits):
        """Test l'analyse statistique d'un dark valide"""
        info = FitsInfo(valid_dark_fits)
        stats = info.analyze_image_statistics()
        
        assert stats is not None
        assert 'median' in stats
        assert 'std' in stats
        assert 'hot_pixels_percent' in stats
        
        # Vérifier que les valeurs sont dans les bonnes plages
        assert 80 <= stats['median'] <= 120  # Médiane autour de 100
        assert 10 <= stats['std'] <= 20      # Écart-type autour de 15
        assert stats['hot_pixels_percent'] < 0.1  # Très peu de pixels chauds

    def test_analyze_image_statistics_invalid_dark(self, invalid_dark_fits):
        """Test l'analyse statistique d'un dark invalide"""
        info = FitsInfo(invalid_dark_fits)
        stats = info.analyze_image_statistics()
        
        assert stats is not None
        assert stats['median'] > 200  # Médiane élevée (lumière)
        assert stats['hot_pixels_percent'] > 0.01  # Plus de pixels chauds


class TestFitsInfoValidation:
    """Tests pour la validation des darks (détection capot ouvert)"""
    
    def test_valid_dark_validation(self, valid_dark_fits):
        """Test qu'un dark valide passe la validation"""
        info = FitsInfo(valid_dark_fits)
        is_valid, reason = info.is_valid_dark()
        
        assert is_valid is True
        assert reason == "Valid dark frame"

    def test_invalid_dark_validation(self, invalid_dark_fits):
        """Test qu'un dark invalide échoue la validation"""
        info = FitsInfo(invalid_dark_fits)
        is_valid, reason = info.is_valid_dark()
        
        assert is_valid is False
        assert "too high" in reason.lower()

    def test_validation_with_custom_thresholds(self, valid_dark_fits):
        """Test la validation avec des seuils personnalisés"""
        info = FitsInfo(valid_dark_fits)
        
        # Test avec seuils très stricts
        is_valid, reason = info.is_valid_dark(max_median_adu=50.0)
        assert is_valid is False
        
        # Test avec seuils très laxistes  
        is_valid, reason = info.is_valid_dark(max_median_adu=500.0)
        assert is_valid is True


class TestFitsInfoGrouping:
    """Tests pour le groupement des darks"""
    
    def test_group_key_generation(self, valid_dark_fits):
        """Test la génération de clés de groupement"""
        info = FitsInfo(valid_dark_fits)
        
        # Test avec précision par défaut (0.2°C)
        group_key = info.group_key()
        expected = "TestCamera_T-15.0_E300_G125_B1x1"
        assert group_key == expected

    def test_group_key_temperature_precision(self, valid_dark_fits):
        """Test l'effet de la précision de température sur le groupement"""
        info = FitsInfo(valid_dark_fits)
        
        # Test avec différentes précisions
        key_02 = info.group_key(temperature_precision=0.2)
        key_10 = info.group_key(temperature_precision=1.0)
        
        assert "T-15.0_" in key_02
        assert "T-15.0_" in key_10  # -15.0 arrondi à 1.0 reste -15.0

    def test_is_equivalent_same_group(self, sample_dark_group):
        """Test que des darks du même groupe sont équivalents"""
        info1 = FitsInfo(sample_dark_group[0])
        info2 = FitsInfo(sample_dark_group[1])
        
        # Avec précision de température élevée, ils devraient être équivalents
        assert info1.is_equivalent(info2, temperature_precision=1.0) is True
        
        # Avec précision fine, ils pourraient être différents
        equiv_fine = info1.is_equivalent(info2, temperature_precision=0.05)
        # Le résultat dépend des variations de température dans sample_dark_group


class TestFitsInfoValidationReport:
    """Tests pour les rapports de validation"""
    
    def test_validation_report_structure(self, valid_dark_fits):
        """Test la structure du rapport de validation"""
        info = FitsInfo(valid_dark_fits)
        report = info.get_validation_report()
        
        required_keys = ['filepath', 'is_valid', 'reason', 'statistics', 
                        'exposure_time', 'temperature', 'camera']
        
        for key in required_keys:
            assert key in report
            
        assert report['filepath'] == valid_dark_fits
        assert report['is_valid'] is True
        assert report['statistics'] is not None

    def test_validation_report_invalid_dark(self, invalid_dark_fits):
        """Test le rapport de validation pour un dark invalide"""
        info = FitsInfo(invalid_dark_fits)
        report = info.get_validation_report()
        
        assert report['is_valid'] is False
        assert "too high" in report['reason'].lower()
        assert report['statistics']['median'] > 200


class TestFitsInfoErrorHandling:
    """Tests pour la gestion d'erreurs"""
    
    def test_nonexistent_file(self):
        """Test le comportement avec un fichier inexistant"""
        info = FitsInfo("/path/that/does/not/exist.fit")
        
        assert info.validData() is False
        assert info.analyze_image_statistics() is None

    def test_bias_frame_validation(self, bias_fits):
        """Test qu'un bias n'est pas validé comme dark"""
        info = FitsInfo(bias_fits)
        
        assert info.is_dark() is False
        assert info.is_bias() is True
        
        is_valid, reason = info.is_valid_dark()
        assert is_valid is False
        assert reason == "Not a dark frame"