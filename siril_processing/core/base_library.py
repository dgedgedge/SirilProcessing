"""
Base library class for managing FITS calibration files.
"""

import os
import json
from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import datetime


class BaseLibrary:
    """Base class for managing FITS calibration libraries."""
    
    def __init__(self, library_path: str, library_type: str = "calibration"):
        """
        Initialize the library manager.
        
        Args:
            library_path: Path to the library directory
            library_type: Type of library (e.g., 'dark', 'bias')
        """
        self.library_path = Path(library_path)
        self.library_type = library_type
        self.metadata_file = self.library_path / f"{library_type}_metadata.json"
        self.metadata: Dict[str, Any] = {}
        
        # Create library directory if it doesn't exist
        self.library_path.mkdir(parents=True, exist_ok=True)
        
        # Load existing metadata
        self._load_metadata()
    
    def _load_metadata(self) -> None:
        """Load metadata from JSON file."""
        if self.metadata_file.exists():
            with open(self.metadata_file, 'r') as f:
                self.metadata = json.load(f)
        else:
            self.metadata = {
                "library_type": self.library_type,
                "created": datetime.now().isoformat(),
                "masters": []
            }
    
    def _save_metadata(self) -> None:
        """Save metadata to JSON file."""
        self.metadata["last_updated"] = datetime.now().isoformat()
        with open(self.metadata_file, 'w') as f:
            json.dump(self.metadata, f, indent=2)
    
    def list_masters(self) -> List[Dict[str, Any]]:
        """
        List all master frames in the library.
        
        Returns:
            List of master frame metadata dictionaries
        """
        return self.metadata.get("masters", [])
    
    def add_master(self, master_path: str, properties: Dict[str, Any]) -> bool:
        """
        Add a master frame to the library.
        
        Args:
            master_path: Path to the master FITS file
            properties: Dictionary of frame properties (e.g., exposure, temperature, binning)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            master_path = Path(master_path)
            if not master_path.exists():
                raise FileNotFoundError(f"Master file not found: {master_path}")
            
            # Create entry
            entry = {
                "filename": master_path.name,
                "path": str(master_path.absolute()),
                "added": datetime.now().isoformat(),
                "properties": properties
            }
            
            # Add to metadata
            if "masters" not in self.metadata:
                self.metadata["masters"] = []
            self.metadata["masters"].append(entry)
            
            # Save metadata
            self._save_metadata()
            return True
        except Exception as e:
            print(f"Error adding master: {e}")
            return False
    
    def remove_master(self, filename: str) -> bool:
        """
        Remove a master frame from the library.
        
        Args:
            filename: Name of the master file to remove
            
        Returns:
            True if successful, False otherwise
        """
        try:
            masters = self.metadata.get("masters", [])
            self.metadata["masters"] = [m for m in masters if m["filename"] != filename]
            self._save_metadata()
            return True
        except Exception as e:
            print(f"Error removing master: {e}")
            return False
    
    def find_master(self, **criteria) -> Optional[Dict[str, Any]]:
        """
        Find a master frame matching the given criteria.
        
        Args:
            **criteria: Property criteria to match (e.g., exposure=300, temperature=-10)
            
        Returns:
            Master frame metadata dictionary or None if not found
        """
        for master in self.metadata.get("masters", []):
            properties = master.get("properties", {})
            match = all(
                properties.get(key) == value 
                for key, value in criteria.items()
            )
            if match:
                return master
        return None
    
    def get_library_info(self) -> Dict[str, Any]:
        """
        Get library information.
        
        Returns:
            Dictionary with library statistics and information
        """
        return {
            "library_type": self.library_type,
            "library_path": str(self.library_path),
            "total_masters": len(self.metadata.get("masters", [])),
            "created": self.metadata.get("created"),
            "last_updated": self.metadata.get("last_updated")
        }
