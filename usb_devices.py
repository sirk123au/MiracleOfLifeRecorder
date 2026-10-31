"""Identify USB-connected fixed disks with a read-only Windows storage query."""
import ctypes
from ctypes import wintypes
import os
import re
import struct

IOCTL_STORAGE_QUERY_PROPERTY=0x002D1400
BUS_TYPE_USB=7


def storage_bus_type(root, kernel=None):
    match=re.fullmatch(r'([A-Za-z]):[\\/]*',str(root))
    if not match or (kernel is None and os.name!='nt'):return None
    if kernel is None:kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    dword=ctypes.c_uint32
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,dword,dword,ctypes.c_void_p,dword,dword,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.DeviceIoControl.argtypes=[wintypes.HANDLE,dword,ctypes.c_void_p,dword,ctypes.c_void_p,dword,ctypes.POINTER(dword),ctypes.c_void_p]
    kernel.DeviceIoControl.restype=wintypes.BOOL
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.CloseHandle.restype=wintypes.BOOL
    # Access 0 requests metadata only. FILE_SHARE_READ | FILE_SHARE_WRITE.
    device='\\\\.\\'+match.group(1).upper()+':'
    handle=kernel.CreateFileW(device,0,3,None,3,0,None)
    if handle in (None,ctypes.c_void_p(-1).value):return None
    try:
        # StorageDeviceProperty=0, PropertyStandardQuery=0, plus padding.
        query=ctypes.create_string_buffer(12)
        descriptor=ctypes.create_string_buffer(4096)
        returned=dword()
        success=kernel.DeviceIoControl(handle,IOCTL_STORAGE_QUERY_PROPERTY,query,len(query),
                                       descriptor,len(descriptor),ctypes.byref(returned),None)
        if not success or returned.value<36:return None
        data=descriptor.raw
        if struct.unpack_from('<I',data,4)[0]<36:return None
        return struct.unpack_from('<I',data,28)[0]
    except OSError:
        return None
    finally:
        kernel.CloseHandle(handle)


def is_usb_volume(root):
    try:return storage_bus_type(root)==BUS_TYPE_USB
    except OSError:return False
