"""Read/control Spotify's Windows session; track metadata stays in memory."""
import asyncio
import os
import threading
import time
import subprocess
import ctypes
from ctypes import wintypes

_launch_lock = threading.Lock()
_last_launch = None

_lock = threading.Lock()
_checked = None
_cached = None


class SpotifyControlError(Exception):
    pass


def _minimize_spotify_windows(user, kernel, callback_type):
    minimized = []

    def visit(window, _):
        if not user.IsWindowVisible(window) or user.IsIconic(window):
            return True
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(window, ctypes.byref(pid))
        process = kernel.OpenProcess(0x1000, False, pid.value)
        if not process:
            return True
        try:
            path = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(path))
            if kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(length)):
                if path.value.rsplit('\\', 1)[-1].casefold() == 'spotify.exe':
                    if user.ShowWindowAsync(window, 7):  # SW_SHOWMINNOACTIVE
                        minimized.append(window)
        finally:
            kernel.CloseHandle(process)
        return True

    user.EnumWindows(callback_type(visit), 0)
    return bool(minimized)


def minimize_spotify_windows():
    if os.name != 'nt':
        return False
    user, kernel = ctypes.windll.user32, ctypes.windll.kernel32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows.restype = wintypes.BOOL
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsIconic.argtypes = [wintypes.HWND]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    return _minimize_spotify_windows(user, kernel, callback_type)


def minimize_after_launch():
    # Spotify can create its window several seconds after protocol activation.
    # Its main window can restore itself after the first successful minimize.
    # Watch only the initial startup period, then allow normal manual reopening.
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            minimize_spotify_windows()
        except OSError:
            return
        time.sleep(0.5)


def process_running():
    if os.name != 'nt':
        return None
    try:
        result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq Spotify.exe', '/FO', 'CSV', '/NH'],
                                capture_output=True, text=True, errors='replace', timeout=3,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            return None
        return any(line.split(',')[0].strip('"').casefold() == 'spotify.exe'
                   for line in result.stdout.splitlines())
    except (OSError, subprocess.TimeoutExpired):
        return None


def ensure_running():
    """Open the installed Spotify app using its Windows protocol, once per launch."""
    global _last_launch
    with _launch_lock:
        if check(force=True).get('session_available') or process_running() is True:
            return
        if _last_launch is not None and time.monotonic() - _last_launch < 15:
            return
        try:
            os.startfile('spotify:', show_cmd=7)  # SW_SHOWMINNOACTIVE: minimized without taking focus.
        except (OSError, AttributeError):
            raise SpotifyControlError('Spotify could not be opened. Install Spotify and sign in on the recorder PC.') from None
        _last_launch = time.monotonic()
        threading.Thread(target=minimize_after_launch, daemon=True).start()


def startup():
    try:
        ensure_running()
    except SpotifyControlError:
        pass  # The header will still show missing Spotify; recording remains available.


def spotify_sessions(sessions):
    return [session for session in sessions
            if session.source_app_user_model_id.casefold().startswith('spotify')]


def summarize_sessions(sessions, playing_status):
    spotify = spotify_sessions(sessions)
    playing = any(session.get_playback_info().playback_status == playing_status for session in spotify)
    return {'playing': playing, 'available': True, 'session_available': bool(spotify), 'title': '', 'artist': '',
            'message': 'Spotify is playing on the recorder PC.' if playing else
                       'Spotify is paused or stopped. Start the background music.' if spotify else
                       'Spotify playback was not detected. Open the Spotify desktop app and start the background music.'}


async def read_windows_status():
    from winrt.runtime import ApartmentType, init_apartment, uninit_apartment
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus,
    )
    init_apartment(ApartmentType.MULTI_THREADED)
    try:
        manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
        return await session_status(manager.get_sessions(), GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING)
    finally:
        uninit_apartment()


async def session_status(sessions, playing_status):
    sessions = list(sessions)
    result = summarize_sessions(sessions, playing_status)
    matches = spotify_sessions(sessions)
    if matches:
        selected = next((session for session in matches if session.get_playback_info().playback_status == playing_status), matches[0])
        try:
            properties = await asyncio.wait_for(selected.try_get_media_properties_async(), timeout=0.75)
            result['title'] = (properties.title or '')[:500]
            result['artist'] = (properties.artist or '')[:500]
        except Exception:
            pass  # Track metadata failure must not turn a valid playback check into a warning.
    return result


async def control_session(sessions, action, playing_status):
    matches = spotify_sessions(sessions)
    if not matches:
        raise SpotifyControlError('Open the Spotify desktop app and select music before using Play or Pause.')
    selected = next((session for session in matches if session.get_playback_info().playback_status == playing_status), matches[0])
    if action == 'restart':
        try:
            restarted = await selected.try_change_playback_position_async(0)
        except Exception:
            restarted = False
        played = await selected.try_play_async()
        if not played:
            raise SpotifyControlError('Spotify did not start playback. Check the Spotify app.')
        if not restarted:
            raise SpotifyControlError('Spotify is playing, but could not restart the current song from the beginning. Check Spotify.')
        return
    method = {'play':'try_play_async', 'pause':'try_pause_async',
              'previous':'try_skip_previous_async', 'next':'try_skip_next_async'}[action]
    accepted = await getattr(selected, method)()
    if not accepted:
        raise SpotifyControlError('Spotify did not accept the request. Use the controls in Spotify and try again.')


async def windows_control(action):
    from winrt.runtime import ApartmentType, init_apartment, uninit_apartment
    from winrt.windows.media.control import (GlobalSystemMediaTransportControlsSessionManager,
                                           GlobalSystemMediaTransportControlsSessionPlaybackStatus)
    init_apartment(ApartmentType.MULTI_THREADED)
    try:
        manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
        await control_session(manager.get_sessions(), action, GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING)
    finally:
        uninit_apartment()


def control(action):
    global _checked
    if action not in ('play', 'pause', 'previous', 'next', 'restart'):
        raise ValueError('Invalid Spotify control')
    with _lock:
        try:
            if os.name != 'nt':
                raise OSError()
            asyncio.run(asyncio.wait_for(windows_control(action), timeout=3))
        except SpotifyControlError:
            raise
        except Exception:
            raise SpotifyControlError('Spotify could not be controlled. Check the desktop app on the recorder PC.') from None
        finally:
            _checked = None
    return {'message': 'Spotify ' + action + ' requested.'}


def check(force=False):
    global _checked, _cached
    with _lock:
        if not force and _checked is not None and time.monotonic() - _checked < 5:
            return dict(_cached)
        try:
            if os.name != 'nt':
                raise OSError()
            result = asyncio.run(asyncio.wait_for(read_windows_status(), timeout=2))
        except Exception:
            result = {'playing': None, 'available': False, 'session_available': False, 'title': '', 'artist': '',
                      'message': 'Spotify playback could not be checked. Confirm background music is playing on the recorder PC.'}
        _cached, _checked = result, time.monotonic()
        return dict(result)


def warning(status):
    return '' if status['playing'] is True else status['message']+' Recording is still allowed.'


def prepare_for_recording():
    """Open Spotify if needed, then keep it paused until the intro ends."""
    status = check(force=True)
    try:
        if not status.get('session_available', True):
            ensure_running()
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                status = check(force=True)
                if status.get('session_available'):
                    break
                time.sleep(0.5)
        if status['playing'] is not False:
            control('pause')
    except SpotifyControlError as error:
        return str(error) + ' Recording is still allowed.'
    return ''


def restart_for_recording():
    try:
        control('restart')
    except SpotifyControlError as error:
        return str(error) + ' Recording is still allowed.'
    return ''


def pause_after_recording():
    if check(force=True)['playing'] is False:
        return ''
    try:
        control('pause')
    except SpotifyControlError:
        return 'Spotify could not be paused automatically. Pause it in Spotify.'
    return ''
