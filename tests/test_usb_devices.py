import ctypes
import struct
import unittest
from unittest.mock import Mock
import usb_devices


class UsbDeviceTests(unittest.TestCase):
    def kernel(self,bus=7,size=36):
        kernel=Mock()
        kernel.CreateFileW.return_value=123
        def query(handle,code,request,request_size,output,output_size,returned,overlapped):
            data=bytearray(36)
            struct.pack_into('<I',data,4,36)
            struct.pack_into('<I',data,28,bus)
            ctypes.memmove(output,bytes(data),36)
            returned._obj.value=size
            return True
        kernel.DeviceIoControl.side_effect=query
        return kernel

    def test_reads_usb_bus_with_metadata_only_access_and_closes_handle(self):
        kernel=self.kernel()
        self.assertEqual(usb_devices.storage_bus_type('F:/',kernel),7)
        args=kernel.CreateFileW.call_args.args
        self.assertEqual(args[0],chr(92)*2+'.'+chr(92)+'F:')
        self.assertEqual(args[1],0)
        self.assertEqual(args[2],3)
        kernel.CloseHandle.assert_called_once_with(123)

    def test_internal_nvme_or_sata_is_not_reported_as_usb(self):
        for bus in (11,17,0):
            kernel=self.kernel(bus)
            self.assertEqual(usb_devices.storage_bus_type('C:/',kernel),bus)
            kernel.CloseHandle.assert_called_once_with(123)
        with unittest.mock.patch.object(usb_devices,'storage_bus_type',return_value=17):
            self.assertFalse(usb_devices.is_usb_volume('C:/'))

    def test_denied_truncated_or_failed_queries_do_not_qualify_fixed_disks(self):
        kernel=self.kernel(size=12)
        self.assertIsNone(usb_devices.storage_bus_type('F:/',kernel))
        kernel.CloseHandle.assert_called_once_with(123)
        kernel=self.kernel();kernel.DeviceIoControl.side_effect=None;kernel.DeviceIoControl.return_value=False
        self.assertIsNone(usb_devices.storage_bus_type('F:/',kernel))
        kernel.CloseHandle.assert_called_once_with(123)
        kernel=self.kernel();kernel.CreateFileW.return_value=ctypes.c_void_p(-1).value
        self.assertIsNone(usb_devices.storage_bus_type('F:/',kernel))
        kernel.CloseHandle.assert_not_called()

    def test_non_volume_paths_are_never_opened(self):
        kernel=self.kernel()
        self.assertIsNone(usb_devices.storage_bus_type('C:/folder',kernel))
        kernel.CreateFileW.assert_not_called()
