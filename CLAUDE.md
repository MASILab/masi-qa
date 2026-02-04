# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

masi-qa is a Flask-based web application for reviewing and annotating medical images (PNG files). It provides a keyboard-driven interface for rapid quality assurance review of imaging data. Supports both BIDS-agnostic and BIDS-compliant modes.

**Citation:** Kim, Michael E., et al. "Scalable quality control on processing of large diffusion-weighted and structural magnetic resonance imaging datasets." PLOS One (2025).

**Related:** BIDS-specific version at `https://github.com/MASILab/ADSP_AutoQA`

## Running the Application

```bash
masi-qa [--debug]
```

- `--debug`: Enable Flask debug mode with hot reload

Access at http://localhost:5000 (or http://0.0.0.0:5000 for Docker/external access)

## Dependencies

Install via: `pip install masi-qa`

- Flask 2.2.2
- pandas 2.0.3
- tqdm
- Werkzeug 2.2.2

## Architecture

**Backend (`src/masi_qa/app_montage.py`):**
- Flask REST API serving images and handling QA status updates
- JSON/CSV file management for persistent QA tracking
- Directory traversal for dataset/pipeline discovery

**Frontend (`src/masi_qa/templates/`):**
- `montage.html`: Main QA interface with image viewer and keyboard shortcuts
- `root.html`: Initial dataset selection with QA settings (reviewer name, BIDS mode)
- `bids_errors.html`: Error page showing non-BIDS-compliant files
- `mode_mismatch.html`: Error page when selected mode doesn't match existing QA data format

**Data Flow:**
1. User selects root directory → dataset → pipeline, then enters reviewer name
2. Backend scans for PNG files, validates BIDS compliance (if enabled), creates/loads `QA.json`
3. Frontend displays images with QA controls
4. QA status saved to `QA.json` and exported to `QA.csv`

## QA Settings

The dataset selection page includes two settings:

**Reviewer Name:** Text input to track who reviewed each image. The name is recorded in the `user` field of the output files after an image is reviewed.

**BIDS Compliance Mode:** When enabled:
- Validates all PNG filenames match the BIDS pattern: `sub-XX_ses-YY_pipeline.png`
- Shows an error page listing non-compliant files if any are found
- Uses nested JSON structure and BIDS-formatted CSV output (matching ADSP_AutoQA)
- Required BIDS fields: `sub-*`, Optional: `ses-*`, `acq-*`, `run-*`

**Format Mismatch Detection:** When opening a dataset that has existing QA data:
- Detects if the existing `QA.json` format (BIDS vs flat) matches the selected mode
- If mismatched, shows an error page with options to:
  1. Go back and change the mode to match existing data
  2. Convert the existing data to match the selected mode
- Conversion creates a backup (`QA.json.backup`) before modifying
- Converting flat→BIDS requires all filenames to be BIDS-compliant

**QA.json Entry Structure (Non-BIDS Mode):**
```json
{
  "filename.png": {
    "filename": "filename.png",
    "QA_status": "yes",
    "reason": "",
    "user": "",
    "date": "2024-07-10 00:09:13",
    "duration": 45
  }
}
```

**QA.json Entry Structure (BIDS Mode):**
```json
{
  "sub-001": {
    "ses-01": {
      "QA_status": "yes",
      "reason": "",
      "user": "",
      "date": "2024-07-10 00:09:13",
      "sub": "sub-001",
      "ses": "ses-01",
      "acq": "",
      "run": ""
    }
  }
}
```

- `date`: Timestamp when user last reviewed the image (empty until reviewed)
- `user`: Name of reviewer who last reviewed the image (empty until reviewed)
- `duration`: Total seconds spent viewing the image (non-BIDS mode only)

## Expected Directory Structure

```
/qa/directory/
├── dataset1/
│   └── pipeline1/
│       ├── image1.png
│       ├── QA.json (auto-created)
│       └── QA.csv (auto-created)
```

## Keyboard Shortcuts (montage.html)

- Arrow Left/Right: Navigate images (one at a time)
- Q: Mark "Yes"
- W: Mark "No"
- E: Mark "Maybe"
- N: Jump to next unreviewed image
- Space: Toggle autoplay
- Enter: Focus/unfocus reason input

**Quick Navigation:**
- Use the "Go to #" input field to jump directly to a specific image number
- Press N to skip reviewed images and jump to the next unreviewed one

## Important Operational Notes

- **Use Chrome**: The app runs significantly faster in Chrome; Firefox is slow and jittery
- **No concurrent users**: Do not have multiple people QAing the same dataset/pipeline simultaneously. The CSV is only read at startup; updates are not detected until the app restarts
- **Large directories**: Directories with many QA images take time to preload. Be patient and avoid clicking outside the browser during loading (may crash)

## Key Implementation Details

- Host bound to `0.0.0.0` for Docker compatibility
- Only supports PNG image format
- QA status values: "yes", "no", "maybe"
- Unix group permissions set to `p_masi` with 0o770
- `QA.json` auto-created with default status "yes"; changes saved when navigating to next image
- `QA.csv` is regenerated from JSON on each save

## QA.json and QA.csv Design Rationale

**Why both JSON and CSV formats exist:**

The application produces both `QA.json` and `QA.csv` because:

1. **JSON is the working format** - JavaScript natively parses and manipulates JSON. The frontend receives QA data as JSON via Jinja templating (`{{ json_dict | tojson | safe }}`), manipulates it as a JavaScript object, and sends updates back to the server as JSON via `fetch()` API with `Content-Type: application/json`.

2. **CSV is the export/analysis format** - Researchers typically want CSV for downstream analysis (Excel, pandas, R, etc.). The CSV is a derived output regenerated from JSON on every save.

**Data flow:**
```
Backend (Python)                    Frontend (JavaScript)
     │                                      │
     ├── Load/create QA.json ──────────────►│ Parse as JS object (nestedDict)
     │                                      │
     │                                      ├── User edits QA status/reason
     │                                      │
     │◄── POST /update_qa_dict ─────────────┤ Send nestedDict as JSON
     │                                      │
     ├── save_json_file()                   │
     ├── convert_json_to_csv()              │
     │                                      │
```

**Key code locations:**
- JSON passed to frontend: `app_montage.py:587` (`render_template`)
- Frontend receives JSON: `montage.html:214` (`const nestedDict = JSON.parse(...)`)
- Frontend sends updates: `montage.html:570-588` (`fetch('/update_qa_dict', ...)`)
- Backend saves both: `app_montage.py:664-667` (`save_json_file` then `convert_json_to_csv`)

**Known inefficiency:**
The CSV is fully regenerated on every image navigation, not just at session end. This is redundant I/O if CSV is the primary output format. Potential improvements:
- Generate CSV only on explicit export or session end
- Add a "dirty" flag and batch CSV writes periodically
- Use SQLite for efficient single-row updates with CSV export on demand
