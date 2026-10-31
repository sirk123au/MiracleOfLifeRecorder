"""In-memory OBS audio meter samples and recording performance summaries."""
import math
import threading
import time
import obsws_python as obs
from obsws_python.subs import Subs


class AudioMeter:
    def __init__(self):
        self.lock = threading.Lock()
        self.samples = {}
        self.connection = None
        self.identity = None
        self.connection_lock = threading.Lock()

    def on_input_volume_meters(self, data):
        now=time.monotonic()
        with self.lock:
            for item in data.inputs:
                levels=item.get('inputLevelsMul',[])
                peak=max((channel[1] for channel in levels if len(channel)>1),default=0)
                if not isinstance(peak,(float,int)) or not math.isfinite(peak):continue
                old=self.samples.get(item['inputName'],{})
                self.samples[item['inputName']]={'checked':now,'level':peak,
                    'signal':now if peak>0.001 else old.get('signal')}

    def ensure(self, settings):
        identity=(settings['obs_host'],settings['obs_port'],settings['obs_password'])
        with self.connection_lock:
            if self.connection is not None and self.identity==identity and self.connection.worker.is_alive():return
            if self.connection is not None:
                self.connection.disconnect()
            self.connection=None
            with self.lock:self.samples.clear()
            connection=obs.EventClient(host=identity[0],port=int(identity[1]),password=identity[2],
                                       timeout=3,subs=Subs.INPUTVOLUMEMETERS)
            connection.callback.register(self.on_input_volume_meters)
            self.connection,self.identity=connection,identity

    def status(self, source):
        with self.lock:sample=self.samples.get(source,{}).copy()
        now=time.monotonic()
        if not sample or now-sample['checked']>5:return 'unknown'
        signal=sample.get('signal')
        return 'signal' if signal is not None and now-signal<5 else 'quiet'


def stats_summary(stats, previous=None):
    def number(name):
        value=getattr(stats,name,None)
        return value if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) else None
    current={name:number(name) for name in ('cpu_usage','active_fps','render_skipped_frames','output_skipped_frames')}
    warnings=[]
    if current['cpu_usage'] is not None and current['cpu_usage']>=90:
        warnings.append('OBS CPU usage is high.')
    for key,label in [('render_skipped_frames','rendering'),('output_skipped_frames','encoding')]:
        before=(previous or {}).get(key)
        after=current[key]
        if before is not None and after is not None and after>before:
            warnings.append('OBS skipped '+label+' frames since the last check.')
    return current,warnings
