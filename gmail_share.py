"""Gmail SMTP with encrypted credentials, explicit sending and no automatic retries."""
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import parseaddr,formatdate,make_msgid
import hashlib
import json
import os
import re
import smtplib
import ssl
import threading
from urllib.parse import urlsplit
import secure_store

MAX_ATTACHMENTS=25_000_000


class GmailError(Exception):pass


class SendUncertain(GmailError):pass


def validate_recipient(value):
    if not isinstance(value,str) or len(value)>254 or parseaddr(value)[1]!=value or not re.fullmatch(r'[^@\s<>;,]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}',value):
        raise GmailError('Enter one valid recipient email address.')
    return value


def validate_download_link(value):
    try:
        if not isinstance(value,str) or len(value)>512:raise ValueError()
        value=value.strip()
        if any(char.isspace() or ord(char)<32 for char in value):raise ValueError()
        parsed=urlsplit(value)
        match=re.fullmatch(r'/d/([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})/?',parsed.path)
        if parsed.scheme!='https' or parsed.hostname not in ('swisstransfer.com','www.swisstransfer.com') or parsed.port not in (None,443) or parsed.username or parsed.password or parsed.query or parsed.fragment or not match:
            raise ValueError()
        return 'https://www.swisstransfer.com/d/'+match.group(1).lower()
    except (ValueError,TypeError):raise GmailError('Paste the HTTPS SwissTransfer download link, in the form https://www.swisstransfer.com/d/... .') from None


def build_message(sender,recipient,subject,body,attachments,download_link=None):
    validate_recipient(sender);validate_recipient(recipient)
    if not isinstance(subject,str) or not subject.strip() or len(subject)>200 or any(c in subject for c in '\r\n'):
        raise GmailError('Enter a subject up to 200 characters on one line.')
    if not isinstance(body,str) or len(body)>20000:raise GmailError('The email message is too long.')
    if download_link is not None:
        link=validate_download_link(download_link)
        if attachments or link not in body:raise GmailError('Review the download-link email again. It must contain the link and no attachments.')
    elif not 1<=len(attachments)<=50 or sum(item['size'] for item in attachments)>MAX_ATTACHMENTS:
        raise GmailError('Select 1 to 50 files totalling no more than 25 MB. No email was sent.')
    message=EmailMessage(policy=SMTP)
    message['From']=sender;message['To']=recipient;message['Subject']=subject
    message['Date']=formatdate(localtime=True);message['Message-ID']=make_msgid(domain='miracle-of-life.local')
    message.set_content(body)
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

    def send(self,recipient,subject,body,attachments,revision,download_link=None):
        with self.lock:
            if self.revision()!=revision:raise GmailError('Gmail settings changed. Review the email again before sending.')
            saved=self.credentials()
        # Build and validate all bytes before making any SMTP connection.
        try:message=build_message(saved['email'],recipient,subject,body,attachments,download_link)
        except OSError:raise GmailError('Attachments could not be read. Reconnect the original USB and review again. No email was sent.') from None
        smtp=self.connection(saved)
        try:
            refused=smtp.send_message(message,from_addr=saved['email'],to_addrs=[recipient])
            if refused:raise GmailError('Gmail did not accept the recipient. No email was sent.')
        except (smtplib.SMTPRecipientsRefused,smtplib.SMTPSenderRefused,smtplib.SMTPDataError):
            raise GmailError('Gmail rejected the message or recipient. Check the address, attachment size and account sending limits before reviewing again.') from None
        except GmailError:raise
        except Exception:
            raise SendUncertain('Sending could not be confirmed. Check Gmail Sent before sending again; the email may already have been accepted. No automatic retry will occur.') from None
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
