import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import obs_devices


class CaptureDevices(unittest.TestCase):
    def client(self):
        client = Mock()
        client.get_input_list.return_value.inputs = [{'inputName':'Video Capture Device','inputKind':'dshow_input'}]
        client.get_input_properties_list_property_items.return_value.property_items = [
            {'itemName':'USB3.0 capture','itemValue':'usb-id','itemEnabled':True},
            {'itemName':'Pixel virtual camera','itemValue':'phone-id','itemEnabled':True},
            {'itemName':'OBS Virtual Camera','itemValue':'obs-id','itemEnabled':True},
            {'itemName':'Disconnected device','itemValue':'gone','itemEnabled':False}]
        client.get_input_settings.return_value.input_settings = {'video_device_id':'obs-id'}
        return client

    def test_lists_all_enabled_devices_not_just_current_capture_source(self):
        client = self.client()
        devices = obs_devices.list_devices(client, {'branding_key':'test'})
        self.assertEqual({item['id'] for item in devices}, {'usb-id','phone-id','obs-id'})
        client.get_input_properties_list_property_items.assert_called_once_with('Video Capture Device','video_device_id')
        client.create_input.assert_not_called()
        client.set_input_settings.assert_not_called()

    def test_reuses_existing_source_only_for_the_selected_device(self):
        client = self.client()
        inputs = client.get_input_list.return_value.inputs
        self.assertEqual(obs_devices.select(client, {'video_device_id':'obs-id'}, inputs), 'Video Capture Device')
        self.assertIsNone(obs_devices.select(client, {'video_device_id':'usb-id'}, inputs))
        with self.assertRaises(ValueError):
            obs_devices.select(client, {'video_device_id':'disconnected'}, inputs)

    def test_enumeration_without_an_existing_source_uses_disabled_temporary_probe(self):
        client = self.client()
        client.get_input_list.return_value.inputs = []
        devices = obs_devices.list_devices(client, {'branding_key':'test'})
        self.assertEqual(len(devices), 3)
        created = client.create_input.call_args
        self.assertEqual(created.args[2], 'dshow_input')
        self.assertEqual(created.args[3]['video_device_id'], '')
        self.assertFalse(created.args[4])
        client.remove_scene.assert_called_once_with(client.create_scene.call_args.args[0])

    def test_probe_is_cleaned_up_even_when_enumeration_fails(self):
        client = self.client()
        client.get_input_list.return_value.inputs = []
        client.get_input_properties_list_property_items.side_effect = OSError()
        with self.assertRaises(OSError):
            obs_devices.list_devices(client, {'branding_key':'test'})
        client.remove_scene.assert_called_once()
