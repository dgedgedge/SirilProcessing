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
- `Siril`: Class for managing Siril execution with configuration validation and caching

### darkprocess.py
Contains the `DarkLib` class which manages dark frame library operations. It provides methods to:
- Group dark files by camera, temperature, exposure time, gain, and binning
- Stack dark frames using Siril
- Manage master dark library with smart overwrite logic
- List existing master darks with their characteristics

## Usage

These modules are imported by the scripts in `bin/`:

```python
```python
from lib.fits_info import FitsInfo
from lib.config import Config
from lib.siril_utils import Siril
from lib.darkprocess import DarkLib

# Configure Siril globally
Siril.configure_defaults(siril_path="/path/to/siril", siril_mode="native")

# Create instance without Siril parameters
darklib = DarkLib(config)
```
```

The `bin/darklibupdate.py` script uses the `DarkLib` class:

```python
from lib.darklib import DarkLib

config = Config()
darklib = DarkLib(config, siril_path="siril", siril_mode="flatpak")
```

## Design

The library modules were extracted from the original `bin/darklibupdate.py` to improve code organization and enable reuse across multiple scripts. The `DarkLib` class was also moved to the library to separate the business logic from the command-line interface.
