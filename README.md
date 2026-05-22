# masi-qa

A Flask-based web application for reviewing and annotating medical QA images (PNG files). It provides a keyboard-driven interface for rapid quality assurance review of imaging data.

Supports both Standard and BIDS-compliant modes.

If you use this for your research, please cite the following papers:

Kim, M. E., Gao, C., Ramadass, K., Rudravaram, G., McMaster, E. M., Saunders, A. M., Yang, Y., Levy, E., Kanakaraj, P., Newlin, N. R., Li, Z., Khairi, N. M., Dewey, B. E., The HABS-HD Study Team, Alzheimer’s Disease Neuroimaging Initiative, Schilling, K. G., Archer, D., Hohman, T. J., Landman, B. A., & Liu, Y. (2026). Large-scale deployment and analytical implications of structured quality control in diffusion magnetic resonance imaging. arXiv. https://doi.org/10.48550/arXiv.2605.21799

Kim, M. E., Gao, C., Newlin, N. R., Rudravaram, G., Krishnan, A. R., Ramadass, K., Kanakaraj, P., Schilling, K. G., Dewey, B. E., Bennett, D. A., O’Bryant, S., Barber, R. C., Archer, D., Hohman, T. J., Bao, S., Li, Z., Landman, B. A., Khairi, N. M., Alzheimer’s Disease Neuroimaging Initiative, & HABS-HD Study Team. (2025). Scalable quality control on processing of large diffusion-weighted and structural magnetic resonance imaging datasets. PLOS ONE, 20(8), e0327388. https://doi.org/10.1371/journal.pone.0327388

## Features

- Web-based image viewer for PNG files
- Keyboard-driven workflow for fast QA review
- Configurable QA status options (2–8 labels, default: Yes, No, Maybe)
- Optional reason field for documenting QA decisions
- Reviewer name tracking for multi-user workflows
- BIDS compliance mode via separate `masi-bids-qa` command
- Automatic tracking of review timestamps and duration
- Persistent storage via JSON with CSV export
- Autoplay mode with adjustable speed (50–500 ms per image)
- Quick navigation: jump to specific image or next unreviewed
- Completion notification when all images have been reviewed

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

## Quick Start

1. Run the application:
   ```bash
   masi-qa           # Standard mode (any PNG filename)
   masi-bids-qa      # BIDS mode (requires BIDS-compliant filenames)
   ```

2. Open the URL shown in the terminal (port is automatically selected)

3. Select a root directory, dataset, and pipeline

4. Enter your reviewer name in the QA Settings section

5. Click "Continue to QA" to begin review

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `←` / `→` | Navigate between images |
| `Q` / `W` / `E` ... | Set QA status (first 8 options mapped to Q, W, E, R, A, S, D, F) |
| `N` | Jump to next unreviewed image |
| `Space` | Toggle autoplay mode |
| `Enter` | Focus/unfocus reason input field |

### Quick Navigation

- **Go to specific image**: Use the "Go to #" input field next to the image counter and press Enter
- **Next unreviewed**: Press `N` to skip already-reviewed images and jump to the next one that hasn't been reviewed yet
- **Completion notification**: A green toast notification appears when all images have been reviewed. This triggers automatically when you review the last unreviewed image, when you press `N` with no unreviewed images remaining, or when you open a dataset where all images were already reviewed

### Autoplay

Press `Space` to start/stop autoplay. Use the **Speed** slider in the toolbar to set the delay per image (50–500 ms, default 250 ms).

### Configurable QA Options

On the dataset selection page, expand **QA Options** to customize the status labels shown during review. You can:

- Rename any option (e.g. change "maybe" to "exclude")
- Add options (up to 8 total)
- Remove options (minimum 2 required)

Keyboard shortcuts are assigned automatically in order: `Q`, `W`, `E`, `R`, `A`, `S`, `D`, `F`. The first option is the default status assigned to new images.

The default options are **yes**, **no**, **maybe**.

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

## Modes

### Standard Mode (`masi-qa`)

Works with any PNG filename. This is the default mode for general use.

### BIDS Mode (`masi-bids-qa`)

Validates that all PNG filenames follow the [BIDS](https://bids.neuroimaging.io/) naming convention:

```
sub-<subject>_ses-<session>_<pipeline>.png
sub-<subject>_<pipeline>.png  (if no session)
```

**Required**: `sub-*` (subject identifier)
**Optional**: `ses-*` (session), `acq-*` (acquisition), `run-*` (run number)

If any files are non-compliant, an error page will list them. You can rename the files or use `masi-qa` instead.

### Switching Between Modes

If you open a dataset that has existing QA data in a different format, the application will detect this and offer options to either restart with the matching command or convert the existing data. Converting creates a backup (`QA.json.backup`) before modifying.

## QA Data Format

QA results are stored in `QA.json` and exported to `QA.csv`. The format depends on the mode.

### Standard Mode

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
| `filename` | Name of the image file (Standard mode only) |
| `sub/ses/acq/run` | BIDS identifiers (BIDS mode only) |
| `QA_status` | Review status; one of the configured QA options (default: `yes`, `no`, `maybe`) |
| `reason` | Optional text explaining the QA decision |
| `user` | Name of the reviewer (empty until reviewed) |
| `date` | Timestamp of the last review (empty until reviewed) |
| `duration` | Total seconds spent viewing the image (Standard mode only) |

## Advanced Options

### Debug Mode

Enable Flask debug mode with hot reload for development:
```bash
masi-qa --debug
```

### Custom Port

Specify a port manually (default: auto-detect from 5000-5009):
```bash
masi-qa --port 8080
```

## Important Notes

- **PNG only**: PDF files are not supported; use PNG images only.

- **Use Chrome**: The application runs significantly faster in Chrome. Firefox may experience slowness and jittery behavior.

- **Single user per dataset**: Do not have multiple people QAing the same dataset/pipeline simultaneously. The data is only loaded at startup; concurrent edits will not be synchronized.

- **Large directories**: Directories with many images take time to preload. Be patient during initial loading.

- **Default status**: New images are assigned `QA_status: "yes"` by default. Changes are saved when navigating to the next image.

## Citation

If you use this software in your research, please cite:

@article{kim2025scalable,
  title={Scalable quality control on processing of large diffusion-weighted and structural magnetic resonance imaging datasets},
  author={Kim, Michael E and Gao, Chenyu and Newlin, Nancy R and Rudravaram, Gaurav and Krishnan, Aravind R and Ramadass, Karthik and Kanakaraj, Praitayini and Schilling, Kurt G and Dewey, Blake E and Bennett, David A and others},
  journal={PloS one},
  volume={20},
  number={8},
  pages={e0327388},
  year={2025},
  publisher={Public Library of Science San Francisco, CA USA}
}

@article{kim2026largescaledeployment,
  title         = {Large-Scale Deployment and Analytical Implications of Structured Quality Control in Diffusion Magnetic Resonance Imaging},
  author        = {Kim, Michael E. and Gao, Chenyu and Ramadass, Karthik and Rudravaram, Gaurav and McMaster, Elyssa M. and Saunders, Adam M. and Yang, Yisu and Levy, Elias and Kanakaraj, Praitayini and Newlin, Nancy R. and Li, Zhiyuan and Mohd Khairi, Nazirah and Dewey, Blake E. and {The HABS-HD Study Team} and {Alzheimer's Disease Neuroimaging Initiative} and Schilling, Kurt G. and Archer, Derek and Hohman, Timothy J. and Landman, Bennett A. and Liu, Yihao},
  year          = {2026},
  journal       = {arXiv preprint arXiv:2605.21799},
  eprint        = {2605.21799},
  archivePrefix = {arXiv},
  primaryClass  = {eess.IV},
  doi           = {10.48550/arXiv.2605.21799},
  url           = {https://arxiv.org/abs/2605.21799}
}

## Authors

- Michael Kim (michael.kim@vanderbilt.edu)
- Yihao Liu (yihao.liu@vanderbilt.edu)
- Gaurav Rudravaram (gaurav.rudravaram@Vanderbilt.Edu)

## License

MIT License - Copyright (c) 2024-2026 MASI Lab @ Vanderbilt

See [LICENSE](LICENSE) for details.

## Related Projects

- [ADSP_AutoQA](https://github.com/MASILab/ADSP_AutoQA) - BIDS-specific version with enforced BIDS compliance
