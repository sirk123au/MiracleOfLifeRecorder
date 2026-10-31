import unittest
from unittest.mock import Mock, patch
import obs_preview


class PreviewTests(unittest.TestCase):
    def client(self):
        client=Mock()
        client.get_current_program_scene.return_value.current_program_scene_name='Scene'
        client.get_source_screenshot.return_value.image_data='data:image/jpeg;base64,aW1hZ2U='
        return client

    def test_full_resolution_reuses_connection_and_cached_frame(self):
        service=obs_preview.Preview()
        client=self.client()
        factory=Mock(return_value=client)
        with patch.object(obs_preview.time,'monotonic',return_value=10):
            self.assertEqual(service.capture(factory,('local',),'full'),b'image')
            self.assertEqual(service.capture(factory,('local',),'full'),b'image')
        factory.assert_called_once()
        client.get_source_screenshot.assert_called_once_with('Scene','jpg',1920,1080,80)

    def test_quality_or_connection_change_refreshes_frame(self):
        service=obs_preview.Preview()
        client=self.client()
        service.capture(lambda:client,('local',),'full')
        service.capture(lambda:client,('local',),'low')
        client.get_source_screenshot.assert_called_with('Scene','jpg',960,540,80)
        service.capture(lambda:client,('new-host',),'hd')
        client.disconnect.assert_called_once()
        client.get_source_screenshot.assert_called_with('Scene','jpg',1280,720,80)

    def test_capture_error_clears_stale_frame_and_reconnects(self):
        service=obs_preview.Preview()
        client=self.client()
        service.capture(lambda:client,('local',),'low')
        client.get_source_screenshot.side_effect=OSError('private details')
        with self.assertRaises(OSError):service.capture(lambda:client,('local',),'full')
        self.assertIsNone(service.frame)
        self.assertIsNone(service.connection)
        client.disconnect.assert_called_once()

    def test_invalid_mode_does_not_connect(self):
        factory=Mock()
        with self.assertRaises(ValueError):obs_preview.Preview().capture(factory,(),'unlimited')
        factory.assert_not_called()
