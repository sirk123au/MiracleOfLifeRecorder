import json
import sys
import tempfile
import types
import unittest
import io
import base64
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import Mock, patch

# Connections are mocked in setUp; importing the package does not contact OBS.
import app as recorder


class Workflows(unittest.TestCase):
    def test_network_testing_requires_password_before_serving_dashboard(self):
        with patch.object(recorder, 'NETWORK_MODE', True), \
             patch.object(recorder, 'NETWORK_PASSWORD', 'example-test-password'), \
             patch.object(recorder, 'ALLOWED_HOSTS', {'test.example.com'}):
            response = self.client.get('/', base_url='https://test.example.com',
                                       environ_base={'REMOTE_ADDR': '192.0.2.1'})
            self.assertEqual(response.status_code, 401)
            credentials = base64.b64encode(b'tester:example-test-password').decode()
            headers = {'Authorization': 'Basic '+credentials}
            response = self.client.get('/', base_url='https://test.example.com', headers=headers,
                                       environ_base={'REMOTE_ADDR': '192.0.2.1'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.client.post('/api/start', json={'test': True},
                base_url='https://test.example.com', headers=headers).status_code, 403)
            self.assertEqual(self.client.get('/', base_url='https://untrusted.example.com',
                headers=headers).status_code, 403)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.appdata = self.root / 'appdata'
        self.appdata.mkdir()
        self.data_root = self.appdata / 'patients'
        for target, value in [('ROOT', self.appdata), ('CFG', self.appdata / 'settings.json')]:
            location_patch = patch.object(recorder, target, value)
            location_patch.start()
            self.addCleanup(location_patch.stop)
        volume_patch = patch.object(recorder, 'volume_id', return_value='TESTVOL')
        volume_patch.start()
        self.addCleanup(volume_patch.stop)
        pointer_patch = patch.object(recorder, 'RECOVERY_POINTER', self.appdata / 'pending-session.json')
        pointer_patch.start()
        self.addCleanup(pointer_patch.stop)
        self.client = recorder.app.test_client()
        recorder.preview_capture = recorder.obs_preview.Preview()
        music_patch = patch.object(recorder.spotify_status, 'check', return_value={'playing': True, 'available': True, 'message': 'Spotify is playing'})
        music_patch.start()
        self.addCleanup(music_patch.stop)
        music_control_patch=patch.object(recorder.spotify_status,'control',return_value={'message':'Playback requested'})
        music_control_patch.start()
        self.addCleanup(music_control_patch.stop)
        self.headers = {'X-Recorder-Token': recorder.TOKEN}
        self.old_settings = recorder.settings.copy()
        recorder.settings.update(recorder.DEFAULT)
        self.addCleanup(lambda: recorder.settings.update(self.old_settings))
        recorder.state.update(music_warning='',observed_recording_mbps=None,preflight_id=None,preflight_result=None,active=False, pending=None, patient=None, started=None,
                              destination=None, staging=None, message='Ready', warning='', storage_warning='', recovery_blocked=False, planned=None)
        recorder.playback_files.clear()
        recorder.recording_versions.clear()
        recorder.obs_launch = None
        self.real_prepare_capture = recorder.obs_branding.prepare_capture
        capture_patch = patch.object(recorder.obs_branding, 'prepare_capture',
            side_effect=lambda client, settings, test=False: recorder.obs_branding.prepare(client, settings))
        capture_patch.start()
        self.addCleanup(capture_patch.stop)
        self.obs = Mock()
        self.obs.get_record_status.return_value.output_active = False
        self.obs.get_record_directory.side_effect = lambda: types.SimpleNamespace(record_directory=self.obs.set_record_directory.call_args.args[0])
        for target, value in [('base_dir', lambda: self.root), ('client', lambda: self.obs)]:
            patcher = patch.object(recorder, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def post(self, path, data=None):
        return self.client.post(path, json=data or {}, headers=self.headers)

    def start(self, **extra):
        data = {'patient_id': 'DUMMY', **extra}
        records = self.post('/api/patients/search', {'patient_id': data['patient_id'], 'test': data.get('test', False)}).json.get('records', [])
        if records:data.setdefault('expected_record_id', records[0]['id'])
        return self.post('/api/start', data)

    def update_patient(self, data):
        records = self.post('/api/patients/search', {'patient_id': data['patient_id'], 'test': data.get('test', False)}).json.get('records', [])
        if records:data = {**data, 'expected_record_id': records[0]['id']}
        return self.post('/api/patients/save', data)

    def output(self, suffix='.mp4'):
        path = recorder.state['staging'] / ('obs' + suffix)
        path.write_bytes(b'simulated recording')
        self.obs.stop_record.return_value.output_path = str(path)
        return path

    def test_start_stop_preserves_existing_recording(self):
        self.assertEqual(self.start().status_code, 200)
        source = self.output()
        target = recorder.state['destination'] / ('DUMMY_' + recorder.state['started'].strftime('%Y-%m-%d_%H%M%S') + '.mp4')
        target.write_bytes(b'existing recording')
        self.assertEqual(self.post('/api/stop').status_code, 200)
        self.assertEqual(target.read_bytes(), b'existing recording')
        self.assertFalse(source.exists())
        self.assertEqual(len(list(target.parent.glob('*.mp4'))), 2)

    def test_stop_failure_keeps_controls_locked(self):
        self.start()
        self.obs.stop_record.side_effect = RuntimeError('secret patient path')
        response = self.post('/api/stop')
        self.assertEqual(response.status_code, 503)
        self.assertTrue(recorder.state['active'])
        self.assertNotIn('secret patient path', response.get_data(as_text=True))
        self.assertEqual(self.start().status_code, 409)

    def test_save_failure_can_retry_without_stopping_obs_twice(self):
        self.start()
        source = self.output()
        destination = recorder.state['destination']
        destination.rmdir()
        self.assertEqual(self.post('/api/stop').status_code, 500)
        self.assertTrue(source.exists())
        self.assertEqual(self.start().status_code, 409)
        self.assertEqual(self.post('/api/settings').status_code, 409)
        destination.mkdir()
        self.assertEqual(self.post('/api/stop').status_code, 200)
        self.obs.stop_record.assert_called_once()

    def test_unexpected_format_retained(self):
        self.start()
        source = self.output('.mkv')
        self.assertEqual(self.post('/api/stop').status_code, 500)
        self.assertTrue(source.exists())
        self.assertIsNotNone(recorder.state['pending'])

    def test_uncertain_start_blocks_another_start(self):
        self.obs.start_record.side_effect = TimeoutError()
        self.assertEqual(self.start().status_code, 503)
        self.assertTrue(recorder.state['active'])
        self.assertEqual(self.start().status_code, 409)

    def test_invalid_patient_ids_are_json_errors(self):
        for pid in ['../outside', 'CON', 'LPT1', '']:
            self.assertEqual(self.start(patient_id=pid).status_code, 400)
        self.obs.start_record.assert_not_called()

    def test_test_mode_uses_separate_folder(self):
        self.assertEqual(self.start(test=True).status_code, 200)
        self.obs.get_input_list.assert_not_called()
        self.assertEqual(recorder.state['destination'], self.root / 'Test Recordings')
        self.output()
        self.assertEqual(self.post('/api/stop').status_code, 200)

    def test_invalid_settings_do_not_mutate_configuration(self):
        original = recorder.settings.copy()
        self.assertEqual(self.post('/api/settings', {'obs_port': 'invalid'}).status_code, 400)
        self.assertEqual(recorder.settings, original)

    def test_playback_uses_opaque_id_and_requires_authentication(self):
        self.start()
        self.output()
        self.post('/api/stop')
        response = self.post('/api/files', {'q': 'DUMMY'})
        item = response.json['files'][0]
        self.assertNotIn('DUMMY', item['key'])
        self.assertEqual(self.client.get('/video?id=' + item['key']).status_code, 403)
        response = self.client.get('/video?id=' + item['key'], headers=self.headers)
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertEqual(self.client.get('/video?file=../secret.mp4', headers=self.headers).status_code, 404)

    def test_security_headers_and_mutation_token(self):
        response = self.client.get('/')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertIn('HttpOnly', response.headers['Set-Cookie'])
        self.assertEqual(self.client.post('/api/start', json={'test': True}).status_code, 403)
        response = self.client.get('/api/status', headers=self.headers,
                                   environ_base={'REMOTE_ADDR': '192.0.2.1'})
        self.assertEqual(response.status_code, 403)

    def test_preview_failure_is_safe(self):
        self.obs.get_current_program_scene.side_effect = RuntimeError('private details')
        response = self.client.get('/api/preview', headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b'private details', response.data)

    def test_low_disk_space_blocks_start_but_allows_browsing(self):
        with patch.object(recorder.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=0)):
            self.assertEqual(self.start().status_code, 400)
            self.assertEqual(self.post('/api/files').status_code, 200)
        self.obs.start_record.assert_not_called()

    def test_existing_obs_recording_is_not_taken_over(self):
        self.obs.get_record_status.return_value.output_active = True
        self.assertEqual(self.start().status_code, 400)
        self.obs.set_record_directory.assert_not_called()
        self.obs.start_record.assert_not_called()

    def test_preflight_uses_separate_test_record_and_saved_playback(self):
        with patch.object(recorder,'schedule_preflight') as schedule:
            response=self.post('/api/start',{'preflight':True,'patient_id':'PRIVATE','name':'Private name'})
        self.assertEqual(response.status_code,200)
        self.assertTrue(recorder.state['test'])
        self.assertTrue(recorder.state['patient'].startswith('TEST-'))
        token=recorder.state['preflight_id']
        schedule.assert_called_once_with(token)
        self.output()
        recorder.finish_preflight(token)
        self.assertFalse(recorder.state['active'])
        self.assertIsNone(recorder.state['pending'])
        result=recorder.state['preflight_result']
        self.assertIn('key',result)
        self.assertTrue(recorder.playback_files[result['key']].is_file())
        self.assertIn('Test Recordings',str(recorder.playback_files[result['key']]))

    def test_preflight_timer_does_not_stop_a_later_recording(self):
        self.start()
        recorder.finish_preflight('obsolete-test-token')
        self.assertTrue(recorder.state['active'])
        self.obs.stop_record.assert_not_called()

    def test_recording_health_is_authenticated_and_sanitizes_failures(self):
        self.assertEqual(self.client.get('/api/recording-health').status_code,403)
        self.obs.get_stats.side_effect=OSError('private credentials')
        result=self.client.get('/api/recording-health',headers=self.headers)
        self.assertEqual(result.status_code,503)
        self.assertNotIn(b'private credentials',result.data)

    def test_recording_health_uses_obs_audio_signal_not_spotify_playback(self):
        self.start()
        recorder.state['sequence']={'audio':{'source':'Spotify','deferred':False}}
        self.obs.get_input_mute.return_value.input_muted=False
        self.obs.get_stats.return_value=types.SimpleNamespace(cpu_usage=10,active_fps=30,render_skipped_frames=0,output_skipped_frames=0)
        with patch.object(recorder.health_meter,'ensure'), patch.object(recorder.health_meter,'status',return_value='quiet'):
            result=self.client.get('/api/recording-health',headers=self.headers)
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json['audio'],'quiet')
        self.assertIn('No background audio', ' '.join(result.json['warnings']))

    def test_usb_readiness_requires_auth_and_reports_disconnected_selected_drive(self):
        self.assertEqual(self.client.get('/api/usb-readiness').status_code,403)
        recorder.settings['recording_volume']='0000007B'
        recorder.settings['recording_drive_name']='Saved USB'
        with patch.object(recorder,'family_usb_drives',return_value=[]):
            response=self.client.get('/api/usb-readiness',headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['status'],'disconnected')
        self.assertEqual(response.json['drive'],'Saved USB')

    def test_usb_readiness_errors_do_not_expose_private_details(self):
        recorder.settings['recording_volume']='0000007B'
        with patch.object(recorder,'family_usb_drives',side_effect=OSError('private path')):
            response=self.client.get('/api/usb-readiness',headers=self.headers)
        self.assertEqual(response.status_code,503)
        self.assertNotIn(b'private path',response.data)
        self.assertEqual(response.json['status'],'unavailable')

    def test_record_restarts_music_and_confirmed_stop_pauses_it(self):
        order=[]
        self.obs.start_record.side_effect=lambda:order.append('record')
        with patch.object(recorder.spotify_status,'control',side_effect=lambda action:order.append(action)):
            self.assertEqual(self.start().status_code,200)
            self.output()
            self.obs.stop_record.side_effect=lambda:(order.append('stopped') or types.SimpleNamespace(output_path=str(recorder.state['staging']/'obs.mp4')))
            self.assertEqual(self.post('/api/stop').status_code,200)
        self.assertEqual(order,['pause','record','restart','stopped','pause'])

    def test_paused_spotify_does_not_start_until_intro_finishes(self):
        order=[]
        sequence={'logo':'Logo','live':'Live','fade':'Fade','media_input':'Intro',
                  'audio':{'source':'Spotify','mute':[],'snapshot':{},'capture_volume':1}}
        self.obs.start_record.side_effect=lambda:order.append('record')
        self.obs.set_input_mute.side_effect=lambda name,muted:order.append('unmute') if not muted else None
        with patch.object(recorder.spotify_status,'check',return_value={'playing':False,'session_available':True,'message':'Paused'}), \
             patch.object(recorder.spotify_status,'control',side_effect=lambda action:order.append(action)), \
             patch.object(recorder.obs_branding,'prepare',return_value=sequence), \
             patch.object(recorder.obs_branding,'show_intro',side_effect=lambda *args:order.append('intro')), \
             patch.object(recorder.obs_branding,'wait_for_video',side_effect=lambda *args:order.append('intro-ended')), \
             patch.object(recorder.obs_branding.time,'sleep'):
            self.assertEqual(self.start().status_code,200)
        self.assertEqual(order,['intro','record','intro-ended','restart','unmute'])

    def test_unconfirmed_obs_stop_does_not_pause_spotify(self):
        self.start()
        self.obs.stop_record.side_effect=TimeoutError()
        with patch.object(recorder.spotify_status,'control') as control:
            self.assertEqual(self.post('/api/stop').status_code,503)
            control.assert_not_called()
        self.assertTrue(recorder.state['active'])

    def test_spotify_pause_failure_does_not_prevent_video_save(self):
        self.start();source=self.output()
        with patch.object(recorder.spotify_status,'control',side_effect=recorder.spotify_status.SpotifyControlError('Unavailable')):
            response=self.post('/api/stop')
        self.assertEqual(response.status_code,200)
        self.assertIn('Spotify could not be paused',response.json['message'])
        self.assertIsNone(recorder.state['pending'])
        self.assertFalse(source.exists())
        self.assertEqual(len(list((self.root/'Patients'/'DUMMY').glob('*.mp4'))),1)

    def test_restart_warning_survives_recording_monitor_refresh(self):
        with patch.object(recorder.spotify_status,'control',side_effect=recorder.spotify_status.SpotifyControlError('Restart unavailable')):
            self.assertEqual(self.start().status_code,200)
        self.obs.get_record_status.return_value.output_active=True
        recorder.monitor_recording()
        self.assertIn('Restart unavailable',recorder.state['warning'])

    def test_preview_returns_jpeg(self):
        self.obs.get_current_program_scene.return_value.current_program_scene_name = 'Scene'
        self.obs.get_source_screenshot.return_value.image_data = 'data:image/jpeg;base64,aW1hZ2U='
        response = self.client.get('/api/preview', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'image/jpeg')
        self.assertEqual(response.data, b'image')

    def test_recording_clock_includes_preparation_time_and_survives_page_reload(self):
        from datetime import datetime,timedelta
        requested=datetime(2026,1,1,10,0,0)
        with patch.object(recorder,'datetime') as clock:
            clock.now.return_value=requested
            def prepare(*args,**kwargs):
                clock.now.return_value=requested+timedelta(seconds=15)
                return None
            with patch.object(recorder.obs_branding,'prepare',side_effect=prepare):
                self.assertEqual(self.start().status_code,200)
            self.assertEqual(recorder.state['started'],requested)
            status=self.client.get('/api/status',headers=self.headers)
            self.assertEqual(status.json['elapsed_seconds'],15)

    def test_pc_performance_presets_persist_and_limit_preview_only(self):
        self.obs.get_current_program_scene.return_value.current_program_scene_name='Scene'
        self.obs.get_source_screenshot.return_value.image_data='data:image/jpeg;base64,aW1hZ2U='
        for profile,width,height in [('low',960,540),('high',1920,1080)]:
            self.assertEqual(self.post('/api/settings',{'performance_profile':profile}).status_code,200)
            stored=json.loads(recorder.CFG.read_text())
            self.assertEqual(stored['performance_profile'],profile)
            self.assertEqual(self.client.get('/api/preview?mode=full',headers=self.headers).status_code,200)
            self.obs.get_source_screenshot.assert_called_with('Scene','jpg',width,height,80)
        self.obs.set_video_settings.assert_not_called()
        self.assertEqual(self.post('/api/settings',{'performance_profile':'invalid'}).status_code,400)
        self.assertEqual(recorder.settings['performance_profile'],'high')

    def test_patient_details_persist_and_history_is_preserved(self):
        details = {'patient_id': 'DUMMY', 'name': 'Example Patient',
                   'dob': '25/12/1990', 'scan': 'Standard Ultrasound',
                   'notes': 'First visit\nAdditional notes'}
        self.assertEqual(self.post('/api/patients/save', details).status_code, 200)
        details['notes'] = 'Updated visit notes'
        self.assertEqual(self.update_patient(details).status_code, 200)
        # New HTTP client and database connection still retrieve persisted data.
        self.client = recorder.app.test_client()
        found = self.post('/api/patients/search', {'q': 'Example Patient'}).json['records']
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['dob'], '25/12/1990')
        history = self.post('/api/patients/search', {'patient_id': 'dummy'}).json['records']
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]['notes'], 'Updated visit notes')
        self.assertEqual(history[1]['notes'], 'First visit\nAdditional notes')

    def test_recording_links_to_original_details(self):
        self.start(name='Example Patient', dob='25/12/1990', notes='Scan notes', scan='Gender Scan')
        self.output()
        self.assertEqual(self.post('/api/stop').status_code, 200)
        self.update_patient({'patient_id': 'DUMMY', 'name': 'Changed Name'})
        history = self.post('/api/patients/search', {'patient_id': 'DUMMY'}).json['records']
        self.assertEqual(history[1]['name'], 'Example Patient')
        self.assertEqual(history[1]['notes'], 'Scan notes')
        self.assertTrue((self.root / history[1]['recording']).is_file())

    def test_patient_database_failure_prevents_recording(self):
        with patch.object(recorder.patient_store, 'save', side_effect=OSError('private patient data')):
            response = self.start(name='Example Patient')
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(b'private patient data', response.data)
        self.obs.start_record.assert_not_called()

    def test_recording_link_failure_can_retry_after_file_is_moved(self):
        self.start(name='Example Patient')
        self.output()
        with patch.object(recorder.patient_store, 'attach_recording', side_effect=OSError()):
            self.assertEqual(self.post('/api/stop').status_code, 500)
        self.assertTrue(recorder.state['pending'].is_file())
        self.assertTrue(recorder.state['filed'])
        self.assertEqual(self.post('/api/stop').status_code, 200)
        self.obs.stop_record.assert_called_once()
        self.assertEqual(len(list((self.root / 'Patients' / 'DUMMY').glob('*.mp4'))), 1)

    def test_invalid_patient_details_are_not_saved(self):
        for details in [{'dob': '31/02/1990'}, {'dob': 'not a date'},
                        {'notes': 'x' * 10001}, {'name': ['invalid']}]:
            response = self.post('/api/patients/save', {'patient_id': 'DUMMY', **details})
            self.assertEqual(response.status_code, 400)
        self.assertFalse((self.data_root / 'patient-records.sqlite3').exists())

    def test_future_baby_birth_date_is_saved_and_recording_can_start(self):
        details = {'patient_id': 'DUMMY', 'dob': '01/01/2999'}
        self.assertEqual(self.post('/api/patients/save', details).status_code, 200)
        record = self.post('/api/patients/search', {'patient_id': 'DUMMY'}).json['records'][0]
        self.assertEqual(record['dob'], '01/01/2999')
        self.assertEqual(self.start(dob='01/01/2999').status_code, 200)

    def test_stop_accepts_obs_filename_subfolders_inside_staging(self):
        self.start()
        folder = recorder.state['staging'] / 'OBS dated recordings'
        folder.mkdir()
        output = folder / 'recording.mp4'
        output.write_bytes(b'recorded video')
        self.obs.stop_record.return_value.output_path = str(output)
        self.assertEqual(self.post('/api/stop').status_code, 200)
        self.assertFalse(output.exists())

    def test_save_retries_brief_windows_sharing_violation(self):
        source = Mock()
        blocked = PermissionError('private patient path')
        blocked.winerror = 32
        source.rename.side_effect = [blocked, None]
        with patch.object(recorder.time, 'sleep'):
            recorder.move_recording(source, Path('target.mp4'))
        self.assertEqual(source.rename.call_count, 2)

    def test_save_permission_error_explains_stage_and_keeps_retry_state(self):
        self.start()
        original = self.output()
        with patch.object(recorder, 'move_recording', side_effect=PermissionError('private patient path')):
            response = self.post('/api/stop')
        self.assertEqual(response.status_code, 500)
        self.assertIn('Windows denied access', response.json['error'])
        self.assertNotIn('private patient path', response.json['error'])
        self.assertTrue(original.exists())
        self.assertEqual(recorder.state['pending'], original)
        self.assertEqual(self.post('/api/stop').status_code, 200)

    def test_save_outside_staging_is_explained_and_never_moved(self):
        self.start()
        outside = self.root / 'other.mp4'
        outside.write_bytes(b'original')
        self.obs.stop_record.return_value.output_path = str(outside)
        response = self.post('/api/stop')
        self.assertIn('outside the assigned staging folder', response.json['error'])
        self.assertTrue(outside.exists())

    def simulate_restart(self):
        recorder.state.update(active=False,pending=None,patient=None,started=None,
                              staging=None,destination=None,filed=False,planned=None,
                              recovery_blocked=True,warning='',message='Ready')

    def test_recovery_restores_unfinished_video_and_patient_link(self):
        self.start(name='Example Patient')
        output = self.output()
        pointer = recorder.RECOVERY_POINTER.read_text()
        self.assertIn('DUMMY', pointer)
        self.assertFalse((recorder.state['staging'] / 'session.json').exists())
        self.assertNotIn('Example Patient', pointer)
        self.simulate_restart()
        recorder.recover_session()
        self.assertFalse(recorder.state['active'])
        self.assertEqual(recorder.state['pending'], output)
        self.assertEqual(self.start().status_code, 409)
        self.assertEqual(self.post('/api/stop').status_code, 200)
        self.obs.stop_record.assert_not_called()
        self.assertFalse(recorder.RECOVERY_POINTER.exists())
        key = self.post('/api/files').json['files'][0]['key']
        self.assertEqual(self.post('/api/recording-details', {'key': key}).json['details']['name'], 'Example Patient')

    def test_recovery_reconnects_to_same_obs_recording(self):
        self.start()
        staging = recorder.state['staging']
        self.simulate_restart()
        self.obs.get_record_status.return_value.output_active = True
        recorder.recover_session()
        self.assertTrue(recorder.state['active'])
        self.assertEqual(recorder.state['staging'], staging)
        self.assertFalse(recorder.state['recovery_blocked'])

    def test_recovery_never_takes_over_unrelated_obs_recording(self):
        self.start()
        self.simulate_restart()
        self.obs.get_record_status.return_value.output_active = True
        self.obs.get_record_directory.side_effect = None
        self.obs.get_record_directory.return_value.record_directory = str(self.root / 'unrelated')
        recorder.recover_session()
        self.assertTrue(recorder.state['recovery_blocked'])
        self.assertIn('different session', recorder.state['warning'])
        self.obs.stop_record.assert_not_called()

    def test_recovery_after_crash_between_rename_and_database_link(self):
        self.start()
        source = self.output()
        target = recorder.state['destination'] / 'retained.mp4'
        recorder.state.update(active=False,pending=source,planned=target)
        recorder.persist_session()
        source.rename(target)
        self.simulate_restart()
        recorder.recover_session()
        self.assertEqual(recorder.state['pending'], target)
        self.assertTrue(recorder.state['filed'])
        self.assertEqual(self.post('/api/stop').status_code, 200)
        self.assertTrue(target.is_file())
        self.assertEqual(len(list(target.parent.glob('*.mp4'))), 1)

    def test_recovery_with_missing_drive_blocks_new_scan(self):
        self.start()
        self.simulate_restart()
        with patch.object(recorder, 'base_dir', side_effect=OSError()):
            recorder.recover_session()
        self.assertTrue(recorder.state['recovery_blocked'])
        self.assertEqual(self.start().status_code, 409)
        self.assertTrue(recorder.RECOVERY_POINTER.exists())

    def test_monitor_detects_unexpected_stop_and_allows_save(self):
        self.start()
        source = self.output()
        recorder.monitor_recording()
        self.assertFalse(recorder.state['active'])
        self.assertEqual(recorder.state['pending'], source)
        self.assertIn('unexpectedly', recorder.state['message'])
        self.assertEqual(self.post('/api/stop').status_code, 200)
        self.obs.stop_record.assert_not_called()

    def test_monitor_warns_about_obs_disconnect_and_low_space(self):
        self.start()
        self.obs.get_record_status.side_effect = TimeoutError('private path')
        with patch.object(recorder.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=0)):
            recorder.monitor_recording()
        self.assertTrue(recorder.state['active'])
        self.assertIn('space is low', recorder.state['warning'])
        self.assertIn('OBS connection lost', recorder.state['warning'])
        self.assertNotIn('private path', recorder.state['warning'])

    def test_missing_or_ambiguous_recording_requires_review(self):
        self.start()
        self.output()
        (recorder.state['staging'] / 'another.mp4').write_bytes(b'another')
        self.simulate_restart()
        recorder.recover_session()
        self.assertTrue(recorder.state['recovery_blocked'])
        self.assertEqual(self.post('/api/recovery/clear-empty', {'confirm': True}).status_code, 409)

    def test_empty_interrupted_session_can_be_cleared_with_confirmation(self):
        self.start()
        self.simulate_restart()
        recorder.recover_session()
        self.assertTrue(recorder.state['recovery_blocked'])
        self.assertEqual(self.post('/api/recovery/clear-empty').status_code, 400)
        self.assertEqual(self.post('/api/recovery/clear-empty', {'confirm': True}).status_code, 200)
        self.assertFalse(recorder.RECOVERY_POINTER.exists())
        self.assertEqual(len(self.post('/api/patients/search', {'include_archived': True}).json['records']), 1)

    def test_journal_failure_prevents_obs_start(self):
        with patch.object(recorder.recording_recovery, 'begin', side_effect=OSError()):
            self.assertEqual(self.start().status_code, 400)
        self.obs.start_record.assert_not_called()
        self.assertFalse(recorder.state['active'])

    def test_monitor_detects_disconnected_usb_without_claiming_obs_stopped(self):
        self.start()
        self.obs.get_record_status.return_value.output_active = True
        with patch.object(recorder.state['staging'].__class__, 'is_dir', return_value=False):
            recorder.monitor_recording()
        self.assertTrue(recorder.state['active'])
        self.assertIn('USB disconnected', recorder.state['warning'])

    def test_corrupt_recovery_pointer_does_not_unlock_new_recordings(self):
        recorder.RECOVERY_POINTER.write_text('{invalid', encoding='utf-8')
        self.simulate_restart()
        recorder.recover_session()
        self.assertTrue(recorder.state['recovery_blocked'])
        self.assertEqual(self.start().status_code, 409)
        self.assertTrue(recorder.RECOVERY_POINTER.exists())

    def test_monitor_unexpected_stop_without_file_requires_review(self):
        self.start()
        recorder.monitor_recording()
        self.assertFalse(recorder.state['active'])
        self.assertTrue(recorder.state['recovery_blocked'])
        self.assertEqual(self.start().status_code, 409)

    def test_logo_sequence_wraps_recording_start_and_stop(self):
        order = []
        self.obs.start_record.side_effect = lambda: order.append('record')
        with patch.object(recorder.obs_branding, 'prepare', return_value={'logo': 'Logo'}) as prepare, \
             patch.object(recorder.obs_branding, 'show_intro', side_effect=lambda *a: order.append('intro')), \
             patch.object(recorder.obs_branding, 'enter_live', side_effect=lambda *a: order.append('live')), \
             patch.object(recorder.obs_branding, 'show_outro', side_effect=lambda *a: order.append('outro')), \
             patch.object(recorder.obs_branding, 'restore', side_effect=lambda *a: order.append('restore')):
            self.assertEqual(self.start().status_code, 200)
            self.output()
            self.obs.stop_record.side_effect = lambda: (order.append('stop') or types.SimpleNamespace(output_path=str(recorder.state['staging'] / 'obs.mp4')))
            self.assertEqual(self.post('/api/stop').status_code, 200)
        self.assertEqual(order, ['intro', 'record', 'live', 'outro', 'stop', 'restore'])

    def test_outro_failure_still_stops_and_saves_recording(self):
        self.start()
        self.output()
        recorder.state['sequence'] = {'logo': 'Logo'}
        with patch.object(recorder.obs_branding, 'show_outro', side_effect=OSError()), \
             patch.object(recorder.obs_branding, 'restore'):
            response = self.post('/api/stop')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Closing logo could not be completed', response.json['message'])
        self.obs.stop_record.assert_called_once()
        self.assertEqual(len(list((self.root / 'Patients' / 'DUMMY').glob('*.mp4'))), 1)

    def test_logo_upload_preserves_supplied_file(self):
        from PIL import Image
        image = io.BytesIO()
        Image.new('RGB', (16, 16), 'white').save(image, format='PNG')
        supplied = image.getvalue()
        with patch.object(recorder, 'ROOT', self.root), patch.object(recorder, 'CFG', self.root / 'settings.json'):
            response = self.client.post('/api/branding/upload', headers=self.headers,
                data={'intro_image': (io.BytesIO(supplied), 'logo.png')})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Path(recorder.settings['intro_image']).read_bytes(), supplied)

    def test_test_patient_records_are_separate(self):
        details = {'patient_id': 'DUMMY', 'name': 'Test Example', 'test': True}
        self.assertEqual(self.post('/api/patients/save', details).status_code, 200)
        self.assertEqual(self.post('/api/patients/search').json['records'], [])
        self.assertEqual(len(self.post('/api/patients/search', {'test': True}).json['records']), 1)

    def test_patient_endpoints_require_token(self):
        for path in ['/api/patients/save', '/api/patients/search']:
            self.assertEqual(self.client.post(path, json={'patient_id': 'DUMMY'}).status_code, 403)

    def test_patient_interface_renders_history_with_valid_string_escapes(self):
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('Find a patient', html)
        self.assertIn('Save patient details', html)
        self.assertIn("entries)\\n\\n'", html)
        self.assertNotIn("entries)\n\n'", html)
        self.assertNotIn('(not stored)', html)

    def test_obs_not_running_is_reported(self):
        with patch.object(recorder, 'obs_process_running', return_value=False), \
             patch.object(recorder.socket, 'create_connection', side_effect=ConnectionRefusedError()):
            response = self.client.get('/api/obs-health', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json['obs_running'])
        self.assertFalse(response.json['websocket_connected'])
        self.assertIn('OBS is not running', response.json['message'])

    def test_obs_running_with_websocket_disabled(self):
        with patch.object(recorder, 'obs_process_running', return_value=True), \
             patch.object(recorder.socket, 'create_connection', side_effect=ConnectionRefusedError()):
            result = recorder.obs_health()
        self.assertTrue(result['obs_running'])
        self.assertFalse(result['port_open'])
        self.assertIn('enable the server', result['message'])

    def test_open_port_does_not_mean_obs_websocket_is_working(self):
        self.obs.get_version.side_effect = RuntimeError('secret password')
        with patch.object(recorder, 'obs_process_running', return_value=True), \
             patch.object(recorder.socket, 'create_connection'):
            result = recorder.obs_health()
        self.assertTrue(result['port_open'])
        self.assertFalse(result['websocket_connected'])
        self.assertIn('password', result['message'])
        self.assertNotIn('secret password', result['message'])
        self.obs.disconnect.assert_called_once()

    def test_obs_websocket_health_success(self):
        with patch.object(recorder, 'obs_process_running', return_value=True), \
             patch.object(recorder.socket, 'create_connection'):
            result = recorder.obs_health()
        self.assertTrue(result['websocket_connected'])
        self.obs.get_version.assert_called_once()
        self.obs.disconnect.assert_called_once()

    def test_remote_obs_does_not_inspect_local_process(self):
        recorder.settings['obs_host'] = '192.0.2.10'
        with patch.object(recorder, 'obs_process_running') as process, \
             patch.object(recorder.socket, 'create_connection'):
            result = recorder.obs_health()
        process.assert_not_called()
        self.assertIsNone(result['obs_running'])
        self.assertTrue(result['websocket_connected'])

    def test_recording_requires_responsive_obs_websocket(self):
        self.obs.get_version.side_effect = TimeoutError()
        with patch.object(recorder, 'obs_health', return_value={'message': 'Check OBS WebSocket password'}):
            response = self.start()
        self.assertEqual(response.status_code, 503)
        self.assertIn('WebSocket', response.json['error'])
        self.obs.start_record.assert_not_called()
        self.assertFalse(recorder.state['active'])

    def test_start_obs_launches_with_correct_working_directory(self):
        executable = self.root / 'obs64.exe'
        with patch.object(recorder, 'obs_process_running', return_value=False), \
             patch.object(recorder, 'find_obs_executable', return_value=executable), \
             patch.object(recorder.subprocess, 'Popen') as launch:
            launch.return_value.poll.return_value = None
            response = self.post('/api/obs-start')
            self.assertEqual(response.status_code, 202)
            self.assertEqual(launch.call_args.args[0], [str(executable), '--minimize-to-tray'])
            self.assertEqual(launch.call_args.kwargs['cwd'], str(executable.parent))
            startup = launch.call_args.kwargs['startupinfo']
            self.assertTrue(startup.dwFlags & recorder.subprocess.STARTF_USESHOWWINDOW)
            self.assertEqual(startup.wShowWindow, 7)
            self.assertEqual(self.post('/api/obs-start').status_code, 200)
            launch.assert_called_once()

    def test_start_obs_does_not_duplicate_running_obs(self):
        with patch.object(recorder, 'obs_process_running', return_value=True), \
             patch.object(recorder.subprocess, 'Popen') as launch:
            self.assertEqual(self.post('/api/obs-start').status_code, 200)
            launch.assert_not_called()

    def test_start_obs_missing_installation_returns_help(self):
        with patch.object(recorder, 'obs_process_running', return_value=False), \
             patch.object(recorder, 'find_obs_executable', return_value=None), \
             patch.object(recorder.subprocess, 'Popen') as launch:
            self.assertEqual(self.post('/api/obs-start').status_code, 404)
            launch.assert_not_called()

    def test_start_obs_rejects_remote_host_and_active_recording(self):
        with patch.object(recorder.subprocess, 'Popen') as launch:
            recorder.settings['obs_host'] = '192.0.2.1'
            self.assertEqual(self.post('/api/obs-start').status_code, 400)
            recorder.settings['obs_host'] = '127.0.0.1'
            recorder.state['active'] = True
            self.assertEqual(self.post('/api/obs-start').status_code, 409)
            launch.assert_not_called()

    def test_start_obs_requires_token(self):
        self.assertEqual(self.client.post('/api/obs-start', json={}).status_code, 403)

    def test_app_startup_launches_obs_minimized_without_http_request(self):
        with patch.object(recorder, 'obs_process_running', return_value=False), \
             patch.object(recorder, 'find_obs_executable', return_value=self.root / 'obs64.exe'), \
             patch.object(recorder.subprocess, 'Popen') as launch:
            recorder.startup_obs()
        launch.assert_called_once()
        self.assertIn('--minimize-to-tray', launch.call_args.args[0])
        self.assertEqual(launch.call_args.kwargs['startupinfo'].wShowWindow, 7)

    def test_app_startup_keeps_running_obs(self):
        with patch.object(recorder, 'obs_process_running', return_value=True), \
             patch.object(recorder.subprocess, 'Popen') as launch:
            recorder.startup_obs()
        launch.assert_not_called()

    def test_saved_recordings_show_scan_name_and_id_and_search_by_name(self):
        self.start(name='Example Family')
        self.output()
        self.post('/api/stop')
        self.update_patient({'patient_id': 'DUMMY', 'name': 'Later Name'})
        recordings = self.post('/api/files', {'q': 'example family'}).json['files']
        self.assertEqual(len(recordings), 1)
        self.assertEqual(recordings[0]['patient_name'], 'Example Family')
        self.assertEqual(recordings[0]['patient'], 'DUMMY')
        self.assertNotIn('notes', recordings[0])

    def test_saved_recording_without_details_still_shows_id(self):
        self.start()
        path = recorder.state['destination'] / 'legacy.mp4'
        path.write_bytes(b'legacy')
        recordings = self.post('/api/files').json['files']
        self.assertEqual(recordings[0]['patient'], 'DUMMY')
        self.assertEqual(recordings[0]['patient_name'], '')

    def test_app_startup_reports_missing_obs_without_crashing(self):
        with patch.object(recorder, 'obs_process_running', return_value=False), \
             patch.object(recorder, 'find_obs_executable', return_value=None):
            recorder.startup_obs()
        self.assertIn('installation was not found', recorder.state['message'])

    def test_app_startup_does_not_launch_for_remote_or_unknown_process(self):
        with patch.object(recorder.subprocess, 'Popen') as launch:
            recorder.settings['obs_host'] = '192.0.2.1'
            recorder.startup_obs()
            recorder.settings['obs_host'] = '127.0.0.1'
            with patch.object(recorder, 'obs_process_running', return_value=None):
                recorder.startup_obs()
            launch.assert_not_called()

    def test_start_obs_launch_failure_does_not_leak_details(self):
        with patch.object(recorder, 'obs_process_running', return_value=False), \
             patch.object(recorder, 'find_obs_executable', return_value=self.root / 'obs64.exe'), \
             patch.object(recorder.subprocess, 'Popen', side_effect=OSError('private path')):
            response = self.post('/api/obs-start')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b'private path', response.data)

    def test_recording_details_use_scan_snapshot(self):
        self.start(name='Original Example', notes='Original scan notes')
        self.output()
        self.post('/api/stop')
        self.update_patient({'patient_id': 'DUMMY', 'name': 'Changed Example'})
        key = self.post('/api/files').json['files'][0]['key']
        response = self.post('/api/recording-details', {'key': key})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['details']['name'], 'Original Example')
        self.assertEqual(response.json['details']['notes'], 'Original scan notes')
        self.assertGreater(response.json['size_bytes'], 0)

    def test_legacy_recording_does_not_get_unrelated_patient_details(self):
        self.start(name='Example')
        old = recorder.state['destination'] / 'legacy.mp4'
        old.write_bytes(b'legacy recording')
        key = self.post('/api/files').json['files'][0]['key']
        self.assertIsNone(self.post('/api/recording-details', {'key': key}).json['details'])

    def test_recording_details_reject_paths_and_missing_files(self):
        self.assertEqual(self.post('/api/recording-details', {'key': '../secret'}).status_code, 404)

    def test_share_package_contains_video_and_only_selected_scan_details(self):
        self.start(name='Original Example', notes='Private scan notes', dob='25/12/1990')
        source = self.output()
        original = source.read_bytes()
        self.post('/api/stop')
        self.update_patient({'patient_id': 'DUMMY', 'name': 'Changed Example'})
        key = self.post('/api/files').json['files'][0]['key']
        response = self.client.get('/export?id='+key+'&fields=name,scan', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            self.assertEqual(set(archive.namelist()), {'recording.mp4', 'patient-information.txt'})
            self.assertEqual(archive.read('recording.mp4'), original)
            details = archive.read('patient-information.txt').decode('utf-8')
            self.assertIn('Original Example', details)
            for secret in ['Changed Example', 'Private scan notes', '25/12/1990', 'DUMMY']:
                self.assertNotIn(secret, details)
        response.close()
        self.assertEqual(list((self.appdata / 'exports-temp').iterdir()), [])
        self.assertEqual(len(list((self.root / 'Patients' / 'DUMMY').glob('*.mp4'))), 1)

    def test_export_requires_authentication_and_rejects_arbitrary_fields(self):
        self.assertEqual(self.client.get('/export?id=anything').status_code, 403)
        self.start()
        self.output()
        self.post('/api/stop')
        key = self.post('/api/files').json['files'][0]['key']
        response = self.client.get('/export?id='+key+'&fields=unknown', headers=self.headers)
        self.assertEqual(response.status_code, 400)

    def test_email_draft_has_video_and_selected_details_without_recipient(self):
        self.start(name='Example Patient', notes='Do not include these notes')
        path = self.output()
        # Exercise base64 wrapping across more than one streaming chunk.
        original = bytes(range(256)) * 600
        path.write_bytes(original)
        self.post('/api/stop')
        key = self.post('/api/files').json['files'][0]['key']
        response = self.client.get('/export?id='+key+'&fields=name&format=email', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'message/rfc822')
        draft = BytesParser(policy=policy.default).parsebytes(response.data)
        self.assertEqual(draft['X-Unsent'], '1')
        self.assertIsNone(draft['To'])
        body = draft.get_body(preferencelist=('plain',)).get_content()
        self.assertIn('Example Patient', body)
        self.assertNotIn('Do not include these notes', body)
        self.assertEqual(list(draft.iter_attachments())[0].get_payload(decode=True), original)
        response.close()
        self.assertEqual(list((self.appdata / 'exports-temp').iterdir()), [])

    def test_export_low_disk_space_keeps_original(self):
        self.start()
        self.output()
        self.post('/api/stop')
        key = self.post('/api/files').json['files'][0]['key']
        with patch.object(recorder.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=0)):
            response = self.client.get('/export?id='+key, headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(list((self.root / 'Patients' / 'DUMMY').glob('*.mp4'))), 1)

    def test_share_controls_render_without_inline_key_handlers(self):
        html = self.client.get('/').get_data(as_text=True)
        self.assertTrue('Review email' in html)
        self.assertTrue('data-album-select' in html)
        self.assertFalse('Download email draft' in html)
        self.assertFalse('onclick="reviewShare(' in html)

    def prepare_family_copy(self):
        self.start(name='Example Patient', notes='Private notes')
        self.output()
        self.post('/api/stop')
        key = self.post('/api/files').json['files'][0]['key']
        family = self.root / 'family-drive'
        family.mkdir()
        drive = {'root': family, 'serial': 123, 'label': 'FAMILY'}
        patcher = patch.object(recorder, 'family_usb_drives', return_value=[drive])
        patcher.start()
        self.addCleanup(patcher.stop)
        destination = self.post('/api/family-usb/drives').json['drives'][0]['key']
        return key, destination, family

    def test_family_usb_video_only_preserves_existing_files_and_original(self):
        key, destination, family = self.prepare_family_copy()
        existing = family / 'family-photo.jpg'
        existing.write_bytes(b'existing')
        response = self.post('/api/family-usb/copy', {'key': key, 'destination': destination})
        self.assertEqual(response.status_code, 200)
        folder = family / response.json['folder']
        self.assertEqual([p.name for p in folder.iterdir()], ['recording.mp4'])
        self.assertEqual((folder / 'recording.mp4').read_bytes(), b'simulated recording')
        self.assertEqual(existing.read_bytes(), b'existing')
        self.assertEqual(len(list((self.root / 'Patients' / 'DUMMY').glob('*.mp4'))), 1)
        response2 = self.post('/api/family-usb/copy', {'key': key, 'destination': destination})
        self.assertEqual(response2.status_code, 200)
        self.assertNotEqual(response.json['folder'], response2.json['folder'])

    def test_family_usb_only_includes_selected_patient_fields(self):
        key, destination, family = self.prepare_family_copy()
        response = self.post('/api/family-usb/copy', {'key': key, 'destination': destination, 'fields': ['name']})
        self.assertEqual(response.status_code, 200)
        text = (family / response.json['folder'] / 'patient-information.txt').read_text(encoding='utf-8')
        self.assertIn('Example Patient', text)
        self.assertNotIn('Private notes', text)
        self.assertNotIn('DUMMY', text)

    def test_family_usb_replacement_is_rejected(self):
        key, destination, family = self.prepare_family_copy()
        with patch.object(recorder, 'family_usb_drives', return_value=[{'root': family, 'serial': 999}]):
            response = self.post('/api/family-usb/copy', {'key': key, 'destination': destination})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(list(family.iterdir()), [])

    def test_family_usb_low_space_does_not_create_partial_copy(self):
        key, destination, family = self.prepare_family_copy()
        with patch.object(recorder.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=0)):
            response = self.post('/api/family-usb/copy', {'key': key, 'destination': destination})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(family.iterdir()), [])

    def test_new_recording_is_blocked_during_family_copy(self):
        with recorder.family_copy_lock:
            self.assertEqual(self.start().status_code, 409)

    def test_interrupted_family_copy_is_marked_incomplete(self):
        key, destination, family = self.prepare_family_copy()
        with patch.object(recorder.os, 'fsync', side_effect=OSError()):
            response = self.post('/api/family-usb/copy', {'key': key, 'destination': destination})
        self.assertEqual(response.status_code, 503)
        self.assertIn('.incomplete', response.json['error'])
        self.assertTrue(all(p.name.endswith('.incomplete') for p in family.iterdir()))
        self.assertFalse(recorder.family_copy_lock.locked())
        self.assertEqual(len(list((self.root / 'Patients' / 'DUMMY').glob('*.mp4'))), 1)

    def test_family_drive_list_excludes_recording_drive_and_internal_fixed_drives(self):
        kernel = Mock()
        kernel.GetLogicalDrives.return_value = (1 << 3) | (1 << 4) | (1 << 5)
        kernel.GetDriveTypeW.side_effect = lambda root: 3 if root.startswith('F:') else 2
        kernel.GetVolumeInformationW.return_value = 1
        with patch.object(recorder.ctypes.windll, 'kernel32', kernel), patch.object(recorder.usb_devices,'is_usb_volume',return_value=False):
            drives = recorder.family_usb_drives(Path('D:/Ultrasound Recordings'))
        self.assertEqual([drive['root'] for drive in drives], [Path('E:/')])

    def test_external_usb_hdd_is_listed_with_thumb_drives_but_not_internal_disks(self):
        kernel=Mock()
        kernel.GetLogicalDrives.return_value=sum(1<<index for index in (2,3,4,5))
        kernel.GetDriveTypeW.side_effect=lambda root: 2 if root.startswith('E:') else 3
        kernel.GetVolumeInformationW.return_value=1
        with patch.object(recorder.ctypes.windll,'kernel32',kernel), patch.object(recorder.usb_devices,'is_usb_volume',side_effect=lambda root:str(root).startswith('F:')):
            drives=recorder.family_usb_drives()
            self.assertEqual([drive['root'] for drive in drives],[Path('E:/'),Path('F:/')])
            family=recorder.family_usb_drives(Path('F:/Ultrasound Recordings'))
            self.assertEqual([drive['root'] for drive in family],[Path('E:/')])

    def saved_patient_for_deletion(self, test=False):
        self.update_patient({'patient_id': 'DUMMY', 'name': 'Example', 'test': test})
        record = self.post('/api/patients/search', {'patient_id': 'DUMMY', 'test': test}).json['records'][0]
        self.post('/api/patients/archive', {'patient_id': 'DUMMY', 'record_id': record['id'], 'test': test, 'confirm': True, 'archived': True})
        return {'patient_id': 'DUMMY', 'record_id': record['id'], 'test': test, 'confirm': True, 'confirm_patient_id': 'DUMMY'}

    def test_delete_removes_all_patient_versions_but_preserves_recordings(self):
        self.start(name='Example')
        self.output()
        self.post('/api/stop')
        self.post('/api/patients/save', {'patient_id': 'OTHER', 'name': 'Other Example'})
        selection = self.saved_patient_for_deletion()
        self.assertEqual(self.post('/api/patients/delete', selection).status_code, 200)
        self.assertEqual(self.post('/api/patients/search', {'patient_id': 'DUMMY'}).json['records'], [])
        self.assertEqual(len(self.post('/api/patients/search', {'patient_id': 'OTHER'}).json['records']), 1)
        self.assertEqual(len(list((self.root / 'Patients' / 'DUMMY').glob('*.mp4'))), 1)
        self.assertEqual(len(self.post('/api/files').json['files']), 1)
        recording = next((self.root / 'Patients' / 'DUMMY').glob('*.mp4'))
        self.assertIsNone(recorder.patient_store.for_recording(self.data_root, str(recording.relative_to(self.root)), 'TESTVOL'))

    def test_delete_requires_confirmation_and_authentication(self):
        selection = self.saved_patient_for_deletion()
        self.assertEqual(self.client.post('/api/patients/delete', json=selection).status_code, 403)
        self.assertEqual(self.post('/api/patients/delete', {**selection, 'confirm': False}).status_code, 400)
        self.assertEqual(len(self.post('/api/patients/search', {'patient_id': 'DUMMY'}).json['records']), 1)

    def test_delete_rejects_stale_selection(self):
        selection = self.saved_patient_for_deletion()
        self.post('/api/patients/archive', {**selection, 'archived': False})
        self.update_patient({'patient_id': 'DUMMY', 'name': 'Updated Example'})
        self.assertEqual(self.post('/api/patients/delete', selection).status_code, 409)
        self.assertEqual(len(self.post('/api/patients/search', {'patient_id': 'DUMMY'}).json['records']), 2)

    def test_delete_is_blocked_during_recording_or_pending_save(self):
        selection = self.saved_patient_for_deletion()
        recorder.state['active'] = True
        self.assertEqual(self.post('/api/patients/delete', selection).status_code, 409)
        recorder.state.update(active=False, pending=Path('retained.mp4'))
        self.assertEqual(self.post('/api/patients/delete', selection).status_code, 409)

    def test_delete_test_patient_does_not_delete_real_patient(self):
        self.saved_patient_for_deletion()
        selection = self.saved_patient_for_deletion(test=True)
        self.assertEqual(self.post('/api/patients/delete', selection).status_code, 200)
        self.assertEqual(self.post('/api/patients/search', {'test': True}).json['records'], [])
        self.assertEqual(len(self.post('/api/patients/search', {'include_archived': True}).json['records']), 1)

    def test_duplicate_id_requires_confirmation_before_appending_details(self):
        first = self.post('/api/patients/save', {'patient_id': 'DUMMY', 'name': 'Original Example'})
        response = self.post('/api/patients/save', {'patient_id': 'dummy', 'name': 'Different Example'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json['code'], 'duplicate_patient_id')
        self.assertEqual(response.json['existing']['name'], 'Original Example')
        records = self.post('/api/patients/search', {'patient_id': 'DUMMY'}).json['records']
        self.assertEqual(len(records), 1)
        confirmed = self.post('/api/patients/save', {'patient_id': 'dummy', 'name': 'Updated Example',
                              'expected_record_id': first.json['record_id']})
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(len(self.post('/api/patients/search', {'patient_id': 'DUMMY'}).json['records']), 2)

    def test_duplicate_id_blocks_recording_until_existing_patient_is_confirmed(self):
        self.post('/api/patients/save', {'patient_id': 'DUMMY', 'name': 'Original Example'})
        response = self.post('/api/start', {'patient_id': 'DUMMY', 'name': 'Different Example'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json['code'], 'duplicate_patient_id')
        self.obs.start_record.assert_not_called()

    def test_archive_keeps_recording_names_and_can_be_restored(self):
        self.start(name='Example')
        self.output()
        self.post('/api/stop')
        record = self.post('/api/patients/search', {'patient_id': 'DUMMY'}).json['records'][0]
        selection = {'patient_id': 'DUMMY', 'record_id': record['id'], 'confirm': True, 'archived': True}
        self.assertEqual(self.post('/api/patients/archive', selection).status_code, 200)
        self.assertEqual(self.post('/api/patients/search').json['records'], [])
        self.assertTrue(self.post('/api/patients/search', {'include_archived': True}).json['records'][0]['archived'])
        self.assertEqual(self.post('/api/files').json['files'][0]['patient_name'], 'Example')
        self.assertEqual(self.post('/api/start', {'patient_id': 'DUMMY'}).status_code, 409)
        self.assertEqual(self.post('/api/patients/save', {'patient_id': 'DUMMY'}).status_code, 409)
        self.assertEqual(self.post('/api/patients/archive', {**selection, 'archived': False}).status_code, 200)
        self.assertEqual(len(self.post('/api/patients/search').json['records']), 1)

    def test_permanent_deletion_requires_archive_and_typed_id(self):
        saved = self.post('/api/patients/save', {'patient_id': 'DUMMY'})
        selection = {'patient_id': 'DUMMY', 'record_id': saved.json['record_id'], 'confirm': True}
        self.assertEqual(self.post('/api/patients/delete', selection).status_code, 400)
        self.assertEqual(self.post('/api/patients/delete', {**selection, 'confirm_patient_id': 'DUMMY'}).status_code, 409)
        self.assertEqual(len(self.post('/api/patients/search').json['records']), 1)

    def test_old_database_gains_archive_support_without_changing_details(self):
        import sqlite3
        database = self.root / 'patient-records.sqlite3'
        with sqlite3.connect(database) as connection:
            connection.execute('CREATE TABLE patient_records (id TEXT PRIMARY KEY, patient_id TEXT NOT NULL COLLATE NOCASE, details TEXT NOT NULL, created_at TEXT NOT NULL, recording TEXT)')
            connection.execute('INSERT INTO patient_records VALUES (?, ?, ?, ?, ?)',
                               ('legacy', 'OLD', '{"name":"Legacy Example","dob":"01/01/2030","scan":"Other","notes":"Keep this"}', '2026-01-01', None))
        connection.close()
        records = self.post('/api/patients/search').json['records']
        self.assertEqual(records[0]['name'], 'Legacy Example')
        self.assertFalse(records[0]['archived'])
        self.assertEqual(self.post('/api/patients/archive', {'patient_id': 'OLD', 'record_id': 'legacy',
                         'confirm': True, 'archived': True}).status_code, 200)
        self.assertEqual(self.post('/api/patients/search', {'patient_id': 'OLD'}).json['records'][0]['notes'], 'Keep this')

    def test_generate_patient_ids_are_unique_and_do_not_create_blank_patients(self):
        first = self.post('/api/patients/generate-id')
        second = self.post('/api/patients/generate-id')
        self.assertEqual(first.status_code, 200)
        self.assertRegex(first.json['patient_id'], r'^MOL-[A-F0-9]{8}$')
        self.assertNotEqual(first.json['patient_id'], second.json['patient_id'])
        self.assertEqual(self.post('/api/patients/search').json['records'], [])
        self.assertEqual(self.post('/api/patients/save', {'patient_id': first.json['patient_id'], 'name': 'New Example'}).status_code, 200)

    def test_generate_patient_id_skips_archived_reserved_and_existing_folder_ids(self):
        saved = self.post('/api/patients/save', {'patient_id': 'MOL-AAAAAAAA'})
        self.post('/api/patients/archive', {'patient_id': 'MOL-AAAAAAAA', 'record_id': saved.json['record_id'], 'confirm': True, 'archived': True})
        (self.root / 'Patients' / 'MOL-BBBBBBBB').mkdir(parents=True)
        with patch.object(recorder.patient_store.secrets, 'token_hex', side_effect=['aaaaaaaa','bbbbbbbb','cccccccc','cccccccc','dddddddd']):
            first = self.post('/api/patients/generate-id')
            second = self.post('/api/patients/generate-id')
        self.assertEqual(first.json['patient_id'], 'MOL-CCCCCCCC')
        self.assertEqual(second.json['patient_id'], 'MOL-DDDDDDDD')

    def test_generate_patient_id_requires_authorization_and_works_without_usb(self):
        self.assertEqual(self.client.post('/api/patients/generate-id', json={}).status_code, 403)
        self.assertEqual(self.post('/api/patients/generate-id', {'test': 'yes'}).status_code, 400)
        with patch.object(recorder, 'base_dir', side_effect=OSError('private path')):
            response = self.post('/api/patients/generate-id')
        self.assertEqual(response.status_code, 200)
        self.assertRegex(response.json['patient_id'], r'^MOL-[A-F0-9]{8}$')

    def test_generate_patient_id_is_blocked_while_recording(self):
        self.start()
        self.assertEqual(self.post('/api/patients/generate-id').status_code, 409)

    def test_spotify_not_playing_warns_but_does_not_block_recording(self):
        with patch.object(recorder.spotify_status, 'check', return_value={'playing': False, 'available': True, 'message': 'Spotify is paused.'}), patch.object(recorder.spotify_status, 'control', side_effect=recorder.spotify_status.SpotifyControlError('Open Spotify.')) as control:
            self.assertEqual(self.start().status_code, 200)
        self.assertEqual([call.args[0] for call in control.call_args_list],['restart'])
        self.assertTrue(recorder.state['active'])
        self.assertIn('Spotify is paused', recorder.state['warning'])
        self.obs.start_record.assert_called_once()

    def test_spotify_pausing_during_recording_warns_without_stopping_obs(self):
        self.start()
        self.obs.get_record_status.return_value.output_active = True
        with patch.object(recorder.spotify_status, 'check', return_value={'playing': False, 'available': True, 'message': 'Spotify is paused.'}):
            recorder.monitor_recording()
        self.assertTrue(recorder.state['active'])
        self.assertIn('Spotify is paused', recorder.state['warning'])
        self.obs.stop_record.assert_not_called()
        recorder.monitor_recording()
        self.assertEqual(recorder.state['warning'], '')

    def test_music_status_endpoint_is_authenticated_and_handles_unknown_status(self):
        self.assertEqual(self.client.get('/api/music-health').status_code, 403)
        with patch.object(recorder.spotify_status, 'check', return_value={'playing': None, 'available': False, 'message': 'Could not check Spotify.'}):
            response = self.client.get('/api/music-health', headers=self.headers)
            self.assertIsNone(response.json['playing'])
            self.assertEqual(self.start().status_code, 200)
        self.assertIn('Could not check Spotify', recorder.state['warning'])

    def recorded_video_for_deletion(self):
        self.start(name='Example Patient')
        self.output()
        self.post('/api/stop')
        item = self.post('/api/files').json['files'][0]
        return item, next((self.root / 'Patients' / 'DUMMY').glob('*.mp4'))

    def test_delete_recording_preserves_patient_history_and_other_videos(self):
        item, path = self.recorded_video_for_deletion()
        other = path.parent / 'another.mp4'
        other.write_bytes(b'another recording')
        response = self.post('/api/recordings/delete', {'key': item['key'], 'confirm': True})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(path.exists())
        self.assertEqual(other.read_bytes(), b'another recording')
        history = self.post('/api/patients/search', {'patient_id': 'DUMMY'}).json['records']
        self.assertEqual(history[0]['name'], 'Example Patient')
        self.assertIsNone(history[0]['recording'])
        self.assertEqual(self.client.get('/video?id='+item['key'], headers=self.headers).status_code, 404)

    def test_delete_recording_requires_authentication_and_confirmation(self):
        item, path = self.recorded_video_for_deletion()
        self.assertEqual(self.client.post('/api/recordings/delete', json={'key': item['key'], 'confirm': True}).status_code, 403)
        self.assertEqual(self.post('/api/recordings/delete', {'key': item['key']}).status_code, 400)
        self.assertTrue(path.is_file())

    def test_delete_recording_rejects_modified_video(self):
        item, path = self.recorded_video_for_deletion()
        path.write_bytes(b'changed video after list was loaded')
        self.assertEqual(self.post('/api/recordings/delete', {'key': item['key'], 'confirm': True}).status_code, 409)
        self.assertTrue(path.exists())

    def test_delete_recording_failure_preserves_video_database_link(self):
        item, path = self.recorded_video_for_deletion()
        with patch.object(Path, 'unlink', side_effect=PermissionError('private patient path')):
            response = self.post('/api/recordings/delete', {'key': item['key'], 'confirm': True})
        self.assertEqual(response.status_code, 503)
        self.assertTrue(path.is_file())
        self.assertNotIn('private patient path', response.json['error'])
        self.assertIsNotNone(recorder.patient_store.for_recording(self.data_root, str(path.relative_to(self.root)), 'TESTVOL'))

    def test_delete_recording_is_blocked_during_recording_or_copy(self):
        item, path = self.recorded_video_for_deletion()
        recorder.state['active'] = True
        self.assertEqual(self.post('/api/recordings/delete', {'key': item['key'], 'confirm': True}).status_code, 409)
        recorder.state['active'] = False
        with recorder.family_copy_lock:
            self.assertEqual(self.post('/api/recordings/delete', {'key': item['key'], 'confirm': True}).status_code, 409)
        self.assertTrue(path.is_file())

    def test_delete_recording_rejects_arbitrary_paths(self):
        self.assertEqual(self.post('/api/recordings/delete', {'key': '../private.mp4', 'confirm': True}).status_code, 404)

    def test_patient_management_is_available_with_usb_disconnected(self):
        with patch.object(recorder, 'base_dir', side_effect=RuntimeError('No recording USB')):
            saved = self.post('/api/patients/save', {'patient_id': 'OFFLINE', 'name': 'Offline Example'})
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(self.post('/api/patients/search').json['records'][0]['name'], 'Offline Example')
            self.assertEqual(self.post('/api/patients/archive', {'patient_id': 'OFFLINE',
                'record_id': saved.json['record_id'], 'confirm': True, 'archived': True}).status_code, 200)
        self.assertTrue((self.data_root / 'patient-records.sqlite3').is_file())
        self.assertFalse((self.root / 'patient-records.sqlite3').exists())

    def test_recovery_rejects_a_different_usb_volume(self):
        self.start()
        self.output()
        self.simulate_restart()
        with patch.object(recorder, 'volume_id', return_value='DIFFERENT'):
            recorder.recover_session()
        self.assertTrue(recorder.state['recovery_blocked'])
        self.assertEqual(self.start().status_code, 409)

    def test_video_details_stay_linked_when_same_volume_uses_a_different_root(self):
        self.start(name='Example Family')
        self.output()
        self.post('/api/stop')
        source = next((self.root / 'Patients' / 'DUMMY').glob('*.mp4'))
        relative = source.relative_to(self.root)
        remounted = self.root / 'another-drive-letter'
        new_path = remounted / relative
        new_path.parent.mkdir(parents=True)
        new_path.write_bytes(source.read_bytes())
        with patch.object(recorder, 'base_dir', return_value=remounted):
            item = self.post('/api/files').json['files'][0]
            self.assertEqual(item['patient_name'], 'Example Family')
            self.assertEqual(self.post('/api/recording-details', {'key': item['key']}).json['details']['name'], 'Example Family')
        with patch.object(recorder, 'base_dir', return_value=remounted), patch.object(recorder, 'volume_id', return_value='UNRELATED-USB'):
            item = self.post('/api/files').json['files'][0]
            self.assertEqual(item['patient_name'], '')
            self.assertIsNone(self.post('/api/recording-details', {'key': item['key']}).json['details'])

    def test_intro_video_upload_is_preserved_in_appdata(self):
        video = b'\x00\x00\x00\x18ftypmp42' + b'video fixture'
        response = self.client.post('/api/branding/upload', headers=self.headers,
                                   data={'intro_image': (io.BytesIO(video), 'intro.mp4')})
        self.assertEqual(response.status_code, 200)
        saved = Path(recorder.settings['intro_image'])
        self.assertEqual(saved.suffix, '.mp4')
        self.assertTrue(saved.is_relative_to(self.appdata))
        self.assertEqual(saved.read_bytes(), video)

    def test_video_is_not_accepted_for_the_corner_logo(self):
        response = self.client.post('/api/branding/upload', headers=self.headers,
            data={'watermark_image': (io.BytesIO(b'\x00\x00\x00\x18ftypmp42video'), 'overlay.mp4')})
        self.assertEqual(response.status_code, 400)

    def test_invalid_intro_video_keeps_existing_branding(self):
        previous = recorder.settings['intro_image']
        response = self.client.post('/api/branding/upload', headers=self.headers,
            data={'intro_image': (io.BytesIO(b'not a video'), 'intro.mp4')})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(recorder.settings['intro_image'], previous)
        self.assertEqual(list((self.appdata / 'branding').glob('*.tmp')), [])

    def test_normal_recording_does_not_start_without_blackmagic_source(self):
        self.obs.get_input_list.return_value.inputs = [{'inputName': 'Other camera', 'inputKind': 'dshow_input'}]
        with patch.object(recorder.obs_branding, 'prepare_capture', self.real_prepare_capture):
            response = self.start()
        self.assertEqual(response.status_code, 400)
        self.assertIn('blackmagic', response.json['error'])
        self.obs.start_record.assert_not_called()

    def test_audio_plan_is_applied_before_obs_starts_and_route_is_visible(self):
        sequence = {'capture_only': True, 'audio': {'source': 'Spotify', 'route': 'Spotify direct', 'warning': ''}}
        order = []
        self.obs.start_record.side_effect = lambda: order.append('record')
        with patch.object(recorder.obs_branding, 'prepare_capture', return_value=sequence), \
             patch.object(recorder.obs_audio, 'apply', side_effect=lambda *a, **kw: order.append('audio')), \
             patch.object(recorder.obs_branding, 'show_intro'), patch.object(recorder.obs_branding, 'enter_live'):
            self.assertEqual(self.start().status_code, 200)
        self.assertEqual(order, ['audio', 'record'])
        self.assertEqual(self.client.get('/api/status', headers=self.headers).json['audio_route'], 'Spotify direct')

    def test_invalid_audio_capture_setting_does_not_replace_settings(self):
        previous = recorder.settings['music_capture']
        self.assertEqual(self.post('/api/settings', {'music_capture': 'invalid'}).status_code, 400)
        self.assertEqual(recorder.settings['music_capture'], previous)

    def test_obs_source_options_are_authenticated_and_exclude_managed_sources(self):
        self.assertEqual(self.client.get('/api/obs-sources').status_code, 403)
        self.obs.get_input_list.return_value.inputs = [
            {'inputName':'Camera','inputKind':'dshow_input'},
            {'inputName':'Spotify','inputKind':'wasapi_process_output_capture'},
            {'inputName':'MOL Recorder test Logo','inputKind':'image_source'}]
        self.obs.get_scene_list.return_value.scenes = [{'sceneName':'Original'},{'sceneName':'MOL Recorder test Live'}]
        self.obs.get_input_properties_list_property_items.return_value.property_items = [
            {'itemName':'USB3.0 capture','itemValue':'device-1','itemEnabled':True}]
        response = self.client.get('/api/obs-sources', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {'video':['Camera'],'audio':['Spotify'],'scenes':['Original'],
                                       'devices':[{'name':'USB3.0 capture','id':'device-1'}]})

    def test_physical_capture_device_settings_are_saved_separately_from_source_name(self):
        response = self.post('/api/settings', {'video_source':'@device','video_device_id':'device-1','video_device_name':'USB3.0 capture'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(recorder.settings['video_device_id'], 'device-1')
        self.assertEqual(recorder.settings['video_device_name'], 'USB3.0 capture')
        self.assertEqual(self.post('/api/settings', {'video_source':'@device','video_device_id':''}).status_code, 400)

    def test_fixed_outro_upload_enables_image_mode_and_preserves_original_bytes(self):
        from PIL import Image
        image = io.BytesIO()
        Image.new('RGB',(16,16),'white').save(image,format='PNG')
        original = image.getvalue()
        response = self.client.post('/api/branding/upload', headers=self.headers,
            data={'outro_image':(io.BytesIO(original),'outro.png')})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(recorder.settings['outro_mode'], 'image')
        self.assertEqual(Path(recorder.settings['outro_image']).read_bytes(), original)

    def test_outro_upload_rejects_video_and_does_not_change_saved_mode(self):
        response = self.client.post('/api/branding/upload', headers=self.headers,
            data={'outro_image':(io.BytesIO(b'\x00\x00\x00\x18ftypmp42video'),'outro.mp4')})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(recorder.settings['outro_mode'], 'same')

    def test_spotify_controls_require_token_and_validate_action(self):
        self.assertEqual(self.client.post('/api/spotify/control',json={'action':'play'}).status_code,403)
        self.assertEqual(self.post('/api/spotify/control',{'action':'skip'}).status_code,400)
        with patch.object(recorder.spotify_status,'control',return_value={'message':'Spotify pause requested.'}) as control:
            self.assertEqual(self.post('/api/spotify/control',{'action':'pause'}).status_code,200)
            control.assert_called_once_with('pause')
        for action in ('previous','next'):
            with patch.object(recorder.spotify_status,'control',return_value={'message':'Requested'}) as control:
                self.assertEqual(self.post('/api/spotify/control',{'action':action}).status_code,200)
                control.assert_called_once_with(action)

    def test_recording_usb_selection_is_saved_and_follows_volume_identity(self):
        drive = {'root':self.root,'serial':123,'label':'Family USB'}
        with patch.object(recorder, 'family_usb_drives', return_value=[drive]):
            self.assertEqual(self.client.get('/api/recording-drives').status_code,403)
            response=self.client.get('/api/recording-drives',headers=self.headers)
            self.assertEqual(response.json['drives'][0]['id'],'0000007B')
            self.assertEqual(self.post('/api/settings',{'recording_volume':'0000007B'}).status_code,200)
            self.assertEqual(recorder.usb_root(),self.root)
            drive['root']=self.root/'new-letter'
            self.assertEqual(recorder.usb_root(),drive['root'])
        with patch.object(recorder, 'family_usb_drives', return_value=[]):
            with self.assertRaises(RuntimeError):recorder.usb_root()

    def test_recording_usb_rejects_disconnected_or_ambiguous_selection(self):
        drive={'root':self.root,'serial':123,'label':'USB'}
        for drives in ([],[drive,drive]):
            with patch.object(recorder, 'family_usb_drives', return_value=drives):
                self.assertEqual(self.post('/api/settings',{'recording_volume':'0000007B'}).status_code,400)
        self.assertEqual(recorder.settings['recording_volume'],'')

    def test_selected_sources_are_saved_without_losing_other_settings(self):
        previous = recorder.settings['volume_label']
        response = self.post('/api/settings', {'video_source':'Camera B','audio_source':'Spotify audio'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(recorder.settings['video_source'], 'Camera B')
        self.assertEqual(recorder.settings['audio_source'], 'Spotify audio')
        self.assertEqual(recorder.settings['volume_label'], previous)


if __name__ == '__main__':
    unittest.main()
