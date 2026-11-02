"""Snapshot links in APPDATA; only media bytes are written to the recording USB."""
import json
from datetime import datetime
import patient_store


def link_image(root,record_id,relative,volume):
    connection=patient_store.connect(root)
    try:
        with connection:
            if connection.execute('SELECT 1 FROM patient_records WHERE id=?',(record_id,)).fetchone() is None:
                raise ValueError('Patient record unavailable')
            connection.execute('INSERT INTO patient_images VALUES (?,?,?,?)',
                               (relative,volume,record_id,datetime.now().isoformat(timespec='seconds')))
    finally:connection.close()


def image_names(root,volume):
    if not (root/'patient-records.sqlite3').is_file():return {}
    connection=patient_store.connect(root)
    try:
        rows=connection.execute("""SELECT i.relative_path,p.patient_id,json_extract(p.details, '$.name') AS name FROM patient_images i
            JOIN patient_records p ON i.record_id=p.id WHERE i.volume=?""",(volume,)).fetchall()
        return {row['relative_path']:{'patient_id':row['patient_id'],'name':row['name'] or ''} for row in rows}
    finally:connection.close()


def image_details(root,relative,volume):
    if not (root/'patient-records.sqlite3').is_file():return None
    connection=patient_store.connect(root)
    try:
        row=connection.execute("""SELECT p.* FROM patient_images i JOIN patient_records p ON i.record_id=p.id
            WHERE i.relative_path=? AND i.volume=?""",(relative,volume)).fetchone()
        return dict(id=row['id'],patient_id=row['patient_id'],created_at=row['created_at'],**json.loads(row['details'])) if row else None
    finally:connection.close()


def delete_image(root,relative,volume,path):
    connection=patient_store.connect(root)
    try:
        with connection:
            connection.execute('DELETE FROM patient_images WHERE relative_path=? AND volume=?',(relative,volume))
            path.unlink()
    finally:connection.close()
