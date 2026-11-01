import ctypes
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import sys
import app_updates
import update_install
import test_workflows
import app as recorder


class AutomaticSchedule(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.updater=app_updates.Updater(Path(self.temp.name),'1.0.46')

    def test_disabled_or_busy_does_not_check_or_download(self):
        with patch.object(self.updater,'check') as check,patch.object(self.updater,'download') as download:
            self.updater.automatic_tick(False,False,10)
            self.updater.automatic_tick(True,True,10)
            self.updater.set(state='checking')
            self.updater.automatic_tick(True,False,10)
        check.assert_not_called();download.assert_not_called()

    def test_startup_and_six_hour_checks_with_no_tight_error_retry(self):
        with patch.object(self.updater,'check') as check:
            self.updater.automatic_tick(True,False,10)
            self.updater.set(state='error')
            self.updater.automatic_tick(True,False,100)
            self.assertEqual(check.call_count,1)
            self.updater.automatic_tick(True,False,10+6*3600)
            self.assertEqual(check.call_count,2)

    def test_available_release_downloads_only_once_and_only_idle(self):
        self.updater.set(state='available')
        with patch.object(self.updater,'download',side_effect=lambda:self.updater.set(state='downloading')) as download:
            self.updater.automatic_tick(True,True,10)
            download.assert_not_called()
            self.updater.automatic_tick(True,False,11)
            self.updater.automatic_tick(True,False,12)
            download.assert_called_once()
        self.updater.set(state='ready')
        with patch.object(self.updater,'check') as check:self.updater.automatic_tick(True,False,999999)
        check.assert_not_called()


class AutomaticApp(unittest.TestCase):
    setUp=test_workflows.Workflows.setUp
    post=test_workflows.Workflows.post

    def test_preference_is_boolean_and_saved(self):
        self.assertTrue(recorder.settings['auto_update'])
        self.assertEqual(self.post('/api/settings',{'auto_update':False}).status_code,200)
        self.assertFalse(recorder.settings['auto_update'])
        self.assertIn('"auto_update": false',recorder.CFG.read_text())
        self.assertEqual(self.post('/api/settings',{'auto_update':'yes'}).status_code,400)
        self.assertFalse(recorder.settings['auto_update'])

    def test_scheduler_sees_recording_recovery_and_sharing_as_busy(self):
        updater=Mock()
        with patch.object(recorder,'get_updater',return_value=updater):
            for field,value in [('active',True),('pending',Path('pending.mp4')),('recovery_blocked',True)]:
                recorder.state[field]=value
                recorder.automatic_update_tick()
                self.assertTrue(updater.automatic_tick.call_args.args[1])
                recorder.state[field]=False if field!='pending' else None
            recorder.family_copy_lock.acquire()
            try:
                recorder.automatic_update_tick()
                self.assertTrue(updater.automatic_tick.call_args.args[1])
            finally:recorder.family_copy_lock.release()

    def test_exit_never_starts_update_or_shutdown_during_work(self):
        icon=Mock();server=Mock()
        with patch.object(recorder,'open_dashboard'),patch.object(update_install,'handoff') as handoff:
            for field,value in [('active',True),('pending',Path('pending.mp4')),('recovery_blocked',True)]:
                recorder.state[field]=value
                self.assertFalse(recorder.exit_application(icon,server))
                recorder.state[field]=False if field!='pending' else None
            recorder.family_copy_lock.acquire()
            try:self.assertFalse(recorder.exit_application(icon,server))
            finally:recorder.family_copy_lock.release()
        handoff.assert_not_called();icon.stop.assert_not_called();server.shutdown.assert_not_called()

    def test_idle_exit_hands_off_verified_update_before_shutdown(self):
        updater=Mock();updater.status.return_value={'state':'ready'};order=[]
        icon=Mock();server=Mock()
        icon.stop.side_effect=lambda:order.append('tray-stopped')
        server.shutdown.side_effect=lambda:order.append('server-stopped')
        with patch.object(recorder,'get_updater',return_value=updater),patch.object(update_install,'installed_executable',return_value=Path('installed.exe')),patch.object(update_install,'handoff',side_effect=lambda _:order.append('handoff')):
            self.assertTrue(recorder.exit_application(icon,server))
        self.assertEqual(order,['handoff','tray-stopped','server-stopped'])
        self.assertTrue(recorder.state['exiting'])
        self.assertEqual(self.post('/api/start',{'patient_id':'DUMMY'}).status_code,409)

    def test_source_run_and_disabled_updates_do_not_launch_installer(self):
        updater=Mock();updater.status.return_value={'state':'ready'}
        with patch.object(recorder,'get_updater',return_value=updater),patch.object(update_install,'installed_executable',return_value=None),patch.object(update_install,'handoff') as handoff:
            self.assertTrue(recorder.exit_application(Mock(),Mock()))
            recorder.state['exiting']=False;recorder.settings['auto_update']=False
            self.assertTrue(recorder.exit_application(Mock(),Mock()))
        handoff.assert_not_called()

    def test_failed_handoff_does_not_trap_the_operator_in_the_app(self):
        updater=Mock();updater.status.return_value={'state':'ready'}
        with patch.object(recorder,'get_updater',return_value=updater),patch.object(update_install,'installed_executable',return_value=Path('installed.exe')),patch.object(update_install,'handoff',side_effect=OSError('private path')):
            self.assertTrue(recorder.exit_application(Mock(),Mock()))
        self.assertEqual(updater.set.call_args.kwargs['state'],'error')
        self.assertNotIn('private',updater.set.call_args.kwargs['message'])


class InstallerHandoff(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_helper_waits_for_exit_and_checks_hash_without_killing_anything(self):
        path=self.root/"O'Brien __PID__"/'setup.exe'
        script=update_install.helper_script(path,{'sha256':'a'*64},self.root/'result.json',1234)
        self.assertIn("O''Brien __PID__",script)
        self.assertIn('GetProcessById(1234)',script)
        self.assertLess(script.index('WaitForExit'),script.index('ComputeHash'))
        self.assertLess(script.index('ComputeHash'),script.index('[System.Diagnostics.Process]::Start'))
        self.assertIn('OpenExisting',script)
        self.assertIn('/NORESTART',script)
        self.assertIn('/NOCLOSEAPPLICATIONS',script)
        self.assertIn('[System.Diagnostics.ProcessWindowStyle]::Hidden',script)
        for forbidden in ['Stop-Process','taskkill','Restart-Computer','Invoke-Expression','Remove-Item']:
            self.assertNotIn(forbidden,script)

    def test_no_installer_for_source_or_portable_build(self):
        with patch.object(sys,'frozen',False,create=True):self.assertIsNone(update_install.installed_executable())
        exe=self.root/'MiracleOfLifeRecorder.exe';exe.touch()
        with patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',str(exe)):
            self.assertIsNone(update_install.installed_executable())
            (self.root/'unins000.exe').touch()
            self.assertEqual(update_install.installed_executable(),exe.resolve())

    def updater(self):
        updater=app_updates.Updater(self.root,'1.0.46')
        path=self.root/'MiracleOfLifeRecorder-Setup-1.0.47.exe';path.write_bytes(b'fake installer')
        updater.release={'filename':path.name,'size':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        updater.set(state='ready')
        return updater,path

    def test_handoff_only_writes_helper_and_launches_it_hidden(self):
        updater,path=self.updater()
        with patch.object(update_install,'installed_executable',return_value=self.root/'app.exe'),patch.object(update_install,'launch_helper') as launch:
            update_install.handoff(updater)
        command,folder=launch.call_args.args
        self.assertIn('-NonInteractive',command)
        self.assertIn('Hidden',command)
        helper=Path(command[-1]);self.assertTrue(helper.is_file())
        self.assertIn(updater.release['sha256'],helper.read_text(encoding='utf-8-sig'))
        self.assertEqual(folder,self.root.resolve())
        self.assertEqual(path.read_bytes(),b'fake installer')

    def test_tampered_installer_never_launches_helper(self):
        updater,path=self.updater();path.write_bytes(b'changed')
        with patch.object(update_install,'installed_executable',return_value=self.root/'app.exe'),patch.object(update_install,'launch_helper') as launch:
            with self.assertRaises(app_updates.UpdateError):update_install.handoff(updater)
        launch.assert_not_called()

    def test_clean_launch_removes_inherited_runtime_search_paths(self):
        runtime=self.root/'_internal';runtime.mkdir()
        kernel=Mock()
        def get_directory(capacity,buffer):buffer.value=str(runtime);return len(str(runtime))
        kernel.GetDllDirectoryW.side_effect=get_directory;kernel.SetDllDirectoryW.return_value=True
        with patch.object(update_install.ctypes,'WinDLL',return_value=kernel),patch.object(sys,'_MEIPASS',str(runtime),create=True),patch.dict(os.environ,{'PATH':str(runtime)+os.pathsep+str(self.root/'safe'),'_PYI_TEST':'not-inherited'}),patch.object(update_install.subprocess,'Popen') as spawn:
            update_install.launch_helper(['powershell.exe','-WindowStyle','Hidden'],self.root)
        env=spawn.call_args.kwargs['env']
        self.assertNotIn('_PYI_TEST',env)
        self.assertNotIn(str(runtime),env['PATH'])
        self.assertEqual(env['PYINSTALLER_RESET_ENVIRONMENT'],'1')
        self.assertEqual([call.args[0] for call in kernel.SetDllDirectoryW.call_args_list],[None,str(runtime)])

    def test_last_install_result_never_echoes_raw_message_content(self):
        (self.root/'install-result.json').write_text('{"state":"error","message":"private patient path"}')
        result=update_install.last_result(self.root)
        self.assertIn('did not complete',result)
        self.assertNotIn('private',result)
