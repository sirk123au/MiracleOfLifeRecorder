import hashlib
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch,Mock
import family_copy
import test_album
import test_workflows
import app as recorder


class MediaCopy(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.destination=self.root/'family';self.destination.mkdir()
        self.dest={'root':self.destination,'filesystem':'exFAT'}

    def item(self,name,data):
        path=self.root/name;path.write_bytes(data);info=path.stat()
        return {'path':path,'size':info.st_size,'fingerprint':(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)}

    def test_mixed_files_originals_existing_files_and_unique_folders(self):
        items=[self.item('private-id.mp4',b'video original'),self.item('private-id.png',b'image original')]
        existing=self.destination/'family-photo.jpg';existing.write_bytes(b'existing family photo')
        folder=family_copy.copy_selected(items,self.dest,Mock(),Mock())
        self.assertEqual((self.destination/folder/'Ultrasound 01.mp4').read_bytes(),b'video original')
        self.assertEqual((self.destination/folder/'Ultrasound 02.png').read_bytes(),b'image original')
        self.assertFalse((self.destination/folder/'patient-information.txt').exists())
        self.assertEqual(existing.read_bytes(),b'existing family photo')
        self.assertEqual(items[0]['path'].read_bytes(),b'video original')
        second=family_copy.copy_selected(items,self.dest,Mock(),Mock())
        self.assertNotEqual(folder,second)

    def test_bad_capacity_and_fat32_large_video_rejected_before_writes(self):
        item=self.item('video.mp4',b'video');item['size']=0x100000000
        with self.assertRaisesRegex(family_copy.CopyError,'FAT32'):
            family_copy.copy_selected([item],{**self.dest,'filesystem':'FAT32'},Mock(),Mock())
        self.assertEqual(list(self.destination.iterdir()),[])
        item['size']=5
        with patch.object(family_copy.shutil,'disk_usage',return_value=types.SimpleNamespace(free=0)):
            with self.assertRaisesRegex(family_copy.CopyError,'free space'):
                family_copy.copy_selected([item],self.dest,Mock(),Mock())
        self.assertEqual(list(self.destination.iterdir()),[])

    def test_changed_source_is_rejected_and_only_incomplete_folder_retained(self):
        item=self.item('video.mp4',b'original');item['path'].write_bytes(b'changed')
        with self.assertRaises(family_copy.CopyError):family_copy.copy_selected([item],self.dest,Mock(),Mock())
        self.assertTrue(all(path.name.endswith('.incomplete') for path in self.destination.iterdir()))
        self.assertEqual(item['path'].read_bytes(),b'changed')

    def test_verification_failure_never_marks_copy_complete(self):
        item=self.item('image.png',b'original')
        original=hashlib.sha256
        corrupt=Mock();corrupt.digest.return_value=b'wrong';corrupt.update.return_value=None
        with patch.object(family_copy.hashlib,'sha256',side_effect=[original(),corrupt]):
            with self.assertRaisesRegex(family_copy.CopyError,'verification'):
                family_copy.copy_selected([item],self.dest,Mock(),Mock())
        self.assertTrue(all(path.name.endswith('.incomplete') for path in self.destination.iterdir()))
        self.assertTrue(item['path'].exists())

    def test_disconnection_during_copy_never_finalizes_folder(self):
        item=self.item('image.png',b'original')
        validate=Mock(side_effect=[None,None,family_copy.CopyError('drive disconnected')])
        with self.assertRaisesRegex(family_copy.CopyError,'disconnected'):
            family_copy.copy_selected([item],self.dest,validate,Mock())
        self.assertTrue(all(path.name.endswith('.incomplete') for path in self.destination.iterdir()))


class FamilyMediaWorkflows(unittest.TestCase):
    def setUp(self):
        test_workflows.Workflows.setUp(self)
        recorder.family_copy_jobs.clear()
    post=test_workflows.Workflows.post
    start=test_workflows.Workflows.start
    output=test_workflows.Workflows.output
    snapshot=test_album.Album.snapshot

    def setup_copy(self):
        self.start(name='Example Patient');self.output();self.post('/api/stop')
        image=self.snapshot(expected_record_id=recorder.state['record_id'])
        self.assertEqual(image.status_code,200,image.json)
        files=self.post('/api/album').json['albums'][0]['files']
        self.keys=[file['key'] for file in files]
        self.family=self.root/'family-drive';self.family.mkdir()
        self.drive={'root':self.family,'serial':123,'label':'FAMILY','filesystem':'exFAT'}
        patcher=patch.object(recorder,'family_usb_drives',return_value=[self.drive]);patcher.start();self.addCleanup(patcher.stop)
        self.destination=self.post('/api/family-usb/drives').json['drives'][0]['key']

    def copy(self,**extra):
        inline=lambda target,daemon:types.SimpleNamespace(start=target)
        data={'keys':self.keys,'destination':self.destination,'request_id':'family-request-0123456789','confirm':True,**extra}
        with patch.object(recorder.threading,'Thread',side_effect=inline):return self.post('/api/family-usb/copy-media',data)

    def status(self,job='family-request-0123456789'):
        return self.client.get('/api/family-usb/job?id='+job,headers=self.headers).json

    def test_selected_images_and_video_copy_and_duplicate_clicks_are_safe(self):
        self.setup_copy()
        response=self.copy();self.assertEqual(response.status_code,202,response.json)
        result=self.status();self.assertEqual(result['state'],'complete',result)
        files=list((self.family/result['folder']).iterdir())
        self.assertEqual(len(files),2)
        self.assertEqual({path.suffix for path in files},{'.mp4','.png'})
        self.assertEqual(len(self.post('/api/album').json['albums'][0]['files']),2)
        again=self.copy();self.assertEqual(again.json,response.json)
        self.assertEqual(len(list(self.family.iterdir())),1)
        self.assertFalse(recorder.family_copy_lock.locked())

    def test_recording_same_drive_and_changed_volume_rejected(self):
        self.setup_copy();recorder.state['active']=True
        self.assertEqual(self.copy().status_code,409);recorder.state['active']=False
        self.drive['serial']=999
        self.assertEqual(self.copy().status_code,400)
        self.drive['serial']=123
        with patch.object(recorder,'volume_id',return_value='0000007B'):
            # Explicit source/destination serial equality is rejected even if listed.
            with self.assertRaises(family_copy.CopyError):
                recorder.validate_family_media([],[],self.drive,self.root,'0000007B')
        self.assertEqual(list(self.family.iterdir()),[])

    def test_mixed_patients_and_changed_files_rejected(self):
        self.setup_copy()
        image=self.snapshot(patient_id='OTHER')
        self.assertEqual(self.copy(keys=[self.keys[0],image.json['key']]).status_code,400)
        recorder.selected_media(self.keys[0])['path'].write_bytes(b'changed')
        self.assertEqual(self.copy().status_code,409)
        self.assertFalse(recorder.family_copy_lock.locked())

    def test_more_than_email_size_limit_can_copy(self):
        self.setup_copy()
        path=self.root/'Patients'/'LARGE'/'video.mp4';path.parent.mkdir()
        with path.open('wb') as output:output.truncate(25_000_001)
        key=self.post('/api/album',{'q':'LARGE'}).json['albums'][0]['files'][0]['key']
        response=self.copy(keys=[key]);self.assertEqual(response.status_code,202,response.json)
        result=self.status();self.assertEqual(result['state'],'complete',result)
        self.assertEqual(next((self.family/result['folder']).iterdir()).stat().st_size,25_000_001)

    def test_copy_failure_is_sanitized_and_incomplete_files_are_kept(self):
        self.setup_copy()
        with patch.object(family_copy.os,'fsync',side_effect=OSError('private patient path')):response=self.copy()
        self.assertEqual(response.status_code,202)
        result=self.status();self.assertEqual(result['state'],'error')
        self.assertNotIn('private patient',str(result))
        self.assertIn('.incomplete',result['message'])
        self.assertTrue(all(path.name.endswith('.incomplete') for path in self.family.iterdir()))
        self.assertFalse(recorder.family_copy_lock.locked())

    def test_active_job_can_be_found_and_mutations_require_auth(self):
        self.setup_copy()
        recorder.family_copy_jobs['active-0123456789']={'root':str(recorder.ROOT.resolve()),'state':'working','message':'Copying'}
        result=self.client.get('/api/family-usb/job',headers=self.headers).json
        self.assertEqual(result['job'],'active-0123456789')
        self.assertNotIn('root',result)
        self.assertEqual(self.client.get('/api/family-usb/job').status_code,403)
        self.assertEqual(self.client.post('/api/family-usb/copy-media',json={}).status_code,403)
        self.assertEqual(self.post('/api/family-usb/copy-media',{'confirm':False}).status_code,400)
