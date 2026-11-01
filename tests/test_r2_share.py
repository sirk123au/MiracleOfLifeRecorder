import io
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import r2_share
import app as recorder
import test_album
from test_transfer_links import LINK


class Uploads(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.client=r2_share.Client(self.root/'cloud')
        self.remote=Mock();self.remote.get_bucket_lifecycle_configuration.return_value={'Rules':[{'Status':'Enabled','Filter':{'Prefix':r2_share.PREFIX},'Expiration':{'Days':8}}]}
        self.remote.generate_presigned_url.return_value=LINK
        patcher=patch.object(self.client,'connection',return_value=(self.remote,'ultrasound'));patcher.start();self.addCleanup(patcher.stop)
        self.path=self.root/'PRIVATE-PATIENT.mp4';self.path.write_bytes(b'dummy video')
        stat=self.path.stat();self.items=[dict(path=self.path,size=stat.st_size,fingerprint=(stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns))]

    def uploaded(self,source,bucket,key,**kwargs):
        self.uploaded_data=source.read()
        source.close()
        self.remote.head_object.return_value={'ContentLength':len(self.uploaded_data)}

    def test_upload_streams_original_video_bytes_without_zip_or_staging(self):
        self.remote.upload_fileobj.side_effect=self.uploaded
        result=self.client.upload(self.items)
        self.assertEqual(result['link'],[LINK])
        self.assertTrue(self.path.exists())
        self.assertEqual(self.uploaded_data,b'dummy video')
        self.assertEqual(self.remote.upload_fileobj.call_args.kwargs['ExtraArgs']['ContentType'],'video/mp4')
        self.assertEqual(list(self.client.root.glob('upload-*')),[])
        self.assertNotIn('PRIVATE',str(self.remote.upload_fileobj.call_args))
        self.assertEqual(self.remote.generate_presigned_url.call_args.kwargs['ExpiresIn'],604800)

    def test_upload_progress_counts_bytes_across_files_and_marks_verification(self):
        events=[]
        def transfer(source,bucket,key,**kwargs):
            data=source.read();kwargs['Callback'](len(data)//2);kwargs['Callback'](len(data)-len(data)//2)
            self.remote.head_object.return_value={'ContentLength':len(data)}
        self.remote.upload_fileobj.side_effect=transfer
        self.client.upload(self.items*2,events.append)
        updates=[event for event in events if isinstance(event,dict)]
        self.assertEqual(updates[0]['bytes_done'],self.items[0]['size']//2)
        self.assertEqual(updates[-1]['bytes_done'],self.items[0]['size']*2)
        self.assertEqual(updates[-1]['bytes_total'],self.items[0]['size']*2)
        self.assertEqual(updates[-1]['files_done'],2)
        self.assertIn('verifying',[event['stage'] for event in updates])
        self.assertTrue(all(0<=event['bytes_done']<=event['bytes_total'] for event in updates))

    def test_missing_lifecycle_prevents_any_upload(self):
        self.remote.get_bucket_lifecycle_configuration.return_value={'Rules':[]}
        with self.assertRaisesRegex(r2_share.R2Error,'lifecycle'):self.client.upload(self.items)
        self.remote.upload_fileobj.assert_not_called()

    def test_conflicting_shorter_deletion_rule_prevents_upload(self):
        self.remote.get_bucket_lifecycle_configuration.return_value['Rules'].append(
            {'Status':'Enabled','Filter':{'Prefix':''},'Expiration':{'Days':1}})
        with self.assertRaisesRegex(r2_share.R2Error,'before the link expires'):self.client.upload(self.items)
        self.remote.upload_fileobj.assert_not_called()

    def test_changed_file_prevents_upload(self):
        self.path.write_bytes(b'changed')
        with self.assertRaisesRegex(r2_share.R2Error,'changed'):self.client.upload(self.items)
        self.remote.upload_fileobj.assert_not_called()

    def test_image_upload_streams_original_bytes_and_uses_image_mime_type(self):
        image=self.path.with_suffix('.png');self.path.rename(image);self.items[0]['path']=image
        self.remote.upload_fileobj.side_effect=self.uploaded
        self.remote.generate_presigned_url.return_value=LINK.replace('.mp4?','.png?')
        result=self.client.upload(self.items)
        self.assertEqual(self.uploaded_data,b'dummy video')
        self.assertEqual(self.remote.upload_fileobj.call_args.kwargs['ExtraArgs']['ContentType'],'image/png')
        self.assertIn('.png?',result['link'][0])
        self.assertTrue(image.exists())

    def test_multiple_videos_get_separate_links_and_partial_failure_cleans_up(self):
        self.remote.upload_fileobj.side_effect=self.uploaded
        self.remote.generate_presigned_url.side_effect=[LINK,LINK.replace('b'*48,'d'*48)]
        result=self.client.upload(self.items*2)
        self.assertEqual(len(result['link']),2)
        self.assertNotEqual(result['link'][0],result['link'][1])
        self.remote.reset_mock()
        self.remote.upload_fileobj.side_effect=[None,RuntimeError('failed second video')]
        self.remote.head_object.return_value={'ContentLength':self.items[0]['size']}
        self.remote.generate_presigned_url.side_effect=None;self.remote.generate_presigned_url.return_value=LINK
        with self.assertRaises(r2_share.R2Error):self.client.upload(self.items*2)
        self.assertEqual(self.remote.delete_object.call_count,2)
        self.assertTrue(self.path.exists())

    def test_failed_or_incomplete_upload_never_returns_link_or_removes_original(self):
        self.remote.upload_fileobj.side_effect=RuntimeError('private path and credentials')
        with self.assertRaises(r2_share.R2Error) as result:self.client.upload(self.items)
        self.assertNotIn('credentials',str(result.exception))
        self.remote.generate_presigned_url.assert_not_called()
        self.remote.delete_object.assert_called_once()
        self.assertTrue(self.path.exists())
        self.assertEqual(list(self.client.root.glob('upload-*')),[])

    def test_size_mismatch_deletes_only_new_cloud_object(self):
        self.remote.head_object.return_value={'ContentLength':0}
        with self.assertRaisesRegex(r2_share.R2Error,'verification'):self.client.upload(self.items)
        self.remote.delete_object.assert_called_once()
        self.assertTrue(self.path.exists())

    def test_keys_are_encrypted_and_never_returned_in_status(self):
        with patch.object(r2_share.secure_store,'save') as save:
            self.client.configure('a'*32,'ultrasound','k'*32,'s'*64)
        self.assertEqual(json.loads(save.call_args.args[1])['secret'],'s'*64)
        with patch.object(self.client,'credentials',return_value=json.loads(save.call_args.args[1])),patch.object(Path,'exists',return_value=True):
            status=self.client.status()
        self.assertNotIn('key',status);self.assertNotIn('secret',status)
        with self.assertRaises(r2_share.R2Error):self.client.configure('https://evil.example','bucket','k'*32,'s'*64)

    def test_real_sdk_generates_expected_private_link_without_network(self):
        with patch.object(self.client,'credentials',return_value=dict(account='a'*32,bucket='ultrasound',key='k'*32,secret='s'*64)):
            # Call the real connection method, bypassing the fixture's upload mock.
            client,bucket=r2_share.Client.connection(self.client)
            link=client.generate_presigned_url('get_object',Params={'Bucket':bucket,'Key':r2_share.PREFIX+'b'*48+'.mp4'},ExpiresIn=604800)
        self.assertEqual(r2_share.validate_link(link),link)


class CloudWorkflows(unittest.TestCase):
    setUp=test_album.Album.setUp
    post=test_album.Album.post
    def snapshot(self,patient_id='DUMMY'):
        import secrets
        folder=self.root/'Patients'/patient_id;folder.mkdir(parents=True,exist_ok=True)
        name=secrets.token_hex(6)+'.mp4';(folder/name).write_bytes(b'dummy video')
        albums=self.post('/api/album').json['albums']
        key=next(file['key'] for album in albums for file in album['files'] if file['name']==name)
        return types.SimpleNamespace(json={'key':key})

    def test_upload_requires_confirmation_and_authentication(self):
        self.assertEqual(self.post('/api/cloud/upload',{}).status_code,400)
        self.assertEqual(self.client.post('/api/cloud/upload',json={'confirm':True}).status_code,403)

    def test_album_image_is_rejected_before_cloud_upload(self):
        key=test_album.Album.snapshot(self).json['key']
        with patch.object(recorder,'get_r2_client') as client:
            response=self.post('/api/cloud/upload',dict(keys=[key],confirm=True,request_id='12345678-1234-1234-1234-123456789abc'))
        self.assertEqual(response.status_code,400,response.json)
        self.assertIn('at least one video',response.json['error'])
        client.assert_not_called()
        self.assertFalse(recorder.family_copy_lock.locked())

    def test_mixed_email_uploads_only_videos_and_attaches_original_images(self):
        import gmail_share
        image=test_album.Album.snapshot(self).json['key']
        video=self.snapshot().json['key']
        client=Mock();client.status.return_value={'configured':True}
        client.upload.return_value=dict(link=[LINK],expires=__import__('time').time()+604800,message='Uploaded')
        inline=lambda target,daemon:types.SimpleNamespace(start=target)
        data=dict(keys=[image,video],confirm=True,request_id='12345678-1234-1234-1234-123456789abc')
        with patch.object(recorder,'get_r2_client',return_value=client),patch.object(recorder.threading,'Thread',side_effect=inline):
            upload=self.post('/api/cloud/upload',data)
        self.assertEqual(upload.status_code,202,upload.json)
        uploaded=client.upload.call_args.args[0]
        self.assertEqual(len(uploaded),1);self.assertEqual(uploaded[0]['kind'],'video')
        review=self.post('/api/gmail/prepare',dict(keys=[image,video],confirm=True,recipient='family@example.com',delivery='link',upload_job=upload.json['job']))
        self.assertEqual(review.status_code,200,review.json)
        self.assertEqual(len(review.json['links']),1)
        self.assertEqual(len(review.json['attachments']),1)
        ticket=recorder.gmail_reviews[review.json['ticket']]
        self.assertEqual(ticket['keys'],[image])
        message=gmail_share.build_message('clinic@example.com',ticket['recipient'],ticket['subject'],ticket['body'],ticket['items'],ticket['download_link'])
        attachments=list(message.iter_attachments())
        self.assertEqual(len(attachments),1)
        self.assertEqual(attachments[0].get_content_type(),'image/png')
        self.assertEqual(attachments[0].get_payload(decode=True),self.png)
        self.assertIn('<a href=',message.get_body(preferencelist=('html',)).get_content())
        self.gmail.send.return_value={'state':'sent','message':'Accepted'}
        with patch.object(recorder.threading,'Thread',side_effect=inline):
            sent=self.post('/api/gmail/send',dict(ticket=review.json['ticket'],confirm=True))
        self.assertEqual(sent.status_code,202,sent.json)
        self.assertEqual(self.gmail.send.call_args.args[3],ticket['items'])

    def test_large_images_prevent_video_upload_before_network_work(self):
        video=self.snapshot().json['key']
        folder=recorder.selected_media(video)['path'].parent
        with (folder/'large.png').open('wb') as output:output.truncate(25_000_001)
        albums=self.post('/api/album').json['albums']
        image=next(file['key'] for album in albums for file in album['files'] if file['name']=='large.png')
        with patch.object(recorder,'get_r2_client') as client:
            result=self.post('/api/cloud/upload',dict(keys=[video,image],confirm=True,request_id='12345678-1234-1234-1234-123456789abc'))
        self.assertEqual(result.status_code,400,result.json)
        self.assertIn('25 MB',result.json['error']);client.assert_not_called()

    def test_oversized_images_offer_confirmed_links_instead_of_attachments(self):
        video=self.snapshot().json['key']
        folder=recorder.selected_media(video)['path'].parent
        with (folder/'large.png').open('wb') as output:output.truncate(25_000_001)
        image=next(file['key'] for album in self.post('/api/album').json['albums'] for file in album['files'] if file['name']=='large.png')
        client=Mock();client.status.return_value={'configured':True}
        client.upload.return_value=dict(link=[LINK.replace('.mp4?','.png?')],expires=__import__('time').time()+604800,message='Uploaded')
        inline=lambda target,daemon:types.SimpleNamespace(start=target)
        data=dict(keys=[image],confirm=True,upload_images=True,request_id='12345678-1234-1234-1234-123456789abc')
        with patch.object(recorder,'get_r2_client',return_value=client),patch.object(recorder.threading,'Thread',side_effect=inline):
            upload=self.post('/api/cloud/upload',data)
        self.assertEqual(upload.status_code,202,upload.json)
        self.assertEqual(client.upload.call_args.args[0][0]['kind'],'image')
        review=self.post('/api/gmail/prepare',dict(keys=[image],confirm=True,recipient='family@example.com',delivery='link',upload_job=upload.json['job']))
        self.assertEqual(review.status_code,200,review.json)
        self.assertEqual(review.json['attachments'],[])
        self.assertEqual(review.json['links'][0]['name'],'large.png')
        self.assertEqual(recorder.gmail_reviews[review.json['ticket']]['items'],[])
        # A changed upload preference cannot reuse the same request ID.
        data['upload_images']=False
        self.assertEqual(self.post('/api/cloud/upload',data).status_code,409)
        # The same option also works when videos and oversized images are selected together.
        data.update(upload_images=True,keys=[image,video],request_id='12345678-1234-1234-1234-123456789abd')
        client.upload.return_value=dict(link=[LINK.replace('.mp4?','.png?'),LINK],expires=__import__('time').time()+604800,message='Uploaded')
        with patch.object(recorder,'get_r2_client',return_value=client),patch.object(recorder.threading,'Thread',side_effect=inline):
            combined=self.post('/api/cloud/upload',data)
        self.assertEqual(combined.status_code,202,combined.json)
        self.assertEqual([item['kind'] for item in client.upload.call_args.args[0]],['image','video'])
        review=self.post('/api/gmail/prepare',dict(keys=[image,video],confirm=True,recipient='family@example.com',delivery='link',upload_job=combined.json['job']))
        self.assertEqual(review.status_code,200,review.json)
        self.assertEqual(len(review.json['links']),2)
        self.assertEqual(review.json['attachments'],[])

    def test_small_images_cannot_be_uploaded_by_accident(self):
        image=test_album.Album.snapshot(self).json['key']
        with patch.object(recorder,'get_r2_client') as client:
            result=self.post('/api/cloud/upload',dict(keys=[image],confirm=True,upload_images=True,request_id='12345678-1234-1234-1234-123456789abc'))
        self.assertEqual(result.status_code,400,result.json);client.assert_not_called()

    def test_success_duplicate_request_and_review_use_server_link(self):
        key=self.snapshot().json['key'];client=Mock();client.status.return_value={'configured':True}
        client.upload.return_value=dict(link=[LINK],expires=__import__('time').time()+604800,message='Uploaded')
        inline=lambda target,daemon:types.SimpleNamespace(start=target)
        data={'keys':[key],'confirm':True,'request_id':'12345678-1234-1234-1234-123456789abc'}
        with patch.object(recorder,'get_r2_client',return_value=client),patch.object(recorder.threading,'Thread',side_effect=inline):
            first=self.post('/api/cloud/upload',data)
            self.assertEqual(first.status_code,202,first.json)
            second=self.post('/api/cloud/upload',data)
        self.assertEqual(first.json,second.json);client.upload.assert_called_once()
        status=self.client.get('/api/cloud/job?id='+first.json['job'],headers=self.headers)
        self.assertEqual(status.json['state'],'complete');self.assertNotIn('link',status.json)
        review=self.post('/api/gmail/prepare',dict(keys=[key],confirm=True,recipient='family@example.com',delivery='link',upload_job=first.json['job']))
        self.assertEqual(review.status_code,200,review.json)
        self.assertIn(LINK,review.json['body'])
        self.assertEqual(review.json['attachments'],[])
        self.assertFalse(recorder.family_copy_lock.locked())

    def test_other_patient_selection_and_active_recording_are_rejected(self):
        first=self.snapshot().json['key'];second=self.snapshot(patient_id='OTHER').json['key']
        data={'keys':[first,second],'confirm':True,'request_id':'12345678-1234-1234-1234-123456789abc'}
        self.assertEqual(self.post('/api/cloud/upload',data).status_code,400)
        self.assertFalse(recorder.family_copy_lock.locked())
        recorder.state['active']=True;data['keys']=[first]
        self.assertEqual(self.post('/api/cloud/upload',data).status_code,409)

    def test_failed_worker_releases_lock_without_publishing_link(self):
        key=self.snapshot().json['key'];client=Mock();client.status.return_value={'configured':True};client.upload.side_effect=RuntimeError('secret')
        inline=lambda target,daemon:types.SimpleNamespace(start=target)
        with patch.object(recorder,'get_r2_client',return_value=client),patch.object(recorder.threading,'Thread',side_effect=inline):
            response=self.post('/api/cloud/upload',dict(keys=[key],confirm=True,request_id='12345678-1234-1234-1234-123456789abc'))
        status=self.client.get('/api/cloud/job?id='+response.json['job'],headers=self.headers)
        self.assertEqual(status.json['state'],'error');self.assertNotIn('secret',str(status.json))
        self.assertFalse(recorder.family_copy_lock.locked())

    def test_cannot_review_expired_other_patient_or_other_appdata_upload(self):
        import time
        key=self.snapshot().json['key']
        entry=dict(root=str(recorder.ROOT.resolve()),keys=[key],state='complete',link=LINK,expires=time.time()+604800)
        recorder.r2_jobs['review-test']=entry
        data=dict(keys=[key],confirm=True,recipient='family@example.com',delivery='link',upload_job='review-test')
        for changes in [dict(expires=0),dict(keys=['other-key']),dict(root='different-recorder')]:
            with patch.dict(entry,changes):self.assertEqual(self.post('/api/gmail/prepare',data).status_code,400)
        response=self.post('/api/gmail/prepare',data)
        self.assertEqual(response.status_code,200,response.json)
        recorder.gmail_reviews[response.json['ticket']]['link_expires']=0.1
        sent=self.post('/api/gmail/send',dict(ticket=response.json['ticket'],confirm=True))
        self.assertEqual(sent.status_code,409)
        self.gmail.send.assert_not_called()

    def test_worker_start_failure_does_not_leave_a_working_job_or_lock(self):
        key=self.snapshot().json['key'];client=Mock();client.status.return_value={'configured':True}
        before=set(recorder.r2_jobs)
        with patch.object(recorder,'get_r2_client',return_value=client),patch.object(recorder.threading,'Thread',side_effect=RuntimeError('start failed')):
            response=self.post('/api/cloud/upload',dict(keys=[key],confirm=True,request_id='12345678-1234-1234-1234-123456789abc'))
        self.assertEqual(response.status_code,503)
        self.assertEqual(set(recorder.r2_jobs),before)
        self.assertFalse(recorder.family_copy_lock.locked())
