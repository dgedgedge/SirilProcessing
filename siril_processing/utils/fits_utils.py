"""
Utility functions for working with FITS files.
"""

from typing import Dict, Any, Optional
from pathlib import Path


def get_fits_header(fits_path: str) -> Optional[Dict[str, Any]]:
    """
    Read FITS header information.
    
    Args:
        fits_path: Path to the FITS file
        
    Returns:
        Dictionary with FITS header information or None if error
        
    Note:
        This is a placeholder. Install astropy for full FITS support:
        pip install astropy
    """
    try:
        # Try to import astropy
        try:
            from astropy.io import fits
            
            with fits.open(fits_path) as hdul:
                header = hdul[0].header
                return dict(header)
        except ImportError:
            print("Warning: astropy not installed. FITS header reading not available.")
            print("Install with: pip install astropy")
            return None
    except Exception as e:
        print(f"Error reading FITS file: {e}")
        return None


def extract_fits_properties(fits_path: str) -> Dict[str, Any]:
    """
    Extract common properties from a FITS file.
    
    Args:
        fits_path: Path to the FITS file
        
    Returns:
        Dictionary with extracted properties (exposure, temperature, etc.)
        
    Note:
        This is a placeholder. Install astropy for full FITS support.
    """
    properties = {}
    
    header = get_fits_header(fits_path)
    if header is None:
        return properties
    
    # Common FITS keywords for calibration frames
    # These may vary by camera/software, adjust as needed
    keyword_mappings = {
        "exposure": ["EXPTIME", "EXPOSURE"],
        "temperature": ["CCD-TEMP", "SET-TEMP", "TEMPERAT"],
        "binning": ["XBINNING", "BINNING"],
        "gain": ["GAIN"],
        "offset": ["OFFSET"],
        "camera": ["INSTRUME", "CAMERA"],
        "date": ["DATE-OBS"]
    }
    
    for prop_name, keywords in keyword_mappings.items():
        for keyword in keywords:
            if keyword in header:
                value = header[keyword]
                
                # Handle binning format
                if prop_name == "binning" and "YBINNING" in header:
                    value = f"{value}x{header['YBINNING']}"
                
                properties[prop_name] = value
                break
    
    return properties
