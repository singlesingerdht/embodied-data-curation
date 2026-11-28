"""A strict, read-only compatibility view for existing algorithm APIs."""
from edc.data.readers.base import DataReader


class EpisodeAccess(DataReader):
    def __init__(self, episode):
        self.episode = episode
        self.path = str(episode.path)

    def open(self, path=None):
        self.episode.open()
        return self.info()

    def info(self):
        return self.episode.describe_legacy()

    def close(self):
        self.episode.close()

    def read_field(self, info, name, field):
        return self.episode.read_signal(name, field)

    def channel_values(self, name, indices=None):
        return self.episode.read_values(name, indices)

    def iter_values(self, name):
        return iter(self.channel_values(name))

    def frames_at(self, name, indices):
        ep = self.episode
        # 解开 CanonicalEpisode 等包装，拿到底层真实 Episode 的 reader
        while hasattr(ep, '_source'):
            ep = ep._source
        ep.open()
        return ep._reader.frames_at(name, indices)

    def channel_timestamps(self, name):
        return self.episode.channels[name].timestamps

    def materialize_image_channel(self, info, name, output_path=None):
        if output_path:
            return self.episode.materialize_video(name, output_path)
        return self.episode.video_source(name)

    def image_bytes(self, name):
        return ((f['index'], f['playback_time'], f['image']) for f in self.episode.iter_frames(name))
