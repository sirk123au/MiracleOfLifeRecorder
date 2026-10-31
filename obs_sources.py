"""OBS input choices; audio-only capture inputs never become video layers."""
AUDIO_KINDS = {'wasapi_input_capture', 'wasapi_output_capture', 'wasapi_process_output_capture',
               'pulse_input_capture', 'pulse_output_capture', 'coreaudio_input_capture', 'coreaudio_output_capture'}


def resolve(name, inputs):
    if name in inputs:
        return name
    matches = [candidate for candidate in inputs if candidate.casefold() == name.casefold()]
    return matches[0] if len(matches) == 1 else None


def choices(inputs):
    video, audio = [], []
    for item in inputs:
        name, kind = item['inputName'], item.get('inputKind', '')
        if name.startswith('MOL Recorder '):
            continue
        (audio if kind in AUDIO_KINDS else video).append(name)
    return {'video': sorted(video, key=str.casefold), 'audio': sorted(audio, key=str.casefold)}
