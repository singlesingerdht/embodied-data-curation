"""Frame identity with separate playback and recorded source time axes."""
import numpy as np


class FrameTimeline:
    """Source seconds select physical windows; FPS only describes playback.

    ``offset`` returns a half-open slice boundary. Use ``side='right'``
    minus one for an inclusive upper event boundary. Recorded time never
    gets replaced by a fitted rate or an extrapolated last timestamp.
    """

    def __init__(self, source_timestamps=None, fps=None, max_gap_sec=None, *, playback_timestamps=None, count=None):
        self.count = count if count is not None else len(source_timestamps)
        if self.count < 2:
            raise ValueError('At least two frames are required')
        def axis(values):
            if values is None: return None
            a=np.asarray(values,float)
            if a.shape != (self.count,) or not np.isfinite(a).all() or np.any(np.diff(a)<=0):
                raise ValueError('Frame timestamps must be finite and strictly increasing')
            return a
        self.source=axis(source_timestamps)
        self.times=self.source-self.source[0] if self.source is not None else None
        if playback_timestamps is None and fps is not None:
            if not np.isfinite(fps) or fps<=0: raise ValueError('Invalid playback FPS')
            playback_timestamps=np.arange(self.count)/fps
        self.playback_times=axis(playback_timestamps)
        if self.playback_times is not None: self.playback_times-=self.playback_times[0]
        self.fps=None
        if self.playback_times is not None:
            dt=np.diff(self.playback_times)
            if np.allclose(dt,dt[0],atol=1e-5,rtol=1e-4): self.fps=float(fps) if fps else float(1/dt[0])
        self.uniform_source=bool(self.times is not None and np.allclose(np.diff(self.times),np.diff(self.times)[0],atol=1e-5,rtol=1e-4))
        self.matches_playback=bool(self.uniform_source and self.playback_times is not None and np.allclose(self.times,self.playback_times,atol=1e-5,rtol=0))
        self.rule_fps = self.fps if self.matches_playback else None

    @classmethod
    def from_manifest(cls,manifest):
        row=manifest['views'][manifest['reference']]
        timeline = cls(row['source_timestamps'],playback_timestamps=row['playback_timestamps'],count=manifest['frame_count'])
        # Frozen uniform-window rounding depends on observed source intervals,
        # never on the storage container or its playback rate.
        timeline.rule_fps = float(1/np.median(np.diff(timeline.times))) if timeline.uniform_source else None
        return timeline

    def axis(self,basis):
        return self.playback_times if basis=='playback' else self.times if basis=='source' else None

    def duration(self,basis):
        axis=self.axis(basis)
        if axis is None: return None
        return self.count/self.fps if basis=='playback' and self.fps else float(axis[-1])

    def time_at(self, frame):
        if self.times is None: return None
        if not 0 <= int(frame) < self.count: raise ValueError('Frame outside recorded timeline')
        return float(self.times[int(frame)])

    def elapsed(self, start, end):
        if self.times is None: raise ValueError('Recorded duration unavailable')
        return self.time_at(end) - self.time_at(start)

    def offset(self, frame, seconds, side='left'):
        if self.times is None or side not in {'left', 'right'} or not np.isfinite(seconds):
            raise ValueError('Invalid source time boundary')
        if self.rule_fps is not None:
            # Retain the frozen window rounding on the original uniform path.
            index = int(frame) + round(float(seconds) * self.rule_fps) + (side == 'right')
        else:
            index = int(np.searchsorted(self.times, self.time_at(frame) + seconds, side=side))
        return min(max(index, 0), len(self.times))

    def describe(self):
        dt=np.diff(self.times) if self.times is not None else None
        delta=self.times-self.playback_times if self.times is not None and self.playback_times is not None else None
        return {'time_basis':'recorded_source_seconds' if self.times is not None else 'frame_order_only',
            'source_start_timestamp':float(self.source[0]) if self.source is not None else None,
            'source_duration_sec':self.duration('source'),'playback_duration_sec':self.duration('playback'),
            'playback_fps':self.fps,'frame_count':self.count,'matches_playback':self.matches_playback,
            'uniform_source_intervals':self.uniform_source,
            'interval_min_sec':float(dt.min()) if dt is not None else None,
            'interval_median_sec':float(np.median(dt)) if dt is not None else None,
            'interval_max_sec':float(dt.max()) if dt is not None else None,
            'maximum_playback_source_offset_sec':float(np.max(np.abs(delta))) if delta is not None else None,
            'source_timestamps':self.source.tolist() if self.source is not None else None,
            'source_relative_timestamps':self.times.tolist() if self.times is not None else None,
            'playback_timestamps':self.playback_times.tolist() if self.playback_times is not None else None,
            'source_endpoint':'last observed frame; no time extrapolation'}


def normalize_subtasks(subtasks,timeline,basis=None):
    basis=basis or ('playback' if timeline.playback_times is not None else 'source')
    if basis not in {'source','playback'}: raise ValueError('Unknown subtask_time_basis')
    result=[]; steps=set(); previous=0; previous_kind=None
    for item in subtasks:
        seconds=any(k in item for k in ('clip_start_sec','clip_end_sec'))
        frames=any(k in item for k in ('clip_start_frame','clip_end_frame'))
        if seconds==frames or not item.get('query') or item['step'] in steps:
            raise ValueError('Provide unique steps and exactly one seconds or frame interval')
        kind='seconds' if seconds else 'frames'
        if previous_kind is not None and previous_kind!=kind: raise ValueError('Mixed interval representations')
        if frames:
            start,stop=item['clip_start_frame'],item['clip_end_frame']
            if not all(isinstance(x,int) and not isinstance(x,bool) for x in (start,stop)) or not 0<=previous<=start<stop<=timeline.count:
                raise ValueError('Frame intervals must be ordered non-overlapping half-open ranges')
            end=stop-1; previous=stop
        else:
            a,b=item['clip_start_sec'],item['clip_end_sec']; axis=timeline.axis(basis)
            if axis is None or not all(np.isfinite(x) for x in (a,b)) or not previous<=a<b<=timeline.duration(basis)+1e-6:
                raise ValueError('Task interval exceeds available '+basis+' timeline')
            if basis=='playback' and timeline.fps:
                start=int(a*timeline.fps);end=min(int(b*timeline.fps),timeline.count-1)
            else:
                start=int(np.searchsorted(axis,a,side='left'));end=min(int(np.searchsorted(axis,b,side='right'))-1,timeline.count-1)
            previous=b
        if end<=start: raise ValueError('Each subtask needs at least two original frames')
        result.append({**item,'_start_frame':start,'_end_frame':end,'_time_basis':'frame' if frames else basis})
        steps.add(item['step']);previous_kind=kind
    if not result: raise ValueError('At least one task interval is required')
    return result


def sampled_indices(subtasks,interval):
    selected=set()
    for item in subtasks:
        start,end=item['_start_frame'],item['_end_frame']
        selected.update(range(start,end+1,interval));selected.add(end)
    return sorted(selected)
