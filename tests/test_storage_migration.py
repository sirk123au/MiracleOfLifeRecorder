import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import patient_store
import recording_recovery
import storage_migration


class StorageMigration(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.usb = self.root / 'usb'
        self.local = self.root / 'appdata' / 'patients'
        self.backups = self.root / 'appdata' / 'migration-backups'

    def create_legacy_patient(self):
        details = {'name': 'Example Family', 'dob': '01/01/2030', 'scan': 'Gender Scan', 'notes': 'Preserve scan notes'}
        record = patient_store.save(self.usb, 'OLD', details)
        patient_store.attach_recording(self.usb, record, 'Patients/OLD/scan.mp4')
        patient_store.change_archive(self.usb, 'OLD', record, True)
        generated = patient_store.generate_id(self.usb)
        video = self.usb / 'Patients' / 'OLD' / 'scan.mp4'
        video.parent.mkdir(parents=True)
        video.write_bytes(b'original video')
        return record, generated, video

    def test_verified_migration_keeps_history_archives_reservations_and_video_links(self):
        record, generated, video = self.create_legacy_patient()
        storage_migration.migrate(self.usb, self.local, 'VOLUME1', self.backups)
        self.assertFalse((self.usb / 'patient-records.sqlite3').exists())
        self.assertEqual(video.read_bytes(), b'original video')
        records = patient_store.find(self.local, include_archived=True)
        self.assertEqual(records[0]['id'], record)
        self.assertTrue(records[0]['archived'])
        self.assertEqual(records[0]['notes'], 'Preserve scan notes')
        self.assertEqual(patient_store.for_recording(self.local, 'Patients/OLD/scan.mp4', 'VOLUME1')['name'], 'Example Family')
        self.assertIsNone(patient_store.for_recording(self.local, 'Patients/OLD/scan.mp4', 'OTHER-VOLUME'))
        self.assertEqual(len(list(self.backups.glob('*.sqlite3'))), 1)
        connection = patient_store.connect(self.local)
        try:
            self.assertIsNotNone(connection.execute('SELECT 1 FROM generated_patient_ids WHERE patient_id=?', (generated,)).fetchone())
        finally:
            connection.close()

    def test_conflicting_patient_ids_keep_both_original_databases(self):
        self.create_legacy_patient()
        patient_store.save(self.local, 'OLD', {'name': 'Different Family'})
        with self.assertRaises(storage_migration.MigrationError):
            storage_migration.migrate(self.usb, self.local, 'VOLUME1', self.backups)
        self.assertTrue((self.usb / 'patient-records.sqlite3').exists())
        self.assertEqual(patient_store.find(self.local)[0]['name'], 'Different Family')

    def test_cleanup_failure_is_retryable_without_duplicate_or_rearchived_records(self):
        self.create_legacy_patient()
        with patch.object(Path, 'unlink', side_effect=PermissionError()):
            with self.assertRaises(PermissionError):
                storage_migration.migrate(self.usb, self.local, 'VOLUME1', self.backups)
        records = patient_store.find(self.local, include_archived=True)
        patient_store.change_archive(self.local, 'OLD', records[0]['id'], False)
        storage_migration.migrate(self.usb, self.local, 'VOLUME1', self.backups)
        records = patient_store.find(self.local, include_archived=True)
        self.assertEqual(len(records), 1)
        self.assertFalse(records[0]['archived'])

    def test_incomplete_sqlite_transaction_never_gets_removed(self):
        self.create_legacy_patient()
        journal = self.usb / 'patient-records.sqlite3-journal'
        journal.write_bytes(b'needs SQLite recovery')
        with self.assertRaises(storage_migration.MigrationError):
            storage_migration.migrate(self.usb, self.local, 'VOLUME1', self.backups)
        self.assertTrue(journal.is_file())
        self.assertTrue((self.usb / 'patient-records.sqlite3').is_file())

    def test_legacy_recovery_metadata_moves_local_without_moving_video(self):
        staging = self.usb / '_staging' / ('a' * 32)
        staging.mkdir(parents=True)
        original = {'patient': 'OLD', 'test': False}
        (staging / 'session.json').write_text(json.dumps(original), encoding='utf-8')
        (staging / 'clip.mp4').write_bytes(b'video')
        pointer = self.root / 'appdata' / 'pending-session.json'
        pointer.parent.mkdir(parents=True)
        pointer.write_text(json.dumps({'session': staging.name}), encoding='utf-8')
        recording_recovery.migrate_legacy_journals(pointer, self.usb)
        recovered_staging, recovered = recording_recovery.read(pointer, self.usb)
        self.assertEqual(recovered_staging, staging)
        self.assertEqual(recovered, original)
        self.assertEqual(json.loads(pointer.read_text())['version'], 2)
        self.assertEqual([p.name for p in staging.iterdir()], ['clip.mp4'])

    def test_new_session_journal_never_writes_metadata_on_usb(self):
        staging = self.usb / '_staging' / ('b' * 32)
        staging.mkdir(parents=True)
        pointer = self.root / 'appdata' / 'pending-session.json'
        pointer.parent.mkdir()
        recording_recovery.begin(pointer, staging, {'patient': 'NEW'})
        self.assertEqual(list(staging.iterdir()), [])
        self.assertEqual(recording_recovery.read(pointer, self.usb)[1], {'patient': 'NEW'})

    def test_orphan_export_packages_move_to_appdata_after_verification(self):
        orphan = self.usb / '_exports' / 'old-package.tmp'
        orphan.parent.mkdir(parents=True)
        orphan.write_bytes(b'export package containing video and patient information')
        local = self.root / 'appdata' / 'recovery' / 'legacy-exports'
        storage_migration.migrate_exports(self.usb, local)
        self.assertFalse(orphan.exists())
        self.assertEqual(next(local.glob('*.export')).read_bytes(), b'export package containing video and patient information')
