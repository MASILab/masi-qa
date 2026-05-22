"""
Authors: Michael Kim (michael.kim@vanderbilt.edu)
         Yihao Liu (yihao.liu@vanderbilt.edu)

MASI Lab @ Vanderbilt University
License: MIT
"""

from flask import Flask, request, render_template, redirect, url_for, flash, jsonify, send_file, session
import pandas as pd
import os, json, io, argparse, re, grp, pwd, logging, socket, shutil, fcntl, stat
from functools import wraps
from pathlib import Path
from datetime import datetime
from tqdm import tqdm
from importlib.metadata import version, PackageNotFoundError
import itertools

try:
    __version__ = version("masi-qa")
except PackageNotFoundError:
    __version__ = "unknown"

def find_available_port(start_port=5000, max_attempts=10):
    """Find an available port starting from start_port."""
    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('0.0.0.0', port))
                return port
        except OSError:
            continue
    return None

def pa():
    parser = argparse.ArgumentParser(description="""

    Given the path to the QA directory, will set up a montage of images to QA.
                                     
    Depending on the image quality, the user can update the QA status and add a reason for the update.
                                
    The updated CSV will be saved to the specified save path as updates are made.

""")
    parser.add_argument('--debug', action='store_true', help='enable debug mode')
    parser.add_argument('--port', type=int, default=None,
                        help='port to run the server on (default: auto-detect 5000-5009)')

    return parser.parse_args()

app = Flask(__name__)
app.secret_key = 'supersecretkey'

# Defaults are safe for import (e.g., when installed as a package).
args = argparse.Namespace(debug=False)

# Global BIDS mode flag - set at startup based on which entry point was used
# This determines whether the app runs in Standard mode (masi-qa) or BIDS mode (masi-bids-qa)
BIDS_MODE = False

def get_qa_directory():
    """Get QA directory from session, falling back to command-line arg."""
    if 'qa_directory' in session:
        return session['qa_directory']
    return None

def validate_directory(path):
    """Validate that a path is an absolute, existing, accessible directory."""
    errors = []
    if not path:
        errors.append("Path cannot be empty")
        return False, errors
    if not os.path.isabs(path):
        errors.append("Path must be an absolute path (starting with /)")
    if not os.path.exists(path):
        errors.append("Path does not exist")
    elif not os.path.isdir(path):
        errors.append("Path is not a directory")
    elif not os.access(path, os.R_OK):
        errors.append("Path is not readable")
    return len(errors) == 0, errors


def _is_valid_browse_path(path):
    """Check if a path is valid for browsing (exists, is directory, is readable)."""
    if not path:
        return False
    try:
        return os.path.isdir(path) and os.access(path, os.R_OK)
    except (OSError, TypeError):
        return False


def _get_initial_browse_path(session_path):
    """
    Get the initial browse path with fallback chain:
    1. Previously selected path (from session) - if still valid
    2. Current working directory
    3. Home directory

    Returns:
        (path, from_session): Tuple of the path and whether it came from a valid session
    """
    # Try session path first
    if _is_valid_browse_path(session_path):
        return session_path, True

    # Try current working directory
    try:
        cwd = os.getcwd()
        if _is_valid_browse_path(cwd):
            return cwd, False
    except OSError:
        pass

    # Fall back to home directory
    home = os.path.expanduser('~')
    if _is_valid_browse_path(home):
        return home, False

    # Last resort: root directory
    return '/', False


def _get_path_info(path):
    """Return owner, group, and permission info for a path. Returns None if stat fails."""
    try:
        st = os.stat(path)
        mode = stat.S_IMODE(st.st_mode)
        try:
            owner = pwd.getpwuid(st.st_uid).pw_name
        except KeyError:
            owner = str(st.st_uid)
        try:
            group = grp.getgrgid(st.st_gid).gr_name
        except KeyError:
            group = str(st.st_gid)
        symbolic = stat.filemode(st.st_mode)[1:]  # strip leading type char (d/-)
        return {
            'owner': owner,
            'group': group,
            'mode_octal': oct(mode)[2:],   # e.g. '750'
            'mode_symbolic': symbolic,      # e.g. 'rwxr-x---'
            'uid': st.st_uid,
            'gid': st.st_gid,
            'mode': mode,                  # raw int for bit-checking
        }
    except OSError:
        return None


def check_write_permissions(pipeline_path):
    """
    Check if QA files can be written/created in the pipeline directory.

    Returns:
        tuple: (can_write: bool, file_issues: list, files_missing: bool)
        - can_write: True if all necessary write permissions are available
        - file_issues: List of dicts with 'name', 'status', 'message' for each issue
        - files_missing: True if files need to be created (vs modified)
    """
    json_path = pipeline_path / 'QA.json'
    csv_path = pipeline_path / 'QA.csv'

    file_issues = []
    files_missing = False

    current_uid = os.getuid()
    current_gids = set(os.getgroups()) | {os.getgid()}

    # Check if files exist
    json_exists = json_path.exists()
    csv_exists = csv_path.exists()

    # Always check directory write permission (needed for .QA.lock file during writes)
    if not os.access(pipeline_path, os.W_OK):
        info = _get_path_info(pipeline_path)
        file_issues.append({
            'name': 'Directory',
            'full_path': str(pipeline_path),
            'status': 'not-writable',
            'message': 'cannot create lock file for safe writes',
            'owner': info['owner'] if info else '?',
            'group': info['group'] if info else '?',
            'mode_octal': info['mode_octal'] if info else '?',
            'mode_symbolic': info['mode_symbolic'] if info else '?',
            'needed': 'write permission (u+w or g+w)',
            'user_is_owner': info['uid'] == current_uid if info else False,
            'user_in_group': info['gid'] in current_gids if info else False,
            'group_has_write': bool(info['mode'] & 0o020) if info else False,
        })
        files_missing = not json_exists or not csv_exists

    # Check existing files for write permission
    if json_exists and not os.access(json_path, os.W_OK):
        info = _get_path_info(json_path)
        file_issues.append({
            'name': 'QA.json',
            'full_path': str(json_path),
            'status': 'not-writable',
            'message': 'exists but not writable',
            'owner': info['owner'] if info else '?',
            'group': info['group'] if info else '?',
            'mode_octal': info['mode_octal'] if info else '?',
            'mode_symbolic': info['mode_symbolic'] if info else '?',
            'needed': 'write permission (u+w or g+w)',
            'user_is_owner': info['uid'] == current_uid if info else False,
            'user_in_group': info['gid'] in current_gids if info else False,
            'group_has_write': bool(info['mode'] & 0o020) if info else False,
        })

    if csv_exists and not os.access(csv_path, os.W_OK):
        info = _get_path_info(csv_path)
        file_issues.append({
            'name': 'QA.csv',
            'full_path': str(csv_path),
            'status': 'not-writable',
            'message': 'exists but not writable',
            'owner': info['owner'] if info else '?',
            'group': info['group'] if info else '?',
            'mode_octal': info['mode_octal'] if info else '?',
            'mode_symbolic': info['mode_symbolic'] if info else '?',
            'needed': 'write permission (u+w or g+w)',
            'user_is_owner': info['uid'] == current_uid if info else False,
            'user_in_group': info['gid'] in current_gids if info else False,
            'group_has_write': bool(info['mode'] & 0o020) if info else False,
        })

    # Check existing files have correct permissions (0o770) for multi-user access
    # If permissions are wrong and we're the owner, fix them silently
    # If permissions are wrong and we're not the owner, add to file_issues
    expected_mode = 0o770

    for file_path, file_name, exists in [
        (json_path, 'QA.json', json_exists),
        (csv_path, 'QA.csv', csv_exists)
    ]:
        if exists and os.access(file_path, os.W_OK):
            try:
                stat_info = os.stat(file_path)
                current_mode = stat.S_IMODE(stat_info.st_mode)
                if current_mode != expected_mode:
                    if stat_info.st_uid == current_uid:
                        # We're the owner, fix permissions silently
                        try:
                            os.chmod(file_path, expected_mode)
                        except OSError:
                            pass  # Best effort, continue anyway
                    else:
                        # Not the owner, can't fix - report issue
                        info = _get_path_info(file_path)
                        file_issues.append({
                            'name': file_name,
                            'full_path': str(file_path),
                            'status': 'wrong-permissions',
                            'message': f'has mode {oct(current_mode)}, needs {oct(expected_mode)} (ask owner to fix)',
                            'owner': info['owner'] if info else '?',
                            'group': info['group'] if info else '?',
                            'mode_octal': info['mode_octal'] if info else '?',
                            'mode_symbolic': info['mode_symbolic'] if info else '?',
                            'needed': 'rwxrwx--- (770)',
                            'user_is_owner': False,  # auto-fixed if owned; only reported when not owner
                            'user_in_group': info['gid'] in current_gids if info else False,
                            'group_has_write': bool(info['mode'] & 0o020) if info else False,
                        })
            except OSError:
                pass  # Can't stat file, skip permission check

    can_write = len(file_issues) == 0
    return can_write, file_issues, files_missing

def require_qa_directory(f):
    """Decorator to ensure QA directory is set before accessing route."""
    @wraps(f)
    def decorated_function(*args_inner, **kwargs):
        qa_dir = get_qa_directory()
        if not qa_dir:
            return redirect(url_for('select_root'))
        valid, errors = validate_directory(qa_dir)
        if not valid:
            session.pop('qa_directory', None)
            flash(f"Directory is no longer valid: {'; '.join(errors)}", 'error')
            return redirect(url_for('select_root'))
        return f(*args_inner, **kwargs)
    return decorated_function


def get_BIDS_fields_from_png(filename, return_pipeline=False):
    """
    Given a QA png filename, return the BIDS fields.
    Returns None if filename doesn't match BIDS pattern (instead of raising error).
    """
    pattern = r'(sub-\w+)(?:_(ses-\w+))?_([A-Za-z0-9\.\-]+?)(?=acq\-|run\-|\.png)(?:(acq-\w+))?(?:(run-\d{1,4}))?\.png'
    match = re.match(pattern, filename)
    if not match:
        return None  # Return None instead of asserting
    tags = {'sub': match.group(1), 'ses': match.group(2), 'acq': match.group(4), 'run': match.group(5)}
    if return_pipeline:
        tags['pipeline'] = match.group(3)
    return tags


def validate_bids_compliance(pngs):
    """
    Validate a list of PNG filenames for BIDS compliance.
    Returns tuple: (compliant_files, non_compliant_files)
    """
    compliant = []
    non_compliant = []
    for png in pngs:
        if get_BIDS_fields_from_png(png) is not None:
            compliant.append(png)
        else:
            non_compliant.append(png)
    return compliant, non_compliant

def create_json_dict(filepaths, default_status='yes'):
    """
    Given a list of filenames, create the flat json dictionary (non-BIDS mode).
    """
    nested_d = {}
    for png in tqdm(filepaths):
        row = {'filename': str(png), 'QA_status': default_status, 'reason': '', 'user': '', 'date': '', 'duration': 0}
        nested_d[png] = row
    return nested_d


def create_bids_json_dict(filepaths, default_status='yes'):
    """
    Given a list of BIDS-compliant filenames, create the nested BIDS json dictionary.
    Structure: sub -> ses -> acq -> run -> leaf dict
    """
    nested_d = {}
    for png in tqdm(filepaths):
        current_d = nested_d
        tags = get_BIDS_fields_from_png(png)
        if tags is None:
            continue  # Skip non-compliant files (shouldn't happen if validated first)
        sub, ses, acq, run = tags['sub'], tags['ses'], tags['acq'], tags['run']
        for tag in [sub, ses, acq, run]:
            if tag:
                current_d = current_d.setdefault(tag, {})
        # Set the default values
        row = {
            'QA_status': default_status,
            'reason': '',
            'user': '',
            'date': '',
            'sub': sub,
            'ses': ses if ses else '',
            'acq': acq if acq else '',
            'run': run if run else ''
        }
        current_d.update(row)
    return nested_d

def get_tag_type(d):
    """
    Returns the type of BIDS tag for sub, ses, acq, run. Throws an error if the tag is not one of these.
    """
    tag_types = {
        'sub': 'sub',
        'ses': 'ses',
        'acq': 'acq',
        'run': 'run'
    }
    for key, value in tag_types.items():
        if d.startswith(key):
            return value
    assert False, f"Unknown tag type: {d}"

def get_leaf_dicts(d, path=None, curr_dict=None):
    """
    Given a json dictionary, return a list of the leaf dictionaries.
    Handles both flat structure (filename -> entry) and nested BIDS structure.
    """
    if path is None:
        path = []
    if curr_dict is None:
        curr_dict = {}
    leaf_dicts = []

    # Check if this is a flat structure (values have 'QA_status' key)
    first_value = next(iter(d.values()), None)
    if isinstance(first_value, dict) and 'QA_status' in first_value:
        # Flat structure: each value is already a leaf dict
        for key, value in d.items():
            leaf_dicts.append(([key], value))
        return leaf_dicts

    # Original nested BIDS structure handling
    nested_keys = {}
    leaf_keys = {}
    for key, value in d.items():
        #print(key)
        if isinstance(value, dict):
            nested_keys[key] = (value,d)
        else:
            leaf_keys[key] = (value,d)

    for nested_key, (nested_value, nested_dict) in nested_keys.items():
        new_path = path + [nested_key]
        curr_dict[get_tag_type(nested_key)] = nested_key

        leaf_dicts.extend(get_leaf_dicts(nested_value, new_path, curr_dict))

    for leaf_key, (leaf_value, leaf_dict) in leaf_keys.items():
        leaf_dicts.append((path, leaf_dict))
        break

    return leaf_dicts

def set_file_permissions(file_path, group_name=None, file_permissions=0o770):
    """
    Set file permissions to be group-writable so multiple users can access QA files.

    Args:
        file_path: Path to the file
        group_name: Optional group name to set (e.g., 'p_masi'). If None or group
                    doesn't exist, only file permissions are changed.
        file_permissions: File permission mode (default 0o770 = rwxrwx---)

    This function is designed to be robust - it will set permissions if possible
    but won't crash if group operations fail (e.g., group doesn't exist or user
    lacks permission to change group).
    """
    try:
        # Set file permissions to group-writable
        os.chmod(file_path, file_permissions)
    except OSError as e:
        # Permission setting failed, but don't crash - file was still written
        print(f"Warning: Could not set permissions on {file_path}: {e}")

    # Optionally try to set group ownership
    if group_name:
        try:
            gid = grp.getgrnam(group_name).gr_gid
            os.chown(file_path, -1, gid)
        except KeyError:
            # Group doesn't exist on this system - that's okay
            pass
        except OSError as e:
            # User can't change group ownership - that's okay
            print(f"Warning: Could not set group on {file_path}: {e}")

def convert_json_to_csv(json_dict, pipeline_path, bids_mode=False):
    """
    Given a QA JSON dictionary, convert it to a CSV file.
    Handles both flat (non-BIDS) and nested (BIDS) structures.
    Uses file locking to prevent corruption from concurrent writes.
    """
    lock_path = Path(pipeline_path) / '.QA.lock'

    if bids_mode:
        # BIDS mode: nested structure
        leaf_dicts = get_leaf_dicts(json_dict)

        # Make sure that the paths are unique and the dictionary has all the information
        for paths, ds in leaf_dicts:
            for path in paths:
                ds[path[:3]] = path
            if 'run' not in ds:
                ds['run'] = ''
            if 'acq' not in ds:
                ds['acq'] = ''
            if 'ses' not in ds:
                ds['ses'] = ''

        # Get a list of only the leaf dictionaries
        leaf_dicts = [ds for paths, ds in leaf_dicts]

        # BIDS CSV header
        header = ['sub', 'ses', 'acq', 'run', 'QA_status', 'reason', 'user', 'date']
        df = pd.DataFrame(leaf_dicts)
        df = df[header]
        df = df.fillna('')
        df_sorted = df.sort_values(by=['sub', 'ses', 'acq', 'run'])

        with open(lock_path, 'w') as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                csv_path = pipeline_path / 'QA.csv'
                df_sorted.to_csv(csv_path, index=False)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)

        return df_sorted
    else:
        # Non-BIDS mode: flat structure
        header = ['filename', 'QA_status', 'reason', 'user', 'date', 'duration']
        df = pd.DataFrame(json_dict).T
        # Ensure all columns exist
        for col in header:
            if col not in df.columns:
                df[col] = ''
        df = df[header]
        df = df.fillna('')

        with open(lock_path, 'w') as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                csv_path = pipeline_path / 'QA.csv'
                df.to_csv(csv_path, index=False)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)

        return df

def read_csv_to_json(df):
    """
    Given a QA CSV dataframe, convert it to a QA JSON dictionary
    """

    json_data = {}

    for index, row in df.iterrows():
        #sub, ses, acq, run = row['sub'], row['ses'], row['acq'], row['run']
        qa_status, reason, user, date = row['QA_status'], row['reason'], row['user'], row['date']
        current_d = json_data
        has_d = {}
        for tag in ['sub', 'ses', 'acq', 'run']:
            if row[tag]:
                current_d = current_d.setdefault(row[tag], {})
                has_d[tag] = row[tag]
        #set the values
        add_row = {'QA_status': qa_status, 'reason': reason, 'user': user, 'date': date}
        if 'run' not in has_d:
            add_row.update({'run': ''})
        if 'acq' not in has_d:
            add_row.update({'acq': ''})
        if 'ses' not in has_d:
            add_row.update({'ses': ''})
        add_row.update(has_d)
        current_d.update(add_row)
        current_d = json_data
    
    #print(json.dumps(json_data, indent=4))

    return json_data

def compare_dicts(d1, d2):
    """
    Compare two dictionaries
    """
    
    #assert len(d1) == len(d2), "Dictionaries have different lengths"
    for key in d1:
        #print(key)
        #print(d1)
        #print(d2)
        assert key in d2, f"Key {key} not in d2. d1: {d1} \n d2: {d2}"
        if isinstance(d1[key], dict):
            compare_dicts(d1[key], d2[key])
        else:
            assert d1[key] == d2[key], f"Values for key {key} are different: {d1[key]} vs {d2[key]}"

def are_unique_qa_dicts(dict_list):
    """
    Given a list of qa dictionaries, check that no two dictionaries are the same.
    Works with both BIDS mode (sub, ses, acq, run) and non-BIDS mode (filename).
    """
    def add_items(curr_set, elt):
        curr_set.add(elt)
        return len(curr_set)

    seen = set()
    for d in tqdm(dict_list):
        # Check if this is BIDS mode (has 'sub' field) or non-BIDS mode (has 'filename' field)
        if 'filename' in d:
            # Non-BIDS mode
            key = d['filename']
        else:
            # BIDS mode - use (sub, ses, acq, run) tuple
            sub, ses, acq, run = d.get('sub', ''), d.get('ses', ''), d.get('acq', ''), d.get('run', '')
            key = (sub, ses, acq, run)
        if len(seen) == add_items(seen, key):
            return False
    return True

def assert_tags_in_dict(paths, leaf_dicts):
    """
    For given lists of paths and leaf dictionaries, assert that the paths are in the dictionaries
    """
    for paths,ds in zip(paths, leaf_dicts):
        for path in paths:
            assert path in ds.values(), f"Path {path} not in dict {ds}"

def check_png_for_json(dicts, pngs):
    """
    Given a list of QA json leaf dictionaries and list of pngs, make sure that every single json entry has a corresponding png file.
    Works with both BIDS mode and non-BIDS mode.
    """
    for dic in dicts:
        if 'filename' in dic:
            # Non-BIDS mode
            png = dic['filename']
            assert png in pngs, f"PNG {png} from {dic} not in list of pngs"
        else:
            # BIDS mode - need to reconstruct filename from BIDS tags
            # Get pipeline from one of the pngs
            if pngs:
                sample_tags = get_BIDS_fields_from_png(pngs[0], return_pipeline=True)
                if sample_tags:
                    pipeline = sample_tags.get('pipeline', '')
                    sub, ses, acq, run = dic.get('sub', ''), dic.get('ses', ''), dic.get('acq', ''), dic.get('run', '')
                    png = f'{sub}_'
                    if ses:
                        png += f"{ses}_"
                    png += f"{pipeline}"
                    if acq:
                        png += f"{acq}"
                    if run:
                        png += f"{run}"
                    png += ".png"
                    assert png in pngs, f"PNG {png} from {dic} not in list of pngs"

def check_json_for_png(nested, pngs, default_status='yes'):
    """
    Given a flat json and list of pngs, make sure that every single png file has a corresponding json entry.
    If it does not, then add the default values to the json file.
    (Non-BIDS mode)
    """
    keys = nested.keys()
    for png in pngs:
        if png not in keys:
            row = {'filename': str(png), 'QA_status': default_status, 'reason': '', 'user': '', 'date': '', 'duration': 0}
            nested[png] = row
    return nested


def check_json_for_png_bids(nested, pngs, default_status='yes'):
    """
    Given a nested BIDS json and list of pngs, make sure that every single png file has a corresponding json entry.
    If it does not, then add the default values to the json file.
    (BIDS mode)
    """
    for png in pngs:
        tags = get_BIDS_fields_from_png(png)
        if tags is None:
            continue  # Skip non-compliant files
        sub, ses, acq, run = tags['sub'], tags['ses'], tags['acq'], tags['run']
        current_d = nested
        for tag in [sub, ses, acq, run]:
            if tag:
                try:
                    current_d = current_d[tag]
                except KeyError:
                    print(f"PNG {png} has no corresponding json entry. Adding to json file.")
                    current_d = current_d.setdefault(tag, {})
        # If current_d is empty (new entry), add the default values
        if not current_d or 'QA_status' not in current_d:
            row = {
                'QA_status': default_status,
                'reason': '',
                'user': '',
                'date': '',
                'sub': sub,
                'ses': ses if ses else '',
                'acq': acq if acq else '',
                'run': run if run else ''
            }
            current_d.update(row)
    return nested

def assert_valid_qa_status(dict_list, valid_statuses=None):
    """
    Given a list of QA dictionaries, assert that the QA status is valid for all.
    valid_statuses defaults to ['yes', 'no', 'maybe'] if not provided.
    """
    if valid_statuses is None:
        valid_statuses = ['yes', 'no', 'maybe']

    for d in dict_list:
        assert d['QA_status'] in valid_statuses, f"QA status {d['QA_status']} is not valid for dictionary {d}"


def check_qa_status_mismatch(dict_list, valid_statuses):
    """
    Check if any existing QA_status values are not in the configured options.
    Returns a list of unrecognized status values, or empty list if all valid.
    """
    unrecognized = set()
    for d in dict_list:
        status = d.get('QA_status', '')
        if status and status not in valid_statuses:
            unrecognized.add(status)
    return sorted(unrecognized)


def detect_json_format(json_dict):
    """
    Detect the format of a QA JSON dictionary.
    Returns: 'bids' if nested BIDS structure, 'flat' if flat filename-keyed structure, 'empty' if no entries.
    """
    if not json_dict:
        return 'empty'

    # Check the first key to determine format
    first_key = next(iter(json_dict.keys()))
    first_value = json_dict[first_key]

    # Flat format: keys are filenames (end with .png), values have 'filename' field
    if first_key.endswith('.png') and isinstance(first_value, dict) and 'filename' in first_value:
        return 'flat'

    # BIDS format: keys start with 'sub-', values are nested dicts
    if first_key.startswith('sub-') and isinstance(first_value, dict):
        return 'bids'

    # Edge case: check if it's a flat dict without .png extension (shouldn't happen but handle gracefully)
    if isinstance(first_value, dict) and 'QA_status' in first_value and 'filename' in first_value:
        return 'flat'

    # Default to BIDS if keys look like BIDS tags
    if first_key.startswith('sub-'):
        return 'bids'

    return 'unknown'


def convert_bids_to_flat(bids_json, pngs):
    """
    Convert a BIDS-structured JSON to flat filename-keyed format.
    Uses the PNG filenames to map BIDS entries back to files.

    Args:
        bids_json: The nested BIDS JSON dictionary
        pngs: List of PNG filenames (without path prefix)

    Returns:
        Flat JSON dictionary keyed by filename
    """
    flat_json = {}

    # Build a mapping from BIDS tags to filenames
    bids_to_filename = {}
    for png in pngs:
        tags = get_BIDS_fields_from_png(png)
        if tags:
            # Create a key from the BIDS tags (sub, ses, acq, run)
            key = (tags['sub'], tags['ses'] or '', tags['acq'] or '', tags['run'] or '')
            bids_to_filename[key] = png

    # Extract leaf dicts from BIDS structure
    leaf_dicts = get_leaf_dicts(bids_json)

    for paths, leaf_dict in leaf_dicts:
        # Get BIDS tags from the leaf dict
        sub = leaf_dict.get('sub', '')
        ses = leaf_dict.get('ses', '')
        acq = leaf_dict.get('acq', '')
        run = leaf_dict.get('run', '')

        # Find the corresponding filename
        key = (sub, ses, acq, run)
        if key in bids_to_filename:
            filename = bids_to_filename[key]
            flat_json[filename] = {
                'filename': filename,
                'QA_status': leaf_dict.get('QA_status', 'yes'),
                'reason': leaf_dict.get('reason', ''),
                'user': leaf_dict.get('user', ''),
                'date': leaf_dict.get('date', ''),
                'duration': 0  # Duration not tracked in BIDS mode
            }

    # Add any PNG files that weren't in the BIDS JSON
    for png in pngs:
        if png not in flat_json:
            flat_json[png] = {
                'filename': png,
                'QA_status': 'yes',
                'reason': '',
                'user': '',
                'date': '',
                'duration': 0
            }

    return flat_json


def convert_flat_to_bids(flat_json, pngs):
    """
    Convert a flat filename-keyed JSON to BIDS nested structure.
    Only works if filenames are BIDS-compliant.

    Args:
        flat_json: The flat JSON dictionary keyed by filename
        pngs: List of PNG filenames (without path prefix)

    Returns:
        Nested BIDS JSON dictionary, or None if any filename is not BIDS-compliant
    """
    # First verify all filenames are BIDS-compliant
    compliant, non_compliant = validate_bids_compliance(pngs)
    if non_compliant:
        return None  # Cannot convert if files aren't BIDS-compliant

    bids_json = {}

    for png in pngs:
        tags = get_BIDS_fields_from_png(png)
        if tags is None:
            continue

        sub, ses, acq, run = tags['sub'], tags['ses'], tags['acq'], tags['run']

        # Get existing data from flat JSON if available
        flat_entry = flat_json.get(png, {})

        # Navigate/create nested structure
        current_d = bids_json
        for tag in [sub, ses, acq, run]:
            if tag:
                current_d = current_d.setdefault(tag, {})

        # Set the values
        current_d.update({
            'QA_status': flat_entry.get('QA_status', 'yes'),
            'reason': flat_entry.get('reason', ''),
            'user': flat_entry.get('user', ''),
            'date': flat_entry.get('date', ''),
            'sub': sub,
            'ses': ses if ses else '',
            'acq': acq if acq else '',
            'run': run if run else ''
        })

    return bids_json


def save_json_file(path, dict):
    """
    Given a json dictionary, save it to the json file.
    Uses file locking to prevent corruption from concurrent writes.
    """
    path = Path(path)
    lock_path = path.parent / '.QA.lock'
    with open(lock_path, 'w') as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            with open(path, 'w') as f:
                json.dump(dict, f, indent=4)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)

@app.route('/select-root', methods=['GET'])
def select_root():
    """Redirect to main page which now includes root directory selection."""
    return redirect(url_for('index'))

@app.route('/select-root', methods=['POST'])
def set_root():
    """Set the QA root directory from user input."""
    path = request.form.get('qa_directory', '').strip()
    valid, errors = validate_directory(path)
    if not valid:
        for error in errors:
            flash(error, 'error')
        return redirect(url_for('index'))
    session['qa_directory'] = path
    flash(f"QA directory set to: {path}", 'success')
    return redirect(url_for('index'))

@app.route('/clear-root', methods=['POST'])
def clear_root():
    """Clear the current QA directory and return to selector."""
    session.pop('qa_directory', None)
    flash("QA directory cleared", 'info')
    return redirect(url_for('select_root'))

@app.route('/set-root-ajax', methods=['POST'])
def set_root_ajax():
    """AJAX endpoint to set the QA root directory without redirect."""
    data = request.get_json()
    path = data.get('path', '').strip()
    valid, errors = validate_directory(path)
    if not valid:
        return jsonify({'success': False, 'errors': errors}), 400
    session['qa_directory'] = path
    return jsonify({'success': True, 'path': path})

@app.route('/validate-path', methods=['POST'])
def validate_path_route():
    """AJAX endpoint for real-time path validation."""
    data = request.get_json()
    path = data.get('path', '').strip()
    valid, errors = validate_directory(path)
    subdir_count = 0
    if valid:
        subdirs = [x for x in Path(path).glob('*') if x.is_dir()]
        subdir_count = len(subdirs)
    return jsonify({'valid': valid, 'errors': errors, 'subdir_count': subdir_count})


@app.route('/set-options', methods=['POST'])
def set_options():
    """AJAX endpoint to set QA session options (user name, custom QA options)."""
    data = request.get_json()
    # BIDS mode is determined by which entry point was used (masi-qa vs masi-bids-qa)
    # We ignore any bids_mode sent from client and use the global setting
    session['bids_mode'] = BIDS_MODE
    session['user_name'] = data.get('user_name', '').strip()

    # Custom QA options (list of status labels)
    qa_options = data.get('qa_options', None)
    if qa_options and isinstance(qa_options, list):
        # Lowercase, strip whitespace, remove empties
        qa_options = [opt.strip().lower() for opt in qa_options if opt.strip()]
        # Validate: 2-8 unique options
        if len(qa_options) < 2:
            qa_options = ['yes', 'no', 'maybe']
        elif len(qa_options) > 8:
            qa_options = qa_options[:8]
        # Remove duplicates while preserving order
        seen = set()
        unique_options = []
        for opt in qa_options:
            if opt not in seen:
                seen.add(opt)
                unique_options.append(opt)
            if len(unique_options) < 2:
                pass  # will fall through to default below
        qa_options = unique_options if len(unique_options) >= 2 else ['yes', 'no', 'maybe']
    else:
        qa_options = ['yes', 'no', 'maybe']

    session['qa_options'] = qa_options

    # "Start from unreviewed" flag controls the initial-image-index and preload order
    # in the montage page. False by default; only true if the home-page checkbox is set.
    session['start_from_unreviewed'] = bool(data.get('start_from_unreviewed', False))

    return jsonify({'success': True, 'bids_mode': session['bids_mode'], 'user_name': session['user_name'], 'qa_options': session['qa_options'], 'start_from_unreviewed': session['start_from_unreviewed']})


@app.route('/convert-qa-format/<path:clicked_path>/<path:pipeline>', methods=['POST'])
@require_qa_directory
def convert_qa_format(clicked_path, pipeline):
    """Convert QA.json between BIDS and flat formats."""
    qa_directory = get_qa_directory()
    bids_mode = session.get('bids_mode', False)
    target_format = 'bids' if bids_mode else 'flat'

    pipeline_path = Path(qa_directory + '/' + clicked_path + '/' + pipeline)
    json_path = pipeline_path / 'QA.json'

    if not json_path.exists():
        flash("No QA.json file found to convert.", 'error')
        return redirect(url_for('index'))

    # Load existing JSON
    with open(json_path, 'r') as f:
        json_dict = json.load(f)

    existing_format = detect_json_format(json_dict)

    # Get list of PNG files
    pngs = [str(x.relative_to(qa_directory)) for x in pipeline_path.glob('**/*.png')]
    pngs = sorted(pngs)
    prefix = clicked_path + '/' + pipeline + '/'
    pngs_files = [x[len(prefix):] for x in pngs]

    # Create backup
    backup_path = pipeline_path / 'QA.json.backup'
    shutil.copy(json_path, backup_path)
    print(f"Created backup at: {backup_path}")

    # Perform conversion
    if target_format == 'bids' and existing_format == 'flat':
        # Convert flat to BIDS
        converted_json = convert_flat_to_bids(json_dict, pngs_files)
        if converted_json is None:
            flash("Conversion failed: some files are not BIDS-compliant.", 'error')
            return redirect(url_for('index'))
    elif target_format == 'flat' and existing_format == 'bids':
        # Convert BIDS to flat
        converted_json = convert_bids_to_flat(json_dict, pngs_files)
    else:
        flash(f"No conversion needed: data is already in {existing_format} format.", 'info')
        return redirect(url_for('render_montage', clicked_path=clicked_path, pipeline=pipeline))

    # Save converted JSON
    save_json_file(json_path, converted_json)

    # Regenerate CSV in new format
    convert_json_to_csv(converted_json, pipeline_path, bids_mode=bids_mode)

    print(f"Converted QA data from {existing_format} to {target_format} format")
    flash(f"Successfully converted QA data to {target_format.upper() if target_format == 'bids' else 'Standard'} format. Backup saved as QA.json.backup", 'success')

    # Redirect to the QA page
    return redirect(url_for('render_montage', clicked_path=clicked_path, pipeline=pipeline))


@app.route('/browse-path', methods=['POST'])
def browse_path():
    """AJAX endpoint for browsing server directories."""
    data = request.get_json()
    path = data.get('path', '/').strip()

    # Default to home directory or root
    if not path:
        path = os.path.expanduser('~')

    # Normalize the path
    path = os.path.normpath(path)

    # Check if path exists and is accessible
    if not os.path.exists(path):
        return jsonify({'error': 'Path does not exist', 'path': path}), 400
    if not os.path.isdir(path):
        return jsonify({'error': 'Path is not a directory', 'path': path}), 400
    if not os.access(path, os.R_OK):
        return jsonify({'error': 'Path is not readable', 'path': path}), 403

    # Get list of subdirectories
    try:
        entries = []
        for entry in sorted(os.listdir(path)):
            full_path = os.path.join(path, entry)
            if os.path.isdir(full_path):
                # Check if we can read this directory
                readable = os.access(full_path, os.R_OK)
                entries.append({
                    'name': entry,
                    'path': full_path,
                    'readable': readable
                })

        # Build breadcrumb parts
        parts = []
        current = path
        while current != os.path.dirname(current):  # Stop at root
            parts.append({'name': os.path.basename(current) or current, 'path': current})
            current = os.path.dirname(current)
        if current:  # Add root
            parts.append({'name': current, 'path': current})
        parts.reverse()

        return jsonify({
            'current_path': path,
            'parent_path': os.path.dirname(path) if path != '/' else None,
            'breadcrumbs': parts,
            'directories': entries
        })
    except PermissionError:
        return jsonify({'error': 'Permission denied', 'path': path}), 403

@app.route('/')
def index():
    # Get validated initial browse path with fallback chain: session → CWD → home → /
    initial_browse_path, has_valid_session = _get_initial_browse_path(get_qa_directory())

    # Clear invalid session path to avoid repeated errors
    if not has_valid_session:
        session.pop('qa_directory', None)

    if args.debug:
        print("Initial Browse Path:", initial_browse_path)
        print("Has Valid Session:", has_valid_session)

    # Set session bids_mode from global at each page load to ensure consistency
    session['bids_mode'] = BIDS_MODE
    # Get previously entered user name from session (if any)
    user_name = session.get('user_name', '')
    qa_options = session.get('qa_options', ['yes', 'no', 'maybe'])
    return render_template('root.html',
        bids_mode=BIDS_MODE,
        user_name=user_name,
        qa_options=qa_options,
        initial_browse_path=initial_browse_path,
        has_valid_session=has_valid_session)

@app.route('/datasets', methods=['POST'])
@require_qa_directory
def load_datasets():
    qa_directory = get_qa_directory()
    data = request.get_json()
    path = data.get('path')

    # Here you can customize what datasets to show for the selected path
    # For simplicity, I'll assume you fetch directories in a similar manner
    datasets = sorted([str(x.name) for x in Path(qa_directory + '/' + path).glob('*') if x.is_dir()])

    return jsonify(datasets=datasets)

@app.route('/datasets/<path:clicked_path>')
@require_qa_directory
def datasets(clicked_path):
    """Redirect to combined selection page with dataset pre-selected."""
    if args.debug:
        print("Redirecting clicked path:", clicked_path)
    return redirect(url_for('index', dataset=clicked_path))

@app.route('/datasets/<path:clicked_path>/<path:pipeline>')
@require_qa_directory
def render_montage(clicked_path, pipeline):
    qa_directory = get_qa_directory()
    bids_mode = session.get('bids_mode', False)
    user_name = session.get('user_name', '')

    print(f"Loading images from: {clicked_path}/{pipeline} (BIDS mode: {bids_mode})")

    # Get the list of PNG files in the pipeline directory
    pipeline_path = Path(qa_directory + '/' + clicked_path + '/' + pipeline)

    # Check write permissions before proceeding
    can_write, file_issues, files_missing = check_write_permissions(pipeline_path)
    if not can_write:
        try:
            current_user = pwd.getpwuid(os.getuid()).pw_name
        except KeyError:
            current_user = str(os.getuid())
        return render_template('permission_error.html',
                               clicked_path=clicked_path,
                               pipeline=pipeline,
                               pipeline_path=str(pipeline_path),
                               file_issues=file_issues,
                               files_missing=files_missing,
                               current_user=current_user)

    pngs = [str(x.relative_to(qa_directory)) for x in pipeline_path.glob('**/*.png')]
    pngs = sorted(pngs)

    # Pass image paths to montage.html so they can be loaded
    image_paths = [str(png) for png in pngs]
    # Extract paths relative to pipeline folder (e.g., "subdir/image.png" for recursive images)
    prefix = clicked_path + '/' + pipeline + '/'
    pngs_files = [x[len(prefix):] for x in pngs]

    # BIDS mode: validate compliance first
    if bids_mode:
        compliant, non_compliant = validate_bids_compliance(pngs_files)
        if non_compliant:
            # Show BIDS errors page instead of crashing
            return render_template('bids_errors.html',
                                   non_compliant_files=non_compliant,
                                   total_files=len(pngs_files),
                                   clicked_path=clicked_path,
                                   pipeline=pipeline)

    # Check to see if the json file exists. If it doesn't, create it
    json_path = pipeline_path / 'QA.json'
    session['json_path'] = str(json_path)
    session['bids_mode'] = bids_mode  # Store for update_qa_dict

    qa_options = session.get('qa_options', ['yes', 'no', 'maybe'])
    default_status = qa_options[0]

    if not json_path.exists():
        print("Creating new QA session...")
        if bids_mode:
            json_dict = create_bids_json_dict(pngs_files, default_status=default_status)
        else:
            json_dict = create_json_dict(pngs_files, default_status=default_status)
        df = convert_json_to_csv(json_dict, pipeline_path, bids_mode=bids_mode)
        save_json_file(json_path, json_dict)
        # Set permissions on newly created files
        set_file_permissions(json_path)
        set_file_permissions(pipeline_path / 'QA.csv')
    else:
        with open(json_path, 'r') as f:
            json_dict = json.load(f)

        # Detect format mismatch between selected mode and existing data
        existing_format = detect_json_format(json_dict)
        selected_mode = 'bids' if bids_mode else 'flat'

        if existing_format != 'empty' and existing_format != 'unknown':
            if (bids_mode and existing_format == 'flat') or (not bids_mode and existing_format == 'bids'):
                # Format mismatch detected
                conversion_error = None
                if bids_mode and existing_format == 'flat':
                    # Want BIDS but have flat - check if files are BIDS-compliant
                    compliant, non_compliant = validate_bids_compliance(pngs_files)
                    if non_compliant:
                        conversion_error = f"Cannot convert to BIDS format because {len(non_compliant)} file(s) are not BIDS-compliant. Go back and disable BIDS mode, or rename files to follow BIDS naming convention."

                return render_template('mode_mismatch.html',
                                       clicked_path=clicked_path,
                                       pipeline=pipeline,
                                       selected_mode=selected_mode,
                                       existing_format=existing_format,
                                       conversion_error=conversion_error)

        # Check to make sure there are no duplicate QA dictionaries
        paths, leaf_dicts = zip(*get_leaf_dicts(json_dict))
        assert are_unique_qa_dicts(leaf_dicts), f"There are duplicate QA dictionaries in the json file {json_path}. Please correct before attempting QA."

        # Check to make sure that the paths to the json dictionaries are correct
        assert_tags_in_dict(paths, leaf_dicts)

        # Check if existing QA_status values are compatible with configured options
        qa_options = session.get('qa_options', ['yes', 'no', 'maybe'])
        unrecognized = check_qa_status_mismatch(leaf_dicts, qa_options)
        if unrecognized:
            return render_template('status_mismatch.html',
                                   clicked_path=clicked_path,
                                   pipeline=pipeline,
                                   qa_options=qa_options,
                                   unrecognized_statuses=unrecognized)

        assert_valid_qa_status(leaf_dicts, valid_statuses=qa_options)

        # Check to make sure that every json entry has a corresponding png file
        check_png_for_json(leaf_dicts, [str(x) for x in pngs_files])

        # If the png does not have a corresponding json entry, it needs to be added
        if bids_mode:
            json_dict = check_json_for_png_bids(json_dict, pngs_files, default_status=default_status)
        else:
            json_dict = check_json_for_png(json_dict, pngs_files, default_status=default_status)

    return render_template('montage.html',
                           clicked_path=clicked_path,
                           pipeline=pipeline,
                           image_paths=image_paths,
                           json_dict=json_dict,
                           bids_mode=bids_mode,
                           user_name=user_name,
                           qa_options=qa_options,
                           default_status=default_status,
                           start_from_unreviewed=session.get('start_from_unreviewed', False))

    #maybe assert the following python functions:

        #1.) make sure that the 'QA_status' is either 'yes', 'no', or 'maybe' when reading in the json file
            #done

    #need to create the following JS functions:
        #1.) read in the sub, ses, acq, run tags from the image filename
            #getBIDSFieldsFromPNG in app_json/json_test, also has example code below to access the tags
        #2.) given the sub, ses, acq, run, be able to get the correspoding QA leaf dictionary from the json
            #2a.) Be able to read in the json dictionary from the data passed to the template
                #single line at beginning of loop
            #getLeafDict in app_json, also has example code at bottom of script to access leaf dictionary
        #3.) set up the yes,no,maybe and populate reason based on the corresponding values of the leaf dictionary
            # Done. Set up in the code body.
        #4.) given the sub, ses, acq, run, query the json dictionary to see if the QA status and reason have been updated
            #4a.) Before we change pngs, need to get the values of the current yes,no,maybe and reason
                # DONE
        #5.) be able to get the username and datetime of the update
            #getUserNameAndDateTime in app_json
                #note the username is passed to the render_template function
        #6.) push any changes to the json dictionary (should be able to call the update_file app function)
            # DONE: also have it saving the csv as well
        #7.) Make it so that if there is ANY error detected, it freezes the page and displays the error message
            # ** TODO **




@app.route('/qa')
@require_qa_directory
def render_montage_standard():
    """Standard (non-BIDS) mode: QA directory contains PNGs directly."""
    qa_directory = get_qa_directory()
    bids_mode = session.get('bids_mode', False)
    user_name = session.get('user_name', '')

    pipeline_path = Path(qa_directory)

    # Check write permissions before proceeding
    can_write, file_issues, files_missing = check_write_permissions(pipeline_path)
    if not can_write:
        try:
            current_user = pwd.getpwuid(os.getuid()).pw_name
        except KeyError:
            current_user = str(os.getuid())
        return render_template('permission_error.html',
                               clicked_path='',
                               pipeline='',
                               pipeline_path=str(pipeline_path),
                               file_issues=file_issues,
                               files_missing=files_missing,
                               current_user=current_user)

    pngs = sorted([str(x.relative_to(qa_directory)) for x in pipeline_path.glob('**/*.png')])

    image_paths = list(pngs)
    pngs_files = list(pngs)

    # Check to see if the json file exists. If it doesn't, create it
    json_path = pipeline_path / 'QA.json'
    session['json_path'] = str(json_path)
    session['bids_mode'] = bids_mode

    qa_options = session.get('qa_options', ['yes', 'no', 'maybe'])
    default_status = qa_options[0]

    if not json_path.exists():
        print("Creating new QA session...")
        json_dict = create_json_dict(pngs_files, default_status=default_status)
        df = convert_json_to_csv(json_dict, pipeline_path, bids_mode=False)
        save_json_file(json_path, json_dict)
        # Set permissions on newly created files
        set_file_permissions(json_path)
        set_file_permissions(pipeline_path / 'QA.csv')
    else:
        with open(json_path, 'r') as f:
            json_dict = json.load(f)

        # Detect format mismatch
        existing_format = detect_json_format(json_dict)
        if existing_format != 'empty' and existing_format != 'unknown':
            if existing_format == 'bids':
                return render_template('mode_mismatch.html',
                                       clicked_path='',
                                       pipeline='',
                                       selected_mode='flat',
                                       existing_format=existing_format,
                                       conversion_error=None)

        # Validate existing data
        paths, leaf_dicts = zip(*get_leaf_dicts(json_dict))
        assert are_unique_qa_dicts(leaf_dicts), f"There are duplicate QA dictionaries in the json file {json_path}. Please correct before attempting QA."
        assert_tags_in_dict(paths, leaf_dicts)

        # Check if existing QA_status values are compatible with configured options
        unrecognized = check_qa_status_mismatch(leaf_dicts, qa_options)
        if unrecognized:
            return render_template('status_mismatch.html',
                                   clicked_path='',
                                   pipeline='',
                                   qa_options=qa_options,
                                   unrecognized_statuses=unrecognized)

        assert_valid_qa_status(leaf_dicts, valid_statuses=qa_options)
        check_png_for_json(leaf_dicts, [str(x) for x in pngs_files])
        json_dict = check_json_for_png(json_dict, pngs_files, default_status=default_status)

    return render_template('montage.html',
                           clicked_path='',
                           pipeline='',
                           image_paths=image_paths,
                           json_dict=json_dict,
                           bids_mode=bids_mode,
                           user_name=user_name,
                           qa_options=qa_options,
                           default_status=default_status,
                           start_from_unreviewed=session.get('start_from_unreviewed', False))

@app.route('/qa/<path:image_filename>')
@require_qa_directory
def serve_image_standard(image_filename):
    """Serve images in Standard mode directly from qa_directory."""
    qa_directory = get_qa_directory()
    image_path = os.path.join(qa_directory, image_filename)
    if os.path.isfile(image_path):
        return send_file(image_path, mimetype='image/png')
    else:
        return 'Image not found', 404

@app.route('/datasets/<path:clicked_path>/<path:pipeline>/<path:image_filename>')
@require_qa_directory
def serve_image(clicked_path, pipeline, image_filename):
    """
    This function is used to load in a single image file (png) from the QA directory
    """
    qa_directory = get_qa_directory()

    # Construct the full path to the image file
    image_path = os.path.join(qa_directory, clicked_path, pipeline, image_filename)

    # Check if the image file exists
    #print("Checking for file:", image_path)
    if os.path.isfile(image_path):
        # Send the image file as a response
        #print("Sending file:", image_path)
        return send_file(image_path, mimetype='image/png')
    else:
        # Return a 404 error if the file doesn't exist
        return 'Image not found', 404
# def serve_image(image_path):
#     # Construct the full path to the image file
#     #image_path = os.path.join(QA_directory, clicked_path, pipeline, image_filename)

#     # Check if the image file exists
#     if os.path.isfile(image_path):
#         # Send the image file as a response
#         return send_file(image_path, mimetype='image/png')  # Adjust mimetype as needed
#     else:
#         # Return a 404 error if the file doesn't exist
#         return 'Image not found', 404

@app.route('/update_qa_dict', methods=['POST'])
def update_qa_dict():
    """
    Update the QA JSON and CSV with the new QA status and reason.
    Supports a writeToDisk flag to skip file I/O when only metadata (date/duration) changed.
    """
    json_path_str = session.get('json_path')
    if not json_path_str:
        return jsonify({'status': 'error', 'message': 'No active QA session'}), 400
    json_path = Path(json_path_str)
    bids_mode = session.get('bids_mode', False)

    data = request.json
    nested_dict = data['nestedDict']
    write_to_disk = data.get('writeToDisk', True)

    if write_to_disk:
        save_json_file(json_path, nested_dict)
        _ = convert_json_to_csv(nested_dict, json_path.parent, bids_mode=bids_mode)

    return jsonify({'status': 'success'})

def _run_app(bids_mode=False):
    """Shared startup logic for both entry points."""
    global args, BIDS_MODE
    BIDS_MODE = bids_mode
    args = pa()

    # Suppress Flask/Werkzeug request logging unless in debug mode
    if not args.debug:
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)

    app_name = "MASI-BIDS-QA" if BIDS_MODE else "MASI-QA"
    mode_name = "BIDS" if BIDS_MODE else "Standard"

    print()
    print("=" * 50)
    print(f"  {app_name} v{__version__}")
    print("=" * 50)
    print()
    print("  Authors: Michael Kim, Yihao Liu, Gaurav Rudravaram")
    print("  MASI Lab @ Vanderbilt University")
    print("  License: MIT")
    print()
    print(f"  Mode: {mode_name}")

    # Determine port
    if args.port:
        port = args.port
    else:
        port = find_available_port()
        if port is None:
            print("Error: Could not find an available port (tried 5000-5009)")
            print("Use --port to specify a different port")
            return

    print()
    print("-" * 50)
    print(f"  Running at: http://localhost:{port}")
    print("  Press Ctrl+C to stop")
    print("-" * 50)
    print()

    app.run(host='0.0.0.0', port=port, debug=args.debug)


def main():
    """Entry point for masi-qa (Standard mode)."""
    _run_app(bids_mode=False)


def main_bids():
    """Entry point for masi-bids-qa (BIDS compliance mode)."""
    _run_app(bids_mode=True)


if __name__ == "__main__":
    main()
