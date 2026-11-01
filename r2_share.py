"""Private R2 uploads. Credentials stay in DPAPI; object names contain no patient data."""
import json
import os
import re
import secrets
import time
import threading
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import secure_store

PREFIX='miracle-of-life/'
LINK_SECONDS=7*24*60*60


class R2Error(Exception):pass


def validate_link(value):
    try:
        if not isinstance(value,str) or len(value)>4096 or any(c.isspace() or ord(c)<32 for c in value):raise ValueError()
        url=urlsplit(value);query=parse_qs(url.query,strict_parsing=True)
        if (url.scheme!='https' or not re.fullmatch(r'[0-9a-f]{32}\.r2\.cloudflarestorage\.com',url.hostname or '')
            or url.port not in (None,443) or url.username or url.password or url.fragment
            or not re.fullmatch(r'/[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]/miracle-of-life/[0-9a-f]{48}\.(?:mp4|png|jpg|jpeg)',url.path)
            or query.get('X-Amz-Algorithm')!=['AWS4-HMAC-SHA256']
            or not re.fullmatch(r'[0-9a-f]{64}',query.get('X-Amz-Signature',[''])[0])
            or not 1<=int(query.get('X-Amz-Expires',['0'])[0])<=LINK_SECONDS):raise ValueError()
        return value
    except (ValueError,TypeError,IndexError):raise R2Error('The cloud download link is invalid. Upload the selection again.') from None


class Client:
    def __init__(self,root):
        self.root=Path(root);self.path=self.root/'credentials.dpapi'

    def credentials(self):
        try:return json.loads(secure_store.load(self.path))
        except Exception:raise R2Error('Save Cloudflare R2 settings first, or enter the credentials again.') from None

    def status(self):
        if not self.path.exists():return dict(configured=False,account='',bucket='',message='Cloud uploads are not configured.')
        try:
            saved=self.credentials()
            return dict(configured=True,account=saved['account'],bucket=saved['bucket'],message='Cloud credentials saved. Check connection before uploading.')
        except R2Error:return dict(configured=False,account='',bucket='',message='Cloud credentials could not be opened. Enter them again.')

    def configure(self,account,bucket,key,secret):
        if not isinstance(account,str) or not re.fullmatch('[0-9a-f]{32}',account):raise R2Error('Enter the 32-character Cloudflare account ID.')
        if not isinstance(bucket,str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]',bucket):raise R2Error('Enter a valid R2 bucket name (3 to 63 letters, digits or hyphens).')
        if not key and not secret:
            old=self.credentials()
            if (old['account'],old['bucket'])!=(account,bucket):raise R2Error('Enter both API keys when changing the account or bucket.')
            key,secret=old['key'],old['secret']
        if not isinstance(key,str) or not re.fullmatch('[A-Za-z0-9]{16,128}',key) or not isinstance(secret,str) or not re.fullmatch('[A-Za-z0-9/+_=.-]{16,256}',secret):
            raise R2Error('Enter both the Access Key ID and Secret Access Key.')
        secure_store.save(self.path,json.dumps(dict(account=account,bucket=bucket,key=key,secret=secret)).encode())

    def forget(self):self.path.unlink(missing_ok=True)

    def connection(self):
        import boto3
        from botocore.config import Config
        saved=self.credentials()
        client=boto3.client('s3',endpoint_url='https://'+saved['account']+'.r2.cloudflarestorage.com',
            aws_access_key_id=saved['key'],aws_secret_access_key=saved['secret'],region_name='auto',
            config=Config(signature_version='s3v4',s3={'addressing_style':'path'},connect_timeout=15,read_timeout=60,
                          retries={'max_attempts':2},request_checksum_calculation='when_required',response_checksum_validation='when_required'))
        return client,saved['bucket']

    def check_retention(self,client,bucket):
        rules=client.get_bucket_lifecycle_configuration(Bucket=bucket).get('Rules',[])
        valid=any(rule.get('Status')=='Enabled' and rule.get('Filter')=={'Prefix':PREFIX}
                  and rule.get('Expiration')=={'Days':8} for rule in rules)
        if not valid:raise R2Error('In Cloudflare, add an enabled lifecycle rule: prefix miracle-of-life/, delete after 8 days. Then check connection again.')
        for rule in rules:
            other=rule.get('Filter',{'Prefix':rule.get('Prefix','')})
            if (rule.get('Status')=='Enabled' and set(other)=={'Prefix'}
                and (PREFIX.startswith(other['Prefix']) or other['Prefix'].startswith(PREFIX))
                and rule.get('Expiration',{}).get('Days',8)<8):
                raise R2Error('Another lifecycle rule could delete these uploads before the link expires. Use a dedicated bucket with the eight-day rule.')

    def check(self):
        try:
            client,bucket=self.connection()
            client.head_bucket(Bucket=bucket);self.check_retention(client,bucket)
            return dict(message='Connected. Eight-day cloud deletion rule verified. Keep this bucket private.')
        except R2Error:raise
        except Exception:raise R2Error('Could not access R2. Check the account, bucket, API permissions and internet connection. The token needs permission to read bucket lifecycle rules.') from None

    def upload(self,items,progress=lambda message:None):
        """Stream original video/image bytes; publish links only after every upload is verified."""
        client=None;bucket=None;created=[];links=[];expires=None
        total=sum(item['size'] for item in items);completed=0;progress_lock=threading.Lock()
        try:
            if not items or any(item['path'].suffix.lower() not in ('.mp4','.png','.jpg','.jpeg') for item in items):
                raise R2Error('Select supported videos or images for cloud uploading.')
            client,bucket=self.connection();self.check_retention(client,bucket)
            from boto3.s3.transfer import TransferConfig
            class Reader:
                # Keep our source handle open for identity verification after the SDK finishes.
                def __init__(self,source):self.source=source
                def read(self,*args):return self.source.read(*args)
                def seek(self,*args):return self.source.seek(*args)
                def tell(self):return self.source.tell()
                def close(self):pass
            for index,item in enumerate(items,1):
                suffix=item['path'].suffix.lower()
                object_key=PREFIX+secrets.token_hex(24)+suffix
                progress(f'Uploading file {index} of {len(items)}. Keep the USB connected...')
                transferred=0
                def transferred_bytes(amount):
                    nonlocal transferred
                    with progress_lock:
                        transferred=max(0,min(item['size'],transferred+amount))
                        progress(dict(bytes_done=completed+transferred,bytes_total=total,files_done=index-1,files_total=len(items),stage='uploading'))
                with item['path'].open('rb') as source:
                    def fingerprint():
                        stat=os.fstat(source.fileno());return stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns
                    if fingerprint()!=item['fingerprint']:raise R2Error('A selected file changed. Refresh the album and try again.')
                    created.append(object_key)
                    client.upload_fileobj(Reader(source),bucket,object_key,
                        ExtraArgs={'ContentType':{'.mp4':'video/mp4','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg'}[suffix],'ContentDisposition':f'inline; filename="Ultrasound-{index:02d}{suffix}"'},
                        Callback=transferred_bytes,
                        Config=TransferConfig(multipart_threshold=16*1024*1024,multipart_chunksize=16*1024*1024,max_concurrency=2))
                    if fingerprint()!=item['fingerprint']:raise R2Error('A file changed during uploading. No download links were created.')
                progress(dict(bytes_done=completed+item['size'],bytes_total=total,files_done=index-1,files_total=len(items),stage='verifying'))
                if client.head_object(Bucket=bucket,Key=object_key)['ContentLength']!=item['size']:
                    raise R2Error('Cloud upload verification failed. No download links were created.')
                link=validate_link(client.generate_presigned_url('get_object',Params={'Bucket':bucket,'Key':object_key},ExpiresIn=LINK_SECONDS))
                links.append(link)
                completed+=item['size']
                progress(dict(bytes_done=completed,bytes_total=total,files_done=index,files_total=len(items),stage='verified'))
                if expires is None:expires=time.time()+LINK_SECONDS
            return dict(link=links,expires=expires,message='Files uploaded. Review the email before sending. Each download link lasts seven days.')
        except Exception as error:
            for object_key in created:
                try:client.delete_object(Bucket=bucket,Key=object_key)
                except Exception:pass  # Eight-day lifecycle remains the fallback; never touch USB originals.
            if isinstance(error,R2Error):raise
            raise R2Error('Upload could not finish. USB originals were kept. Check internet access and R2 permissions, then try again.') from None
