"""Execution-local topic selection shared by recommendation and CPU execution."""
import copy
from edc.algorithms.channel_names import resolve_channel_parameters


class ChannelSelection:
    """Expose selected topics, leaving source metadata and payloads unchanged."""
    def __init__(self, episode, names):
        if not isinstance(names, list) or not names or any(
                not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names):
            raise ValueError('channels must be a nonempty list of unique exact channel names')
        names = resolve_channel_parameters(episode, {'channels': names})['channels']
        self._episode = episode
        self.channels = {n: episode.channels[n] for n in names}
        self.profile = copy.deepcopy(episode.profile)
        # Requirements for deliberately unselected devices are outside this run.
        sensors = {c.sensor_id for c in episode.channels.values() if c.sensor_id and c.name not in names}
        self.profile['camera_requirements'] = {k: v for k, v in self.profile.get('camera_requirements', {}).items()
                                               if k not in sensors or any(c.sensor_id == k for c in self.channels.values())}
        if 'required_channels' in self.profile:
            self.profile['required_channels'] = [n for n in self.profile['required_channels'] if n in self.channels]

    def __getattr__(self, name):
        return getattr(self._episode, name)

    def open(self):
        self._episode.open()
        return self

    def read_signal(self, channel, field):
        if channel not in self.channels:
            raise ValueError(f'Channel is outside the selected input scope: {channel}')
        return self._episode.read_signal(channel, field)

    def inspect_recorded_signal(self, channel, field):
        if channel not in self.channels:
            raise ValueError(f'Channel is outside the selected input scope: {channel}')
        return self._episode.inspect_recorded_signal(channel, field)


def select_episode(episode, params):
    names = params.get('channels')
    if names is None:
        return episode
    return ChannelSelection(episode, names)
