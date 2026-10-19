# Python Scripts for Siril Documentation

[Documentation](../../README.md) › [pyecho](README.md)

<a id="overview"></a>

## 1. Overview

This document describes the Python replacement scripts for common shell commands used in Siril scripts. These scripts provide cross-platform compatibility and can be easily extended with additional features if needed.

<a id="pyecho-script"></a>

## 2. PyEcho Script

<a id="overview_1"></a>

### 2.1. Overview
The `pyecho.py` script is a Python replacement for the traditional `echo` command in Siril scripts.

<a id="location"></a>

### 2.2. Location
- **File**: `bin/pyecho.py`
- **Purpose**: Echo arguments to stdout, replacing `echo` commands in generated Siril scripts

<a id="usage"></a>

### 2.3. Usage

#### Basic usage (command line):
```bash
python3 bin/pyecho.py "Hello World"
# Output: Hello World

python3 bin/pyecho.py "Multiple" "arguments" "work too"
# Output: Multiple arguments work too

python3 bin/pyecho.py
# Output: (empty line, like echo without arguments)
```

#### In Siril scripts:
```siril
# Old way:
echo "Converting Light Frames to .fit files"

# New way (automatically generated):
pyscript /path/to/SirilProcessing/bin/pyecho.py "Converting Light Frames to .fit files"
```

<a id="pydir-script"></a>

## 3. PyDir Script

<a id="overview_2"></a>

### 3.1. Overview
The `pydir.py` script is a Python replacement for the `dir` command in Siril scripts, providing cross-platform directory listing functionality.

<a id="location_1"></a>

### 3.2. Location
- **File**: `bin/pydir.py`
- **Purpose**: List directory contents, replacing `dir` commands in generated Siril scripts

<a id="features"></a>

### 3.3. Features
- Cross-platform directory listing
- Formatted output with file sizes
- Separates directories and files
- Error handling for non-existent or inaccessible directories
- Sorts items alphabetically (directories first, then files)

<a id="usage_1"></a>

### 3.4. Usage

#### Basic usage (command line):
```bash
python3 bin/pydir.py
# Lists current directory

python3 bin/pydir.py /path/to/directory
# Lists specified directory

python3 bin/pydir.py bin
# Lists relative directory
```

#### Example output:
```
Directory listing of /home/user/SirilProcessing/bin:
  darkLibUpdate.py                  10.3 KB
  lightProcess.py                    8.6 KB
  pydir.py                           2.7 KB
  pyecho.py                           498 B
Total: 0 directories, 4 files
```

#### In Siril scripts:
```siril
# Old way:
dir

# New way (automatically generated):
pyscript /path/to/SirilProcessing/bin/pydir.py
```

<a id="error-handling"></a>

### 3.5. Error Handling
- Returns exit code 1 on errors
- Prints error messages to stderr
- Handles permission denied scenarios
- Validates directory existence

<a id="integration"></a>

## 4. Integration

Both scripts are automatically integrated into the light processing workflow through the `LightProcessor` class:

1. Script paths are dynamically determined based on the current module location
2. All `echo` and `dir` commands in generated Siril scripts are replaced with `pyscript` calls
3. Arguments are properly quoted and passed to the Python scripts

<a id="benefits"></a>

## 5. Benefits

1. **Cross-platform compatibility**: Works the same on all platforms where Python is available
2. **Consistency**: Uniform behavior across different operating systems
3. **Extensibility**: Can be easily enhanced with additional features (timestamps, colors, etc.)
4. **Debugging**: Easier to debug and modify than shell commands
5. **Enhanced output**: pydir provides formatted output with file sizes and better organization

<a id="implementation-details"></a>

## 6. Implementation Details

- Located in: `bin/pyecho.py` and `bin/pydir.py`
- Used by: `lib/lightprocessor.py` in the `_generate_siril_script()` method
- Path resolution: Dynamically calculated using `Path(__file__).parent.parent / "bin"`

<a id="examples-in-generated-scripts"></a>

## 7. Examples in Generated Scripts

The script generates Siril commands like:

```siril
pyscript /home/user/SirilProcessing/bin/pyecho.py "====================================================================="
pyscript /home/user/SirilProcessing/bin/pyecho.py "Convert Light Frames to .fit files"
pyscript /home/user/SirilProcessing/bin/pydir.py
pyscript /home/user/SirilProcessing/bin/pyecho.py "cmd: convert light_sequence -out=/tmp/process"
```

Instead of the original:

```siril
echo "====================================================================="
echo "Convert Light Frames to .fit files"
dir
echo "cmd: convert light_sequence -out=/tmp/process"
```
