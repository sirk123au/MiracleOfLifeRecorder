import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import spotify_status


def session(app, status):
    return SimpleNamespace(source_app_user_model_id=app,
                           get_playback_info=lambda: SimpleNamespace(playback_status=status))


class SpotifyStatus(unittest.TestCase):
    def test_restart_seeks_current_song_to_zero_then_plays_without_skipping(self):
        spotify=session('Spotify.exe',4)
        order=[]
        spotify.try_change_playback_position_async=AsyncMock(side_effect=lambda position:(order.append(('seek',position)) or True))
        spotify.try_play_async=AsyncMock(side_effect=lambda:(order.append(('play',)) or True))
        asyncio.run(spotify_status.control_session([session('chrome.exe',4),spotify],'restart',4))
        self.assertEqual(order,[('seek',0),('play',)])

    def test_rejected_seek_still_starts_music_and_reports_incomplete_restart(self):
        spotify=session('Spotify.exe',5)
        spotify.try_change_playback_position_async=AsyncMock(return_value=False)
        spotify.try_play_async=AsyncMock(return_value=True)
        with self.assertRaisesRegex(spotify_status.SpotifyControlError,'could not restart'):
            asyncio.run(spotify_status.control_session([spotify],'restart',4))
        spotify.try_play_async.assert_awaited_once()

    def test_minimizes_only_windows_owned_by_spotify(self):
        user,kernel=Mock(),Mock()
        user.EnumWindows.side_effect=lambda callback,_: [callback(window,0) for window in (10,20)]
        user.IsWindowVisible.return_value=True
        user.IsIconic.return_value=False
        user.GetWindowThreadProcessId.side_effect=lambda window,pid: setattr(pid._obj,'value',window)
        kernel.OpenProcess.side_effect=lambda access,inherit,pid: pid
        def path(process,flags,buffer,length):
            buffer.value='C:\\Apps\\'+('Spotify.exe' if process==10 else 'chrome.exe')
            return True
        kernel.QueryFullProcessImageNameW.side_effect=path
        self.assertTrue(spotify_status._minimize_spotify_windows(user,kernel,lambda callback:callback))
        user.ShowWindowAsync.assert_called_once_with(10,7)
        self.assertEqual(kernel.CloseHandle.call_count,2)

    def test_minimize_keeps_watching_after_success_until_startup_deadline(self):
        with patch.object(spotify_status,'minimize_spotify_windows',side_effect=[False,True,True,False]) as minimize, patch.object(spotify_status.time,'monotonic',side_effect=[0,0,0.5,3,19.5,20]), patch.object(spotify_status.time,'sleep') as sleep:
            spotify_status.minimize_after_launch()
            self.assertEqual(minimize.call_count,4)
            self.assertEqual(sleep.call_count,4)

    def test_minimized_windows_are_left_alone(self):
        user,kernel=Mock(),Mock()
        user.EnumWindows.side_effect=lambda callback,_: callback(10,0)
        user.IsWindowVisible.return_value=True
        user.IsIconic.return_value=True
        self.assertFalse(spotify_status._minimize_spotify_windows(user,kernel,lambda callback:callback))
        user.ShowWindowAsync.assert_not_called()
        kernel.OpenProcess.assert_not_called()

    def test_previous_and_next_target_only_spotify(self):
        spotify=session('Spotify.exe',4)
        spotify.try_skip_next_async=AsyncMock(return_value=True)
        spotify.try_skip_previous_async=AsyncMock(return_value=True)
        for action in ('previous','next'):
            asyncio.run(spotify_status.control_session([session('chrome.exe',4),spotify],action,4))
        spotify.try_skip_next_async.assert_awaited_once()
        spotify.try_skip_previous_async.assert_awaited_once()

    def test_launches_missing_spotify_once_without_reopening_running_app(self):
        with patch.object(spotify_status, '_last_launch', None), patch.object(spotify_status, 'check', return_value={'session_available':False}), patch.object(spotify_status, 'process_running', return_value=False), patch.object(spotify_status.os, 'startfile', create=True) as launch, patch.object(spotify_status.threading, 'Thread') as worker:
            spotify_status.ensure_running()
            spotify_status.ensure_running()
            launch.assert_called_once_with('spotify:', show_cmd=7)
            worker.assert_called_once_with(target=spotify_status.minimize_after_launch, daemon=True)
        with patch.object(spotify_status, 'check', return_value={'session_available':False}), patch.object(spotify_status, 'process_running', return_value=True), patch.object(spotify_status.os, 'startfile', create=True) as launch, patch.object(spotify_status.threading, 'Thread') as worker:
            spotify_status.ensure_running()
            launch.assert_not_called()

    def test_missing_installation_has_clear_error(self):
        with patch.object(spotify_status, '_last_launch', None), patch.object(spotify_status, 'check', return_value={}), patch.object(spotify_status, 'process_running', return_value=False), patch.object(spotify_status.os, 'startfile', create=True, side_effect=OSError('private path')):
            with self.assertRaisesRegex(spotify_status.SpotifyControlError, 'Install Spotify'):
                spotify_status.ensure_running()

    def test_record_opens_spotify_without_starting_playback(self):
        statuses = [{'playing':False,'session_available':False}, {'playing':False,'session_available':True}]
        with patch.object(spotify_status, 'check', side_effect=statuses), patch.object(spotify_status, 'ensure_running') as launch, patch.object(spotify_status, 'control') as control:
            self.assertEqual(spotify_status.prepare_for_recording(), '')
            launch.assert_called_once()
            control.assert_not_called()

    def test_record_keeps_paused_music_paused_and_pauses_playing_music(self):
        with patch.object(spotify_status, 'check', return_value={'playing':False}), patch.object(spotify_status, 'control') as control:
            self.assertEqual(spotify_status.prepare_for_recording(), '')
            control.assert_not_called()
        with patch.object(spotify_status, 'check', return_value={'playing':True}), patch.object(spotify_status, 'control') as control:
            self.assertEqual(spotify_status.prepare_for_recording(), '')
            control.assert_called_once_with('pause')

    def test_record_music_failure_returns_warning(self):
        with patch.object(spotify_status, 'check', return_value={'playing':None}), patch.object(spotify_status, 'control', side_effect=spotify_status.SpotifyControlError('Open Spotify.')):
            self.assertIn('Recording is still allowed', spotify_status.prepare_for_recording())

    def test_track_and_artist_are_read_from_spotify_only(self):
        other = session('chrome.exe', 4)
        spotify = session('Spotify.exe', 4)
        spotify.try_get_media_properties_async = AsyncMock(return_value=SimpleNamespace(title='Example track',artist='Example artist'))
        result = asyncio.run(spotify_status.session_status([other,spotify],4))
        self.assertEqual(result['title'], 'Example track')
        self.assertEqual(result['artist'], 'Example artist')
        self.assertTrue(result['session_available'])

    def test_metadata_failure_keeps_playback_status(self):
        spotify = session('Spotify.exe',4)
        spotify.try_get_media_properties_async = AsyncMock(side_effect=OSError())
        result = asyncio.run(spotify_status.session_status([spotify],4))
        self.assertTrue(result['playing'])
        self.assertEqual(result['title'], '')

    def test_play_pause_target_spotify_and_not_another_player(self):
        other = session('chrome.exe',4)
        spotify = session('Spotify.exe',5)
        spotify.try_play_async = AsyncMock(return_value=True)
        spotify.try_pause_async = AsyncMock(return_value=True)
        asyncio.run(spotify_status.control_session([other,spotify],'play',4))
        asyncio.run(spotify_status.control_session([other,spotify],'pause',4))
        spotify.try_play_async.assert_awaited_once()
        spotify.try_pause_async.assert_awaited_once()

    def test_missing_spotify_or_rejected_play_request_is_explained(self):
        with self.assertRaises(spotify_status.SpotifyControlError):
            asyncio.run(spotify_status.control_session([session('chrome.exe',4)],'play',4))
        spotify = session('Spotify.exe',5)
        spotify.try_play_async = AsyncMock(return_value=False)
        with self.assertRaises(spotify_status.SpotifyControlError):
            asyncio.run(spotify_status.control_session([spotify],'play',4))
    def test_playing_spotify_is_detected_among_other_media_apps(self):
        result = spotify_status.summarize_sessions([session('chrome.exe', 4), session('Spotify.exe', 4)], 4)
        self.assertTrue(result['playing'])
        self.assertEqual(spotify_status.warning(result), '')

    def test_other_music_player_does_not_count_as_spotify(self):
        result = spotify_status.summarize_sessions([session('chrome.exe', 4)], 4)
        self.assertFalse(result['playing'])
        self.assertIn('not detected', result['message'])

    def test_paused_store_spotify_warns(self):
        result = spotify_status.summarize_sessions([session('SpotifyAB.SpotifyMusic_package!Spotify', 5)], 4)
        self.assertFalse(result['playing'])
        self.assertIn('paused', result['message'])
        self.assertIn('Recording is still allowed', spotify_status.warning(result))

    def test_status_errors_are_private_and_do_not_prohibit_recording(self):
        with patch.object(spotify_status, '_checked', None), patch.object(spotify_status, '_cached', None), \
             patch.object(spotify_status, 'read_windows_status', AsyncMock(side_effect=OSError('private data'))):
            result = spotify_status.check()
        self.assertIsNone(result['playing'])
        self.assertFalse(result['available'])
        self.assertNotIn('private data', result['message'])
        self.assertIn('Recording is still allowed', spotify_status.warning(result))

    def test_repeated_checks_reuse_short_lived_cache(self):
        result = {'playing': True, 'available': True, 'message': 'Spotify is playing.'}
        with patch.object(spotify_status, '_checked', None), patch.object(spotify_status, '_cached', None), \
             patch.object(spotify_status, 'read_windows_status', AsyncMock(return_value=result)) as read:
            self.assertTrue(spotify_status.check()['playing'])
            self.assertTrue(spotify_status.check()['playing'])
            read.assert_awaited_once()
