import json
from pathlib import Path
import smtplib
import tempfile
import types
import unittest
from unittest.mock import Mock,patch
import gmail_share
import secure_store
import test_album
import app as recorder


class GmailSMTP(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.client=gmail_share.Client(self.root)
        self.client.path.write_bytes(b'encrypted-placeholder')
        saved={'email':'clinic@example.com','password':'abcdefghijklmnop'}
        patcher=patch.object(secure_store,'load',return_value=json.dumps(saved).encode())
        patcher.start();self.addCleanup(patcher.stop)
        self.path=self.root/'PRIVATE-PATIENT-123.png';self.path.write_bytes(b'original snapshot')
        info=self.path.stat()
        self.item={'path':self.path,'size':info.st_size,'fingerprint':(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)}

    def send(self):
        return self.client.send('family@example.com','Ultrasound','Review',[self.item],self.client.revision())

    def test_tls_login_and_original_attachment_no_private_filename(self):
        with patch.object(gmail_share.smtplib,'SMTP_SSL') as factory:
            smtp=factory.return_value;smtp.send_message.return_value={}
            result=self.send()
        self.assertEqual(result['state'],'sent')
        self.assertEqual(factory.call_args.args,('smtp.gmail.com',465))
        self.assertEqual(factory.call_args.kwargs['timeout'],30)
        context=factory.call_args.kwargs['context']
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode,2)
        smtp.login.assert_called_once_with('clinic@example.com','abcdefghijklmnop')
        message=smtp.send_message.call_args.args[0]
        attachment=list(message.iter_attachments())[0]
        self.assertEqual(attachment.get_filename(),'Ultrasound 01.png')
        self.assertEqual(attachment.get_payload(decode=True),self.path.read_bytes())
        self.assertNotIn(b'PRIVATE-PATIENT',message.as_bytes())
        self.assertEqual(smtp.send_message.call_args.kwargs['to_addrs'],['family@example.com'])

    def test_auth_rejection_is_sanitized_and_never_sends(self):
        with patch.object(gmail_share.smtplib,'SMTP_SSL') as factory:
            factory.return_value.login.side_effect=smtplib.SMTPAuthenticationError(535,b'private-password-and-address')
            with self.assertRaises(gmail_share.GmailError) as error:self.send()
        self.assertNotIn('private-password',str(error.exception))
        factory.return_value.send_message.assert_not_called()

    def test_uncertain_send_not_retried_and_close_failure_not_duplicate(self):
        with patch.object(gmail_share.smtplib,'SMTP_SSL') as factory:
            factory.return_value.send_message.side_effect=TimeoutError('private')
            with self.assertRaises(gmail_share.SendUncertain):self.send()
            self.assertEqual(factory.return_value.send_message.call_count,1)
        with patch.object(gmail_share.smtplib,'SMTP_SSL') as factory:
            factory.return_value.send_message.return_value={}
            factory.return_value.close.side_effect=OSError('connection closed')
            self.assertEqual(self.send()['state'],'sent')

    def test_definite_rejection_and_connection_check(self):
        with patch.object(gmail_share.smtplib,'SMTP_SSL') as factory:
            smtp=factory.return_value
            smtp.send_message.side_effect=smtplib.SMTPDataError(552,b'private server response')
            with self.assertRaises(gmail_share.GmailError) as error:self.send()
            self.assertNotIsInstance(error.exception,gmail_share.SendUncertain)
            smtp.reset_mock()
            self.client.check()
            smtp.send_message.assert_not_called()

    def test_changed_file_and_limit_block_before_connecting(self):
        with patch.object(gmail_share.smtplib,'SMTP_SSL') as factory:
            self.path.write_bytes(b'changed')
            with self.assertRaises(gmail_share.GmailError):self.send()
            self.item.update(path=self.root/'missing.mp4',size=25_000_001)
            with self.assertRaises(gmail_share.GmailError):self.send()
            factory.assert_not_called()

    def test_headers_and_recipient_injection_rejected(self):
        for address in ['family@example.com\r\nBcc: x@example.com','a@example.com,b@example.com',None]:
            with self.assertRaises(gmail_share.GmailError):gmail_share.validate_recipient(address)
        with self.assertRaises(gmail_share.GmailError):
            gmail_share.build_message('clinic@example.com','family@example.com','Hello\nBcc: x','',[self.item])

    def test_credentials_encrypted_and_never_returned(self):
        with patch.object(secure_store,'transform',return_value=b'protected-by-windows'):
            self.client.configure('clinic@example.com','abcd efgh ijkl mnop')
        self.assertEqual(self.client.path.read_bytes(),b'protected-by-windows')
        self.assertFalse(self.client.path.with_suffix('.tmp').exists())
        self.assertNotIn('abcdefghijklmnop',json.dumps(self.client.status()))
        self.client.configure('clinic@example.com','')
        with self.assertRaises(gmail_share.GmailError):self.client.configure('other@example.com','')

    def test_restart_reports_interrupted_send_without_resending(self):
        self.client.record_job('opaque-key',{'state':'working','message':'Sending email'})
        restored=gmail_share.Client(self.root)
        with patch.object(gmail_share.smtplib,'SMTP_SSL') as factory:
            job=restored.job('opaque-key')
        self.assertEqual(job['state'],'unknown')
        self.assertIn('Gmail Sent',job['message'])
        factory.assert_not_called()
        self.assertNotIn('clinic@example.com',self.client.history_path.read_text())


class GmailWorkflows(unittest.TestCase):
    setUp=test_album.Album.setUp
    post=test_album.Album.post
    snapshot=test_album.Album.snapshot
    draft_data=test_album.Album.draft_data

    def prepare(self):
        key=self.snapshot().json['key']
        response=self.post('/api/gmail/prepare',self.draft_data(key,body='Frozen message'))
        self.assertEqual(response.status_code,200,response.json)
        return response.json

    def run_send(self,ticket):
        self.gmail.send.return_value={'state':'sent','message':'Gmail accepted email'}
        inline=lambda target,daemon:types.SimpleNamespace(start=target)
        with patch.object(recorder.threading,'Thread',side_effect=inline):
            return self.post('/api/gmail/send',{'ticket':ticket,'confirm':True,'recipient':'ignored@example.com','body':'ignored'})

    def test_review_required_frozen_contents_and_duplicate_click_safe(self):
        self.assertEqual(self.post('/api/gmail/send',{'confirm':True}).status_code,400)
        preview=self.prepare()
        self.gmail.send.assert_not_called()
        response=self.run_send(preview['ticket'])
        self.assertEqual(response.status_code,202,response.json)
        self.assertEqual(self.gmail.send.call_args.args[:3],('family@example.com','Your Miracle of Life ultrasound','Frozen message'))
        self.assertEqual(self.post('/api/gmail/send',{'ticket':preview['ticket'],'confirm':True}).json,response.json)
        self.assertEqual(self.gmail.send.call_count,1)
        self.gmail.record_job.assert_called()
        self.assertFalse(recorder.family_copy_lock.locked())

    def test_recording_and_changed_files_block_send(self):
        preview=self.prepare()
        recorder.state['active']=True
        self.assertEqual(self.run_send(preview['ticket']).status_code,409)
        recorder.state['active']=False
        recorder.selected_media(preview['attachments'][0]['key'])['path'].write_bytes(b'changed')
        self.assertEqual(self.run_send(preview['ticket']).status_code,409)
        self.gmail.send.assert_not_called()
        self.assertFalse(recorder.family_copy_lock.locked())

    def test_changed_account_expired_review_and_status_auth(self):
        preview=self.prepare()
        self.gmail.revision.return_value='different'
        self.assertEqual(self.run_send(preview['ticket']).status_code,409)
        self.gmail.revision.return_value='dummy-revision'
        recorder.gmail_reviews[preview['ticket']]['created']-=901
        self.assertEqual(self.run_send(preview['ticket']).status_code,409)
        self.assertEqual(self.client.get('/api/gmail/job?id=opaque').status_code,403)
        self.gmail.send.assert_not_called()

    def test_journal_failure_blocks_network_send(self):
        preview=self.prepare()
        self.gmail.record_job.side_effect=OSError('private path')
        response=self.run_send(preview['ticket'])
        self.assertEqual(response.status_code,503)
        self.assertNotIn('private path',str(response.json))
        self.gmail.send.assert_not_called()
        self.assertFalse(recorder.family_copy_lock.locked())

    def test_unknown_result_stored_and_no_retry(self):
        preview=self.prepare()
        self.gmail.send.side_effect=gmail_share.SendUncertain('Check Gmail Sent before sending again.')
        response=self.run_send(preview['ticket'])
        self.assertEqual(response.status_code,202)
        self.assertEqual(self.gmail.record_job.call_args.args[1]['state'],'unknown')
        self.post('/api/gmail/send',{'ticket':preview['ticket'],'confirm':True})
        self.assertEqual(self.gmail.send.call_count,1)
