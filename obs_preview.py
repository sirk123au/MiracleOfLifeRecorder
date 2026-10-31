"""Bounded OBS snapshot preview with one reusable connection and no frame backlog."""
import base64
import threading
import time

MODES = {'low': (960, 540, 2), 'hd': (1280, 720, 10), 'full': (1920, 1080, 15)}


class Preview:
    def __init__(self):
        self.lock = threading.Lock()
        self.connection = None
        self.identity = None
        self.frame = None
        self.mode = None
        self.checked = 0

    def capture(self, factory, identity, mode):
        if mode not in MODES:
            raise ValueError('Invalid preview mode')
        width, height, fps = MODES[mode]
        with self.lock:
            try:
                if self.identity != identity:
                    self.close()
                    self.identity = identity
                if self.frame is not None and self.mode == mode and time.monotonic()-self.checked < 1/fps:
                    return self.frame
                if self.connection is None:
                    self.connection = factory()
                scene = self.connection.get_current_program_scene().current_program_scene_name
                result = self.connection.get_source_screenshot(scene, 'jpg', width, height, 80)
                frame = base64.b64decode(result.image_data.split(',', 1)[1], validate=True)
                self.frame, self.mode, self.checked = frame, mode, time.monotonic()
                return frame
            except Exception:
                self.close()
                raise

    def close(self):
        if self.connection is not None:
            try:
                self.connection.disconnect()
            except Exception:
                pass
        self.connection = None
        self.frame = None
        self.mode = None
