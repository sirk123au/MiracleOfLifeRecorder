from pathlib import Path
import unittest
from unittest.mock import patch,Mock
import recording_recovery
import test_workflows
import app as recorder


class MetadataCleanup(unittest.TestCase):
    def test_brief_windows_sharing_lock_is_retried(self):
        path=Mock()
        locked=PermissionError('private metadata path');locked.winerror=32
        path.unlink.side_effect=[locked,locked,None]
        with patch.object(recording_recovery.time,'sleep') as sleep:
            recording_recovery.remove_metadata(path)
        self.assertEqual(path.unlink.call_count,3)
        self.assertEqual(sleep.call_count,2)

    def test_permanent_lock_has_bounded_retries(self):
        path=Mock()
        locked=PermissionError();locked.winerror=33
        path.unlink.side_effect=locked
        with patch.object(recording_recovery.time,'sleep') as sleep:
            with self.assertRaises(PermissionError):recording_recovery.remove_metadata(path)
        self.assertEqual(path.unlink.call_count,5)
        self.assertEqual(sleep.call_count,4)

    def test_unrelated_permission_error_is_not_suppressed_or_retried(self):
        path=Mock();path.unlink.side_effect=PermissionError('access denied')
        with patch.object(recording_recovery.time,'sleep') as sleep:
            with self.assertRaises(PermissionError):recording_recovery.remove_metadata(path)
        sleep.assert_not_called()


class CleanupWorkflows(unittest.TestCase):
    setUp=test_workflows.Workflows.setUp
    post=test_workflows.Workflows.post
    start=test_workflows.Workflows.start
    output=test_workflows.Workflows.output

    def test_locked_recovery_marker_keeps_saved_video_link_and_can_retry(self):
        self.start(name='Example Patient');self.output()
        original=Path.unlink
        locked=PermissionError('private patient path');locked.winerror=32
        def unlink(path,*args,**kwargs):
            if path==recorder.RECOVERY_POINTER:raise locked
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',autospec=True,side_effect=unlink),patch.object(recording_recovery.time,'sleep'):
            response=self.post('/api/stop')
        self.assertEqual(response.status_code,503)
        self.assertIn('video is saved and linked',response.json['error'])
        self.assertNotIn('database link could not be saved',response.json['error'])
        self.assertNotIn('private patient path',response.json['error'])
        saved=recorder.state['pending']
        self.assertTrue(saved.exists())
        details=recorder.patient_store.for_recording(self.data_root,str(saved.relative_to(self.root)),'TESTVOL')
        self.assertEqual(details['name'],'Example Patient')
        self.assertEqual(self.start().status_code,409)
        self.assertEqual(self.post('/api/stop').status_code,200)
        self.assertTrue(saved.exists())
        self.assertEqual(len(list(saved.parent.glob('*.mp4'))),1)
        self.obs.stop_record.assert_called_once()
        self.assertFalse(recorder.RECOVERY_POINTER.exists())

    def test_transient_marker_lock_does_not_show_a_false_save_error(self):
        self.start();self.output()
        original=Path.unlink;attempts=[]
        locked=PermissionError();locked.winerror=32
        def unlink(path,*args,**kwargs):
            if path==recorder.RECOVERY_POINTER:
                attempts.append(path)
                if len(attempts)<3:raise locked
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',autospec=True,side_effect=unlink),patch.object(recording_recovery.time,'sleep'):
            response=self.post('/api/stop')
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(len(attempts),3)
        self.assertEqual(len(list(recorder.state['destination'].glob('*.mp4'))),1)
