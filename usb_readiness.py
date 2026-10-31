"""Read-only recording USB readiness and explicitly approximate capacity estimates."""
import math
import shutil


def observed_rate(recording):
    size=getattr(recording,'output_bytes',None)
    duration=getattr(recording,'output_duration',None)
    if not all(isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
               for value in (size,duration)):
        return None
    if size<=0 or duration<5000:return None
    return size*8/(duration/1000)/1_000_000


def summarize(drives, selected, minimum_gb, rate=None, saved_name=''):
    result={'status':'not_selected','connected':False,'drive':saved_name or 'Recording USB',
            'free_bytes':None,'total_bytes':None,'estimated_minutes':None,
            'message':'Select a recording USB in Settings.'}
    if not selected:return result
    matches=[drive for drive in drives if f'{drive["serial"]:08X}'==selected]
    if not matches:
        return {**result,'status':'disconnected','message':'Selected recording USB disconnected. Reconnect it or check Settings.'}
    if len(matches)!=1:
        return {**result,'status':'ambiguous','message':'More than one drive matches the selected USB. Disconnect the duplicate and check Settings.'}
    drive=matches[0]
    usage=shutil.disk_usage(drive['root'])
    reserve=int(minimum_gb)*1024**3
    usable=max(0,usage.free-reserve)
    measured=isinstance(rate,(int,float)) and not isinstance(rate,bool) and math.isfinite(rate) and rate>0
    mbps=rate if measured else 20
    ready=usable>0
    return {**result,'status':'ready' if ready else 'low_space','connected':True,
            'drive':str(drive['root'])+' - '+drive['label'],'free_bytes':usage.free,'total_bytes':usage.total,
            'reserve_bytes':reserve,'estimated_minutes':int(usable*8/(mbps*1_000_000*60)),
            'estimate_basis':'recent OBS recording average' if measured else 'assumed 20 Mbps',
            'message':'Ready' if ready else 'Low USB space. Free space is at or below the recording reserve.'}
