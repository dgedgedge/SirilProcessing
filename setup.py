"""
Setup script for SirilProcessing package.
"""

from setuptools import setup, find_packages
from pathlib import Path

# Read README for long description
this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text()

setup(
    name="siril-processing",
    version="0.1.0",
    author="dgedgedge",
    description="A tool for managing FITS calibration libraries for Siril",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/dgedgedge/SirilProcessing",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Science/Research",
        "Topic :: Scientific/Engineering :: Astronomy",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
    python_requires=">=3.7",
    install_requires=[
        # Core dependencies (none required, all optional)
    ],
    extras_require={
        "fits": ["astropy>=5.0"],
    },
    entry_points={
        "console_scripts": [
            "siril-processing=siril_processing.cli.main:main",
            "siril-processing-gui=siril_processing.gui.main:main",
        ],
    },
    include_package_data=True,
    keywords="astronomy astrophotography siril fits calibration dark bias",
    project_urls={
        "Bug Reports": "https://github.com/dgedgedge/SirilProcessing/issues",
        "Source": "https://github.com/dgedgedge/SirilProcessing",
    },
)
