"""Versioned patient details stored in the application's local data directory."""
import json
import secrets
import sqlite3
from datetime import datetime


class PatientValidationError(ValueError):
    pass


class PatientChangedError(ValueError):
    pass


class DuplicatePatientError(ValueError):
    def __init__(self, record):
        self.record = record


class ArchivedPatientError(ValueError):
    pass


class ArchiveRequiredError(ValueError):
    pass


def change_archive(root, patient_id, expected_id, archived):
    connection = connect(root)
    try:
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            latest = connection.execute('SELECT id FROM patient_records WHERE patient_id=? ORDER BY rowid DESC LIMIT 1', (patient_id,)).fetchone()
            if latest is None or latest['id'] != expected_id:
                raise PatientChangedError()
            if archived:
                connection.execute('INSERT OR IGNORE INTO patient_lifecycle VALUES (?, ?)',
                                   (patient_id, datetime.now().isoformat(timespec='seconds')))
            else:
                connection.execute('DELETE FROM patient_lifecycle WHERE patient_id=?', (patient_id,))
    finally:
        connection.close()


def delete(root, patient_id, expected_id):
    """Delete all saved versions, leaving recording files untouched."""
    connection = connect(root)
    try:
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            latest = connection.execute('SELECT id FROM patient_records WHERE patient_id=? ORDER BY rowid DESC LIMIT 1',
                                        (patient_id,)).fetchone()
            if latest is None or latest['id'] != expected_id:
                raise PatientChangedError()
            if connection.execute('SELECT 1 FROM patient_lifecycle WHERE patient_id=?', (patient_id,)).fetchone() is None:
                raise ArchiveRequiredError()
            connection.execute('PRAGMA secure_delete=ON')
            connection.execute('DELETE FROM patient_records WHERE patient_id=?', (patient_id,))
            connection.execute('DELETE FROM patient_lifecycle WHERE patient_id=?', (patient_id,))
    finally:
        connection.close()


def validate_details(data):
    details = {}
    for key, limit in [('name', 200), ('dob', 10), ('scan', 100), ('notes', 10000)]:
        value = data.get(key, '')
        if not isinstance(value, str) or len(value) > limit:
            raise PatientValidationError(f'Invalid {key}; maximum {limit} characters')
        details[key] = value.strip()
    if details['dob']:
        try:
            datetime.strptime(details['dob'], '%d/%m/%Y')
        except ValueError:
            raise PatientValidationError('Enter baby’s date of birth as a valid date in DD/MM/YYYY format') from None
    return details


def connect(root):
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / 'patient-records.sqlite3', timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA synchronous=FULL')
        connection.execute('''CREATE TABLE IF NOT EXISTS patient_records (
            id TEXT PRIMARY KEY, patient_id TEXT NOT NULL COLLATE NOCASE,
            details TEXT NOT NULL, created_at TEXT NOT NULL, recording TEXT)''')
        connection.execute('CREATE INDEX IF NOT EXISTS patient_lookup ON patient_records(patient_id)')
        connection.execute('''CREATE TABLE IF NOT EXISTS patient_lifecycle (
            patient_id TEXT PRIMARY KEY COLLATE NOCASE, archived_at TEXT NOT NULL)''')
        connection.execute('''CREATE TABLE IF NOT EXISTS generated_patient_ids (
            patient_id TEXT PRIMARY KEY COLLATE NOCASE)''')
        if 'recording_volume' not in {row[1] for row in connection.execute('PRAGMA table_info(patient_records)')}:
            connection.execute('ALTER TABLE patient_records ADD COLUMN recording_volume TEXT')
        connection.commit()
        return connection
    except Exception:
        connection.close()
        raise


def generate_id(root, recording_root=None):
    """Reserve an anonymous ID without creating an empty patient record."""
    connection = connect(root)
    try:
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            for _ in range(100):
                candidate = 'MOL-' + secrets.token_hex(4).upper()
                if connection.execute('SELECT 1 FROM patient_records WHERE patient_id=?', (candidate,)).fetchone():
                    continue
                if recording_root is not None and (recording_root / 'Patients' / candidate).exists():
                    continue
                try:
                    connection.execute('INSERT INTO generated_patient_ids VALUES (?)', (candidate,))
                    return candidate
                except sqlite3.IntegrityError:
                    continue
            raise ValueError('Could not reserve a patient ID')
    finally:
        connection.close()


def save(root, patient_id, details, expected_id=None, check_duplicate=False):
    record_id = secrets.token_hex(16)
    connection = connect(root)
    try:
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            if connection.execute('SELECT 1 FROM patient_lifecycle WHERE patient_id=?', (patient_id,)).fetchone():
                raise ArchivedPatientError()
            latest = connection.execute('SELECT * FROM patient_records WHERE patient_id=? ORDER BY rowid DESC LIMIT 1', (patient_id,)).fetchone()
            if check_duplicate and latest and expected_id != latest['id']:
                raise DuplicatePatientError({'id': latest['id'], 'patient_id': latest['patient_id'],
                                             'name': json.loads(latest['details']).get('name', '')})
            if check_duplicate and not latest and expected_id:
                raise PatientChangedError()
            connection.execute('INSERT INTO patient_records (id,patient_id,details,created_at,recording) VALUES (?, ?, ?, ?, NULL)',
                               (record_id, patient_id, json.dumps(details, ensure_ascii=False),
                                datetime.now().isoformat(timespec='seconds')))
    finally:
        connection.close()
    return record_id


def attach_recording(root, record_id, relative_path, volume=None):
    connection = connect(root)
    try:
        with connection:
            row = connection.execute('SELECT recording FROM patient_records WHERE id=?', (record_id,)).fetchone()
            if row is None or row['recording'] not in (None, relative_path):
                raise ValueError('Patient recording link cannot be replaced')
            connection.execute('UPDATE patient_records SET recording=?,recording_volume=? WHERE id=?', (relative_path, volume, record_id))
    finally:
        connection.close()


def delete_recording(root, relative_path, path, volume=None):
    """Keep patient history; remove only the confirmed video and its link."""
    if not (root / 'patient-records.sqlite3').is_file():
        path.unlink()
        return
    connection = connect(root)
    try:
        with connection:
            connection.execute('UPDATE patient_records SET recording=NULL WHERE recording=? AND recording_volume IS ?', (relative_path, volume))
            path.unlink()  # Failure rolls back the database change.
    finally:
        connection.close()


def for_recording(root, relative_path, volume=None):
    """Use the scan's saved snapshot, never a patient's newer details."""
    if not (root / 'patient-records.sqlite3').is_file():
        return None
    connection = connect(root)
    try:
        row = connection.execute('''SELECT * FROM patient_records WHERE recording=? AND recording_volume IS ?
                                    ORDER BY rowid DESC LIMIT 1''', (relative_path, volume)).fetchone()
        if row is None:
            return None
        return dict(patient_id=row['patient_id'], created_at=row['created_at'],
                    **json.loads(row['details']))
    finally:
        connection.close()


def recording_names(root, volume=None):
    if not (root / 'patient-records.sqlite3').is_file():
        return {}
    connection = connect(root)
    try:
        rows = connection.execute('''SELECT recording, patient_id,
                                    json_extract(details, '$.name') AS name
                                    FROM patient_records WHERE recording IS NOT NULL AND recording_volume IS ?
                                    ORDER BY rowid''', (volume,)).fetchall()
        return {row['recording']: {'patient_id': row['patient_id'], 'name': row['name'] or ''}
                for row in rows}
    finally:
        connection.close()


def matches_record(root, record_id, patient_id):
    if not (root / 'patient-records.sqlite3').is_file():
        return False
    connection = connect(root)
    try:
        return connection.execute('SELECT 1 FROM patient_records WHERE id=? AND patient_id=?',
                                  (record_id, patient_id)).fetchone() is not None
    finally:
        connection.close()


def find(root, query='', patient_id=None, include_archived=False):
    connection = connect(root)
    try:
        if patient_id is not None:
            rows = connection.execute('''SELECT * FROM patient_records WHERE patient_id=?
                                         ORDER BY rowid DESC LIMIT 200''', (patient_id,)).fetchall()
        else:
            # Search only each patient's latest details, retaining all earlier versions.
            rows = connection.execute('''SELECT * FROM patient_records
                WHERE rowid IN (SELECT max(rowid) FROM patient_records GROUP BY patient_id)
                AND (? OR patient_id NOT IN (SELECT patient_id FROM patient_lifecycle))
                AND (instr(lower(patient_id), lower(?)) > 0
                     OR instr(lower(json_extract(details, '$.name')), lower(?)) > 0)
                ORDER BY rowid DESC LIMIT 200''', (include_archived, query, query)).fetchall()
        archived_ids = {row[0].casefold() for row in connection.execute('SELECT patient_id FROM patient_lifecycle')}
        return [dict(id=row['id'], patient_id=row['patient_id'],
                     archived=row['patient_id'].casefold() in archived_ids,
                     created_at=row['created_at'], recording=row['recording'],
                     **json.loads(row['details'])) for row in rows]
    finally:
        connection.close()
