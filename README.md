# SirilProcessing
The aim of the scripts I develop is to ease my processing with Siril.

## Scripts

### darklibupdate.py
A script to create and maintain a library of master dark frames. It groups dark frames by camera, temperature, exposure time, gain, and binning, then stacks them using Siril to create master darks.

#### Features:
- Automatic grouping of dark frames by metadata
- Smart overwrite logic based on date and stacking parameters
- Support for multiple Siril execution modes (native, flatpak, appimage)
- Configuration persistence
- Age-based filtering

### biaslib.py
A script similar to darklibupdate.py but for bias frames. It creates and maintains a library of master bias frames grouped by camera, temperature, gain, and binning.

## Library

The `lib/` directory contains shared modules used by both scripts:
- `fits_info.py`: FITS file metadata handling
- `config.py`: Configuration management
- `siril_utils.py`: Siril script execution utilities

See [lib/README.md](lib/README.md) for more details.

## Requirements

```bash
pip install -r requirements.txt
```

Main dependencies:
- astropy: For FITS file handling

## Usage

### Dark Library
```bash
# Create master darks from input directories
python3 bin/darklibupdate.py --input-dirs /path/to/darks1 /path/to/darks2

# List existing master darks
python3 bin/darklibupdate.py --list-darks

# Test mode (no Siril execution)
python3 bin/darklibupdate.py --input-dirs /path/to/darks --dummy
```

### Bias Library
```bash
# Create master bias from input directories
python3 bin/biaslib.py --input-dirs /path/to/bias1 /path/to/bias2

# List existing master bias
python3 bin/biaslib.py --list-biases
```

For more options, use `--help`.
