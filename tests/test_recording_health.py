import unittest
from types import SimpleNamespace
from unittest.mock import patch
import recording_health


class HealthTests(unittest.TestCase):
    def test_meter_reports_unknown_signal_then_quiet_without_persisting_audio(self):
        meter=recording_health.AudioMeter()
        self.assertEqual(meter.status('Spotify'),'unknown')
        with patch.object(recording_health.time,'monotonic',return_value=10):
            meter.on_input_volume_meters(SimpleNamespace(inputs=[{'inputName':'Spotify','inputLevelsMul':[[0.1,0.2,0.3]]}]))
            self.assertEqual(meter.status('Spotify'),'signal')
        with patch.object(recording_health.time,'monotonic',return_value=16):
            meter.on_input_volume_meters(SimpleNamespace(inputs=[{'inputName':'Spotify','inputLevelsMul':[[0,0,0]]}]))
            self.assertEqual(meter.status('Spotify'),'quiet')
        with patch.object(recording_health.time,'monotonic',return_value=22):
            self.assertEqual(meter.status('Spotify'),'unknown')

    def test_stats_warn_on_new_skips_not_historic_totals(self):
        stats=SimpleNamespace(cpu_usage=20,active_fps=30,render_skipped_frames=100,output_skipped_frames=50)
        previous,warnings=recording_health.stats_summary(stats)
        self.assertEqual(warnings,[])
        stats.cpu_usage=95;stats.output_skipped_frames=51
        _,warnings=recording_health.stats_summary(stats,previous)
        self.assertEqual(len(warnings),2)
        self.assertIn('encoding',' '.join(warnings))

    def test_unavailable_stats_are_not_reported_as_healthy_numbers(self):
        values,warnings=recording_health.stats_summary(SimpleNamespace())
        self.assertTrue(all(value is None for value in values.values()))
