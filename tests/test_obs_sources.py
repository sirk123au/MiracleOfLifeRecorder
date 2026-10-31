import unittest
import obs_sources


class SourceChoices(unittest.TestCase):
    def test_choices_separate_audio_and_video_and_hide_internal_sources(self):
        result = obs_sources.choices([
            {'inputName':'Camera','inputKind':'dshow_input'},
            {'inputName':'Desktop Audio','inputKind':'wasapi_output_capture'},
            {'inputName':'Spotify','inputKind':'wasapi_process_output_capture'},
            {'inputName':'MOL Recorder test Intro','inputKind':'ffmpeg_source'}])
        self.assertEqual(result['video'], ['Camera'])
        self.assertEqual(result['audio'], ['Desktop Audio','Spotify'])

    def test_ambiguous_case_insensitive_names_are_not_guessed(self):
        self.assertIsNone(obs_sources.resolve('camera', {'Camera':'dshow_input','CAMERA':'dshow_input'}))
        self.assertEqual(obs_sources.resolve('Camera', {'Camera':'dshow_input','CAMERA':'dshow_input'}), 'Camera')
