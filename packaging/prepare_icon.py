"""Generate the Windows ICO from the same artwork used by the tray."""
from pathlib import Path
import runpy

root = Path(__file__).resolve().parent.parent
artwork = runpy.run_path(str(root / 'app_icon.py'))['tray_icon_image']()
output = root / 'build' / 'application.ico'
output.parent.mkdir(exist_ok=True)
artwork.save(output, format='ICO', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64)])
