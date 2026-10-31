"""Verify local SQLite backups before importing/removing legacy USB metadata."""
import hashlib
import sqlite3
import os
import shutil
from pathlib import Path
import patient_store


class MigrationError(Exception):
    pass


def file_digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def migrate(legacy_root, local_root, volume, backup_root):
    source = legacy_root / 'patient-records.sqlite3'
    if not source.is_file():
        return
    if source.resolve() == (local_root / source.name).resolve():
        raise MigrationError('Local and legacy storage cannot be the same')
    if any(Path(str(source) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise MigrationError('Close the older recorder before migrating USB records')
    signature = file_digest(source)
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = backup_root / (signature + '.sqlite3')
    original = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    try:
        original.row_factory = sqlite3.Row
        if original.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise MigrationError('Legacy patient database needs manual review')
        if not backup.exists():
            copy = sqlite3.connect(backup)
            try:
                original.backup(copy)
            finally:
                copy.close()
    finally:
        original.close()
    snapshot = sqlite3.connect(backup.resolve().as_uri() + '?mode=ro', uri=True)
    snapshot.row_factory = sqlite3.Row
    try:
        if snapshot.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise MigrationError('Local migration backup could not be verified')
        records = snapshot.execute('SELECT * FROM patient_records ORDER BY rowid').fetchall()
        tables = {row[0] for row in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        archives = snapshot.execute('SELECT * FROM patient_lifecycle').fetchall() if 'patient_lifecycle' in tables else []
        generated = snapshot.execute('SELECT * FROM generated_patient_ids').fetchall() if 'generated_patient_ids' in tables else []
    finally:
        snapshot.close()
    local = patient_store.connect(local_root)
    try:
        with local:
            local.execute('BEGIN IMMEDIATE')
            existing_ids = {row[0].casefold() for row in local.execute('SELECT DISTINCT patient_id FROM patient_records')}
            source_ids = {row['id'] for row in records}
            shared_patients = {row['patient_id'].casefold() for row in local.execute('SELECT id,patient_id FROM patient_records')
                               if row['id'] in source_ids}
            for row in records:
                existing = local.execute('SELECT * FROM patient_records WHERE id=?', (row['id'],)).fetchone()
                if existing:
                    if any(existing[key] != row[key] for key in ('patient_id', 'details', 'created_at')):
                        raise MigrationError('Conflicting patient history requires manual review')
                    continue
                # Distinct databases must not silently merge two families sharing an ID.
                if row['patient_id'].casefold() in existing_ids:
                    if row['patient_id'].casefold() not in shared_patients:
                        raise MigrationError('Patient ID collision during migration requires manual review')
                    latest = local.execute('SELECT max(created_at) FROM patient_records WHERE patient_id=?', (row['patient_id'],)).fetchone()[0]
                    if latest is not None and row['created_at'] < latest:
                        raise MigrationError('Older patient history merge requires manual review')
                local.execute('''INSERT INTO patient_records (id,patient_id,details,created_at,recording,recording_volume)
                                 VALUES (?,?,?,?,?,?)''', (row['id'], row['patient_id'], row['details'], row['created_at'], row['recording'], volume))
            for row in archives:
                if row['patient_id'].casefold() not in existing_ids:
                    local.execute('INSERT OR IGNORE INTO patient_lifecycle VALUES (?,?)', (row['patient_id'], row['archived_at']))
            for row in generated:
                local.execute('INSERT OR IGNORE INTO generated_patient_ids VALUES (?)', (row['patient_id'],))
            for row in records:
                if local.execute('SELECT 1 FROM patient_records WHERE id=?', (row['id'],)).fetchone() is None:
                    raise MigrationError('Patient history import could not be verified')
    finally:
        local.close()
    if file_digest(source) != signature or any(Path(str(source) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise MigrationError('USB database changed during migration; original retained')
    source.unlink()  # Only this verified metadata file; never a video or folder tree.


def migrate_exports(usb_root, local_root):
    source_root = usb_root / '_exports'
    if not source_root.is_dir():
        return
    for source in source_root.iterdir():
        if not source.is_file():
            continue
        signature = file_digest(source)
        local_root.mkdir(parents=True, exist_ok=True)
        target = local_root / (signature + '.export')
        if not target.exists():
            temporary = target.with_suffix('.tmp')
            with source.open('rb') as original, temporary.open('wb') as output:
                shutil.copyfileobj(original, output, 1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            if file_digest(temporary) != signature:
                raise MigrationError('Legacy export could not be copied')
            temporary.replace(target)
        if file_digest(target) != signature or file_digest(source) != signature:
            raise MigrationError('Legacy export changed during migration')
        source.unlink()
