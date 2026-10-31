import hashlib
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch,Mock
import app_updates
import test_workflows
import app as recorder


def release(data=b'fake Windows installer',tag='v1.0.44'):
    name='MiracleOfLifeRecorder-Setup-'+tag[1:]+'.exe'
    return {'draft':False,'prerelease':False,'tag_name':tag,'assets':[{
        'name':name,'state':'uploaded','size':len(data),'digest':'sha256:'+hashlib.sha256(data).hexdigest(),
        'browser_download_url':f'https://github.com/{app_updates.REPOSITORY}/releases/download/{tag}/{name}'}]}


class Updates(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'updates';self.updater=app_updates.Updater(self.root,'1.0.43')
        inline=lambda target,daemon:types.SimpleNamespace(start=target)
        patcher=patch.object(app_updates.threading,'Thread',side_effect=inline)
        patcher.start();self.addCleanup(patcher.stop)

    def check(self,data=None):
        with patch.object(app_updates,'open_url',return_value=io.BytesIO(json.dumps(data or release()).encode())) as request:
            self.updater.check()
        request.assert_called_once_with(app_updates.API)

    def test_version_comparison_is_numeric_and_never_downgrades(self):
        self.assertGreater(app_updates.version('1.0.100'),app_updates.version('1.0.99'))
        self.assertIsNone(app_updates.parse_release(release(tag='v1.0.43'),'1.0.43'))
        self.assertIsNone(app_updates.parse_release(release(tag='v1.0.42'),'1.0.43'))
        for value in ['../1.0.44','1.0.44-beta',None,'v1.0']:
            with self.assertRaises(app_updates.UpdateError):app_updates.version(value)

    def test_only_expected_stable_installer_with_digest_is_accepted(self):
        for mutate in [lambda d:d.update(draft=True),lambda d:d.update(prerelease=True),
                       lambda d:d['assets'][0].update(digest=None),lambda d:d['assets'][0].update(size=0),
                       lambda d:d['assets'][0].update(browser_download_url='https://evil.example/installer.exe'),
                       lambda d:d['assets'][0].update(name='other-installer.exe')]:
            data=release();mutate(data)
            with self.assertRaises(app_updates.UpdateError):app_updates.parse_release(data,'1.0.43')

    def test_status_does_not_check_internet_until_requested(self):
        with patch.object(app_updates,'open_url') as request:
            self.assertEqual(self.updater.status()['state'],'idle')
        request.assert_not_called()

    def test_download_verifies_hash_then_opens_only_the_folder(self):
        self.check()
        self.assertEqual(self.updater.status()['state'],'available')
        with patch.object(app_updates,'open_url',return_value=io.BytesIO(b'fake Windows installer')):
            self.updater.download()
        self.assertEqual(self.updater.status()['state'],'ready')
        self.assertEqual(self.updater.folder(),self.root.resolve())
        target=self.root/self.updater.release['filename']
        self.assertEqual(target.read_bytes(),b'fake Windows installer')
        with patch.object(app_updates,'open_url') as request:self.updater.download()
        request.assert_not_called()
        target.write_bytes(b'tampered')
        with self.assertRaises(app_updates.UpdateError):self.updater.folder()

    def test_truncated_changed_or_oversized_download_never_becomes_ready(self):
        for data in [b'short',b'x'*len(b'fake Windows installer'),b'x'*100]:
            self.check()
            with patch.object(app_updates,'open_url',return_value=io.BytesIO(data)):self.updater.download()
            self.assertEqual(self.updater.status()['state'],'error')
            self.assertEqual(list(self.root.glob('*.exe')),[])
            self.assertEqual(list(self.root.glob('*.part')),[])

    def test_redirects_and_insecure_urls_are_blocked(self):
        for url in ['http://github.com/'+app_updates.REPOSITORY+'/releases/download/a.exe',
                    'https://evil.example/a.exe','https://github.com/other/repo/releases/download/a.exe',
                    'https://user:password@api.github.com/a','https://api.github.com:444/a']:
            self.assertFalse(app_updates.allowed_url(url))
        with self.assertRaises(app_updates.UpdateError):
            app_updates.GitHubRedirects().redirect_request(Mock(),None,302,'',{},'https://evil.example/a')

    def test_failed_check_and_no_disk_space_are_safe(self):
        with patch.object(app_updates,'open_url',side_effect=OSError('private local information')):self.updater.check()
        self.assertEqual(self.updater.status()['state'],'error')
        self.assertNotIn('private',self.updater.status()['message'])
        self.check()
        with patch.object(app_updates.shutil,'disk_usage',return_value=types.SimpleNamespace(free=0)),patch.object(app_updates,'open_url') as request:
            self.updater.download()
        self.assertEqual(self.updater.status()['state'],'error')
        request.assert_not_called()

    def test_busy_and_unchecked_downloads_rejected(self):
        with self.assertRaises(app_updates.UpdateError):self.updater.download()
        self.updater.set(state='checking')
        with self.assertRaises(app_updates.UpdateError):self.updater.check()
        with self.assertRaises(app_updates.UpdateError):self.updater.download()


class UpdateRoutes(unittest.TestCase):
    setUp=test_workflows.Workflows.setUp
    post=test_workflows.Workflows.post

    def test_status_and_mutations_require_local_recorder_auth(self):
        self.assertEqual(self.client.get('/api/updates/status').status_code,403)
        for route in ['check','download','folder']:
            self.assertEqual(self.client.post('/api/updates/'+route,json={}).status_code,403)
        with patch.object(app_updates,'open_url') as network:
            data=self.client.get('/api/updates/status',headers=self.headers).json
        self.assertEqual(data['current_version'],recorder.__version__)
        network.assert_not_called()

    def test_download_cannot_supply_arbitrary_url_or_open_unverified_folder(self):
        response=self.post('/api/updates/download',{'url':'https://evil.example/installer.exe'})
        self.assertEqual(response.status_code,409)
        with patch.object(recorder.os,'startfile',create=True) as opener:
            self.assertEqual(self.post('/api/updates/folder').status_code,409)
        opener.assert_not_called()
