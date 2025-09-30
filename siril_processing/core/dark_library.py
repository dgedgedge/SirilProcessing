"""
Dark library management for FITS files.
"""

from typing import Dict, Any, Optional, List
from pathlib import Path
from .base_library import BaseLibrary


class DarkLibrary(BaseLibrary):
    """Class for managing dark frame master FITS files."""
    
    def __init__(self, library_path: str):
        """
        Initialize the dark library manager.
        
        Args:
            library_path: Path to the dark library directory
        """
        super().__init__(library_path, library_type="dark")
    
    def add_dark_master(
        self, 
        master_path: str, 
        exposure: float, 
        temperature: float,
        binning: str = "1x1",
        gain: Optional[int] = None,
        offset: Optional[int] = None,
        camera: Optional[str] = None,
        date_taken: Optional[str] = None
    ) -> bool:
        """
        Add a dark master frame to the library.
        
        Args:
            master_path: Path to the master FITS file
            exposure: Exposure time in seconds
            temperature: Sensor temperature in Celsius
            binning: Binning mode (e.g., "1x1", "2x2")
            gain: Camera gain setting
            offset: Camera offset setting
            camera: Camera model/name
            date_taken: Date the dark was taken
            
        Returns:
            True if successful, False otherwise
        """
        properties = {
            "exposure": exposure,
            "temperature": temperature,
            "binning": binning
        }
        
        if gain is not None:
            properties["gain"] = gain
        if offset is not None:
            properties["offset"] = offset
        if camera is not None:
            properties["camera"] = camera
        if date_taken is not None:
            properties["date_taken"] = date_taken
        
        return self.add_master(master_path, properties)
    
    def find_dark_master(
        self,
        exposure: float,
        temperature: Optional[float] = None,
        binning: str = "1x1",
        temperature_tolerance: float = 5.0
    ) -> Optional[Dict[str, Any]]:
        """
        Find a dark master matching the given criteria.
        
        Args:
            exposure: Required exposure time in seconds
            temperature: Desired temperature in Celsius
            binning: Required binning mode
            temperature_tolerance: Acceptable temperature difference in Celsius
            
        Returns:
            Master frame metadata dictionary or None if not found
        """
        # First try exact match
        exact_match = self.find_master(exposure=exposure, binning=binning)
        if exact_match and temperature is None:
            return exact_match
        
        # If temperature specified, find best match within tolerance
        if temperature is not None:
            best_match = None
            best_temp_diff = float('inf')
            
            for master in self.metadata.get("masters", []):
                props = master.get("properties", {})
                if (props.get("exposure") == exposure and 
                    props.get("binning") == binning):
                    
                    master_temp = props.get("temperature")
                    if master_temp is not None:
                        temp_diff = abs(temperature - master_temp)
                        if temp_diff <= temperature_tolerance and temp_diff < best_temp_diff:
                            best_temp_diff = temp_diff
                            best_match = master
            
            return best_match
        
        return exact_match
    
    def list_by_exposure(self) -> Dict[float, List[Dict[str, Any]]]:
        """
        List all dark masters grouped by exposure time.
        
        Returns:
            Dictionary with exposure times as keys and lists of masters as values
        """
        grouped = {}
        for master in self.metadata.get("masters", []):
            exposure = master.get("properties", {}).get("exposure")
            if exposure is not None:
                if exposure not in grouped:
                    grouped[exposure] = []
                grouped[exposure].append(master)
        return grouped
