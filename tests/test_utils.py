"""
Unit tests for pure Python utility functions in app_montage.py.

These tests exercise existing, well-used functionality to catch regressions.
No Flask app or filesystem writes are required for most of these tests.
"""
import json
import os
import pytest
from pathlib import Path

from masi_qa.app_montage import (
    get_BIDS_fields_from_png,
    validate_bids_compliance,
    create_json_dict,
    create_bids_json_dict,
    detect_json_format,
    convert_flat_to_bids,
    convert_bids_to_flat,
    check_json_for_png,
    check_json_for_png_bids,
    are_unique_qa_dicts,
    get_leaf_dicts,
    save_json_file,
    convert_json_to_csv,
)


# ---------------------------------------------------------------------------
# get_BIDS_fields_from_png
# ---------------------------------------------------------------------------

class TestGetBIDSFieldsFromPNG:

    @pytest.mark.parametrize('filename,expected', [
        (
            'sub-01_ses-01_T1w.png',
            {'sub': 'sub-01', 'ses': 'ses-01', 'acq': None, 'run': None},
        ),
        (
            'sub-01_T1w.png',          # no ses
            {'sub': 'sub-01', 'ses': None, 'acq': None, 'run': None},
        ),
        (
            'sub-01_ses-01_T1wacq-fast.png',
            {'sub': 'sub-01', 'ses': 'ses-01', 'acq': 'acq-fast', 'run': None},
        ),
        (
            'sub-01_ses-01_T1wrun-01.png',
            {'sub': 'sub-01', 'ses': 'ses-01', 'acq': None, 'run': 'run-01'},
        ),
        (
            'sub-01_ses-01_T1wacq-fastrun-02.png',
            {'sub': 'sub-01', 'ses': 'ses-01', 'acq': 'acq-fast', 'run': 'run-02'},
        ),
    ])
    def test_valid_patterns(self, filename, expected):
        result = get_BIDS_fields_from_png(filename)
        assert result == expected

    def test_non_compliant_returns_none(self):
        assert get_BIDS_fields_from_png('image_001.png') is None
        assert get_BIDS_fields_from_png('noprefix_T1w.png') is None
        assert get_BIDS_fields_from_png('') is None

    def test_return_pipeline(self):
        result = get_BIDS_fields_from_png('sub-01_ses-01_T1w.png', return_pipeline=True)
        assert result['pipeline'] == 'T1w'

    def test_return_pipeline_no_ses(self):
        result = get_BIDS_fields_from_png('sub-01_T1w.png', return_pipeline=True)
        assert result['pipeline'] == 'T1w'


# ---------------------------------------------------------------------------
# validate_bids_compliance
# ---------------------------------------------------------------------------

class TestValidateBIDSCompliance:

    def test_all_compliant(self):
        files = ['sub-01_ses-01_T1w.png', 'sub-02_ses-01_T1w.png']
        compliant, non_compliant = validate_bids_compliance(files)
        assert compliant == files
        assert non_compliant == []

    def test_all_non_compliant(self):
        files = ['image_001.png', 'image_002.png']
        compliant, non_compliant = validate_bids_compliance(files)
        assert compliant == []
        assert non_compliant == files

    def test_mixed(self):
        files = ['sub-01_ses-01_T1w.png', 'image_001.png']
        compliant, non_compliant = validate_bids_compliance(files)
        assert 'sub-01_ses-01_T1w.png' in compliant
        assert 'image_001.png' in non_compliant

    def test_empty_list(self):
        compliant, non_compliant = validate_bids_compliance([])
        assert compliant == [] and non_compliant == []


# ---------------------------------------------------------------------------
# create_json_dict / create_bids_json_dict
# ---------------------------------------------------------------------------

class TestCreateJsonDict:

    def test_creates_entry_for_each_file(self):
        files = ['a.png', 'b.png', 'c.png']
        result = create_json_dict(files)
        assert set(result.keys()) == set(files)

    def test_default_status_is_first_option(self):
        result = create_json_dict(['a.png'], default_status='no')
        assert result['a.png']['QA_status'] == 'no'

    def test_entry_has_required_fields(self):
        result = create_json_dict(['a.png'])
        entry = result['a.png']
        for field in ('filename', 'QA_status', 'reason', 'user', 'date', 'duration'):
            assert field in entry

    def test_empty_list(self):
        assert create_json_dict([]) == {}


class TestCreateBidsJsonDict:

    def test_creates_nested_structure(self):
        files = ['sub-01_ses-01_T1w.png']
        result = create_bids_json_dict(files)
        assert 'sub-01' in result
        assert 'ses-01' in result['sub-01']

    def test_leaf_has_required_fields(self):
        files = ['sub-01_ses-01_T1w.png']
        result = create_bids_json_dict(files)
        leaf = result['sub-01']['ses-01']
        for field in ('QA_status', 'reason', 'user', 'date', 'sub', 'ses'):
            assert field in leaf

    def test_no_ses_creates_direct_leaf(self):
        files = ['sub-01_T1w.png']
        result = create_bids_json_dict(files)
        assert 'sub-01' in result
        assert 'QA_status' in result['sub-01']

    def test_default_status_applied(self):
        result = create_bids_json_dict(['sub-01_ses-01_T1w.png'], default_status='maybe')
        assert result['sub-01']['ses-01']['QA_status'] == 'maybe'

    def test_skips_non_compliant(self):
        result = create_bids_json_dict(['image_001.png'])
        assert result == {}


# ---------------------------------------------------------------------------
# detect_json_format
# ---------------------------------------------------------------------------

class TestDetectJsonFormat:

    def test_empty_dict_returns_empty(self):
        assert detect_json_format({}) == 'empty'

    def test_flat_format(self):
        d = {'image_001.png': {'filename': 'image_001.png', 'QA_status': 'yes'}}
        assert detect_json_format(d) == 'flat'

    def test_bids_format(self):
        d = {'sub-01': {'ses-01': {'QA_status': 'yes'}}}
        assert detect_json_format(d) == 'bids'


# ---------------------------------------------------------------------------
# convert_flat_to_bids / convert_bids_to_flat
# ---------------------------------------------------------------------------

class TestConvertFlatToBids:

    def test_converts_structure(self):
        pngs = ['sub-01_ses-01_T1w.png']
        flat = {
            'sub-01_ses-01_T1w.png': {
                'filename': 'sub-01_ses-01_T1w.png',
                'QA_status': 'no',
                'reason': 'bad',
                'user': 'u',
                'date': '2025-01-01',
                'duration': 3,
            }
        }
        result = convert_flat_to_bids(flat, pngs)
        leaf = result['sub-01']['ses-01']
        assert leaf['QA_status'] == 'no'
        assert leaf['reason'] == 'bad'

    def test_non_compliant_filenames_returns_none(self):
        pngs = ['image_001.png']
        flat = {'image_001.png': {'filename': 'image_001.png', 'QA_status': 'yes',
                                  'reason': '', 'user': '', 'date': '', 'duration': 0}}
        assert convert_flat_to_bids(flat, pngs) is None


class TestConvertBidsToFlat:

    def test_converts_structure(self):
        pngs = ['sub-01_ses-01_T1w.png']
        bids = {
            'sub-01': {
                'ses-01': {
                    'QA_status': 'no', 'reason': 'bad', 'user': 'u',
                    'date': '2025-01-01', 'sub': 'sub-01', 'ses': 'ses-01',
                    'acq': '', 'run': '',
                }
            }
        }
        result = convert_bids_to_flat(bids, pngs)
        assert 'sub-01_ses-01_T1w.png' in result
        assert result['sub-01_ses-01_T1w.png']['QA_status'] == 'no'
        assert result['sub-01_ses-01_T1w.png']['reason'] == 'bad'

    def test_adds_missing_pngs_with_default(self):
        pngs = ['sub-01_ses-01_T1w.png', 'sub-02_ses-01_T1w.png']
        result = convert_bids_to_flat({}, pngs)
        assert 'sub-02_ses-01_T1w.png' in result
        assert result['sub-02_ses-01_T1w.png']['QA_status'] == 'yes'


class TestRoundTrip:
    """Flat → BIDS → Flat preserves QA data."""

    def test_flat_to_bids_to_flat(self):
        pngs = ['sub-01_ses-01_T1w.png', 'sub-02_ses-01_T1w.png']
        flat = {
            png: {
                'filename': png, 'QA_status': 'no', 'reason': 'r',
                'user': 'u', 'date': 'd', 'duration': 1,
            }
            for png in pngs
        }
        bids = convert_flat_to_bids(flat, pngs)
        recovered = convert_bids_to_flat(bids, pngs)
        for png in pngs:
            assert recovered[png]['QA_status'] == 'no'
            assert recovered[png]['reason'] == 'r'


# ---------------------------------------------------------------------------
# check_json_for_png / check_json_for_png_bids
# ---------------------------------------------------------------------------

class TestCheckJsonForPng:

    def test_adds_missing_png(self):
        existing = {
            'a.png': {'filename': 'a.png', 'QA_status': 'yes',
                      'reason': '', 'user': '', 'date': '', 'duration': 0}
        }
        result = check_json_for_png(existing, ['a.png', 'b.png'])
        assert 'b.png' in result
        assert result['b.png']['QA_status'] == 'yes'  # default

    def test_does_not_overwrite_existing(self):
        existing = {
            'a.png': {'filename': 'a.png', 'QA_status': 'no',
                      'reason': 'bad', 'user': 'u', 'date': 'd', 'duration': 2}
        }
        result = check_json_for_png(existing, ['a.png'])
        assert result['a.png']['QA_status'] == 'no'
        assert result['a.png']['reason'] == 'bad'

    def test_custom_default_status(self):
        result = check_json_for_png({}, ['a.png'], default_status='maybe')
        assert result['a.png']['QA_status'] == 'maybe'


class TestCheckJsonForPngBids:

    def test_adds_missing_bids_png(self):
        existing = {
            'sub-01': {
                'ses-01': {
                    'QA_status': 'yes', 'reason': '', 'user': '', 'date': '',
                    'sub': 'sub-01', 'ses': 'ses-01', 'acq': '', 'run': '',
                }
            }
        }
        result = check_json_for_png_bids(existing, ['sub-01_ses-01_T1w.png', 'sub-02_ses-01_T1w.png'])
        assert 'sub-02' in result
        assert result['sub-02']['ses-01']['QA_status'] == 'yes'

    def test_does_not_overwrite_existing(self):
        existing = {
            'sub-01': {
                'ses-01': {
                    'QA_status': 'no', 'reason': 'bad', 'user': 'u', 'date': 'd',
                    'sub': 'sub-01', 'ses': 'ses-01', 'acq': '', 'run': '',
                }
            }
        }
        result = check_json_for_png_bids(existing, ['sub-01_ses-01_T1w.png'])
        assert result['sub-01']['ses-01']['QA_status'] == 'no'

    def test_skips_non_compliant(self):
        result = check_json_for_png_bids({}, ['image_001.png'])
        assert result == {}


# ---------------------------------------------------------------------------
# are_unique_qa_dicts
# ---------------------------------------------------------------------------

class TestAreUniqueQaDicts:

    def test_unique_flat(self):
        dicts = [
            {'filename': 'a.png', 'QA_status': 'yes'},
            {'filename': 'b.png', 'QA_status': 'no'},
        ]
        assert are_unique_qa_dicts(dicts) is True

    def test_duplicate_flat(self):
        dicts = [
            {'filename': 'a.png', 'QA_status': 'yes'},
            {'filename': 'a.png', 'QA_status': 'no'},
        ]
        assert are_unique_qa_dicts(dicts) is False

    def test_unique_bids(self):
        dicts = [
            {'sub': 'sub-01', 'ses': 'ses-01', 'acq': '', 'run': '', 'QA_status': 'yes'},
            {'sub': 'sub-02', 'ses': 'ses-01', 'acq': '', 'run': '', 'QA_status': 'no'},
        ]
        assert are_unique_qa_dicts(dicts) is True

    def test_duplicate_bids(self):
        dicts = [
            {'sub': 'sub-01', 'ses': 'ses-01', 'acq': '', 'run': '', 'QA_status': 'yes'},
            {'sub': 'sub-01', 'ses': 'ses-01', 'acq': '', 'run': '', 'QA_status': 'no'},
        ]
        assert are_unique_qa_dicts(dicts) is False


# ---------------------------------------------------------------------------
# save_json_file / convert_json_to_csv  (disk I/O)
# ---------------------------------------------------------------------------

class TestSaveJsonFile:

    def test_writes_valid_json(self, tmp_path):
        data = {'key': 'value', 'num': 42}
        path = tmp_path / 'test.json'
        save_json_file(path, data)
        assert path.exists()
        loaded = json.loads(path.read_text())
        assert loaded == data

    def test_creates_lock_file(self, tmp_path):
        path = tmp_path / 'test.json'
        save_json_file(path, {})
        # Lock file is created in the same directory
        assert (tmp_path / '.QA.lock').exists()

    def test_overwrites_existing(self, tmp_path):
        path = tmp_path / 'test.json'
        save_json_file(path, {'a': 1})
        save_json_file(path, {'b': 2})
        loaded = json.loads(path.read_text())
        assert loaded == {'b': 2}


class TestConvertJsonToCsv:

    def test_flat_mode_csv_columns(self, tmp_path):
        pipeline_path = tmp_path / 'pipeline'
        pipeline_path.mkdir()
        data = {
            'a.png': {'filename': 'a.png', 'QA_status': 'yes',
                      'reason': '', 'user': '', 'date': '', 'duration': 0}
        }
        df = convert_json_to_csv(data, pipeline_path, bids_mode=False)
        assert list(df.columns) == ['filename', 'QA_status', 'reason', 'user', 'date', 'duration']

    def test_flat_mode_csv_written(self, tmp_path):
        pipeline_path = tmp_path / 'pipeline'
        pipeline_path.mkdir()
        data = {
            'a.png': {'filename': 'a.png', 'QA_status': 'yes',
                      'reason': '', 'user': '', 'date': '', 'duration': 0}
        }
        convert_json_to_csv(data, pipeline_path, bids_mode=False)
        assert (pipeline_path / 'QA.csv').exists()

    def test_bids_mode_csv_columns(self, tmp_path):
        pipeline_path = tmp_path / 'pipeline'
        pipeline_path.mkdir()
        data = {
            'sub-01': {
                'ses-01': {
                    'QA_status': 'yes', 'reason': '', 'user': '', 'date': '',
                    'sub': 'sub-01', 'ses': 'ses-01', 'acq': '', 'run': '',
                }
            }
        }
        df = convert_json_to_csv(data, pipeline_path, bids_mode=True)
        for col in ('sub', 'ses', 'QA_status', 'reason', 'user', 'date'):
            assert col in df.columns
