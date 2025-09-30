# SirilProcessing - FITS Calibration Library Manager

The aim of this tool is to ease the processing workflow with Siril by managing dark and bias master FITS files.

## Features

- **Object-oriented library management**: Clean class-based architecture for managing dark and bias libraries
- **Dark library management**: Store and retrieve dark master frames by exposure, temperature, binning, and more
- **Bias library management**: Store and retrieve bias master frames by temperature and binning
- **Dual interface**: Both command-line (CLI) and graphical (GUI) interfaces
- **Metadata tracking**: JSON-based metadata storage for easy management
- **Smart matching**: Find the best matching calibration frame based on criteria with tolerance

## Installation

1. Clone this repository:
```bash
git clone https://github.com/dgedgedge/SirilProcessing.git
cd SirilProcessing
```

2. Install dependencies (optional - for FITS file reading):
```bash
pip install -r requirements.txt
```

Note: The core functionality works without dependencies. Install `astropy` if you want to automatically extract FITS header information.

## Usage

### Command-Line Interface (CLI)

The CLI provides full functionality for managing libraries:

#### Dark Library Commands

```bash
# List all dark masters
python siril_processing_cli.py dark --library ~/my_darks list

# Show library information
python siril_processing_cli.py dark --library ~/my_darks info

# Add a dark master
python siril_processing_cli.py dark --library ~/my_darks add master_dark_300s.fit \
  --exposure 300 --temp -10 --binning 1x1 --gain 100

# Find a matching dark master
python siril_processing_cli.py dark --library ~/my_darks find \
  --exposure 300 --temp -10 --binning 1x1

# Remove a dark master
python siril_processing_cli.py dark --library ~/my_darks remove master_dark_300s.fit
```

#### Bias Library Commands

```bash
# List all bias masters
python siril_processing_cli.py bias --library ~/my_bias list

# Show library information
python siril_processing_cli.py bias --library ~/my_bias info

# Add a bias master
python siril_processing_cli.py bias --library ~/my_bias add master_bias.fit \
  --temp 20 --binning 1x1 --gain 100

# Find a matching bias master
python siril_processing_cli.py bias --library ~/my_bias find \
  --temp 20 --binning 1x1

# Remove a bias master
python siril_processing_cli.py bias --library ~/my_bias remove master_bias.fit
```

### Graphical User Interface (GUI)

Launch the GUI:

```bash
python siril_processing_gui.py
```

The GUI provides:
- Tab-based interface for Dark and Bias libraries
- Easy browsing and opening of library directories
- Add/remove masters with dialog forms
- List view of all masters with key properties
- Visual library statistics

## Project Structure

```
SirilProcessing/
├── siril_processing/           # Main package
│   ├── __init__.py
│   ├── core/                   # Core library classes
│   │   ├── __init__.py
│   │   ├── base_library.py    # Base library functionality
│   │   ├── dark_library.py    # Dark library manager
│   │   └── bias_library.py    # Bias library manager
│   ├── cli/                    # Command-line interface
│   │   ├── __init__.py
│   │   └── main.py
│   ├── gui/                    # Graphical user interface
│   │   ├── __init__.py
│   │   └── main.py
│   └── utils/                  # Utility functions
│       ├── __init__.py
│       └── fits_utils.py      # FITS file utilities
├── siril_processing_cli.py     # CLI entry point
├── siril_processing_gui.py     # GUI entry point
├── requirements.txt            # Python dependencies
├── setup.py                    # Package setup
└── README.md                   # This file
```

## API Usage

You can also use the library programmatically in your own Python scripts:

```python
from siril_processing import DarkLibrary, BiasLibrary

# Create/open a dark library
dark_lib = DarkLibrary("~/my_darks")

# Add a dark master
dark_lib.add_dark_master(
    master_path="master_dark_300s.fit",
    exposure=300.0,
    temperature=-10.0,
    binning="1x1",
    gain=100
)

# Find a matching dark
master = dark_lib.find_dark_master(
    exposure=300.0,
    temperature=-10.0,
    temperature_tolerance=5.0
)

if master:
    print(f"Found: {master['path']}")

# List all darks grouped by exposure
by_exposure = dark_lib.list_by_exposure()
for exposure, masters in by_exposure.items():
    print(f"{exposure}s: {len(masters)} masters")
```

## Library Storage Format

Libraries are stored as directories with JSON metadata:

```
my_darks/
├── dark_metadata.json          # Library metadata
└── (FITS files can be anywhere)

my_bias/
├── bias_metadata.json          # Library metadata
└── (FITS files can be anywhere)
```

The metadata files contain:
- Library type and creation date
- List of all masters with their properties
- File paths and timestamps

## Development

### Adding New Features

The project uses an object-oriented design:

1. **BaseLibrary**: Common functionality for all library types
2. **DarkLibrary**: Dark-specific features (exposure matching, etc.)
3. **BiasLibrary**: Bias-specific features
4. **CLI/GUI**: Separate interface implementations using the same core classes

To add a new calibration type (e.g., flats), extend `BaseLibrary` similar to `DarkLibrary` and `BiasLibrary`.

## License

This project is licensed under the GNU General Public License v3.0 - see the LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit pull requests or open issues for bugs and feature requests.

## Author

dgedgedge
