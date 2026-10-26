"""Execution-only affine clock mapping; never resamples or changes source evidence."""
import copy
from edc.data.ir.bridge import EpisodeAccess
from edc.data.transforms import RobotTransforms


class ClockMappedEpisode:
    def __init__(self, episode, target):
        from edc.algorithms.quality.shared.timing_scope import timing_exclusion_reason
        self._episode = episode
        self.channels = copy.deepcopy(episode.channels)
        self.evidence = []
        transforms = RobotTransforms(episode.profile)
        for name, c in self.channels.items():
            if timing_exclusion_reason(name, c) or not c.timestamps:
                continue
            if c.time_source not in {'acquisition', 'container'}:
                continue
            mapped = transforms.clock(c.timestamps, c.clock, target, time_source=c.time_source)
            row = {'channel': name, 'source_clock': c.clock, 'target_clock': target,
                   'original_timestamps_s': list(c.timestamps), 'params': mapped.params,
                   'source_mapping': mapped.source_mapping, 'rows': 'identity',
                   'signal_selection': copy.deepcopy(c.selection),
                   'time_source': c.time_source, 'resampled': False}
            c.timestamps = mapped.output.tolist()
            c.clock = target
            c.time_evidence['clock_transform'] = row
            self.evidence.append(row)
        source = episode
        while hasattr(source, '_episode'):
            source = source._episode
        if hasattr(source, '_clock_evidence'):
            source._clock_evidence.extend(self.evidence)

    def __getattr__(self, key):
        return getattr(self._episode, key)

    def describe_legacy(self):
        info = copy.deepcopy(self._episode.describe_legacy())
        for name, c in self.channels.items():
            info.topics[name].timestamps = c.timestamps
            info.topics[name].clock = c.clock
            info.topics[name].time_evidence = copy.deepcopy(c.time_evidence)
        info.extra['clock_normalization'] = copy.deepcopy(self.evidence)
        return info

    def algorithm_view(self):
        return self.describe_legacy(), EpisodeAccess(self)
