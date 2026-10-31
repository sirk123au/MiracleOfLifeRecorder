"""Windows current-user DPAPI storage, with encrypted atomic writes."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path


class SecretError(Exception):pass


class Blob(ctypes.Structure):
    _fields_=[('size',ctypes.c_uint32),('data',ctypes.POINTER(ctypes.c_ubyte))]


def transform(data,decrypt=False):
    if os.name!='nt':raise SecretError('Secure credential storage requires Windows.')
    buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    output=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    if decrypt:
        fn=crypt.CryptUnprotectData
        fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint32,ctypes.POINTER(Blob)]
        success=fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(output))
    else:
        fn=crypt.CryptProtectData
        fn.argtypes=[ctypes.POINTER(Blob),wintypes.LPCWSTR,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint32,ctypes.POINTER(Blob)]
        success=fn(ctypes.byref(source),'Miracle of Life Gmail',None,None,None,1,ctypes.byref(output))
    if not success:raise SecretError('Credentials could not be protected or opened for this Windows user.')
    try:return ctypes.string_at(output.data,output.size)
    finally:kernel.LocalFree(output.data)


def save(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    encrypted=transform(data)
    temporary=path.with_suffix('.tmp')
    with temporary.open('wb') as output:
        output.write(encrypted);output.flush();os.fsync(output.fileno())
    temporary.replace(path)


def load(path):return transform(Path(path).read_bytes(),decrypt=True)
