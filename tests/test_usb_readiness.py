import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
import usb_readiness


class UsbReadiness(unittest.TestCase):
    def setUp(self):
        self.drive={'root':Path('D:/'),'serial':123,'label':'Recording USB'}

    def test_capacity_estimate_subtracts_reserve_and_labels_assumption(self):
        usage=SimpleNamespace(free=10*1024**3,total=64*1024**3)
        with patch.object(usb_readiness.shutil,'disk_usage',return_value=usage):
            result=usb_readiness.summarize([self.drive],'0000007B',5)
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['estimated_minutes'],int(5*1024**3*8/20_000_000/60))
        self.assertEqual(result['estimate_basis'],'assumed 20 Mbps')
        self.assertEqual(result['free_bytes'],usage.free)

    def test_recent_rate_updates_estimate(self):
        with patch.object(usb_readiness.shutil,'disk_usage',return_value=SimpleNamespace(free=10**10,total=2*10**10)):
            result=usb_readiness.summarize([self.drive],'0000007B',0,40)
        self.assertEqual(result['estimated_minutes'],int(10**10*8/40_000_000/60))
        self.assertEqual(result['estimate_basis'],'recent OBS recording average')
        self.assertEqual(usb_readiness.observed_rate(SimpleNamespace(output_bytes=25_000_000,output_duration=10000)),20)
        self.assertIsNone(usb_readiness.observed_rate(SimpleNamespace(output_bytes=25,output_duration=1000)))

    def test_missing_or_duplicate_drive_never_uses_another_drive(self):
        with patch.object(usb_readiness.shutil,'disk_usage') as usage:
            self.assertEqual(usb_readiness.summarize([], '',5)['status'],'not_selected')
            result=usb_readiness.summarize([self.drive],'000000FF',5,saved_name='Saved USB')
            self.assertEqual(result['status'],'disconnected')
            self.assertEqual(result['drive'],'Saved USB')
            self.assertEqual(usb_readiness.summarize([self.drive,self.drive],'0000007B',5)['status'],'ambiguous')
            usage.assert_not_called()

    def test_low_space_shows_zero_time_above_reserve(self):
        with patch.object(usb_readiness.shutil,'disk_usage',return_value=SimpleNamespace(free=1024**3,total=64*1024**3)):
            result=usb_readiness.summarize([self.drive],'0000007B',5)
        self.assertEqual(result['status'],'low_space')
        self.assertTrue(result['connected'])
        self.assertEqual(result['estimated_minutes'],0)
