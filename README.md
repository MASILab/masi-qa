# masi-qa

A Flask-based web application for reviewing and annotating medical QA images (PNG files). It provides a keyboard-driven interface for rapid quality assurance review of imaging data.

This is the BIDS-agnostic version of [ADSP_AutoQA](https://github.com/MASILab/ADSP_AutoQA).

## Features

- Web-based image viewer for PNG files
- Keyboard-driven workflow for fast QA review
- Three-state QA classification: Yes, No, Maybe
- Optional reason field for documenting QA decisions
- Automatic tracking of review timestamps and duration
- Persistent storage via JSON with CSV export
- Autoplay mode for rapid image cycling
- Quick navigation: jump to specific image or next unreviewed

## Installation

```bash
pip install masi-qa
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

3. Select a root directory containing your datasets

4. Navigate to a dataset and pipeline to begin QA review

### Debug Mode

Enable Flask debug mode with hot reload:
```bash
masi-qa --debug
```

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

QA results are stored in `QA.json` with the following structure:

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

| Field | Description |
|-------|-------------|
| `filename` | Name of the image file |
| `QA_status` | Review status: `yes`, `no`, or `maybe` |
| `reason` | Optional text explaining the QA decision |
| `date` | Timestamp of the last review |
| `duration` | Total seconds spent viewing the image (cumulative) |

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

MIT License - Copyright (c) 2024 MASI Lab @ Vanderbilt

See [LICENSE](LICENSE) for details.

## Related Projects

- [ADSP_AutoQA](https://github.com/MASILab/ADSP_AutoQA) - BIDS-specific version of this tool
