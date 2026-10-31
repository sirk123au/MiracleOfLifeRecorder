"""Generate PyInstaller's Windows version resource from the release version."""
from pathlib import Path
import runpy

root = Path(__file__).resolve().parent.parent
version = runpy.run_path(str(root / 'version.py'))['__version__']
parts = tuple(int(part) for part in version.split('.'))
if len(parts) != 3 or any(part < 0 or part > 65535 for part in parts):
    raise ValueError('Version must contain three integers from 0 to 65535')
full = (*parts, 0)
output = root / 'build' / 'windows_version_info.txt'
output.parent.mkdir(exist_ok=True)
output.write_text(f'''VSVersionInfo(
  ffi=FixedFileInfo(filevers={full!r}, prodvers={full!r}, mask=0x3f,
    flags=0, OS=0x40004, fileType=0x1, subtype=0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Miracle of Life'),
    StringStruct('FileDescription', 'Miracle of Life Ultrasound Recorder'),
    StringStruct('FileVersion', '{version}'),
    StringStruct('ProductName', 'Miracle of Life Ultrasound Recorder'),
    StringStruct('ProductVersion', '{version}'),
    StringStruct('OriginalFilename', 'MiracleOfLifeRecorder.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
''', encoding='utf-8')
