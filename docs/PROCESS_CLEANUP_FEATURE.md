# Process Directory Cleanup Feature

## Overview

The LightProcessor now includes automatic cleanup of the `process` directory at the beginning of each light processing workflow to ensure a clean working environment.

## Implementation

### Location
- **File**: `lib/lightprocessor.py`
- **Method**: `process_light_group()` 
- **Lines**: Added before creating the process directory

### Logic
```python
# Nettoyer le répertoire de traitement existant pour un démarrage propre
process_dir = self.work_dir / "process"
if process_dir.exists():
    try:
        shutil.rmtree(process_dir)
        logging.debug(f"Répertoire de traitement nettoyé: {process_dir}")
    except Exception as e:
        logging.warning(f"Impossible de nettoyer le répertoire de traitement {process_dir}: {e}")
        # Continuer quand même, les fichiers seront écrasés si possible

# Créer le répertoire de traitement
process_dir.mkdir(parents=True, exist_ok=True)
```

## Benefits

### 🧹 **Clean Start**
- Removes all residual files from previous processing runs
- Prevents conflicts with old intermediate files
- Ensures a fresh working environment for each processing session

### 🛡️ **Error Prevention**
- Avoids issues with corrupted or incomplete files from failed runs
- Prevents filename conflicts and file locking issues
- Reduces risk of processing errors due to stale data

### 🔄 **Reliability**
- Improves consistency between processing runs
- Makes the workflow more predictable and debuggable
- Eliminates "works sometimes" issues related to file system state

### ⚡ **Performance**
- Prevents accumulation of large intermediate files over time
- Keeps the work directory size manageable
- Avoids potential disk space issues

## Error Handling

The cleanup process includes robust error handling:

- **Success**: Directory is removed and recreated cleanly
- **Permission Error**: Warning is logged, but processing continues
- **Other Errors**: Warning is logged, files may be overwritten during processing

## Scenarios Handled

### 1. Normal Case
```
process_dir/ exists with old files
├── pp_light_old_00001.fit
├── r_pp_light_old_00002.fit
└── conversion.txt

After cleanup:
process_dir/ (empty, ready for new processing)
```

### 2. Permission Issues
```
Warning: Cannot clean process_dir due to permissions
Processing continues, new files will overwrite where possible
```

### 3. First Run
```
process_dir/ doesn't exist
Directory created fresh for first use
```

## Log Messages

### Debug Level
- `Répertoire de traitement nettoyé: /path/to/process`

### Warning Level  
- `Impossible de nettoyer le répertoire de traitement /path/to/process: [error]`

## Usage

This feature is automatic and requires no user configuration. It activates:

- At the beginning of each `process_light_group()` execution
- Before creating the process directory
- Only in non-dry-run mode (actual processing)

## Implementation Details

- Uses `shutil.rmtree()` for complete directory removal
- Creates directory with `mkdir(parents=True, exist_ok=True)`
- Wrapped in try-catch for graceful error handling
- Logs at appropriate levels for troubleshooting

## Testing

The feature has been tested with:
- ✅ Empty process directory
- ✅ Process directory with multiple files and subdirectories  
- ✅ Non-existent process directory
- ✅ Permission error scenarios
- ✅ Dry-run mode (cleanup is skipped)

This ensures reliable and clean processing for all light frame workflows.