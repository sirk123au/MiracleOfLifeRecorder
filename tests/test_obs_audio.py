import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import obs_audio
import inspect
from obsws_python import ReqClient


class AudioCapture(unittest.TestCase):
    def test_direct_capture_uses_required_obs_sdk_kind_lookup_argument(self):
        client = self.client(spotify=False)
        signature = inspect.signature(ReqClient.get_input_kind_list)
        def kinds(*args, **kwargs):
            signature.bind(None, *args, **kwargs)
            return SimpleNamespace(input_kinds=['wasapi_process_output_capture'])
        client.get_input_kind_list.side_effect = kinds
        client.get_input_properties_list_property_items.return_value.property_items = [
            {'itemValue':'Spotify:Window:Spotify.exe'}]
        result = obs_audio.plan(client, {'branding_key':'test'}, 'Capture')
        self.assertEqual(result['route'], 'Spotify direct')
        client.get_input_kind_list.assert_called_once_with(unversioned=True)

    def test_spotify_application_capture_refreshes_window_and_restores_original_settings(self):
        client = self.client()
        client.get_input_properties_list_property_items.return_value.property_items = [
            {'itemValue':'New song:Chrome_WidgetWin_0:Spotify.exe','itemEnabled':True}]
        plan = obs_audio.plan(client, {'branding_key':'test'}, 'Capture')
        obs_audio.apply(client, plan)
        client.set_input_settings.assert_called_with('Spotify audio',
            {'window':'New song:Chrome_WidgetWin_0:Spotify.exe','priority':2}, True)
        client.set_input_audio_monitor_type.assert_called_with('Spotify audio','OBS_MONITORING_TYPE_NONE')
        obs_audio.restore(client, plan)
        client.set_input_audio_monitor_type.assert_called_with('Spotify audio','OBS_MONITORING_TYPE_MONITOR_ONLY')
        client.set_input_settings.assert_called_with('Spotify audio',
            {'window':'Some title:Chrome_WidgetWin_0:Spotify.exe'}, False)

    def test_existing_managed_system_capture_is_reused_after_scene_rebuild(self):
        client = self.client(spotify=False)
        name = 'MOL Recorder test System audio'
        client.send.return_value = {}
        client.get_input_list.return_value.inputs.append({'inputName':name,'inputKind':'wasapi_output_capture'})
        plan = obs_audio.plan(client, {'branding_key':'test','music_capture':'system'}, 'Capture')
        self.assertEqual(plan['source'], name)
        client.create_input.assert_not_called()
        client.create_scene_item.assert_called_once_with('Capture', name, True)

    def test_existing_spotify_probe_can_be_reconfigured_without_duplicate_input(self):
        client = self.client(spotify=False)
        name = 'MOL Recorder test Spotify audio'
        client.get_input_list.return_value.inputs.append({'inputName':name,'inputKind':'wasapi_process_output_capture'})
        client.get_input_properties_list_property_items.return_value.property_items = [
            {'itemValue':'Music:Window:Spotify.exe'}]
        plan = obs_audio.plan(client, {'branding_key':'test','music_capture':'spotify'}, 'Capture')
        self.assertEqual(plan['source'], name)
        client.create_input.assert_not_called()
        client.create_scene_item.assert_called_once_with('Capture', name, True)

    def client(self, spotify=True):
        client = Mock()
        inputs = [{'inputName': 'Desktop Audio', 'inputKind': 'wasapi_output_capture'}]
        if spotify:
            inputs.append({'inputName': 'Spotify audio', 'inputKind': 'wasapi_process_output_capture'})
        client.get_input_list.return_value.inputs = inputs
        client.send.return_value = {'desktop1': 'Desktop Audio', 'desktop2': None}
        client.get_input_settings.side_effect = lambda name: SimpleNamespace(input_settings=
            {'window': 'Some title:Chrome_WidgetWin_0:Spotify.exe'} if name == 'Spotify audio' else {'device_id': 'default'})
        client.get_input_kind_list.return_value.input_kinds = ['wasapi_process_output_capture', 'wasapi_output_capture']
        client.get_input_properties_list_property_items.return_value.property_items = []
        client.get_input_mute.return_value.input_muted = False
        client.get_input_volume.return_value.input_volume_mul = 0.5
        client.get_input_audio_tracks.return_value.input_audio_tracks = {'1': True, '2': False}
        client.get_input_audio_monitor_type.return_value.monitor_type = 'OBS_MONITORING_TYPE_MONITOR_ONLY'
        client.get_scene_item_id.return_value.scene_item_id = 7
        return client

    def test_spotify_capture_temporarily_mutes_duplicate_desktop_audio(self):
        client = self.client()
        plan = obs_audio.plan(client, {'branding_key': 'test', 'music_capture': 'spotify'}, 'Capture')
        self.assertEqual(plan['route'], 'Spotify direct')
        self.assertEqual(plan['source'], 'Spotify audio')
        self.assertEqual(plan['mute'], ['Desktop Audio'])
        obs_audio.apply(client, plan)
        client.set_input_mute.assert_any_call('Desktop Audio', True)
        client.set_input_mute.assert_any_call('Spotify audio', False)
        client.set_input_audio_tracks.assert_called_with('Spotify audio', {str(i): True for i in range(1,7)})
        obs_audio.restore(client, plan)
        client.set_input_mute.assert_any_call('Desktop Audio', False)
        client.set_input_volume.assert_called_with('Spotify audio', vol_mul=0.5)
        client.set_input_audio_tracks.assert_called_with('Spotify audio', {'1': True, '2': False})
        self.assertTrue(plan['restored'])

    def test_system_capture_reuses_global_desktop_audio_without_duplicate_source(self):
        client = self.client()
        plan = obs_audio.plan(client, {'branding_key': 'test', 'music_capture': 'system'}, 'Capture')
        self.assertEqual(plan['route'], 'System output')
        self.assertTrue(plan['global_source'])
        self.assertEqual(plan['mute'], ['Spotify audio'])
        client.create_input.assert_not_called()
        client.create_scene_item.assert_not_called()

    def test_spotify_unavailable_falls_back_to_system_audio_with_warning(self):
        client = self.client(spotify=False)
        plan = obs_audio.plan(client, {'branding_key': 'test', 'music_capture': 'spotify'}, 'Capture')
        self.assertEqual(plan['route'], 'System output')
        self.assertIn('unavailable', plan['warning'])
        client.set_scene_item_enabled.assert_not_called()

    def test_creates_direct_capture_for_spotify_window(self):
        client = self.client(spotify=False)
        client.get_input_properties_list_property_items.return_value.property_items = [
            {'itemValue': 'Some title:Chrome_WidgetWin_0:Spotify.exe', 'itemEnabled': True}]
        plan = obs_audio.plan(client, {'branding_key': 'test', 'music_capture': 'spotify'}, 'Capture')
        self.assertEqual(plan['route'], 'Spotify direct')
        client.set_input_settings.assert_called_with(plan['source'],
            {'window': 'Some title:Chrome_WidgetWin_0:Spotify.exe', 'priority': 2}, True)

    def test_creates_system_output_capture_if_no_desktop_source_exists(self):
        client = self.client(spotify=False)
        client.send.return_value = {}
        client.get_input_list.return_value.inputs = []
        plan = obs_audio.plan(client, {'branding_key': 'test', 'music_capture': 'system'}, 'Capture')
        client.create_input.assert_called_with('Capture', plan['source'], 'wasapi_output_capture', {'device_id': 'default'}, True)

    def test_restore_failure_is_reported_and_remains_retryable(self):
        client = self.client()
        plan = obs_audio.plan(client, {'branding_key': 'test', 'music_capture': 'spotify'}, 'Capture')
        client.set_input_mute.side_effect = OSError()
        with self.assertRaises(obs_audio.AudioError):
            obs_audio.restore(client, plan)
        self.assertFalse(plan['restored'])

    def test_unavailable_application_capture_api_falls_back_to_system_output(self):
        client = self.client(spotify=False)
        client.create_input.side_effect = RuntimeError('Application capture unavailable')
        plan = obs_audio.plan(client, {'branding_key': 'test', 'music_capture': 'spotify'}, 'Capture')
        self.assertEqual(plan['route'], 'System output')
        self.assertIn('using system output', plan['warning'])

    def test_nested_existing_spotify_source_is_not_added_a_second_time(self):
        client = self.client()
        plan = obs_audio.plan(client, {'branding_key': 'test', 'music_capture': 'spotify'}, 'Capture', nested_audio={'Spotify audio'})
        self.assertEqual(plan['route'], 'Spotify direct')
        client.create_scene_item.assert_not_called()

    def test_obsolete_managed_audio_sources_are_not_muted_or_snapshotted(self):
        client = self.client()
        obsolete = 'MOL Recorder test Spotify audio'
        client.get_input_list.return_value.inputs.append({'inputName': obsolete, 'inputKind': 'wasapi_process_output_capture'})
        plan = obs_audio.plan(client, {'branding_key': 'test', 'music_capture': 'system'}, 'Capture')
        self.assertNotIn(obsolete, plan['mute'])
        self.assertNotIn(obsolete, plan['snapshot'])

    def test_explicit_audio_source_overrides_automatic_preference(self):
        client = self.client()
        plan = obs_audio.plan(client, {'branding_key':'test','audio_source':'Desktop Audio','music_capture':'spotify'}, 'Capture')
        self.assertEqual(plan['source'], 'Desktop Audio')
        self.assertEqual(plan['route'], 'Desktop Audio')
        client.create_input.assert_not_called()

    def test_missing_explicit_audio_source_never_switches_to_another_source(self):
        with self.assertRaises(obs_audio.AudioError):
            obs_audio.plan(self.client(), {'branding_key':'test','audio_source':'Missing'}, 'Capture')

    def test_selected_video_audio_does_not_add_another_video_item(self):
        client = self.client()
        client.get_input_list.return_value.inputs.append({'inputName':'blackmagic','inputKind':'dshow_input'})
        plan = obs_audio.plan(client, {'branding_key':'test','video_source':'blackmagic','audio_source':'@video'}, 'Capture')
        self.assertEqual(plan['source'], 'blackmagic')
        self.assertTrue(plan['video_audio'])
        client.create_scene_item.assert_not_called()

    def test_explicit_audio_mutes_unselected_video_device_audio(self):
        client = self.client()
        client.get_input_list.return_value.inputs.append({'inputName':'blackmagic','inputKind':'dshow_input'})
        plan = obs_audio.plan(client, {'branding_key':'test','video_source':'blackmagic','audio_source':'Spotify audio'}, 'Capture')
        self.assertIn('blackmagic', plan['mute'])

    def test_background_capture_stays_muted_until_explicitly_started(self):
        client = self.client()
        plan = obs_audio.plan(client, {'branding_key':'test','music_capture':'spotify'}, 'Capture')
        obs_audio.apply(client, plan, defer=True)
        self.assertTrue(plan['deferred'])
        client.set_input_mute.assert_any_call('Spotify audio', True)
        self.assertNotIn(('Spotify audio',False), [call.args for call in client.set_input_mute.call_args_list])
        obs_audio.start_background(client, plan)
        client.set_input_mute.assert_called_with('Spotify audio', False)
        self.assertFalse(plan['deferred'])
        count = client.set_input_mute.call_count
        obs_audio.start_background(client, plan)
        self.assertEqual(client.set_input_mute.call_count, count)
