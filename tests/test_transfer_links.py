import types
import time
import r2_share
import unittest
from html.parser import HTMLParser
from unittest.mock import patch
import gmail_share
import test_album
import test_gmail_smtp
import app as recorder

LINK='https://'+'a'*32+'.r2.cloudflarestorage.com/ultrasound/miracle-of-life/'+'b'*48+'.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Expires=604800&X-Amz-Signature='+'c'*64



class LinkValidation(unittest.TestCase):
    def test_html_email_displays_filename_and_escapes_message_and_link_label(self):
        name='Scan & family.mp4'
        message=gmail_share.build_message('clinic@example.com','family@example.com','Your video','<script>bad()</script>\n'+LINK,[],download_link=[dict(url=LINK,name=name)])
        html=message.get_body(preferencelist=('html',)).get_content()
        class Reader(HTMLParser):
            def __init__(self):super().__init__();self.links=[];self.text=[]
            def handle_starttag(self,tag,attrs):
                if tag=='a':self.links.append(dict(attrs)['href'])
            def handle_data(self,text):self.text.append(text)
        reader=Reader();reader.feed(html)
        self.assertEqual(reader.links,[LINK])
        self.assertIn(name,''.join(reader.text))
        self.assertNotIn(LINK,''.join(reader.text))
        self.assertNotIn('<script>',html)
        self.assertIn(LINK,message.get_body(preferencelist=('plain',)).get_content())

    def test_only_valid_https_r2_links_are_accepted(self):
        self.assertEqual(gmail_share.validate_download_link(LINK),LINK)
        for value in [None,'',LINK.replace('https:','http:'),LINK.replace('.r2.cloudflarestorage.com','.evil.example'),
                      LINK+'#password',LINK.replace('/miracle-of-life/','/patient/'),
                      LINK.replace('604800','604801'),'https://www.swisstransfer.com/d/12345678-1234-1234-1234-123456789abc']:
            with self.assertRaises(gmail_share.GmailError):gmail_share.validate_download_link(value)

    def test_link_email_is_plaintext_and_has_no_attachments(self):
        body='Your ultrasound video is available here:\n'+LINK
        message=gmail_share.build_message('clinic@example.com','family@example.com','Your ultrasound',body,[],download_link=LINK)
        self.assertEqual(list(message.iter_attachments()),[])
        self.assertIn(LINK,message.get_body(preferencelist=('plain',)).get_content())
        self.assertEqual(message['To'],'family@example.com')

    def test_each_video_link_is_included_without_attachments(self):
        links=[LINK,LINK.replace('b'*48,'d'*48)]
        message=gmail_share.build_message('clinic@example.com','family@example.com','Your videos','\n'.join(links),[],download_link=links)
        self.assertEqual(list(message.iter_attachments()),[])
        for link in links:self.assertIn(link,message.get_body(preferencelist=('plain',)).get_content())
        with self.assertRaises(gmail_share.GmailError):
            gmail_share.build_message('clinic@example.com','family@example.com','Your videos',LINK,[],download_link=links)

    def test_empty_email_is_not_allowed_without_a_valid_reviewed_link(self):
        with self.assertRaises(gmail_share.GmailError):
            gmail_share.build_message('clinic@example.com','family@example.com','Your ultrasound','Hello',[])
        with self.assertRaises(gmail_share.GmailError):
            gmail_share.build_message('clinic@example.com','family@example.com','Your ultrasound','Hello',[],download_link=LINK)
        with self.assertRaises(gmail_share.GmailError):
            gmail_share.build_message('clinic@example.com','family@example.com','Your ultrasound',LINK,[{'size':10}],download_link=LINK)


class LinkSMTP(unittest.TestCase):
    setUp=test_gmail_smtp.GmailSMTP.setUp

    def test_smtp_sends_only_the_reviewed_link_with_existing_credentials(self):
        with patch.object(gmail_share.smtplib,'SMTP_SSL') as factory:
            smtp=factory.return_value;smtp.send_message.return_value={}
            result=self.client.send('family@example.com','Your ultrasound','Please download:\n'+LINK,[],self.client.revision(),download_link=LINK)
        self.assertEqual(result['state'],'sent')
        message=smtp.send_message.call_args.args[0]
        self.assertEqual(list(message.iter_attachments()),[])
        self.assertIn(LINK,message.get_body(preferencelist=('plain',)).get_content())
        smtp.login.assert_called_once_with('clinic@example.com','abcdefghijklmnop')


class LinkWorkflows(unittest.TestCase):
    setUp=test_album.Album.setUp
    post=test_album.Album.post
    snapshot=test_album.Album.snapshot
    draft_data=test_album.Album.draft_data

    def video(self,**extra):
        snapshot=self.snapshot(**extra)
        item=recorder.selected_media(snapshot.json['key'])
        path=item['path'].with_suffix('.mp4');path.write_bytes(b'dummy video')
        recorder.patient_store.attach_recording(item['record_root'],snapshot.json['record_id'],str(path.relative_to(self.root)),item['volume'])
        return types.SimpleNamespace(json={'key':recorder.register_media(path,item['volume'])})

    def data(self,key,**extra):
        recorder.r2_jobs['uploaded']={'root':str(recorder.ROOT.resolve()),'keys':[key],'state':'complete','link':LINK,'expires':time.time()+604800}
        return self.draft_data(key,delivery='link',upload_job='uploaded',**extra)

    def test_large_video_can_be_reviewed_as_link_without_upload_or_attachment(self):
        folder=self.root/'Patients'/'DUMMY';folder.mkdir(parents=True)
        path=folder/'large.mp4'
        with path.open('wb') as output:output.truncate(25_000_001)
        key=self.post('/api/album').json['albums'][0]['files'][0]['key']
        with patch('urllib.request.urlopen') as external:
            response=self.post('/api/gmail/prepare',self.data(key,body='Your video is ready.'))
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['delivery'],'link')
        self.assertEqual(response.json['attachments'],[])
        self.assertEqual(response.json['links'][0]['name'],'large.mp4')
        self.assertIn(LINK,response.json['body'])
        self.assertNotIn('DUMMY',response.json['body'])
        external.assert_not_called();self.gmail.send.assert_not_called()
        path.unlink()  # Uploaded links no longer need the local video after review.
        self.gmail.send.return_value={'state':'sent','message':'Email accepted'}
        inline=lambda target,daemon:types.SimpleNamespace(start=target)
        with patch.object(recorder.threading,'Thread',side_effect=inline):
            sent=self.post('/api/gmail/send',{'ticket':response.json['ticket'],'confirm':True,'download_link':'https://evil.example'})
        self.assertEqual(sent.status_code,202,sent.json)
        self.assertEqual(self.gmail.send.call_args.args[3],[])
        self.assertEqual(self.gmail.send.call_args.kwargs['download_link'][0]['url'],LINK)
        self.assertFalse(recorder.family_copy_lock.locked())

    def test_completed_upload_is_required_and_caller_link_is_ignored(self):
        key=self.video().json['key']
        data=self.data(key);data['upload_job']='forged'
        self.assertEqual(self.post('/api/gmail/prepare',data).status_code,400)
        data['upload_job']='uploaded';data['download_link']='https://evil.example/video'
        response=self.post('/api/gmail/prepare',data)
        self.assertEqual(response.status_code,200,response.json)
        self.assertNotIn('evil.example',response.json['body'])
        data['delivery']='anything'
        self.assertEqual(self.post('/api/gmail/prepare',data).status_code,400)
        self.gmail.send.assert_not_called()

    def test_link_cannot_combine_patients_but_optional_details_work(self):
        first=self.video();second=self.video(patient_id='OTHER')
        data=self.data(first.json['key']);data['keys'].append(second.json['key'])
        self.assertEqual(self.post('/api/gmail/prepare',data).status_code,400)
        data['keys']=[first.json['key']];data['fields']=['name']
        response=self.post('/api/gmail/prepare',data)
        self.assertEqual(response.status_code,200,response.json)
        self.assertIn('Example Patient',response.json['body'])
        self.assertIn(LINK,response.json['body'])

    def test_folder_button_opens_only_registered_media_folders(self):
        key=self.snapshot().json['key']
        expected=recorder.selected_media(key)['path'].parent
        with patch.object(recorder.os,'startfile',create=True) as opener:
            response=self.post('/api/media/folders',{'keys':[key]})
            self.assertEqual(response.status_code,200,response.json)
            opener.assert_called_once_with(str(expected))
            opener.reset_mock()
            response=self.post('/api/media/folders',{'keys':['../../outside']})
            self.assertEqual(response.status_code,409)
            opener.assert_not_called()
        self.assertEqual(self.client.post('/api/media/folders',json={'keys':[key]}).status_code,403)
