"""Detached, verified installation after the recorder process has exited."""
import json
import ctypes
from ctypes import wintypes
import os
import re
from pathlib import Path
import secrets
import subprocess
import sys
from app_updates import UpdateError,verified


def installed_executable():
    if os.name!='nt' or not getattr(sys,'frozen',False):return None
    executable=Path(sys.executable).resolve()
    if executable.name.casefold()!='miracleofliferecorder.exe' or not (executable.parent/'unins000.exe').is_file():return None
    return executable


def ps_literal(value):return "'"+str(value).replace("'","''")+"'"


def launch_helper(command,folder):
    # PyInstaller's DLL search directory is inherited by external processes.
    # Do not let the helper load/lock the runtime it is about to replace.
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetDllDirectoryW.argtypes=[wintypes.DWORD,wintypes.LPWSTR]
    kernel.GetDllDirectoryW.restype=wintypes.DWORD
    kernel.SetDllDirectoryW.argtypes=[wintypes.LPCWSTR];kernel.SetDllDirectoryW.restype=wintypes.BOOL
    buffer=ctypes.create_unicode_buffer(32768)
    length=kernel.GetDllDirectoryW(len(buffer),buffer)
    if length>=len(buffer):raise UpdateError('The application runtime path could not be isolated.')
    previous=buffer.value
    env={key:value for key,value in os.environ.items() if not key.startswith('_PYI_') and key!='_MEIPASS2'}
    env['PYINSTALLER_RESET_ENVIRONMENT']='1'
    runtime=Path(getattr(sys,'_MEIPASS',folder)).resolve()
    paths=[]
    for entry in env.get('PATH','').split(os.pathsep):
        try:
            if Path(entry.strip('"')).resolve().is_relative_to(runtime):continue
        except (OSError,ValueError):pass
        paths.append(entry)
    env['PATH']=os.pathsep.join(paths)
    if not kernel.SetDllDirectoryW(None):raise UpdateError('The update helper could not be isolated from the runtime.')
    try:
        return subprocess.Popen(command,cwd=str(folder),env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP,close_fds=True)
    finally:kernel.SetDllDirectoryW(previous or None)


def helper_script(installer,release,result_path,parent_pid):
    # All paths are literal strings, never evaluated as PowerShell commands.
    template=r'''$ErrorActionPreference = 'Stop'
function Save-Result([string]$state, [string]$message) {
    $safeState = $state.Replace('\','\\').Replace('"','\"')
    $safeMessage = $message.Replace('\','\\').Replace('"','\"').Replace("`r",'\r').Replace("`n",'\n')
    $json = '{"state":"' + $safeState + '","message":"' + $safeMessage + '"}'
    [System.IO.File]::WriteAllText(__RESULT__, $json, [System.Text.Encoding]::UTF8)
}
try {
    Save-Result 'waiting' 'Update is waiting for the recorder to close.'
    $recorderProcess = $null
    try { $recorderProcess = [System.Diagnostics.Process]::GetProcessById(__PID__) } catch [System.ArgumentException] {}
    if ($null -ne $recorderProcess) {
        try {
            if (-not $recorderProcess.WaitForExit(90000)) {
                Save-Result 'deferred' 'The recorder did not exit. Update was postponed; no process was stopped.'
                exit 0
            }
        } finally { $recorderProcess.Dispose() }
    }
    # A newly opened recorder must not be replaced by the delayed installer.
    $recorderMutex = $null
    try { $recorderMutex = [System.Threading.Mutex]::OpenExisting('Local\MiracleOfLifeRecorder') } catch [System.Threading.WaitHandleCannotBeOpenedException] {}
    if ($null -ne $recorderMutex) {
        $recorderMutex.Dispose()
        Save-Result 'deferred' 'The recorder was reopened. Update was postponed until a later exit.'
        exit 0
    }
    $hashStream = [System.IO.File]::OpenRead(__INSTALLER__)
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = [System.BitConverter]::ToString($hasher.ComputeHash($hashStream)).Replace('-','').ToLowerInvariant()
    } finally { $hashStream.Dispose(); $hasher.Dispose() }
    if ($hash -ne __DIGEST__) { throw 'Installer checksum mismatch' }
    $setupInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $setupInfo.FileName = __INSTALLER__
    $setupInfo.Arguments = '/VERYSILENT /SUPPRESSMSGBOXES /SP- /NORESTART /RESTARTEXITCODE=3010 /NOCLOSEAPPLICATIONS /NORESTARTAPPLICATIONS'
    $setupInfo.WorkingDirectory = [System.IO.Path]::GetDirectoryName(__INSTALLER__)
    $setupInfo.UseShellExecute = $false
    $setupInfo.CreateNoWindow = $true
    $setupInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $setupProcess = [System.Diagnostics.Process]::Start($setupInfo)
    $setupProcess.WaitForExit()
    if ($setupProcess.ExitCode -eq 0) {
        Save-Result 'installed' 'The application update installed successfully.'
    } elseif ($setupProcess.ExitCode -eq 3010) {
        Save-Result 'restart_needed' 'The update needs a Windows restart. No automatic reboot was performed.'
    } else {
        Save-Result 'error' 'Automatic installation did not finish. Use the downloaded installer manually after closing the recorder.'
    }
    $setupProcess.Dispose()
} catch {
    try { Save-Result 'error' 'Automatic installation was blocked or could not complete. Check the downloaded installer after closing the recorder.' } catch {}
    exit 1
}
'''
    values={'RESULT':ps_literal(result_path),'INSTALLER':ps_literal(installer),'DIGEST':ps_literal(release['sha256']),'PID':str(int(parent_pid))}
    return re.sub(r'__(RESULT|INSTALLER|DIGEST|PID)__',lambda match:values[match.group(1)],template)


def handoff(updater):
    if installed_executable() is None:raise UpdateError('Automatic installation is available in the installed Windows app. Source and portable runs can download updates only.')
    with updater.lock:
        folder=updater.folder();release=dict(updater.release);installer=folder/release['filename']
        if not installer.resolve().is_relative_to(folder) or not verified(installer,release):raise UpdateError('The installer could not be verified again.')
        helper=folder/('install-'+secrets.token_hex(12)+'.ps1')
        result=folder/'install-result.json'
        with helper.open('x',encoding='utf-8-sig') as output:
            output.write(helper_script(installer,release,result,os.getpid()));output.flush();os.fsync(output.fileno())
        powershell=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe'
        if not powershell.is_file():raise UpdateError('Windows PowerShell is unavailable. Install the downloaded update manually.')
        launch_helper([str(powershell),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',str(helper)],folder)
        updater.set(message='Update installation will begin after the recorder exits.')


def last_result(root):
    try:
        value=json.loads((Path(root)/'install-result.json').read_text(encoding='utf-8-sig'))
        if value.get('state') in ('installed','restart_needed','deferred','error','waiting'):
            messages={'installed':'The previous automatic update installed successfully.',
                      'restart_needed':'The previous update requested a Windows restart.',
                      'deferred':'The previous update was postponed because the recorder was running.',
                      'waiting':'An update was waiting for the recorder to close.',
                      'error':'The previous automatic update did not complete. You can install the downloaded update manually.'}
            return messages[value['state']]
    except (OSError,ValueError,TypeError,AttributeError):pass
    return ''
