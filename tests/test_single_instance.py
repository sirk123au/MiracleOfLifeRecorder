import unittest
import os
import subprocess
import sys
import uuid
from unittest.mock import Mock, patch
import single_instance


class SingleInstance(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows mutex integration test')
    def test_second_windows_process_cannot_acquire_same_mutex(self):
        name = 'Local\\MOL-Test-' + uuid.uuid4().hex
        code = f'import single_instance; single_instance.MUTEX_NAME={name!r}; print(single_instance.acquire(), flush=True)'
        first = subprocess.Popen([sys.executable, '-c', code+'; input()'],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(first.stdout.readline().strip(), 'True')
            second = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=10)
            self.assertEqual(second.returncode, 0)
            self.assertEqual(second.stdout.strip(), 'False')
        finally:
            first.communicate('\n', timeout=10)

    def test_first_instance_holds_mutex_for_process_lifetime(self):
        kernel = Mock()
        kernel.CreateMutexW.return_value = 123
        with patch.object(single_instance, '_handle', None):
            self.assertTrue(single_instance.acquire(kernel, lambda: 0))
            self.assertTrue(single_instance.acquire(kernel, lambda: 0))
            kernel.CreateMutexW.assert_called_once_with(None, False, single_instance.MUTEX_NAME)
            kernel.CloseHandle.assert_not_called()

    def test_duplicate_closes_only_its_new_handle(self):
        kernel = Mock()
        kernel.CreateMutexW.return_value = 456
        with patch.object(single_instance, '_handle', None):
            self.assertFalse(single_instance.acquire(kernel, lambda: 183))
            kernel.CloseHandle.assert_called_once_with(456)
            self.assertIsNone(single_instance._handle)

    def test_mutex_failure_does_not_allow_an_unprotected_instance(self):
        kernel = Mock()
        kernel.CreateMutexW.return_value = None
        with patch.object(single_instance, '_handle', None), self.assertRaises(OSError):
            single_instance.acquire(kernel, lambda: 5)
