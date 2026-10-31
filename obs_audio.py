"""Choose one background-audio capture route and restore prior mixer settings."""
import obs_sources


class AudioError(Exception):
    pass


def is_spotify(settings):
    return str(settings.get('window', '')).rsplit(':', 1)[-1].casefold() == 'spotify.exe'


def plan(client, settings, scene, nested_audio=None):
    inputs = {item['inputName']: item['inputKind'] for item in client.get_input_list().inputs}
    specials = client.send('GetSpecialInputs', raw=True)
    globals_ = {name for name in specials.values() if isinstance(name, str) and name}
    prefix = 'MOL Recorder ' + settings['branding_key']
    selected = None
    already_added = False
    route = 'System output'
    warning = ''
    requested = settings.get('audio_source', '')
    video_name = obs_sources.resolve(settings.get('video_source', 'blackmagic'), inputs)
    if requested:
        selected = video_name if requested == '@video' else obs_sources.resolve(requested, inputs)
        if selected is None or (requested != '@video' and inputs[selected] not in obs_sources.AUDIO_KINDS):
            raise AudioError('The selected audio capture source is unavailable. Select an OBS audio input in Settings.')
        if requested == '@video':
            if inputs[selected] not in ('dshow_input','decklink-input','ffmpeg_source','browser_source','vlc_source'):
                raise AudioError('This video source does not provide a supported audio output. Choose an audio capture source in Settings.')
            already_added = True  # The video source is already in the recording scene.
        route = selected
    if selected is None and settings.get('music_capture', 'spotify') == 'spotify':
        try:
            for name, kind in inputs.items():
                if kind == 'wasapi_process_output_capture' and is_spotify(client.get_input_settings(name).input_settings):
                    selected = name
                    break
            if selected is None:
                kinds = client.get_input_kind_list(unversioned=True).input_kinds
                if 'wasapi_process_output_capture' in kinds:
                    name = prefix + ' Spotify audio'
                    if name not in inputs:
                        client.create_input(scene, name, 'wasapi_process_output_capture', {'window': '', 'priority': 2}, False)
                        already_added = True
                    windows = client.get_input_properties_list_property_items(name, 'window').property_items
                    target = next((item['itemValue'] for item in windows
                                   if item.get('itemEnabled', True) and is_spotify({'window': item['itemValue']})), None)
                    if target:
                        client.set_input_settings(name, {'window': target, 'priority': 2}, True)
                        selected = name
                        if already_added:
                            client.set_scene_item_enabled(scene, client.get_scene_item_id(scene, name).scene_item_id, True)
                if selected is None:
                    warning = 'Spotify direct capture is unavailable; using system output audio instead.'
            if selected is not None:
                route = 'Spotify direct'
        except Exception:
            selected=None
            already_added=False
            warning='Spotify direct capture could not be prepared; using system output audio instead.'
    if selected is None:
        already_added = False
        # Prefer an existing default-device global Desktop Audio input.
        selected = next((name for name in globals_ if inputs.get(name) == 'wasapi_output_capture'
                         and client.get_input_settings(name).input_settings.get('device_id', 'default') == 'default'), None)
        if selected is None:
            selected = prefix + ' System audio'
            if selected not in inputs:
                client.create_input(scene, selected, 'wasapi_output_capture', {'device_id': 'default'}, True)
                already_added = True
            elif inputs[selected] != 'wasapi_output_capture':
                raise AudioError('The recorder system audio source has an incompatible type. Choose an OBS audio source in Settings.')
    if selected not in globals_ and not already_added and selected not in (nested_audio or ()):
        client.create_scene_item(scene, selected, True)
    competing_kinds = obs_sources.AUDIO_KINDS if requested else {'wasapi_output_capture','wasapi_process_output_capture'}
    mute = [name for name in inputs if name != selected and not name.startswith(prefix + ' ') and inputs.get(name) in competing_kinds]
    if requested:
        for name, kind in inputs.items():
            if name != selected and not name.startswith(prefix + ' ') and kind in ('dshow_input','decklink-input','ffmpeg_source','browser_source','vlc_source'):
                mute.append(name)
        if video_name is not None and video_name != selected and video_name not in mute and inputs[video_name] in ('dshow_input','decklink-input','ffmpeg_source','browser_source','vlc_source'):
            mute.append(video_name)
    snapshot = {}
    for name in [selected, *mute]:
        if name.startswith(prefix + ' '):
            continue  # Newly managed sources have no operator mixer state to restore.
        snapshot[name] = {'muted': client.get_input_mute(name).input_muted}
        if name == selected:
            snapshot[name]['volume'] = client.get_input_volume(name).input_volume_mul
            snapshot[name]['tracks'] = client.get_input_audio_tracks(name).input_audio_tracks
            snapshot[name]['monitor'] = client.get_input_audio_monitor_type(name).monitor_type
    application_settings = None
    if inputs.get(selected) == 'wasapi_process_output_capture':
        original = client.get_input_settings(selected).input_settings
        if is_spotify(original):
            # Spotify changes its window title for every song. Match the executable,
            # and refresh the window identifier when OBS can see the current one.
            target = original['window']
            try:
                windows = client.get_input_properties_list_property_items(selected, 'window').property_items
                target = next((item['itemValue'] for item in windows if item.get('itemEnabled', True)
                               and is_spotify({'window': item['itemValue']})), target)
            except Exception:
                pass  # Executable matching still works with the previously selected window.
            application_settings = {'window': target, 'priority': 2}
            if selected in snapshot:
                snapshot[selected]['settings'] = original
    previous_volume = snapshot.get(selected, {}).get('volume', 1.0)
    capture_volume = previous_volume if isinstance(previous_volume, (int, float)) and previous_volume > 0 else 1.0
    return {'source': selected, 'route': route, 'warning': warning, 'capture_volume': capture_volume,
            'application_settings': application_settings,
            'global_source': selected in globals_, 'video_audio': requested == '@video', 'mute': mute, 'snapshot': snapshot, 'restored': False}


def apply(client, audio, defer=False):
    if audio.get('application_settings'):
        client.set_input_settings(audio['source'], audio['application_settings'], True)
    for name in audio['mute']:
        client.set_input_mute(name, True)
    client.set_input_mute(audio['source'], defer)
    client.set_input_volume(audio['source'], vol_mul=audio.get('capture_volume', 1.0))
    client.set_input_audio_tracks(audio['source'], {str(track): True for track in range(1, 7)})
    # Monitor Only explicitly excludes audio from recordings in OBS.
    client.set_input_audio_monitor_type(audio['source'], 'OBS_MONITORING_TYPE_NONE')
    audio['restored'] = False
    audio['deferred'] = defer


def start_background(client, audio):
    if audio and audio.get('deferred'):
        client.set_input_mute(audio['source'], False)
        audio['deferred'] = False


def restore(client, audio):
    if not audio or audio.get('restored'):
        return
    failures = []
    for name, values in audio['snapshot'].items():
        try:
            client.set_input_mute(name, values['muted'])
            if 'volume' in values:
                client.set_input_volume(name, vol_mul=values['volume'])
                client.set_input_audio_tracks(name, values['tracks'])
            if 'monitor' in values:
                client.set_input_audio_monitor_type(name, values['monitor'])
            if 'settings' in values:
                client.set_input_settings(name, values['settings'], False)
        except Exception:
            failures.append(name)
    if failures:
        raise AudioError('OBS mixer settings could not be restored automatically. Check the OBS mixer.')
    audio['restored'] = True
