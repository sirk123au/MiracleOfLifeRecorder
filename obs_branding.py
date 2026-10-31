"""A recorder-owned intro scene and a live scene nesting the operator's scene."""
import time
from pathlib import Path
from PIL import Image
import obs_audio
import obs_sources
import obs_devices


class BrandingError(Exception):
    pass


VIDEO_EXTENSIONS = {'.mp4', '.mov', '.webm', '.mkv'}
MAX_VIDEO_SECONDS = 60


def rewind_video(client, name):
    client.trigger_media_input_action(name, 'OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART')
    client.trigger_media_input_action(name, 'OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PAUSE')
    client.set_media_input_cursor(name, 0)


def wait_for_video(client, sequence):
    name = sequence['media_input']
    client.trigger_media_input_action(name, 'OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART')
    deadline = time.monotonic() + MAX_VIDEO_SECONDS + 5
    started = False
    while time.monotonic() < deadline:
        status = client.get_media_input_status(name)
        if status.media_state == 'OBS_MEDIA_STATE_PLAYING':
            started = True
        elif status.media_state == 'OBS_MEDIA_STATE_ENDED':
            if started or status.media_cursor > 0:
                return
        elif status.media_state == 'OBS_MEDIA_STATE_ERROR':
            raise BrandingError('The opening/closing video could not be played by OBS.')
        time.sleep(0.1)
    raise BrandingError('The opening/closing video did not finish. Check its format and duration in OBS.')


def fit(client, scene, item, width, height):
    client.set_scene_item_transform(scene, item, {
        'positionX': 0.0, 'positionY': 0.0, 'alignment': 5,
        'boundsType': 'OBS_BOUNDS_SCALE_INNER', 'boundsAlignment': 0,
        'boundsWidth': float(width), 'boundsHeight': float(height)})


def prepare_blackmagic(client, settings, test=False):
    input_list = client.get_input_list().inputs
    inputs = {item['inputName']: item.get('inputKind', '') for item in input_list}
    requested = settings.get('video_source', 'blackmagic')
    device_mode = requested == '@device'
    if device_mode:
        try:source = obs_devices.select(client, settings, input_list)
        except ValueError as error:raise BrandingError(str(error)) from error
    else:
        source = obs_sources.resolve(requested, inputs)
    test_fallback = test and source is None and requested.casefold() == 'blackmagic'
    if source is None and not test_fallback and not device_mode:
        raise BrandingError('The selected video source is unavailable in OBS. Select a video source in Settings (default: blackmagic).')
    if source is not None and inputs[source] in obs_sources.AUDIO_KINDS:
        raise BrandingError('Choose a video input in Settings; an audio-only source cannot provide the recording picture.')
    if client.get_studio_mode_enabled().studio_mode_enabled:
        raise BrandingError('Turn off OBS Studio Mode before recording.')
    current = client.get_current_program_scene().current_program_scene_name
    original = settings.get('ultrasound_scene', '').strip() or current
    scene = 'MOL Recorder ' + settings['branding_key'] + (' Test Capture' if test else ' Capture')
    scenes = {item['sceneName'] for item in client.get_scene_list().scenes}
    if original not in scenes or original == scene:
        raise BrandingError('Select your original ultrasound scene in Settings before recording.')
    if test_fallback:
        source = original
    audio = set()
    visited = set()

    def collect_audio(name, group=False):
        if name in visited:
            return
        visited.add(name)
        items = (client.get_group_scene_item_list(name) if group else client.get_scene_item_list(name)).scene_items
        for item in items:
            if not item.get('sceneItemEnabled', True):
                continue
            name = item['sourceName']
            if inputs.get(name) in ('wasapi_input_capture', 'wasapi_output_capture', 'wasapi_process_output_capture'):
                audio.add(name)
            elif item.get('isGroup'):
                collect_audio(name, True)
            elif name in scenes and name != scene:
                collect_audio(name)
    collect_audio(original)
    previous = client.get_current_scene_transition()
    transitions = client.get_scene_transition_list().transitions
    cut = next((item['transitionName'] for item in transitions if item['transitionKind'] == 'cut_transition'), None)
    if not cut:
        raise BrandingError('Add a Cut scene transition in OBS before recording.')
    if scene not in scenes:
        client.create_scene(scene)
    for item in client.get_scene_item_list(scene).scene_items:
        client.remove_scene_item(scene, item['sceneItemId'])
    video = client.get_video_settings()
    if device_mode and source is None:
        source = 'MOL Recorder ' + settings['branding_key'] + ' Selected camera'
        item = client.create_input(scene, source, 'dshow_input',
            {'video_device_id': settings['video_device_id'], 'deactivate_when_not_showing': True}, True).scene_item_id
    else:
        item = client.create_scene_item(scene, source, True).scene_item_id
    fit(client, scene, item, video.base_width, video.base_height)
    for name in sorted(audio):
        if not test_fallback and not settings.get('audio_source') and name != source and inputs.get(name) == 'wasapi_input_capture':
            client.create_scene_item(scene, name, True)
    try:
        audio_plan = obs_audio.plan(client, {**settings, 'video_source': source}, scene, nested_audio=audio if test_fallback else None)
    except obs_audio.AudioError as error:
        raise BrandingError(str(error)) from error
    except Exception as error:
        # OBS comments can contain source names or paths; expose only protocol identifiers.
        request = getattr(error, 'req_name', '')
        code = getattr(error, 'code', None)
        detail = f' (OBS {request}, code {code})' if isinstance(request, str) and request.isalnum() and isinstance(code, int) else ''
        raise BrandingError('Could not configure background audio'+detail+'. Check OBS audio devices and the selected capture mode.') from error
    return {'capture_only': True, 'original': original, 'live': scene, 'cut': cut,
            'managed_prefix': 'MOL Recorder ' + settings['branding_key'],
            'audio': audio_plan,
            'previous_transition': previous.transition_name, 'previous_duration': previous.transition_duration}


def prepare_capture(client, settings, test=False):
    capture = prepare_blackmagic(client, settings, test)
    if not settings.get('branding_enabled'):
        return capture
    sequence = prepare(client, {**settings, 'ultrasound_scene': capture['live']})
    sequence['original'] = capture['original']
    sequence['audio'] = capture['audio']
    if not capture['audio']['global_source'] and not capture['audio'].get('video_audio'):
        client.create_scene_item(sequence['logo'], capture['audio']['source'], True)
        if sequence.get('outro'):
            client.create_scene_item(sequence['outro'], capture['audio']['source'], True)
    return sequence


def prepare(client, settings):
    if not settings.get('branding_enabled'):
        return None
    if settings.get('obs_host', '127.0.0.1').casefold() not in ('127.0.0.1', 'localhost', '::1'):
        raise BrandingError('Branding media must be loaded by OBS on this PC. Use localhost as the OBS host for the logo sequence.')
    intro = Path(settings.get('intro_image', ''))
    overlay = Path(settings.get('watermark_image', ''))
    if not intro.is_file() or not overlay.is_file():
        raise BrandingError('Choose an opening/closing image or video and a corner-logo image in Settings.')
    outro = None
    if settings.get('outro_mode', 'same') == 'image':
        outro = Path(settings.get('outro_image', ''))
        try:
            with Image.open(outro) as supplied:
                if supplied.format not in ('PNG', 'JPEG'):
                    raise ValueError()
                supplied.verify()
        except (OSError, ValueError):
            raise BrandingError('Upload a valid fixed PNG/JPEG outro image in Settings before recording.') from None
    if client.get_studio_mode_enabled().studio_mode_enabled:
        raise BrandingError('Turn off OBS Studio Mode before using the automatic logo sequence.')
    prefix = 'MOL Recorder ' + settings['branding_key']
    logo_scene, live_scene = prefix + ' Logo', prefix + ' Live'
    outro_scene = prefix + ' Outro' if outro else None
    current = client.get_current_program_scene().current_program_scene_name
    original = settings.get('ultrasound_scene', '').strip() or current
    if original in (logo_scene, live_scene, outro_scene):
        raise BrandingError('Select the original ultrasound scene in Settings or OBS before starting.')
    scenes = {item['sceneName'] for item in client.get_scene_list().scenes}
    if original not in scenes:
        raise BrandingError('The ultrasound scene selected in Settings is not available in OBS.')
    # Verify that the built-in fade exists before making any scene changes.
    transitions = client.get_scene_transition_list().transitions
    fade = next((item['transitionName'] for item in transitions if item['transitionKind'] == 'fade_transition'), None)
    if not fade:
        raise BrandingError('Add a Fade scene transition in OBS before recording.')
    previous = client.get_current_scene_transition()
    video = client.get_video_settings()
    width, height = video.base_width, video.base_height
    managed_scenes = [logo_scene, live_scene] + ([outro_scene] if outro_scene else [])
    for scene in managed_scenes:
        if scene not in scenes:
            client.create_scene(scene)
    # Rebuild only app-owned scene items; operator scene contents are untouched.
    for scene in managed_scenes:
        for item in client.get_scene_item_list(scene).scene_items:
            client.remove_scene_item(scene, item['sceneItemId'])
    inputs = {item['inputName'] for item in client.get_input_list().inputs}

    def image(scene, name, path):
        if name in inputs:
            client.set_input_settings(name, {'file': str(path)}, True)
            item = client.create_scene_item(scene, name, True).scene_item_id
        else:
            item = client.create_input(scene, name, 'image_source', {'file': str(path)}, True).scene_item_id
        fit(client, scene, item, width, height)
        return name

    # Full-frame supplied overlay retains its original corner-logo placement.
    background = prefix + ' Background'
    if background in inputs:
        client.set_input_settings(background, {'color': 0xFFFFFFFF, 'width': width, 'height': height}, True)
        client.create_scene_item(logo_scene, background, True)
    else:
        client.create_input(logo_scene, background, 'color_source_v3',
                            {'color': 0xFFFFFFFF, 'width': width, 'height': height}, True)
    media_input = None
    if intro.suffix.lower() in VIDEO_EXTENSIONS:
        media_input = prefix + ' Intro video'
        media_settings = {'is_local_file': True, 'local_file': str(intro), 'looping': False,
                          'restart_on_activate': False, 'close_when_inactive': False, 'clear_on_media_end': False}
        if media_input in inputs:
            client.set_input_settings(media_input, media_settings, True)
            item = client.create_scene_item(logo_scene, media_input, True).scene_item_id
        else:
            item = client.create_input(logo_scene, media_input, 'ffmpeg_source', media_settings, True).scene_item_id
        fit(client, logo_scene, item, width, height)
        rewind_video(client, media_input)
        duration = 0
        for _ in range(20):
            status = client.get_media_input_status(media_input)
            if status.media_state == 'OBS_MEDIA_STATE_ERROR':
                raise BrandingError('OBS could not load this opening/closing video. Try an H.264 MP4 file.')
            duration = status.media_duration
            if isinstance(duration, (int, float)) and duration > 0:
                break
            time.sleep(0.1)
        if not isinstance(duration, (int, float)) or not 0 < duration <= MAX_VIDEO_SECONDS * 1000:
            raise BrandingError('Use a playable opening/closing video up to 60 seconds long. H.264 MP4 is recommended.')
    else:
        image(logo_scene, prefix + ' Intro image', intro)
    if outro_scene:
        client.create_scene_item(outro_scene, background, True)
        image(outro_scene, prefix + ' Outro image', outro)
    nested = client.create_scene_item(live_scene, original, True).scene_item_id
    fit(client, live_scene, nested, width, height)
    name = image(live_scene, prefix + ' Video logo', overlay)
    # Black background can be removed by OBS without altering the supplied file.
    with Image.open(overlay) as source:
        sample = source.convert('RGBA')
        corners = [sample.getpixel(point) for point in ((0, 0), (sample.width-1, 0), (0, sample.height-1), (sample.width-1, sample.height-1))]
        black = all(max(pixel[:3]) < 20 and pixel[3] > 240 for pixel in corners)
    filters = {item['filterName'] for item in client.get_source_filter_list(name).filters}
    filter_name = 'MOL black background'
    if black and filter_name not in filters:
        client.create_source_filter(name, filter_name, 'color_key_filter_v2',
                                    {'key_color_type': 'custom', 'key_color': 0, 'similarity': 50, 'smoothness': 50, 'opacity': 1.0})
    if filter_name in filters or black:
        client.set_source_filter_enabled(name, filter_name, black)
    opacity_filter = 'MOL overlay fade'
    if opacity_filter not in filters:
        client.create_source_filter(name, opacity_filter, 'color_filter_v2', {'opacity': 0.0})
    else:
        client.set_source_filter_settings(name, opacity_filter, {'opacity': 0.0}, True)
        client.set_source_filter_enabled(name, opacity_filter, True)
    return {'logo': logo_scene, 'live': live_scene, 'original': original, 'fade': fade,
            'overlay_source': name, 'overlay_filter': opacity_filter,
            'managed_prefix': prefix,
            'outro': outro_scene,
            'media_input': media_input,
            'previous_transition': previous.transition_name, 'previous_duration': previous.transition_duration}


def show_intro(client, sequence):
    if sequence.get('capture_only'):
        client.set_current_scene_transition(sequence['cut'])
        client.set_current_program_scene(sequence['live'])
        time.sleep(0.1)
        return
    # Select the opening frame before OBS starts writing the recording.
    client.set_current_scene_transition(sequence['fade'])
    client.set_current_scene_transition_duration(1000)
    if sequence.get('media_input'):
        rewind_video(client, sequence['media_input'])
    client.set_current_program_scene(sequence['logo'])
    time.sleep(1)


def enter_live(client, sequence, before_background=None):
    if sequence.get('capture_only'):
        if before_background:before_background()
        obs_audio.start_background(client, sequence.get('audio'))
        return
    if sequence.get('media_input'):
        wait_for_video(client, sequence)
    else:
        time.sleep(3)
    client.set_current_scene_transition(sequence['fade'])
    client.set_current_scene_transition_duration(1000)
    client.set_current_program_scene(sequence['live'])
    time.sleep(1)
    overlay = sequence.get('overlay_source')
    audio = sequence.get('audio')
    if overlay:
        fading_audio = audio and audio.get('deferred')
        if fading_audio:
            client.set_input_volume(audio['source'], vol_mul=0.0)
        if before_background:before_background()
        obs_audio.start_background(client, audio)
        # Fade together over one second once the intro transition has finished.
        for step in range(1, 21):
            time.sleep(0.05)
            fraction = step / 20
            client.set_source_filter_settings(overlay, sequence['overlay_filter'], {'opacity': fraction}, True)
            if fading_audio:
                client.set_input_volume(audio['source'], vol_mul=audio.get('capture_volume', 1.0) * fraction)
        return
    if before_background:before_background()
    obs_audio.start_background(client, sequence.get('audio'))


def show_outro(client, sequence):
    if sequence.get('capture_only'):
        return
    client.set_current_scene_transition(sequence['fade'])
    client.set_current_scene_transition_duration(1000)
    if sequence.get('outro'):
        client.set_current_program_scene(sequence['outro'])
        time.sleep(4)  # One second of fading plus three seconds on the fixed image.
        return
    if sequence.get('media_input'):
        rewind_video(client, sequence['media_input'])
    client.set_current_program_scene(sequence['logo'])
    if sequence.get('media_input'):
        time.sleep(1)
        wait_for_video(client, sequence)
    else:
        time.sleep(4)  # One second of fading, then three seconds on the logo.


def restore(client, sequence):
    try:
        client.set_current_program_scene(sequence['original'])
        time.sleep(1)
        client.set_current_scene_transition(sequence['previous_transition'])
        duration = sequence['previous_duration']
        if isinstance(duration, (int, float)) and duration >= 50:
            client.set_current_scene_transition_duration(duration)
    finally:
        obs_audio.restore(client, sequence.get('audio'))
    cleanup_scenes(client, sequence)


def cleanup_scenes(client, sequence):
    """Remove only this installation's known temporary scenes after restoring OBS."""
    prefix = sequence.get('managed_prefix')
    if not prefix or not prefix.startswith('MOL Recorder '):
        return  # Older recovery records do not establish ownership.
    owned = {prefix + suffix for suffix in (' Logo', ' Live', ' Outro', ' Capture', ' Test Capture')}
    owned.discard(sequence['original'])
    current = client.get_current_program_scene().current_program_scene_name
    if current in owned:
        raise BrandingError('Temporary OBS scenes could not be removed while still on air. Switch to your original scene.')
    existing = {item['sceneName'] for item in client.get_scene_list().scenes}
    # Remove Live before Capture, which it can reference.
    for suffix in (' Live', ' Logo', ' Outro', ' Capture', ' Test Capture'):
        name = prefix + suffix
        if name in owned and name in existing:
            client.remove_scene(name)
