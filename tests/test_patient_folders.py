from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
import patient_folders
import test_workflows
import test_album
import app as recorder


class FolderNames(unittest.TestCase):
    def test_name_and_id_with_windows_safe_characters(self):
        self.assertEqual(patient_folders.folder_name('MOL-1234','Jane Smith'),'MOL-1234 - Jane Smith')
        self.assertEqual(patient_folders.folder_name('MOL-1234',' Jane / Smith: <Jones>? '),'MOL-1234 - Jane Smith Jones')
        self.assertEqual(patient_folders.folder_name('MOL-1234','..\\../../Jane\x00\nSmith'),'MOL-1234 - Jane Smith')
        self.assertEqual(patient_folders.folder_name('MOL-1234',"Zoë O'Neil"),"MOL-1234 - Zoë O'Neil")

    def test_missing_name_and_long_unicode_name(self):
        for name in ['', '  ', '..', '<>:/\\?*']:
            self.assertEqual(patient_folders.folder_name('MOL-1234',name),'MOL-1234')
        label=patient_folders.folder_name('A'*64,'😀'*200)
        self.assertTrue(label.startswith('A'*64+' - '))
        self.assertLessEqual(len(label.encode('utf-16-le'))//2,96)
        self.assertFalse(label.endswith((' ','.')))

    def test_identity_supports_new_and_legacy_folders(self):
        self.assertEqual(patient_folders.identity('MOL-A-B - Jane - Smith'),('MOL-A-B','Jane - Smith'))
        self.assertEqual(patient_folders.identity('MOL-A-B'),('MOL-A-B',''))
        for invalid in ['../escape','with spaces','']:
            with self.assertRaises(ValueError):patient_folders.folder_name(invalid,'Jane')

    def test_new_and_legacy_folders_reserve_the_patient_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'Patients'/'MOL-1234 - Jane Smith').mkdir(parents=True)
            (root/'Patients'/'MOL-5678').mkdir()
            self.assertTrue(patient_folders.id_exists(root,'mol-1234'))
            self.assertTrue(patient_folders.id_exists(root,'MOL-5678'))
            self.assertFalse(patient_folders.id_exists(root,'MOL-9999'))


class NamedFolderWorkflows(unittest.TestCase):
    setUp=test_workflows.Workflows.setUp
    post=test_workflows.Workflows.post
    start=test_workflows.Workflows.start
    output=test_workflows.Workflows.output
    simulate_restart=test_workflows.Workflows.simulate_restart
    snapshot=test_album.Album.snapshot

    def test_video_and_snapshot_share_named_folder_and_album(self):
        response=self.start(name='Example Patient')
        self.assertEqual(response.status_code,200,response.json)
        folder=self.root/'Patients'/'DUMMY - Example Patient'
        self.assertEqual(recorder.state['destination'],folder)
        image=self.snapshot(patient_id='OTHER',name='Edited form')
        self.assertEqual(image.status_code,200,image.json)
        self.assertEqual(recorder.selected_media(image.json['key'])['path'].parent,folder.resolve())
        self.output()
        self.assertEqual(self.post('/api/stop').status_code,200)
        self.assertEqual(len(list(folder.glob('*.mp4'))),1)
        self.assertEqual(len(list(folder.glob('*.png'))),1)
        album=self.post('/api/album').json['albums']
        self.assertEqual(len(album),1)
        self.assertEqual(album[0]['patient_id'],'DUMMY')
        self.assertEqual(album[0]['patient_name'],'Example Patient')
        self.assertEqual({file['kind'] for file in album[0]['files']},{'image','video'})

    def test_idle_snapshot_then_video_share_same_folder(self):
        response=self.snapshot()
        self.assertEqual(response.status_code,200,response.json)
        image=recorder.selected_media(response.json['key'])['path']
        self.assertEqual(self.start(name='Example Patient').status_code,200)
        self.assertEqual(recorder.state['destination'].resolve(),image.parent)

    def test_recovery_uses_original_saved_name_even_after_later_edit(self):
        self.start(name='Original Family')
        folder=recorder.state['destination'];source=self.output()
        record=recorder.state['record_id']
        recorder.patient_store.save(self.data_root,'DUMMY',{'name':'Later Name','dob':'','scan':'','notes':''},record,check_duplicate=True)
        self.simulate_restart();recorder.recover_session()
        self.assertFalse(recorder.state['recovery_blocked'])
        self.assertEqual(recorder.state['pending'],source)
        self.assertEqual(recorder.state['destination'],folder)
        self.assertEqual(self.post('/api/stop').status_code,200)
        self.assertEqual(len(list(folder.glob('*.mp4'))),1)
        self.assertFalse((self.root/'Patients'/'DUMMY - Later Name').exists())

    def test_legacy_recovery_retains_id_only_destination(self):
        self.start(name='Example Patient')
        self.output()
        legacy=self.root/'Patients'/'DUMMY';legacy.mkdir()
        recorder.state.update(destination=legacy,folder_version=0)
        recorder.persist_session()
        saved=json.loads(recorder.RECOVERY_POINTER.read_text())
        saved['data'].pop('folder_version')
        recorder.RECOVERY_POINTER.write_text(json.dumps(saved))
        self.simulate_restart();recorder.recover_session()
        self.assertEqual(recorder.state['destination'],legacy)
        self.assertEqual(self.post('/api/stop').status_code,200)
        self.assertEqual(len(list(legacy.glob('*.mp4'))),1)

    def test_named_folder_recovery_after_file_was_moved_before_linking(self):
        self.start(name='Example Patient')
        source=self.output();folder=recorder.state['destination']
        target=folder/'retained.mp4'
        recorder.state.update(active=False,pending=source,planned=target)
        recorder.persist_session();source.rename(target)
        self.simulate_restart();recorder.recover_session()
        self.assertEqual(recorder.state['pending'],target)
        self.assertTrue(recorder.state['filed'])
        self.assertEqual(self.post('/api/stop').status_code,200)
        self.assertEqual(len(list(folder.glob('*.mp4'))),1)
        details=recorder.patient_store.for_recording(self.data_root,str(target.relative_to(self.root)),'TESTVOL')
        self.assertEqual(details['name'],'Example Patient')

    def test_legacy_files_are_not_renamed_or_overwritten(self):
        old=self.root/'Patients'/'DUMMY';old.mkdir(parents=True)
        video=old/'old.mp4';video.write_bytes(b'existing video')
        self.start(name='Example Patient');self.output()
        self.assertEqual(self.post('/api/stop').status_code,200)
        self.assertTrue(old.is_dir())
        self.assertEqual(video.read_bytes(),b'existing video')
        album=self.post('/api/album').json['albums']
        self.assertEqual(len(album),1)
        self.assertEqual(album[0]['patient_id'],'DUMMY')
        self.assertEqual(len(album[0]['files']),2)

    def test_name_change_preserves_existing_media_and_patient_grouping(self):
        first=self.snapshot(name='Original Name')
        old=recorder.selected_media(first.json['key'])['path']
        second=self.snapshot(name='New Name',expected_record_id=first.json['record_id'])
        self.assertEqual(second.status_code,200,second.json)
        new=recorder.selected_media(second.json['key'])['path']
        self.assertNotEqual(old.parent,new.parent)
        self.assertTrue(old.exists())
        self.assertTrue(new.exists())
        albums=self.post('/api/album').json['albums']
        self.assertEqual(len(albums),1)
        self.assertEqual(len(albums[0]['files']),2)

    def test_id_generation_avoids_existing_named_folder(self):
        (self.root/'Patients'/'MOL-AAAAAAAA - Existing Name').mkdir(parents=True)
        with patch.object(recorder.patient_store.secrets,'token_hex',side_effect=['aaaaaaaa','bbbbbbbb']):
            generated=recorder.patient_store.generate_id(self.data_root,self.root)
        self.assertEqual(generated,'MOL-BBBBBBBB')
