"""Process-lifetime Windows mutex, also checked by the installer."""
import ctypes
import os
from ctypes import wintypes

MUTEX_NAME = r'Local\MiracleOfLifeRecorder'
_handle = None


def acquire(kernel=None, last_error=None):
    global _handle
    if _handle is not None:
        return True
    if kernel is None:
        if os.name != 'nt':
            raise OSError('This application requires Windows')
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    if last_error is None:
        last_error = ctypes.get_last_error
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateMutexW(None, False, MUTEX_NAME)
    error = last_error()
    if not handle:
        raise OSError(error, 'Could not acquire the recorder instance lock')
    if error == 183:  # ERROR_ALREADY_EXISTS
        kernel.CloseHandle(handle)
        return False
    _handle = handle  # Held until process termination, including while recording.
    return True
