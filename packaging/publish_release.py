"""Publish a built installer using the existing GitHub Git sign-in (never print tokens)."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from version import __version__

REPO='sirk123au/MiracleOfLifeRecorder'
API='https://api.github.com/repos/'+REPO


def git(*args):
    return subprocess.run(['git',*args],cwd=ROOT,check=True,capture_output=True,text=True).stdout.strip()


def publish():
    tag='v'+__version__;name=f'MiracleOfLifeRecorder-Setup-{__version__}.exe'
    installer=ROOT/'dist'/'installer'/name
    if not installer.is_file():raise RuntimeError('Build the matching installer first.')
    if git('status','--porcelain'):raise RuntimeError('Commit and push the reviewed source before publishing.')
    sha=git('rev-parse','HEAD')
    if git('rev-parse',tag+'^{}')!=sha:raise RuntimeError('The release tag must point to the current source commit.')
    env={**os.environ,'GIT_TERMINAL_PROMPT':'0','GCM_INTERACTIVE':'Never'}
    result=subprocess.run(['git','credential','fill'],input='protocol=https\nhost=github.com\n\n',
                          cwd=ROOT,env=env,capture_output=True,text=True)
    if result.returncode:raise RuntimeError('GitHub Git sign-in is unavailable. Sign into GitHub for Git first.')
    values=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
    if not values.get('password'):raise RuntimeError('No GitHub publishing credential is available.')
    authorization='Basic '+base64.b64encode((values.get('username','git')+':'+values['password']).encode()).decode()

    def request(url,method='GET',data=None,content_type='application/json'):
        if not (url.startswith(API+'/') or url.startswith('https://uploads.github.com/repos/'+REPO+'/')):
            raise RuntimeError('Unsupported publishing endpoint.')
        encoded=json.dumps(data).encode() if isinstance(data,dict) else data
        req=Request(url,data=encoded,method=method,headers={'Authorization':authorization,'Accept':'application/vnd.github+json',
                    'Content-Type':content_type,'User-Agent':'MiracleOfLifeRecorder-Release','X-GitHub-Api-Version':'2022-11-28'})
        with urlopen(req,timeout=120) as response:return json.load(response)

    request(API+'/commits/'+sha)  # Publishing never substitutes an unpushed local commit.
    try:release=request(API+'/releases/tags/'+tag)
    except HTTPError as error:
        if error.code!=404:raise
        notes=(ROOT/'CHANGELOG.md').read_text(encoding='utf-8')
        release=request(API+'/releases','POST',{'tag_name':tag,'target_commitish':sha,'name':'Miracle of Life Recorder '+__version__,
            'body':notes+'\n\nDownload the Setup EXE below. SHA256SUMS.txt verifies the installer. Finish recording/sharing and exit the recorder from the tray before installing.','draft':True,'prerelease':False})
    if not release['draft']:raise RuntimeError('This release is already published; published assets will not be replaced.')
    data=installer.read_bytes();digest=hashlib.sha256(data).hexdigest()
    checksums=installer.parent/'SHA256SUMS.txt'
    checksums.write_text(digest+'  '+name+'\n',encoding='ascii')
    for filename,payload,content in [(name,data,'application/vnd.microsoft.portable-executable'),('SHA256SUMS.txt',checksums.read_bytes(),'text/plain')]:
        existing=next((item for item in release['assets'] if item['name']==filename),None)
        expected='sha256:'+hashlib.sha256(payload).hexdigest()
        if existing:
            if existing.get('digest')!=expected:raise RuntimeError('A draft asset differs from this build; it will not be overwritten.')
            continue
        asset=request(f'https://uploads.github.com/repos/{REPO}/releases/{release["id"]}/assets?name={filename}',
                      'POST',payload,content)
        if asset.get('digest')!=expected:raise RuntimeError('GitHub did not confirm the uploaded asset checksum. Release remains a draft.')
    final=request(API+'/releases/'+str(release['id']),'PATCH',{'draft':False,'make_latest':'true'})
    print('Published '+final['html_url'])


if __name__=='__main__':
    try:publish()
    except HTTPError as error:
        print('GitHub publishing failed (HTTP '+str(error.code)+'). No credentials or response bodies were printed.',file=sys.stderr);sys.exit(1)
    except Exception as error:
        if isinstance(error,RuntimeError):print(str(error),file=sys.stderr)
        else:print('Publishing failed. Check GitHub Releases for a draft before retrying.',file=sys.stderr)
        sys.exit(1)
