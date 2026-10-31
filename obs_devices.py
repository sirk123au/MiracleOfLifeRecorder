"""Enumerate actual DirectShow devices using OBS's capture-device property list."""
import secrets


def list_devices(client, settings, inputs=None):
    if inputs is None:
        inputs = client.get_input_list().inputs
    capture = next((item['inputName'] for item in inputs if item.get('inputKind') == 'dshow_input'), None)
    probe_scene = None
    try:
        if capture is None:
            prefix = 'MOL Recorder ' + settings['branding_key'] + ' Discover ' + secrets.token_hex(4)
            probe_scene, capture = prefix, prefix + ' input'
            client.create_scene(probe_scene)
            client.create_input(probe_scene, capture, 'dshow_input',
                                {'video_device_id': '', 'deactivate_when_not_showing': True}, False)
        items = client.get_input_properties_list_property_items(capture, 'video_device_id').property_items
        devices = {}
        for item in items:
            value = item.get('itemValue')
            if item.get('itemEnabled', True) and isinstance(value, str) and value:
                devices[value] = {'id': value, 'name': item.get('itemName') or 'Video capture device'}
        return sorted(devices.values(), key=lambda item: item['name'].casefold())
    finally:
        if probe_scene is not None:
            client.remove_scene(probe_scene)


def select(client, settings, inputs):
    requested = settings.get('video_device_id', '')
    devices = list_devices(client, settings, inputs)
    if not requested or not any(device['id'] == requested for device in devices):
        raise ValueError('The selected capture device is disconnected or unavailable. Refresh the device list and select it again.')
    for item in inputs:
        name = item['inputName']
        if item.get('inputKind') == 'dshow_input' and not name.startswith('MOL Recorder '):
            configured = client.get_input_settings(name).input_settings
            if configured.get('video_device_id') == requested:
                return name  # Reuse the configured source instead of opening the camera twice.
    return None
