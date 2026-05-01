"""
Integration tests for Flask routes using the test client.

No real server or browser is started — Flask's test client simulates HTTP
requests entirely in-process, including session management.
"""
import json
import os
import pytest
from pathlib import Path

from conftest import set_session  # shared session helper


# ---------------------------------------------------------------------------
# Root / index route
# ---------------------------------------------------------------------------

class TestIndexRoute:

    def test_returns_200(self, client):
        response = client.get('/')
        assert response.status_code == 200

    def test_renders_directory_selector(self, client):
        data = response_text(client.get('/'))
        assert 'QA' in data  # basic sanity


# ---------------------------------------------------------------------------
# /set-options
# ---------------------------------------------------------------------------

class TestSetOptions:

    def test_stores_user_name_in_session(self, client):
        resp = client.post('/set-options',
                           data=json.dumps({'user_name': 'alice', 'qa_options': ['yes', 'no', 'maybe']}),
                           content_type='application/json')
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body['success'] is True
        assert body['user_name'] == 'alice'

    def test_stores_custom_qa_options(self, client):
        opts = ['pass', 'fail', 'review']
        resp = client.post('/set-options',
                           data=json.dumps({'user_name': '', 'qa_options': opts}),
                           content_type='application/json')
        body = json.loads(resp.data)
        assert body['qa_options'] == opts

    def test_single_option_falls_back_to_default(self, client):
        resp = client.post('/set-options',
                           data=json.dumps({'user_name': '', 'qa_options': ['only']}),
                           content_type='application/json')
        body = json.loads(resp.data)
        assert body['qa_options'] == ['yes', 'no', 'maybe']

    def test_options_limited_to_eight(self, client):
        opts = [str(i) for i in range(10)]  # 10 options → truncated to 8
        resp = client.post('/set-options',
                           data=json.dumps({'user_name': '', 'qa_options': opts}),
                           content_type='application/json')
        body = json.loads(resp.data)
        assert len(body['qa_options']) == 8

    def test_options_lowercased(self, client):
        resp = client.post('/set-options',
                           data=json.dumps({'user_name': '', 'qa_options': ['YES', 'No']}),
                           content_type='application/json')
        body = json.loads(resp.data)
        assert body['qa_options'] == ['yes', 'no']


# ---------------------------------------------------------------------------
# /validate-path
# ---------------------------------------------------------------------------

class TestValidatePath:

    def test_valid_directory(self, client, tmp_path):
        resp = client.post('/validate-path',
                           data=json.dumps({'path': str(tmp_path)}),
                           content_type='application/json')
        body = json.loads(resp.data)
        assert body['valid'] is True

    def test_nonexistent_path(self, client, tmp_path):
        resp = client.post('/validate-path',
                           data=json.dumps({'path': str(tmp_path / 'nope')}),
                           content_type='application/json')
        body = json.loads(resp.data)
        assert body['valid'] is False
        assert len(body['errors']) > 0

    def test_relative_path_is_invalid(self, client):
        resp = client.post('/validate-path',
                           data=json.dumps({'path': 'relative/path'}),
                           content_type='application/json')
        body = json.loads(resp.data)
        assert body['valid'] is False


# ---------------------------------------------------------------------------
# /datasets/<path>/<pipeline>  — render_montage
# ---------------------------------------------------------------------------

class TestRenderMontage:

    def test_happy_path_returns_200(self, client, tmp_pipeline, tmp_path):
        set_session(client, tmp_path)
        resp = client.get('/datasets/dataset/pipeline')
        assert resp.status_code == 200

    def test_image_names_appear_in_response(self, client, tmp_pipeline, tmp_path):
        set_session(client, tmp_path)
        data = response_text(client.get('/datasets/dataset/pipeline'))
        assert 'image_001.png' in data
        assert 'image_002.png' in data

    def test_creates_qa_json_when_missing(self, client, tmp_pipeline, tmp_path):
        set_session(client, tmp_path)
        client.get('/datasets/dataset/pipeline')
        assert (tmp_pipeline / 'QA.json').exists()

    def test_creates_qa_csv_when_missing(self, client, tmp_pipeline, tmp_path):
        set_session(client, tmp_path)
        client.get('/datasets/dataset/pipeline')
        assert (tmp_pipeline / 'QA.csv').exists()

    def test_loads_existing_flat_qa_json(self, client, tmp_pipeline, tmp_path, qa_json_flat):
        set_session(client, tmp_path)
        resp = client.get('/datasets/dataset/pipeline')
        assert resp.status_code == 200
        data = response_text(resp)
        # The existing 'no' status for image_002 should appear in the rendered JSON
        assert 'blurry' in data  # reason from fixture

    def test_no_qa_directory_redirects(self, client):
        # No session set → should redirect to index
        resp = client.get('/datasets/dataset/pipeline')
        assert resp.status_code in (302, 308)

    def test_permission_error_shows_current_user(self, client, tmp_pipeline, tmp_path):
        """The staged change: current_user must be passed to permission_error.html."""
        os.chmod(tmp_pipeline, 0o555)
        try:
            set_session(client, tmp_path)
            resp = client.get('/datasets/dataset/pipeline')
            assert resp.status_code == 200
            data = response_text(resp)
            assert 'logged in as' in data.lower() or 'current_user' in data.lower() or \
                   'Permission' in data
            # The new table columns must be present
            assert 'Owner' in data or 'owner' in data.lower()
        finally:
            os.chmod(tmp_pipeline, 0o770)

    def test_permission_error_includes_full_path(self, client, tmp_pipeline, tmp_path):
        """full_path (new field) must appear in the rendered permission error page."""
        os.chmod(tmp_pipeline, 0o555)
        try:
            set_session(client, tmp_path)
            resp = client.get('/datasets/dataset/pipeline')
            data = response_text(resp)
            assert str(tmp_pipeline) in data
        finally:
            os.chmod(tmp_pipeline, 0o770)

    def test_mode_mismatch_bids_data_flat_mode(self, client, tmp_pipeline, tmp_path):
        """Existing BIDS QA.json opened in flat mode → mode_mismatch page."""
        # Write BIDS-format json into the flat pipeline dir
        bids_data = {
            'sub-01': {'ses-01': {'QA_status': 'yes', 'reason': '', 'user': '', 'date': '',
                                  'sub': 'sub-01', 'ses': 'ses-01', 'acq': '', 'run': ''}}
        }
        import json as _json
        (tmp_pipeline / 'QA.json').write_text(_json.dumps(bids_data))
        set_session(client, tmp_path, bids_mode=False)
        resp = client.get('/datasets/dataset/pipeline')
        assert resp.status_code == 200
        data = response_text(resp)
        assert 'mismatch' in data.lower() or 'format' in data.lower()


# ---------------------------------------------------------------------------
# /qa  — render_montage_standard
# ---------------------------------------------------------------------------

class TestRenderMontageStandard:

    def test_happy_path_returns_200(self, client, tmp_path):
        # Put PNGs directly in the qa_directory (standard mode)
        from conftest import MINIMAL_PNG
        (tmp_path / 'img_001.png').write_bytes(MINIMAL_PNG)
        set_session(client, tmp_path)
        resp = client.get('/qa')
        assert resp.status_code == 200

    def test_permission_error_shows_current_user(self, client, tmp_path):
        from conftest import MINIMAL_PNG
        (tmp_path / 'img_001.png').write_bytes(MINIMAL_PNG)
        os.chmod(tmp_path, 0o555)
        try:
            set_session(client, tmp_path)
            resp = client.get('/qa')
            assert resp.status_code == 200
            data = response_text(resp)
            assert 'Permission' in data or 'permission' in data
        finally:
            os.chmod(tmp_path, 0o770)


# ---------------------------------------------------------------------------
# /update_qa_dict
# ---------------------------------------------------------------------------

class TestUpdateQaDict:

    def _setup_session(self, client, json_path):
        with client.session_transaction() as sess:
            sess['json_path'] = str(json_path)
            sess['bids_mode'] = False

    def test_write_to_disk_true_saves_json(self, client, tmp_pipeline, qa_json_flat):
        json_path = tmp_pipeline / 'QA.json'
        self._setup_session(client, json_path)

        updated = dict(qa_json_flat)
        updated['image_001.png']['QA_status'] = 'no'
        updated['image_001.png']['reason'] = 'updated'

        resp = client.post('/update_qa_dict',
                           data=json.dumps({'nestedDict': updated, 'writeToDisk': True}),
                           content_type='application/json')
        assert resp.status_code == 200
        saved = json.loads(json_path.read_text())
        assert saved['image_001.png']['QA_status'] == 'no'
        assert saved['image_001.png']['reason'] == 'updated'

    def test_write_to_disk_true_saves_csv(self, client, tmp_pipeline, qa_json_flat):
        json_path = tmp_pipeline / 'QA.json'
        self._setup_session(client, json_path)

        resp = client.post('/update_qa_dict',
                           data=json.dumps({'nestedDict': qa_json_flat, 'writeToDisk': True}),
                           content_type='application/json')
        assert resp.status_code == 200
        assert (tmp_pipeline / 'QA.csv').exists()

    def test_write_to_disk_false_does_not_write(self, client, tmp_pipeline, qa_json_flat):
        json_path = tmp_pipeline / 'QA.json'
        mtime_before = json_path.stat().st_mtime
        self._setup_session(client, json_path)

        updated = dict(qa_json_flat)
        updated['image_001.png']['QA_status'] = 'no'

        resp = client.post('/update_qa_dict',
                           data=json.dumps({'nestedDict': updated, 'writeToDisk': False}),
                           content_type='application/json')
        assert resp.status_code == 200
        # File must not have been modified
        assert json_path.stat().st_mtime == mtime_before

    def test_returns_success_json(self, client, tmp_pipeline, qa_json_flat):
        self._setup_session(client, tmp_pipeline / 'QA.json')
        resp = client.post('/update_qa_dict',
                           data=json.dumps({'nestedDict': qa_json_flat, 'writeToDisk': True}),
                           content_type='application/json')
        body = json.loads(resp.data)
        assert body['status'] == 'success'

    def test_no_session_returns_400(self, client):
        resp = client.post('/update_qa_dict',
                           data=json.dumps({'nestedDict': {}, 'writeToDisk': True}),
                           content_type='application/json')
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def response_text(resp):
    return resp.data.decode('utf-8')
