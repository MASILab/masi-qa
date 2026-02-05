"""
Authors: Michael Kim (michael.kim@vanderbilt.edu)
         Yihao Liu (yihao.liu@vanderbilt.edu)

MASI Lab @ Vanderbilt University
License: MIT
"""

from flask import Flask, request, render_template, redirect, url_for, flash, jsonify, send_file, session
import pandas as pd
import os, json, io, argparse, re, grp, logging, socket, shutil, tempfile, fcntl, sqlite3
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

    # Check if files exist
    json_exists = json_path.exists()
    csv_exists = csv_path.exists()

    if not json_exists or not csv_exists:
        # Files need to be created - check directory write permission
        if not os.access(pipeline_path, os.W_OK):
            files_missing = True
            if not json_exists:
                file_issues.append({
                    'name': 'QA.json',
                    'status': 'missing',
                    'message': 'needs to be created (directory not writable)'
                })
            if not csv_exists:
                file_issues.append({
                    'name': 'QA.csv',
                    'status': 'missing',
                    'message': 'needs to be created (directory not writable)'
                })

    # Check existing files for write permission
    if json_exists and not os.access(json_path, os.W_OK):
        file_issues.append({
            'name': 'QA.json',
            'status': 'not-writable',
            'message': 'exists but not writable'
        })

    if csv_exists and not os.access(csv_path, os.W_OK):
        file_issues.append({
            'name': 'QA.csv',
            'status': 'not-writable',
            'message': 'exists but not writable'
        })

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

def create_json_dict(filepaths):
    """
    Given a list of filenames, create the flat json dictionary (non-BIDS mode).
    """
    nested_d = {}
    for png in tqdm(filepaths):
        row = {'filename': str(png), 'QA_status': 'yes', 'reason': '', 'user': '', 'date': '', 'duration': 0}
        nested_d[png] = row
    return nested_d


def create_bids_json_dict(filepaths):
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
            'QA_status': 'yes',
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
    Sets group-writable permissions so multiple users can access the file.
    """
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

        csv_path = pipeline_path / 'QA.csv'
        df_sorted.to_csv(csv_path, index=False)

        # Set group-writable permissions for multi-user access
        set_file_permissions(csv_path)

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

        csv_path = pipeline_path / 'QA.csv'
        df.to_csv(csv_path, index=False)

        # Set group-writable permissions for multi-user access
        set_file_permissions(csv_path)

        return df


def update_csv_entry(csv_path, key_data, entry_data, bids_mode=False):
    """
    Update a single row in QA.csv without regenerating the entire file.
    Uses file locking to prevent race conditions from concurrent updates.

    Args:
        csv_path: Path to QA.csv
        key_data: Dictionary with key fields to identify the row
                  - Non-BIDS: {'filename': 'image.png'}
                  - BIDS: {'sub': 'sub-001', 'ses': 'ses-01', 'acq': '', 'run': ''}
        entry_data: Dictionary of fields to update
        bids_mode: Whether using BIDS format
    """
    csv_path = Path(csv_path)
    lock_path = csv_path.parent / '.QA.csv.lock'

    # Use file locking to serialize concurrent updates
    with open(lock_path, 'w') as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)  # Exclusive lock
        try:
            df = pd.read_csv(csv_path)

            if bids_mode:
                # Build mask for BIDS key fields
                mask = (df['sub'] == key_data.get('sub', ''))
                if 'ses' in df.columns:
                    mask &= (df['ses'].fillna('') == key_data.get('ses', ''))
                if 'acq' in df.columns:
                    mask &= (df['acq'].fillna('') == key_data.get('acq', ''))
                if 'run' in df.columns:
                    mask &= (df['run'].fillna('') == key_data.get('run', ''))
            else:
                # Non-BIDS: match by filename
                mask = df['filename'] == key_data['filename']

            # Update matching row(s)
            if mask.any():
                for field, value in entry_data.items():
                    if field in df.columns:
                        df.loc[mask, field] = value

            # Atomic write
            def write_csv(f):
                df.to_csv(f, index=False)

            atomic_write_file(csv_path, write_csv)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)  # Release lock


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

def check_json_for_png(nested, pngs):
    """
    Given a flat json and list of pngs, make sure that every single png file has a corresponding json entry.
    If it does not, then add the default values to the json file.
    (Non-BIDS mode)
    """
    keys = nested.keys()
    for png in pngs:
        if png not in keys:
            row = {'filename': str(png), 'QA_status': 'yes', 'reason': '', 'user': '', 'date': '', 'duration': 0}
            nested[png] = row
    return nested


def check_json_for_png_bids(nested, pngs):
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
                'QA_status': 'yes',
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

def assert_valid_qa_status(dict_list):
    """
    Given a list of QA dictionaries, assert that the QA status is either 'yes', 'no', or 'maybe' for all
    """

    valid_statuses = ['yes', 'no', 'maybe']

    for d in dict_list:
        assert d['QA_status'] in valid_statuses, f"QA status {d['QA_status']} is not valid for dictionary {d}"


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
    Sets group-writable permissions so multiple users can access the file.
    """
    with open(path, 'w') as f:
        json.dump(dict, f, indent=4)

    # Set group-writable permissions for multi-user access
    set_file_permissions(path)


def atomic_write_file(path, write_func):
    """
    Write file atomically using temp file + rename pattern.
    This ensures the file is never corrupted if the process is interrupted.
    """
    path = Path(path)
    dir_path = path.parent
    fd, temp_path = tempfile.mkstemp(dir=str(dir_path), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            write_func(f)
        os.replace(temp_path, str(path))
        set_file_permissions(path)
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def update_json_entry(json_path, key_path, entry_data):
    """
    Update a single entry in QA.json without rewriting the entire file.
    Uses file locking to prevent race conditions from concurrent updates.

    Args:
        json_path: Path to QA.json
        key_path: List of keys to navigate to the entry
                  - Non-BIDS: ["filename.png"]
                  - BIDS: ["sub-001", "ses-01"] (with optional acq, run)
        entry_data: Dictionary of fields to update

    Returns:
        Updated json_dict
    """
    json_path = Path(json_path)
    lock_path = json_path.parent / '.QA.json.lock'

    # Use file locking to serialize concurrent updates
    with open(lock_path, 'w') as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)  # Exclusive lock
        try:
            with open(json_path, 'r') as f:
                json_dict = json.load(f)

            # Navigate to the entry
            current = json_dict
            for key in key_path[:-1]:
                if key and key in current:
                    current = current[key]

            # Update the leaf entry
            final_key = key_path[-1] if key_path else None
            if final_key and final_key in current:
                current[final_key].update(entry_data)

            # Atomic write
            atomic_write_file(json_path, lambda f: json.dump(json_dict, f, indent=4))
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)  # Release lock

    return json_dict


# ============================================================================
# SQLite Database Functions
# ============================================================================

def get_db_path(pipeline_path):
    """Get the path to the SQLite database file."""
    return Path(pipeline_path) / 'QA.db'


def get_db_connection(db_path):
    """
    Get a database connection with proper settings.
    Uses WAL mode for better concurrency.
    Used for one-time operations (init, migration, export).
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


# Module-level connection cache for pooling: {db_path_str: connection}
_db_connections = {}


def get_pooled_connection(db_path):
    """
    Get or create a pooled database connection.
    Used for frequent write operations during QA review to avoid
    connection open/close overhead and improve WAL checkpointing.
    """
    db_path_str = str(db_path)
    if db_path_str not in _db_connections or _db_connections[db_path_str] is None:
        conn = sqlite3.connect(db_path_str, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # WAL mode is persistent, but set it in case this is a new connection
        conn.execute('PRAGMA journal_mode=WAL')
        # NORMAL sync is safe for WAL mode and faster than FULL
        conn.execute('PRAGMA synchronous=NORMAL')
        _db_connections[db_path_str] = conn
    return _db_connections[db_path_str]


def close_pooled_connection(db_path):
    """
    Close and checkpoint a pooled connection.
    Call this when user leaves the QA page to ensure WAL is merged
    back into the main database file.
    """
    db_path_str = str(db_path)
    if db_path_str in _db_connections and _db_connections[db_path_str]:
        conn = _db_connections[db_path_str]
        try:
            # TRUNCATE mode: checkpoint and truncate WAL file to zero bytes
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            conn.close()
        except Exception:
            pass
        _db_connections[db_path_str] = None


def init_database(db_path, bids_mode=False):
    """
    Initialize the SQLite database with the required schema.
    Creates tables if they don't exist.
    """
    conn = get_db_connection(db_path)
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS qa_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT,
                sub TEXT,
                ses TEXT,
                acq TEXT,
                run TEXT,
                QA_status TEXT NOT NULL DEFAULT 'yes',
                reason TEXT DEFAULT '',
                user TEXT DEFAULT '',
                date TEXT DEFAULT '',
                duration REAL DEFAULT 0,
                bids_mode INTEGER NOT NULL DEFAULT 0
            )
        ''')
        # Create indexes for efficient lookups
        conn.execute('CREATE INDEX IF NOT EXISTS idx_filename ON qa_entries(filename)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_bids ON qa_entries(sub, ses, acq, run)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_unreviewed ON qa_entries(date)')

        # Metadata table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS qa_metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        conn.commit()
    finally:
        conn.close()

    # Set file permissions
    set_file_permissions(db_path)


def detect_storage_format(pipeline_path):
    """
    Detect what storage format exists in the directory.
    Returns: 'sqlite', 'json', 'both', or 'none'
    """
    pipeline_path = Path(pipeline_path)
    db_exists = (pipeline_path / 'QA.db').exists()
    json_exists = (pipeline_path / 'QA.json').exists()

    if db_exists and json_exists:
        return 'both'
    elif db_exists:
        return 'sqlite'
    elif json_exists:
        return 'json'
    else:
        return 'none'


def db_add_qa_entry(conn, entry_data, bids_mode=False):
    """
    Add a new QA entry to the database.
    """
    if bids_mode:
        conn.execute('''
            INSERT INTO qa_entries (sub, ses, acq, run, QA_status, reason, user, date, bids_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
        ''', (
            entry_data.get('sub', ''),
            entry_data.get('ses', ''),
            entry_data.get('acq', ''),
            entry_data.get('run', ''),
            entry_data.get('QA_status', 'yes'),
            entry_data.get('reason', ''),
            entry_data.get('user', ''),
            entry_data.get('date', '')
        ))
    else:
        conn.execute('''
            INSERT INTO qa_entries (filename, QA_status, reason, user, date, duration, bids_mode)
            VALUES (?, ?, ?, ?, ?, ?, 0)
        ''', (
            entry_data.get('filename', ''),
            entry_data.get('QA_status', 'yes'),
            entry_data.get('reason', ''),
            entry_data.get('user', ''),
            entry_data.get('date', ''),
            entry_data.get('duration', 0)
        ))


def db_update_qa_entry(db_path, key_data, entry_data, bids_mode=False):
    """
    Update a single QA entry in the database.
    This is the main function called during QA review.
    Uses pooled connection for efficiency during rapid navigation.
    """
    conn = get_pooled_connection(db_path)
    if bids_mode:
        # Build SET clause dynamically
        set_fields = []
        values = []
        for field in ['QA_status', 'reason', 'user', 'date']:
            if field in entry_data:
                set_fields.append(f'{field} = ?')
                values.append(entry_data[field])

        if not set_fields:
            return

        # Add WHERE clause values
        values.extend([
            key_data.get('sub', ''),
            key_data.get('ses', ''),
            key_data.get('acq', ''),
            key_data.get('run', '')
        ])

        sql = f'''
            UPDATE qa_entries
            SET {', '.join(set_fields)}
            WHERE sub = ? AND COALESCE(ses, '') = ? AND COALESCE(acq, '') = ? AND COALESCE(run, '') = ?
        '''
        conn.execute(sql, values)
    else:
        # Non-BIDS mode
        set_fields = []
        values = []
        for field in ['QA_status', 'reason', 'user', 'date', 'duration']:
            if field in entry_data:
                set_fields.append(f'{field} = ?')
                values.append(entry_data[field])

        if not set_fields:
            return

        values.append(key_data.get('filename', ''))

        sql = f'''
            UPDATE qa_entries
            SET {', '.join(set_fields)}
            WHERE filename = ?
        '''
        conn.execute(sql, values)

    conn.commit()
    # Note: Connection stays open for reuse - closed via close_pooled_connection()


def db_get_all_qa_entries(db_path, bids_mode=False):
    """
    Get all QA entries from the database.
    """
    conn = get_db_connection(db_path)
    try:
        if bids_mode:
            cursor = conn.execute('''
                SELECT sub, ses, acq, run, QA_status, reason, user, date
                FROM qa_entries
                WHERE bids_mode = 1
                ORDER BY sub, ses, acq, run
            ''')
        else:
            cursor = conn.execute('''
                SELECT filename, QA_status, reason, user, date, duration
                FROM qa_entries
                WHERE bids_mode = 0
                ORDER BY filename
            ''')

        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def migrate_json_to_sqlite(pipeline_path, bids_mode=False):
    """
    Migrate existing QA.json data to SQLite database.
    Called when QA.json exists but QA.db does not.
    """
    pipeline_path = Path(pipeline_path)
    json_path = pipeline_path / 'QA.json'
    db_path = get_db_path(pipeline_path)

    # Read existing JSON
    with open(json_path, 'r') as f:
        json_dict = json.load(f)

    # Initialize database
    init_database(db_path, bids_mode)

    # Extract entries and insert
    conn = get_db_connection(db_path)
    try:
        if bids_mode:
            # BIDS mode - extract leaf dicts
            leaf_data = get_leaf_dicts(json_dict)
            for paths, leaf_dict in leaf_data:
                entry = {
                    'sub': leaf_dict.get('sub', ''),
                    'ses': leaf_dict.get('ses', ''),
                    'acq': leaf_dict.get('acq', ''),
                    'run': leaf_dict.get('run', ''),
                    'QA_status': leaf_dict.get('QA_status', 'yes'),
                    'reason': leaf_dict.get('reason', ''),
                    'user': leaf_dict.get('user', ''),
                    'date': leaf_dict.get('date', '')
                }
                db_add_qa_entry(conn, entry, bids_mode=True)
        else:
            # Non-BIDS mode - flat structure
            for filename, entry in json_dict.items():
                entry_data = {
                    'filename': filename,
                    'QA_status': entry.get('QA_status', 'yes'),
                    'reason': entry.get('reason', ''),
                    'user': entry.get('user', ''),
                    'date': entry.get('date', ''),
                    'duration': entry.get('duration', 0)
                }
                db_add_qa_entry(conn, entry_data, bids_mode=False)

        conn.commit()

        # Update metadata
        conn.execute('INSERT OR REPLACE INTO qa_metadata VALUES (?, ?)',
                    ('migrated_from_json', datetime.now().isoformat()))
        conn.commit()

        print(f"Successfully migrated QA data from JSON to SQLite")
        return True
    except Exception as e:
        print(f"Migration failed: {e}")
        # Clean up failed migration
        if db_path.exists():
            db_path.unlink()
        raise
    finally:
        conn.close()


def create_empty_database(pipeline_path, pngs, bids_mode=False):
    """
    Create a new empty database with default entries for all PNG files.
    Called when no existing QA data (JSON or SQLite) exists.
    """
    pipeline_path = Path(pipeline_path)
    db_path = get_db_path(pipeline_path)

    # Initialize database
    init_database(db_path, bids_mode)

    conn = get_db_connection(db_path)
    try:
        for png in tqdm(pngs, desc="Creating database entries"):
            if bids_mode:
                tags = get_BIDS_fields_from_png(png)
                if tags:
                    entry = {
                        'sub': tags['sub'],
                        'ses': tags['ses'] if tags['ses'] else '',
                        'acq': tags['acq'] if tags['acq'] else '',
                        'run': tags['run'] if tags['run'] else '',
                        'QA_status': 'yes',
                        'reason': '',
                        'user': '',
                        'date': ''
                    }
                    db_add_qa_entry(conn, entry, bids_mode=True)
            else:
                entry = {
                    'filename': png,
                    'QA_status': 'yes',
                    'reason': '',
                    'user': '',
                    'date': '',
                    'duration': 0
                }
                db_add_qa_entry(conn, entry, bids_mode=False)

        conn.commit()
        print(f"Created database with {len(pngs)} entries")
    finally:
        conn.close()


def sync_pngs_with_db(db_path, pngs, bids_mode=False):
    """
    Ensure all PNG files have corresponding database entries.
    Adds default entries for new PNGs.
    """
    conn = get_db_connection(db_path)
    try:
        if bids_mode:
            # Get existing BIDS keys from database
            cursor = conn.execute('SELECT sub, ses, acq, run FROM qa_entries WHERE bids_mode = 1')
            existing = set()
            for row in cursor:
                existing.add((row['sub'], row['ses'] or '', row['acq'] or '', row['run'] or ''))

            # Add missing entries
            added = 0
            for png in pngs:
                tags = get_BIDS_fields_from_png(png)
                if tags:
                    key = (tags['sub'], tags['ses'] or '', tags['acq'] or '', tags['run'] or '')
                    if key not in existing:
                        entry = {
                            'sub': tags['sub'],
                            'ses': tags['ses'] if tags['ses'] else '',
                            'acq': tags['acq'] if tags['acq'] else '',
                            'run': tags['run'] if tags['run'] else '',
                            'QA_status': 'yes',
                            'reason': '',
                            'user': '',
                            'date': ''
                        }
                        db_add_qa_entry(conn, entry, bids_mode=True)
                        added += 1
        else:
            # Get existing filenames from database
            cursor = conn.execute('SELECT filename FROM qa_entries WHERE bids_mode = 0')
            existing = set(row['filename'] for row in cursor)

            # Add missing entries
            added = 0
            for png in pngs:
                if png not in existing:
                    entry = {
                        'filename': png,
                        'QA_status': 'yes',
                        'reason': '',
                        'user': '',
                        'date': '',
                        'duration': 0
                    }
                    db_add_qa_entry(conn, entry, bids_mode=False)
                    added += 1

        if added > 0:
            conn.commit()
            print(f"Added {added} new entries to database")
    finally:
        conn.close()


def build_json_dict_from_db(db_path, bids_mode=False):
    """
    Build the nested dictionary structure from database.
    Used to pass data to frontend templates (maintains existing API).
    """
    entries = db_get_all_qa_entries(db_path, bids_mode)

    if bids_mode:
        # Build nested BIDS structure
        json_dict = {}
        for entry in entries:
            sub = entry.get('sub', '')
            ses = entry.get('ses', '')
            acq = entry.get('acq', '')
            run = entry.get('run', '')

            current = json_dict
            for tag in [sub, ses, acq, run]:
                if tag:
                    current = current.setdefault(tag, {})

            current.update({
                'QA_status': entry.get('QA_status', 'yes'),
                'reason': entry.get('reason', ''),
                'user': entry.get('user', ''),
                'date': entry.get('date', ''),
                'sub': sub,
                'ses': ses,
                'acq': acq,
                'run': run
            })
        return json_dict
    else:
        # Build flat structure
        json_dict = {}
        for entry in entries:
            filename = entry.get('filename', '')
            json_dict[filename] = {
                'filename': filename,
                'QA_status': entry.get('QA_status', 'yes'),
                'reason': entry.get('reason', ''),
                'user': entry.get('user', ''),
                'date': entry.get('date', ''),
                'duration': entry.get('duration', 0)
            }
        return json_dict


def export_db_to_json(db_path, output_path, bids_mode=False):
    """
    Export database contents to JSON file.
    Reconstructs the nested BIDS structure if in BIDS mode.
    """
    json_dict = build_json_dict_from_db(db_path, bids_mode)

    # Atomic write
    atomic_write_file(output_path, lambda f: json.dump(json_dict, f, indent=4))
    print(f"Exported database to {output_path}")
    return json_dict


def export_db_to_csv(db_path, output_path, bids_mode=False):
    """
    Export database contents to CSV file.
    """
    entries = db_get_all_qa_entries(db_path, bids_mode)

    if bids_mode:
        header = ['sub', 'ses', 'acq', 'run', 'QA_status', 'reason', 'user', 'date']
    else:
        header = ['filename', 'QA_status', 'reason', 'user', 'date', 'duration']

    df = pd.DataFrame(entries)

    # Ensure all columns exist
    for col in header:
        if col not in df.columns:
            df[col] = ''

    df = df[header].fillna('')

    if bids_mode:
        df = df.sort_values(by=['sub', 'ses', 'acq', 'run'])

    # Atomic write
    def write_csv(f):
        df.to_csv(f, index=False)

    atomic_write_file(output_path, write_csv)
    print(f"Exported database to {output_path}")
    return df


def export_all(db_path, pipeline_path, bids_mode=False):
    """
    Export both JSON and CSV from database.
    Called by export endpoints and "Change Selection" button.
    """
    pipeline_path = Path(pipeline_path)
    json_path = pipeline_path / 'QA.json'
    csv_path = pipeline_path / 'QA.csv'

    export_db_to_json(db_path, json_path, bids_mode)
    export_db_to_csv(db_path, csv_path, bids_mode)

    return True


# ============================================================================
# End of SQLite Database Functions
# ============================================================================


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
    """AJAX endpoint to set QA session options (user name)."""
    data = request.get_json()
    # BIDS mode is determined by which entry point was used (masi-qa vs masi-bids-qa)
    # We ignore any bids_mode sent from client and use the global setting
    session['bids_mode'] = BIDS_MODE
    session['user_name'] = data.get('user_name', '').strip()
    return jsonify({'success': True, 'bids_mode': session['bids_mode'], 'user_name': session['user_name']})


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
    pngs = sorted(pngs, key=str.lower)  # Case-insensitive sort for consistent ordering
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
    qa_directory = get_qa_directory()
    # Pass qa_directory and bids_mode to template (qa_directory may be None if not set)
    if args.debug:
        print("QA Directory:", qa_directory)
    # Set session bids_mode from global at each page load to ensure consistency
    session['bids_mode'] = BIDS_MODE
    # Get previously entered user name from session (if any)
    user_name = session.get('user_name', '')
    return render_template('root.html', qa_directory=qa_directory, bids_mode=BIDS_MODE, user_name=user_name)

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

@app.route('/qa')
@require_qa_directory
def render_montage_direct():
    """Direct QA for standard mode - qa_directory is the target containing PNG files."""
    qa_directory = get_qa_directory()
    bids_mode = session.get('bids_mode', False)
    user_name = session.get('user_name', '')

    print(f"Loading images from: {qa_directory} (Standard mode)")

    # Use qa_directory directly as the target
    pipeline_path = Path(qa_directory)

    # Check write permissions before proceeding
    can_write, file_issues, files_missing = check_write_permissions(pipeline_path)
    if not can_write:
        return render_template('permission_error.html',
                               clicked_path='',
                               pipeline='',
                               pipeline_path=str(pipeline_path),
                               file_issues=file_issues,
                               files_missing=files_missing)

    pngs = [str(x.relative_to(qa_directory)) for x in pipeline_path.glob('**/*.png')]
    pngs = sorted(pngs, key=str.lower)  # Case-insensitive sort for consistent ordering

    # For direct mode, pngs_files are the same as pngs (relative to qa_directory)
    pngs_files = pngs

    # Detect storage format and handle accordingly
    db_path = get_db_path(pipeline_path)
    storage_format = detect_storage_format(pipeline_path)

    if storage_format == 'json':
        # Existing JSON but no DB - check format first, then migrate
        json_path = pipeline_path / 'QA.json'
        with open(json_path, 'r') as f:
            json_dict = json.load(f)

        existing_format = detect_json_format(json_dict)
        if existing_format != 'empty' and existing_format != 'unknown':
            if existing_format == 'bids':
                # Standard mode but existing data is BIDS format
                return render_template('mode_mismatch.html',
                                       clicked_path='',
                                       pipeline='',
                                       selected_mode='flat',
                                       existing_format=existing_format,
                                       conversion_error=None)

        print("Migrating existing QA.json to SQLite database...")
        migrate_json_to_sqlite(pipeline_path, bids_mode=False)

    elif storage_format == 'none':
        # No existing data - create empty database
        print("Creating new QA database...")
        create_empty_database(pipeline_path, pngs_files, bids_mode=False)

    # Sync any new PNGs with database
    sync_pngs_with_db(db_path, pngs_files, bids_mode=False)

    # Store db_path in session for update_single_qa
    session['db_path'] = str(db_path)
    session['bids_mode'] = bids_mode

    # Build json_dict from database for frontend template (maintains existing API)
    json_dict = build_json_dict_from_db(db_path, bids_mode=False)

    # Use database key order for consistent ordering
    pngs_files = list(json_dict.keys())

    # For direct mode, image_paths are the same as pngs_files
    image_paths = pngs_files

    return render_template('montage.html',
                           clicked_path='',
                           pipeline='',
                           image_paths=image_paths,
                           json_dict=json_dict,
                           bids_mode=bids_mode,
                           user_name=user_name,
                           qa_directory=qa_directory)

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
        return render_template('permission_error.html',
                               clicked_path=clicked_path,
                               pipeline=pipeline,
                               pipeline_path=str(pipeline_path),
                               file_issues=file_issues,
                               files_missing=files_missing)

    pngs = [str(x.relative_to(qa_directory)) for x in pipeline_path.glob('**/*.png')]
    pngs = sorted(pngs, key=str.lower)  # Case-insensitive sort for consistent ordering

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

    # Detect storage format and handle accordingly
    db_path = get_db_path(pipeline_path)
    storage_format = detect_storage_format(pipeline_path)

    if storage_format == 'json':
        # Existing JSON but no DB - check format first, then migrate
        json_path = pipeline_path / 'QA.json'
        with open(json_path, 'r') as f:
            json_dict = json.load(f)

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

        print("Migrating existing QA.json to SQLite database...")
        migrate_json_to_sqlite(pipeline_path, bids_mode)

    elif storage_format == 'none':
        # No existing data - create empty database
        print("Creating new QA database...")
        create_empty_database(pipeline_path, pngs_files, bids_mode)

    # Sync any new PNGs with database
    sync_pngs_with_db(db_path, pngs_files, bids_mode)

    # Store db_path in session for update_single_qa
    session['db_path'] = str(db_path)
    session['bids_mode'] = bids_mode

    # Build json_dict from database for frontend template (maintains existing API)
    json_dict = build_json_dict_from_db(db_path, bids_mode)

    # For non-BIDS mode, use database key order for consistent ordering
    if not bids_mode:
        pngs_files = list(json_dict.keys())

    # Construct image_paths from the final pngs_files order
    prefix = clicked_path + '/' + pipeline + '/'
    image_paths = [prefix + f for f in pngs_files]

    return render_template('montage.html',
                           clicked_path=clicked_path,
                           pipeline=pipeline,
                           image_paths=image_paths,
                           json_dict=json_dict,
                           bids_mode=bids_mode,
                           user_name=user_name,
                           qa_directory=qa_directory)

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

@app.route('/qa/<path:image_filename>')
@require_qa_directory
def serve_image_direct(image_filename):
    """
    Serve image files for standard mode (direct QA without dataset/pipeline structure).
    """
    qa_directory = get_qa_directory()

    # Construct the full path to the image file (directly under qa_directory)
    image_path = os.path.join(qa_directory, image_filename)

    # Check if the image file exists
    if os.path.isfile(image_path):
        return send_file(image_path, mimetype='image/png')
    else:
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
    This function is called to update the QA JSON and CSV with the new QA status and reason
    """
    # Get json_path and bids_mode from session
    json_path_str = session.get('json_path')
    if not json_path_str:
        return jsonify({'status': 'error', 'message': 'No active QA session'}), 400
    json_path = Path(json_path_str)
    bids_mode = session.get('bids_mode', False)

    # Get the JSON data from the request
    nested_dict = request.json

    # Push the changes of the json file
    save_json_file(json_path, nested_dict)

    # Also update the csv file (with appropriate mode)
    _ = convert_json_to_csv(nested_dict, json_path.parent, bids_mode=bids_mode)

    # Return a JSON response with the updated dictionary
    return jsonify({'status': 'success', 'updatedDict': nested_dict})


@app.route('/update_single_qa', methods=['POST'])
def update_single_qa():
    """
    Update a single QA entry incrementally.
    Now uses SQLite database for efficient single-row updates.

    Expected JSON payload:
    {
        "key_path": ["filename.png"] or ["sub-001", "ses-01", ...],
        "data": {
            "QA_status": "yes",
            "reason": "",
            "user": "reviewer",
            "date": "2024-07-10 00:09:13",
            "duration": 45  // non-BIDS only
        }
    }
    """
    db_path_str = session.get('db_path')
    if not db_path_str:
        return jsonify({'status': 'error', 'message': 'No active QA session'}), 400

    db_path = Path(db_path_str)
    bids_mode = session.get('bids_mode', False)

    request_data = request.json
    key_path = request_data.get('key_path', [])
    entry_data = request_data.get('data', {})

    if not key_path:
        return jsonify({'status': 'error', 'message': 'key_path is required'}), 400

    try:
        # Convert key_path to key_data dict for database lookup
        if bids_mode:
            key_data = {'sub': key_path[0] if key_path else '', 'ses': '', 'acq': '', 'run': ''}
            for k in key_path[1:]:
                if k and k.startswith('ses-'):
                    key_data['ses'] = k
                elif k and k.startswith('acq-'):
                    key_data['acq'] = k
                elif k and k.startswith('run-'):
                    key_data['run'] = k
        else:
            key_data = {'filename': key_path[0]}

        # Update the database entry (SQLite handles concurrency)
        db_update_qa_entry(db_path, key_data, entry_data, bids_mode)

        return jsonify({'status': 'success'})

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/close_session', methods=['POST'])
def close_session():
    """
    Close database connection and checkpoint WAL when user leaves QA page.
    Called via navigator.sendBeacon() on page unload to ensure WAL files
    are properly cleaned up after rapid navigation.
    """
    db_path_str = session.get('db_path')
    if db_path_str:
        close_pooled_connection(db_path_str)
    return jsonify({'status': 'success'})


@app.route('/export_qa/<path:clicked_path>/<path:pipeline>', methods=['POST'])
@require_qa_directory
def export_qa(clicked_path, pipeline):
    """
    Export QA data to JSON and CSV files.
    Called from both root.html (dataset selection) and montage.html (during QA).

    Handles three scenarios:
    1. SQLite exists: Export directly
    2. Only JSON exists: Migrate to SQLite first, then export
    3. Nothing exists: Create empty database first, then export
    """
    qa_directory = get_qa_directory()
    bids_mode = session.get('bids_mode', False)
    pipeline_path = Path(qa_directory) / clicked_path / pipeline
    db_path = get_db_path(pipeline_path)

    storage_format = detect_storage_format(pipeline_path)

    try:
        if storage_format == 'none':
            # Get PNG files and create empty database first
            pngs = [str(x.relative_to(qa_directory)) for x in pipeline_path.glob('**/*.png')]
            prefix = clicked_path + '/' + pipeline + '/'
            pngs_files = [x[len(prefix):] for x in pngs]

            # Validate BIDS compliance if in BIDS mode
            if bids_mode:
                compliant, non_compliant = validate_bids_compliance(pngs_files)
                if non_compliant:
                    return jsonify({
                        'status': 'error',
                        'message': f'{len(non_compliant)} files are not BIDS-compliant'
                    }), 400

            create_empty_database(pipeline_path, pngs_files, bids_mode)

        elif storage_format == 'json':
            # Migrate JSON to SQLite first
            migrate_json_to_sqlite(pipeline_path, bids_mode)

        # Export from database
        export_all(db_path, pipeline_path, bids_mode)
        return jsonify({'status': 'success', 'message': 'Exported QA.json and QA.csv'})

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/export_qa_direct', methods=['POST'])
@require_qa_directory
def export_qa_direct():
    """
    Export QA data for standard mode (no dataset/pipeline structure).
    """
    qa_directory = get_qa_directory()
    bids_mode = session.get('bids_mode', False)
    pipeline_path = Path(qa_directory)
    db_path = get_db_path(pipeline_path)

    storage_format = detect_storage_format(pipeline_path)

    try:
        if storage_format == 'none':
            # Get PNG files and create empty database first
            pngs = [str(x.relative_to(qa_directory)) for x in pipeline_path.glob('**/*.png')]
            pngs_files = pngs  # For direct mode, no prefix stripping needed
            create_empty_database(pipeline_path, pngs_files, bids_mode)

        elif storage_format == 'json':
            # Migrate JSON to SQLite first
            migrate_json_to_sqlite(pipeline_path, bids_mode)

        # Export from database
        export_all(db_path, pipeline_path, bids_mode)
        return jsonify({'status': 'success', 'message': 'Exported QA.json and QA.csv'})

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


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
    print("  Authors: Michael Kim, Yihao Liu")
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
