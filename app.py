import ctypes, json, os, re, shutil, threading, time, webbrowser, base64, secrets
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, render_template_string, Response, abort, make_response, send_file
import obsws_python as obs
import pystray
from app_icon import tray_icon_image
import patient_store
import recording_recovery
import storage_migration
import obs_branding
import obs_audio
import obs_preview
import recording_health
import usb_readiness
import usb_devices
import obs_sources
import obs_devices
import single_instance
import spotify_status
from version import __version__
import socket
import subprocess
import tempfile
import zipfile
import hashlib
from urllib.parse import urlsplit
from PIL import Image, ImageDraw

ROOT=Path(os.environ.get('APPDATA',str(Path.home()))) / 'OBS-Ultrasound-Web'
ROOT.mkdir(parents=True,exist_ok=True)
CFG=ROOT/'settings.json'
RECOVERY_POINTER=ROOT/'pending-session.json'
DEFAULT={'volume_label':'ULTRASOUND','recording_volume':'','recording_drive_name':'','performance_profile':'high','obs_host':'127.0.0.1','obs_port':5555,'obs_password':'','min_free_gb':5,'video_source':'blackmagic','video_device_id':'','video_device_name':'','audio_source':'','music_capture':'spotify','branding_enabled':False,'ultrasound_scene':'','intro_image':'','outro_mode':'same','outro_image':'','watermark_image':'','branding_key':secrets.token_hex(4)}
try: settings={**DEFAULT,**{k:v for k,v in json.loads(CFG.read_text()).items() if k in DEFAULT}}
except Exception: settings=DEFAULT.copy()
app=Flask(__name__)
BIND_HOST=os.environ.get('RECORDER_BIND_HOST','127.0.0.1')
NETWORK_MODE=BIND_HOST not in ('127.0.0.1','::1','localhost')
NETWORK_PASSWORD=os.environ.get('RECORDER_TEST_PASSWORD','')
ALLOWED_HOSTS={'127.0.0.1','localhost','::1',BIND_HOST.casefold()}|{
    host.strip().casefold() for host in os.environ.get('RECORDER_ALLOWED_HOSTS','').split(',') if host.strip()}
TOKEN=secrets.token_urlsafe(32)
lock=threading.RLock()
state={'active':False,'patient':None,'test':False,'started':None,'destination':None,'staging':None,'message':'Ready','hotkey':0,'pending':None}
playback_files={}
recording_versions={}
obs_launch=None
family_destinations={}
family_copy_lock=threading.Lock()
storage_lock=threading.RLock()

def volume_id(root):
    serial=ctypes.c_ulong()
    if os.name!='nt' or not ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root.resolve().anchor),None,0,ctypes.byref(serial),None,None,None,0):
        raise OSError('Cannot identify recording volume')
    return f'{serial.value:08X}'

def session_data():
    root=state['staging'].parent.parent
    return {'patient':state['patient'],'test':state['test'],
            'started':state['started'].isoformat(),'record_id':state['record_id'],
            'pending':str(state['pending'].relative_to(root)) if state['pending'] else None,
            'planned':str(state['planned'].relative_to(root)) if state.get('planned') else None,
            'filed':state.get('filed',False),'sequence':state.get('sequence'),'volume':state.get('volume')}

def persist_session():
    recording_recovery.begin(RECOVERY_POINTER,state['staging'],session_data())

def recover_session():
    """Restore only the tracked scan; never take over an unrelated OBS recording."""
    with lock:
        if not RECOVERY_POINTER.exists():
            state['recovery_blocked']=False
            return
        state['recovery_blocked']=True
        connection=None
        try:
            root=base_dir()
            staging,data=recording_recovery.read(RECOVERY_POINTER,root)
            volume=volume_id(root)
            if data.get('volume') is not None and data['volume']!=volume:raise ValueError()
            pid=check_id(data['patient'])
            if not isinstance(data['test'],bool) or not re.fullmatch('[a-f0-9]{32}',data['record_id']):raise ValueError()
            if not patient_store.matches_record(patient_root(root,data['test']),data['record_id'],pid):raise ValueError()
            destination=root/'Test Recordings' if data['test'] else root/'Patients'/pid
            pending=None;filed=False
            # USB-relative paths must remain inside this session or its destination.
            for key in ('pending','planned'):
                if not data.get(key):continue
                candidate=(root/data[key]).resolve()
                if not (candidate.is_relative_to(staging.resolve()) or candidate.parent==destination.resolve()):raise ValueError()
                if candidate.is_file():
                    pending=candidate;filed=candidate.parent==destination.resolve()
                    break
            connection=client()
            recording=connection.get_record_status()
            if not isinstance(recording.output_active,bool):raise ValueError()
            if recording.output_active:
                if Path(connection.get_record_directory().record_directory).resolve()!=staging.resolve():
                    state['warning']='Recovery paused: OBS is recording a different session. Do not stop it from this app.'
                    return
                pending=None;filed=False
            elif pending is None:
                candidates=[p for p in staging.rglob('*') if p.is_file() and p.suffix.lower() in ('.mp4','.mkv','.mov','.flv','.ts')]
                if len(candidates)!=1:
                    state['warning']='Interrupted scan needs review: no single recording could be identified. Original files are retained; reconnect the original USB and OBS, or recover manually.'
                    return
                pending=candidates[0]
            state.update(patient=pid,test=data['test'],started=datetime.fromisoformat(data['started']),
                         record_id=data['record_id'],record_root=patient_root(root,data['test']),
                         staging=staging,destination=destination,active=recording.output_active,
                         pending=pending,filed=filed,planned=None,recovery_blocked=False,
                         warning='',volume=volume,sequence=data.get('sequence'),message='Interrupted scan recovered. Use Stop & save to finish.' if recording.output_active else 'Unfinished save recovered. Select Retry save.')
        except Exception:
            state['warning']='Recovery waiting: connect the original recording USB and OBS. New recordings are blocked to protect the unfinished scan.'
        finally:
            if connection is not None:
                try:connection.disconnect()
                except Exception:pass

def monitor_recording():
    with lock:
        if state.get('recovery_blocked'):
            recover_session()
            return
        if not state['active']:return
        warnings=[]
        if state.get('music_warning'):warnings.append(state['music_warning'])
        music_warning=spotify_status.warning(spotify_status.check())
        if music_warning:warnings.append(music_warning)
        audio=(state.get('sequence') or {}).get('audio')
        if audio and audio.get('warning'):warnings.append(audio['warning'])
        try:
            if not state['staging'].is_dir():warnings.append('Recording USB disconnected or unavailable. Reconnect the original drive.')
            elif shutil.disk_usage(state['staging']).free<int(settings['min_free_gb'])*1024**3:
                warnings.append('Recording USB space is low. Stop & save soon.')
        except OSError:warnings.append('Recording USB could not be checked. Check the drive connection.')
        connection=None
        try:
            connection=client()
            recording=connection.get_record_status()
            rate=usb_readiness.observed_rate(recording)
            if rate is not None:state['observed_recording_mbps']=rate
            if recording.output_active is False:
                music_stop_warning=spotify_status.pause_after_recording()
                if music_stop_warning:warnings.append(music_stop_warning)
                if audio:
                    try:obs_audio.restore(connection,audio)
                    except Exception:warnings.append('Check the OBS mixer; previous audio settings could not be restored automatically.')
                candidates=[p for p in state['staging'].rglob('*') if p.is_file() and p.suffix.lower() in ('.mp4','.mkv','.mov','.flv','.ts')]
                if len(candidates)==1:
                    state.update(active=False,pending=candidates[0])
                    try:persist_session()
                    except OSError:pass
                    state['message']='OBS stopped unexpectedly. Video retained; select Retry save.'
                else:
                    state.update(active=False,recovery_blocked=True)
                    warnings.append('OBS stopped unexpectedly but no single recording could be identified. Review the interrupted session before starting another scan.')
            elif recording.output_active is not True:warnings.append('OBS recording state could not be confirmed.')
        except Exception:warnings.append('OBS connection lost. Recording may still be running; reconnect OBS before starting another scan.')
        finally:
            if connection is not None:
                try:connection.disconnect()
                except Exception:pass
        state['warning']=' '.join(warnings)

def recording_monitor_loop():
    while True:
        try:monitor_recording()
        except Exception:
            with lock:state['warning']='Recording health check failed. Check OBS and the recording USB.'
        time.sleep(3)

@app.post('/api/recovery/clear-empty')
def clear_empty_recovery():
    d=request.get_json()
    if not isinstance(d,dict) or d.get('confirm') is not True:return jsonify(error='Confirm clearing the empty interrupted session'),400
    with lock:
        if state['active'] or state['pending'] or not state.get('recovery_blocked'):
            return jsonify(error='There is no empty recovery session to clear'),409
        connection=None
        try:
            staging,data=recording_recovery.read(RECOVERY_POINTER,base_dir())
            connection=client()
            if connection.get_record_status().output_active is not False:
                return jsonify(error='OBS may still be recording. The interrupted session has been kept.'),409
            if data.get('pending') or data.get('planned') or any(
                    p.is_file() and p.name not in ('session.json','session.json.tmp') for p in staging.rglob('*')):
                return jsonify(error='Files or a pending save exist. The session must be recovered; it cannot be cleared as empty.'),409
            obs_audio.restore(connection,(data.get('sequence') or {}).get('audio'))
            recording_recovery.finish(RECOVERY_POINTER,staging)
            state.update(recovery_blocked=False,warning='',message='Empty interrupted session cleared. No recording files were deleted.')
            return jsonify(message=state['message'])
        except Exception:return jsonify(error='Cannot verify the session is empty. Connect the original USB and OBS before retrying.'),503
        finally:
            if connection is not None:
                try:connection.disconnect()
                except Exception:pass

def family_usb_drives(source=None):
    if os.name!='nt':raise OSError('Windows required')
    kernel=ctypes.windll.kernel32
    mask=kernel.GetLogicalDrives()
    drives=[]
    for index in range(26):
        if not mask&(1<<index):continue
        root=Path(f'{chr(65+index)}:\\')
        if source is not None and str(root).casefold()==source.anchor.casefold():continue
        drive_type=kernel.GetDriveTypeW(str(root))
        if drive_type!=2 and not (drive_type==3 and usb_devices.is_usb_volume(root)):continue
        label=ctypes.create_unicode_buffer(261)
        serial=ctypes.c_ulong()
        if not kernel.GetVolumeInformationW(str(root),label,261,ctypes.byref(serial),None,None,None,0):continue
        drives.append({'root':root,'label':label.value or 'USB drive','serial':serial.value})
    return drives

@app.post('/api/family-usb/drives')
def get_family_drives():
    try:
        drives=family_usb_drives(base_dir().resolve())
        result=[]
        with lock:
            family_destinations.clear()
            for drive in drives:
                key=secrets.token_urlsafe(24)
                family_destinations[key]={**drive,'expires':time.monotonic()+600}
                result.append({'key':key,'label':f'{drive["root"]} - {drive["label"]}'})
        return jsonify(drives=result)
    except Exception:return jsonify(error='Cannot list family USB drives. Connect a separate USB drive and retry.'),503

@app.post('/api/family-usb/copy')
def copy_family_usb():
    d=request.get_json()
    if not isinstance(d,dict):return jsonify(error='Invalid copy request'),400
    fields=d.get('fields',[])
    if not isinstance(fields,list) or any(not isinstance(field,str) or field not in SHARE_FIELDS for field in fields):
        return jsonify(error='Invalid patient information selection'),400
    if not family_copy_lock.acquire(blocking=False):return jsonify(error='A USB copy is already in progress'),409
    try:
        with lock:
            if state['active'] or state['pending'] or state.get('recovery_blocked'):return jsonify(error='Finish saving the recording before preparing a family USB'),409
            destination=family_destinations.get(d.get('destination'))
        if destination is None or destination['expires']<time.monotonic():
            return jsonify(error='Refresh the USB list and select the destination again'),400
        base,source,relative=selected_recording(d.get('key'))
        present=family_usb_drives(base)
        if not any(drive['root']==destination['root'] and drive['serial']==destination['serial'] for drive in present):
            return jsonify(error='The selected USB has changed or disconnected. Refresh the drive list.'),409
        before=source.stat()
        if shutil.disk_usage(destination['root']).free<before.st_size+1024*1024:
            return jsonify(error='The family USB does not have enough free space'),400
        details=patient_store.for_recording(patient_root(base,relative.parts[0]=='Test Recordings'),str(relative),volume_id(base))
        if fields and details is None:return jsonify(error='No saved patient details are linked to this recording. Copy the video only.'),400
        folder_name='Miracle of Life '+datetime.now().strftime('%Y-%m-%d_%H%M%S')+'_'+secrets.token_hex(4)
        incomplete=destination['root']/(folder_name+'.incomplete')
        incomplete.mkdir(exist_ok=False)
        digest=hashlib.sha256()
        with source.open('rb') as original,(incomplete/'recording.mp4').open('xb') as output:
            while chunk:=original.read(1024*1024):
                output.write(chunk)
                digest.update(chunk)
            output.flush();os.fsync(output.fileno())
        copied=hashlib.sha256()
        with (incomplete/'recording.mp4').open('rb') as check:
            while chunk:=check.read(1024*1024):copied.update(chunk)
        after=source.stat()
        if digest.digest()!=copied.digest() or (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise OSError()
        if fields:
            text='Miracle of Life Ultrasound Recorder\n\n'+'\n'.join(f'{SHARE_FIELDS[field]}: {details.get(field, "")}' for field in fields)
            with (incomplete/'patient-information.txt').open('x',encoding='utf-8') as info:
                info.write(text);info.flush();os.fsync(info.fileno())
        incomplete.rename(destination['root']/folder_name)
        return jsonify(message='Family USB copy completed and verified. Safely eject the USB in Windows before removing it.',folder=folder_name)
    except ValueError:return jsonify(error='Recording unavailable. Refresh the saved recordings list.'),404
    except Exception:return jsonify(error='USB copy failed. Originals are unchanged. Any .incomplete folder is unfinished; do not give it to the family. Reconnect the drive and retry.'),503
    finally:family_copy_lock.release()

def find_obs_executable():
    candidates=[]
    if os.name=='nt':
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
            for name in ('obs64.exe','obs32.exe'):
                try:
                    with winreg.OpenKey(hive,rf'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{name}') as key:
                        candidates.append(Path(winreg.QueryValue(key,None).strip('"')))
                except OSError:pass
    for variable in ('ProgramW6432','ProgramFiles','ProgramFiles(x86)'):
        if os.environ.get(variable):
            for architecture,name in (('64bit','obs64.exe'),('32bit','obs32.exe')):
                candidates.append(Path(os.environ[variable])/'obs-studio'/'bin'/architecture/name)
    return next((path for path in candidates if path.is_absolute() and path.is_file()),None)

def ensure_obs_running():
    global obs_launch
    if os.name!='nt':return {'error':'Starting OBS is supported on Windows only'},400
    with lock:
        if settings['obs_host'].casefold() not in ('localhost','127.0.0.1','::1'):
            return {'error':'OBS is configured on a remote computer. Start OBS on that computer.'},400
        if state['active'] or state['pending']:
            return {'error':'Finish the current recording before launching OBS'},409
        running=obs_process_running()
        if running:
            return {'message':'OBS is already running. Check its WebSocket server settings.'},200
        if obs_launch is not None and obs_launch.poll() is None:
            return {'message':'OBS is starting. Waiting for its WebSocket server.'},200
        if running is None:
            return {'error':'Could not check whether OBS is running. Open OBS manually to avoid launching another instance.'},503
        executable=find_obs_executable()
        if executable is None:
            return {'error':'OBS installation was not found. Install OBS Studio in its standard location or open your custom installation manually.'},404
        try:
            startup=subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow=7  # SW_SHOWMINNOACTIVE: minimized without taking focus.
            obs_launch=subprocess.Popen([str(executable),'--minimize-to-tray'],cwd=str(executable.parent),
                                        startupinfo=startup,
                                        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        except OSError:return {'error':'Could not launch OBS. Open OBS Studio manually.'},503
        return {'message':'OBS is starting. Waiting for its WebSocket server.'},202


@app.post('/api/obs-start')
def start_obs():
    result,status_code=ensure_obs_running()
    return jsonify(result),status_code

def startup_obs():
    try:
        result,status_code=ensure_obs_running()
        if status_code>=400:
            with lock:
                if not state['active'] and state['message']=='Ready':
                    state['message']=result['error']
    except Exception:
        with lock:
            if not state['active'] and state['message']=='Ready':
                state['message']='Could not check OBS at startup. Use the OBS button to retry.'

def available_recording_root():
    try:return base_dir()
    except (OSError,RuntimeError):return None

def startup_storage():
    with lock:
        try:
            patient_root()
            state['storage_warning']=''
        except Exception:
            state['storage_warning']='USB patient metadata could not be migrated. Originals are retained. Close older app copies and check APPDATA/USB write access; conflicting patient IDs need manual review.'

def patient_root(root=None, test=False):
    with storage_lock:
        if root is None:
            try:root=base_dir()
            except (OSError,RuntimeError):root=None
        if root is not None:
            try:
                volume=volume_id(root)
                for is_test in (False,True):
                    legacy=root/'Test Patient Records' if is_test else root
                    local=ROOT/'test-patients' if is_test else ROOT/'patients'
                    storage_migration.migrate(legacy,local,volume,ROOT/'migration-backups'/volume/('test' if is_test else 'patients'))
                recording_recovery.migrate_legacy_journals(RECOVERY_POINTER,root)
                storage_migration.migrate_exports(root,ROOT/'recovery'/'legacy-exports')
                state['storage_warning']=''
            except Exception:
                state['storage_warning']='USB metadata migration needs attention. Originals are retained; check APPDATA/USB access and close older app copies. Conflicting patient IDs require manual review.'
                raise
        return ROOT/'test-patients' if test else ROOT/'patients'

@app.post('/api/patients/generate-id')
def generate_patient_id():
    d=request.get_json()
    if not isinstance(d,dict) or not isinstance(d.get('test',False),bool):return jsonify(error='Invalid patient ID request'),400
    with lock:
        if state['active'] or state['pending'] or state.get('recovery_blocked') or family_copy_lock.locked():
            return jsonify(error='Finish recording, saving or copying before generating a new patient ID'),409
        try:
            pid=patient_store.generate_id(patient_root(test=d.get('test',False)),available_recording_root())
            return jsonify(patient_id=pid)
        except Exception:return jsonify(error='Could not generate a patient ID. Check APPDATA write access and any pending USB metadata migration.'),503

def duplicate_patient_response(error):
    return jsonify(error='This patient ID already exists. Confirm the existing patient before continuing.',
                   code='duplicate_patient_id',existing=error.record),409

@app.post('/api/patients/archive')
def archive_patient():
    d=request.get_json()
    if not isinstance(d,dict) or d.get('confirm') is not True or not isinstance(d.get('test',False),bool) or not isinstance(d.get('archived'),bool):
        return jsonify(error='Confirm archiving or restoring the selected patient'),400
    try:
        pid=check_id(d.get('patient_id'))
        if not isinstance(d.get('record_id'),str):raise ValueError()
        with lock:
            if state['active'] or state['pending'] or state.get('recovery_blocked') or family_copy_lock.locked():
                return jsonify(error='Finish recording, saving, copying or recovery before changing patients'),409
            patient_store.change_archive(patient_root(test=d.get('test',False)),pid,d['record_id'],d['archived'])
        return jsonify(message='Patient archived. Details and recordings have been kept.' if d['archived'] else 'Patient restored.')
    except patient_store.PatientChangedError:return jsonify(error='Patient changed or was deleted. Search again before continuing.'),409
    except (ValueError,TypeError):return jsonify(error='Invalid patient selection'),400
    except Exception:return jsonify(error='Could not update the patient archive. Check APPDATA access.'),503

@app.post('/api/patients/save')
def save_patient():
    d=request.get_json()
    if not isinstance(d,dict) or not isinstance(d.get('test',False),bool):return jsonify(error='Invalid patient record'),400
    try:
        pid=check_id(str(d.get('patient_id','')).strip())
        details=patient_store.validate_details(d)
        with lock:
            if state['active'] or state['pending']:return jsonify(error='Finish the recording before saving patient changes'),409
            record_id=patient_store.save(patient_root(test=d.get('test',False)),pid,details,d.get('expected_record_id'),check_duplicate=True)
        return jsonify(message='Patient details saved',record_id=record_id)
    except patient_store.DuplicatePatientError as error:return duplicate_patient_response(error)
    except patient_store.ArchivedPatientError:return jsonify(error='This patient ID is archived. Restore the patient before adding details or recordings.'),409
    except patient_store.PatientChangedError:return jsonify(error='Patient changed or was deleted. Search again before saving.'),409
    except patient_store.PatientValidationError as error:return jsonify(error=str(error)),400
    except ValueError:return jsonify(error='Enter a valid patient ID'),400
    except Exception:return jsonify(error='Patient details could not be saved. Check APPDATA write access and metadata migration; changes have not been confirmed.'),503

@app.post('/api/patients/search')
def search_patients():
    d=request.get_json()
    if not isinstance(d,dict) or not isinstance(d.get('test',False),bool) or not isinstance(d.get('include_archived',False),bool):return jsonify(error='Invalid search'),400
    try:
        pid=check_id(d['patient_id']) if 'patient_id' in d else None
        with lock:
            rows=patient_store.find(patient_root(test=d.get('test',False)),str(d.get('q',''))[:200],pid,d.get('include_archived',False))
        return jsonify(records=rows)
    except ValueError:return jsonify(error='Enter a valid patient ID'),400
    except Exception:return jsonify(error='Patient records unavailable. Check APPDATA access and any pending USB metadata migration.'),503

@app.post('/api/patients/delete')
def delete_patient():
    d=request.get_json()
    if not isinstance(d,dict) or d.get('confirm') is not True or not isinstance(d.get('test',False),bool):
        return jsonify(error='Confirm the patient deletion first'),400
    try:
        pid=check_id(d.get('patient_id'))
        if not isinstance(d.get('confirm_patient_id'),str) or d['confirm_patient_id'].casefold()!=pid.casefold():
            return jsonify(error='Type the selected patient ID to confirm permanent deletion'),400
        if not isinstance(d.get('record_id'),str) or not d['record_id']:raise ValueError()
        with lock:
            if state['active'] or state['pending'] or state.get('recovery_blocked') or family_copy_lock.locked():
                return jsonify(error='Finish recording, saving or copying before deleting patient details'),409
            patient_store.delete(patient_root(test=d.get('test',False)),pid,d['record_id'])
        return jsonify(message='Patient details and saved history deleted. Video recordings have been kept.')
    except patient_store.PatientChangedError:
        return jsonify(error='Patient details changed or were already deleted. Search again before deleting.'),409
    except patient_store.ArchiveRequiredError:
        return jsonify(error='Archive this patient first. Archived patients can be restored instead of permanently deleted.'),409
    except (ValueError,TypeError):return jsonify(error='Invalid patient selection'),400
    except Exception:return jsonify(error='Deletion could not be confirmed. Check the USB drive and search again.'),503

def client(): return obs.ReqClient(host=settings['obs_host'],port=int(settings['obs_port']),password=settings['obs_password'],timeout=5)

def obs_process_running():
    """Return None when local process inspection is unavailable."""
    if os.name!='nt':return None
    try:
        result=subprocess.run(['tasklist','/FO','CSV','/NH'],capture_output=True,
                              text=True,errors='replace',timeout=3,creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:return None
        return any(line.split(',')[0].strip('"').casefold() in ('obs64.exe','obs32.exe','obs.exe')
                   for line in result.stdout.splitlines())
    except (OSError,subprocess.TimeoutExpired):return None

def obs_health():
    host=settings['obs_host']
    running=obs_process_running() if host.casefold() in ('localhost','127.0.0.1','::1') else None
    result={'obs_running':running,'port_open':False,'websocket_connected':False}
    try:
        with socket.create_connection((host,int(settings['obs_port'])),timeout=2):pass
        result['port_open']=True
    except OSError:
        result['message']=('OBS is not running. Open OBS Studio, then enable its WebSocket server.'
                           if running is False else
                           'WebSocket port is unreachable. In OBS, open Tools > WebSocket Server Settings, enable the server and check the configured host and port (default 5555).')
        return result
    connection=None
    try:
        connection=client()
        connection.get_version()
        result.update(websocket_connected=True,message='OBS WebSocket connected and responding')
    except Exception:
        result['message']='The port is open, but OBS WebSocket could not connect. Check the OBS WebSocket password, server settings and compatibility.'
    finally:
        if connection is not None:
            try:connection.disconnect()
            except Exception:pass
    return result

@app.get('/api/obs-health')
def get_obs_health():
    return jsonify(obs_health())

@app.get('/api/music-health')
def get_music_health():
    return jsonify(spotify_status.check())

@app.post('/api/spotify/control')
def control_spotify():
    d=request.get_json()
    if not isinstance(d,dict) or d.get('action') not in ('play','pause','previous','next'):
        return jsonify(error='Choose a valid Spotify playback control'),400
    try:return jsonify(spotify_status.control(d['action']))
    except spotify_status.SpotifyControlError as error:return jsonify(error=str(error)),409

@app.get('/api/obs-sources')
def get_obs_sources():
    connection=None
    try:
        connection=client()
        inputs=connection.get_input_list().inputs
        sources=obs_sources.choices(inputs)
        try:sources['devices']=obs_devices.list_devices(connection,settings,inputs)
        except Exception:
            sources['devices']=[]
            sources['device_warning']='Capture devices could not be enumerated. Existing OBS sources remain available.'
        sources['scenes']=sorted([item['sceneName'] for item in connection.get_scene_list().scenes
                                 if not item['sceneName'].startswith('MOL Recorder ')],key=str.casefold)
        return jsonify(sources)
    except Exception:return jsonify(error='Could not load OBS sources. Start OBS, check the WebSocket connection and refresh the list.'),503
    finally:
        if connection is not None:
            try:connection.disconnect()
            except Exception:pass
@app.get('/api/recording-drives')
def recording_drives():
    try:
        return jsonify(drives=[{'id':f'{drive["serial"]:08X}', 'label':f'{drive["root"]} - {drive["label"]}'}
                               for drive in family_usb_drives()])
    except Exception:
        return jsonify(error='Cannot list recording USB drives. Connect the USB and refresh.'),503


@app.get('/api/usb-readiness')
def get_usb_readiness():
    with lock:
        config=settings.copy()
        rate=state.get('observed_recording_mbps')
    try:
        drives=family_usb_drives() if config.get('recording_volume') else []
        return jsonify(usb_readiness.summarize(drives,config.get('recording_volume',''),
                       config['min_free_gb'],rate,config.get('recording_drive_name','')))
    except Exception:
        return jsonify(status='unavailable',connected=False,drive=config.get('recording_drive_name') or 'Recording USB',
                       message='Cannot check the recording USB. Check its connection and refresh Settings.'),503


def usb_root(require_space=False):
    selected=settings.get('recording_volume','')
    if not selected:raise RuntimeError('Select the recording USB in Settings first.')
    matches=[drive['root'] for drive in family_usb_drives() if f'{drive["serial"]:08X}'==selected]
    if len(matches)!=1:raise RuntimeError('The selected recording USB is disconnected or its identity is ambiguous. Check Settings.')
    if require_space and shutil.disk_usage(matches[0]).free < int(settings['min_free_gb'])*1024**3:raise RuntimeError('USB drive has insufficient free space')
    return matches[0]

def base_dir(): return usb_root()/'Ultrasound Recordings'
def check_id(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',value or ''):raise ValueError('Patient ID: letters, numbers, underscores and hyphens only (max 64)')
    if value.upper().split('.')[0] in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}:raise ValueError('Patient ID is a reserved Windows filename')
    return value
@app.before_request
def local_only():
    if not NETWORK_MODE and request.remote_addr not in ('127.0.0.1','::1'):abort(403)
    if (urlsplit(request.host_url).hostname or '').casefold() not in ALLOWED_HOSTS:abort(403)
    if NETWORK_MODE:
        auth=request.authorization
        if not NETWORK_PASSWORD or not auth or auth.type!='basic' or auth.username!='tester' or not secrets.compare_digest(
                hashlib.sha256((auth.password or '').encode()).digest(),hashlib.sha256(NETWORK_PASSWORD.encode()).digest()):
            return Response('Sign in to the test recorder',401,{'WWW-Authenticate':'Basic realm="Recorder testing", charset="UTF-8"'})
    if request.path!='/':
        if request.headers.get('X-Recorder-Token')!=TOKEN and request.cookies.get('recorder_token')!=TOKEN:abort(403)
    if request.method=='POST' and request.headers.get('X-Recorder-Token')!=TOKEN:abort(403)

@app.after_request
def private_response(response):
    response.headers['Cache-Control']='no-store'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    return response

HTML='''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Miracle of Life | Ultrasound Recorder</title><style>
body{margin:0;background:#101723;color:#e9eef7;font:15px system-ui,Segoe UI,Arial}main{max-width:1250px;margin:auto;padding:24px}header,.row{display:flex;gap:12px;align-items:center;justify-content:space-between}h1{font-size:24px}h2{font-size:19px}section{background:#1b2635;border:1px solid #344253;border-radius:12px;padding:22px;margin:16px 0}label{display:block;font-size:13px;margin:12px 0 5px;color:#b9c8d9}input,select{box-sizing:border-box;width:100%;padding:11px;border-radius:7px;background:#111a27;border:1px solid #46556b;color:white}input[type=checkbox]{width:auto}button{background:#2866bb;border:0;color:white;padding:12px 18px;border-radius:8px;cursor:pointer}button:disabled{opacity:.45;cursor:default}.danger{background:#b43645}.muted{color:#aebed1}.grid{display:grid;grid-template-columns:minmax(280px,1fr) minmax(400px,1.5fr);gap:20px}.preview{background:black;aspect-ratio:16/9;width:100%;object-fit:contain;border-radius:8px}.small{font-size:12px}.item{border-bottom:1px solid #354356;padding:10px 0}.pill{background:#34455d;padding:5px 10px;border-radius:20px}a{color:#8ec5ff}.step{background:#34455d}.step.selected{background:#2866bb}.hidden{display:none!important}.topnav{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.summary{padding:12px;background:#111a27;border-radius:8px;margin:12px 0}#message{min-height:20px;white-space:pre-wrap}@media(max-width:800px){.grid{grid-template-columns:1fr}main{padding:12px}header{flex-wrap:wrap}}

:root{--rose:#ad7189;--plum:#754c60;--ink:#493c43;--blush:#f3dce1;--cream:#fff9f7}
body{background:var(--cream);color:var(--ink)}main{max-width:1350px;padding:20px 26px}header{background:var(--plum);border-radius:15px;padding:17px 22px;box-shadow:0 3px 15px #754c6020}.brand{min-width:0}.brand img{display:block;max-width:350px;width:100%;height:72px;object-fit:contain;object-position:left center}.tagline{color:#f5e4ea;font-size:11px;letter-spacing:.055em;margin-top:3px}section{background:white;border:1px solid #ebd9df;box-shadow:0 3px 15px #754c6010}h2{color:var(--plum)}label{color:#805f70}input,select{background:#fffafc;color:var(--ink);border:1px solid #d9bac7}button{background:var(--rose)}button:hover{filter:brightness(.95)}button:disabled{filter:none}.danger{background:#a54863}.muted{color:#8b7180}.item{border-bottom-color:#eddce2}.pill{background:#fff0f4;color:var(--plum)}a{color:#8a4669}.step{background:#efdee4;color:var(--plum)}.step.selected{background:var(--rose);color:white}.summary{background:#faeef2;color:var(--plum)}.topnav button{background:#f6e9ee;color:var(--plum)}.preview{background:#171219;border:2px solid #e6ccd5}@media(max-width:800px){.brand img{height:54px;max-width:250px}.tagline{font-size:9px}header{align-items:flex-start}}
.header-controls{flex:1;min-width:0;max-width:100%;display:flex;flex-direction:column;gap:10px}.header-controls .topnav{justify-content:flex-end}.spotify-buttons{display:flex;gap:5px;flex-shrink:0}.spotify-buttons button{padding:7px 9px;display:flex;align-items:center;justify-content:center}.header-status{display:flex;align-items:center;gap:8px;justify-content:flex-end;min-width:0;width:100%}.header-controls .pill{flex-shrink:0}#spotify-status{box-sizing:border-box;max-width:100%;min-width:0;text-align:right;white-space:normal;overflow-wrap:anywhere;padding:6px 10px;font-size:13px}@media(max-width:800px){.header-controls{flex-basis:100%}.header-controls .topnav{justify-content:flex-start}}
#launch-obs[data-state="error"],#launch-obs[data-state="stopped"]{background:#a54863;color:white}#launch-obs[data-state="connected"]{background:#e0efdf;color:#305331}#clock{font-size:42px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.2;letter-spacing:.04em;color:var(--plum);margin:18px 0 0}
</style></head><body><main><header><div class="brand"><img src="data:image/png;base64,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ThYQQD/Zx6O7IaCVBAgQIECAAAECBAgQIECAwP4L6CEBAgTWLiCAvnZyOyRAgAABAgQIECBAgAABAgQIECBAgACBXRAQQN+Fo6SNBLZZQNsIECBAgAABAgQIECBAgACB/RfQQwIHKiCAfqAHXrcJECBAgAABAgQIHKqAfhMgQIAAAQIECBCYVUAAfVYp6xEgQGD7BLSIAAECBAgQIECAAAECBAgQ2H8BPdyggAD6BvHtmgABAgQIECBAgAABAocloLcECBAgQIAAgd0SEEDfreOltQQIECCwLQLaQYAAAQIECBAgQIAAAQIECOy9wCfsfQ91kAABAgQIECBAgAABAgQIEDgHAQECBAgQIDC/gBHo85vZggABAgQIENisgL0TIECAAAECBAgQIECAAIG1CAigr4V52k6UEyBAgAABAgQIECBAgAABAvsvoIcECBAgsKsCAui7euS0mwABAgQIECCwCQH7JECAAAECBAgQIECAwAEJCKAf0MHW1csKuEeAAAECBAgQIECAAAECBAjsv4AeEiBAYBkBAfRl9GxLgAABAgQIECBAYH0C9kSAAAECBAgQIECAwJoFBNDXDG53BAhUQCZAgAABAgQIECBAgAABAgT2X0APCey+gAD67h9DPSBAgAABAgQIECBAYNUC6idAgAABAgQIEDhIAQH0gzzsOk2AwCEL6DsBAgQIECBAgAABAgQIECCw/wJ6OIyAAPowjmohQIAAAQIECBAgQIAAgdUIqJUAAQIECBAgsDEBAfSN0dsxAQIECByegB4TIECAAAECBAgQIECAAAECuySwWAB9l3qorQQIECBAgAABAgQIECBAgMBiArYiQIAAAQIHLiCAfuBPAN0nQIAAAQKHIqCfBAgQIECAAAECBAgQIEBgXgEB9HnFNr++FhAgQIAAAQIECBAgQIAAAQL7L6CHBAgQILAFAgLoW3AQNIEAAQIECBAgsN8CekeAAAECBAgQIECAAIHdFBBA383jptWbErBfAgQIECBAgAABAgQIECBAYP8F9JAAAQIfFRBA/yiEBQECBAgQIECAAIF9FNAnAgQIECBAgAABAgQWFxBAX9zOlgQIrFfA3ggQIECAAAECBAgQIECAAIH9F9BDAlslIIC+VYdDYwgQIECAAAECBAgQ2B8BPSFAgAABAgQIENh1AQH0XT+C2k+AAIF1CNgHAQIECBAgQIAAAQIECBAgsP8CeniWgAD6WSQKCBAgQIAAAQIECBAgQGDXBbSfAAECBAgQIDCEgAD6EIrqIECAAAECqxNQMwECBAgQIECAAAECBAgQILAhgTUG0DfUQ7slQIAAAQIECBAgQIAAAQIE1ihgVwQIECBAYH8EBND351jqCQECBAgQIDC0gPoIECBAgAABAgQIECBA4KAFBNAP5PDrJgECBAgQIECAAAECBAgQILD/AnpIgAABAsMK/P8AAAD//623QwwAAAAGSURBVAMAtcwA/yl5VW8AAAAASUVORK5CYII=" alt="Miracle of Life"><div class="tagline">ULTRASOUND RECORDER · Capturing Precious Memories of Your Future Family</div></div><div class="header-controls"><div class="topnav"><span class="pill" id="status">Connecting...</span><button id="launch-obs" onclick="startObs()" title="Checking OBS connection" aria-live="polite">OBS: Checking...</button><button onclick="showPage('saved')">Saved recordings</button><button onclick="showPage('settings')">Settings</button></div><div class="header-status"><button id="spotify-status" type="button" onclick="checkSpotify()" title="Check background music playback" aria-live="polite">Spotify: Checking...</button><div class="spotify-buttons" role="group" aria-label="Spotify playback"><button id="spotify-previous" type="button" aria-label="Previous track" title="Previous track" onclick="controlSpotify('previous')" disabled><svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false" fill="currentColor"><path d="M5 5h2v14H5zm14 0v14L8 12z"/></svg></button><button id="spotify-control" type="button" aria-label="Play" title="Play" onclick="controlSpotify('toggle')" disabled><svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false" fill="currentColor"><path d="M8 5v14l11-7z"/></svg></button><button id="spotify-next" type="button" aria-label="Next track" title="Next track" onclick="controlSpotify('next')" disabled><svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false" fill="currentColor"><path d="M17 5h2v14h-2zM5 5v14l11-7z"/></svg></button></div></div></div></header><div class="row" style="justify-content:flex-start;margin-top:18px"><button class="step selected" id="nav-patient" onclick="showPage('patient')">1 · Patient information</button><button class="step" id="nav-preview" onclick="showPage('preview')">2 · Preview &amp; record</button></div>
<p id="recording-warning" role="alert" style="color:#a54863;font-weight:600" hidden></p><button id="clear-empty-recovery" hidden onclick="clearEmptyRecovery()">Clear empty interrupted session</button><div id="page-patient" class="grid"><section><h2>Patient information</h2><label>Patient name</label><input id="name" maxlength="200" autocomplete="off"><label>Patient ID</label><div style="display:flex;gap:8px"><input id="pid" autocomplete="off"><button id="generate-pid" type="button" onclick="generatePatientId()">Generate ID</button></div><label>Baby’s date of birth</label><input id="dob" type="date" lang="en-AU" autocomplete="off"><label>Scan type</label><select id="scan"><option>Standard Ultrasound</option><option>3D / 4D Ultrasound</option><option>Gender Scan</option><option>Other</option></select><label>Notes</label><textarea id="notes" rows="4" maxlength="10000" style="box-sizing:border-box;width:100%"></textarea><button onclick="savePatient()">Save patient details</button> <button onclick="newPatient()">New patient</button><p id="patient-message" role="status"></p><button onclick="next()">Next: Preview &amp; record →</button></section><section><h2>Recording workflow</h2><p>Enter the patient details, then select <b>Next</b> to check the video preview and start recording.</p><p class="muted">Recordings are stored by patient ID on the configured USB drive. Patient details, notes and scan history are saved on this PC in APPDATA. Only videos are stored on the recording USB. Starting a recording saves a copy of the details entered for that scan.</p><h2>Find a patient</h2><label>Patient name or ID</label><input id="patient-search" autocomplete="off"><p><button onclick="findPatients()">Search patients</button></p><label><input type="checkbox" id="include-archived" onchange="findPatients()"> Include archived patients</label><div id="patient-results"></div><div id="patient-history" style="white-space:pre-wrap"></div></section></div>
<div id="page-preview" class="grid hidden"><section><h2>Recording details</h2><div class="summary" id="patient-summary"></div><button onclick="showPage('patient')">← Edit patient details</button><p class="muted small">Keep OBS running. Check the preview before recording.</p><p id="audio-route" class="muted small"></p><button id="start" onclick="start()">● Start recording</button> <button class="danger" id="stop" onclick="stop()">■ Stop &amp; save</button><p id="message" class="muted"></p><p id="clock" aria-label="Recording elapsed time">00:00:00</p></section><section><h2>OBS video preview</h2><p id="recording-health" role="status">Health: waiting for OBS...</p><p id="preview-preset" class="muted small">Preview quality follows the PC performance preset in Settings.</p><img class="preview" id="preview" alt="OBS preview unavailable"><p class="muted small"><span id="preview-status">Connecting to OBS preview...</span> OBS records at its configured full quality.</p></section></div>
<div id="page-saved" class="hidden"><section><div class="row"><h2>Saved recordings</h2><button onclick="showPage('patient');newPatient()">New patient</button></div><label>Search by patient name or ID</label><input id="search" placeholder="Search patient name or ID" oninput="loadFiles()"><div id="files" class="muted">Loading...</div></section></div>
<div id="page-settings" class="hidden"><section><div class="row"><h2>Settings</h2><button onclick="showPage('patient')">Close</button></div><h2>Pre-session test</h2><button id="preflight-start" type="button" onclick="startPreflight()">Record a 10-second test</button> <button id="preflight-stop" class="danger hidden" type="button" onclick="stop()">Stop test &amp; save</button><p id="preflight-message" role="status"></p><p class="muted small">Use a test image with no patient information. The test saves separately; your patient form is kept.</p><p id="preflight-result" role="status"></p><video id="preflight-player" controls preload="none" class="preview hidden"></video><h2>PC performance</h2><label for="performance_profile">Performance preset</label><select id="performance_profile"><option value="low">Low performance PC</option><option value="high">High performance PC</option></select><p class="muted small">Low: 540p preview, up to 2 fps. High: 1080p preview, up to 15 fps. Recording quality stays as configured in OBS.</p><label>Recording USB drive</label><select id="recording_volume"><option value="">Select a recording USB</option></select><p><button type="button" onclick="loadRecordingDrives()">Refresh USB drives</button></p><p id="recording-drive-message" class="muted small">The selected USB is remembered even if its drive letter changes.</p><label>OBS host</label><input id="obs_host"><label>OBS WebSocket port</label><input type="number" id="obs_port"><label>OBS password</label><input type="password" id="obs_password" placeholder="Leave blank to keep existing password"><p><label><input type="checkbox" id="test"> Test mode (separate test records and recordings)</label></p><h2>Recording sources</h2><label>Video capture device / OBS source</label><select id="video_source"><option value="blackmagic">blackmagic</option></select><label>OBS audio source</label><select id="audio_source"><option value="">Automatic Spotify / system output</option><option value="@video">Use selected video source audio</option></select><p><button type="button" onclick="loadObsSources()">Refresh OBS sources</button></p><p id="source-message" class="muted small"></p><label>Automatic audio preference</label><select id="music_capture"><option value="spotify">Spotify directly (system output fallback)</option><option value="system">System output (default Windows speakers)</option></select><p class="muted small">System output includes other PC sounds. Spotify playback warnings allow recording to continue.</p><h2>Video logos</h2><p>Images show for 3 seconds. Videos play in full (up to 60 seconds), then fade into the ultrasound over 1 second. Choose whether to replay the intro or show a fixed outro image for 3 seconds before saving.</p><label>Intro image or video</label><input id="intro_image_upload" type="file" accept="image/png,image/jpeg,video/mp4,video/quicktime,video/webm,video/x-matroska,.mkv"><label>Outro</label><select id="outro_mode"><option value="same">Replay the intro media</option><option value="image">Use a fixed outro image</option></select><label>Fixed outro image (PNG/JPEG)</label><input id="outro_image_upload" type="file" accept="image/png,image/jpeg"><label>Video overlay (white corner logo)</label><input id="watermark_image_upload" type="file" accept="image/png,image/jpeg"><p><button onclick="uploadLogos()">Upload branding media</button></p><label>Original OBS scene (for microphone routing and returning after recording)</label><select id="ultrasound_scene"><option value="">Current OBS scene</option></select><p><label><input id="branding_enabled" type="checkbox"> Enable opening and closing logo sequence</label></p><p class="muted small">Use the full-frame corner logo image. Black background removal is handled in OBS. OBS must run on this PC with Studio Mode off.</p><label>Minimum free USB space (GB)</label><input type="number" id="min_free_gb"><p><button onclick="saveSettings()">Save settings</button></p><p id="settings-message" class="muted"></p></section></div><dialog id="share-dialog" style="max-width:600px;border:1px solid #d9bac7;border-radius:12px"><h2>Prepare family USB or share recording</h2><p>The video can be copied on its own. Select any patient information you want to include.</p><label>Family USB drive</label><select id="family-drive"><option value="">Connect a separate USB and refresh</option></select><p><button onclick="loadFamilyDrives()">Refresh USB list</button> <button id="family-copy" onclick="copyFamilyUsb()">Copy to family USB</button></p><p id="family-message" role="status"></p><div id="share-fields"></div><p id="share-info" style="white-space:pre-wrap"></p><p>The ZIP contains the video and a text file. The email draft contains the video as an attachment and selected details in its body. Open the downloaded .eml file in your email app, add the recipient and review before sending. Large videos may exceed email attachment limits. Exports are not encrypted.</p><button onclick="downloadShare()">Download share package</button> <button onclick="downloadShare('email')">Download email draft</button> <button onclick="document.getElementById('share-dialog').close()">Close</button></dialog><div id="usb-readiness" class="summary" role="status" aria-live="polite">USB: Checking recording drive...</div></main><script>
const token=__TOKEN__;let lastHotkey=0,currentPage='patient';
async function api(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json','X-Recorder-Token':token},body:body?JSON.stringify(body):undefined});const d=await r.json();if(!r.ok){const e=Error(d.error||'Request failed');e.data=d;throw e}return d}
function val(id){const value=document.getElementById(id).value;if(id==='dob'&&value){const [year,month,day]=value.split('-');return day+'/'+month+'/'+year}return value}
function msg(s){document.getElementById('message').textContent=s}
function showPage(page){currentPage=page;for(const p of ['patient','preview','saved','settings'])document.getElementById('page-'+p).classList.toggle('hidden',p!==page);for(const p of ['patient','preview'])document.getElementById('nav-'+p).classList.toggle('selected',p===page);if(page==='preview')document.getElementById('patient-summary').textContent='Patient: '+(val('name').trim()||'Name not entered')+' | Patient ID: '+(val('pid')||'(test)')+' | '+val('scan')+(document.getElementById('test').checked?' | TEST MODE':'');if(page==='saved')loadFiles()}
function next(){if(!document.getElementById('test').checked&&!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(val('pid').trim())){alert('Enter a valid patient ID (letters, numbers, underscores or hyphens).');return}showPage('preview')}
let usbCheckBusy=false;
function showUsbReadiness(d){const panel=document.getElementById('usb-readiness');let text='USB: '+d.drive+' | '+d.message;if(d.connected){text+=' | '+(d.free_bytes/1024**3).toFixed(1)+' GiB free of '+(d.total_bytes/1024**3).toFixed(1)+' GiB';const mins=d.estimated_minutes;const duration=mins>0?(Math.floor(mins/60)?Math.floor(mins/60)+'h ':'')+(mins%60)+'m':(d.status==='ready'?'<1 min':'0 min');text+=' | Approx. '+duration+' recording remaining ('+(d.estimate_basis==='assumed 20 Mbps'?'20 Mbps estimate':'recent recording rate')+')';panel.title='Estimate above '+(d.reserve_bytes/1024**3).toFixed(1)+' GiB reserve, based on '+d.estimate_basis+'. Actual time varies with OBS quality and video content.'}else panel.title=d.message;panel.textContent=text;panel.style.backgroundColor=d.status==='ready'?'#e0efdf':d.status==='low_space'?'#fff0d9':'#fae1e8';panel.style.color=d.status==='ready'?'#305331':'#853d55'}
async function refreshUsbReadiness(){if(usbCheckBusy||document.hidden)return;usbCheckBusy=true;try{const r=await fetch('/api/usb-readiness',{headers:{'X-Recorder-Token':token},signal:AbortSignal.timeout(7000)});const d=await r.json();if(!d.status)throw Error('USB check unavailable');showUsbReadiness(d)}catch(e){showUsbReadiness({status:'unavailable',connected:false,drive:'Recording USB',message:'Readiness unavailable. Check the USB connection.'})}finally{usbCheckBusy=false}}
setInterval(refreshUsbReadiness,5000);refreshUsbReadiness();
async function startPreflight(){const button=document.getElementById('preflight-start'),status=document.getElementById('preflight-message');button.disabled=true;status.textContent='Preparing test recording...';try{const d=await api('/api/start',{preflight:true});status.textContent=d.message;refresh()}catch(e){status.textContent=e.message;refresh()}}
let healthBusy=false,lastTestKey='';
async function refreshHealth(){if(healthBusy||currentPage!=='preview'||document.hidden)return;healthBusy=true;try{const h=await api('/api/recording-health');const audio={signal:'Audio signal detected in OBS',quiet:'No recent audio signal',muted:'Recording audio muted',intro:'Background audio waiting for intro',unknown:'Audio signal not verified',not_recording:'Audio checked during recording'}[h.audio]||'Audio signal not verified';const fps=typeof h.stats.active_fps==='number'?' | OBS '+h.stats.active_fps.toFixed(1)+' fps':'';document.getElementById('recording-health').textContent=audio+fps+(h.warnings.length?' | '+h.warnings.join(' '):'')+' | Check the preview picture.'}catch(e){document.getElementById('recording-health').textContent='Health unavailable. Check OBS.'}finally{healthBusy=false}}
setInterval(refreshHealth,3000);
let recordingClockAnchor=null,recordingStartPending=false,clockRevision=0;
function tickRecordingClock(){if(recordingClockAnchor===null)return;const seconds=Math.max(0,Math.floor((performance.now()-recordingClockAnchor)/1000));const text=[Math.floor(seconds/3600),Math.floor(seconds/60)%60,seconds%60].map(value=>String(value).padStart(2,'0')).join(':');const clock=document.getElementById('clock');if(clock.textContent!==text)clock.textContent=text}
function beginRecordingClock(seconds=0){recordingClockAnchor=performance.now()-seconds*1000;tickRecordingClock()}
setInterval(tickRecordingClock,250);
async function start(){if(recordingStartPending)return;recordingStartPending=true;clockRevision++;beginRecordingClock();msg('Starting recording...');document.getElementById('start').disabled=true;try{const d=await patientRequest('/api/start',patientData());msg(d.message);if(d.record_id)rememberPatient(d.record_id)}catch(e){msg(e.message)}finally{recordingStartPending=false;clockRevision++;refresh()}}
async function stop(){clockRevision++;msg('Finishing recording and saving...');document.getElementById('stop').disabled=true;try{const d=await api('/api/stop',{});msg(d.message);clockRevision++;refresh()}catch(e){msg(e.message);clockRevision++;refresh()}}
async function refresh(){const revision=clockRevision;try{const s=await api('/api/status');if(revision!==clockRevision)return;document.getElementById('status').textContent=s.active?'● RECORDING':s.pending?'Save needs attention':s.recovery_blocked?'Recovery required':'Ready';document.getElementById('start').disabled=recordingStartPending||s.active||s.pending||s.recovery_blocked;document.getElementById('preflight-start').disabled=s.active||s.pending||s.recovery_blocked;const testStop=document.getElementById('preflight-stop');testStop.classList.toggle('hidden',!s.preflight||(!s.active&&!s.pending));testStop.disabled=!s.active&&!s.pending;testStop.textContent=s.pending?'Retry test save':'Stop test & save';if(s.preflight)document.getElementById('preflight-message').textContent=s.message;if(s.preflight_result){document.getElementById('preflight-result').textContent=s.preflight_result.message;if(lastTestKey!==s.preflight_result.key){document.getElementById('preflight-message').textContent='Test recording finished.';lastTestKey=s.preflight_result.key;const player=document.getElementById('preflight-player');player.src='/video?id='+encodeURIComponent(lastTestKey);player.classList.remove('hidden')}}const warning=document.getElementById('recording-warning');warning.textContent=s.warning||'';warning.hidden=!s.warning;document.getElementById('clear-empty-recovery').hidden=!s.recovery_blocked;document.getElementById('stop').disabled=!s.active&&!s.pending;document.getElementById('stop').textContent=s.pending?'Retry save':'Stop & save';msg(s.message);document.getElementById('audio-route').textContent=s.audio_route?'Background audio: '+s.audio_route:'';if(s.active){if(recordingClockAnchor===null)beginRecordingClock(typeof s.elapsed_seconds==='number'?s.elapsed_seconds:0)}else if(!recordingStartPending){recordingClockAnchor=null;document.getElementById('clock').textContent='00:00:00'}if(s.hotkey!==lastHotkey){lastHotkey=s.hotkey;showPage('patient');document.getElementById('pid').focus()}}catch(e){document.getElementById('status').textContent=e.message}}
async function loadFiles(){try{const d=await api('/api/files',{q:val('search')});listedRecordings=new Map(d.files.map(f=>[f.key,f]));document.getElementById('files').innerHTML=d.files.length?d.files.map(f=>'<div class="item"><b>'+escapeHtml(f.patient_name||'Name unavailable')+'</b> | Patient ID: '+escapeHtml(f.patient)+'<br>'+escapeHtml(f.name)+'<br><a target="_blank" rel="noopener" href="/video?id='+encodeURIComponent(f.key)+'">Play recording</a> <button data-share-key="'+escapeHtml(f.key)+'">Share / export</button> <button class="danger" data-delete-key="'+escapeHtml(f.key)+'">Delete recording</button></div>').join(''):'No recordings found'}catch(e){document.getElementById('files').textContent=e.message}}
let listedRecordings=new Map();
document.getElementById('files').addEventListener('click',e=>{const button=e.target.closest('[data-share-key]');if(button)reviewShare(button.dataset.shareKey)});
document.getElementById('files').addEventListener('click',e=>{const button=e.target.closest('[data-delete-key]');if(button)deleteRecording(button.dataset.deleteKey,button)});
async function deleteRecording(key,button){const record=listedRecordings.get(key);if(!record)return;const label=(record.patient_name||'Name unavailable')+' | Patient ID: '+record.patient+' | '+record.name;if(!confirm('Permanently delete this video? '+label+'. This cannot be undone. Patient details/history and copies already shared with the family will be kept.'))return;button.disabled=true;try{const d=await api('/api/recordings/delete',{key,confirm:true});await loadFiles();alert(d.message)}catch(e){alert(e.message);button.disabled=false}}
let shareKey=null;
async function reviewShare(key){try{
 const d=await api('/api/recording-details',{key});shareKey=key;
 const container=document.getElementById('share-fields');container.replaceChildren();
 const labels={patient_id:'Patient ID',name:'Patient name',dob:'Baby’s date of birth',scan:'Scan type',notes:'Notes',created_at:'Scan entry saved'};
 if(d.details)for(const [field,label] of Object.entries(labels)){
  const row=document.createElement('label'),input=document.createElement('input'),text=document.createElement('span');
  input.type='checkbox';input.dataset.shareField=field;input.checked=false;
  text.textContent=' '+label+': '+(d.details[field]||'(not entered)');row.append(input,text);container.append(row);
 }
 document.getElementById('share-info').textContent='Video size: '+(d.size_bytes/1024/1024).toFixed(1)+' MB.'+(d.details?'':' No patient details are linked to this recording.');
 document.getElementById('family-message').textContent='';document.getElementById('share-dialog').showModal();loadFamilyDrives();
}catch(e){alert(e.message)}}
async function loadFamilyDrives(){const select=document.getElementById('family-drive');select.replaceChildren(new Option('Select the family USB',''));try{const d=await api('/api/family-usb/drives',{});for(const drive of d.drives)select.add(new Option(drive.label,drive.key));if(!d.drives.length)document.getElementById('family-message').textContent='Connect a separate family USB and refresh. The recording drive is excluded.'}catch(e){document.getElementById('family-message').textContent=e.message}}
async function copyFamilyUsb(){const destination=val('family-drive'),status=document.getElementById('family-message'),button=document.getElementById('family-copy');if(!destination){status.textContent='Select the family USB first';return}button.disabled=true;status.textContent='Copying and verifying video. Keep both USB drives connected...';try{const fields=Array.from(document.querySelectorAll('[data-share-field]:checked')).map(e=>e.dataset.shareField);const d=await api('/api/family-usb/copy',{key:shareKey,destination,fields});status.textContent=d.message}catch(e){status.textContent=e.message}finally{button.disabled=false}}
function downloadShare(format='zip'){if(!shareKey)return;const fields=Array.from(document.querySelectorAll('[data-share-field]:checked')).map(e=>e.dataset.shareField);window.location.href='/export?id='+encodeURIComponent(shareKey)+'&fields='+encodeURIComponent(fields.join(','))+'&format='+encodeURIComponent(format)}
let obsCheckBusy=false,obsLaunchBusy=false,obsRevision=0;
function setObsButton(text,state,detail){const button=document.getElementById('launch-obs');button.textContent=text;button.dataset.state=state;button.title=detail;button.setAttribute('aria-label',text+'. '+detail)}
async function startObs(){
 const button=document.getElementById('launch-obs');
 if(obsLaunchBusy)return;
 if(button.dataset.state==='connected'){checkObs();return}
 if(button.dataset.state==='error'){alert(button.title);checkObs();return}
 obsLaunchBusy=true;obsRevision++;button.disabled=true;
 setObsButton('OBS: Starting...','starting','Starting OBS minimized to the system tray');
 try{const d=await api('/api/obs-start',{});setObsButton('OBS: Starting...','starting',d.message);setTimeout(checkObs,2000)}
 catch(e){setObsButton('OBS: Start failed','stopped',e.message);alert(e.message)}
 finally{obsLaunchBusy=false;button.disabled=false}
}
async function checkObs(){
 if(obsCheckBusy||obsLaunchBusy)return;obsCheckBusy=true;const revision=obsRevision;
 try{const h=await api('/api/obs-health');if(revision!==obsRevision)return;
 if(h.websocket_connected)setObsButton('OBS: Connected','connected',h.message);
 else if(h.obs_running===false)setObsButton('Start OBS - not running','stopped',h.message);
 else setObsButton('OBS: WebSocket error','error',h.message);
 }catch(e){if(revision===obsRevision)setObsButton('OBS: Check failed','error','Unable to check OBS connection. Check the recorder is running and click to retry.')}
 finally{obsCheckBusy=false}
}
setInterval(()=>{if(!document.hidden)checkObs()},5000);checkObs();
async function clearEmptyRecovery(){if(!confirm('Clear this interrupted session only if OBS is stopped and no recording files exist? Patient details will be kept.'))return;try{const d=await api('/api/recovery/clear-empty',{confirm:true});msg(d.message);refresh()}catch(e){alert(e.message)}}
let loadedPatient=null;
function rememberPatient(id){loadedPatient={id,patient_id:val('pid').trim(),test:document.getElementById('test').checked}}
async function patientRequest(path,data){try{return await api(path,data)}catch(e){if(e.data&&e.data.code==='duplicate_patient_id'){
 const existing=e.data.existing;
 if(!confirm('Patient ID '+existing.patient_id+' already belongs to '+(existing.name||'a saved patient')+'. Continue using this existing patient ID? Earlier details and recordings will be kept. Choose Cancel if this is a different family.'))throw Error('Patient ID already exists. Load the correct patient or choose a different ID.');
 data.expected_record_id=existing.id;const result=await api(path,data);return result;
 }throw e}}
const spotifyPlayIcon='<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>',spotifyPauseIcon='<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false" fill="currentColor"><path d="M6 5h4v14H6zm8 0h4v14h-4z"/></svg>';
function setSpotifyControlsDisabled(disabled){for(const id of ['spotify-previous','spotify-control','spotify-next'])document.getElementById(id).disabled=disabled}
let spotifyCheckBusy=false,spotifyControlBusy=false,spotifyRevision=0,spotifyPlaying=false,spotifySessionAvailable=false;
function showSpotify(status){const button=document.getElementById('spotify-status'),control=document.getElementById('spotify-control');spotifyPlaying=status.playing===true;spotifySessionAvailable=!!status.session_available;const state=spotifyPlaying?'Playing':status.playing===false?'Paused / stopped':'Check needed';const track=[status.title,status.artist].filter(Boolean).join(' - ');button.textContent='Spotify: '+state+(track?' | '+track:'');button.style.backgroundColor=spotifyPlaying?'#e0efdf':'#fff0d9';button.style.color=spotifyPlaying?'#305331':'#795020';button.title=(track?track+'. ':'')+status.message+(spotifyPlaying?'':' Recording is still allowed.');control.innerHTML=spotifyPlaying?spotifyPauseIcon:spotifyPlayIcon;control.title=spotifyPlaying?'Pause':'Play';control.setAttribute('aria-label',control.title);setSpotifyControlsDisabled(spotifyControlBusy||!spotifySessionAvailable)}
async function checkSpotify(){if(spotifyCheckBusy||spotifyControlBusy)return;spotifyCheckBusy=true;const revision=spotifyRevision;try{const status=await api('/api/music-health');if(revision===spotifyRevision)showSpotify(status)}catch(e){if(revision===spotifyRevision)showSpotify({playing:null,session_available:false,message:'Could not check Spotify. Recording is still allowed.'})}finally{spotifyCheckBusy=false}}
async function controlSpotify(requested='toggle'){if(spotifyControlBusy||!spotifySessionAvailable)return;const action=requested==='toggle'?(spotifyPlaying?'pause':'play'):requested;spotifyControlBusy=true;spotifyRevision++;setSpotifyControlsDisabled(true);try{const d=await api('/api/spotify/control',{action});document.getElementById('spotify-status').title=d.message;setTimeout(checkSpotify,500)}catch(e){alert(e.message)}finally{spotifyControlBusy=false;setSpotifyControlsDisabled(!spotifySessionAvailable)}}
setInterval(()=>{if(!document.hidden)checkSpotify()},5000);checkSpotify();
function patientData(){const expected=loadedPatient&&loadedPatient.patient_id.toLowerCase()===val('pid').trim().toLowerCase()&&loadedPatient.test===document.getElementById('test').checked?loadedPatient.id:null;return {expected_record_id:expected,patient_id:val('pid').trim(),name:val('name'),dob:val('dob'),scan:val('scan'),notes:val('notes'),test:document.getElementById('test').checked}}
async function savePatient(){try{const d=await patientRequest('/api/patients/save',patientData());rememberPatient(d.record_id);document.getElementById('patient-message').textContent=d.message}catch(e){document.getElementById('patient-message').textContent=e.message}}
async function generatePatientId(){const status=document.getElementById('patient-message'),button=document.getElementById('generate-pid');
 if(loadedPatient){status.textContent='Select New patient before generating an ID for a different family.';return}
 if(val('pid').trim()&&!confirm('Replace the entered patient ID with a newly generated ID?'))return;
 const previous=val('pid'),testMode=document.getElementById('test').checked;button.disabled=true;
 try{const d=await api('/api/patients/generate-id',{test:testMode});if(val('pid')!==previous||loadedPatient||document.getElementById('test').checked!==testMode)return;document.getElementById('pid').value=d.patient_id;status.textContent='New patient ID generated. Enter the details, then save or start recording.'}catch(e){status.textContent=e.message}finally{button.disabled=false}}
async function newPatient(){clearPatient();await generatePatientId()}
function clearPatient(){loadedPatient=null;for(const k of ['pid','name','dob','notes'])document.getElementById(k).value='';document.getElementById('scan').selectedIndex=0;document.getElementById('patient-history').textContent='';document.getElementById('patient-message').textContent='';document.getElementById('pid').focus()}
async function findPatients(){const container=document.getElementById('patient-results');try{
 const testMode=document.getElementById('test').checked;const d=await api('/api/patients/search',{q:val('patient-search'),test:testMode,include_archived:document.getElementById('include-archived').checked});
 container.replaceChildren();if(!d.records.length)container.textContent='No patients found';
 for(const record of d.records){
 const row=document.createElement('p'),button=document.createElement('button');button.textContent=record.patient_id+' | '+(record.name||'Name not entered')+(record.archived?' (archived)':'');
 button.onclick=()=>{if(record.archived){alert('Restore this archived patient before loading their details.');return}loadPatient(record.patient_id)};
 const archive=document.createElement('button');archive.textContent=record.archived?'Restore patient':'Archive patient';archive.style.marginLeft='8px';archive.onclick=()=>archivePatient(record,testMode,!record.archived,archive);row.append(button,archive);
 if(record.archived){const remove=document.createElement('button');remove.textContent='Permanently delete';remove.className='danger';remove.style.marginLeft='8px';remove.onclick=()=>deletePatient(record,testMode,remove);row.append(remove)}container.append(row);
 }
}catch(e){container.textContent=e.message}}
async function archivePatient(record,testMode,archived,button){
 if(!confirm((archived?'Archive ':'Restore ')+record.patient_id+' | '+(record.name||'Name not entered')+'? Details and recordings will be kept.'))return;
 button.disabled=true;try{const d=await api('/api/patients/archive',{patient_id:record.patient_id,record_id:record.id,test:testMode,archived,confirm:true});
 if(archived&&val('pid').trim().toLowerCase()===record.patient_id.toLowerCase()&&document.getElementById('test').checked===testMode)clearPatient();
 document.getElementById('patient-message').textContent=d.message;await findPatients();
 }catch(e){document.getElementById('patient-message').textContent=e.message;button.disabled=false}}
async function deletePatient(record,testMode,button){
 const label=(testMode?'TEST patient: ':'Patient: ')+record.patient_id+' | '+(record.name||'Name not entered');
 if(!confirm(label+' - Delete all saved patient details and scan history? This cannot be undone. Video recordings will be kept, but their linked patient information will be removed. Previously exported copies and backups are unaffected.'))return;
 const typed=prompt('Type patient ID '+record.patient_id+' to permanently delete their stored details and history.');if(typed===null||typed.toLowerCase()!==record.patient_id.toLowerCase())return;
 button.disabled=true;
 try{const d=await api('/api/patients/delete',{patient_id:record.patient_id,record_id:record.id,test:testMode,confirm:true,confirm_patient_id:typed});
 if(val('pid').trim().toLowerCase()===record.patient_id.toLowerCase()&&document.getElementById('test').checked===testMode)clearPatient();
 document.getElementById('patient-message').textContent=d.message;await findPatients();
 }catch(e){document.getElementById('patient-message').textContent=e.message;button.disabled=false}
}
async function loadPatient(patient_id){try{
 const d=await api('/api/patients/search',{patient_id,test:document.getElementById('test').checked});if(!d.records.length)throw Error('Patient record not found');
 const record=d.records[0];if(record.archived)throw Error('Restore this archived patient before loading details');document.getElementById('pid').value=record.patient_id;rememberPatient(record.id);
 for(const k of ['name','notes'])document.getElementById(k).value=record[k];
 const birth=(record.dob||'').split('/');document.getElementById('dob').value=birth.length===3?[birth[2],birth[1].padStart(2,'0'),birth[0].padStart(2,'0')].join('-'):'';
 const select=document.getElementById('scan');if(!Array.from(select.options).some(o=>o.value===record.scan)){select.add(new Option(record.scan,record.scan))}select.value=record.scan;
 document.getElementById('patient-history').textContent='Saved history (latest 200 entries)\\n\\n'+d.records.map(r=>r.created_at+' | '+r.scan+'\\nName: '+r.name+' | Baby’s date of birth: '+r.dob+'\\nNotes: '+r.notes+'\\n'+(r.recording?'Recording: '+r.recording:'Details saved; no recording linked')).join('\\n\\n');
 document.getElementById('patient-message').textContent='Patient details loaded';
}catch(e){document.getElementById('patient-message').textContent=e.message}}
function escapeHtml(s){return s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
let savedCaptureDevice={id:'',name:''};
async function loadSettings(){const d=await api('/api/settings');savedCaptureDevice={id:d.video_device_id||'',name:d.video_device_name||''};for(const [k,stored] of Object.entries(d)){const el=document.getElementById(k);if(el){let value=stored;if(k==='video_source'&&value==='@device')value='@device:'+savedCaptureDevice.id;if(el.type==='checkbox')el.checked=value;else{if(el.tagName==='SELECT'&&!Array.from(el.options).some(o=>o.value===value))el.add(new Option(k==='video_source'&&stored==='@device'?(savedCaptureDevice.name||'Saved capture device'):value||'Automatic',value));el.value=value}}}applyPerformanceProfile(d.performance_profile);await loadRecordingDrives();await loadObsSources()}
async function loadRecordingDrives(){const select=document.getElementById('recording_volume'),selected=select.value,message=document.getElementById('recording-drive-message');try{const d=await api('/api/recording-drives');select.replaceChildren(new Option('Select a recording USB',''));for(const drive of d.drives)select.add(new Option(drive.label,drive.id));if(selected&&!d.drives.some(d=>d.id===selected))select.add(new Option('Saved USB (disconnected)',selected));select.value=selected;message.textContent='The selected USB is remembered even if its drive letter changes.'}catch(e){message.textContent=e.message}}
async function loadObsSources(){const selected={video_source:val('video_source'),audio_source:val('audio_source'),ultrasound_scene:val('ultrasound_scene')};const message=document.getElementById('source-message');try{
 const sources=await api('/api/obs-sources');const video=document.getElementById('video_source');video.replaceChildren();
 const devices=document.createElement('optgroup');devices.label='Video capture devices';for(const device of sources.devices||[])devices.append(new Option(device.name,'@device:'+device.id));if(devices.children.length)video.append(devices);
 const inputs=document.createElement('optgroup');inputs.label='Existing OBS sources';for(const name of sources.video)inputs.append(new Option(name+' (OBS source)',name));video.append(inputs);
 let value=selected.video_source;const match=sources.video.find(name=>name.toLowerCase()===value.toLowerCase());if(match)value=match;
 if(value&&!Array.from(video.options).some(option=>option.value===value)){const label=value.startsWith('@device:')?(savedCaptureDevice.name||'Selected capture device'):value;video.add(new Option(label+' (unavailable)',value))}video.value=value;
 for(const [id,names] of [['audio_source',sources.audio],['ultrasound_scene',sources.scenes]]){const select=document.getElementById(id);select.replaceChildren();select.add(new Option(id==='audio_source'?'Automatic Spotify / system output':'Current OBS scene',''));if(id==='audio_source')select.add(new Option('Use selected video source audio','@video'));for(const name of names)select.add(new Option(name,name));let value=selected[id];const match=names.find(name=>name.toLowerCase()===value.toLowerCase());if(match)value=match;if(value&&!(id==='audio_source'&&value==='@video')&&!names.includes(value))select.add(new Option(value+' (unavailable)',value));select.value=value}
 message.textContent=sources.device_warning||'Devices and sources loaded from OBS. Select your video/audio and Save settings.';
 }catch(e){message.textContent=e.message}}
async function uploadLogos(){const data=new FormData();for(const field of ['intro_image','watermark_image','outro_image']){const file=document.getElementById(field+'_upload').files[0];if(file)data.append(field,file)}try{const response=await fetch('/api/branding/upload',{method:'POST',headers:{'X-Recorder-Token':token},body:data});const d=await response.json();if(!response.ok)throw Error(d.error||'Logo upload failed');if(data.has('outro_image'))document.getElementById('outro_mode').value='image';document.getElementById('settings-message').textContent=d.message}catch(e){document.getElementById('settings-message').textContent=e.message}}
async function saveSettings(){const d={};for(const k of ['performance_profile','recording_volume','obs_host','obs_port','obs_password','min_free_gb','branding_enabled','ultrasound_scene','music_capture','video_source','audio_source','outro_mode']){const el=document.getElementById(k);d[k]=el.type==='checkbox'?el.checked:el.value}if(d.video_source.startsWith('@device:')){d.video_device_id=d.video_source.slice('@device:'.length);d.video_device_name=document.getElementById('video_source').selectedOptions[0].textContent.replace(' (unavailable)','');d.video_source='@device'}try{await api('/api/settings',d);applyPerformanceProfile(d.performance_profile);refreshUsbReadiness();savedCaptureDevice={id:d.video_device_id||savedCaptureDevice.id,name:d.video_device_name||savedCaptureDevice.name};checkObs();document.getElementById('settings-message').textContent='Settings saved'}catch(e){document.getElementById('settings-message').textContent=e.message}}
let previewBusy=false,previewUrl=null,previewMode='low';
function applyPerformanceProfile(profile){previewMode=profile==='low'?'low':'full';document.getElementById('preview-preset').textContent=profile==='low'?'Low performance PC: 540p preview, up to 2 fps.':'High performance PC: 1080p preview, up to 15 fps.'}
async function updatePreview(){
 if(previewBusy||currentPage!=='preview'||document.hidden)return;
 previewBusy=true;
 try{
  const r=await fetch('/api/preview?mode='+previewMode,{headers:{'X-Recorder-Token':token},signal:AbortSignal.timeout(7000)});
  if(!r.ok)throw Error();
  const url=URL.createObjectURL(await r.blob());
  const img=document.getElementById('preview');
  img.src=url;if(previewUrl)URL.revokeObjectURL(previewUrl);previewUrl=url;
  img.style.visibility='visible';document.getElementById('preview-status').textContent='Live OBS snapshots - '+(previewMode==='low'?'540p, up to 2 fps':'1080p, up to 15 fps');
 }catch(e){document.getElementById('preview').style.visibility='hidden';document.getElementById('preview-status').textContent='Preview unavailable. Check OBS connection. Retrying...';}
 finally{previewBusy=false;}
}
async function previewLoop(){await updatePreview();const fps={low:2,hd:10,full:15}[previewMode]||2;setTimeout(previewLoop,currentPage==='preview'&&!document.hidden?1000/fps:500)}
setInterval(refresh,1000);setTimeout(previewLoop,100);refresh();loadSettings();loadFiles();const requestedPage=new URLSearchParams(location.search).get('page');if(['patient','saved','settings'].includes(requestedPage))showPage(requestedPage);
</script></body></html>'''
@app.get('/')
def home():
    response=make_response(render_template_string(HTML.replace('__TOKEN__',json.dumps(TOKEN))))
    response.set_cookie('recorder_token',TOKEN,httponly=True,samesite='Strict')
    return response
@app.get('/api/settings')
def get_settings():return jsonify({**settings,'obs_password':''})

@app.post('/api/branding/upload')
def upload_branding():
    request.max_content_length=272*1024*1024
    with lock:
        if state['active'] or state['pending'] or state.get('recovery_blocked'):
            return jsonify(error='Finish the recording or recovery before changing branding media'),409
        candidate=settings.copy()
        temporary=None
        try:
            directory=ROOT/'branding'
            directory.mkdir(exist_ok=True)
            found=False
            for field in ('intro_image','watermark_image','outro_image'):
                supplied_file=request.files.get(field)
                if supplied_file is None or not supplied_file.filename:continue
                found=True
                suffix=Path(supplied_file.filename).suffix.lower()
                is_video=field=='intro_image' and suffix in obs_branding.VIDEO_EXTENSIONS
                if is_video:
                    temporary=directory/(secrets.token_hex(16)+'.tmp')
                    size=0
                    with temporary.open('xb') as output:
                        while chunk:=supplied_file.stream.read(1024*1024):
                            size+=len(chunk)
                            if size>250*1024*1024:raise ValueError()
                            output.write(chunk)
                        output.flush();os.fsync(output.fileno())
                    with temporary.open('rb') as source:header=source.read(64)
                    if suffix in ('.mkv','.webm'):
                        if not header.startswith(b'\x1aE\xdf\xa3'):raise ValueError()
                    elif header[4:8] not in (b'ftyp',b'moov',b'mdat',b'wide'):
                        raise ValueError()
                    path=temporary.with_suffix(suffix)
                    temporary.replace(path)
                    temporary=None
                else:
                    raw=supplied_file.read(10*1024*1024+1)
                    if len(raw)>10*1024*1024:raise ValueError()
                    import io
                    with Image.open(io.BytesIO(raw)) as supplied:
                        if supplied.format not in ('PNG','JPEG') or max(supplied.size)>8192:raise ValueError()
                        extension='.png' if supplied.format=='PNG' else '.jpg'
                        supplied.verify()
                    path=directory/(secrets.token_hex(16)+extension)
                    with path.open('xb') as output:output.write(raw)
                candidate[field]=str(path)
                if field=='outro_image':candidate['outro_mode']='image'
            if not found:return jsonify(error='Select an opening/closing image or video and/or a corner-logo image'),400
            recording_recovery.write_json(CFG,candidate)
            settings.update(candidate)
            return jsonify(message='Branding media saved. Save settings to enable the sequence. Intro videos play in full. A fixed outro image is used when selected; use intro clips up to 60 seconds.')
        except (ValueError,OSError,Image.DecompressionBombError):
            return jsonify(error='Could not save branding media. Use PNG/JPEG images up to 10 MB, or an opening/closing MP4/MOV/WebM/MKV video up to 250 MB. Check APPDATA space and write access.'),400
        finally:
            if temporary is not None:
                try:temporary.unlink(missing_ok=True)
                except OSError:pass

@app.errorhandler(413)
def upload_too_large(error):
    return jsonify(error='Upload is too large. Use a video up to 250 MB and an image up to 10 MB.'),413
@app.post('/api/settings')
def set_settings():
    with lock:
        if state['active'] or state['pending'] or state.get('recovery_blocked'):return jsonify(error='Finish saving the current recording before changing settings'),409
        d=request.get_json()
        if not isinstance(d,dict):return jsonify(error='Settings must be an object'),400
        candidate=settings.copy()
        for k in DEFAULT:
            if k in d and k not in ('intro_image','watermark_image','outro_image','branding_key','recording_drive_name') and (k!='obs_password' or d[k]):candidate[k]=d[k]
        try:
            candidate['obs_port']=int(candidate['obs_port'])
            candidate['min_free_gb']=int(candidate['min_free_gb'])
            if not 1<=candidate['obs_port']<=65535 or candidate['min_free_gb']<0:raise ValueError()
            if not all(isinstance(candidate[k],str) and candidate[k].strip() for k in ['volume_label','obs_host']):raise ValueError()
            selected=candidate['recording_volume']
            if not isinstance(selected,str) or (selected and not re.fullmatch(r'[0-9A-F]{8}',selected)):raise ValueError()
            if selected!=settings.get('recording_volume',''):
                matches=[drive for drive in family_usb_drives() if format(drive["serial"], "08X")==selected]
                if len(matches)!=1:return jsonify(error='Connect the selected recording USB and refresh the drive list.'),400
                candidate['recording_drive_name']=matches[0]['label']
            if candidate['outro_mode'] not in ('same','image'):raise ValueError()
            if candidate['music_capture'] not in ('spotify','system'):raise ValueError()
            if candidate['performance_profile'] not in ('low','high'):raise ValueError()
            if not isinstance(candidate['video_source'],str) or not candidate['video_source'].strip() or len(candidate['video_source'])>200:raise ValueError()
            if candidate['video_source']=='@device' and (not isinstance(candidate['video_device_id'],str) or not candidate['video_device_id'].strip() or len(candidate['video_device_id'])>4096):raise ValueError()
            if not isinstance(candidate['video_device_name'],str) or len(candidate['video_device_name'])>512:raise ValueError()
            if not isinstance(candidate['audio_source'],str) or len(candidate['audio_source'])>200:raise ValueError()
            if not isinstance(candidate['obs_password'],str) or not isinstance(candidate['branding_enabled'],bool) or not isinstance(candidate['ultrasound_scene'],str) or len(candidate['ultrasound_scene'])>200:raise ValueError()
        except (ValueError,TypeError):return jsonify(error='Enter valid settings, port (1?65535) and non-negative free space'),400
        try:
            temporary=CFG.with_suffix('.tmp')
            temporary.write_text(json.dumps(candidate,indent=2),encoding='utf-8')
            temporary.replace(CFG)
        except OSError:return jsonify(error='Could not save settings. Existing settings remain in use.'),500
        settings.update(candidate)
        return jsonify(ok=True)
health_meter=recording_health.AudioMeter()
health_stats_lock=threading.Lock()
health_previous_stats=None

@app.get('/api/recording-health')
def get_recording_health():
    global health_previous_stats
    with lock:
        active=state['active']
        audio=dict((state.get('sequence') or {}).get('audio') or {}) if active else {}
        config=settings.copy()
    connection=None
    try:
        connection=client()
        stats=connection.get_stats()
        with health_stats_lock:
            values,warnings=recording_health.stats_summary(stats,health_previous_stats)
            health_previous_stats=values
        audio_status='not_recording'
        if audio:
            if audio.get('deferred'):audio_status='intro'
            elif connection.get_input_mute(audio['source']).input_muted:
                audio_status='muted';warnings.append('The selected recording audio is muted in OBS.')
            else:
                try:
                    health_meter.ensure(config)
                    audio_status=health_meter.status(audio['source'])
                except Exception:audio_status='unknown'
                if audio_status=='quiet':warnings.append('No background audio signal detected in OBS. Check the selected source and play back a test.')
        return jsonify(connected=True,active=active,audio=audio_status,stats=values,warnings=warnings,
                       video='Check the live preview to confirm the ultrasound picture.')
    except Exception:
        return jsonify(connected=False,audio='unknown',stats={},warnings=['Recording health unavailable. Check the OBS connection.']),503
    finally:
        if connection is not None:
            try:connection.disconnect()
            except Exception:pass


def finish_preflight(token):
    with app.app_context(),lock:
        if state.get('preflight_id')!=token or not state['active'] or not state.get('test'):return
        stop()


def schedule_preflight(token):
    timer=threading.Timer(10,finish_preflight,args=(token,))
    timer.daemon=True
    timer.start()

@app.get('/api/status')
def status():
    with lock:
        elapsed=str(datetime.now()-state['started']).split('.')[0] if state['active'] and state['started'] else None
        return jsonify(active=state['active'],elapsed=elapsed,elapsed_seconds=max(0,(datetime.now()-state['started']).total_seconds()) if state['active'] and state['started'] else None,hotkey=state['hotkey'],message=state['message'],pending=bool(state['pending']),warning=' '.join(filter(None,[state.get('warning',''),state.get('storage_warning','')])),recovery_blocked=state.get('recovery_blocked',False),audio_route=((state.get('sequence') or {}).get('audio') or {}).get('route',''),preflight=bool(state.get('preflight_id')),preflight_result=state.get('preflight_result'))
@app.post('/api/start')
def start():
    requested_at=datetime.now()
    with lock:
        if state['active'] or state['pending'] or state.get('recovery_blocked') or family_copy_lock.locked():return jsonify(error='Finish the current recording or family USB copy before starting another'),409
        d=request.get_json()
        if not isinstance(d,dict) or not isinstance(d.get('test',False),bool):return jsonify(error='Invalid recording request'),400
        if not isinstance(d.get('preflight',False),bool):return jsonify(error='Invalid test request'),400
        preflight=secrets.token_hex(12) if d.get('preflight') else None
        if preflight:d={'test':True,'patient_id':'TEST-'+preflight,'name':'Pre-session test','scan':'Pre-session test'}
        try:
            test=d.get('test',False);pid=check_id(str(d.get('patient_id','')).strip() or ('TEST' if test else ''))
            details=patient_store.validate_details(d)
            root=base_dir()
            if shutil.disk_usage(root.parent).free < int(settings['min_free_gb'])*1024**3:raise OSError()
            staging=root/'_staging'/secrets.token_hex(16);staging.mkdir(parents=True,exist_ok=False)
            dest=root/('Test Recordings' if test else 'Patients') if test else root/'Patients'/pid
            dest.mkdir(parents=True,exist_ok=True)
            try:
                c=client()
                c.get_version()
            except Exception:
                return jsonify(error='Cannot start: OBS connection failed. '+obs_health()['message']),503
            if c.get_record_status().output_active:raise RuntimeError('OBS is already recording')
            c.set_record_directory(str(staging))
            actual=Path(c.get_record_directory().record_directory)
            if os.path.normcase(str(actual))!=os.path.normcase(str(staging)):raise RuntimeError('OBS rejected USB recording folder')
            music_start_warning=spotify_status.prepare_for_recording()
            sequence=obs_branding.prepare_capture(c,settings,test)
            record_root=patient_root(root,test)
            record_id=patient_store.save(record_root,pid,details,d.get('expected_record_id'),check_duplicate=True)
            state.update(preflight_id=preflight,preflight_result=None,music_warning=music_start_warning)
            state.update(record_root=record_root,record_id=record_id,volume=volume_id(root),filed=False,planned=None,warning='',sequence=sequence,branding_warning='',outro_done=False)
            state.update(active=True,patient=pid,test=test,started=requested_at,destination=dest,staging=staging,message='Recording started')
            try:
                recording_recovery.begin(RECOVERY_POINTER,staging,session_data())
            except Exception:
                state.update(active=False,recovery_blocked=RECOVERY_POINTER.exists())
                raise
            if sequence and sequence.get('audio'):
                try:obs_audio.apply(c,sequence['audio'],defer=True)
                except Exception:
                    try:obs_audio.restore(c,sequence['audio'])
                    except Exception:pass
                    state.update(active=False,recovery_blocked=True)
                    return jsonify(error='Could not activate the selected audio capture. Check the OBS mixer. Recording was not started; clear the empty recovery session after checking OBS.'),503
            if sequence:
                try:obs_branding.show_intro(c,sequence)
                except Exception:
                    try:obs_audio.restore(c,sequence.get('audio'))
                    except Exception:pass
                    state.update(active=False,pending=None,recovery_blocked=True)
                    return jsonify(error='Could not prepare the recording scene. OBS recording was not started. Check OBS; the empty session can be cleared through recovery.'),503
            try:
                c.start_record()
            except Exception:
                state['message']='OBS start could not be confirmed. Use Stop & save before starting another recording.'
                return jsonify(error=state['message']),503
            state['warning']=' '.join(filter(None,[music_start_warning,spotify_status.warning(spotify_status.check()),((sequence or {}).get('audio') or {}).get('warning','')]))
            def restart_recording_music():
                warning=spotify_status.restart_for_recording()
                state['music_warning']=warning
                state['warning']=' '.join(filter(None,[warning,spotify_status.warning(spotify_status.check()),((sequence or {}).get('audio') or {}).get('warning','')]))
            if sequence:
                state['message']='Recording opening logo...'
                try:obs_branding.enter_live(c,sequence,restart_recording_music)
                except Exception:
                    if preflight:schedule_preflight(preflight)
                    state['message']='Recording is running, but the fade to ultrasound failed. Select the ultrasound scene in OBS, or use Stop & save.'
                    return jsonify(error=state['message']),503
                state['message']='Recording ultrasound'
            else:restart_recording_music()
            if preflight:
                state['message']='Recording pre-session test. Automatic stop after 10 seconds of live recording.'
                schedule_preflight(preflight)
            return jsonify(message=state['message'],record_id=record_id)
        except patient_store.DuplicatePatientError as error:return duplicate_patient_response(error)
        except patient_store.ArchivedPatientError:return jsonify(error='This patient ID is archived. Restore it before recording.'),409
        except patient_store.PatientChangedError:return jsonify(error='Patient changed or was deleted. Search again before recording.'),409
        except (patient_store.PatientValidationError,obs_branding.BrandingError) as error:
            state['message']=str(error)
            return jsonify(error=state['message']),400
        except ValueError:return jsonify(error='Enter a valid patient ID; Windows reserved names are not allowed'),400
        except Exception:return jsonify(error='Cannot start recording. Check OBS connection and USB drive/free space.'),400
class RecordingSaveError(Exception):
    pass

def move_recording(source,target):
    # OBS/antivirus can briefly keep the finalized file open on Windows.
    for attempt in range(5):
        try:
            source.rename(target)
            return
        except PermissionError as error:
            if getattr(error,'winerror',None) not in (32,33) or attempt==4:raise
            time.sleep(0.25)

@app.post('/api/stop')
def stop():
    with lock:
        if not state['active'] and not state['pending']:return jsonify(error='Not recording'),409
        if state['active']:
            try:
                c=client()
                sequence=state.get('sequence')
                if sequence and not state.get('outro_done'):
                    state['message']='Recording closing logo...'
                    try:
                        obs_branding.show_outro(c,sequence)
                        state['outro_done']=True
                    except Exception:state['branding_warning']=' Closing logo could not be completed.'
                output=Path(c.stop_record().output_path)
                music_stop_warning=spotify_status.pause_after_recording()
                if music_stop_warning:state['branding_warning']=state.get('branding_warning','')+' '+music_stop_warning
                if sequence:
                    try:obs_branding.restore(c,sequence)
                    except Exception:state['branding_warning']=state.get('branding_warning','')+' OBS scene restoration, audio restoration or temporary scene cleanup could not be completed; check OBS.'
            except Exception:
                state['message']='OBS stop could not be confirmed. Check OBS and retry Stop & save. Recording controls remain locked.'
                return jsonify(error=state['message']),503
            state.update(active=False,pending=output)
            try:persist_session()
            except (OSError,ValueError):pass
        audio=(state.get('sequence') or {}).get('audio')
        if audio and not audio.get('restored'):
            audio_client=None
            try:
                audio_client=client()
                obs_audio.restore(audio_client,audio)
            except Exception:
                state['branding_warning']=state.get('branding_warning','')+' Previous OBS audio settings need manual review in the mixer.'
            finally:
                if audio_client is not None:
                    try:audio_client.disconnect()
                    except Exception:pass
        step='checking the recorded file'
        try:
            output=state['pending']
            if not output.is_file():raise RecordingSaveError('OBS reported a file that is not available. Check the recording USB is connected, then retry Stop & save.')
            if output.stat().st_size==0:raise RecordingSaveError('The recorded file is empty or still being finalized. Wait a few seconds and retry Stop & save.')
            if output.suffix.lower()!='.mp4':raise RecordingSaveError('OBS saved a non-MP4 recording. Original retained in staging. Set OBS recording format to Hybrid MP4 for future recordings; this file needs manual recovery.')
            if not state.get('filed'):
                if not output.resolve().is_relative_to(state['staging'].resolve()):raise RecordingSaveError('OBS saved outside the assigned staging folder. Original has not been moved. Check the OBS recording path; manual recovery is required.')
                if not state['destination'].is_dir():raise RecordingSaveError('The patient destination folder is unavailable. Reconnect the original USB and restore the destination folder, then retry Stop & save.')
                if state['destination'].resolve().parent != (state['staging'].parent.parent / ('Patients' if not state['test'] else '')).resolve():raise RecordingSaveError('Patient destination does not match this recording. Original retained; manual recovery is required.')
                step='moving the video into the patient folder'
                stamp=state['started'].strftime('%Y-%m-%d_%H%M%S')
                i=0
                while True:
                    suffix=f'_{i}' if i else ''
                    target=state['destination']/f'{state["patient"]}_{stamp}{suffix}.mp4'
                    try:
                        if target.exists():raise FileExistsError()
                        state['planned']=target
                        persist_session()
                        move_recording(output,target)
                        break
                    except FileExistsError:i+=1
                state.update(pending=target,filed=True)
                persist_session()
            step='linking the saved video to the patient database'
            patient_store.attach_recording(state['record_root'],state['record_id'],str(state['pending'].relative_to(state['staging'].parent.parent)),state.get('volume'))
            recording_recovery.finish(RECOVERY_POINTER,state['staging'])
            if state.get('preflight_id'):
                key=secrets.token_urlsafe(24)
                path=state['pending'].resolve()
                playback_files[key]=path
                metadata=path.stat()
                recording_versions[key]=(metadata.st_dev,metadata.st_ino,metadata.st_size,metadata.st_mtime_ns)
                state['preflight_result']={'key':key,'message':'Test saved. Play it back to check picture, music, intro and outro.'}
                state['preflight_id']=None
            state.update(pending=None,patient=None,planned=None,warning='',message='Recording saved successfully'+state.get('branding_warning',''))
            return jsonify(message=state['message'])
        except RecordingSaveError as error:
            state['message']=str(error)
            return jsonify(error=state['message']),500
        except Exception as error:
            if step=='linking the saved video to the patient database':
                detail='The video is already in the patient folder, but its database link could not be saved. Check APPDATA space and write access, then retry Stop & save.'
            elif isinstance(error,PermissionError):
                detail='Windows denied access while '+step+'. OBS or another program may still have the file open. Wait a few seconds and retry Stop & save; also check USB write protection.'
            else:
                detail='Failed while '+step+'. Check the original USB connection, free space and write access, then retry Stop & save.'
            state['message']=detail+' The original video has not been deleted or overwritten.'
            return jsonify(error=state['message']),500
@app.post('/api/files')
def files():
    try:
        base=base_dir();q=str((request.get_json() or {}).get('q','')).casefold();results=[]
        for folder in [base/'Patients',base/'Test Recordings']:
            if not folder.exists():continue
            names=patient_store.recording_names(patient_root(base,folder.name=='Test Recordings'),volume_id(base))
            for f in folder.rglob('*.mp4'):
                if not f.is_file() or not f.resolve().is_relative_to(base.resolve()):continue
                rel=f.relative_to(base);patient=rel.parts[1] if rel.parts[0]=='Patients' else 'TEST'
                saved=names.get(str(rel),{})
                patient=saved.get('patient_id',patient)
                patient_name=saved.get('name','')
                if q and q not in patient.casefold() and q not in patient_name.casefold():continue
                key=secrets.token_urlsafe(24)
                with lock:
                    if len(playback_files)>=2000:
                        playback_files.clear()
                        recording_versions.clear()
                    playback_files[key]=f.resolve()
                    metadata=f.stat()
                    recording_versions[key]=(metadata.st_dev,metadata.st_ino,metadata.st_size,metadata.st_mtime_ns)
                results.append({'key':key,'patient':patient,'patient_name':patient_name,'name':f.name,'mtime':f.stat().st_mtime})
        results.sort(key=lambda x:x['mtime'],reverse=True)
        return jsonify(files=results[:200])
    except Exception:return jsonify(error='Cannot read recordings. Check the USB drive.'),400
@app.get('/video')
def video():
    try:
        base=base_dir().resolve()
        with lock:p=playback_files.get(request.args.get('id',''))
        if p is None:abort(404)
        if not p.is_relative_to(base) or not p.is_file() or p.suffix.lower()!='.mp4' or '_staging' in p.parts:abort(404)
        return send_file(p,mimetype='video/mp4',conditional=True)
    except (OSError,ValueError):abort(404)

def selected_recording(key):
    base=base_dir().resolve()
    with lock:path=playback_files.get(key)
    if path is None:raise ValueError()
    path=path.resolve()
    if not path.is_relative_to(base) or not path.is_file() or path.suffix.lower()!='.mp4':raise ValueError()
    relative=path.relative_to(base)
    if relative.parts[0] not in ('Patients','Test Recordings'):raise ValueError()
    return base,path,relative

@app.post('/api/recordings/delete')
def delete_saved_recording():
    d=request.get_json()
    if not isinstance(d,dict) or d.get('confirm') is not True or not isinstance(d.get('key'),str):
        return jsonify(error='Confirm the selected video deletion first'),400
    with lock:
        if state['active'] or state['pending'] or state.get('recovery_blocked') or family_copy_lock.locked():
            return jsonify(error='Finish recording, saving, recovery or USB copying before deleting videos'),409
        try:
            base,path,relative=selected_recording(d['key'])
            metadata=path.stat()
            if recording_versions.get(d['key'])!=(metadata.st_dev,metadata.st_ino,metadata.st_size,metadata.st_mtime_ns):
                return jsonify(error='This video changed since it was listed. Refresh Saved recordings and confirm again.'),409
            patient_store.delete_recording(patient_root(base,relative.parts[0]=='Test Recordings'),str(relative),path,volume_id(base))
            for key,value in list(playback_files.items()):
                if value==path:
                    playback_files.pop(key,None)
                    recording_versions.pop(key,None)
            return jsonify(message='Video recording deleted. Patient details and history have been kept.')
        except ValueError:return jsonify(error='Recording unavailable. Refresh the saved recordings list.'),404
        except Exception:return jsonify(error='Deletion could not be confirmed. Refresh the list; if the video remains, close its player and check USB write access before retrying. Patient details have been kept.'),503

@app.post('/api/recording-details')
def recording_details():
    d=request.get_json()
    if not isinstance(d,dict):return jsonify(error='Invalid recording selection'),400
    try:
        base,path,relative=selected_recording(d.get('key'))
        details=patient_store.for_recording(patient_root(base,relative.parts[0]=='Test Recordings'),str(relative),volume_id(base))
        return jsonify(details=details,size_bytes=path.stat().st_size)
    except ValueError:return jsonify(error='Recording unavailable. Refresh the saved recordings list.'),404
    except Exception:return jsonify(error='Cannot read recording details. Check the USB drive.'),503

SHARE_FIELDS={'patient_id':'Patient ID','name':'Patient name','dob':'Baby’s date of birth',
              'scan':'Scan type','notes':'Notes','created_at':'Scan entry saved'}

def write_email_draft(package,path,text):
    boundary='recorder_'+secrets.token_hex(24)
    def write(value):package.write(value.encode('ascii'))
    write('X-Unsent: 1\r\nSubject: Ultrasound recording\r\nMIME-Version: 1.0\r\n'
          f'Content-Type: multipart/mixed; boundary="{boundary}"\r\n\r\n'
          f'--{boundary}\r\nContent-Type: text/plain; charset=utf-8\r\n'
          'Content-Transfer-Encoding: base64\r\n\r\n')
    package.write(base64.encodebytes(text.encode('utf-8')).replace(b'\n',b'\r\n'))
    write(f'\r\n--{boundary}\r\nContent-Type: video/mp4\r\n'
          'Content-Disposition: attachment; filename="recording.mp4"\r\n'
          'Content-Transfer-Encoding: base64\r\n\r\n')
    with path.open('rb') as source:
        while chunk:=source.read(57*1024):
            package.write(base64.encodebytes(chunk).replace(b'\n',b'\r\n'))
    write(f'\r\n--{boundary}--\r\n')

@app.get('/export')
def export_recording():
    package=None
    try:
        base,path,relative=selected_recording(request.args.get('id'))
        fields=request.args.get('fields','').split(',') if request.args.get('fields') else []
        mode=request.args.get('format','zip')
        if mode not in ('zip','email') or len(fields)>len(SHARE_FIELDS) or any(field not in SHARE_FIELDS for field in fields):
            return jsonify(error='Invalid export options'),400
        details=patient_store.for_recording(patient_root(base,relative.parts[0]=='Test Recordings'),str(relative),volume_id(base))
        text='Miracle of Life Ultrasound Recorder\n\n'
        if details is None:
            text+='No patient details are linked to this recording.\n'
        else:
            text+='\n'.join(f'{SHARE_FIELDS[field]}: {details.get(field, "")}' for field in fields)
            if not fields:text+='No patient details selected for this export.'
        # Keep temporary export packages in APPDATA, with bounded memory use.
        temporary=ROOT/'exports-temp'
        temporary.mkdir(parents=True,exist_ok=True)
        required=path.stat().st_size*(1.4 if mode=='email' else 1)+1024*1024
        if shutil.disk_usage(ROOT).free < required+100*1024**2:
            raise OSError()
        package=tempfile.TemporaryFile(dir=temporary)
        if mode=='email':
            write_email_draft(package,path,text)
        else:
            with zipfile.ZipFile(package,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
                archive.write(path,'recording.mp4')
                archive.writestr('patient-information.txt',text)
        package.seek(0)
        def chunks():
            try:
                while chunk:=package.read(1024*1024):yield chunk
            finally:package.close()
        response=Response(chunks(),mimetype='message/rfc822' if mode=='email' else 'application/zip')
        response.headers['Content-Disposition']='attachment; filename="ultrasound-email.eml"' if mode=='email' else 'attachment; filename="ultrasound-share.zip"'
        response.call_on_close(package.close)
        return response
    except ValueError:
        if package is not None:package.close()
        return jsonify(error='Recording unavailable. Refresh the saved recordings list.'),404
    except Exception:
        if package is not None:package.close()
        return jsonify(error='Export failed. Check the recording USB and free space in APPDATA. Original recording is unchanged.'),503

preview_capture=obs_preview.Preview()

@app.get('/api/preview')
def preview():
    mode=request.args.get('mode','low' if settings['performance_profile']=='low' else 'full')
    if mode not in obs_preview.MODES:return Response(status=400)
    if settings['performance_profile']=='low':mode='low'
    try:
        identity=(settings['obs_host'],settings['obs_port'],settings['obs_password'])
        data=preview_capture.capture(client,identity,mode)
        return Response(data,mimetype='image/jpeg',headers={'Cache-Control':'no-store'})
    except Exception:return Response(status=503)

def hotkey_loop():
    if os.name!='nt':return
    user32=ctypes.windll.user32
    if not user32.RegisterHotKey(None,0xB50,0x4000|0x2|0x4,0x4e):print('Hotkey unavailable: Ctrl+Shift+N') ;return
    class MSG(ctypes.Structure):
        _fields_=[('hwnd',ctypes.c_void_p),('message',ctypes.c_uint),('wParam',ctypes.c_size_t),('lParam',ctypes.c_ssize_t),('time',ctypes.c_ulong),('pt_x',ctypes.c_long),('pt_y',ctypes.c_long)]
    msg=MSG()
    while user32.GetMessageW(ctypes.byref(msg),None,0,0)>0:
        if msg.message==0x312:
            with lock:state['hotkey']+=1
            open_dashboard()


def open_dashboard(path=''):
    host=BIND_HOST if BIND_HOST not in ('0.0.0.0','::') else '127.0.0.1'
    webbrowser.open('http://'+host+':8765/'+path)

def run_tray(server):
    def exit_app(icon,item):
        with lock:
            if state['active'] or state['pending'] or state.get('recovery_blocked') or family_copy_lock.locked():
                # Do not kill the controller while OBS is recording.
                open_dashboard()
                return
        icon.stop()
        server.shutdown()
    menu=pystray.Menu(
        pystray.MenuItem('Open Dashboard',lambda icon,item:open_dashboard(),default=True),
        pystray.MenuItem('New Patient',lambda icon,item:open_dashboard('?page=patient')),
        pystray.MenuItem('Saved Recordings',lambda icon,item:open_dashboard('?page=saved')),
        pystray.MenuItem('Settings',lambda icon,item:open_dashboard('?page=settings')),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Exit Application',exit_app),
    )
    pystray.Icon('UltrasoundRecorder',tray_icon_image(),'Miracle of Life Recorder',menu).run()

if __name__=='__main__':
    from werkzeug.serving import make_server
    if not single_instance.acquire():
        open_dashboard()
        raise SystemExit(0)
    if NETWORK_MODE and len(NETWORK_PASSWORD)<12:
        raise RuntimeError('Network testing requires RECORDER_TEST_PASSWORD with at least 12 characters')
    threading.Thread(target=hotkey_loop,daemon=True).start()
    server=make_server(BIND_HOST,8765,app,threaded=True)
    state['recovery_blocked']=RECOVERY_POINTER.exists()
    if state['recovery_blocked']:state['warning']='Checking the interrupted recording session...'
    threading.Thread(target=server.serve_forever,daemon=True).start()
    threading.Thread(target=startup_storage,daemon=True).start()
    threading.Thread(target=recording_monitor_loop,daemon=True).start()
    threading.Thread(target=startup_obs,daemon=True).start()
    threading.Thread(target=spotify_status.startup,daemon=True).start()
    threading.Timer(1.3,open_dashboard).start()
    run_tray(server)
