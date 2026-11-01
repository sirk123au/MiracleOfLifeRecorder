"""Check the complete installed runtime without launching OBS, Spotify or the app."""
from pathlib import Path
import os
import subprocess
import sys
from PyInstaller.archive.readers import CArchiveReader

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from version import __version__


def verify():
    bundle=ROOT/'dist'/'MiracleOfLifeRecorder'
    executable=bundle/'MiracleOfLifeRecorder.exe'
    runtime=bundle/'_internal'
    if not executable.is_file() or not runtime.is_dir():raise RuntimeError('The complete one-folder application bundle is missing.')
    for name in ('base_library.zip',f'python{sys.version_info.major}{sys.version_info.minor}.dll','_ssl.pyd'):
        if not (runtime/name).is_file():raise RuntimeError('Required runtime file missing: '+name)
    archive=CArchiveReader(str(executable))
    if any(value[-1] in ('b','x','Z') for value in archive.toc.values()):
        raise RuntimeError('The EXE contains extractable runtime files; a one-folder build is required.')
    pyz=archive.open_embedded_archive(next(key for key in archive.toc if key.endswith('.pyz')))
    required=('app_updates','update_install','gmail_share','family_copy','secure_store','patient_folders','obs_branding','spotify_status')
    if any(key not in pyz.toc for key in required):raise RuntimeError('The bundle is missing an application module.')
    if __version__ not in pyz.extract('version').co_consts:raise RuntimeError('Bundle version does not match the release source.')
    profile=ROOT/'build'/'runtime-check-profile';profile.mkdir(parents=True,exist_ok=True)
    result=subprocess.run([str(executable),'--check-runtime'],cwd=str(bundle),
                          env={**os.environ,'APPDATA':str(profile)},timeout=45,
                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:raise RuntimeError('The packaged runtime could not start in self-check mode (exit '+str(result.returncode)+').')
    print('Complete runtime bundle verified: version '+__version__+'; no self-extracted runtime files.')


if __name__=='__main__':verify()
