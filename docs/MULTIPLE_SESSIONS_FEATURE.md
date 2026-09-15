# Multiple Sessions Processing Feature

## Overview

The `lightProcess.py` script supports processing either a single session directory or a target directory that contains several sub-sessions. This allows one command to process a full observation block such as a target captured over several nights or several subfields.

## Supported layouts

### Single session
```text
/path/to/session_M31/
  light/
  flat/
```

### Target with several sub-sessions
```text
/path/to/
  M_33/
    session_01/
      light/
      flat/
    session_02/
      light/
      flat/
    session_03/
      light/
      flat/
```

When such a structure is detected, the script treats each `session_x` as an independent processing unit, then combines their calibrated outputs into a single result for the parent target directory.

## Usage

### Single Session
```bash
python3 bin/lightProcess.py /path/to/session_M31 [options]
```

### Multiple independent sessions passed explicitly
```bash
python3 bin/lightProcess.py /path/to/session_M31 /path/to/session_M42 /path/to/session_NGC7000 [options]
```

### Target root with sub-sessions
```bash
python3 bin/lightProcess.py /path/to/M_33 --log-level INFO
```

## Features

### ✅ **Sequential Processing**
- Each session is processed one after another
- Progress indicator shows current session (e.g., "2/5")
- Clear separation between sessions in logs

### ✅ **Target-root auto-detection**
- If a directory contains immediate child folders with `light`/`Light`, each child is processed as its own session.
- The parent target is then stacked from the resulting session outputs.
- This is the mode used for structures like `M_33/session_01`, `M_33/session_02`.

### ✅ **Combined output stacking**
- The combined stack is created from the outputs of the successful child sessions.
- The generated Siril script uses the converted sequence name `session_` rather than a stale `r_session_` name.
- This avoids the invalid sequence error during the final stack command.

### ✅ **Robust Error Handling**
- If one session fails, processing continues with the next
- Failed sessions are tracked and reported
- Detailed error logging for each session

### ✅ **Comprehensive Reporting**
- Summary report at the end showing:
  - Total sessions processed successfully
  - List of failed sessions
  - Overall success/failure status

### ✅ **Session Validation**
- Each session directory is validated before processing
- Checks for existence of `light` or `Light` subdirectory
- Invalid sessions are reported immediately

## Command Examples

### Process Multiple Sessions with Custom Settings
```bash
python3 bin/lightProcess.py \
    /data/sessions/2024-01-15_M31 \
    /data/sessions/2024-01-16_M42 \
    /data/sessions/2024-01-17_NGC7000 \
    --output /processed/results \
    --stack-method median \
    --rejection-method sigma \
    --rejection-param1 2.5 \
    --rejection-param2 3.0 \
    --dry-run
```

### Batch Process with Configuration Save
```bash
python3 bin/lightProcess.py \
    ~/sessions/winter_2024/* \
    --save-config \
    --log-level INFO
```

## Log Output Format

### Session Header
```
============================================================
Traitement de la session 2/5: /path/to/session_M42
============================================================
```

### Session Processing
```
Début du traitement de la session: /path/to/session_M42
Répertoire light: /path/to/session_M42/light
✅ Session /path/to/session_M42 traitée avec succès
```

### Final Summary
```
============================================================
RÉSUMÉ DU TRAITEMENT
============================================================
Sessions traitées avec succès: 3/5
Sessions échouées (2):
  - /path/to/session_bad1
  - /path/to/session_bad2
⚠️  Traitement partiel: 3/5 sessions réussies
```

## Exit Codes

- **0**: All sessions processed successfully
- **1**: Some or all sessions failed

## Implementation Details

### Session Loop
```python
for i, session_dir in enumerate(session_dirs, 1):
    logging.info(f"Traitement de la session {i}/{total_sessions}: {session_dir}")
    
    # Create processor for this session
    processor = LightProcessor(session_dir=session_dir, ...)
    
    # Process the session
    success = processor.process_session(stack_params)
    
    # Track results
    if success:
        successful_sessions += 1
    else:
        failed_sessions.append(session_dir)
```

### Error Handling
- **Session validation errors**: Stop immediately
- **Processor initialization errors**: Skip session, continue with next
- **Processing errors**: Skip session, continue with next
- **KeyboardInterrupt**: Stop processing, report partial results

## Benefits

### 🚀 **Efficiency**
- Process multiple observation sessions with one command
- No need to manually run the script multiple times
- Consistent settings applied across all sessions

### 🛡️ **Reliability**
- Individual session failures don't stop the entire batch
- Clear reporting of what succeeded and what failed
- Easy to retry only the failed sessions

### 📊 **Visibility**
- Progress tracking with session counters
- Detailed logs for each session
- Comprehensive summary report

### 🔧 **Flexibility**
- Works with any number of sessions (1 to many)
- Compatible with all existing command-line options
- Shell glob patterns supported for easy batch selection

## Backward Compatibility

This change is fully backward compatible. Existing scripts and workflows that process single sessions will continue to work exactly as before.

## Use Cases

### 1. Nightly Observations
Process all sessions from a night of observations:
```bash
python3 bin/lightProcess.py /observations/2024-01-15/session_* --log-level INFO
```

### 2. Survey Projects
Process multiple target sessions for a survey:
```bash
python3 bin/lightProcess.py \
    /survey/messier/M31 \
    /survey/messier/M42 \
    /survey/messier/M45 \
    --output /survey/processed
```

### 3. Archive Processing
Reprocess historical sessions with updated settings:
```bash
python3 bin/lightProcess.py /archive/2023/*/* --force --stack-method median
```