# SirilProcessing
The aim of the scripts I develop is to ease my processing with Siril.

## Scripts

### darklibupdate.py
A script to create and maintain a library of master dark frames. It groups dark frames by camera, temperature, exposure time, gain, and binning, then stacks them using Siril to create master darks.

**Note:** This script can also handle bias frames, as the only difference between darks and bias is the exposure time. Bias frames (0s exposure) will be automatically grouped separately from dark frames.

#### Features:
- **Automatic grouping** of dark frames by metadata (camera, temperature, exposure, gain, binning)
- **Smart update logic** based on date, stacking parameters, and dark count thresholds
- **Dark frame validation** with robust statistical analysis to detect problematic frames
- **Flexible update criteria** with configurable minimum dark count thresholds
- **Comprehensive reporting** with detailed validation and processing statistics
- **Support for multiple Siril execution modes** (native, flatpak, appimage)
- **Configuration persistence** to save user preferences
- **Age-based filtering** to exclude old dark frames
- **Temperature precision control** for grouping similar temperatures
- **Interrupt handling** for clean cancellation with Ctrl+C

### solarEclipseGif.py
A script to build an animated GIF from monochrome solar eclipse FITS frames.

Detailed documentation is available in
[`docs/SOLAR_ECLIPSE_GIF.md`](docs/SOLAR_ECLIPSE_GIF.md).

#### Features:
- **Sky-background threshold calibration** from manual value or dark frames
- **Full-sun session model** from selected full-sun frames or first nearly-full-sun frames
- **Centering by detected solar circle** with mask-correlation fallback
- **Optional NVIDIA/CUDA acceleration** for correlation steps when CuPy is available
- **Fixed-size centered crops** with zero padding outside source boundaries
- **Timestamp slotting** with at most 10 fps and stacking inside slots
- **Full-sun debug panels** for calibration inspection
- **Shift/centering debug panels** for mask and circle inspection

#### Usage:
```bash
# With explicit full-sun and dark calibration frames
bin/solarEclipseGif.sh \
  --input-dir ~/Images/AstroDirect/sun/Light \
  --full-sun-frames 1-10 \
  --dark-calib-frames 180-190 \
  --target-duration 30 \
  --output ~/Images/AstroDirect/sun/eclipse.gif

# With first nearly-full-sun frames as model source
bin/solarEclipseGif.sh \
  --input-dir ~/Images/AstroDirect/sun/Light \
  --first-nearly-full-sun-frames 10 \
  --target-duration 30 \
  --output ~/Images/AstroDirect/sun/eclipse.gif

# Full-sun model debug
bin/solarEclipseGif.sh \
  --input-dir ~/Images/AstroDirect/sun/Light \
  --full-sun-frames 1-10 \
  --debug-full-sun \
  --output ~/Images/AstroDirect/sun/eclipse.gif
```

#### Useful options:
- `--manual-seuil-fond-du-ciel`: Manual sky-background threshold
- `--dark-calib-frames`: 1-based dark calibration selection
- `--full-sun-frames`: 1-based full-sun model selection
- `--first-nearly-full-sun-frames`: Fallback model frame count when no full-sun selection is provided
- `--exclude-frames`: 1-based frames to exclude
- `--debug-dir`: Debug output directory (default: `<input-dir>/debug`)
- `--debug-full-sun`: Write full-sun model debug images
- `--debug-shifts`: Write shift/centering debug images
- `--debug-gif-frames`: Write the frames actually included in the GIF
- `--debug-watershed`: Compute and show watershed contours in mask debug images
- `--rotate-clockwise-deg`: Clockwise crop rotation
- `--target-duration`: Target GIF duration in seconds

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

## Documentation HTML

Generate a browsable HTML copy of the Markdown documentation:

```bash
bin/generate_docs_html.py
```

The generated site is written to `generated_doc/`, which is ignored by git.
Open `generated_doc/index.html` in a browser to browse it.

The generator also writes `generated_doc/all_docs.html`, a single-page version
suited for printing. If WeasyPrint is installed, a PDF can be generated with:

```bash
bin/generate_docs_html.py --pdf
```

## Usage

### Dark Library (also handles Bias frames)

#### Basic Usage
```bash
# Create master darks from input directories
python3 bin/darklibupdate.py --input-dirs /path/to/darks1 /path/to/darks2

# Process bias frames (0s exposure) along with darks
python3 bin/darklibupdate.py --input-dirs /path/to/darks /path/to/bias

# List existing master darks with details
python3 bin/darklibupdate.py --list-darks

# Test mode (analyze files but don't execute Siril)
python3 bin/darklibupdate.py --input-dirs /path/to/darks --dummy
```

#### Advanced Features
```bash
# Enable dark frame validation to detect problematic frames
python3 bin/darklibupdate.py --input-dirs /path/to/darks --validate-darks

# Set minimum dark count threshold for updates
python3 bin/darklibupdate.py --input-dirs /path/to/darks --min-darks-threshold 20

# Generate detailed processing and validation report
python3 bin/darklibupdate.py --input-dirs /path/to/darks --report

# Set temperature precision for grouping (default 0.5°C)
python3 bin/darklibupdate.py --input-dirs /path/to/darks --temperature-precision 0.2

# Save current configuration for future use
python3 bin/darklibupdate.py --input-dirs /path/to/darks --validate-darks --min-darks-threshold 15 --save-config

# Force recalculation of all master darks
python3 bin/darklibupdate.py --input-dirs /path/to/darks --force-recalc
```

#### Key Options
- `--validate-darks`: Enable statistical validation to reject problematic dark frames
- `--min-darks-threshold N`: Only update master darks if ≥N darks available and date is newer
- `--report`: Generate comprehensive processing and validation report
- `--temperature-precision X`: Set temperature grouping precision in °C
- `--max-age N`: Limit dark frame age to N days from newest frame
- `--save-config`: Save current settings as defaults

For more options, use `--help`.

## Documentation

### User Documentation
- **[Complete Guide](GUIDE_COMPLET.md)** - Comprehensive user documentation (French)
- **[Technical Documentation](docs/)** - Developer and advanced user documentation

### Key Technical Documents
- **[Dark Frame Validation](docs/MIN_DARKS_THRESHOLD_FEATURE.md)** - Minimum dark count threshold feature
- **[Absolute Paths Management](docs/ABSOLUTE_PATHS_FEATURE.md)** - Automatic path conversion for robust configuration
- **[Validation Optimization](docs/VALIDATION_OPTIMIZATION.md)** - Conditional validation logic
- **[Robust Statistics](docs/ROBUST_STATISTICS_UPDATE.md)** - MAD-based validation methods
- **[Configuration Guide](docs/VALIDATION_CONFIG_GUIDE.md)** - Persistent configuration options
