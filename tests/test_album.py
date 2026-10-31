import base64
import io
import types
import unittest
from unittest.mock import Mock, patch
from PIL import Image
import test_workflows
import app as recorder


class Album(unittest.TestCase):
    # Share the hardware-isolated fixture with the recording workflow suite.
    def setUp(self):
        test_workflows.Workflows.setUp(self)
        self.gmail=Mock()
        self.gmail.status.return_value={'configured':True,'email':'clinic@example.com','message':'Configured'}
        self.gmail.revision.return_value='dummy-revision'
        patcher=patch.object(recorder,'get_gmail_client',return_value=self.gmail)
        patcher.start();self.addCleanup(patcher.stop)
    post=test_workflows.Workflows.post
    start=test_workflows.Workflows.start
    def snapshot(self, **extra):
        output=io.BytesIO()
        Image.new('RGB',(16,12),'pink').save(output,format='PNG')
        self.png=output.getvalue()
        self.obs.get_current_program_scene.return_value.current_program_scene_name='Ultrasound'
        self.obs.get_video_settings.return_value=types.SimpleNamespace(base_width=16,base_height=12)
        self.obs.get_source_screenshot.return_value.image_data='data:image/png;base64,'+base64.b64encode(self.png).decode()
        return self.post('/api/snapshots',{'patient_id':'DUMMY','name':'Example Patient',**extra})

    def test_snapshot_original_album_thumbnail_and_record_reuse(self):
        first=self.snapshot()
        self.assertEqual(first.status_code,200,first.json)
        second=self.snapshot(expected_record_id=first.json['record_id'])
        self.assertEqual(second.status_code,200,second.json)
        self.assertEqual(first.json['record_id'],second.json['record_id'])
        self.assertNotEqual(first.json['key'],second.json['key'])
        files=list((self.root/'Patients'/'DUMMY - Example Patient').glob('*.png'))
        self.assertEqual(len(files),2)
        self.assertEqual(files[0].read_bytes(),self.png)
        album=self.post('/api/album',{'q':'Example'}).json['albums']
        self.assertEqual(len(album),1)
        self.assertEqual(album[0]['patient_name'],'Example Patient')
        self.assertEqual(len(album[0]['files']),2)
        key=album[0]['files'][0]['key']
        with self.client.get('/image?id='+key,headers=self.headers) as original:
            self.assertEqual(original.data,self.png)
        thumbnail=self.client.get('/image?thumb=1&id='+key,headers=self.headers)
        self.assertEqual(thumbnail.mimetype,'image/jpeg')
        self.assertEqual(self.client.get('/image?id='+key).status_code,403)
        self.obs.get_source_screenshot.assert_called_with('Ultrasound','png',16,12,100)

    def test_active_snapshot_uses_recording_patient(self):
        self.start(name='Recording Patient')
        response=self.snapshot(patient_id='OTHER',name='Changed form')
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['patient_id'],'DUMMY')
        self.assertFalse((self.root/'Patients'/'OTHER').exists())
        item=recorder.selected_media(response.json['key'])
        self.assertEqual(item['details']['name'],'Recording Patient')

    def test_snapshot_link_failure_retains_image(self):
        with patch.object(recorder.media_store,'link_image',side_effect=OSError('private path')):
            response=self.snapshot()
        self.assertEqual(response.status_code,503)
        self.assertIn('retained',response.json['error'])
        self.assertNotIn('private path',response.json['error'])
        self.assertEqual(len(self.post('/api/album').json['albums'][0]['files']),1)

    def test_image_delete_confirmation_and_changed_file_protection(self):
        key=self.snapshot().json['key']
        path=recorder.selected_media(key)['path']
        self.assertEqual(self.post('/api/media/delete',{'key':key}).status_code,400)
        path.write_bytes(b'changed')
        self.assertEqual(self.post('/api/media/delete',{'key':key,'confirm':True}).status_code,409)
        self.assertTrue(path.exists())
        key=self.post('/api/album').json['albums'][0]['files'][0]['key']
        self.assertEqual(self.post('/api/media/delete',{'key':key,'confirm':True}).status_code,200)
        self.assertFalse(path.exists())
        self.assertTrue((self.data_root/'patient-records.sqlite3').exists())

    def test_changed_usb_cannot_serve_or_share_image(self):
        key=self.snapshot().json['key']
        with patch.object(recorder,'volume_id',return_value='OTHER'):
            self.assertEqual(self.client.get('/image?id='+key,headers=self.headers).status_code,404)
            self.assertEqual(self.post('/api/gmail/prepare',self.draft_data(key)).status_code,400)

    def draft_data(self,key,**extra):
        return {'keys':[key],'recipient':'family@example.com','confirm':True,**extra}

    def test_web_gmail_prepare_has_opt_in_details_and_neutral_downloads(self):
        key=self.snapshot().json['key']
        response=self.post('/api/gmail/prepare',self.draft_data(key,body='Your images'))
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['body'],'Your images')
        self.assertNotIn('DUMMY',str(response.json))
        self.assertEqual(response.json['attachments'][0]['name'],'Ultrasound 01.png')
        response=self.post('/api/gmail/prepare',self.draft_data(key,fields=['name']))
        self.assertIn('Example Patient',response.json['body'])
        with self.client.get('/api/media/download?id='+key+'&n=1',headers=self.headers) as download:
            self.assertEqual(download.status_code,200)
            self.assertEqual(download.data,self.png)
            self.assertIn('Ultrasound 01.png',download.headers['Content-Disposition'])
        self.assertFalse(recorder.family_copy_lock.locked())

    def test_gmail_limit_rejected_before_connection_or_upload(self):
        path=self.root/'Patients'/'DUMMY'/'large.mp4'
        path.parent.mkdir(parents=True)
        with path.open('wb') as output:output.truncate(25_000_001)
        key=self.post('/api/album').json['albums'][0]['files'][0]['key']
        response=self.post('/api/gmail/prepare',self.draft_data(key))
        self.assertEqual(response.status_code,400)
        self.assertTrue(path.exists())
        self.assertFalse(recorder.family_copy_lock.locked())

    def test_snapshot_pending_save_and_draft_confirmation(self):
        recorder.state['pending']={'path':'pending'}
        self.assertEqual(self.snapshot().status_code,409)
        self.assertEqual(self.post('/api/gmail/prepare',{'confirm':False}).status_code,400)

    def test_gmail_rejects_mixed_patient_albums(self):
        first=self.snapshot().json['key']
        second=self.snapshot(patient_id='OTHER').json['key']
        response=self.post('/api/gmail/prepare',self.draft_data(first,keys=[first,second]))
        self.assertEqual(response.status_code,400)
        self.assertIn('one patient',response.json['error'])

    def test_album_prunes_old_keys_before_registering_current_files(self):
        self.snapshot()
        recorder.playback_files.update({str(i):self.root/'expired.mp4' for i in range(3001)})
        album=self.post('/api/album').json['albums']
        key=album[0]['files'][0]['key']
        self.assertEqual(recorder.selected_media(key)['kind'],'image')
        self.assertEqual(len(recorder.playback_files),1)

    def test_web_download_rejects_changed_files_and_missing_auth(self):
        key=self.snapshot().json['key']
        self.assertEqual(self.client.get('/api/media/download?id='+key).status_code,403)
        recorder.selected_media(key)['path'].write_bytes(b'changed')
        self.assertEqual(self.client.get('/api/media/download?id='+key,headers=self.headers).status_code,404)

    def test_gmail_setup_uses_app_password_without_oauth(self):
        html=self.client.get('/').get_data(as_text=True)
        self.assertTrue('Google app password' in html)
        self.assertTrue('Send email' in html)
        self.assertFalse('One-time Gmail setup' in html)
        self.assertFalse('gmail.compose' in html)
        self.assertEqual(self.post('/api/gmail/connect').status_code,404)
        self.assertEqual(self.post('/api/gmail/draft').status_code,404)

    def test_prepare_rejects_injected_recipient_and_releases_lock(self):
        key=self.snapshot().json['key']
        response=self.post('/api/gmail/prepare',self.draft_data(key,recipient='a@example.com\r\nBcc: b@example.com'))
        self.assertEqual(response.status_code,400)
        self.assertFalse(recorder.family_copy_lock.locked())
