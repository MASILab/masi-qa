## Project Overview

masi-qa is a Flask-based web application for reviewing and annotating medical images (PNG files). It provides a keyboard-driven interface for rapid quality assurance review of imaging data. Supports both BIDS-agnostic and BIDS-compliant modes.

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
- `permission_error.html`: Error page when QA files cannot be written; shows per-file ownership/mode info and context-aware fix commands

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

## Permission Error Handling

When the app cannot write QA files, it shows `permission_error.html` with context about each affected file and context-aware fix commands.

**Detection** (`app_montage.py: check_write_permissions`):
- Checks directory write access (needed for `.QA.lock`)
- Checks `QA.json` and `QA.csv` write access if they exist
- Checks that existing files have mode `0o770`; auto-fixes silently if current user is the owner, otherwise reports as `wrong-permissions`

**Per-issue metadata** (stored in `file_issues` list):
- `name`, `full_path`, `status`, `message` — what the problem is
- `owner`, `group`, `mode_octal`, `mode_symbolic` — current file state
- `user_is_owner`, `user_in_group`, `group_has_write` — current user's relationship to the file

**Context-aware fix commands** (template groups issues by relationship):

| Relationship | Fix shown |
|---|---|
| User owns the file | `chmod u+w "path"` (no sudo) |
| User is in the file's group, g-w not set | `sudo chmod g+w "path"` |
| User has no relationship | `sudo chown $(whoami) "path"` (primary) |
| User has no relationship, g+w already set | Also offers `sudo chgrp $(id -gn) "path"` (preserves original owner) |
| Wrong permissions, not owner | `sudo chmod 770 "path"` |

**Helper functions:**
- `_get_path_info(path)` — stats a path, returns owner/group names, octal/symbolic mode, raw uid/gid/mode int
- `check_write_permissions(pipeline_path)` — returns `(can_write, file_issues, files_missing)`

## Key Implementation Details

- Host bound to `0.0.0.0` for Docker compatibility
- Only supports PNG image format
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
