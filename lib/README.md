# Library Modules

This directory contains shared library modules used by the Siril processing scripts.

## Modules

### fits_info.py
Contains the `FitsInfo` class which handles reading and accessing FITS file metadata. It provides methods to:
- Read FITS headers
- Parse date/time information
- Extract camera, temperature, gain, exposure time, and binning information
- Check if a file is a dark or bias frame
- Create symlinks and update FITS headers

### config.py
Contains the `Config` class which manages configuration persistence. It provides methods to:
- Load configuration from JSON files
- Save configuration to JSON files
- Get and set configuration values with defaults
- Update configuration from command line arguments

### siril_utils.py
Contains utility functions for running Siril scripts. It provides:
- `run_siril_script()`: Execute Siril scripts with support for different execution modes (native, flatpak, appimage)

## Usage

These modules are imported by `darklib.py` and `biaslib.py`:

```python
from lib.fits_info import FitsInfo
from lib.config import Config
from lib.siril_utils import run_siril_script
```

## Design

The library modules were extracted from `darklib.py` to improve code organization and enable reuse across multiple scripts (darklib.py and biaslib.py).
