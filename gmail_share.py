"""Gmail SMTP with encrypted credentials, explicit sending and no automatic retries."""
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import parseaddr,formatdate,make_msgid
import hashlib
from html import escape
import json
import os
import re
import smtplib
import ssl
import threading
from urllib.parse import urlsplit
import secure_store

MAX_ATTACHMENTS=25_000_000
SEND_TIMEOUT=600  # MIME attachments can take minutes to upload on a slower connection.


class GmailError(Exception):pass


class SendUncertain(GmailError):pass


def validate_recipient(value):
    if not isinstance(value,str) or len(value)>254 or parseaddr(value)[1]!=value or not re.fullmatch(r'[^@\s<>;,]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}',value):
        raise GmailError('Enter one valid recipient email address.')
    return value


def validate_download_link(value):
    import r2_share
    try:return r2_share.validate_link(value)
    except r2_share.R2Error as error:raise GmailError(str(error)) from None



def build_message(sender,recipient,subject,body,attachments,download_link=None):
    validate_recipient(sender);validate_recipient(recipient)
    if not isinstance(subject,str) or not subject.strip() or len(subject)>200 or any(c in subject for c in '\r\n'):
        raise GmailError('Enter a subject up to 200 characters on one line.')
    if not isinstance(body,str) or len(body)>220000:raise GmailError('The email message is too long.')
    if download_link is not None:
        links=download_link if isinstance(download_link,list) else [download_link]
        if not 1<=len(links)<=50:raise GmailError('Review the video download links again.')
        labels={}
        for index,entry in enumerate(links,1):
            link=validate_download_link(entry.get('url') if isinstance(entry,dict) else entry)
            name=entry.get('name') if isinstance(entry,dict) else f'Ultrasound {index:02d}.mp4'
            if not isinstance(name,str) or not name.strip() or len(name)>255 or any(ord(c)<32 for c in name):raise GmailError('Review the video filenames again.')
            labels[link]=name
        links=list(labels)
        if any(link not in body for link in links):raise GmailError('Review the video download links again.')
        if any(getattr(item.get('path'),'suffix','').lower() not in ('.png','.jpg','.jpeg') for item in attachments):raise GmailError('Only images can be attached alongside video links.')
    if (download_link is None and not attachments) or len(attachments)>50 or sum(item['size'] for item in attachments)>MAX_ATTACHMENTS:
        raise GmailError('Select 1 to 50 files totalling no more than 25 MB. No email was sent.')
    message=EmailMessage(policy=SMTP)
    message['From']=sender;message['To']=recipient;message['Subject']=subject
    message['Date']=formatdate(localtime=True);message['Message-ID']=make_msgid(domain='miracle-of-life.local')
    message.set_content(body)
    if download_link is not None:
        # Plain text remains available to mail readers without HTML support.
        replacements={escape(link):'<a href="'+escape(link,quote=True)+'">'+escape(name)+'</a>' for link,name in labels.items()}
        html=re.sub('|'.join(re.escape(link) for link in sorted(replacements,key=len,reverse=True)),lambda match:replacements[match.group()],escape(body))
        message.add_alternative('<!doctype html><html><body><div style="white-space:pre-wrap;font-family:Arial,sans-serif">'+html+'</div></body></html>',subtype='html')
    for index,item in enumerate(attachments,1):
        kinds={'.png':('image','png'),'.jpg':('image','jpeg'),'.jpeg':('image','jpeg'),'.mp4':('video','mp4')}
        suffix=item['path'].suffix.lower()
        if suffix not in kinds:raise GmailError('Unsupported attachment type.')
        with item['path'].open('rb') as source:
            before=os.fstat(source.fileno())
            identity=(before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)
            if identity!=item['fingerprint']:raise GmailError('A selected file changed. Review the album again. No email was sent.')
            data=source.read(MAX_ATTACHMENTS+1)
            after=os.fstat(source.fileno())
            if (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)!=identity or len(data)!=item['size']:
                raise GmailError('A selected file changed while preparing attachments. No email was sent.')
        main,sub=kinds[suffix]
        message.add_attachment(data,maintype=main,subtype=sub,filename=f'Ultrasound {index:02d}'+suffix)
    if len(message.as_bytes())>35_000_000:raise GmailError('The encoded email is too large. Select fewer attachments.')
    return message


class Client:
    def __init__(self,root):
        self.root=root;self.path=root/'smtp.dpapi';self.history_path=root/'send-status.json'
        self.lock=threading.RLock();self.jobs=None

    def credentials(self):
        try:
            saved=json.loads(secure_store.load(self.path))
            validate_recipient(saved['email'])
            if not re.fullmatch('[A-Za-z0-9]{16}',saved['password']):raise ValueError()
            return saved
        except Exception:raise GmailError('Save your Gmail address and app password in Settings first.') from None

    def status(self):
        with self.lock:
            if not self.path.exists():return {'configured':False,'email':'','message':'Gmail not configured.'}
            try:return {'configured':True,'email':self.credentials()['email'],'message':'Gmail settings saved. Use Check connection to verify sign-in.'}
            except GmailError:return {'configured':False,'email':'','message':'Gmail credentials could not be opened. Save them again for this Windows user.'}

    def configure(self,email,password):
        validate_recipient(email)
        if not isinstance(password,str):raise GmailError('Enter a 16-character Google app password.')
        password=password.replace(' ','')
        with self.lock:
            if not password and self.path.exists():
                saved=self.credentials()
                if email!=saved['email']:raise GmailError('Enter an app password for the new Gmail address.')
                return
            if not re.fullmatch('[A-Za-z0-9]{16}',password):raise GmailError('Enter the 16-character Google app password, not your normal Google password.')
            secure_store.save(self.path,json.dumps({'email':email,'password':password}).encode())

    def revision(self):
        with self.lock:
            self.credentials()
            return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def forget(self):
        with self.lock:self.path.unlink(missing_ok=True)

    def connection(self,saved):
        smtp=None
        try:
            smtp=smtplib.SMTP_SSL('smtp.gmail.com',465,timeout=30,context=ssl.create_default_context())
            smtp.login(saved['email'],saved['password'])
            return smtp
        except smtplib.SMTPAuthenticationError:
            if smtp is not None:
                try:smtp.close()
                except Exception:pass
            raise GmailError('Gmail rejected sign-in. Check the Gmail address, 2-Step Verification and app password in Settings.') from None
        except Exception:
            if smtp is not None:
                try:smtp.close()
                except Exception:pass
            raise GmailError('Could not connect securely to Gmail. Check internet access and try Check connection. No email was sent.') from None

    def check(self):
        with self.lock:saved=self.credentials()
        smtp=self.connection(saved)
        try:return {'message':'Gmail connection successful. No test email was sent.'}
        finally:
            try:smtp.close()
            except Exception:pass

    def send(self,recipient,subject,body,attachments,revision,download_link=None,progress=None):
        def report(stage,message):
            if progress is not None:
                try:progress(stage,message)
                except Exception:pass  # A progress display failure must never cause a duplicate send.
        report('preparing','Preparing email and checking attachments...')
        with self.lock:
            if self.revision()!=revision:raise GmailError('Gmail settings changed. Review the email again before sending.')
            saved=self.credentials()
        # Build and validate all bytes before making any SMTP connection.
        try:message=build_message(saved['email'],recipient,subject,body,attachments,download_link)
        except OSError:raise GmailError('Attachments could not be read. Reconnect the original USB and review again. No email was sent.') from None
        report('connecting','Connecting and signing in to Gmail...')
        smtp=self.connection(saved)
        try:smtp.sock.settimeout(SEND_TIMEOUT)
        except Exception:
            try:smtp.close()
            except Exception:pass
            raise GmailError('The Gmail connection closed before sending. Check connection in Settings. No email was sent.') from None
        try:
            report('sending','Sending email to Gmail. Please wait for confirmation...')
            refused=smtp.send_message(message,from_addr=saved['email'],to_addrs=[recipient])
            if refused:raise GmailError('Gmail did not accept the recipient. No email was sent.')
        except (smtplib.SMTPRecipientsRefused,smtplib.SMTPSenderRefused,smtplib.SMTPDataError):
            raise GmailError('Gmail rejected the message or recipient. Check the address, attachment size and account sending limits before reviewing again.') from None
        except GmailError:raise
        except smtplib.SMTPNotSupportedError:
            raise GmailError('Gmail could not send using this email address format. Check the sender and recipient addresses. No email was sent.') from None
        except (TimeoutError,smtplib.SMTPServerDisconnected,ssl.SSLError) as error:
            reason='Gmail confirmation timed out' if isinstance(error,TimeoutError) else 'The connection to Gmail was interrupted'
            raise SendUncertain(reason+'. Check Gmail Sent before sending again; the email may already have been accepted. No automatic retry will occur.') from None
        except Exception as error:
            # Class name only: never surface SMTP responses, email addresses or raw errors.
            kind=type(error).__name__
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}',kind):kind='UnexpectedError'
            raise SendUncertain('Sending could not be confirmed ('+kind+'). Check Gmail Sent before sending again; the email may already have been accepted. No automatic retry will occur.') from None
        finally:
            # A failed QUIT must not turn a confirmed acceptance into a retry.
            try:smtp.close()
            except Exception:pass
        return {'state':'sent','message':'Gmail accepted the email for sending. You can check Gmail Sent; delivery to the recipient is not guaranteed.'}

    def history(self):
        if self.jobs is None:
            try:self.jobs=json.loads(self.history_path.read_text(encoding='utf-8'))
            except FileNotFoundError:self.jobs={}
            except Exception:raise GmailError('Email status history cannot be opened. Check AppData and Gmail Sent before sending again.') from None
            if not isinstance(self.jobs,dict):raise GmailError('Email status history needs review. Check Gmail Sent before sending again.')
            for job,value in self.jobs.items():
                if value.get('state')=='working':self.jobs[job]={'state':'unknown','message':'The app closed before sending was confirmed. Check Gmail Sent before sending again. No automatic retry occurred.'}
        return self.jobs

    def record_job(self,job,value):
        # Only opaque IDs and generic status messages; never patient data or addresses.
        with self.lock:
            jobs=dict(self.history());jobs[job]=value
            if len(jobs)>200:jobs=dict(list(jobs.items())[-200:])
            self.root.mkdir(parents=True,exist_ok=True)
            temporary=self.history_path.with_suffix('.tmp')
            with temporary.open('w',encoding='utf-8') as output:
                json.dump(jobs,output);output.flush();os.fsync(output.fileno())
            temporary.replace(self.history_path);self.jobs=jobs

    def job(self,job):
        with self.lock:return self.history().get(job)
