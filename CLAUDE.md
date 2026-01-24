# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GeneralQATool is a Flask-based web application for reviewing and annotating medical images (PNG/PDF files). It provides a keyboard-driven interface for rapid quality assurance review of imaging data. This is the BIDS-agnostic version of ADSP_AutoQA.

**Citation:** Kim, Michael E., et al. "Scalable quality control on processing of large diffusion-weighted and structural magnetic resonance imaging datasets." PLOS One (2025).

**Related:** BIDS-specific version at https://github.com/MASILab/ADSP_AutoQA

## Running the Application

```bash
python CODE/app_montage.py /path/to/qa/directory [--debug]
```

- First argument: Absolute path to QA images directory (required)
- `--debug`: Enable Flask debug mode with hot reload

Access at http://localhost:5000 (or http://0.0.0.0:5000 for Docker/external access)

## Dependencies

Install via: `pip install -r requirements.txt`

- Flask 2.2.2
- pandas 2.0.3
- tqdm
- Werkzeug 2.2.2

## Architecture

**Backend (`CODE/app_montage.py`):**
- Flask REST API serving images and handling QA status updates
- JSON/CSV file management for persistent QA tracking
- Directory traversal for dataset/pipeline discovery

**Frontend (`CODE/templates/`):**
- `montage.html`: Main QA interface with image viewer and keyboard shortcuts
- `root.html`: Initial dataset selection
- `datasets.html`: Pipeline directory selection

**Data Flow:**
1. User selects dataset → pipeline directory
2. Backend scans for PNG/PDF files, creates/loads `QA.json`
3. Frontend displays images with QA controls
4. QA status saved to `QA.json` and exported to `QA.csv`

**QA.json Entry Structure:**
```json
{
  "filename.png": {
    "filename": "filename.png",
    "QA_status": "yes",
    "reason": "",
    "date": "2024-07-10 00:09:13",
    "duration": 45
  }
}
```

- `date`: Timestamp when user last reviewed the image (empty until reviewed)
- `duration`: Total seconds spent viewing the image (accumulates across views)

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

- Arrow Left/Right: Navigate images
- Q: Mark "Yes"
- W: Mark "No"
- E: Mark "Maybe"
- Space: Toggle autoplay
- Enter: Focus/unfocus reason input

## Important Operational Notes

- **Use Chrome**: The app runs significantly faster in Chrome; Firefox is slow and jittery
- **No concurrent users**: Do not have multiple people QAing the same dataset/pipeline simultaneously. The CSV is only read at startup; updates are not detected until the app restarts
- **Large directories**: Directories with many QA images take time to preload. Be patient and avoid clicking outside the browser during loading (may crash)

## Key Implementation Details

- Host bound to `0.0.0.0` for Docker compatibility
- Enforces single image format (PNG or PDF) per pipeline
- QA status values: "yes", "no", "maybe"
- Unix group permissions set to `p_masi` with 0o775
- `QA.json` auto-created with default status "yes"; changes saved when navigating to next image
- `QA.csv` is regenerated from JSON on each save
