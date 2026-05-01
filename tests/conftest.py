"""
Shared fixtures for masi-qa tests.

Fixtures are available to all test files automatically — no imports needed.
"""
import json
import os
import pytest
from pathlib import Path

import masi_qa.app_montage as app_module
from masi_qa.app_montage import app as flask_app

# A minimal valid 1×1 RGB PNG (no external dependencies required).
MINIMAL_PNG = (
    b'\x89PNG\r\n\x1a\n'
    b'\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x02\x00\x00\x00\x90wS\xde'
    b'\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N'
    b'\x00\x00\x00\x00IEND\xaeB`\x82'
)


# ---------------------------------------------------------------------------
# App-level fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_bids_mode():
    """Restore the global BIDS_MODE after every test."""
    original = app_module.BIDS_MODE
    yield
    app_module.BIDS_MODE = original


@pytest.fixture
def app():
    flask_app.config['TESTING'] = True
    flask_app.config['SECRET_KEY'] = 'test-secret'
    yield flask_app


@pytest.fixture
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Filesystem fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_pipeline(tmp_path):
    """
    Temporary pipeline dir with two flat (non-BIDS) PNG files.

    Layout: tmp_path/dataset/pipeline/{image_001.png, image_002.png}
    The returned path is the pipeline directory.
    """
    pipeline = tmp_path / 'dataset' / 'pipeline'
    pipeline.mkdir(parents=True)
    (pipeline / 'image_001.png').write_bytes(MINIMAL_PNG)
    (pipeline / 'image_002.png').write_bytes(MINIMAL_PNG)
    return pipeline


@pytest.fixture
def tmp_bids_pipeline(tmp_path):
    """
    Temporary pipeline dir with BIDS-compliant PNG files.

    Layout: tmp_path/dataset/pipeline/sub-0X_[ses-01_]T1w.png
    The returned path is the pipeline directory.
    """
    pipeline = tmp_path / 'dataset' / 'pipeline'
    pipeline.mkdir(parents=True)
    (pipeline / 'sub-01_ses-01_T1w.png').write_bytes(MINIMAL_PNG)
    (pipeline / 'sub-02_ses-01_T1w.png').write_bytes(MINIMAL_PNG)
    (pipeline / 'sub-03_T1w.png').write_bytes(MINIMAL_PNG)  # no ses
    return pipeline


@pytest.fixture
def qa_json_flat(tmp_pipeline):
    """Write a flat QA.json into tmp_pipeline and return the dict."""
    data = {
        'image_001.png': {
            'filename': 'image_001.png',
            'QA_status': 'yes',
            'reason': '',
            'user': '',
            'date': '',
            'duration': 0,
        },
        'image_002.png': {
            'filename': 'image_002.png',
            'QA_status': 'no',
            'reason': 'blurry',
            'user': 'tester',
            'date': '2025-01-01 12:00:00',
            'duration': 5,
        },
    }
    path = tmp_pipeline / 'QA.json'
    path.write_text(json.dumps(data, indent=4))
    os.chmod(path, 0o770)
    return data


@pytest.fixture
def qa_json_bids(tmp_bids_pipeline):
    """Write a BIDS QA.json into tmp_bids_pipeline and return the dict."""
    data = {
        'sub-01': {
            'ses-01': {
                'QA_status': 'yes', 'reason': '', 'user': '', 'date': '',
                'sub': 'sub-01', 'ses': 'ses-01', 'acq': '', 'run': '',
            }
        },
        'sub-02': {
            'ses-01': {
                'QA_status': 'no', 'reason': 'bad', 'user': 'tester',
                'date': '2025-01-01 12:00:00',
                'sub': 'sub-02', 'ses': 'ses-01', 'acq': '', 'run': '',
            }
        },
        'sub-03': {
            'QA_status': 'yes', 'reason': '', 'user': '', 'date': '',
            'sub': 'sub-03', 'ses': '', 'acq': '', 'run': '',
        },
    }
    path = tmp_bids_pipeline / 'QA.json'
    path.write_text(json.dumps(data, indent=4))
    os.chmod(path, 0o770)
    return data


# ---------------------------------------------------------------------------
# Route-level helpers
# ---------------------------------------------------------------------------

def set_session(client, qa_directory, bids_mode=False, user_name='tester',
                qa_options=None):
    """Populate Flask session variables needed by most routes."""
    if qa_options is None:
        qa_options = ['yes', 'no', 'maybe']
    with client.session_transaction() as sess:
        sess['qa_directory'] = str(qa_directory)
        sess['bids_mode'] = bids_mode
        sess['user_name'] = user_name
        sess['qa_options'] = qa_options
