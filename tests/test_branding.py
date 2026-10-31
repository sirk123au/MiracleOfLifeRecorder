import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PIL import Image
import obs_branding


class Branding(unittest.TestCase):
    def test_music_restart_happens_after_intro_before_capture_unmutes(self):
        client=Mock();order=[]
        sequence={'media_input':'Intro','fade':'Fade','live':'Live',
                  'audio':{'source':'Spotify','deferred':True}}
        client.set_input_mute.side_effect=lambda *args:order.append('unmute')
        with patch.object(obs_branding,'wait_for_video',side_effect=lambda *args:order.append('intro-ended')), patch.object(obs_branding.time,'sleep'):
            obs_branding.enter_live(client,sequence,lambda:order.append('restart'))
        self.assertEqual(order,['intro-ended','restart','unmute'])

    def test_overlay_and_background_audio_fade_to_their_final_levels(self):
        client = Mock()
        sequence = {'media_input':'Intro','fade':'Fade','live':'Live',
                    'overlay_source':'Logo','overlay_filter':'Opacity',
                    'audio':{'source':'Spotify','deferred':True,'capture_volume':0.5}}
        with patch.object(obs_branding, 'wait_for_video'), patch.object(obs_branding.time, 'sleep'):
            obs_branding.enter_live(client, sequence)
        volumes = [call.kwargs['vol_mul'] for call in client.set_input_volume.call_args_list]
        self.assertEqual(volumes[0], 0)
        self.assertEqual(volumes[-1], 0.5)
        self.assertEqual(volumes, sorted(volumes))
        self.assertEqual(len(volumes), 21)
        client.set_source_filter_settings.assert_called_with('Logo','Opacity',{'opacity':1.0},True)
        client.set_input_mute.assert_called_once_with('Spotify',False)
        self.assertFalse(sequence['audio']['deferred'])

    def test_cleanup_removes_all_owned_scenes_and_preserves_operator_scenes(self):
        client = Mock()
        prefix = 'MOL Recorder test'
        owned = [prefix+s for s in (' Live',' Logo',' Outro',' Capture',' Test Capture')]
        client.get_current_program_scene.return_value.current_program_scene_name = 'Ultrasound'
        client.get_scene_list.return_value.scenes = [{'sceneName':name} for name in
            [*owned,'Ultrasound','MOL Recorder other Live',prefix+' Custom']]
        obs_branding.cleanup_scenes(client, {'managed_prefix':prefix,'original':'Ultrasound'})
        self.assertEqual([call.args[0] for call in client.remove_scene.call_args_list], owned)

    def test_cleanup_refuses_to_delete_active_scene(self):
        client = Mock()
        client.get_current_program_scene.return_value.current_program_scene_name = 'MOL Recorder test Live'
        with self.assertRaises(obs_branding.BrandingError):
            obs_branding.cleanup_scenes(client, {'managed_prefix':'MOL Recorder test','original':'Ultrasound'})
        client.remove_scene.assert_not_called()

    def capture_client(self):
        def audio_plan(client, settings, scene, nested_audio=None):
            client.create_scene_item(scene, 'Spotify audio', True)
            return {'source': 'Spotify audio', 'route': 'Spotify direct', 'warning': '',
                    'global_source': False, 'mute': [], 'snapshot': {}}
        audio_patch = patch.object(obs_branding.obs_audio, 'plan', side_effect=audio_plan)
        audio_patch.start()
        self.addCleanup(audio_patch.stop)
        client = Mock()
        client.get_input_list.return_value.inputs = [
            {'inputName': 'blackmagic', 'inputKind': 'dshow_input'},
            {'inputName': 'Other camera', 'inputKind': 'dshow_input'},
            {'inputName': 'Desktop display', 'inputKind': 'monitor_capture'},
            {'inputName': 'Spotify audio', 'inputKind': 'wasapi_process_output_capture'}]
        client.get_studio_mode_enabled.return_value.studio_mode_enabled = False
        client.get_current_program_scene.return_value.current_program_scene_name = 'Original'
        client.get_scene_list.return_value.scenes = [{'sceneName': 'Original'}]
        client.get_scene_item_list.side_effect = lambda scene: SimpleNamespace(scene_items=[
            {'sourceName': 'Other camera', 'sceneItemEnabled': True},
            {'sourceName': 'Desktop display', 'sceneItemEnabled': True},
            {'sourceName': 'Spotify audio', 'sceneItemEnabled': True}] if scene == 'Original' else [])
        client.get_current_scene_transition.return_value = SimpleNamespace(transition_name='Fade', transition_duration=500)
        client.get_scene_transition_list.return_value.transitions = [{'transitionName': 'Cut', 'transitionKind': 'cut_transition'}]
        client.get_video_settings.return_value = SimpleNamespace(base_width=1920, base_height=1080)
        client.create_scene_item.return_value.scene_item_id = 1
        return client

    def test_capture_scene_only_includes_blackmagic_and_existing_audio(self):
        client = self.capture_client()
        sequence = obs_branding.prepare_capture(client, {'branding_key': 'test', 'branding_enabled': False})
        self.assertTrue(sequence['capture_only'])
        self.assertEqual([call.args[1] for call in client.create_scene_item.call_args_list], ['blackmagic', 'Spotify audio'])
        client.remove_input.assert_not_called()
        client.remove_scene_item.assert_not_called()
        with patch.object(obs_branding.time, 'sleep'):
            obs_branding.show_intro(client, sequence)
            obs_branding.enter_live(client, sequence)
            obs_branding.show_outro(client, sequence)
        client.set_current_program_scene.assert_called_once_with(sequence['live'])

    def test_branded_live_scene_nests_only_the_clean_capture_scene(self):
        client = self.capture_client()
        with patch.object(obs_branding, 'prepare', return_value={'original': 'capture', 'live': 'Branded live', 'logo': 'Logo'}) as prepare:
            sequence = obs_branding.prepare_capture(client, {'branding_key': 'test', 'branding_enabled': True})
        self.assertEqual(prepare.call_args.args[1]['ultrasound_scene'], 'MOL Recorder test Capture')
        self.assertEqual(sequence['original'], 'Original')

    def test_capture_missing_blackmagic_never_uses_another_camera(self):
        client = self.capture_client()
        client.get_input_list.return_value.inputs = [{'inputName': 'Other camera', 'inputKind': 'dshow_input'}]
        with self.assertRaises(obs_branding.BrandingError):
            obs_branding.prepare_capture(client, {'branding_key': 'test'})
        client.create_scene.assert_not_called()

    def test_test_mode_keeps_existing_scene_workflow(self):
        client = self.capture_client()
        client.get_input_list.return_value.inputs = [item for item in client.get_input_list.return_value.inputs if item['inputName']!='blackmagic']
        sequence = obs_branding.prepare_capture(client, {'branding_key': 'test','branding_enabled': False}, test=True)
        self.assertEqual(sequence['original'], 'Original')
        self.assertEqual(client.create_scene_item.call_args_list[0].args[1], 'Original')
        self.assertIn('Test Capture', sequence['live'])

    def test_selected_video_replaces_blackmagic_without_other_video_layers(self):
        client = self.capture_client()
        sequence = obs_branding.prepare_capture(client, {'branding_key': 'test','video_source':'Other camera','branding_enabled':False})
        self.assertTrue(sequence['capture_only'])
        self.assertEqual([call.args[1] for call in client.create_scene_item.call_args_list], ['Other camera', 'Spotify audio'])

    def test_selected_physical_device_creates_its_own_capture_without_changing_user_source(self):
        client = self.capture_client()
        client.get_input_properties_list_property_items.return_value.property_items = [
            {'itemName':'USB3.0 capture','itemValue':'new-usb','itemEnabled':True}]
        client.get_input_settings.return_value.input_settings = {'video_device_id':'old-camera'}
        client.create_input.return_value.scene_item_id = 7
        sequence = obs_branding.prepare_capture(client, {'branding_key':'test','video_source':'@device',
            'video_device_id':'new-usb','branding_enabled':False})
        client.create_input.assert_called_once_with(sequence['live'], 'MOL Recorder test Selected camera',
            'dshow_input', {'video_device_id':'new-usb','deactivate_when_not_showing':True}, True)
        client.set_input_settings.assert_not_called()

    def test_disabled_does_not_modify_obs(self):
        client = Mock()
        self.assertIsNone(obs_branding.prepare(client, {'branding_enabled': False}))
        self.assertEqual(client.mock_calls, [])

    def test_prepares_separate_scenes_and_black_background_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            logo = Path(directory) / 'logo.png'
            Image.new('RGB', (192, 108), 'black').save(logo)
            before = logo.read_bytes()
            client = Mock()
            client.get_studio_mode_enabled.return_value.studio_mode_enabled = False
            client.get_current_program_scene.return_value.current_program_scene_name = 'Ultrasound'
            client.get_scene_list.return_value.scenes = [{'sceneName': 'Ultrasound'}]
            client.get_scene_transition_list.return_value.transitions = [{'transitionName': 'Fade', 'transitionKind': 'fade_transition'}]
            client.get_current_scene_transition.return_value = SimpleNamespace(transition_name='Cut', transition_duration=None)
            client.get_video_settings.return_value = SimpleNamespace(base_width=1920, base_height=1080)
            client.get_scene_item_list.return_value.scene_items = []
            client.get_input_list.return_value.inputs = []
            client.get_source_filter_list.return_value.filters = []
            client.create_input.return_value.scene_item_id = 1
            client.create_scene_item.return_value.scene_item_id = 2
            result = obs_branding.prepare(client, {'branding_enabled': True, 'intro_image': str(logo),
                                                'watermark_image': str(logo), 'branding_key': 'test'})
            self.assertEqual(result['original'], 'Ultrasound')
            self.assertEqual(client.create_scene.call_count, 2)
            self.assertEqual(client.create_source_filter.call_count, 2)
            self.assertEqual(logo.read_bytes(), before)
            client.set_current_program_scene.assert_not_called()

    def test_sequence_timing_and_scene_switches(self):
        client = Mock()
        sequence = {'logo': 'Logo', 'live': 'Live', 'original': 'Original', 'fade': 'Fade',
                    'previous_transition': 'Cut', 'previous_duration': None}
        with patch.object(obs_branding.time, 'sleep') as wait:
            obs_branding.show_intro(client, sequence)
            obs_branding.enter_live(client, sequence)
            obs_branding.show_outro(client, sequence)
            obs_branding.restore(client, sequence)
        self.assertEqual([call.args[0] for call in client.set_current_program_scene.call_args_list],
                         ['Logo', 'Live', 'Logo', 'Original'])
        self.assertEqual([call.args[0] for call in wait.call_args_list], [1, 3, 1, 4, 1])
        for call in client.set_current_scene_transition_duration.call_args_list:
            self.assertEqual(call.args, (1000,))

    def test_missing_images_block_sequence_before_recording(self):
        with self.assertRaises(obs_branding.BrandingError):
            obs_branding.prepare(Mock(), {'branding_enabled': True})

    def test_video_is_used_as_non_looping_obs_media_source(self):
        with tempfile.TemporaryDirectory() as directory:
            intro = Path(directory) / 'intro.mp4'
            intro.write_bytes(b'video fixture')
            overlay = Path(directory) / 'overlay.png'
            Image.new('RGBA', (192, 108), (0, 0, 0, 0)).save(overlay)
            client = Mock()
            client.get_studio_mode_enabled.return_value.studio_mode_enabled = False
            client.get_current_program_scene.return_value.current_program_scene_name = 'Ultrasound'
            client.get_scene_list.return_value.scenes = [{'sceneName': 'Ultrasound'}]
            client.get_scene_transition_list.return_value.transitions = [{'transitionName': 'Fade', 'transitionKind': 'fade_transition'}]
            client.get_current_scene_transition.return_value = SimpleNamespace(transition_name='Cut', transition_duration=None)
            client.get_video_settings.return_value = SimpleNamespace(base_width=1920, base_height=1080)
            client.get_scene_item_list.return_value.scene_items = []
            client.get_input_list.return_value.inputs = []
            client.get_source_filter_list.return_value.filters = []
            client.create_input.return_value.scene_item_id = 1
            client.create_scene_item.return_value.scene_item_id = 2
            client.get_media_input_status.return_value = SimpleNamespace(media_duration=4500, media_state='OBS_MEDIA_STATE_PAUSED')
            sequence = obs_branding.prepare(client, {'branding_enabled': True, 'intro_image': str(intro),
                                                   'watermark_image': str(overlay), 'branding_key': 'test'})
            media_call = next(call for call in client.create_input.call_args_list if call.args[2]=='ffmpeg_source')
            self.assertEqual(media_call.args[3]['local_file'], str(intro))
            self.assertFalse(media_call.args[3]['looping'])
            self.assertFalse(media_call.args[3]['clear_on_media_end'])
            self.assertEqual(sequence['media_input'], media_call.args[1])
            client.get_media_input_status.return_value.media_duration = 61000
            with self.assertRaises(obs_branding.BrandingError):
                obs_branding.prepare(client, {'branding_enabled': True, 'intro_image': str(intro),
                                            'watermark_image': str(overlay), 'branding_key': 'test'})

    def test_video_plays_in_full_at_start_and_end_instead_of_three_second_hold(self):
        client = Mock()
        sequence = {'logo': 'Logo', 'live': 'Live', 'fade': 'Fade', 'media_input': 'Intro video'}
        statuses = [SimpleNamespace(media_state='OBS_MEDIA_STATE_PLAYING', media_cursor=0),
                    SimpleNamespace(media_state='OBS_MEDIA_STATE_PLAYING', media_cursor=4000),
                    SimpleNamespace(media_state='OBS_MEDIA_STATE_ENDED', media_cursor=5000)]
        client.get_media_input_status.side_effect = statuses + statuses
        with patch.object(obs_branding.time, 'sleep') as wait:
            obs_branding.show_intro(client, sequence)
            obs_branding.enter_live(client, sequence)
            obs_branding.show_outro(client, sequence)
        self.assertEqual([call.args[0] for call in client.set_current_program_scene.call_args_list], ['Logo','Live','Logo'])
        self.assertNotIn((3,), [call.args for call in wait.call_args_list])
        self.assertNotIn((4,), [call.args for call in wait.call_args_list])
        self.assertEqual(client.set_media_input_cursor.call_count, 2)

    def test_video_failure_and_timeout_are_reported(self):
        client = Mock()
        client.get_media_input_status.return_value = SimpleNamespace(media_state='OBS_MEDIA_STATE_ERROR')
        with self.assertRaises(obs_branding.BrandingError):
            obs_branding.wait_for_video(client, {'media_input':'Video'})
        client.get_media_input_status.return_value = SimpleNamespace(media_state='OBS_MEDIA_STATE_PLAYING')
        with patch.object(obs_branding.time, 'monotonic', side_effect=[0,0,66]), \
             patch.object(obs_branding.time, 'sleep'), self.assertRaises(obs_branding.BrandingError):
            obs_branding.wait_for_video(client, {'media_input':'Video'})

    def test_music_starts_after_video_finishes_and_fade_completes(self):
        order = []
        client = Mock()
        client.set_current_program_scene.side_effect = lambda *a: order.append('live')
        client.set_input_mute.side_effect = lambda *a: order.append('music')
        sequence = {'media_input':'Intro video','fade':'Fade','live':'Live',
                    'audio':{'source':'Spotify','deferred':True}}
        with patch.object(obs_branding, 'wait_for_video', side_effect=lambda *a: order.append('video-ended')), \
             patch.object(obs_branding.time, 'sleep', side_effect=lambda *a: order.append('fade-finished')):
            obs_branding.enter_live(client, sequence)
        self.assertEqual(order, ['video-ended','live','fade-finished','music'])
        client.set_input_mute.assert_called_once_with('Spotify',False)

    def test_fixed_image_outro_does_not_restart_or_wait_for_intro_video(self):
        client = Mock()
        sequence = {'fade':'Fade','logo':'Intro','outro':'Fixed outro','media_input':'Intro video'}
        with patch.object(obs_branding.time, 'sleep') as wait, \
             patch.object(obs_branding, 'wait_for_video') as playback:
            obs_branding.show_outro(client, sequence)
        client.set_current_program_scene.assert_called_once_with('Fixed outro')
        client.trigger_media_input_action.assert_not_called()
        playback.assert_not_called()
        wait.assert_called_once_with(4)

    def test_fixed_outro_is_checked_before_scene_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'logo.png'
            Image.new('RGB',(16,16),'white').save(image)
            client = Mock()
            with self.assertRaises(obs_branding.BrandingError):
                obs_branding.prepare(client, {'branding_enabled':True, 'intro_image':str(image),
                    'watermark_image':str(image), 'outro_mode':'image','outro_image':str(Path(directory)/'missing.png')})
            client.create_scene.assert_not_called()
