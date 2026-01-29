# masi-qa

A Flask-based web application for reviewing and annotating medical QA images (PNG files). It provides a keyboard-driven interface for rapid quality assurance review of imaging data.

Supports both **BIDS-agnostic** and **BIDS-compliant** modes. For BIDS-only workflows, see [ADSP_AutoQA](https://github.com/MASILab/ADSP_AutoQA).

If you use this for your research, please cite the following paper:

Kim, Michael E., et al. "Scalable quality control on processing of large diffusion-weighted and structural magnetic resonance imaging datasets." PLOS One (2025).

## Features

- Web-based image viewer for PNG files
- Keyboard-driven workflow for fast QA review
- Three-state QA classification: Yes, No, Maybe
- Optional reason field for documenting QA decisions
- **Reviewer name tracking** for multi-user workflows
- **Optional BIDS compliance mode** with filename validation
- Automatic tracking of review timestamps and duration
- Persistent storage via JSON with CSV export
- Autoplay mode for rapid image cycling
- Quick navigation: jump to specific image or next unreviewed

## Installation

### From PyPI

```bash
pip install masi-qa
```

### From Source

```bash
git clone https://github.com/MASILab/masi-qa.git
cd masi-qa
pip install .
```

### Requirements

- Python >= 3.8
- Flask 2.2.2
- pandas 2.0.3
- numpy < 2.0
- tqdm
- Werkzeug 2.2.2

## Quick Start

1. Run the application:
   ```bash
   masi-qa
   ```

2. Open your browser to http://localhost:5000

3. Enter your **reviewer name** (required)

4. Optionally enable **BIDS compliance mode** if your files follow BIDS naming conventions

5. Select a root directory, dataset, and pipeline

6. Click "Continue to QA" to begin review

### Debug Mode

Enable Flask debug mode with hot reload:
```bash
masi-qa --debug
```

## QA Settings

Before starting a QA session, configure these settings on the selection page:

### Reviewer Name (Required)

Enter your name to track who reviewed each image. This is recorded in the `user` field of the output files. The field turns green when filled.

### BIDS Compliance Mode (Optional)

When enabled, the application validates that all PNG filenames follow the [BIDS](https://bids.neuroimaging.io/) naming convention:

```
sub-<subject>_ses-<session>_<pipeline>.png
sub-<subject>_ses-<session>_<pipeline>acq-<acquisition>.png
sub-<subject>_ses-<session>_<pipeline>run-<run>.png
sub-<subject>_<pipeline>.png  (if no session)
```

**Required**: `sub-*` (subject identifier)
**Optional**: `ses-*` (session), `acq-*` (acquisition), `run-*` (run number)

If any files are non-compliant, an error page will list them with the expected format. You can then go back and either rename the files or disable BIDS mode.

When BIDS mode is enabled:
- Output uses a nested JSON structure organized by BIDS tags
- CSV includes columns: `sub, ses, acq, run, QA_status, reason, user, date`

## Expected Directory Structure

```
/your/qa/directory/
├── dataset1/
│   └── pipeline1/
│       ├── image1.png
│       ├── image2.png
│       ├── QA.json    (auto-created)
│       └── QA.csv     (auto-created)
├── dataset2/
│   └── pipeline2/
│       ├── image1.png
│       └── ...
```

Each pipeline directory should contain PNG files only.

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `←` / `→` | Navigate between images |
| `Q` | Mark as "Yes" (pass) |
| `W` | Mark as "No" (fail) |
| `E` | Mark as "Maybe" (uncertain) |
| `N` | Jump to next unreviewed image |
| `Space` | Toggle autoplay mode |
| `Enter` | Focus/unfocus reason input field |

### Quick Navigation

- **Go to specific image**: Use the "Go to #" input field next to the image counter and press Enter
- **Next unreviewed**: Press `N` to skip already-reviewed images and jump to the next one that hasn't been reviewed yet

## QA Data Format

QA results are stored in `QA.json` and exported to `QA.csv`. The format depends on whether BIDS mode is enabled.

### Standard Mode (Non-BIDS)

```json
{
  "filename.png": {
    "filename": "filename.png",
    "QA_status": "yes",
    "reason": "",
    "user": "reviewer_name",
    "date": "2024-07-10 00:09:13",
    "duration": 45
  }
}
```

**CSV columns**: `filename, QA_status, reason, user, date, duration`

### BIDS Mode

```json
{
  "sub-001": {
    "ses-01": {
      "QA_status": "yes",
      "reason": "",
      "user": "reviewer_name",
      "date": "2024-07-10 00:09:13",
      "sub": "sub-001",
      "ses": "ses-01",
      "acq": "",
      "run": ""
    }
  }
}
```

**CSV columns**: `sub, ses, acq, run, QA_status, reason, user, date`

### Field Descriptions

| Field | Description |
|-------|-------------|
| `filename` | Name of the image file (non-BIDS mode only) |
| `sub/ses/acq/run` | BIDS identifiers (BIDS mode only) |
| `QA_status` | Review status: `yes`, `no`, or `maybe` |
| `reason` | Optional text explaining the QA decision |
| `user` | Name of the reviewer (empty until reviewed) |
| `date` | Timestamp of the last review (empty until reviewed) |
| `duration` | Total seconds spent viewing the image (non-BIDS mode only) |

A `QA.csv` file is automatically generated alongside the JSON for easy data analysis.

## Important Notes

- **PNG only**: PDF files are not supported; use PNG images only.

- **Use Chrome**: The application runs significantly faster in Chrome. Firefox may experience slowness and jittery behavior.

- **Single user per dataset**: Do not have multiple people QAing the same dataset/pipeline simultaneously. The data is only loaded at startup; concurrent edits will not be synchronized.

- **Large directories**: Directories with many images take time to preload. Be patient during initial loading.

- **Default status**: New images are assigned `QA_status: "yes"` by default. Changes are saved when navigating to the next image.

## Citation

If you use this software in your research, please cite:

> Kim, Michael E., et al. "Scalable quality control on processing of large diffusion-weighted and structural magnetic resonance imaging datasets." *PLOS One* (2025).

## Authors

- Michael Kim (michael.kim@vanderbilt.edu)
- Yihao Liu (yihao.liu@vanderbilt.edu)

## License

MIT License - Copyright (c) 2024-2026 MASI Lab @ Vanderbilt

See [LICENSE](LICENSE) for details.

## Related Projects

- [ADSP_AutoQA](https://github.com/MASILab/ADSP_AutoQA) - BIDS-specific version with enforced BIDS compliance
