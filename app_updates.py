"""User-requested stable GitHub updates; downloads only, never runs installers."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request,build_opener,HTTPRedirectHandler

REPOSITORY='sirk123au/MiracleOfLifeRecorder'
API='https://api.github.com/repos/'+REPOSITORY+'/releases/latest'
MAX_INSTALLER=300*1024*1024


class UpdateError(Exception):pass


def version(value):
    if not isinstance(value,str) or not re.fullmatch(r'v?\d{1,5}\.\d{1,5}\.\d{1,5}',value):
        raise UpdateError('The release version is not supported.')
    return tuple(map(int,value.removeprefix('v').split('.')))


def allowed_url(url):
    parsed=urlsplit(url)
    return (parsed.scheme=='https' and parsed.port in (None,443) and not parsed.username and not parsed.password and
            (parsed.hostname in ('api.github.com','release-assets.githubusercontent.com','objects.githubusercontent.com') or
             (parsed.hostname=='github.com' and parsed.path.startswith('/'+REPOSITORY+'/releases/'))))


class GitHubRedirects(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if not allowed_url(newurl):raise UpdateError('GitHub redirected the download to an unsupported location.')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def open_url(url):
    if not allowed_url(url):raise UpdateError('Unsupported update address.')
    return build_opener(GitHubRedirects()).open(Request(url,headers={
        'User-Agent':'MiracleOfLifeRecorder-Updater','Accept':'application/vnd.github+json' if url==API else 'application/octet-stream',
        'X-GitHub-Api-Version':'2022-11-28'}),timeout=30)


def parse_release(data,current):
    if not isinstance(data,dict) or data.get('draft') is not False or data.get('prerelease') is not False:
        raise UpdateError('No published stable release is available.')
    tag=data.get('tag_name');remote=version(tag);local=version(current)
    display=tag.removeprefix('v')
    if remote<=local:return None
    name=f'MiracleOfLifeRecorder-Setup-{display}.exe'
    assets=[asset for asset in data.get('assets',[]) if isinstance(asset,dict) and asset.get('name')==name and asset.get('state')=='uploaded']
    if len(assets)!=1:raise UpdateError('The latest release does not yet have its Windows installer. Try again later.')
    asset=assets[0];digest=asset.get('digest','');size=asset.get('size')
    expected=f'https://github.com/{REPOSITORY}/releases/download/{tag}/{name}'
    if asset.get('browser_download_url')!=expected:raise UpdateError('The release installer address could not be verified.')
    if not isinstance(digest,str) or not re.fullmatch(r'sha256:[0-9a-fA-F]{64}',digest):
        raise UpdateError('The release has no verified SHA-256 checksum. Download was blocked.')
    if isinstance(size,bool) or not isinstance(size,int) or not 1<=size<=MAX_INSTALLER:
        raise UpdateError('The release installer size is unsupported.')
    return {'version':display,'filename':name,'url':expected,'sha256':digest[7:].lower(),'size':size,
            'release_url':f'https://github.com/{REPOSITORY}/releases/tag/{tag}'}


def verified(path,release):
    if not path.is_file() or path.stat().st_size!=release['size']:return False
    digest=hashlib.sha256()
    with path.open('rb') as source:
        while chunk:=source.read(1024*1024):digest.update(chunk)
    return digest.hexdigest()==release['sha256']


class Updater:
    def __init__(self,root,current):
        self.root=Path(root);self.current=current;self.lock=threading.RLock();self.release=None
        self.result={'state':'idle','current_version':current,'message':'Select Check for updates to check GitHub Releases.'}

    def status(self):
        with self.lock:return dict(self.result)

    def set(self,**values):
        with self.lock:self.result.update(values)

    def busy(self):return self.result['state'] in ('checking','downloading')

    def check(self):
        with self.lock:
            if self.busy():raise UpdateError('An update check or download is already running.')
            self.release=None
            self.result={'state':'checking','current_version':self.current,'message':'Checking GitHub Releases...'}
        def work():
            try:
                with open_url(API) as response:
                    raw=response.read(1024*1024+1)
                if len(raw)>1024*1024:raise UpdateError('GitHub returned an unsupported release response.')
                release=parse_release(json.loads(raw),self.current)
                with self.lock:
                    self.release=release
                    if release:self.set(state='available',latest_version=release['version'],size_bytes=release['size'],release_url=release['release_url'],message='Version '+release['version']+' is available.')
                    else:self.set(state='current',message='You are running the latest available stable version.')
            except HTTPError as error:
                self.set(state='error',message='No published release is available yet.' if error.code==404 else 'GitHub could not be checked. Wait a little and try again.')
            except UpdateError as error:self.set(state='error',message=str(error))
            except Exception:self.set(state='error',message='Could not check for updates. Check internet access and try again.')
        try:threading.Thread(target=work,daemon=True).start()
        except Exception:self.set(state='error',message='The update check could not start. Try again.');raise UpdateError('The update check could not start.') from None

    def download(self):
        with self.lock:
            if self.busy():raise UpdateError('An update check or download is already running.')
            if self.release is None:raise UpdateError('Check for an available update first.')
            release=dict(self.release)
            self.set(state='downloading',downloaded_bytes=0,message='Downloading the Windows installer...')
        def work():
            temporary=None
            try:
                self.root.mkdir(parents=True,exist_ok=True)
                target=self.root/release['filename']
                if not verified(target,release):
                    if shutil.disk_usage(self.root).free<release['size']+100*1024*1024:
                        raise UpdateError('Not enough free space in AppData to download this installer.')
                    temporary=self.root/(release['filename']+'.'+secrets.token_hex(8)+'.part')
                    digest=hashlib.sha256();total=0
                    with open_url(release['url']) as response,temporary.open('xb') as output:
                        while chunk:=response.read(1024*1024):
                            total+=len(chunk)
                            if total>release['size']:raise UpdateError('The installer size did not match. Download was blocked.')
                            output.write(chunk);digest.update(chunk)
                            self.set(downloaded_bytes=total)
                        output.flush();os.fsync(output.fileno())
                    if total!=release['size'] or digest.hexdigest()!=release['sha256']:
                        raise UpdateError('The installer checksum did not match. Download was blocked; try checking again.')
                    temporary.replace(target);temporary=None
                self.set(state='ready',downloaded_bytes=release['size'],message='Installer downloaded and verified. Finish recording/sharing, exit the app from the tray, then run the installer from the download folder. Your settings are kept.')
            except UpdateError as error:self.set(state='error',message=str(error))
            except Exception:self.set(state='error',message='Download failed. Check internet access and AppData free space, then try again. The current app is unchanged.')
            finally:
                if temporary is not None:
                    try:temporary.unlink(missing_ok=True)
                    except OSError:pass
        try:threading.Thread(target=work,daemon=True).start()
        except Exception:self.set(state='error',message='The download could not start. Try again.');raise UpdateError('The download could not start.') from None

    def folder(self):
        with self.lock:
            if self.result['state']!='ready' or not self.release:raise UpdateError('Download and verify the installer first.')
            if not verified(self.root/self.release['filename'],self.release):
                self.set(state='error',message='The downloaded installer changed. Download it again before installing.')
                raise UpdateError('The downloaded installer changed. Download it again before installing.')
            return self.root.resolve()
