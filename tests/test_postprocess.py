"""
Tests pour le module de post-traitement (extraction de gradient).
"""

import argparse
import sys
from pathlib import Path

import pytest
import numpy as np
from astropy.io import fits

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.postprocess import GradientExtractor, extract_gradient_from_fits


def test_add_arguments_to_parser():
    parser = argparse.ArgumentParser()
    GradientExtractor(method='polynomial').add_arguments(parser)
    args = parser.parse_args([
        '--gradient-min-order', '2', '--gradient-max-order', '4',
        '--gradient-points', '200', '--gradient-output', '/tmp/gradient_output',
        '--gradient-sample-fraction', '0.5', '--gradient-max-samples', '400',
        '--no-gradient-measurement-image',
    ])
    assert args.gradient_min_order == 2
    assert args.gradient_max_order == 4
    assert args.gradient_n_points == 200
    assert args.gradient_output_dir == Path('/tmp/gradient_output')
    assert args.gradient_sample_fraction == 0.5
    assert args.gradient_max_samples == 400
    assert args.gradient_measurement_image is False


def _create_test_fits_file(path, data=None, header=None):
    """Crée un fichier FITS de test."""
    if data is None:
        data = np.random.randint(1000, 5000, size=(100, 100), dtype=np.uint16)
    
    if header is None:
        header = fits.Header()
        header['IMAGETYP'] = 'LIGHT'
        header['EXPTIME'] = 180.0
        header['CCD-TEMP'] = -10.0
    
    hdu = fits.PrimaryHDU(data=data, header=header)
    hdu.writeto(path, overwrite=True)
    return path


def test_postprocess_gradient_extractor_initialization(tmp_path):
    """Test de l'initialisation de l'extracteur de gradient."""
    extractor = GradientExtractor(
        method='polynomial',
        min_polynomial_order=1,
        max_polynomial_order=3,
        sample_fraction=0.5,
        max_samples=5000,
        output_dir=tmp_path
    )
    
    assert extractor.min_polynomial_order == 1
    assert extractor.max_polynomial_order == 3
    assert extractor.sample_fraction == 0.5
    assert extractor.max_samples == 5000
    assert extractor.output_dir == tmp_path


def test_polynomial_matrix_creation(tmp_path):
    """Test de la création de la matrice polynomiale."""
    extractor = GradientExtractor(method='polynomial')
    
    # Tester avec ordre 1
    x = np.array([-1.0, 0.0, 1.0])
    y = np.array([-1.0, 0.0, 1.0])
    A = extractor._create_polynomial_matrix(x, y, order=1)
    
    # Pour l'ordre 1, on a: 1, x, y
    assert A.shape == (3, 3)
    
    # Tester avec ordre 2
    A2 = extractor._create_polynomial_matrix(x, y, order=2)
    # Pour l'ordre 2, on a: 1, x, y, x^2, xy, y^2
    assert A2.shape == (3, 6)


def test_fit_polynomial_surface_linear(tmp_path):
    """Test de l'ajustement d'une surface polynomiale linéaire."""
    extractor = GradientExtractor(method='polynomial', sample_fraction=1.0, max_samples=10000)
    
    # Créer des données avec un gradient linéaire connu
    height, width = 50, 50
    x, y = np.meshgrid(np.arange(width), np.arange(height))
    
    # z = 100 + 0.1*x - 0.05*y (gradient linéaire)
    z = 100 + 0.1 * x - 0.05 * y
    
    result = extractor._fit_polynomial_surface(z, order=1)
    
    assert result['success'] is True
    assert result['order'] == 1
    assert result['r_squared'] >= 0.99  # Très bon ajustement
    
    # Vérifier les coefficients (approximatifs)
    coeffs = result['coefficients']
    # L'ordre est: constante, x, y (car on normalise)
    # Les coefficients doivent être proches des valeurs attendues
    assert len(coeffs) == 3


def test_fit_polynomial_surface_quadratic(tmp_path):
    """Test de l'ajustement d'une surface polynomiale quadratique."""
    extractor = GradientExtractor(method='polynomial', sample_fraction=1.0, max_samples=10000)
    
    # Créer des données avec un gradient quadratique connu
    height, width = 50, 50
    x, y = np.meshgrid(np.arange(width), np.arange(height))
    
    # Normaliser les coordonnées
    x_norm = (x - width/2) / (width/2)
    y_norm = (y - height/2) / (height/2)
    
    # z = 100 + 10*x_norm^2 + 5*y_norm^2
    z = 100 + 10 * x_norm**2 + 5 * y_norm**2
    
    # Ajuster avec ordre 1 (devrait mal fonctionner)
    result_order1 = extractor._fit_polynomial_surface(z, order=1)
    assert result_order1['success'] is True
    
    # Ajuster avec ordre 2 (devrait bien fonctionner)
    result_order2 = extractor._fit_polynomial_surface(z, order=2)
    assert result_order2['success'] is True
    
    # L'ordre 2 doit mieux expliquer les données
    assert result_order2['r_squared'] > result_order1['r_squared']


def test_extract_gradient_all_orders(tmp_path):
    """Test de l'extraction de gradient à tous les ordres."""
    # Créer un fichier FITS de test avec un gradient
    fits_path = tmp_path / "test_gradient.fit"
    height, width = 100, 100
    x, y = np.meshgrid(np.arange(width), np.arange(height))
    
    # Créer un gradient linéaire
    data = 1000 + 0.5 * x - 0.3 * y
    _create_test_fits_file(fits_path, data=data)
    
    extractor = GradientExtractor(
        method='polynomial',
        min_polynomial_order=1,
        max_polynomial_order=2,
        output_dir=tmp_path
    )
    
    results = extractor.extract_gradient_all_orders(fits_path)
    
    assert 'image_path' in results
    assert 'results' in results
    assert 'recommendations' in results
    assert 'final_recommendation' in results
    
    # Vérifier que les résultats contiennent les ordres 1 et 2
    assert 'order_1' in results['results']
    assert 'order_2' in results['results']
    
    # Vérifier les recommandations
    assert 'best_r_squared' in results['recommendations']
    assert 'best_compromise' in results['recommendations']
    assert 'simplest_good_fit' in results['recommendations']


def test_extract_gradient_from_fits_function(tmp_path):
    """Test de la fonction utilitaire extract_gradient_from_fits."""
    # Créer un fichier FITS de test
    fits_path = tmp_path / "test_extract.fit"
    data = np.random.randint(1000, 5000, size=(50, 50), dtype=np.uint16)
    _create_test_fits_file(fits_path, data=data)
    
    results = extract_gradient_from_fits(
        fits_path=fits_path,
        output_dir=tmp_path,
        min_order=1,
        max_order=2,
        create_measurement_image=False,
        n_measurement_points=50
    )
    
    assert 'image_path' in results
    assert 'results' in results
    assert results['image_path'] == str(fits_path)


def test_create_measurement_points_image(tmp_path):
    """Test de la création d'une image des points de mesure."""
    # Créer un fichier FITS de test
    fits_path = tmp_path / "test_measurement.fit"
    data = np.random.randint(1000, 5000, size=(50, 50), dtype=np.uint16)
    _create_test_fits_file(fits_path, data=data)
    
    extractor = GradientExtractor(method='polynomial', output_dir=tmp_path)
    
    # Créer des points de mesure
    measurement_points = [(10, 10), (20, 20), (30, 30)]
    
    # Créer l'image
    output_path = tmp_path / "test_measurement_points.png"
    result_path = extractor.create_measurement_points_image(
        fits_path,
        measurement_points,
        output_path=output_path
    )
    
    assert result_path is not None
    assert result_path.exists()
    assert output_path.exists()


def test_extract_with_measurement_points(tmp_path):
    """Test de l'extraction avec points de mesure."""
    # Créer un fichier FITS de test
    fits_path = tmp_path / "test_with_points.fit"
    data = np.random.randint(1000, 5000, size=(50, 50), dtype=np.uint16)
    _create_test_fits_file(fits_path, data=data)
    
    extractor = GradientExtractor(method='polynomial', output_dir=tmp_path)
    
    results, measurement_image = extractor.extract_with_measurement_points(
        fits_path,
        n_points=20
    )
    
    assert 'measurement_points' in results
    assert 'measurement_points_count' in results
    assert results['measurement_points_count'] == 20
    assert len(results['measurement_points']) == 20
    
    # Vérifier que l'image a été créée
    assert measurement_image is not None
    assert measurement_image.exists()


def test_postprocess_gradient_extractor_with_noisy_data(tmp_path):
    """Test de l'extracteur avec des données bruitées."""
    fits_path = tmp_path / "test_noisy.fit"
    
    # Créer des données avec du bruit
    np.random.seed(42)
    height, width = 100, 100
    x, y = np.meshgrid(np.arange(width), np.arange(height))
    
    # Signal + bruit avec un signal plus fort
    signal = 1000 + 5.0 * x - 3.0 * y  # Gradient plus prononcé
    noise = np.random.normal(0, 10, size=(height, width))  # Moins de bruit
    data = signal + noise
    
    _create_test_fits_file(fits_path, data=data.astype(np.uint16))
    
    extractor = GradientExtractor(
        method='polynomial',
        min_polynomial_order=1,
        max_polynomial_order=1,
        output_dir=tmp_path
    )
    
    results = extractor.extract_gradient_all_orders(fits_path)
    
    assert results['final_recommendation'] is not None
    # Avec du bruit mais un signal fort, le R² devrait être bon
    max_r2 = max(
        r.get('r_squared', 0) 
        for r in results['results'].values()
    )
    assert max_r2 > 0.5  # Bon ajustement malgré le bruit


def test_postprocess_gradient_extractor_with_constant_data(tmp_path):
    """Test de l'extracteur avec des données constantes (pas de gradient)."""
    fits_path = tmp_path / "test_constant.fit"
    
    # Créer des données constantes
    data = np.full((50, 50), 1000, dtype=np.uint16)
    _create_test_fits_file(fits_path, data=data)
    
    extractor = GradientExtractor(
        method='polynomial',
        min_polynomial_order=1,
        max_polynomial_order=2,
        output_dir=tmp_path
    )
    
    results = extractor.extract_gradient_all_orders(fits_path)
    
    # Avec des données constantes, le R² peut être faible ou instable
    # mais l'extraction doit se terminer sans erreur
    assert 'results' in results
    assert 'final_recommendation' in results


def test_extract_gradient_from_fits_method(tmp_path):
    """Test de la méthode extract_gradient_from_fits de la classe."""
    # Créer un fichier FITS de test
    fits_path = tmp_path / "test_method.fit"
    data = np.random.randint(1000, 5000, size=(50, 50), dtype=np.uint16)
    _create_test_fits_file(fits_path, data=data)
    
    # Créer un extracteur avec des paramètres
    extractor = GradientExtractor(
        method='polynomial',
        min_polynomial_order=1,
        max_polynomial_order=2,
        output_dir=tmp_path,
        create_measurement_image=True,
        n_measurement_points=50
    )
    
    # Appeler la méthode extract_gradient_from_fits
    results = extractor.extract_gradient_from_fits(fits_path)
    
    # Vérifier les résultats
    assert 'image_path' in results
    assert 'results' in results
    assert 'final_recommendation' in results
    assert results['image_path'] == str(fits_path)
    
    # Vérifier que le rapport n'a pas été sauvegardé (output_path=None)
    assert 'report_saved_to' not in results


def test_extract_gradient_from_fits_method_with_output(tmp_path):
    """Test de la méthode extract_gradient_from_fits avec sauvegarde du rapport."""
    # Créer un fichier FITS de test
    fits_path = tmp_path / "test_method_output.fit"
    data = np.random.randint(1000, 5000, size=(50, 50), dtype=np.uint16)
    _create_test_fits_file(fits_path, data=data)
    
    # Chemin de sortie pour le rapport
    report_path = tmp_path / "report.json"
    
    # Créer un extracteur
    extractor = GradientExtractor(
        method='polynomial',
        min_polynomial_order=1,
        max_polynomial_order=2,
        output_dir=tmp_path,
        create_measurement_image=True,
        n_measurement_points=50
    )
    
    # Appeler la méthode avec sauvegarde
    results = extractor.extract_gradient_from_fits(fits_path, output_path=report_path)
    
    # Vérifier que le rapport a été sauvegardé
    assert report_path.exists()
    assert 'report_saved_to' in results
    assert results['report_saved_to'] == str(report_path)
    
    # Vérifier que le contenu du rapport est valide
    import json
    with open(report_path, 'r') as f:
        saved_results = json.load(f)
    assert 'image_path' in saved_results
    assert 'results' in saved_results


def test_postprocessor_hierarchy():
    """Test de la hiérarchie d'héritage processor -> GradientExtractor."""
    from lib.processor import processor
    from lib.postprocess import GradientExtractor
    
    # Vérifier l'héritage
    assert issubclass(GradientExtractor, processor)
    
    # Vérifier que processor a les méthodes abstraites
    assert hasattr(processor, 'add_arguments')
    assert hasattr(processor, 'post_process')
    
    # Vérifier que GradientExtractor implémente ces méthodes
    assert hasattr(GradientExtractor, 'add_arguments')
    assert hasattr(GradientExtractor, 'post_process')


def test_postprocessor_abstract_methods():
    from lib.processor import processor
    with pytest.raises(TypeError):
        processor()


def test_seq_postprocessor_initialization():
    """Test de l'initialisation de _PostProcessorSequence."""
    from lib.postprocess import _PostProcessorSequence, GradientExtractor
    
    seq_processor = _PostProcessorSequence()
    
    # Vérifier que la liste des processeurs contient GradientExtractor
    assert len(seq_processor.processors) == 4
    assert isinstance(seq_processor.processors[0], GradientExtractor)


def test_seq_postprocessor_add_arguments():
    """Test de l'ajout des arguments par _PostProcessorSequence."""
    import argparse
    from lib.postprocess import _PostProcessorSequence
    
    parser = argparse.ArgumentParser(description="Test parser")
    seq_processor = _PostProcessorSequence()
    seq_processor.add_arguments(parser)
    
    # Parser des arguments
    args = parser.parse_args([
        '--enable_gradient',
        '--gradient-min-order', '2',
        '--gradient-max-order', '4'
    ])
    
    # Vérifier les arguments
    assert args.enable_gradient is True
    assert args.gradient_min_order == 2
    assert args.gradient_max_order == 4


@pytest.mark.parametrize("image_enabled", [True, False])
def test_wrapper_options_reach_processing(tmp_path, image_enabled):
    import json
    import os
    import subprocess
    root = Path(__file__).resolve().parents[1]
    _create_test_fits_file(tmp_path / "image.fit")
    options = ["--gradient-method", "polynomial", "--disable-photometry", "--disable-denoise", "--disable-deconvolution", "--gradient-min-order", "2", "--gradient-max-order", "2",
               "--gradient-points", "7", "--gradient-sample-fraction", "0.1",
               "--gradient-max-samples", "80", "--gradient-output", "points"]
    if not image_enabled:
        options.append("--no-gradient-measurement-image")
    result = subprocess.run([str(root / "bin/postProcess.sh"), "image.fit",
                             "reports/result.json", *options], cwd=tmp_path,
                            env={**os.environ, "VENV_DIR": sys.prefix},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / "reports/result.json").read_text())["gradient"]
    assert list(report["results"]) == ["order_2"]
    assert report["results"]["order_2"]["n_samples"] == 80
    assert (tmp_path / "points/01_gradient_image_measurement_points.png").exists() == image_enabled
    if image_enabled:
        assert report["measurement_points_count"] == 7
    else:
        assert "measurement_image_path" not in report


def test_sample_fraction_and_defaults_are_preserved(tmp_path):
    from lib.postprocess import _PostProcessorSequence
    image = _create_test_fits_file(tmp_path / "image.fit", np.arange(400).reshape(20, 20))
    gradient = GradientExtractor(method='polynomial', create_measurement_image=False)
    sequence = _PostProcessorSequence([gradient])
    parser = argparse.ArgumentParser()
    sequence.add_arguments(parser)
    args = parser.parse_args(["--gradient-sample-fraction", "0.1"])
    result = sequence.post_process(image, tmp_path / "report.json", args)
    assert result["gradient"]["results"]["order_1"]["n_samples"] == 40
    assert gradient.sample_fraction == 0.3
    assert sequence.post_process(image, tmp_path / "default.json")["gradient"]["results"]["order_1"]["n_samples"] == 120


@pytest.mark.parametrize("options", [
    ["--gradient-min-order", "3", "--gradient-max-order", "2"],
    ["--gradient-points", "0"], ["--gradient-max-samples", "0"],
    ["--gradient-sample-fraction", "0"], ["--gradient-sample-fraction", "1.1"],
])
def test_invalid_processing_options(tmp_path, options):
    from lib.postprocess import _PostProcessorSequence
    sequence = _PostProcessorSequence()
    parser = argparse.ArgumentParser()
    sequence.add_arguments(parser)
    with pytest.raises(RuntimeError, match="gradient"):
        sequence.post_process(tmp_path / "image.fit", tmp_path / "report.json", parser.parse_args(options))
    assert not (tmp_path / "report.json").exists()


def test_disabled_gradient(tmp_path):
    from lib.postprocess import _PostProcessorSequence
    sequence = _PostProcessorSequence()
    parser = argparse.ArgumentParser()
    sequence.add_arguments(parser)
    assert sequence.post_process(tmp_path / "unused.fit", tmp_path / "report.json",
                                 parser.parse_args(["--disable-gradient", "--disable-photometry", "--disable-denoise", "--disable-deconvolution"])) == {}
    assert (tmp_path / "report.json").read_text() == "{}"


def test_sequence_supports_additional_processors(tmp_path):
    import json
    from lib.processor import processor
    from lib.postprocess import _PostProcessorSequence
    calls = []

    class ExtraProcessor(processor):
        def __init__(self, prefix, produces_image=False):
            self.prefix = prefix
            self.produces_image = produces_image

        def get_prefix(self):
            return self.prefix

        def add_arguments(self, parser):
            parser.add_argument(f"--{self.prefix}-value", type=int, default=1)

        def post_process(self, input_path, output_path, args=None):
            calls.append((self.prefix, input_path, output_path, getattr(args, f"{self.prefix}_value")))
            if self.produces_image:
                fits.writeto(output_path.parent / "changed.fit", np.ones((10, 10)))
                return {"output_image": "changed.fit"}
            return {"value": getattr(args, f"{self.prefix}_value")}

    image = _create_test_fits_file(tmp_path / "original.fit")
    sequence = _PostProcessorSequence([
        ExtraProcessor("first", True), ExtraProcessor("disabled"),
        ExtraProcessor("analysis"), ExtraProcessor("last"),
    ])
    parser = argparse.ArgumentParser()
    sequence.add_arguments(parser)
    args = parser.parse_args(["--first-value", "42", "--last-value", "7", "--disable-disabled"])
    report_path = tmp_path / "report.json"
    result = sequence.post_process(image, report_path, args)
    assert [call[0] for call in calls] == ["first", "analysis", "last"]
    assert calls[0][1] == image
    assert calls[0][3] == 42
    assert calls[-1][3] == 7
    assert calls[1][1] == calls[2][1] == tmp_path / "report_steps/changed.fit"
    assert [call[2].name for call in calls] == ['01_first.json', '03_analysis.json', '04_last.json']
    assert len({call[2] for call in calls}) == 3
    assert json.loads(report_path.read_text()) == result


def test_failed_input_does_not_report_success(tmp_path):
    from lib.postprocess import _PostProcessorSequence
    image = tmp_path / "bad.fit"
    image.write_text("invalid FITS")
    with pytest.raises(RuntimeError, match="gradient"):
        _PostProcessorSequence().post_process(image, tmp_path / "report.json")
    assert not (tmp_path / "report.json").exists()


@pytest.mark.parametrize("failure,level", [(False, "INFO"), (True, "INFO"), (True, "DEBUG")])
def test_wrapper_file_logging(tmp_path, failure, level):
    import os
    import subprocess
    root = Path(__file__).resolve().parents[1]
    image = tmp_path / "image.fit"
    if failure:
        image.write_text("not FITS")
    else:
        _create_test_fits_file(image)
    log_file = tmp_path / "report_steps/00_image_postProcess.log"
    log_file.parent.mkdir()
    log_file.write_text("old invocation")
    result = subprocess.run([
        str(root / "bin/postProcess.sh"), "image.fit", "report.json",
        "--log-level", level, "--no-gradient-measurement-image",
        "--disable-photometry", "--disable-denoise", "--disable-deconvolution",
    ], cwd=tmp_path, env={**os.environ, "VENV_DIR": sys.prefix}, capture_output=True, text=True)
    assert result.returncode == (1 if failure else 0), result.stderr
    log = log_file.read_text()
    assert "old invocation" not in log
    assert "[INFO] root: Analyse de: image.fit" in log
    message = "Erreur:" if failure else "Post-traitements terminés"
    assert message in log
    assert message in result.stderr
    assert ("Traceback (most recent call last)" in log) == (failure and level == "DEBUG")


def test_default_output_uses_general_name_and_indexed_files(tmp_path):
    import os
    import subprocess
    root = Path(__file__).resolve().parents[1]
    _create_test_fits_file(tmp_path / 'target.fit')
    result = subprocess.run([str(root / 'bin/postProcess.sh'), 'target.fit',
                             '--disable-photometry', '--disable-denoise', '--disable-deconvolution'], cwd=tmp_path,
                            env={**os.environ, 'VENV_DIR': sys.prefix}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'target_postProcess.json').exists()
    work = tmp_path / 'target_postProcess_steps'
    assert (work / '00_target_postProcess.log').exists()
    assert (work / '01_gradient.json').exists()
    assert (work / '01_gradient_target_measurement_points.png').exists()
    assert not (tmp_path / 'target_gradient_steps').exists()
