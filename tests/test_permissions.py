"""
Tests for _get_path_info and check_write_permissions.

These cover the staged changes: enriched file_issues entries with owner/group/
mode/user_relationship fields, and the _get_path_info helper itself.

Permission scenarios are simulated with os.chmod on temp files — no root needed.
"""
import os
import stat
import json
from pathlib import Path
from unittest.mock import patch
import pytest

from masi_qa.app_montage import _get_path_info, check_write_permissions

# Fields that every file_issue dict must contain after the staged changes.
REQUIRED_ISSUE_FIELDS = {
    'name', 'full_path', 'status', 'message',
    'owner', 'group', 'mode_octal', 'mode_symbolic', 'needed',
    'user_is_owner', 'user_in_group', 'group_has_write',
}


# ---------------------------------------------------------------------------
# _get_path_info
# ---------------------------------------------------------------------------

class TestGetPathInfo:

    def test_returns_dict_for_real_file(self, tmp_path):
        f = tmp_path / 'test.txt'
        f.write_text('hello')
        info = _get_path_info(f)
        assert info is not None
        for key in ('owner', 'group', 'mode_octal', 'mode_symbolic', 'uid', 'gid', 'mode'):
            assert key in info

    def test_returns_none_for_nonexistent_path(self, tmp_path):
        assert _get_path_info(tmp_path / 'does_not_exist.txt') is None

    def test_mode_octal_format(self, tmp_path):
        f = tmp_path / 'test.txt'
        f.write_text('hello')
        os.chmod(f, 0o644)
        info = _get_path_info(f)
        # Should be numeric octal digits, no '0o' prefix
        assert info['mode_octal'].isdigit()
        assert info['mode_octal'] == '644'

    def test_mode_symbolic_format(self, tmp_path):
        f = tmp_path / 'test.txt'
        f.write_text('hello')
        info = _get_path_info(f)
        # symbolic mode is 9 characters (e.g. 'rw-r--r--')
        assert len(info['mode_symbolic']) == 9

    def test_uid_matches_current_user(self, tmp_path):
        f = tmp_path / 'test.txt'
        f.write_text('hello')
        info = _get_path_info(f)
        assert info['uid'] == os.getuid()


# ---------------------------------------------------------------------------
# check_write_permissions — happy path
# ---------------------------------------------------------------------------

class TestCheckWritePermissionsOK:

    def test_writable_dir_no_files(self, tmp_pipeline):
        """Fully writable dir with no QA files → can_write=True, no issues."""
        can_write, issues, files_missing = check_write_permissions(tmp_pipeline)
        assert can_write is True
        assert issues == []

    def test_writable_dir_with_correct_permissions(self, tmp_pipeline, qa_json_flat):
        """Existing QA.json at 0o770 → no issues."""
        # qa_json_flat fixture already sets QA.json to 0o770
        can_write, issues, _ = check_write_permissions(tmp_pipeline)
        assert can_write is True
        assert issues == []


# ---------------------------------------------------------------------------
# check_write_permissions — non-writable directory
# ---------------------------------------------------------------------------

class TestCheckWritePermissionsNonWritableDir:

    def test_detects_non_writable_directory(self, tmp_pipeline):
        os.chmod(tmp_pipeline, 0o555)
        try:
            can_write, issues, _ = check_write_permissions(tmp_pipeline)
            assert can_write is False
            assert len(issues) >= 1
            dir_issue = next(i for i in issues if i['name'] == 'Directory')
            assert dir_issue['status'] == 'not-writable'
        finally:
            os.chmod(tmp_pipeline, 0o770)

    def test_directory_issue_has_all_required_fields(self, tmp_pipeline):
        os.chmod(tmp_pipeline, 0o555)
        try:
            _, issues, _ = check_write_permissions(tmp_pipeline)
            dir_issue = next(i for i in issues if i['name'] == 'Directory')
            assert REQUIRED_ISSUE_FIELDS.issubset(dir_issue.keys())
        finally:
            os.chmod(tmp_pipeline, 0o770)

    def test_full_path_is_absolute(self, tmp_pipeline):
        os.chmod(tmp_pipeline, 0o555)
        try:
            _, issues, _ = check_write_permissions(tmp_pipeline)
            dir_issue = next(i for i in issues if i['name'] == 'Directory')
            assert Path(dir_issue['full_path']).is_absolute()
        finally:
            os.chmod(tmp_pipeline, 0o770)

    def test_user_is_owner_is_bool(self, tmp_pipeline):
        os.chmod(tmp_pipeline, 0o555)
        try:
            _, issues, _ = check_write_permissions(tmp_pipeline)
            dir_issue = next(i for i in issues if i['name'] == 'Directory')
            assert isinstance(dir_issue['user_is_owner'], bool)
            assert isinstance(dir_issue['user_in_group'], bool)
            assert isinstance(dir_issue['group_has_write'], bool)
        finally:
            os.chmod(tmp_pipeline, 0o770)

    def test_user_in_group_includes_primary_gid(self, tmp_path):
        """
        Regression test: user_in_group must be True when file GID == user's primary GID.

        This explicitly guards against the bug where only os.getgroups() is used
        (which excludes the primary GID on Linux).
        """
        path = tmp_path / 'test_dir'
        path.mkdir()
        os.chown(path, -1, os.getgid())

        info = _get_path_info(path)
        assert info is not None
        assert info['gid'] == os.getgid(), (
            'Test setup failed: directory GID does not match user\'s primary GID'
        )

        os.chmod(path, 0o555)
        try:
            _, issues, _ = check_write_permissions(path)
            dir_issue = next(i for i in issues if i['name'] == 'Directory')
            assert dir_issue['user_in_group'] is True
        finally:
            os.chmod(path, 0o770)

    def test_group_has_write_reflects_mode(self, tmp_pipeline):
        # 0o555 = r-xr-xr-x → group does NOT have write
        os.chmod(tmp_pipeline, 0o555)
        try:
            _, issues, _ = check_write_permissions(tmp_pipeline)
            dir_issue = next(i for i in issues if i['name'] == 'Directory')
            assert dir_issue['group_has_write'] is False
        finally:
            os.chmod(tmp_pipeline, 0o770)


# ---------------------------------------------------------------------------
# check_write_permissions — non-writable files
# ---------------------------------------------------------------------------

class TestCheckWritePermissionsNonWritableFiles:

    def test_detects_non_writable_qa_json(self, tmp_pipeline, qa_json_flat):
        json_path = tmp_pipeline / 'QA.json'
        os.chmod(json_path, 0o444)
        try:
            can_write, issues, _ = check_write_permissions(tmp_pipeline)
            assert can_write is False
            json_issue = next(i for i in issues if i['name'] == 'QA.json')
            assert json_issue['status'] == 'not-writable'
            assert REQUIRED_ISSUE_FIELDS.issubset(json_issue.keys())
        finally:
            os.chmod(json_path, 0o770)

    def test_detects_non_writable_qa_csv(self, tmp_pipeline, tmp_path):
        # Create a QA.csv at 0o444 (read-only)
        csv_path = tmp_pipeline / 'QA.csv'
        csv_path.write_text('filename,QA_status\n')
        os.chmod(csv_path, 0o444)
        try:
            can_write, issues, _ = check_write_permissions(tmp_pipeline)
            assert can_write is False
            csv_issue = next(i for i in issues if i['name'] == 'QA.csv')
            assert csv_issue['status'] == 'not-writable'
            assert REQUIRED_ISSUE_FIELDS.issubset(csv_issue.keys())
        finally:
            os.chmod(csv_path, 0o770)

    def test_full_path_points_to_file(self, tmp_pipeline, qa_json_flat):
        json_path = tmp_pipeline / 'QA.json'
        os.chmod(json_path, 0o444)
        try:
            _, issues, _ = check_write_permissions(tmp_pipeline)
            json_issue = next(i for i in issues if i['name'] == 'QA.json')
            assert json_issue['full_path'] == str(json_path)
        finally:
            os.chmod(json_path, 0o770)


# ---------------------------------------------------------------------------
# check_write_permissions — wrong permissions (0o770 enforcement)
# ---------------------------------------------------------------------------

class TestCheckWritePermissionsWrongMode:

    def test_owner_auto_fixes_wrong_permissions(self, tmp_pipeline, qa_json_flat):
        """If current user owns the file and perms are wrong, fix silently — no issue reported."""
        json_path = tmp_pipeline / 'QA.json'
        os.chmod(json_path, 0o600)  # wrong mode, but we own it
        can_write, issues, _ = check_write_permissions(tmp_pipeline)
        # File should have been silently fixed
        assert can_write is True
        assert not any(i['name'] == 'QA.json' and i['status'] == 'wrong-permissions'
                       for i in issues)
        # Permissions should now be 0o770
        assert stat.S_IMODE(os.stat(json_path).st_mode) == 0o770

    def test_non_owner_wrong_permissions_reports_issue(self, tmp_pipeline, qa_json_flat):
        """
        If another user owns a file with wrong permissions, it's reported.
        We simulate this by mocking os.stat to return a different uid.
        """
        json_path = tmp_pipeline / 'QA.json'
        os.chmod(json_path, 0o600)  # wrong mode

        real_stat = os.stat(json_path)

        class FakeStatResult:
            st_uid = real_stat.st_uid + 9999  # different owner
            st_gid = real_stat.st_gid
            st_mode = real_stat.st_mode

        with patch('os.stat', return_value=FakeStatResult()):
            can_write, issues, _ = check_write_permissions(tmp_pipeline)

        wrong_perm_issues = [i for i in issues if i['status'] == 'wrong-permissions']
        assert len(wrong_perm_issues) >= 1
        issue = wrong_perm_issues[0]
        assert REQUIRED_ISSUE_FIELDS.issubset(issue.keys())
        assert issue['user_is_owner'] is False
